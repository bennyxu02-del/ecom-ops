"""单品诊断：一个商品出了什么问题、根因是什么、怎么办、谁来办。分析剧本见 methods/playbooks/product_diagnosis.md。"""
from __future__ import annotations

import pandas as pd

from .. import charts as C
from .. import sop, tiering
from ..metrics import DAY
from .common import chapter, events_between, md, money, money_signed, num, pct, price, r, rate, ymd

# 根因 → 第四章用哪张图（通过图表工具画，参数由这里决定）
CAUSE_CHART = {
    "price_disadvantage": lambda pid, ev: dict(type="dual_line", products=[pid], metrics=["price", "comp_price", "cvr"],
                                               title="我方到手价、竞品到手价与转化率", **ev),
    "reputation_drop": lambda pid, ev: dict(type="dual_line", products=[pid], metrics=["rating", "refund_rate"],
                                            title="评分与退款率", **ev),
    "stockout": lambda pid, ev: dict(type="trend", products=[pid], metrics=["stock"], title="各规格库存", **ev),
    "stockout_risk": lambda pid, ev: dict(type="trend", products=[pid], metrics=["stock"], title="各规格库存", **ev),
    "paid_traffic_drop": lambda pid, ev: dict(type="grouped_bar", products=[pid], by="channel", title="各渠道日均访客：前 7 天 vs 近 7 天"),
    "search_traffic_drop": lambda pid, ev: dict(type="grouped_bar", products=[pid], by="channel", title="各渠道日均访客：前 7 天 vs 近 7 天"),
    "growth_opportunity": lambda pid, ev: dict(type="grouped_bar", products=[pid], by="channel", title="各渠道日均访客：前 7 天 vs 近 7 天"),
    "campaign_end": lambda pid, ev: dict(type="grouped_bar", products=[pid], by="channel", title="各渠道日均访客：前 7 天 vs 近 7 天"),
    "aov_drop": lambda pid, ev: dict(type="trend", products=[pid], metrics=["aov"], title="客单价走势", **ev),
}

MARK_TYPES = {"price_disadvantage": ("competitor_price_change", "price_change"), "reputation_drop": ("review_issue",),
              "stockout": ("restock",), "stockout_risk": ("restock",), "aov_drop": ("price_change",)}


