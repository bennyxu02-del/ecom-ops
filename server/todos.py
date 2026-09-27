"""待办（v9）：一条待办只走 4 步——执行中 → 跟踪中 → 待复盘 → 已完成，执行中可以取消。

- 三个入口（采纳 AI 方案 / 对话生成 / 手动新建）都走 create()，结构相同。
- 分工 = 步骤的负责人：我的步骤我来勾；同事的步骤按角色归并成协同事项，推送飞书（collab / notify）。
- 所有业务日期（截止、执行、跟踪、逾期）按业务日期 = 数据截止日计算。
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import threading

import pandas as pd

from core import actions as core_actions
from core import tools
from core.weekly import METRIC_NAMES, effect

from . import collab, data, llm_client, state

ROLES = ["我", "供应链", "投放运营"]
TRACK_METRICS = ["gmv", "cvr", "uv", "aov", "units", "rating", "uv_paid", "uv_search"]
STAGE_NAMES = {"doing": "执行中", "tracking": "跟踪中", "review": "待复盘", "done": "已完成", "cancelled": "已取消"}
OUTCOMES = {"effective": "有效", "ineffective": "无效", "unknown": "无法判断"}
SOURCE_NAMES = {"diagnosis": "AI 诊断", "chat": "对话", "manual": "手动新建", "report": "报告"}
CAUSE_NAMES = {"chat": "对话发现", "manual": "临时事项"}
DEFAULT_DUE_DAYS = 3
DEFAULT_TRACK_DAYS = 7
INTERFERENCE = {"campaign_start": "活动开始", "campaign_end": "活动结束", "promo_day": "大促",
                "price_change": "自家调价", "competitor_price_change": "竞品调价"}


def today(name: str) -> str:
    return data.ds_of(name).as_of.strftime("%Y-%m-%d")


def default_due(name: str, days: int = DEFAULT_DUE_DAYS) -> str:
    return (data.ds_of(name).as_of + dt.timedelta(days=days)).strftime("%Y-%m-%d")


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except (TypeError, json.JSONDecodeError):
        return default


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


# ---------------------------------------------------------------------------
# 创建（三个入口共用）
# ---------------------------------------------------------------------------
def build_plan(name: str, pid: str, title: str, steps: list, track_metric: str | None, track_days: int | None,
               source: str, base: dict | None = None) -> dict:
    ds = data.ds_of(name)
    steps = _clean_steps(steps)
    if base:
        plan = core_actions.annotate(copy.deepcopy(base))
        if steps:
            plan["steps"] = [s["text"] for s in steps]
            plan["step_owners"] = [s["by"] for s in steps]
        track = dict(plan.get("track") or {})
        track["days"] = int(track_days or track.get("days") or 5)
        plan["track"] = track
    else:
        metric = track_metric if track_metric in METRIC_NAMES else "gmv"
        plan = dict(action_id="custom", cause="custom", cause_name=CAUSE_NAMES.get(source, "临时事项"),
                    target=ds.product(pid)["product_name"], steps=[s["text"] for s in steps],
                    step_owners=[s["by"] for s in steps], params_text=[], risks=[],
                    track=dict(metric=metric, metric_name=METRIC_NAMES[metric], days=int(track_days or DEFAULT_TRACK_DAYS)))
    plan["name"] = (title or plan.get("name") or "").strip()[:60]
    plan["source"] = source
    return core_actions.annotate(plan)


_CHATTER = re.compile(r"^(好的|好|没问题|可以|收到|明白)[，,。!！]|这就为你|为你建|帮你建")


def enrich_context(name: str, pid: str, context: dict | None, card_id: str | None = None) -> dict:
    """协同消息的背景由平台补齐：诊断结论与证据、当前预警，保证同事看得懂发生了什么。
    运营或 AI 写的背景（备注 / 对话里的说明）保留在前；寒暄类的句子丢弃。"""
    from core import sop
    from .agent import diagnose
    ctx = dict(context or {})
    summary = (ctx.get("summary") or "").strip()
    if _CHATTER.search(summary) or len(summary) < 6:
        summary = ""
    ds = data.ds_of(name)
    card = data.card_for(name, pid, today_only=True)
    cached = state.cache_get(diagnose.cache_key(name, pid))
    res = cached["result"] if cached else sop.run(ds, pid, card=card, tiers=data.tiers(name))[1]
    evidence = [e for e in ctx.get("evidence") or [] if e]
    if not evidence:
        for rc in res.get("root_causes") or []:
            evidence += [e.get("text") if isinstance(e, dict) else str(e) for e in rc.get("evidence") or []]
    if card:
        sev = {"red": "红", "yellow": "黄", "blue": "蓝"}.get(card["severity"], "")
        evidence.insert(0, f"{card['first_date'][5:]} 起{sev}色预警：{'、'.join(card['rule_names'])}")
    seen, ev = set(), []
    for e in evidence:
        if e and e not in seen:
            seen.add(e)
            ev.append(e)
    diag = (res.get("summary") or "").strip()
    ctx["summary"] = summary + ("" if not diag or diag in summary else ("\n" if summary else "") + diag)
    ctx["evidence"] = ev[:4]
    return ctx


def preview(name: str, pid: str, plan: dict, context: dict | None, due: str, card_id: str | None = None) -> list[dict]:
    return collab.draft_handoffs(name, pid, plan, enrich_context(name, pid, context, card_id), due)


def create(name: str, pid: str, *, title: str, steps: list, due_date: str | None = None, track_metric: str | None = None,
           track_days: int | None = None, note: str | None = None, source: str = "manual", context: dict | None = None,
           plan: dict | None = None, card_id: str | None = None, base_url: str | None = None) -> int:
    ds = data.ds_of(name)
    if pid not in ds.product_ids():
        raise ValueError("未知商品")
    if not (title or (plan or {}).get("name") or "").strip():
        raise ValueError("请填写待办名称")
    if not _clean_steps(steps) and not (plan or {}).get("steps"):
        raise ValueError("至少填写一个步骤")
    p = build_plan(name, pid, title, steps, track_metric, track_days, source, base=plan)
    due = due_date or default_due(name, int(p.get("due_days") or DEFAULT_DUE_DAYS))
    ctx = dict(context or {})
    if not ctx.get("summary") and note:
        ctx["summary"] = note
    ctx = enrich_context(name, pid, ctx, card_id)
    aid = state.add_action(ds=name, card_id=card_id, product_id=pid, product_name=ds.product(pid)["product_name"],
                           action_id=p.get("action_id"), name=p["name"], cause=p.get("cause"), cause_name=p.get("cause_name"),
                           target=p.get("target"), plan_json=json.dumps(p, ensure_ascii=False), exec_type=p.get("exec_type"),
                           owner_role=p.get("owner_role"), status="doing", track_metric=p["track"].get("metric"),
                           track_days=p["track"]["days"], variant=(p.get("params") or {}).get("variant"),
                           adopted_date=today(name), context_json=json.dumps(ctx, ensure_ascii=False),
                           due_date=due, note=(note or "").strip() or None, source=source)
    collab.create_for_action(name, aid, pid, p, ctx, due, base_url)
    collab.add_log(aid, {"diagnosis": "采纳 AI 诊断方案", "chat": "从 AI 对话创建", "manual": "手动新建", "report": "从报告创建"}.get(source, "创建待办"))
    if card_id:
        st = (state.card_states(name).get(card_id) or {}).get("status")
        if st not in ("done", "ignored"):
            state.set_card(name, card_id, "processing")
    return aid


# ---------------------------------------------------------------------------
# 阶段推进
# ---------------------------------------------------------------------------
def refresh(name: str, aid: int):
    """执行中：我的步骤全勾完 + 同事步骤全部完成 → 跟踪中（执行日期 = 今天）。"""
    a = state.get_action(aid)
    if not a or a["status"] != "doing":
        return
    if collab.progress(a, state.list_handoffs(action_row=aid))["complete"]:
        state.update_action(aid, status="tracking", exec_date=today(name))
        collab.add_log(aid, f"所有步骤完成，开始跟踪效果（{a.get('track_days') or DEFAULT_TRACK_DAYS} 天）", by="系统")


def _effect(name: str, a: dict) -> dict:
    return effect(data.ds_of(name), a["product_id"], a.get("track_metric") or "gmv", a.get("exec_date"),
                  n=int(a.get("track_days") or DEFAULT_TRACK_DAYS), variant=a.get("variant"))


def advance(name: str, a: dict) -> dict:
    """跟踪中：跟踪天数的数据到齐 → 待复盘。"""
    if a["status"] == "tracking" and a.get("exec_date") and _effect(name, a).get("status") == "已完成":
        state.update_action(a["id"], status="review")
        collab.add_log(a["id"], "跟踪期满，等待复盘", by="系统")
        a = state.get_action(a["id"])
    return a


def end_tracking(name: str, aid: int):
    a = state.get_action(aid)
    if not a or a["status"] != "tracking":
        raise ValueError("只有跟踪中的待办可以提前结束")
    state.update_action(aid, status="review", early_end=1)
    collab.add_log(aid, "提前结束跟踪")


def _fmt(metric: str, v):
    if v is None:
        return "—"
    return f"{v * 100:.2f}%" if metric == "cvr" else (f"{v:.2f}" if metric in ("aov", "rating") else f"{v:,.1f}")


def suggestion(name: str, a: dict) -> dict:
    """复盘建议：前后对比 + 干扰事件 → 有效 / 无效 / 无法判断，附一句总结草稿。"""
    ds = data.ds_of(name)
    n = int(a.get("track_days") or DEFAULT_TRACK_DAYS)
    metric = a.get("track_metric") or "gmv"
    e = _effect(name, a)
    mname = e.get("metric_name") or METRIC_NAMES.get(metric, metric)
    events = []
    if a.get("exec_date"):
        end = (pd.Timestamp(a["exec_date"]) + pd.Timedelta(days=n)).strftime("%Y-%m-%d")
        ev = tools.get_events(ds, a["product_id"], a["exec_date"], end)["events"]
        events = [dict(date=x["date"], description=x["description"], kind=INTERFERENCE[x["type"]])
                  for x in ev if x["type"] in INTERFERENCE]
    th = float(ds.profile.get("review_threshold", 0.03))
    chg = e.get("change_pct")
    if e.get("status") != "已完成":
        outcome, why = "unknown", f"跟踪不满 {n} 天，数据不足以判断"
    elif events:
        outcome, why = "unknown", "跟踪期内有" + "、".join(f"{x['date'][5:]} {x['kind']}" for x in events) + "，结果可能受干扰"
    elif chg is not None and chg >= th:
        outcome, why = "effective", f"{mname}提升 {chg:.1%}，达到 {th:.0%} 的有效标准"
    else:
        outcome, why = "ineffective", f"{mname}变化 {chg:+.1%}，未达到 {th:.0%} 的有效标准" if chg is not None else "没有可比数据"
    summary = (f"{mname} {_fmt(metric, e.get('before'))} → {_fmt(metric, e.get('after'))}"
               + (f"（{chg:+.1%}）" if chg is not None and e.get("status") == "已完成" else "")) if e.get("before") is not None else ""
    return dict(outcome=outcome, outcome_name=OUTCOMES[outcome], reason=why, summary=summary, metric=metric,
                metric_name=mname, before=e.get("before"), after=e.get("after"), change_pct=chg, days=n,
                days_observed=e.get("days_observed", n if e.get("status") == "已完成" else 0), events=events,
                threshold=th)


def review(name: str, aid: int, outcome: str, note: str | None = None):
    a = state.get_action(aid)
    if not a or a["status"] != "review":
        raise ValueError("只有待复盘的待办可以复盘")
    if outcome not in OUTCOMES:
        raise ValueError("请选择复盘结论")
    state.update_action(aid, status="done", outcome=outcome, review_note=(note or "").strip() or None,
                        closed_date=today(name))
    collab.add_log(aid, f"复盘结论：{OUTCOMES[outcome]}" + (f" — {note.strip()}" if note and note.strip() else ""))


# ---------------------------------------------------------------------------
# 编辑、取消
# ---------------------------------------------------------------------------
def update(name: str, aid: int, *, title=None, due_date=None, note=None):
    a = state.get_action(aid)
    if not a:
        raise KeyError(aid)
    changes, logs, due_changed = {}, [], False
    if title is not None and title.strip() and title.strip() != a["name"]:
        changes["name"] = title.strip()[:60]
        plan = _loads(a.get("plan_json"), {})
        plan["name"] = changes["name"]
        changes["plan_json"] = json.dumps(plan, ensure_ascii=False)
        logs.append(f"名称改为「{changes['name']}」")
    if due_date and due_date != a.get("due_date"):
        if a["status"] != "doing":
            raise ValueError("只有执行中的待办可以改截止日期")
        changes["due_date"] = due_date
        logs.append(f"截止日期改为 {due_date}")
        due_changed = True
    if note is not None and note.strip() != (a.get("note") or ""):
        changes["note"] = note.strip() or None
        logs.append("更新备注")
    if changes:
        state.update_action(aid, **changes)
        for t in logs:
            collab.add_log(aid, t)
    if due_changed:
        collab.set_due(aid, due_date)
        _bg(lambda: _notify().changed(aid, "due", due_date))
    elif "name" in changes:
        _bg(lambda: _notify().changed(aid, "rename", ""))
    return state.get_action(aid)


def cancel(name: str, aid: int, reason: str | None = None):
    a = state.get_action(aid)
    if not a or a["status"] != "doing":
        raise ValueError("只有执行中的待办可以取消")
    state.update_action(aid, status="cancelled", cancel_reason=(reason or "").strip() or None, closed_date=today(name))
    collab.cancel_all(aid, reason)
    collab.add_log(aid, "取消待办" + (f"：{reason}" if reason else ""))
    _bg(lambda: _notify().changed(aid, "cancel", reason or ""))


def _notify():
    from . import notify
    return notify


def _bg(fn):
    threading.Thread(target=fn, daemon=True).start()


# ---------------------------------------------------------------------------
# 驳回的 AI 方案（不生成待办，只留记录）
# ---------------------------------------------------------------------------
def reject_plan(name: str, pid: str, plan: dict, reason: str, card_id: str | None = None) -> int:
    if not reason:
        raise ValueError("请选择驳回原因")
    return state.add_rejection(ds=name, product_id=pid, card_id=card_id, action_id=plan.get("action_id"),
                               cause=plan.get("cause"), name=plan.get("name"), reason=reason)


# ---------------------------------------------------------------------------
# 输出给前端
# ---------------------------------------------------------------------------
def decorate(name: str, a: dict) -> dict:
    a = advance(name, a)
    hs = state.list_handoffs(action_row=a["id"])
    out = dict(a)
    out["plan"] = _loads(out.pop("plan_json", None), {})
    out["context"] = _loads(out.pop("context_json", None), {})
    out["log"] = _loads(out.pop("log_json", None), [])
    out["step_done"] = _loads(out.get("step_done"), [])
    out["source"] = out.get("source") or "diagnosis"
    out["source_name"] = SOURCE_NAMES.get(out["source"], out["source"])
    out["stage_name"] = STAGE_NAMES.get(out["status"], out["status"])
    out["outcome_name"] = OUTCOMES.get(out.get("outcome") or "")
    out["handoffs"] = [collab.decorate_handoff(h) for h in hs]
    out["progress"] = collab.progress(a, hs)
    out["track_days"] = int(out.get("track_days") or DEFAULT_TRACK_DAYS)
    out["overdue"] = bool(out["status"] == "doing" and out.get("due_date") and out["due_date"] < today(name))
    if out.get("exec_date"):
        out["effect"] = _effect(name, a)
    if out["status"] == "review":
        out["suggestion"] = suggestion(name, a)
    return out


# ---------------------------------------------------------------------------
# 对话生成待办草稿
# ---------------------------------------------------------------------------
TODO_RE = re.compile(r"<todo>\s*(\{.*?\})\s*</todo>", re.S)

SUGGEST_RULE = """
## 待办卡片
以下两种情况，必须在回答最后另起一行附上一张待办卡片（整段放在 <todo> 标签内）：
1. 运营明确要求建待办（如「帮我建个待办」「记一下」「安排一下」）；
2. 你的回答建议运营去做具体的事情。
只是解释原因、回答数据问题时，不要附卡片。卡片格式严格如下：
<todo>{"name": "待办名称，15 字以内", "steps": [{"text": "具体步骤", "by": "我|供应链|投放运营"}], "track_metric": "gmv|cvr|uv|aov|units|rating", "track_days": 7, "due_days": 3, "note": "一句话背景，引用对话里核对过的数字"}</todo>
- 步骤 1–4 步。负责人：补货、批次质量问题归「供应链」；推广投放归「投放运营」；其余（含调价）归「我」。
- 分给同事的步骤会原样发给对方，要写成直接对对方说的话：做什么、针对哪个商品 / 规格 / 批次、要反馈什么结果。
  好的写法：「排查 9 月 12 日前后入库批次的结块、发酸问题，反馈问题批次号、涉及库存量和处理办法」；
  不要写「转交供应链」「联系供应链」这类话，也不要用「我」「你」以外的代称。
