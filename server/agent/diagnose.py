"""商品诊断编排：工具调用 Agent / 证据包 / 规则降级，输出 SSE 事件。"""
from __future__ import annotations

import copy
import functools
import json
import re
import time

from core import actions, config, sop, tools
from core.actions import completeness

from .. import data, llm_client, state
from . import mock_llm, prompts, verify

MAX_TOOL_CALLS = 8


def backend(ds_name):
    m = llm_client.mode()
    if m == "mock":
        return functools.partial(mock_llm.chat, ds=ds_name), mock_llm.stream
    return llm_client.chat, llm_client.stream


def cache_key(name, pid):
    return f"diag__{name}__{pid}__{data.ds_of(name).as_of:%Y%m%d}"


def _parse_json(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("没有找到 JSON")
    return json.loads(t[i:j + 1])


def _compact(res: dict, limit=6000) -> str:
    s = json.dumps(res, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…（已截断）"


def finalize(name, pid, llm_out: dict, steps: list, source: str) -> dict:
    """把大模型输出与工具结果合并：数字、方案一律以工具结果为准，并做校验。"""
    ds = data.ds_of(name)
    dec = next((s["result"] for s in steps if s["tool"] == "decompose_gmv"), None) or tools.decompose_gmv(ds, pid)
    cands = {}
    for s in steps:
        if s["tool"] == "plan_actions":
            for p in s["result"]["candidates"]:
                cands.setdefault((p["action_id"], p["cause"]), p)
                cands.setdefault((p["action_id"], None), p)
    plans, dropped = [], []
    for lp in llm_out.get("plans", []) or []:
        c = cands.get((lp.get("action_id"), lp.get("cause"))) or cands.get((lp.get("action_id"), None))
        if not c:
            dropped.append(dict(action_id=lp.get("action_id"), reason="不在本次 plan_actions 的返回中"))
            continue
        c = copy.deepcopy(c)
        miss = completeness(c)
        if miss:
            dropped.append(dict(action_id=c["action_id"], reason="缺少要素：" + "、".join(miss)))
            continue
        c["rationale"] = lp.get("rationale") or ""
        if any(p["action_id"] == c["action_id"] for p in plans):
            continue
        plans.append(c)
    rcs = []
    for rc in llm_out.get("root_causes", []) or []:
        cause = rc.get("cause")
        rcs.append(dict(cause=cause, cause_name=config.cause_name(cause), confidence=rc.get("confidence", "中"),
                        evidence=[e if isinstance(e, dict) else {"text": str(e)} for e in rc.get("evidence", [])]))
    lim = llm_out.get("limitations") or []
    if not lim:
        lim = ["模型未声明数据局限，已由平台补充：刺激类方案不预测销量提升"]
    res = dict(
        summary=llm_out.get("summary", ""),
        severity=(data.card_for(name, pid, today_only=True) or {}).get("severity", "yellow"),
        contribution=[dict(factor=f["factor"], name=f["name"], change_pct=f["change_pct"], amount=f["amount"], share=f["share"])
                      for f in dec["factors"]],
        gmv=dict(prev=dec["gmv_prev"], cur=dec["gmv_cur"], change=dec["gmv_change"], change_pct=dec["gmv_change_pct"],
                 cur_window=dec["cur_window"], prev_window=dec["prev_window"]),
        root_causes=rcs, plans=plans, limitations=lim, notes=llm_out.get("notes") or [],
    )
    allowed = set()
    for s in steps:
        verify.collect(s["result"], allowed)
    bad = []
    for t in verify.texts_of_result(res):
        bad += verify.check_text(t, allowed)
    no_plan_note = None
    if not plans and any(s["tool"] == "plan_actions" and s["result"]["candidates"] for s in steps):
        no_plan_note = "模型没有选择任何方案"
    res["verify"] = dict(unmatched_numbers=sorted(set(bad)), dropped_plans=dropped, note=no_plan_note,
                         checked_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    res["source"] = source
    return res


def _rules(name, pid, card):
    ds = data.ds_of(name)
    steps, res = sop.run(ds, pid, card=card, tiers=data.tiers(name))
    res["source"] = "rules"
    res["verify"] = dict(unmatched_numbers=[], dropped_plans=[], note=None)
    return steps, res


def _step_event(s):
    return dict(type="step", tool=s["tool"], args=s["args"], summary=s["summary"])


def run(name: str, pid: str, refresh: bool = False):
    """生成器：逐个产出 SSE 事件 dict；结果中的方案统一补齐步骤负责人与协同事项。"""
    for ev in _run(name, pid, refresh):
        if ev.get("type") == "result":
            for p in ev["result"].get("plans") or []:
                actions.annotate(p)
        yield ev


def _run(name: str, pid: str, refresh: bool = False):
    ds = data.ds_of(name)
    card = data.card_for(name, pid, today_only=True)
    key = cache_key(name, pid)
    mode = llm_client.mode()
    cached = None if refresh else state.cache_get(key)

    if cached and (not refresh):
        yield dict(type="meta", mode=mode, source=cached["result"].get("source"), cached=True)
        for s in cached["steps"]:
            time.sleep(0.25)
            yield s
        yield dict(type="result", result=cached["result"])
        return

    def fallback(reason):
        c = state.cache_get(key) if refresh else None
        if c:
            yield dict(type="meta", mode=mode, source=c["result"].get("source"), cached=True, fallback_reason=reason)
            for s in c["steps"]:
                time.sleep(0.15)
                yield s
            yield dict(type="result", result=c["result"])
            return
        yield dict(type="meta", mode=mode, source="rules", cached=False, fallback_reason=reason)
        steps, res = _rules(name, pid, card)
        evs = []
        for s in steps:
            e = _step_event(s)
            evs.append(e)
            time.sleep(0.2)
            yield e
        state.cache_put(key + "__rules", dict(steps=evs, result=res))
        yield dict(type="result", result=res)

    if mode in ("demo", "unconfigured", "off"):
        reason = {"demo": "演示模式", "unconfigured": "未配置模型", "off": "模型已关闭"}[mode]
        yield from fallback(reason)
        return

    chat, _ = backend(name)
    sysmsg = prompts.diagnose_system(ds)
    events, steps = [], []
    try:
        if llm_client.agent_mode() == "evidence_pack":
            yield dict(type="meta", mode=mode, source="llm_evidence", cached=False)
            steps, _ = sop.run(ds, pid, card=card, tiers=data.tiers(name))
            for s in steps:
                e = _step_event(s)
                events.append(e)
                yield e
            msgs = [{"role": "system", "content": sysmsg},
                    {"role": "user", "content": prompts.evidence_user(ds, pid, card, steps)}]
            out = _llm_json(chat, msgs, tools_=None)
            res = finalize(name, pid, out, steps, "llm_evidence")
        else:
            yield dict(type="meta", mode=mode, source="llm_tools", cached=False)
            msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": prompts.diagnose_user(ds, pid, card)}]
            n_calls = 0
            while True:
                msg = chat(msgs, tools=tools.TOOL_SPECS)
                tcs = msg.get("tool_calls") or []
                if not tcs:
                    content = msg.get("content") or ""
                    try:
                        out = _parse_json(content)
                    except Exception:  # noqa: BLE001
                        msgs.append({"role": "assistant", "content": content})
                        msgs.append({"role": "user", "content": "请只输出符合格式要求的 JSON 对象。"})
                        out = _llm_json(chat, msgs, tools_=None)
                    break
                msgs.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tcs})
                for tc in tcs:
                    fn = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    args["product_id"] = pid      # 锁定当前商品
                    if n_calls >= MAX_TOOL_CALLS:
                        result = {"error": "已达到工具调用上限，请根据已有结果输出结论"}
                    else:
                        try:
                            result = tools.call(ds, fn, args, card=card, tiers=data.tiers(name))
                        except Exception as e:  # noqa: BLE001
                            result = {"error": f"工具执行失败：{e}"}
                    n_calls += 1
                    s = dict(tool=fn, args=args, result=result, summary=sop.summarize(fn, result))
                    steps.append(s)
                    e = _step_event(s)
                    events.append(e)
                    yield e
                    msgs.append({"role": "tool", "tool_call_id": tc.get("id", fn), "content": _compact(result)})
                if n_calls > MAX_TOOL_CALLS + 2:
                    msgs.append({"role": "user", "content": "请停止调用工具，直接按格式输出 JSON。"})
                    out = _llm_json(chat, msgs, tools_=None)
                    break
            res = finalize(name, pid, out, steps, "llm_tools")
    except llm_client.LLMError as e:
        yield from fallback(str(e))
        return
    except Exception as e:  # noqa: BLE001
        yield from fallback(f"诊断过程异常：{e}")
        return
    state.cache_put(key, dict(steps=events, result=res))
    yield dict(type="result", result=res)


def _llm_json(chat, msgs, tools_=None):
    msg = chat(msgs, tools=tools_) if tools_ else chat(msgs)
    try:
        return _parse_json(msg.get("content") or "")
    except Exception:  # noqa: BLE001
        msgs = msgs + [{"role": "assistant", "content": msg.get("content") or ""},
                       {"role": "user", "content": "输出格式不正确。请只输出一个符合要求的 JSON 对象。"}]
        msg = chat(msgs)
        return _parse_json(msg.get("content") or "")
