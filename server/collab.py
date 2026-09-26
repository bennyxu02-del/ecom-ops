"""协同：方案采纳后拆出「我的步骤」和需要别人参与的事项（转交 / 审批），跟踪状态直到全部完成。

状态流转
  转交：draft 待发送 → sent 已发送 → received 已接收 → done 已完成（任一环节可 question 有疑问）
  审批：draft 待发送 → sent 待审批 → approved 已批准 / declined 已驳回
动作整体：所有「我的步骤」勾完 + 转交全部 done + 审批全部 approved → 自动记为已执行，开始跟踪效果；
审批被驳回 → 动作记为审批未通过。
"""
from __future__ import annotations

import datetime as dt
import json
import time

from core import actions as core_actions

from . import data, state

SELF = core_actions.SELF

HANDOFF_STATUS = {
    "transfer": {"draft": "待发送", "sent": "已发送", "received": "已接收", "done": "已完成", "question": "有疑问",
                 "cancelled": "已取消"},
    "approval": {"draft": "待发送", "sent": "待审批", "approved": "已批准", "declined": "已驳回", "question": "有疑问",
                 "cancelled": "已取消"},
}
CLOSED = ("done", "approved", "declined", "cancelled")
ACTION_STATUS = {"adopted": "进行中", "executed": "已执行", "transferred": "已转交", "rejected": "已驳回",
                 "declined": "审批未通过", "cancelled": "已取消"}
RESPONSES = {"transfer": {"received", "done", "question"}, "approval": {"approved", "declined", "question"}}


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except (TypeError, json.JSONDecodeError):
        return default


def _fmt_money(v):
    return f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)


