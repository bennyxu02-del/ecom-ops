"""周报生成：大模型流式写作，失败时用规则模板。数字校验同诊断。"""
from __future__ import annotations

import json
import time

from core import sop, weekly

from .. import data, llm_client, state, todos
from . import diagnose, prompts, verify


def _actions_for_pack(name):
    out = []
    for a in state.list_actions(name):
        d = todos.decorate(name, a)          # 顺带推进到期的跟踪
        out.append(dict(card_id=d["card_id"], product_id=d["product_id"], product_name=d["product_name"], name=d["name"],
                        cause_name=d.get("cause_name"), stage=d["status"], status=d["stage_name"], outcome_name=d.get("outcome_name"),
                        review_note=d.get("review_note"), closed_date=d.get("closed_date"), exec_date=d.get("exec_date"),
                        due_date=d.get("due_date"), track_metric=d.get("track_metric"), track_days=d.get("track_days"),
                        variant=d.get("variant")))
    return out


def build_pack(name):
    ds = data.ds_of(name)
    cards = data.cards(name)
    diags = {}
    for c in cards:
        cached = state.cache_get(diagnose.cache_key(name, c["product_id"]))
        if cached:
            diags[c["product_id"]] = cached["result"]
        elif c["is_today"] or c["auto_status"] == "recovered":
            _, res = sop.run(ds, c["product_id"], card=c, tiers=data.tiers(name))
            diags[c["product_id"]] = res
    return weekly.weekly_pack(ds, cards, _actions_for_pack(name), diags)


def run(name: str):
    ds = data.ds_of(name)
    pack = build_pack(name)
    title = f"{pack['category']}重点商品经营周报（{pack['period']}）"
    mode = llm_client.mode()
    yield dict(type="meta", mode=mode, period=pack["period"])
    text, source = "", "llm"
    if mode in ("live", "mock"):
        _, stream = diagnose.backend(name)
        try:
            if mode == "mock":
                body = weekly.render_rules(pack).replace("> 本报告数字均来自平台计算。\n", "")
                for i in range(0, len(body), 40):
                    time.sleep(0.01)
                    text += body[i:i + 40]
                    yield dict(type="delta", text=body[i:i + 40])
            else:
                msgs = [{"role": "system", "content": prompts.weekly_system()},
                        {"role": "user", "content": prompts.weekly_user(pack)}]
                for d in stream(msgs):
                    text += d
                    yield dict(type="delta", text=d)
        except llm_client.LLMError as e:
            yield dict(type="meta", fallback_reason=str(e))
            text = ""
    if not text:
        cached = state.cache_get(f"weekly__{name}__{ds.as_of:%Y%m%d}")
        if cached and mode != "live":
            text, source = cached["content"], cached.get("source", "cache")
        else:
            text, source = weekly.render_rules(pack), "rules"
        for i in range(0, len(text), 60):
            time.sleep(0.01)
            yield dict(type="delta", text=text[i:i + 60])
    allowed = set()
    verify.collect(pack, allowed)
    bad = sorted(set(verify.check_text(text, allowed)))
    rid = state.add_report(ds=name, type="weekly", title=title, period=pack["period"], content=text, status="draft",
                           source=source)
    if source == "llm":
        state.cache_put(f"weekly__{name}__{ds.as_of:%Y%m%d}", dict(content=text, source="llm"))
    yield dict(type="result", id=rid, source=source, unmatched_numbers=bad, pack=pack)
