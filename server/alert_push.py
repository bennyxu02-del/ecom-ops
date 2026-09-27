"""每日预警推送：扫描 → 汇总成一条「今日预警」飞书卡片推给「我」→ 后台给新预警预跑 AI 诊断。

- 每天一条汇总消息，不是一条预警一条消息；最多列 5 条，其余写「还有 N 条」。
- 去重：同一条预警只在新增、严重度升级、重新回到待决定（上次方案无效 / 待办取消 / 观察到期）时逐条推送；
  做过决定的（转待办、已知原因、忽略、观察中）不再推送。用「推送签名」= 严重度 + 回到待决定的次数 实现。
- 定时：每天设定的时间（默认 9:00，时区 ALERT_TZ，默认 Asia/Shanghai）自动跑一次；「立即扫描并推送」随时手动跑。
"""
from __future__ import annotations

import datetime as dt
import os
import threading
import time

from core import charts as C

from . import alert_flow, data, feishu, llm_client, state

MAX_ITEMS = 5
DEFAULTS = dict(enabled=True, time="09:00", send_empty=True)
SEV_TAG = {"red": "红", "yellow": "黄", "blue": "蓝"}


def config(name: str) -> dict:
    c = dict(DEFAULTS)
    c.update(state.get_setting(f"alert_push__{name}", {}) or {})
    return c


def set_config(name: str, **kw) -> dict:
    c = config(name)
    if "time" in kw and kw["time"] is not None:
        try:
            h, m = str(kw["time"]).split(":")
            assert 0 <= int(h) < 24 and 0 <= int(m) < 60
        except Exception:  # noqa: BLE001
            raise ValueError("推送时间格式应为 HH:MM")
        c["time"] = f"{int(h):02d}:{int(m):02d}"
    for k in ("enabled", "send_empty"):
        if kw.get(k) is not None:
            c[k] = bool(kw[k])
    state.set_setting(f"alert_push__{name}", c)
    return c


def base_url() -> str | None:
    return os.environ.get("PUBLIC_BASE_URL") or state.get_setting("public_base_url")


def link(name: str, cid: str | None = None) -> str | None:
    b = base_url()
    if not b:
        return None
    return f"{b.rstrip('/')}/#/alerts?ds={name}" + (f"&open={cid}" if cid else "")


def _pushed(name) -> dict:
    return state.get_setting(f"alert_pushed__{name}", {}) or {}


def summary_line(name: str, c: dict) -> str:
    """一行：AI 一句话结论（有缓存的诊断时）或规则描述 + 在途 + 影响金额。"""
    from . import alert_detail
    text = None
    if not c.get("store"):
        try:
            text = (alert_detail.diagnosis_now(name, c["product_id"]).get("summary") or "").split("。")[0]
        except Exception:  # noqa: BLE001
            text = None
        if text and text.startswith(c["product_name"]):
            text = text[len(c["product_name"]):].lstrip("，, ")
    if not text:
        text = "；".join(h["text"] for h in (c.get("latest") or [])[:2])
    it = (c.get("in_transit") or [None])[0]
    if it and "在途" not in text:
        md = f"{int(it['date'][5:7])}/{int(it['date'][8:10])}"
        text += f"；在途 {it['qty']:,.0f} 件，{md} 到货" if it.get("qty") else f"；在途补货 {md} 到货"
    if c.get("gmv_impact") and c.get("kind") != "opportunity":
        text += f"；近 7 天少卖约 {C.fmt(c['gmv_impact'], 'money')}"
    return text


def _item(name, c, tag=None) -> dict:
    return dict(id=c["id"], product_id=c["product_id"], product_name=c["product_name"], severity=c["severity"],
                severity_name=c["severity_name"], line=summary_line(name, c), tag=tag, link=link(name, c["id"]),
                kind=c.get("kind"), signature=c["signature"])