# ---------------------------------------------------------------------------
# 转交单 / 审批申请的文字
# ---------------------------------------------------------------------------
def compose(kind: str, role: str, plan: dict, step_idx: list[int], context: dict, product_name: str,
            due: str, link: str | None) -> str:
    summary = (context or {}).get("summary") or ""
    evidence = [e for e in (context or {}).get("evidence") or [] if e][:4]
    steps = plan.get("steps") or []
    lines = []
    if kind == "approval":
        lines += [f"【审批申请】{product_name}：{plan.get('name')}", ""]
        if summary:
            lines += [f"背景：{summary}", ""]
        if plan.get("params_text"):
            lines += ["方案内容：" + "；".join(plan["params_text"]), ""]
        lines += ["需要审批的原因：" + "；".join(plan.get("approval_reasons") or [])]
        e = plan.get("estimate") or {}
        if e.get("type") == "price":
            lines += [f"测算：单件毛利 ¥{e.get('unit_margin_before')} → ¥{e.get('unit_margin_after')}，"
                      f"执行后毛利率 {e.get('margin_rate_after', 0) * 100:.0f}%，"
                      + (f"保本需销量提升 {e['breakeven_lift'] * 100:.1f}%" if e.get("breakeven_lift") is not None else "")]
        lines += ["", f"请在 {due} 前批准或驳回。"]
    else:
        lines += [f"【协同请求】{product_name} · {plan.get('cause_name', '')}", ""]
        if summary:
            lines += [f"情况：{summary}", ""]
        if evidence:
            lines += ["数据依据："] + [f"· {x}" for x in evidence] + [""]
        lines += [f"需要{role}协助："]
        lines += [f"{n}. {steps[i]}" for n, i in enumerate(step_idx, 1) if i < len(steps)]
        lines += ["", f"希望在 {due} 前反馈处理结果，谢谢！"]
    if link:
        lines += ["", f"处理入口：{link}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 采纳时创建协同事项
# ---------------------------------------------------------------------------
def draft_handoffs(name: str, pid: str, plan: dict, context: dict, due: str | None = None,
                   base_url: str | None = None, hid_of=None) -> list[dict]:
    """按方案拆出需要别人参与的事项，并生成转交单 / 审批申请的文字（保存前预览与保存共用）。"""
    ds = data.ds_of(name)
    core_actions.annotate(plan)
    as_of = ds.as_of.date() if hasattr(ds.as_of, "date") else ds.as_of
    product_name = ds.product(pid)["product_name"]
    out = []
    for h in plan.get("handoffs") or []:
        h_due = due or (as_of if h["kind"] == "approval" else as_of + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        out.append(dict(kind=h["kind"], role=h["role"], steps=h["steps"], due=h_due,
                        message=compose(h["kind"], h["role"], plan, h["steps"], context, product_name, h_due, None)))
    return out


def create_for_action(name: str, action_row: int, plan: dict, context: dict, base_url: str | None,
                      due: str | None = None):
    pid = plan_pid(plan, action_row)
    ds = data.ds_of(name)
    product_name = ds.product(pid)["product_name"]
    roles = state.get_setting("feishu_roles", {}) or {}
    for h in draft_handoffs(name, pid, plan, context, due):
        hid = state.add_handoff(ds=name, action_row=action_row, product_id=pid, kind=h["kind"],
                                role=h["role"], assignee=(roles.get(h["role"]) or {}).get("name"),
                                steps_json=json.dumps(h["steps"]), message="", status="draft", channel=None, due=h["due"],
                                history_json=json.dumps([dict(t=time.time(), status="draft", by="系统", note="创建待办时生成")],
                                                        ensure_ascii=False))
        link = f"{base_url.rstrip('/')}/#/h/{hid}" if base_url else None
        msg = compose(h["kind"], h["role"], plan, h["steps"], context, product_name, h["due"], link)
        state.update_handoff(hid, message=msg)


def plan_pid(plan: dict, action_row: int) -> str:
    a = state.get_action(action_row)
    return a["product_id"] if a else plan.get("product_id")


# ---------------------------------------------------------------------------
# 状态变更
# ---------------------------------------------------------------------------
def _push_history(h: dict, status: str, by: str, note: str | None = None, **extra):
    hist = _loads(h.get("history_json"), [])
    hist.append(dict(t=time.time(), status=status, by=by, note=note or extra.get("note")))
    state.update_handoff(h["id"], status=status, history_json=json.dumps(hist, ensure_ascii=False), **extra)


def mark_sent(hid: int, channel: str, message: str | None = None, ext_id: str | None = None):
    h = state.get_handoff(hid)
    extra = dict(channel=channel, sent_at=time.time())
    if message:
        extra["message"] = message
    if ext_id:
        extra["ext_id"] = ext_id
    via = {"feishu": "飞书", "copy": "复制发送"}.get(channel, channel)
    _push_history(h, "sent", by="我", note=f"通过{via}发出", **extra)
    _refresh_action(h["ds"], h["action_row"])


def respond(hid: int, status: str, note: str | None = None, by: str | None = None):
    h = state.get_handoff(hid)
    if status not in RESPONSES[h["kind"]]:
        raise ValueError("不支持的处理结果")
    if h["status"] == "draft":
        raise ValueError("该事项尚未发送")
    if h["status"] == "cancelled":
        raise ValueError("这条协同请求已取消，无需处理")
    _push_history(h, status, by=by or h["role"], note=note)
    if note:
        state.update_handoff(hid, note=note)          # 最新反馈（卡片与待办中心展示）
    _refresh_action(h["ds"], h["action_row"])
    return state.get_handoff(hid)


def cancel_all(action_row: int, reason: str | None = None):
    """待办取消：未完成的协同事项一并关闭（记录原状态，重新打开时恢复）。"""
    for h in state.list_handoffs(action_row=action_row):
        if h["status"] not in CLOSED:
            _push_history(h, "cancelled", by="我", note="待办已取消" + (f"：{reason}" if reason else ""))


def reopen_all(action_row: int):
    for h in state.list_handoffs(action_row=action_row):
        if h["status"] == "cancelled":
            hist = _loads(h.get("history_json"), [])
            prev = next((x["status"] for x in reversed(hist[:-1]) if x.get("status") != "cancelled"), "draft")
            _push_history(h, prev, by="我", note="待办重新打开")


def set_due(action_row: int, due: str):
    """截止日期调整：未完成的协同事项同步新的截止日期（转交单正文里的日期一并替换）。"""
    for h in state.list_handoffs(action_row=action_row):
        if h["status"] in CLOSED or not due:
            continue
        msg = (h.get("message") or "")
        if h.get("due"):
            msg = msg.replace(f"在 {h['due']} 前", f"在 {due} 前")
        state.update_handoff(h["id"], due=due, message=msg)


def set_step(action_row: int, index: int, done: bool):
    a = state.get_action(action_row)
    got = set(_loads(a.get("step_done"), []))
    got.add(index) if done else got.discard(index)
    state.update_action(action_row, step_done=json.dumps(sorted(got)))
    _refresh_action(a["ds"], action_row)


def _refresh_action(name: str, action_row: int):
    """按我的步骤与协同事项的状态更新动作整体状态。"""
    a = state.get_action(action_row)
    if not a or a["status"] in ("rejected", "executed", "cancelled"):
        return
    hs = state.list_handoffs(action_row=action_row)
    if any(h["kind"] == "approval" and h["status"] == "declined" for h in hs):
        state.update_action(action_row, status="declined")
        return
    if a["status"] == "declined":
        state.update_action(action_row, status="adopted")
    if progress(a, hs)["complete"]:
        today = data.ds_of(name).as_of.strftime("%Y-%m-%d")
        state.update_action(action_row, status="executed", exec_date=today)
        add_log(action_row, "步骤与协同全部完成，自动记为已执行", by="系统")


def progress(a: dict, hs: list[dict]) -> dict:
    plan = _loads(a.get("plan_json"), {})
    owners = plan.get("step_owners") or []
    mine = [i for i, o in enumerate(owners) if o == SELF]
    done = set(_loads(a.get("step_done"), []))
    my_done = [i for i in mine if i in done]
    ok = lambda h: h["status"] == ("approved" if h["kind"] == "approval" else "done")
    complete = bool(owners) and len(my_done) == len(mine) and all(ok(h) for h in hs)
    approval_pending = any(h["kind"] == "approval" and h["status"] != "approved" for h in hs)
    return dict(mine=mine, mine_done=my_done, complete=complete, approval_pending=approval_pending)


# ---------------------------------------------------------------------------
# 输出给前端
# ---------------------------------------------------------------------------
def decorate_handoff(h: dict, with_context: bool = False) -> dict:
    h = dict(h)
    h["steps"] = _loads(h.pop("steps_json", None), [])
    h["history"] = _loads(h.pop("history_json", None), [])
    h["status_name"] = HANDOFF_STATUS.get(h["kind"], {}).get(h["status"], h["status"])
    h["kind_name"] = "审批" if h["kind"] == "approval" else "转交"
    if with_context:
        a = state.get_action(h["action_row"])
        if a:
            plan = _loads(a.get("plan_json"), {})
            h["action"] = dict(id=a["id"], name=a["name"], product_name=a["product_name"], product_id=a["product_id"],
                               cause_name=a.get("cause_name"), target=a.get("target"), status=a["status"],
                               status_name=ACTION_STATUS.get(a["status"], a["status"]))
            h["step_texts"] = [(plan.get("steps") or [""])[i] for i in h["steps"] if i < len(plan.get("steps") or [])]
            h["plan"] = dict(name=plan.get("name"), params_text=plan.get("params_text") or [],
                             estimate=plan.get("estimate"), approval_reasons=plan.get("approval_reasons") or [])
    return h


def add_log(action_row: int, text: str, by: str = "我"):
    a = state.get_action(action_row)
    if not a:
        return
    log = _loads(a.get("log_json"), [])
    log.append(dict(t=time.time(), by=by, text=text))
    state.update_action(action_row, log_json=json.dumps(log, ensure_ascii=False))


SOURCE_NAMES = {"diagnosis": "AI 诊断", "chat": "追问", "manual": "手动新建"}


def decorate_action(a: dict) -> dict:
    hs = state.list_handoffs(action_row=a["id"])
    a["log"] = _loads(a.pop("log_json", None), [])
    a["source"] = a.get("source") or "diagnosis"
    a["source_name"] = SOURCE_NAMES.get(a["source"], a["source"])
    a["handoffs"] = [decorate_handoff(h) for h in hs]
    p = progress(a, hs)
    a["progress"] = p
    a["step_done"] = _loads(a.get("step_done"), [])
    a["status_name"] = ACTION_STATUS.get(a["status"], a["status"])
    return a
