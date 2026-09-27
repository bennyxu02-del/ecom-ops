"""预警的「决定」与闭环（v12）。

状态只跟「决定」和「结果」走，打开看一眼不改变状态：
- pending 待决定：新预警；观察到期仍在触发；待办复盘无效 / 无法判断且指标没回来；待办被取消；严重度升级
- doing 处理中：用当前方案转了待办
- watch 观察中：先观察 1 / 3 / 7 天
- resolved 已解决：待办复盘有效（或无法判断但指标已回到出问题前的水平）
- closed 已关闭：已知原因 / 忽略（活动后回落的预警自动按「已知原因」关闭；忽略过的同类预警静默 N 天）
- recovered 自然恢复：没人处理，指标回到出问题前的水平

状态是「懒推导」的：每次读取预警时按待办进度、观察期、扫描结果推导，发生变化才写库并记一条处理记录。
"""
from __future__ import annotations

import json
import time

import pandas as pd

from core import alerts
from core.custom_alerts import STORE

from . import state

STATUS_NAMES = {"pending": "待决定", "doing": "处理中", "watch": "观察中", "resolved": "已解决", "closed": "已关闭",
                "recovered": "自然恢复"}
DECISIONS = {"todo": "转待办", "known": "已知原因", "ignore": "忽略", "watch": "先观察"}
IGNORE_REASONS = ["正常波动", "阈值太严", "数据有误", "其他"]
WATCH_DAYS = (1, 3, 7)
FINISHED = ("resolved", "closed", "recovered")
GROUPS = {"pending": "待决定", "doing": "处理中", "watch": "观察中", "opportunity": "机会", "finished": "已完结"}
REOPEN_TEXT = {"escalated": "严重度升级", "failed": "上次方案无效", "cancelled": "待办已取消", "unknown": "上次方案效果不明",
               "watch_expired": "观察到期", "todo_missing": "关联待办不存在"}


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except (TypeError, json.JSONDecodeError):
        return default


def _ds(name):
    from . import data
    return data.ds_of(name)