def build(ds, pid: str, card: dict | None = None, history: list[dict] | None = None, book: C.ChartBook | None = None,
          steps_res=None):
    rp = ds.profile.get("report", {})
    book = book or C.ChartBook(rp.get("extra_chart_limit", 3))
    prod = ds.product(pid)
    pname = prod["product_name"]
    steps, res = steps_res or sop.run(ds, pid, card=card, tiers=tiering.compute(ds))
    tool = {s["tool"]: s["result"] for s in steps}
    end = ds.as_of
    cur = (end - 6 * DAY, end)
    prev = (cur[0] - 7 * DAY, cur[0] - DAY)
    rng = dict(start=ymd(cur[0]), end=ymd(cur[1]), compare_start=ymd(prev[0]), compare_end=ymd(prev[1]))
    g = res["gmv"]
    causes = res["root_causes"]
    main = causes[0] if causes else None
    notes = list(res.get("limitations") or [])

    # ---- 一、结论
    items = [C.kpi_item("近 7 日 GMV", g["cur"], g["prev"], "money", note=f"{g['cur_window']} vs {g['prev_window']}")]
    f7 = C.frame(ds, [pid])
    for m, lab in (("uv", "访客数"), ("cvr", "支付转化率"), ("aov", "客单价")):
        v1, v0 = C.window_value(ds, [pid], m, cur), C.window_value(ds, [pid], m, prev)
        if m == "uv":
            v1, v0 = v1 * 7, v0 * 7
        items.append(C.kpi_item(lab, v1, v0, C.METRICS[m][1]))
    kpi = dict(type="kpi", title=f"{pname}近 7 日核心指标", items=items)
    kpi["summary"] = "；".join(f"{i['label']} {C.fmt(i['prev'], i['unit'])} → {C.fmt(i['value'], i['unit'])}（{pct(i['change'])}）" for i in items)
    kpi["values"] = [x for i in items for x in (i["value"], i["prev"], i["change"]) if x is not None]
    k1 = book.add(kpi, chapter="summary")

    # ---- 二、问题有多大
    s28 = f7["gmv"][f7.index > end - 28 * DAY]
    evs = events_between(ds, pid, end - 27 * DAY, end)
    marks = [dict(x=C._mm(e["date"]), text=e["description"][:12]) for e in evs if e["type"] != "promo_day"][:3]
    tr = dict(type="trend", unit="money", title="近 28 天每日 GMV（竖线为事件）", x=[C._mm(d) for d in s28.index],
              series=[dict(name="每日 GMV", data=[r(v, 0) for v in s28])], marks=marks)
    last3 = float(f7["gmv"].tail(3).mean())
    first4 = float(f7["gmv"][(f7.index >= cur[0]) & (f7.index <= cur[0] + 3 * DAY)].mean())
    prev_avg = float(g["prev"]) / 7
    gap = prev_avg - last3
    loss_7d = max(-(g["change"] or 0), 0)
    next7 = gap * 7 if gap > 0 else 0
    worsening = last3 < first4 * 0.98
    tr["summary"] = (f"近 28 天每日 GMV：最高 {money(s28.max())}（{C._mm(s28.idxmax())}），最低 {money(s28.min())}（{C._mm(s28.idxmin())}）；"
                     f"前 7 天日均 {money(prev_avg)}，本周前 4 天日均 {money(first4)}，近 3 天日均 {money(last3)}；"
                     + ("事件：" + "；".join(f"{e['date'][5:]} {e['description']}" for e in evs) if evs else "期间没有事件记录"))
    tr["values"] = [float(s28.max()), float(s28.min()), prev_avg, first4, last3, loss_7d, next7, gap] + [float(v) for v in s28]
    k2 = book.add(tr, chapter="size")

    # ---- 三、变化拆解
    wf = C.draw(ds, dict(type="waterfall", products=[pid], by="factor", **rng, title="近 7 日 GMV 变化拆解"))
    k3 = book.add(wf, chapter="factors")
    contrib = res.get("contribution") or []
    share_t = rp.get("main_factor_share", 0.30)

    # ---- 四、根因与证据
    k4 = None
    if main and main["cause"] in CAUSE_CHART:
        types = MARK_TYPES.get(main["cause"])
        ev = {}
        mk = [e for e in evs if types and e["type"] in types and pd.Timestamp(e["date"]) <= end]
        if mk:
            ev = dict(mark_date=mk[-1]["date"], mark_text=mk[-1]["description"][:12])
        spec = C.draw(ds, CAUSE_CHART[main["cause"]](pid, ev))
        if "error" not in spec:
            k4 = book.add(spec, chapter="cause")
    conf_rule = "强：指标、事件、时间点三者对齐；中：对上两项；弱：只有指标异常"

    # ---- 五、已排除的原因
    excluded = []
    cf = (tool.get("check_factors") or {}).get("factors", {})
    main_keys = {c["cause"] for c in causes}
    for k, x in cf.items():
        if not x.get("checked") or x.get("abnormal"):
            continue
        if k == "stock":
            excluded.append("不是库存问题：没有断货或库存不足的规格")
        elif k == "price" and x.get("price") is not None and x.get("comp_price"):
            excluded.append(f"不是价格问题：到手价 {price(x['price'])}，竞品 {price(x['comp_price'])}，价格指数 {x['price_index']:.2f}")
        elif k == "rating" and x.get("rating_now") is not None:
            excluded.append(f"不是口碑问题：评分 {x['rating_now']:.2f}（28 天均值 {x['rating_base_28d']:.2f}），近 7 日退款率 {rate(x.get('refund_rate_7d'))}")
        elif k == "campaign":
            excluded.append("不是活动变化：近期没有活动开始或结束")
    for c in contrib:
        if c["factor"] == "uv" and not (main_keys & {"paid_traffic_drop", "search_traffic_drop", "campaign_end", "growth_opportunity"}) \
                and (c["share"] is None or c["share"] < share_t):
            excluded.append(f"不是流量问题：访客数 {pct(c['change_pct'])}，对 GMV 变化的贡献不到 {share_t:.0%}")
        if c["factor"] == "aov" and "aov_drop" not in main_keys and (c["share"] is None or c["share"] < share_t):
            excluded.append(f"不是客单价问题：客单价 {pct(c['change_pct'])}")

    # ---- 六、方案对比
    plans = []
    for p in res.get("plans") or []:
        est = p.get("estimate") or {}
        plans.append(dict(name=p["name"], action_id=p["action_id"], cause=p.get("cause_name"), params=p.get("params_text") or [],
                          exec_type=p.get("exec_type"), steps=p.get("steps") or [], owners=p.get("step_owners") or [],
                          due_days=p.get("due_days"), track=p.get("track"), risks=(p.get("risks") or []) + (p.get("risk_notes") or []),
                          checks=[f"{c['name']}：{'通过' if c['passed'] else '需注意'}（{c['detail']}）" for c in p.get("checks") or []],
                          margin_after=est.get("margin_rate_after"), breakeven_lift=est.get("breakeven_lift"),
                          price_after=est.get("price_after"), recoverable_daily=est.get("daily_gmv_recoverable"),
                          rationale=p.get("rationale")))
    rec = plans[0] if plans else None
    k6 = None
    if len(plans) >= 2 and any(p["margin_after"] is not None for p in plans):
        k6 = book.add(dict(type="hbar", unit="pct", title="各方案执行后的毛利率", band=None,
                           items=[dict(label=p["name"], value=p["margin_after"], tone="main") for p in plans if p["margin_after"] is not None],
                           floor=ds.profile.get("constraints", {}).get("margin_floor"),
                           summary="；".join(f"{p['name']}：执行后毛利率 {rate(p['margin_after'])}" + (f"，销量至少要涨 {p['breakeven_lift']:.1%} 才能保住总毛利" if p["breakeven_lift"] else "")
                                            for p in plans if p["margin_after"] is not None),
                           values=[x for p in plans for x in (p["margin_after"], p["breakeven_lift"]) if x is not None]), chapter="plans")

    # ---- 七、分工与跟踪
    division = None
    if rec:
        due = ymd(end + int(rec["due_days"] or 3) * DAY)
        division = dict(plan=rec["name"], steps=[dict(text=t, by=(rec["owners"] + ["我"] * 9)[i]) for i, t in enumerate(rec["steps"])],
                        due=due, track=rec["track"], threshold=ds.profile.get("review_threshold", 0.03))

    # ---- 八、历史参考
    hist = [h for h in (history or []) if h.get("cause") in main_keys]

    if not causes:
        notes.append("没有找到明确原因：各项检查都在正常范围内，建议继续观察")
    notes.append("没有评价原文，口碑问题只能根据评分、退款率和差评事件判断，不能归类差评原因")
    if "channel" in ds.available:
        notes.append("渠道只有访客数，没有渠道级成交，只能判断「哪个渠道流量变了」")
    notes.append("未来 7 天损失按当前日均缺口估算，不是预测")

    actions = []
    for p in res.get("plans") or []:
        actions.append(dict(product_id=pid, product_name=pname, kind="方案", title=p["name"], why=p.get("rationale") or "",
                            owners=list(dict.fromkeys(p.get("step_owners") or ["我"])), can_todo=True, plan=p,
                            card_id=(card or {}).get("id")))

    sev = (card or {}).get("severity_name")
    chapters = [
        chapter(1, "summary", "诊断结论", "什么问题、什么原因、建议怎么做", dict(
            product=pname, summary=res["summary"], severity=sev, rules=(card or {}).get("rule_names"),
            main_cause=main["cause_name"] if main else None, confidence=main["confidence"] if main else None,
            recommended=rec["name"] if rec else None, gmv_change=round(g["change"]) if g["change"] is not None else None,
            gmv_change_pct=r(g["change_pct"])), k1),
        chapter(2, "size", "问题有多大", "损失多少、是否在恶化", dict(
            loss_7d=round(loss_7d), since=(card or {}).get("first_date"), trigger_days=(card or {}).get("trigger_days"),
            prev_daily=round(prev_avg), first4_daily=round(first4), last3_daily=round(last3), daily_gap=round(gap),
            next7_estimate=round(next7), worsening=worsening, events=evs), k2,
            judgment=("近 3 天比本周前几天更差，还在恶化" if worsening else "近 3 天没有继续变差") + "；未来 7 天损失为估算"),
        chapter(3, "factors", "变化拆解", "流量、转化、客单价哪个出了问题", dict(
            factors=[dict(factor=c["name"], change_pct=r(c["change_pct"]), contribution=round(c["amount"]), share=r(c["share"])) for c in contrib],
            threshold=share_t), k3,
            judgment=f"贡献占比 ≥ {share_t:.0%} 的因子为主因候选"),
        chapter(4, "cause", "根因与证据", "为什么、证据够不够", dict(
            causes=[dict(cause=c["cause_name"], confidence=c["confidence"], evidence=[e["text"] for e in c["evidence"]]) for c in causes]), k4,
            judgment=f"把握度：{conf_rule}"),
        chapter(5, "excluded", "已排除的原因", "为什么不是别的原因", dict(items=excluded), None, show=bool(excluded)),
        chapter(6, "plans", "方案对比", "在哪几个做法里选、各自代价是什么", dict(plans=plans, recommended=rec["name"] if rec else None), k6,
                judgment="方案只来自动作库，已检查毛利底线、调价权限和最低价保护；刺激类方案不预测销量提升，只给保本线",
                show=bool(plans)),
        chapter(7, "division", "分工与跟踪", "谁做什么、怎么判断有没有效", division or {}, None,
                judgment=f"跟踪指标朝目标方向变化 {ds.profile.get('review_threshold', 0.03):.0%} 以上建议判为有效", show=bool(division)),
        chapter(8, "history", "历史参考", "同类问题以前怎么处理、有没有效", dict(items=hist), None, show=bool(hist)),
        chapter(9, "notes", "数据说明", "哪些没有检查、为什么", dict(items=notes), None),
    ]
    pack = dict(scene="product", title=f"{pname}单品诊断（{ymd(end)}）", period=ymd(end), subject=pname, dataset=ds.name,
                as_of=ymd(end), product_id=pid, card_id=(card or {}).get("id"), chapters=chapters, actions=actions,
                data_notes=notes, params=dict(product_id=pid), diagnosis=res)
    return pack, book


