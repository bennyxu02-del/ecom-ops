"""按场景生成报告：平台算数据包和必备图 → AI 按分析剧本写（可调用图表工具补充最多 3 张图）→ 核对数字 → 存为草稿。

没有配置大模型或模型出错时，用规则版报告（同样的章节和数字）。
"""
from __future__ import annotations

import json
import time

from core import charts as C
from core import config
from core.reports import SCENES
from core.reports import campaign as R_campaign
from core.reports import product as R_product
from core.reports import weekly as R_weekly
from core.reports.common import default, finalize, for_llm, insert_after_chapter_chart

from .. import data, llm_client, state, todos
from . import diagnose, verify

BUILDERS = {"weekly": R_weekly, "campaign": R_campaign, "product": R_product}
PLAYBOOKS = {"weekly": "playbooks/weekly_review.md", "campaign": "playbooks/campaign_review.md",
             "product": "playbooks/product_diagnosis.md"}
MAX_ROUNDS = 6


# ---------------------------------------------------------------------------
# 场景与参数
# ---------------------------------------------------------------------------
def target_key(name, month):
    return f"gmv_target__{name}__{month}"


def get_target(name, month) -> float | None:
    v = state.get_setting(target_key(name, month))
    return float(v) if v not in (None, "", 0) else None


def scenes(name) -> dict:
    ds = data.ds_of(name)
    cards = {c["product_id"]: c for c in data.cards(name) if c["is_today"]}
    prods = []
    for pid in ds.product_ids():
        c = cards.get(pid)
        prods.append(dict(id=pid, name=ds.product(pid)["product_name"], severity=(c or {}).get("severity"),
                          severity_name=(c or {}).get("severity_name"), rules=(c or {}).get("rule_names"),
                          expected=bool((c or {}).get("expected"))))
    order = {"red": 0, "yellow": 1, "blue": 2, None: 3}
    prods.sort(key=lambda x: (order.get(x["severity"], 3), x["id"]))
    month = ds.as_of.strftime("%Y-%m")
    return dict(
        as_of=ds.as_of.strftime("%Y-%m-%d"),
        weekly=dict(name=SCENES["weekly"], weeks=R_weekly.week_ends(ds), month=month, target=get_target(name, month)),
        campaign=dict(name=SCENES["campaign"], campaigns=R_campaign.list_campaigns(ds)),
        product=dict(name=SCENES["product"], products=prods),
    )


def _actions_for_pack(name):
    out = []
    for a in state.list_actions(name):
        d = todos.decorate(name, a)
        out.append(dict(id=d["id"], card_id=d["card_id"], product_id=d["product_id"], product_name=d["product_name"],
                        name=d["name"], cause=d.get("cause"), cause_name=d.get("cause_name"), stage=d["status"],
                        status=d["stage_name"], outcome_name=d.get("outcome_name"), review_note=d.get("review_note"),
                        closed_date=d.get("closed_date"), exec_date=d.get("exec_date"), due_date=d.get("due_date"),
                        track_metric=d.get("track_metric"), track_days=d.get("track_days"), variant=d.get("variant"),
                        effect=d.get("effect")))
    return out


