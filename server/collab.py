"""分给同事的步骤：待办里负责人不是「我」的步骤，按角色归并成一条协同事项（对应一张飞书卡片）。

协同事项状态（v9）
  pending 未通知（对方没绑定飞书，等我复制文字手动发）
  notified 已通知 → done 已完成（对方点，或我代为标记）
  notified ⇄ question 有疑问（对方提问 → 我回复后回到已通知）
  cancelled 已取消（待办取消时）
待办阶段的推进（执行中 → 跟踪中）由 todos.refresh 负责。
"""
from __future__ import annotations

import json
import time

from core import actions as core_actions

from . import data, state

SELF = core_actions.SELF
HANDOFF_STATUS = {"pending": "未通知", "notified": "已通知", "question": "有疑问", "done": "已完成", "cancelled": "已取消"}
OPEN = ("pending", "notified", "question")
CLOSED = ("done", "cancelled")
RESPONSES = {"done", "question"}


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except (TypeError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------------------
# 协同卡片的文字
# ---------------------------------------------------------------------------
def compose(role: str, plan: dict, step_idx: list[int], context: dict, product_name: str,
            due: str, link: str | None, sender: str | None = None) -> str:
    """发给同事的协同消息：谁发起、出了什么事（带数据）、要对方做什么、要反馈什么、截止、整体分工。"""
    summary = ((context or {}).get("summary") or "").strip()
    evidence = [e for e in (context or {}).get("evidence") or [] if e][:4]
    steps = plan.get("steps") or []
    owners = plan.get("step_owners") or []
    lines = [f"【协同请求】{product_name} · {plan.get('name', '')}",
             f"{sender or '商品运营'}发起，需要{role}协助完成下面的事项。", ""]
    if summary or evidence:
        lines.append("【背景】")
        if summary:
            lines.append(summary)
        lines += [f"· {x}" for x in evidence]
        lines.append("")
    lines.append("【需要你做的】")
    lines += [f"{n}. {steps[i]}" for n, i in enumerate(step_idx, 1) if i < len(steps)]
    lines += ["完成后点「已完成」；需要补充结果（如时间、数量、排查结论）或有问题，点「有疑问 / 补充说明」。", "",
              f"【截止】{due}"]
    rest = [(o, t) for k, (o, t) in enumerate(zip(owners, steps)) if k not in step_idx]
    if rest:
        lines += ["", "【整体分工】"]
        lines += [f"· {'我（' + (sender or '商品运营') + '）' if o == SELF else o}：{t}" for o, t in rest]
    if link:
        lines += ["", f"处理入口：{link}"]
    return "\n".join(lines)


def _sender() -> str | None:
    return ((state.get_setting("feishu_roles", {}) or {}).get("我") or {}).get("name")


def draft_handoffs(name: str, pid: str, plan: dict, context: dict, due: str) -> list[dict]:
    """按步骤负责人归并出协同事项与消息文字（保存前预览与保存共用）。"""
    core_actions.annotate(plan)
    product_name = data.ds_of(name).product(pid)["product_name"]
    return [dict(role=h["role"], steps=h["steps"], due=due,
                 message=compose(h["role"], plan, h["steps"], context, product_name, due, None, _sender()))
            for h in plan.get("handoffs") or []]


def create_for_action(name: str, action_row: int, pid: str, plan: dict, context: dict, due: str,
                      base_url: str | None):
    product_name = data.ds_of(name).product(pid)["product_name"]
    roles = state.get_setting("feishu_roles", {}) or {}
    for h in draft_handoffs(name, pid, plan, context, due):
        hid = state.add_handoff(ds=name, action_row=action_row, product_id=pid, kind="transfer",
                                role=h["role"], assignee=(roles.get(h["role"]) or {}).get("name"),
                                steps_json=json.dumps(h["steps"]), message="", status="pending", channel=None, due=due,
                                history_json=json.dumps([dict(t=time.time(), status="pending", by="系统", note="创建待办")],
                                                        ensure_ascii=False))
        link = f"{base_url.rstrip('/')}/#/h/{hid}" if base_url else None
        state.update_handoff(hid, message=compose(h["role"], plan, h["steps"], context, product_name, due, link, _sender()))


# ---------------------------------------------------------------------------
# 状态变更
# ---------------------------------------------------------------------------
def _push_history(h: dict, status: str, by: str, note: str | None = None, **extra):
    hist = _loads(h.get("history_json"), [])
    hist.append(dict(t=time.time(), status=status, by=by, note=note))
    state.update_handoff(h["id"], status=status, history_json=json.dumps(hist, ensure_ascii=False), **extra)


def _refresh(h: dict):
    from . import todos          # 避免循环引用
    todos.refresh(h["ds"], h["action_row"])


def mark_sent(hid: int, channel: str, message: str | None = None, ext_id: str | None = None):
    h = state.get_handoff(hid)
    extra = dict(channel=channel, sent_at=time.time())
    if message:
        extra["message"] = message
    if ext_id:
        extra["ext_id"] = ext_id
    via = {"feishu": "飞书推送", "copy": "复制文字手动发送"}.get(channel, channel)
    _push_history(h, "notified", by="我", note=via, **extra)
    add_log(h["action_row"], f"通知{h['role']}（{via}）")


def respond(hid: int, status: str, note: str | None = None, by: str | None = None):
    h = state.get_handoff(hid)
    if status not in RESPONSES:
        raise ValueError("不支持的处理结果")
    if h["status"] == "pending":
        raise ValueError("该事项尚未发出")
    if h["status"] == "cancelled":
        raise ValueError("这条协同请求已取消，无需处理")
    if h["status"] == "done":
        raise ValueError("这条协同请求已经完成")
    if status == "question" and not (note or "").strip():
        raise ValueError("请写下你的疑问")
    _push_history(h, status, by=by or h["role"], note=note)
    if note:
        state.update_handoff(hid, note=note)
    add_log(h["action_row"], ("已完成" if status == "done" else "提出疑问") + (f"：{note}" if note else ""), by=h["role"])
    _refresh(h)
    return state.get_handoff(hid)


def proxy_done(hid: int):
    """同事迟迟不点完成：我代为标记。"""
    h = state.get_handoff(hid)
    if h["status"] not in OPEN:
        raise ValueError("该事项不需要代为完成")
    _push_history(h, "done", by="我", note="发起人代为确认完成")
    add_log(h["action_row"], f"代{h['role']}标记完成")
    _refresh(h)
    return state.get_handoff(hid)


def cancel_all(action_row: int, reason: str | None = None):
    for h in state.list_handoffs(action_row=action_row):
        if h["status"] in OPEN:
            _push_history(h, "cancelled", by="我", note="待办已取消" + (f"：{reason}" if reason else ""))


def set_due(action_row: int, due: str):
    for h in state.list_handoffs(action_row=action_row):
        if h["status"] in CLOSED or not due:
            continue
        msg = (h.get("message") or "")
        if h.get("due"):
            msg = msg.replace(f"【截止】{h['due']}", f"【截止】{due}").replace(f"在 {h['due']} 前", f"在 {due} 前")
        state.update_handoff(h["id"], due=due, message=msg)


def set_step(action_row: int, index: int, done: bool):
    a = state.get_action(action_row)
    got = set(_loads(a.get("step_done"), []))
    got.add(index) if done else got.discard(index)
    state.update_action(action_row, step_done=json.dumps(sorted(got)))
    from . import todos
    todos.refresh(a["ds"], action_row)


def progress(a: dict, hs: list[dict]) -> dict:
    plan = _loads(a.get("plan_json"), {})
    owners = plan.get("step_owners") or []
    mine = [i for i, o in enumerate(owners) if o == SELF]
    done = set(_loads(a.get("step_done"), []))
    my_done = [i for i in mine if i in done]
    live = [h for h in hs if h["status"] != "cancelled"]
    others_done = [h for h in live if h["status"] == "done"]
    complete = bool(owners) and len(my_done) == len(mine) and len(others_done) == len(live)
    return dict(mine=mine, mine_done=my_done, others=len(live), others_done=len(others_done), complete=complete)


# ---------------------------------------------------------------------------
# 输出给前端
# ---------------------------------------------------------------------------
def was_proxy(h: dict) -> bool:
    hist = h["history"] if "history" in h else _loads(h.get("history_json"), [])
    return bool(hist) and hist[-1].get("status") == "done" and hist[-1].get("by") == "我"


def decorate_handoff(h: dict, with_context: bool = False) -> dict:
    h = dict(h)
    h["steps"] = _loads(h.pop("steps_json", None), [])
    h["history"] = _loads(h.pop("history_json", None), [])
    h["status_name"] = HANDOFF_STATUS.get(h["status"], h["status"])
    h["proxy"] = was_proxy(h)
    if h["proxy"]:
        h["status_name"] = "已完成（代标记）"
    if with_context:
        a = state.get_action(h["action_row"])
        if a:
            from . import todos
            plan = _loads(a.get("plan_json"), {})
            h["action"] = dict(id=a["id"], name=a["name"], product_name=a["product_name"], product_id=a["product_id"],
                               cause_name=a.get("cause_name"), target=a.get("target"), status=a["status"],
                               status_name=todos.STAGE_NAMES.get(a["status"], a["status"]))
            h["step_texts"] = [(plan.get("steps") or [""])[i] for i in h["steps"] if i < len(plan.get("steps") or [])]
    return h


def add_log(action_row: int, text: str, by: str = "我"):
    a = state.get_action(action_row)
    if not a:
        return
    log = _loads(a.get("log_json"), [])
    log.append(dict(t=time.time(), by=by, text=text))
    state.update_action(action_row, log_json=json.dumps(log, ensure_ascii=False))