def digest(name: str) -> dict:
    """今天要推送的内容（不发送）。"""
    ds = data.ds_of(name)
    cards = data.cards(name)
    pushed = _pushed(name)
    new, expired, opp = [], [], []
    for c in cards:
        if c["group"] not in ("pending", "opportunity") or pushed.get(c["id"]) == c["signature"]:
            continue
        last = (c.get("flags") or {}).get("last")
        if c["group"] == "opportunity":
            opp.append(_item(name, c))
        elif last == "watch_expired":
            expired.append(_item(name, c, "观察到期"))
        else:
            tag = None
            if c["id"] in pushed:
                tag = alert_flow.REOPEN_TEXT.get(last) or "升级"
            new.append(_item(name, c, tag))
    order = {"red": 0, "yellow": 1, "blue": 2}
    imp = {c["id"]: c["gmv_impact"] for c in cards}
    new.sort(key=lambda x: (order[x["severity"]], -imp.get(x["id"], 0)))
    listed = {x["id"] for x in new + expired + opp}
    stale = [c for c in cards if c["group"] == "pending" and c["id"] not in listed and c["days_open"] >= 3]
    pend = [c for c in cards if c["group"] == "pending"]
    sev = {"red": 0, "yellow": 0, "blue": 0}
    for c in pend:
        sev[c["severity"]] += 1
    return dict(dataset=name, dataset_name=data.DATASETS.get(name), date=ds.as_of.strftime("%Y-%m-%d"),
                new=new, expired=expired, opportunities=opp, stale=len(stale), pending=len(pend), pending_sev=sev,
                opportunity_total=sum(1 for c in cards if c["group"] == "opportunity"),
                empty=not (new or expired or opp), link=link(name))


def text_of(dg: dict) -> str:
    """纯文字版（预览、没有飞书时复制用）。"""
    d = dg["date"]
    head = f"【今日预警】{dg['dataset_name']} · {int(d[5:7])}/{int(d[8:10])}"
    lines = [head]
    if dg["empty"]:
        lines.append("今天没有新预警。" + (f"还有 {dg['pending']} 条待决定。" if dg["pending"] else ""))
        return "\n".join(lines)
    s = dg["pending_sev"]
    lines.append(f"需要你做决定 {dg['pending']} 条（" + "、".join(f"{SEV_TAG[k]} {v}" for k, v in s.items() if v) + "）"
                 + (f"，机会 {dg['opportunity_total']} 条" if dg["opportunity_total"] else ""))
    for x in dg["new"][:MAX_ITEMS]:
        lines.append(f"【{x['severity_name']}】{x['product_name']}" + (f"（{x['tag']}）" if x["tag"] else "") + f"：{x['line']}")
    if len(dg["new"]) > MAX_ITEMS:
        lines.append(f"还有 {len(dg['new']) - MAX_ITEMS} 条新预警")
    for x in dg["expired"]:
        lines.append(f"观察到期：{x['product_name']}：{x['line']}")
    for x in dg["opportunities"]:
        lines.append(f"机会：{x['product_name']}：{x['line']}")
    if dg["stale"]:
        lines.append(f"还有 {dg['stale']} 条超过 2 天没做决定")
    return "\n".join(lines)


def _md(s: str) -> str:
    return s.replace("<", "&lt;").replace(">", "&gt;")


def build_card(dg: dict) -> dict:
    d = dg["date"]
    title = f"【今日预警】{dg['dataset_name']} · {int(d[5:7])}/{int(d[8:10])}"
    els = []

    def row(text, url=None, btn="去处理", primary=True):
        e = {"tag": "div", "text": {"tag": "lark_md", "content": text}}
        if url:
            e["extra"] = {"tag": "button", "text": {"tag": "plain_text", "content": btn},
                          "type": "primary" if primary else "default", "url": url}
        els.append(e)

    if dg["empty"]:
        row("今天没有新预警，系统运行正常。" + (f"\n还有 **{dg['pending']}** 条待决定。" if dg["pending"] else ""))
    else:
        s = dg["pending_sev"]
        row(f"需要你做决定 **{dg['pending']}** 条（" + "、".join(f"{SEV_TAG[k]} {v}" for k, v in s.items() if v) + "）"
            + (f"，机会 {dg['opportunity_total']} 条" if dg["opportunity_total"] else ""))
        if dg["new"]:
            els.append({"tag": "hr"})
            for x in dg["new"][:MAX_ITEMS]:
                row(f"**【{x['severity_name']}】{_md(x['product_name'])}**" + (f"　{x['tag']}" if x["tag"] else "")
                    + f"\n{_md(x['line'])}", x["link"])
            if len(dg["new"]) > MAX_ITEMS:
                row(f"还有 {len(dg['new']) - MAX_ITEMS} 条新预警")
        if dg["expired"]:
            els.append({"tag": "hr"})
            for x in dg["expired"]:
                row(f"**观察到期 · {_md(x['product_name'])}**\n{_md(x['line'])}", x["link"])
        if dg["opportunities"]:
            els.append({"tag": "hr"})
            for x in dg["opportunities"]:
                row(f"**机会 · {_md(x['product_name'])}**\n{_md(x['line'])}", x["link"], "去看看", False)
        if dg["stale"]:
            row(f"还有 {dg['stale']} 条超过 2 天没做决定")
    if dg.get("link"):
        els.append({"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "打开预警中心"},
                                                  "type": "default", "url": dg["link"]}]})
    has_red = any(x["severity"] == "red" for x in dg["new"] + dg["expired"])
    template = "green" if dg["empty"] else "red" if has_red else "orange"
    return {"config": {"wide_screen_mode": True}, "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
            "elements": els}