def build_pack(name, scene, params):
    ds = data.ds_of(name)
    rp = ds.profile.get("report", {})
    book = C.ChartBook(rp.get("extra_chart_limit", 3))
    params = dict(params or {})
    if scene == "weekly":
        cards = data.cards(name)
        diags = {}
        for c in cards:
            cached = state.cache_get(diagnose.cache_key(name, c["product_id"]))
            if cached:
                diags[c["product_id"]] = cached["result"]
        week_end = params.get("week_end") or ds.as_of.strftime("%Y-%m-%d")
        target = params.get("target")
        if target is None:
            target = get_target(name, week_end[:7])
        return R_weekly.build(ds, cards, _actions_for_pack(name), diags, week_end=week_end, target=target, book=book)
    if scene == "campaign":
        cid = params.get("campaign_id")
        if not cid:
            camps = R_campaign.list_campaigns(ds)
            if not camps:
                raise ValueError("数据里没有可复盘的活动")
            cid = camps[0]["id"]
        return R_campaign.build(ds, cid, book=book)
    if scene == "product":
        pid = params.get("product_id")
        if pid not in ds.product_ids():
            raise ValueError(f"未知商品 {pid}")
        card = data.card_for(name, pid, today_only=True)
        hist = []
        for a in _actions_for_pack(name):
            if a["stage"] == "done" and a.get("cause"):
                e = a.get("effect") or {}
                hist.append(dict(name=a["name"], product_name=a["product_name"], cause=a["cause"], outcome_name=a.get("outcome_name"),
                                 review_note=a.get("review_note"), metric_name=e.get("metric_name"), change_pct=e.get("change_pct")))
        return R_product.build(ds, pid, card=card, history=hist, book=book)
    raise ValueError(f"未知场景 {scene}")


# ---------------------------------------------------------------------------
# 写作
# ---------------------------------------------------------------------------
def system_prompt(scene) -> str:
    return ("你是一名资深电商运营分析师，按下面的分析剧本和写作规则撰写报告。\n\n"
            + config.read_text("playbooks/writing_rules.md") + "\n\n---\n\n" + config.read_text(PLAYBOOKS[scene]))


def user_prompt(pack, book) -> str:
    return ("报告数据包如下（章节顺序、数据、判断标签和必备图都已算好）。请按剧本写出完整报告：\n"
            + json.dumps(for_llm(pack, book), ensure_ascii=False, default=default))


def _mock_extras(scene, pack) -> list[tuple[dict, str]]:
    """模拟模型（开发用）：模拟 AI 调用图表工具补充一张图。"""
    if scene != "weekly":
        return []
    ch = next(c for c in pack["chapters"] if c["key"] == "sources")
    needs = [i for i in ch["facts"]["items"] if i["label_key"] == "needs"]
    if not needs:
        return []
    i = needs[0]
    reason = i["reason"]
    if "口碑" in reason:
        a = dict(type="dual_line", products=[i["product_id"]], metrics=["rating", "refund_rate"], title=f"{i['product_name']}：差评出现后评分下滑、退款率上升")
    elif "价格" in reason:
        a = dict(type="dual_line", products=[i["product_id"]], metrics=["price", "comp_price", "cvr"], title=f"{i['product_name']}：竞品降价后转化率下滑")
    elif "断货" in reason:
        a = dict(type="trend", products=[i["product_id"]], metrics=["stock"], title=f"{i['product_name']}：断货规格的库存")
    else:
        a = dict(type="trend", products=[i["product_id"]], metrics=["cvr"], title=f"{i['product_name']}转化率走势")
    return [(a, "sources")]


def _llm_write(name, scene, pack, book, events):
    chat, _ = diagnose.backend(name)
    msgs = [{"role": "system", "content": system_prompt(scene)}, {"role": "user", "content": user_prompt(pack, book)}]
    ds = data.ds_of(name)
    for _ in range(MAX_ROUNDS):
        msg = chat(msgs, tools=[C.TOOL_SPEC], max_tokens=6000, timeout_s=240)
        tcs = msg.get("tool_calls") or []
        if not tcs:
            return msg.get("content") or ""
        msgs.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tcs})
        for tc in tcs:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if tc["function"]["name"] != "draw_chart":
                result = {"error": "报告里只能调用 draw_chart"}
            else:
                result = book.draw_extra(ds, args)
            events.append(dict(type="step", tool="draw_chart",
                               summary=(f"AI 补充图：{result['title']}" if "chart_id" in result else f"图表参数有误，已退回：{result.get('error', '')[:60]}")))
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", "draw_chart"),
                         "content": json.dumps(result, ensure_ascii=False, default=default)[:4000]})
    raise llm_client.LLMError("调用图表工具次数过多")