def render(pack: dict, book: C.ChartBook) -> str:
    ch = {c["key"]: c for c in pack["chapters"]}
    L = [f"# {pack['title']}", ""]

    def head(c):
        L.extend([f"## {c['heading']}", ""])

    c = ch["summary"]
    f = c["facts"]
    head(c)
    s = f"**{f['summary']}"
    if f["main_cause"]:
        s += f"把握度：{f['confidence']}。"
    if f["recommended"]:
        s += f"建议先做：{f['recommended']}。"
    L += [s + "**", "", f"[图表:{c['chart']}]", ""]
    if f["severity"]:
        L += [f"预警：【{f['severity']}】{'、'.join(f['rules'] or [])}。", ""]

    c = ch["size"]
    f = c["facts"]
    head(c)
    L += [f"**近 7 天比前 7 天少卖 {money(f['loss_7d'])}" + (f"，预警从 {f['since'][5:]} 开始、已持续 {f['trigger_days']} 天" if f["since"] else "") + "。**"
          if f["loss_7d"] else "**近 7 天没有少卖。**", "", f"[图表:{c['chart']}]", ""]
    L.append(f"前 7 天日均 {money(f['prev_daily'])}，本周前 4 天日均 {money(f['first4_daily'])}，近 3 天日均 {money(f['last3_daily'])}，"
             + ("还在恶化" if f["worsening"] else "近 3 天没有继续变差")
             + (f"；如果不处理，未来 7 天大约还会少卖 {money(f['next7_estimate'])}（按当前日均缺口估算）。" if f["next7_estimate"] else "。"))
    L.append("")

    c = ch["factors"]
    f = c["facts"]
    head(c)
    top = [x for x in f["factors"] if x["share"] is not None and x["share"] >= f["threshold"]]
    share_txt = lambda x: "是下滑的全部来源" if x["share"] > 1 else f"占 {x['share']:.0%}"  # noqa: E731
    L += [("**" + "、".join(f"{x['factor']}（{pct(x['change_pct'])}，贡献 {money_signed(x['contribution'])}，{share_txt(x)}）" for x in top)
           + "是主要变化来源。**") if top else "**三个因子变化都不大。**", "", f"[图表:{c['chart']}]", ""]
    L += [f"- {x['factor']}：{pct(x['change_pct'])}，贡献 {money_signed(x['contribution'])}" for x in f["factors"]]
    L.append("")

    c = ch["cause"]
    f = c["facts"]
    head(c)
    if f["causes"]:
        c0 = f["causes"][0]
        L += [f"**根因：{c0['cause']}（把握：{c0['confidence']}）。**", ""]
        if c["chart"]:
            L += [f"[图表:{c['chart']}]", ""]
        for x in f["causes"]:
            L += [f"**{x['cause']}**（把握：{x['confidence']}）"] + [f"- {e}" for e in x["evidence"]] + [""]
        L += [f"注：{c['judgment']}。", ""]
    else:
        L += ["**没有找到明确原因，各项检查都在正常范围内。**", ""]

    c = ch["excluded"]
    if c["show"]:
        head(c)
        L += [f"- {x}" for x in c["facts"]["items"]] + [""]

    c = ch["plans"]
    if c["show"]:
        f = c["facts"]
        head(c)
        L += [f"**建议先做「{f['recommended']}」。**", ""]
        if c["chart"]:
            L += [f"[图表:{c['chart']}]", ""]
        L += ["| 方案 | 做法 | 代价与检查 | 风险 |", "| --- | --- | --- | --- |"]
        for p in f["plans"]:
            cost = []
            if p["margin_after"] is not None:
                cost.append(f"执行后毛利率 {rate(p['margin_after'])}")
            if p["breakeven_lift"]:
                cost.append(f"销量至少要涨 {p['breakeven_lift']:.1%} 才能保住总毛利")
            if p["recoverable_daily"]:
                cost.append(f"恢复后每天可挽回约 {money(p['recoverable_daily'])}")
            cost += p["checks"]
            L.append(f"| {p['name']} | {'；'.join(p['params'])} | {'；'.join(cost) or '—'} | {'；'.join(p['risks']) or '—'} |")
        L += ["", f"注：{c['judgment']}。", ""]

    c = ch["division"]
    if c["show"]:
        f = c["facts"]
        head(c)
        L += [f"**按「{f['plan']}」执行，截止 {f['due']}；完成后跟踪 {f['track']['metric_name']} {f['track']['days']} 天。**", ""]
        L += [f"{i}. 【{s['by']}】{s['text']}" for i, s in enumerate(f["steps"], 1)]
        L += ["", f"{c['judgment']}。采纳方案后会生成待办，分给同事的步骤通过飞书通知。", ""]

    c = ch["history"]
    if c["show"]:
        head(c)
        for h in c["facts"]["items"]:
            L.append(f"- {h['name']}（{h['product_name']}）：{h.get('metric_name') or ''} {pct(h.get('change_pct'))}，复盘结论「{h.get('outcome_name') or '—'}」"
                     + (f"：{h['review_note']}" if h.get("review_note") else ""))
        L.append("")

    c = ch["notes"]
    head(c)
    L += [f"- {x}" for x in c["facts"]["items"]]
    return "\n".join(L)