- note 用一两句话说清发生了什么、关键数字是多少（只用对话里平台核对过的数字），同事会先看到这段背景。
- 跟踪指标选和刚才讨论的问题最相关的指标；运营没说截止就用 3 天。
- 卡片内容不要在正文里重复；卡片之外的正文不要写「好的，这就为你建待办」这类话。"""


def split_suggestion(text: str, name: str) -> tuple[str, dict | None]:
    """把回答中的 <todo> 卡片取出来，返回（正文，草稿）。"""
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

    def _int(v, d, lo, hi):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return d
    days = _int(raw.get("due_days", DEFAULT_DUE_DAYS), DEFAULT_DUE_DAYS, 0, 30)
    metric = raw.get("track_metric") if raw.get("track_metric") in TRACK_METRICS else "gmv"
    return dict(name=title[:60], steps=steps, track_metric=metric,
                track_days=_int(raw.get("track_days", DEFAULT_TRACK_DAYS), DEFAULT_TRACK_DAYS, 3, 30),
                due_date=default_due(name, days), note=str(raw.get("note") or "").strip()[:300],
                due_is_default="due_days" not in raw)


DRAFT_SYSTEM = """你负责把一段商品运营对话整理成一条待办。只输出一个 JSON 对象，不要其他文字：
{"name": "待办名称，15 字以内", "steps": [{"text": "具体步骤", "by": "我|供应链|投放运营"}], "track_metric": "gmv|cvr|uv|aov|units|rating", "track_days": 7, "due_days": 3, "note": "一句话说明为什么要做（引用对话中的数字）"}
- 步骤 1–4 步，来自对话内容，不要编造对话里没有的数字。
- 负责人：补货、批次质量问题归「供应链」；推广投放归「投放运营」；其余（含调价）归「我」。
- 分给同事的步骤会原样发给对方，写成直接对对方说的话：做什么、针对哪个商品 / 规格 / 批次、要反馈什么结果；不要写「转交供应链」这类话。"""


def _heuristic(reply: str, question: str, name: str) -> dict:
    lines = [re.sub(r"^\s*(?:[-*•·]|\d+[.、)])\s*", "", x).strip() for x in (reply or "").splitlines()]
    lines = [re.sub(r"[*#`]", "", x) for x in lines if x]
    bullets = [x for x in lines if 6 <= len(x) <= 120 and not x.endswith(("：", ":"))][:4] or [(reply or question)[:120]]
    title = re.sub(r"[？?。！!]$", "", (question or bullets[0]).strip())[:20] or "对话待办"
    return dict(name=title, steps=[dict(text=b, by="我") for b in bullets], track_metric="gmv",
                track_days=DEFAULT_TRACK_DAYS, due_date=default_due(name), note="", due_is_default=True)


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
