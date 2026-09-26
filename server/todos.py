"""待办：AI 诊断方案之外的待办（追问中产生的、运营手动新建的），以及待办的轻量编辑。

自定义待办与诊断方案共用同一套数据结构（actions 表 + plan），因此同样拆分我的步骤与协同事项、
生成转交单、完成后自动跟踪效果、进入周报。自定义待办不走动作库的参数测算。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading

from core import actions as core_actions
from core.weekly import METRIC_NAMES

from . import collab, data, llm_client, state

ROLES = ["我", "供应链", "投放运营", "商品主管"]
TRACK_METRICS = ["gmv", "cvr", "uv", "aov", "units", "rating", "uv_paid", "uv_search"]
CAUSE_NAMES = {"chat": "追问发现", "manual": "临时事项"}
DEFAULT_DUE_DAYS = 3


def default_due(name: str, days: int = DEFAULT_DUE_DAYS) -> str:
    return (data.ds_of(name).as_of + dt.timedelta(days=days)).strftime("%Y-%m-%d")


def _clean_steps(steps: list) -> list[dict]:
    out = []
    for s in steps or []:
        if isinstance(s, str):
            s = {"text": s}
        text = str(s.get("text") or "").strip()
        if not text:
            continue
        by = s.get("by") if s.get("by") in ROLES else "我"
        out.append(dict(text=text[:200], by=by))
    return out


def build_plan(name: str, pid: str, title: str, steps: list[dict], track_metric: str, source: str) -> dict:
    ds = data.ds_of(name)
    steps = _clean_steps(steps)
    metric = track_metric if track_metric in METRIC_NAMES else "gmv"
    plan = dict(action_id="custom", name=title.strip()[:60], cause="custom", cause_name=CAUSE_NAMES.get(source, "临时事项"),
                target=ds.product(pid)["product_name"], steps=[s["text"] for s in steps],
                step_owners=[s["by"] for s in steps], exec_type="", params_text=[], risks=[],
                track=dict(metric=metric, metric_name=METRIC_NAMES[metric], days=5), source=source)
    return core_actions.annotate(plan)


def create(name: str, pid: str, title: str, steps: list, due_date: str | None, track_metric: str | None,
           note: str | None, source: str, context: dict | None, base_url: str | None) -> int:
    ds = data.ds_of(name)
    if pid not in ds.product_ids():
        raise ValueError("未知商品")
    if not (title or "").strip():
        raise ValueError("请填写待办名称")
    if not _clean_steps(steps):
        raise ValueError("至少填写一个步骤")
    plan = build_plan(name, pid, title, steps, track_metric or "gmv", source)
    today = ds.as_of.strftime("%Y-%m-%d")
    ctx = dict(context or {})
    if not ctx.get("summary") and note:
        ctx["summary"] = note
    aid = state.add_action(ds=name, card_id=None, product_id=pid, product_name=ds.product(pid)["product_name"],
                           action_id="custom", name=plan["name"], cause="custom", cause_name=plan["cause_name"],
                           target=plan["target"], plan_json=json.dumps(plan, ensure_ascii=False), exec_type=plan["exec_type"],
                           owner_role=plan["owner_role"], status="adopted", track_metric=plan["track"]["metric"],
                           variant=None, adopted_date=today, context_json=json.dumps(ctx, ensure_ascii=False),
                           due_date=due_date or default_due(name), note=(note or "").strip() or None, source=source)
    collab.create_for_action(name, aid, plan, ctx, base_url, due=due_date or default_due(name))
    collab.add_log(aid, {"chat": "从 AI 追问创建待办", "manual": "手动新建待办"}.get(source, "创建待办"))
    return aid


# ---------------------------------------------------------------------------
# 轻量编辑：名称、截止日期、备注、状态
# ---------------------------------------------------------------------------
STATUS_TEXT = {"executed": "标记为已执行", "cancelled": "取消待办", "adopted": "重新打开"}


def update(name: str, aid: int, *, title=None, due_date=None, note=None, status=None, exec_date=None, reason=None):
    a = state.get_action(aid)
    if not a:
        raise KeyError(aid)
    changes, logs = {}, []
    if title is not None and title.strip() and title.strip() != a["name"]:
        changes["name"] = title.strip()[:60]
        logs.append(f"名称改为「{changes['name']}」")
        plan = json.loads(a.get("plan_json") or "{}")
        plan["name"] = changes["name"]
        changes["plan_json"] = json.dumps(plan, ensure_ascii=False)
    if due_date is not None and due_date != (a.get("due_date") or ""):
        changes["due_date"] = due_date or None
        logs.append(f"截止日期改为 {due_date}" if due_date else "清除截止日期")
    if note is not None and note.strip() != (a.get("note") or ""):
        changes["note"] = note.strip() or None
        logs.append("更新备注")
    if status and status != a["status"]:
        if status == "executed":
            if a["status"] in ("rejected", "cancelled"):
                raise ValueError("已驳回或已取消的待办不能标记执行")
            changes.update(status="executed", exec_date=exec_date or data.ds_of(name).as_of.strftime("%Y-%m-%d"))
        elif status == "cancelled":
            if a["status"] in ("executed", "rejected"):
                raise ValueError("已执行或已驳回的待办不能取消")
            changes["status"] = "cancelled"
        elif status == "adopted":
            if a["status"] != "cancelled":
                raise ValueError("只有已取消的待办可以重新打开")
            changes["status"] = "adopted"
        else:
            changes["status"] = status
        logs.append(STATUS_TEXT.get(status, "状态改为 " + status) + (f"：{reason}" if reason else ""))
    if changes:
        state.update_action(aid, **changes)
        for t in logs:
            collab.add_log(aid, t)
        # 协同事项与飞书同步（飞书调用放到后台，不拖慢页面）
        events = []
        if "due_date" in changes and changes["due_date"]:
            collab.set_due(aid, changes["due_date"])
            events.append(("due", changes["due_date"]))
        if "name" in changes:
            events.append(("rename", ""))
        if changes.get("status") == "cancelled":
            collab.cancel_all(aid, reason)
            events.append(("cancel", reason or ""))
        if changes.get("status") == "adopted":
            collab.reopen_all(aid)
            collab._refresh_action(name, aid)
            events.append(("reopen", ""))
        if events:
            from . import notify
            threading.Thread(target=lambda: [notify.changed(aid, w, d) for w, d in events], daemon=True).start()
    return state.get_action(aid)


# ---------------------------------------------------------------------------
# 从追问生成待办草稿
# ---------------------------------------------------------------------------
TODO_RE = re.compile(r"<todo>\s*(\{.*?\})\s*</todo>", re.S)

SUGGEST_RULE = """
如果你的回答建议运营去做具体的事情（需要有人执行的动作），在回答最后另起一行，附上一个待办建议，格式严格如下（整段放在 <todo> 标签内，JSON 不换行也可以）：
<todo>{"name": "待办名称，15 字以内", "steps": [{"text": "具体步骤", "by": "我|供应链|投放运营|商品主管"}], "track_metric": "gmv|cvr|uv|aov|units|rating", "due_days": 3}</todo>
- 步骤 1–4 步，每步写清楚做什么、对象是什么；需要其他角色做的步骤把 by 写成对应角色。
- 只是解释原因、回答数据问题时，不要附待办建议。
- 待办建议不要在正文里再重复描述。"""


def split_suggestion(text: str, name: str) -> tuple[str, dict | None]:
    """把回答中的 <todo> 建议取出来，返回（正文，草稿）。"""
    m = TODO_RE.search(text or "")
    if not m:
        return text, None
    body = (text[:m.start()] + text[m.end():]).strip()
    try:
        raw = json.loads(m.group(1))
    except json.JSONDecodeError:
        return body, None
    return body, normalize_draft(raw, name)


def normalize_draft(raw: dict, name: str) -> dict | None:
    steps = _clean_steps(raw.get("steps") or [])
    title = str(raw.get("name") or "").strip()
    if not title or not steps:
        return None
    try:
        days = max(0, min(30, int(raw.get("due_days", DEFAULT_DUE_DAYS))))
    except (TypeError, ValueError):
        days = DEFAULT_DUE_DAYS
    metric = raw.get("track_metric") if raw.get("track_metric") in TRACK_METRICS else "gmv"
    return dict(name=title[:60], steps=steps, track_metric=metric, due_date=default_due(name, days),
                note=str(raw.get("note") or "").strip()[:300])


DRAFT_SYSTEM = """你负责把一段商品运营对话整理成一条待办。只输出一个 JSON 对象，不要其他文字：
{"name": "待办名称，15 字以内", "steps": [{"text": "具体步骤", "by": "我|供应链|投放运营|商品主管"}], "track_metric": "gmv|cvr|uv|aov|units|rating", "due_days": 3, "note": "一句话说明为什么要做（引用对话中的数字）"}
- 步骤 1–4 步，来自对话内容，不要编造对话里没有的数字。
- 由商品运营自己完成的步骤 by 写「我」；需要补货或排查批次写「供应链」；调整推广写「投放运营」；超出权限的调价等写「商品主管」。"""


def _heuristic(reply: str, question: str, name: str) -> dict:
    lines = [re.sub(r"^\s*(?:[-*•·]|\d+[.、)])\s*", "", x).strip() for x in (reply or "").splitlines()]
    lines = [re.sub(r"[*#`]", "", x) for x in lines if x]
    bullets = [x for x in lines if 6 <= len(x) <= 120 and not x.endswith(("：", ":"))][:4] or [(reply or question)[:120]]
    title = re.sub(r"[？?。！!]$", "", (question or bullets[0]).strip())[:20] or "追问待办"
    return dict(name=title, steps=[dict(text=b, by="我") for b in bullets], track_metric="gmv",
                due_date=default_due(name), note="")


def draft_from_chat(name: str, pid: str, messages: list[dict], reply: str) -> dict:
    """把一条 AI 回复（及其前面的问题）整理成待办草稿：有在线模型时由模型整理，否则按回复的条目拆步骤。"""
    question = next((m.get("content", "") for m in reversed(messages or []) if m.get("role") == "user"), "")
    reply = TODO_RE.sub("", reply or "").strip()
    if llm_client.mode() == "live":
        p = data.ds_of(name).product(pid)
        msgs = [{"role": "system", "content": DRAFT_SYSTEM},
                {"role": "user", "content": f"商品：{p['product_name']}\n运营的问题：{question}\n\nAI 的回答：\n{reply}"}]
        try:
            msg = llm_client.chat(msgs)
            t = msg.get("content") or ""
            i, j = t.find("{"), t.rfind("}")
            d = normalize_draft(json.loads(t[i:j + 1]), name)
            if d:
                return d
        except Exception as e:  # noqa: BLE001
            print("[todos] 模型整理待办失败，改用规则拆分：", repr(e), flush=True)
    return _heuristic(reply, question, name)
