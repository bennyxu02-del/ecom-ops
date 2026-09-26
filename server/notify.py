"""协同通知：把需要别人处理的事项推送到对方飞书，并在事项变化时同步。

- send：首次推送协同卡片（飞书未绑定时标记为「复制发送」由运营手动转发）
- sync：状态变化后刷新对方卡片，并把进展通知发起人（「我」）
- changed：截止日期调整、待办取消 / 重新打开、名称修改时，刷新卡片并给对方发说明
- remind：催办（手动「催一下」或到期自动提醒）
- reply：对方提出疑问后，发起人的回复推回给对方
- tick：按真实时间检查到期未完成的事项并自动提醒

飞书调用失败不影响平台主流程，结果写入协同记录。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time

from . import collab, data, feishu, state

DAY = 86400
AUTO_REMIND_MAX = int(os.environ.get("AUTO_REMIND_MAX", "3"))
REMIND_GAP = float(os.environ.get("REMIND_GAP_HOURS", "24")) * 3600
OPEN = ("sent", "received")          # 等对方处理的状态（question 时轮到发起人回复，不催）


class NotifyError(Exception):
    pass


def link(hid: int) -> str | None:
    b = os.environ.get("PUBLIC_BASE_URL") or state.get_setting("public_base_url")
    return f"{b.rstrip('/')}/#/h/{hid}" if b else None


def _h(hid: int) -> dict:
    return collab.decorate_handoff(state.get_handoff(hid), with_context=True)


def recipient(role: str) -> dict | None:
    """角色对应的飞书成员；飞书未启用或未绑定时返回 None。"""
    if not feishu.enabled():
        return None
    r = feishu.roles().get(role)
    return r if r and r.get("open_id") else None


def _log(hid: int, text: str, by: str = "系统"):
    h = state.get_handoff(hid)
    hist = json.loads(h.get("history_json") or "[]")
    hist.append(dict(t=time.time(), status=h["status"], by=by, note=text))
    state.update_handoff(hid, history_json=json.dumps(hist, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 首次推送
# ---------------------------------------------------------------------------
def send(hid: int, channel: str = "auto", message: str | None = None) -> dict:
    """channel：feishu / copy / auto（绑定了飞书就推送，否则留给运营复制发送）。"""
    h0 = state.get_handoff(hid)
    if message:
        state.update_handoff(hid, message=message)
    r = recipient(h0["role"])
    if channel == "auto":
        channel = "feishu" if r else "copy"
    if channel == "feishu":
        if not r:
            raise NotifyError(f"飞书未配置，或还没有设置「{h0['role']}」对应的飞书成员")
        h = _h(hid)
        h["status"] = "sent"
        try:
            mid = feishu.send_card(r["open_id"], feishu.build_card(h, link(hid)))
        except feishu.FeishuError as e:
            raise NotifyError(str(e))
        state.update_handoff(hid, assignee=r.get("name"))
        collab.mark_sent(hid, "feishu", message, ext_id=mid)
    else:
        collab.mark_sent(hid, "copy", message)
    return _h(hid)


def send_all(action_row: int) -> list[dict]:
    """保存并通知：把一条待办下所有待发送的事项推出去。返回每一项的结果。"""
    out = []
    for h in state.list_handoffs(action_row=action_row):
        if h["status"] != "draft":
            continue
        r = recipient(h["role"])
        if not r:
            out.append(dict(id=h["id"], role=h["role"], ok=False, channel=None, error="未绑定飞书"))
            continue
        try:
            send(h["id"], "feishu")
            out.append(dict(id=h["id"], role=h["role"], ok=True, channel="feishu", to=r.get("name")))
        except NotifyError as e:
            out.append(dict(id=h["id"], role=h["role"], ok=False, channel="feishu", error=str(e)))
    return out


def preview(handoffs: list[dict]) -> list[dict]:
    """保存前预览：每个协同事项会发给谁、通过什么渠道。"""
    res = []
    for h in handoffs:
        r = recipient(h["role"])
        res.append(dict(kind=h["kind"], role=h["role"], steps=h.get("steps", []), to=(r or {}).get("name"),
                        channel="feishu" if r else "copy", message=h.get("message")))
    return res


# ---------------------------------------------------------------------------
# 变化同步
# ---------------------------------------------------------------------------
def _refresh_card(h: dict):
    if h.get("channel") == "feishu" and h.get("ext_id") and feishu.enabled():
        try:
            feishu.update_card(h["ext_id"], feishu.build_card(h, link(h["id"])))
        except feishu.FeishuError as e:
            print("[notify] 更新卡片失败：", e, flush=True)


def _text_to(role: str, text: str) -> bool:
    r = recipient(role)
    if not r:
        return False
    try:
        feishu.send_text(r["open_id"], text)
        return True
    except feishu.FeishuError as e:
        print("[notify] 发送消息失败：", e, flush=True)
        return False


def sync(hid: int, notify_me: bool = True):
    """对方处理后：刷新卡片，并把进展告诉发起人。"""
    h = _h(hid)
    _refresh_card(h)
    if notify_me and h["status"] != "sent":
        a = h.get("action") or {}
        last = (h.get("history") or [{}])[-1]
        text = (f"【协同进展】{h['role']} {h['status_name']}：{a.get('product_name', '')} · {a.get('name', '')}"
                + (f"\n说明：{last.get('note')}" if last.get("note") else "")
                + ("\n请到待办中心回复对方。" if h["status"] == "question" else ""))
        _text_to("我", text)


def changed(action_row: int, what: str, detail: str = ""):
    """待办本身变化（due / cancel / reopen / rename）后同步到已推送的协同事项。"""
    a = state.get_action(action_row)
    for h0 in state.list_handoffs(action_row=action_row):
        if h0["status"] == "draft":
            continue
        h = _h(h0["id"])
        _refresh_card(h)
        if h0.get("channel") != "feishu":
            continue
        head = f"【待办变更】{a['product_name']} · {a['name']}"
        msg = {"due": f"{head}\n截止日期调整为 {detail}，请按新的时间安排。",
               "cancel": f"{head}\n这条协同请求已取消，无需继续处理。" + (f"\n原因：{detail}" if detail else ""),
               "reopen": f"{head}\n这条协同请求已重新打开，请继续处理。"}.get(what)
        if msg and _text_to(h0["role"], msg):
            _log(h0["id"], {"due": "已通知对方截止日期调整", "cancel": "已通知对方待办取消",
                            "reopen": "已通知对方待办重新打开"}[what])


def remind(hid: int, auto: bool = False, now: float | None = None) -> dict:
    h = _h(hid)
    if h["status"] not in OPEN:
        raise NotifyError("该事项当前不需要催办")
    a = h.get("action") or {}
    steps = "\n".join(f"{i}. {t}" for i, t in enumerate(h.get("step_texts") or [], 1))
    as_of = data.ds_of(h["ds"]).as_of.strftime("%Y-%m-%d")
    late = h.get("due") and h["due"] < as_of
    text = (f"【{'自动提醒' if auto else '催办'}】{a.get('product_name', '')} · {a.get('name', '')}\n"
            + (f"需要{h['role']}协助：\n{steps}\n" if steps else "")
            + f"截止 {h.get('due') or '—'}，目前还没有完成。处理后请在之前的协同卡片上点「已完成」。"
            + (f"\n处理入口：{link(hid)}" if link(hid) else ""))
    if not _text_to(h["role"], text):
        raise NotifyError(f"「{h['role']}」没有绑定飞书，请复制内容手动提醒")
    cnt = int(h.get("remind_count") or 0) + 1
    state.update_handoff(hid, reminded_at=now or time.time(), remind_count=cnt,
                         auto_reminds=int(h.get("auto_reminds") or 0) + (1 if auto else 0))
    _log(hid, ("自动提醒" if auto else "催一下") + ("（已逾期）" if late else ""), by="系统" if auto else "我")
    return _h(hid)


def reply(hid: int, text: str) -> dict:
    """回复对方的疑问：推送给对方，事项回到对方处理中。"""
    h0 = state.get_handoff(hid)
    if not text.strip():
        raise NotifyError("请填写回复内容")
    hist = json.loads(h0.get("history_json") or "[]")
    prev = next((x["status"] for x in reversed(hist) if x.get("status") in ("sent", "received")), "sent")
    hist.append(dict(t=time.time(), status=prev, by="我", note="回复：" + text.strip()))
    state.update_handoff(hid, status=prev, history_json=json.dumps(hist, ensure_ascii=False))
    h = _h(hid)
    a = h.get("action") or {}
    _refresh_card(h)
    sent = _text_to(h["role"], f"【回复】{a.get('product_name', '')} · {a.get('name', '')}\n{text.strip()}")
    collab._refresh_action(h0["ds"], h0["action_row"])
    h["reply_pushed"] = sent
    return h


# ---------------------------------------------------------------------------
# 到期自动提醒（真实时间）
# ---------------------------------------------------------------------------
def deadline(h: dict) -> float | None:
    """平台截止日期按数据日期记录；换算成真实时间：发出时刻 + （截止日期 − 数据日期）天，至少 1 天。"""
    if not h.get("sent_at") or not h.get("due"):
        return None
    try:
        as_of = data.ds_of(h["ds"]).as_of.date()
        days = (dt.date.fromisoformat(h["due"]) - as_of).days
    except Exception:  # noqa: BLE001
        days = 1
    return float(h["sent_at"]) + max(days, 1) * DAY


def due_for_reminder(h: dict, now: float) -> bool:
    if h["status"] not in OPEN or h.get("channel") != "feishu":
        return False
    a = state.get_action(h["action_row"])
    if not a or a["status"] in ("cancelled", "rejected", "executed"):
        return False
    dl = deadline(h)
    if dl is None or now < dl:
        return False
    if int(h.get("auto_reminds") or 0) >= AUTO_REMIND_MAX:
        return False
    return not h.get("reminded_at") or now - float(h["reminded_at"]) >= REMIND_GAP


def tick(now: float | None = None) -> list[int]:
    now = now or time.time()
    done = []
    for name in data.DATASETS:
        for h in state.list_handoffs(name):
            if due_for_reminder(h, now):
                try:
                    remind(h["id"], auto=True, now=now)
                    done.append(h["id"])
                except NotifyError as e:
                    print("[notify] 自动提醒失败：", e, flush=True)
    return done


def start_scheduler(interval: float | None = None):
    interval = interval or float(os.environ.get("REMINDER_INTERVAL", "600"))

    def loop():
        while True:
            time.sleep(interval)
            try:
                tick()
            except Exception as e:  # noqa: BLE001
                print("[notify] 提醒检查出错：", repr(e), flush=True)
    threading.Thread(target=loop, daemon=True, name="reminders").start()