def _mark(name: str, dg: dict):
    pushed = _pushed(name)
    for x in dg["new"] + dg["expired"] + dg["opportunities"]:
        pushed[x["id"]] = x["signature"]
        alert_flow.add_log(name, x["id"], "已推送到飞书（今日预警）")
    state.set_setting(f"alert_pushed__{name}", pushed)


def history(name: str) -> list[dict]:
    return state.get_setting(f"alert_push_log__{name}", []) or []


def _record(name: str, rec: dict):
    h = history(name)
    h.insert(0, rec)
    state.set_setting(f"alert_push_log__{name}", h[:20])


def push(name: str, manual: bool = False, rescan: bool = True) -> dict:
    """扫描并推送。返回 {sent, to, reason, digest, text}。"""
    from . import notify
    if rescan:
        data.rescan(name)
    cfg = config(name)
    dg = digest(name)
    out = dict(sent=False, digest=dg, text=text_of(dg), manual=manual)
    if dg["empty"] and not cfg["send_empty"]:
        out["reason"] = "没有新预警，按设置不发送"
    else:
        r = notify.recipient("我")
        if not r:
            out["reason"] = "「我」还没有绑定飞书（在「集成」页设置），推送内容见下方预览"
        else:
            try:
                feishu.send_card(r["open_id"], build_card(dg))
                out.update(sent=True, to=r.get("name"))
                _mark(name, dg)
            except feishu.FeishuError as e:
                out["reason"] = f"飞书发送失败：{e}"
    _record(name, dict(t=time.time(), date=dg["date"], sent=out["sent"], to=out.get("to"), reason=out.get("reason"),
                       manual=manual, new=len(dg["new"]), expired=len(dg["expired"]), opportunities=len(dg["opportunities"])))
    if out["sent"]:
        prediagnose(name, [x["product_id"] for x in dg["new"] + dg["expired"] if x["severity"] in ("red", "yellow")])
    return out


def prediagnose(name: str, pids: list[str]):
    """后台给新预警预跑 AI 诊断，早上点进来就能看到结论（有在线模型时才跑）。"""
    if llm_client.mode() not in ("live", "mock") or os.environ.get("PREDIAGNOSE", "on") == "off":
        return
    from .agent import diagnose

    def job():
        for pid in dict.fromkeys(p for p in pids if p in data.ds_of(name).product_ids()):
            if state.cache_get(diagnose.cache_key(name, pid)):
                continue
            try:
                for _ in diagnose.run(name, pid):
                    pass
            except Exception as e:  # noqa: BLE001
                print("[alert_push] 预诊断失败：", pid, repr(e), flush=True)
    threading.Thread(target=job, daemon=True, name="prediagnose").start()


# ---------------------------------------------------------------------------
# 定时
# ---------------------------------------------------------------------------
def _now():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(os.environ.get("ALERT_TZ", "Asia/Shanghai")))
    except Exception:  # noqa: BLE001
        return dt.datetime.now()


def due(name: str, now: dt.datetime | None = None) -> bool:
    cfg = config(name)
    if not cfg["enabled"]:
        return False
    now = now or _now()
    if now.strftime("%H:%M") < cfg["time"]:
        return False
    return state.get_setting(f"alert_push_day__{name}") != now.strftime("%Y-%m-%d")


def tick(now: dt.datetime | None = None) -> list[str]:
    done = []
    now = now or _now()
    for name in data.DATASETS:
        if due(name, now):
            state.set_setting(f"alert_push_day__{name}", now.strftime("%Y-%m-%d"))
            try:
                push(name)
                done.append(name)
            except Exception as e:  # noqa: BLE001
                print("[alert_push] 定时推送出错：", repr(e), flush=True)
    return done


def start_scheduler(interval: float | None = None):
    interval = interval or float(os.environ.get("ALERT_PUSH_INTERVAL", "30"))

    def loop():
        while True:
            time.sleep(interval)
            try:
                tick()
            except Exception as e:  # noqa: BLE001
                print("[alert_push] 定时检查出错：", repr(e), flush=True)
    threading.Thread(target=loop, daemon=True, name="alert-push").start()
