"""单品追问与执行物料：复用诊断 Agent 的工具，锁定当前商品。"""
from __future__ import annotations

import json

from core import sop, tools

from .. import data, llm_client, state
from . import diagnose, prompts, verify

MAX_CALLS = 5


def run(name: str, pid: str, messages: list[dict], preset: str | None = None, plan: dict | None = None):
    ds = data.ds_of(name)
    mode = llm_client.mode()
    diag = state.cache_get(diagnose.cache_key(name, pid))
    diag_res = diag["result"] if diag else None
    user_text = prompts.MATERIAL_PRESETS.get(preset) if preset else None
    if user_text and plan:
        user_text += "\n当前方案：" + json.dumps({k: plan.get(k) for k in ("name", "target", "params_text", "steps")}, ensure_ascii=False)
    history = [m for m in messages if m.get("role") in ("user", "assistant")][-10:]
    if user_text:
        history.append({"role": "user", "content": user_text})
    key = f"mat__{name}__{pid}__{preset}" if preset else None
    yield dict(type="meta", mode=mode)

    if mode not in ("live", "mock"):
        c = state.cache_get(key) if key else None
        text = c["content"] if c else ("当前没有连接大模型，追问与物料生成需要在线模型。"
                                        "可以在「模型状态」确认配置后重试。")
        yield dict(type="delta", text=text)
        yield dict(type="result", text=text, source="cache" if c else "none")
        return
    chat, _ = diagnose.backend(name)
    msgs = [{"role": "system", "content": prompts.chat_system(ds, pid, diag_res)}] + history
    steps, text = [], ""
    try:
        if mode == "mock":
            text = f"（模拟模型）已收到：{history[-1]['content'][:60]}"
        else:
            for _ in range(MAX_CALLS + 1):
                msg = chat(msgs, tools=tools.TOOL_SPECS)
                tcs = msg.get("tool_calls") or []
                if not tcs:
                    text = msg.get("content") or ""
                    break
                msgs.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tcs})
                for tc in tcs:
                    fn = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    args["product_id"] = pid
                    try:
                        result = tools.call(ds, fn, args, tiers=data.tiers(name))
                    except Exception as e:  # noqa: BLE001
                        result = {"error": str(e)}
                    steps.append(dict(tool=fn, result=result))
                    yield dict(type="step", tool=fn, summary=sop.summarize(fn, result))
                    msgs.append({"role": "tool", "tool_call_id": tc.get("id", fn),
                                 "content": json.dumps(result, ensure_ascii=False, default=str)[:6000]})
    except llm_client.LLMError as e:
        text = f"模型暂时不可用：{e}"
    for i in range(0, len(text), 30):
        yield dict(type="delta", text=text[i:i + 30])
    allowed = set()
    for s in steps:
        verify.collect(s["result"], allowed)
    if diag_res:
        verify.collect(diag_res, allowed)
    if plan:
        verify.collect(plan, allowed)
    bad = sorted(set(verify.check_text(text, allowed))) if not preset else []
    if key and text and mode == "live":
        state.cache_put(key, dict(content=text))
    yield dict(type="result", text=text, unmatched_numbers=bad)