def run(name: str, scene: str = "weekly", params: dict | None = None):
    if scene not in SCENES:
        raise ValueError(f"未知场景 {scene}")
    mode = llm_client.mode()
    yield dict(type="meta", mode=mode, scene=scene)
    yield dict(type="step", summary="正在按分析剧本计算数据包")
    pack, book = build_pack(name, scene, params)
    n_req = len(book.charts)
    yield dict(type="step", summary=f"已算好 {sum(c['show'] for c in pack['chapters'])} 个章节、{n_req} 张必备图")
    ds = data.ds_of(name)
    builder = BUILDERS[scene]
    text, source = "", "llm"
    if mode == "live":
        yield dict(type="step", summary="AI 正在按剧本撰写")
        events: list = []
        try:
            text = _llm_write(name, scene, pack, book, events)
        except llm_client.LLMError as e:
            yield dict(type="meta", fallback_reason=str(e))
            text = ""
        for ev in events:
            yield ev
    elif mode == "mock":
        text = builder.render(pack, book)
        for a, key in _mock_extras(scene, pack):
            r = book.draw_extra(ds, a, key)
            if "chart_id" in r:
                yield dict(type="step", tool="draw_chart", summary=f"AI 补充图：{r['title']}")
                text = insert_after_chapter_chart(text, pack, key, r["chart_id"])
    if not text.strip():
        text, source = builder.render(pack, book), "rules"
    text, charts = finalize(text, pack, book)
    for i in range(0, len(text), 60):
        time.sleep(0.004)
        yield dict(type="delta", text=text[i:i + 60])
    yield dict(type="step", summary="正在核对报告里的数字")
    allowed = set()
    verify.collect(json.loads(json.dumps(pack, ensure_ascii=False, default=default)), allowed)
    for v in book.values():
        verify.collect(v, allowed)
    bad = sorted(set(verify.check_text(text, allowed)))
    total = len(verify.NUM_RE.findall(text))
    extra = dict(scene_name=SCENES[scene], unmatched=bad, numbers=total, actions=_actions_public(pack),
                 chapters=[dict(key=c["key"], heading=c["heading"], question=c["question"], chart=c["chart"]) for c in pack["chapters"] if c["show"]],
                 subject=pack.get("subject"), product_id=pack.get("product_id"), card_id=pack.get("card_id"),
                 extra_charts=sum(1 for c in charts.values() if c["origin"] == "extra"))
    rid = state.add_report(ds=name, type=scene, title=pack["title"], period=pack.get("period"), content=text, status="draft",
                           source=source, params_json=json.dumps(pack.get("params") or {}, ensure_ascii=False, default=default),
                           charts_json=json.dumps(charts, ensure_ascii=False, default=default),
                           extra_json=json.dumps(extra, ensure_ascii=False, default=default))
    yield dict(type="result", id=rid, source=source, unmatched_numbers=bad)


def _actions_public(pack) -> list[dict]:
    out = []
    for a in pack.get("actions") or []:
        x = {k: v for k, v in a.items() if k in ("product_id", "product_name", "kind", "title", "why", "first_step", "owners",
                                                   "existing_todo", "can_todo", "draft", "card_id")}
        if a.get("plan"):
            p = a["plan"]
            x["plan"] = {k: p.get(k) for k in ("action_id", "name", "cause", "cause_name", "target", "steps", "step_owners", "due_days",
                                                "track", "risk_notes", "params", "params_text", "estimate", "checks", "exec_type",
                                                "owner_role", "risks", "materials", "rationale", "variant")}
            x["context"] = dict(summary=a.get("why") or "")
        out.append(x)
    return out


def generate(name, scene, params) -> dict:
    """同步生成（旧接口与测试用）。"""
    res = None
    for ev in run(name, scene, params):
        if ev["type"] == "result":
            res = ev
    return res