def _d(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _row(name, cid) -> dict | None:
    r = state.q("SELECT * FROM alert_state WHERE ds=? AND card_id=?", (name, cid))
    return r[0] if r else None


def add_log(name: str, cid: str, text: str, by: str = "系统", **extra):
    r = _row(name, cid)
    log = _loads((r or {}).get("log_json"), [])
    log.append(dict(t=time.time(), date=_d(_ds(name).as_of), by=by, text=text, **extra))
    state.upsert_card(name, cid, log_json=json.dumps(log, ensure_ascii=False))


def _set(name: str, cid: str, status: str, text: str | None, by: str = "系统", card: dict | None = None, **kw):
    kw["status"] = status
    if card:
        kw.update(product_id=card["product_id"], severity=card["severity"],
                  rules_json=json.dumps(card["rules"], ensure_ascii=False))
    state.upsert_card(name, cid, **kw)
    if text:
        add_log(name, cid, text, by=by)
    return _row(name, cid)


def _silence_days(name) -> int:
    return int(_ds(name).profile.get("alert", {}).get("ignore_silence_days", 7) or 0)


def silenced_by(name: str, c: dict, rows: dict) -> dict | None:
    """同一商品、有重叠规则的预警在 N 天内被忽略过，且这次没有升级 → 静默。"""
    days = _silence_days(name)
    if days <= 0:
        return None
    as_of = _ds(name).as_of
    for r in rows.values():
        if (r["card_id"] == c["id"] or r.get("decision") != "ignore" or r.get("status") != "closed"
                or r.get("product_id") != c["product_id"] or not r.get("closed_date")):
            continue
        if (as_of - pd.Timestamp(r["closed_date"])).days >= days:
            continue
        if not set(_loads(r.get("rules_json"), [])) & set(c["rules"]):
            continue
        if alerts.SEV_ORDER[c["severity"]] > alerts.SEV_ORDER.get(r.get("severity") or "blue", 1):
            continue
        return r
    return None


def _escalated(c: dict, s: dict) -> bool:
    return (c["auto_status"] == "active" and bool(s.get("severity"))
            and alerts.SEV_ORDER[c["severity"]] > alerts.SEV_ORDER.get(s["severity"], 1))


def _metric_back(name: str, c: dict) -> bool:
    """指标是否已回到出问题前的水平（今天没有再触发，且对应指标回到首次触发前 7 天的水平）。"""
    if c["auto_status"] == "recovered":
        return True
    if c["is_today"]:
        return False
    ds = _ds(name)
    ok, _ = alerts.back_to_base(ds, c["product_id"], pd.Timestamp(c["first_date"]), set(c["rules"]), ds.as_of)
    return ok


def _reopen(name, c, s, last, text, **extra):
    flags = _loads(s.get("flags_json"), {})
    flags.update(reopen=int(flags.get("reopen", 0)) + 1, last=last, last_date=_d(_ds(name).as_of), **extra)
    return _set(name, c["id"], "pending", text, card=c, flags_json=json.dumps(flags, ensure_ascii=False),
                decision=None, watch_until=None, closed_date=None, action_row=None)


def _advance(name, s, c):
    """按待办进度、观察期、扫描结果推进一次状态；有变化返回新行，否则返回 None。"""
    st = s.get("status")
    today = _d(_ds(name).as_of)
    sev = alerts.SEV_NAME.get(c["severity"], "")
    if st == "pending":
        if c["auto_status"] == "recovered":
            return _set(name, c["id"], "recovered", "指标回到出问题前的水平，自然恢复", card=c, closed_date=today)
        return None
    if st == "doing":
        from . import todos
        a = state.get_action(s["action_row"]) if s.get("action_row") else None
        if a:
            a = todos.advance(name, a)
        if not a:
            return _reopen(name, c, s, "todo_missing", "关联的待办不存在，回到待决定")
        if a["status"] == "cancelled":
            return _reopen(name, c, s, "cancelled", f"待办「{a['name']}」已取消，回到待决定")
        if a["status"] == "done":
            out = a.get("outcome")
            if out == "effective":
                return _set(name, c["id"], "resolved", f"待办「{a['name']}」复盘有效，预警已解决", card=c, closed_date=today)
            if out == "ineffective":
                return _reopen(name, c, s, "failed", f"待办「{a['name']}」复盘无效，回到待决定，需要换一个方案",
                               failed_plan=a["name"])
            if _metric_back(name, c):
                return _set(name, c["id"], "resolved", f"待办「{a['name']}」复盘无法判断，但指标已回到出问题前的水平，按已解决处理",
                            card=c, closed_date=today)
            return _reopen(name, c, s, "unknown", f"待办「{a['name']}」复盘无法判断，指标也没有回来，回到待决定",
                           failed_plan=a["name"])
        return None
    if st == "watch":
        if c["auto_status"] == "recovered":
            return _set(name, c["id"], "recovered", "观察期内指标回到出问题前的水平，自然恢复", card=c, closed_date=today)
        if _escalated(c, s):
            return _reopen(name, c, s, "escalated", f"观察期间升级为{sev}色，回到待决定")
        if s.get("watch_until") and today >= s["watch_until"]:
            if _metric_back(name, c):
                return _set(name, c["id"], "recovered", "观察期满，指标已回到出问题前的水平，自然恢复", card=c, closed_date=today)
            return _reopen(name, c, s, "watch_expired", "观察期满仍在触发，回到待决定")
        return None
    if st == "closed" and s.get("decision") in ("ignore", "known") and _escalated(c, s):
        return _reopen(name, c, s, "escalated", f"关闭后升级为{sev}色，重新待决定")
    return None


def derive(name: str, c: dict, s: dict | None, rows: dict) -> tuple[dict, dict | None]:
    """返回（状态信息，最新的状态行）。"""
    if s and s.get("status"):
        for _ in range(3):
            n = _advance(name, s, c)
            if not n:
                break
            s = n
        return dict(status=s["status"], decision=s.get("decision"), reason=s.get("reason"), auto=False), s
    if c.get("expected"):
        return dict(status="closed", decision="known", reason=c["expected"]["reason"], auto=True), s
    r = silenced_by(name, c, rows)
    if r:
        why = f"{r['closed_date'][5:]} 忽略过同类预警（{r.get('reason') or '未填原因'}），{_silence_days(name)} 天内不再提醒"
        return dict(status="closed", decision="ignore", reason=why, auto=True, silenced_by=r["card_id"]), s
    return dict(status="recovered" if c["auto_status"] == "recovered" else "pending", decision=None, reason=None, auto=False), s


def group_of(c: dict) -> str:
    if c["status"] in FINISHED:
        return "finished"
    if c["status"] in ("doing", "watch"):
        return c["status"]
    return "opportunity" if c.get("kind") == "opportunity" else "pending"


def linked_todos(name: str) -> dict:
    from . import todos
    out = {}
    for a in state.q("SELECT id, card_id, name, status, outcome, due_date FROM actions WHERE ds=? AND card_id IS NOT NULL "
                     "ORDER BY id", (name,)):
        out.setdefault(a["card_id"], []).append(dict(id=a["id"], name=a["name"], status=a["status"],
                                                      stage_name=todos.STAGE_NAMES.get(a["status"], a["status"]),
                                                      outcome=a.get("outcome"),
                                                      outcome_name=todos.OUTCOMES.get(a.get("outcome") or ""),
                                                      due_date=a.get("due_date")))
    return out


def _card_date(cid: str, year: int) -> pd.Timestamp | None:
    try:
        return pd.Timestamp(f"{year}-{cid[-4:-2]}-{cid[-2:]}")
    except ValueError:
        return None


def remap(name: str, cards: list[dict], rows: dict) -> dict:
    """调整门槛或规则开关后重新扫描，同一个问题的首次触发日可能变化（预警编号随之变化）。
    把旧编号上的处理状态、关联待办、推送记录、对话迁到同一商品、时间重叠的新预警上，避免处理过的预警「丢失」。"""
    ids = {c["id"] for c in cards}
    orphans = [r for r in rows.values() if r["card_id"] not in ids and r.get("product_id")]
    if not orphans:
        return rows
    year = _ds(name).as_of.year
    pushed = state.get_setting(f"alert_pushed__{name}", {}) or {}
    moved = False
    for c in cards:
        if c["id"] in rows:
            continue
        lo, hi = pd.Timestamp(c["first_date"]) - pd.Timedelta(days=14), pd.Timestamp(c["last_trigger"])
        cand = [r for r in orphans if r["product_id"] == c["product_id"]
                and (d := _card_date(r["card_id"], year)) is not None and lo <= d <= hi]
        if not cand:
            continue
        r = max(cand, key=lambda x: (x.get("status") not in FINISHED, x.get("updated_at") or 0))
        orphans.remove(r)
        old, new = r["card_id"], c["id"]
        state.x("UPDATE alert_state SET card_id=? WHERE ds=? AND card_id=?", (new, name, old))
        state.x("UPDATE actions SET card_id=? WHERE ds=? AND card_id=?", (new, name, old))
        if old in pushed:
            pushed[new] = pushed.pop(old)
        chat = state.cache_get(f"alertchat__{name}__{old}")
        if chat:
            state.cache_put(f"alertchat__{name}__{new}", chat)
            state.cache_del(f"alertchat__{name}__{old}")
        moved = True
    if moved:
        state.set_setting(f"alert_pushed__{name}", pushed)
        rows = state.card_states(name)
    return rows


def decorate(name: str, cards: list[dict]) -> list[dict]:
    rows = remap(name, cards, state.card_states(name))
    todo_map = linked_todos(name)
    as_of = _ds(name).as_of
    out = []
    for c0 in cards:
        c = dict(c0)
        info, s = derive(name, c, rows.get(c["id"]), rows)
        if s:
            rows[c["id"]] = s
        s = s or {}
        flags = _loads(s.get("flags_json"), {})
        c.update(status=info["status"], status_name=STATUS_NAMES[info["status"]], decision=info["decision"],
                 decision_name=DECISIONS.get(info["decision"] or ""), reason=info["reason"], auto_decided=info["auto"],
                 silenced_by=info.get("silenced_by"), watch_until=s.get("watch_until"), flags=flags,
                 reopen_name=REOPEN_TEXT.get(flags.get("last")) if info["status"] == "pending" and flags.get("last") else None,
                 log=_loads(s.get("log_json"), []), todos=todo_map.get(c["id"], []),
                 severity_name=alerts.SEV_NAME[c["severity"]], store=c["product_id"] == STORE,
                 signature=f"{c['severity']}|{int(flags.get('reopen', 0))}",
                 days_open=int((as_of - pd.Timestamp(c["first_date"])).days) + 1)
        cur = next((t for t in reversed(c["todos"]) if t["id"] == s.get("action_row")), None)
        c["todo"] = cur
        c["ignore_reason"] = c["reason"] if c["decision"] == "ignore" else None
        c["group"] = group_of(c)
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# 决定
# ---------------------------------------------------------------------------
def _card(name, cid) -> dict:
    from . import data
    c = next((x for x in data.cards(name, include_suppressed=True) if x["id"] == cid), None)
    if not c:
        raise KeyError("预警不存在")
    return c


def decide(name: str, cid: str, type_: str, reason: str | None = None, days: int | None = None, by: str = "我") -> dict:
    c = _card(name, cid)
    if c["status"] not in ("pending", "watch"):
        raise ValueError(f"这条预警{c['status_name']}，不需要再做决定")
    reason = (reason or "").strip()
    today = _d(_ds(name).as_of)
    if type_ == "known":
        if not reason:
            raise ValueError("请写一句已知的原因")
        _set(name, cid, "closed", f"已知原因：{reason}", by=by, card=c, decision="known", reason=reason, closed_date=today,
             watch_until=None)
    elif type_ == "ignore":
        if not reason:
            raise ValueError("请选择忽略原因")
        _set(name, cid, "closed", f"忽略：{reason}（同类预警 {_silence_days(name)} 天内不再提醒）", by=by, card=c,
             decision="ignore", reason=reason, closed_date=today, watch_until=None)
    elif type_ == "watch":
        if int(days or 0) not in WATCH_DAYS:
            raise ValueError("观察天数只能是 1、3 或 7 天")
        until = _d(_ds(name).as_of + pd.Timedelta(days=int(days)))
        _set(name, cid, "watch", f"先观察 {days} 天（到 {until[5:]}）" + (f"：{reason}" if reason else ""), by=by, card=c,
             decision="watch", reason=reason or None, watch_until=until)
    elif type_ == "todo":
        raise ValueError("转待办请使用「用当前方案转待办」")
    else:
        raise ValueError("未知的决定")
    return _card(name, cid)


def on_todo(name: str, cid: str, aid: int, plan_name: str | None):
    """待办创建后回写预警：处理中。"""
    try:
        c = _card(name, cid)
    except KeyError:
        c = None
    _set(name, cid, "doing", f"转待办：{plan_name or '待办'}", by="我", card=c, decision="todo", action_row=aid,
         watch_until=None, closed_date=None)


# ---------------------------------------------------------------------------
# 统计（预警设置页用）
# ---------------------------------------------------------------------------
def rule_stats(name: str, cards: list[dict]) -> dict:
    """近 14 天每条规则：出现在几条预警里、其中被忽略几条。"""
    out = {}
    for c in cards:
        ignored = c["status"] == "closed" and c["decision"] == "ignore" and not c.get("auto_decided")
        for r in c["rules"]:
            x = out.setdefault(r, dict(cards=0, ignored=0, trigger_days=0))
            x["cards"] += 1
            x["ignored"] += int(ignored)
            x["trigger_days"] += sum(1 for h in c.get("history") or [] if r in h.get("rules", []))
    return out
