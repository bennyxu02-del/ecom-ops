"""开发用模拟模型（LLM_MODE=mock）：按 SOP 脚本返回工具调用与最终 JSON，用于在没有真实模型时验证整条链路。
它不是降级方案，线上不使用。"""
from __future__ import annotations

import json
import re
import time

from core import sop

from .. import data


def _pid(messages):
    for m in messages:
        if m["role"] == "user":
            x = re.search(r"商品编号 (\w+)", m["content"]) or re.search(r"编号 (\w+)", m["content"])
            if x:
                return x.group(1)
    return None


_scripts = {}


def chat(messages, tools=None, ds="3c", **kw):
    time.sleep(0.3)
    ds_name = ds
    pid = _pid(messages)
    if not tools and not pid:
        return {"role": "assistant", "content": "（模拟模型）这是基于证据包的回答。"}
    key = (ds_name, pid)
    if key not in _scripts:
        ds = data.ds_of(ds_name)
        card = data.card_for(ds_name, pid, today_only=True)
        _scripts[key] = sop.run(ds, pid, card=card, tiers=data.tiers(ds_name))
    steps, res = _scripts[key]
    n = sum(len(m.get("tool_calls") or []) for m in messages if m["role"] == "assistant")
    if tools and n < len(steps):
        s = steps[n]
        args = {k: v for k, v in s["args"].items()}
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"call_{n}", "type": "function", "function": {"name": s["tool"], "arguments": json.dumps(args, ensure_ascii=False)}}]}
    out = dict(summary=res["summary"],
               root_causes=[dict(cause=c["cause"], confidence=c["confidence"], evidence=c["evidence"]) for c in res["root_causes"]],
               plans=[dict(action_id=p["action_id"], cause=p["cause"], rationale=p["rationale"]) for p in res["plans"]],
               limitations=res["limitations"], notes=res.get("notes", []))
    return {"role": "assistant", "content": json.dumps(out, ensure_ascii=False)}


def stream(messages, **kw):
    text = "（模拟模型）" + (messages[-1]["content"][:40] if messages else "")
    for i in range(0, len(text), 8):
        time.sleep(0.02)
        yield text[i:i + 8]
