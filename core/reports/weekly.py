"""周度经营分析：看整个类目这一周怎么样、变化从哪来、下周做什么。分析剧本见 methods/playbooks/weekly_review.md。"""
from __future__ import annotations

import calendar

import pandas as pd

from .. import charts as C
from .. import config, sop, tiering
from ..metrics import DAY
from ..weekly import effect
from .common import chapter, events_between, mmdd, md, money, money_signed, num, pct, r, rate, ymd

LABELS = {
    "expected": ("预期内", "muted"),
    "needs": ("需处理", "bad"),
    "adjust": ("主动调整", "warn"),
    "opportunity": ("机会", "good"),
    "growth": ("自然增长", "main"),
    "noise": ("正常波动", "muted"),
}


def week_ends(ds, n=4) -> list[str]:
    """可选的周：以数据截止日为最后一天往前推。"""
    return [ymd(ds.as_of - 7 * i * DAY) for i in range(n)]


def _label(ds, pid, change, cur, prev, card, diag) -> tuple[str, str]:
    """给变化商品贴标签，返回 (标签, 原因)。"""
    evs = events_between(ds, pid, prev[0] - 7 * DAY, cur[1])
    own = [e for e in evs if e["product_id"] == pid]
    if change < 0:
        ended = [e for e in own if e["type"] == "campaign_end"] + \
            [e for e in evs if e["type"] == "promo_day" and pd.Timestamp(e["date"]) <= prev[1]]
        if ended or (card and card.get("expected")):
            e = ended[-1] if ended else None
            return "expected", (f"{md(e['date'])} {e['description']}，属于活动后的正常回落" if e
                                else card["expected"].get("reason", "预期内的回落"))
        adj = [e for e in own if e["type"] in ("ad_budget_change", "price_change")]
        if adj:
            e = adj[-1]
            return "adjust", f"{md(e['date'])} {e['description']}（我们自己的调整）"
        if card and card["is_today"] and card["severity"] in ("red", "yellow"):
            causes = [c for c in (diag or {}).get("root_causes", [])]
            if causes:
                c0 = causes[0]
                ev = "；".join(x["text"] for x in c0.get("evidence", [])[:2] if len(x["text"]) <= 40)
                ev = ev or c0.get("evidence", [{}])[0].get("text", "")
                return "needs", f"{c0['cause_name']}（{ev}）"
            return "needs", "、".join(card["rule_names"])
        return "noise", "未触发预警"
    opp = [e for e in own if e["type"] in ("external_content", "campaign_start")]
    if opp or (card and "R08" in card.get("rules", [])):
        return "opportunity", (f"{md(opp[-1]['date'])} {opp[-1]['description']}" if opp else "访客或 GMV 明显高于正常水平")
    return "growth", "未触发预警"


def build(ds, cards: list[dict], actions: list[dict] | None = None, diagnoses: dict | None = None,
          week_end=None, target: float | None = None, book: C.ChartBook | None = None):
    prof = ds.profile
    rp = prof.get("report", {})
    book = book or C.ChartBook(rp.get("extra_chart_limit", 3))
    actions = actions or []
    diagnoses = dict(diagnoses or {})
    end = pd.Timestamp(week_end) if week_end else ds.as_of
    end = min(end, ds.as_of)
    cur = (end - 6 * DAY, end)
    prev = (cur[0] - 7 * DAY, cur[0] - DAY)
    pids = ds.product_ids()
    today_cards = {c["product_id"]: c for c in cards if c["is_today"]}
    chapters, notes = [], []
    rng = dict(start=ymd(cur[0]), end=ymd(cur[1]), compare_start=ymd(prev[0]), compare_end=ymd(prev[1]))

    # ---- 一、结论（指标卡）
    kpi = C.draw(ds, dict(type="kpi", products=[C.ALL], metrics=["gmv", "uv", "cvr", "aov", "refund_rate"], **rng,
                          title="本周核心指标（对比上周）"))
    k1 = book.add(kpi, chapter="summary")
    g1, g0 = kpi["items"][0]["value"], kpi["items"][0]["prev"]
    gchg = kpi["items"][0]["change"]

    # ---- 二、大盘趋势
    wins = C.weeks_back(ds, rp.get("trend_weeks", 8), end)
    wk_gmv = [sum((C.window_value(ds, [p], "gmv", w) or 0) for p in pids) * 7 for w in wins]
    trend = dict(type="trend", title="近 8 周 GMV，本周高亮", unit="money", x=[C.wlabel(w) for w in wins],
                 series=[dict(name="周 GMV", data=[round(v) for v in wk_gmv], style="bar")], highlight=len(wins) - 1,
                 baseline=dict(value=round(sum(wk_gmv) / len(wk_gmv)), text="8 周平均"))
    avg8 = sum(wk_gmv) / len(wk_gmv)
    rank = sorted(wk_gmv, reverse=True).index(wk_gmv[-1]) + 1
    trend["summary"] = "；".join(f"{C.wlabel(w)} {money(v)}" for w, v in zip(wins, wk_gmv)) + f"；8 周平均 {money(avg8)}，本周排第 {rank}"
    trend["values"] = wk_gmv + [avg8]
    alerts_open = [c for c in today_cards.values() if c["severity"] in ("red", "yellow") and not c.get("expected")]
    band = rp.get("flat_band", 0.05)
    if gchg is not None and abs(gchg) <= band and not alerts_open:
        verdict = "整体平稳"
    elif gchg is not None and gchg > band:
        verdict = "整体上升"
    elif gchg is not None and gchg < -band:
        verdict = "整体下滑"
    else:
        verdict = "大盘平稳，但有商品在预警"
    facts2 = dict(gmv=round(g1), gmv_prev=round(g0), change_pct=r(gchg), avg_8w=round(avg8), rank_in_8w=rank,
                  verdict=verdict, open_alerts=len(alerts_open), flat_band=band)
    if target:
        m0 = end.replace(day=1)
        dim = calendar.monthrange(end.year, end.month)[1]
        mtd = sum((C.window_value(ds, [p], "gmv", (m0, end)) or 0) for p in pids) * ((end - m0).days + 1)
        el = (end - m0).days + 1
        facts2["target"] = dict(month=f"{end.month} 月", target=round(target), mtd=round(mtd), progress=r(mtd / target),
                                time_progress=r(el / dim), forecast=round(mtd / el * dim), gap=round(mtd / el * dim - target),
                                days_left=dim - el)
    else:
        notes.append("没有设置月度 GMV 目标，周报不显示目标完成进度（可在报告中心设置）")
    k2 = book.add(trend, chapter="trend")

    # ---- 三、变化拆解
    wf = C.draw(ds, dict(type="waterfall", products=[C.ALL], by="factor", **rng, title="GMV 变化拆解：流量、转化、客单价"))
    k3 = book.add(wf, chapter="factors")
    fw = C.factor_waterfall(ds, pids, cur, prev)["raw"]
    delta = fw["v1"]["gmv"] - fw["v0"]["gmv"]
    names = {"uv": "访客数", "cvr": "支付转化率", "aov": "客单价"}
    share = rp.get("main_factor_share", 0.30)
    factors = [dict(factor=names[k], prev=r(fw["v0"][k]), cur=r(fw["v1"][k]),
                    change_pct=r(fw["v1"][k] / fw["v0"][k] - 1) if fw["v0"][k] else None,
                    contribution=round(fw["contrib"][k]), share=r(fw["contrib"][k] / delta) if delta else None)
               for k in ("uv", "cvr", "aov")]
    main = [f["factor"] for f in factors if f["share"] is not None and f["share"] >= share]
    # 客单价：各商品价格变化 vs 卖的商品组合变化
    s0, s1, a0, a1 = {}, {}, {}, {}
    b0 = b1 = 0.0
    for p in pids:
        g = ds.pdays(p)
        x0, x1 = g[(g.index >= prev[0]) & (g.index <= prev[1])], g[(g.index >= cur[0]) & (g.index <= cur[1])]
        s0[p], s1[p] = float(x0["buyers"].sum()), float(x1["buyers"].sum())
        a0[p] = float(x0["gmv"].sum()) / s0[p] if s0[p] else 0.0
        a1[p] = float(x1["gmv"].sum()) / s1[p] if s1[p] else a0[p]
        b0 += s0[p]
        b1 += s1[p]
    price_eff = sum((s0[p] / b0) * (a1[p] - a0[p]) for p in pids) if b0 else 0.0
    mix_eff = sum((s1[p] / b1 - s0[p] / b0) * a1[p] for p in pids) if b0 and b1 else 0.0
    mix_driver = max(pids, key=lambda p: abs((s1[p] / b1 - s0[p] / b0) * a1[p]) if b0 and b1 else 0)
    aov = dict(prev=r(fw["v0"]["aov"], 2), cur=r(fw["v1"]["aov"], 2), price_effect=r(price_eff, 2), mix_effect=r(mix_eff, 2),
               mix_driver=ds.product(mix_driver)["product_name"],
               mix_driver_share_prev=r(s0[mix_driver] / b0) if b0 else None, mix_driver_share_cur=r(s1[mix_driver] / b1) if b1 else None,
               mix_driver_aov=r(a1[mix_driver], 2),
               reading="组合变化为主（卖的商品结构变了）" if abs(mix_eff) > abs(price_eff) else "价格变化为主（商品本身的客单价变了）")

    # ---- 四、变化从哪来
    pw = C.product_waterfall(ds, pids, cur, prev, rp.get("cover_share", 0.8), rp.get("max_items", 6))
    net = sum((x["change"] or 0) for x in pw["top"] + pw["rest"])
    items = []
    for x in pw["top"]:
        pid = x["pid"]
        card = today_cards.get(pid)
        if card and card["severity"] in ("red", "yellow") and pid not in diagnoses and not card.get("expected"):
            try:
                diagnoses[pid] = sop.run(ds, pid, card=card, tiers=tiering.compute(ds))[1]
            except Exception:  # noqa: BLE001
                pass
        lab, why = _label(ds, pid, x["change"] or 0, cur, prev, card, diagnoses.get(pid))
        same = net and (x["change"] or 0) * net > 0
        items.append(dict(product_id=pid, product_name=x["label"], gmv_prev=round((x["prev"] or 0) * 7),
                          gmv_cur=round((x["cur"] or 0) * 7), change=round(x["change"] or 0), change_pct=r(x["change_pct"]),
                          share_of_swing=r(abs(x["change"] or 0) / pw["total_abs"]),
                          share_of_net=r((x["change"] or 0) / net) if same else None,
                          label=LABELS[lab][0], label_key=lab, reason=why,
                          severity=(card or {}).get("severity_name")))
    if any((i["change"] or 0) * net < 0 for i in items):
        for i in items:
            i["share_of_net"] = None
    rest = sum((x["change"] or 0) for x in pw["rest"])
    wf2 = dict(type="waterfall", unit="money", title="GMV 变化来自哪些商品（颜色 = 性质）",
               start=dict(label="上周", value=round(sum((x["prev"] or 0) for x in pw["top"] + pw["rest"]) * 7)),
               end=dict(label="本周", value=round(sum((x["cur"] or 0) for x in pw["top"] + pw["rest"]) * 7)),
               items=[dict(label=i["product_name"], value=i["change"], tone=LABELS[i["label_key"]][1], note=i["label"]) for i in items]
               + ([dict(label=f"其他 {len(pw['rest'])} 个商品", value=round(rest), tone="muted", note="合计")] if pw["rest"] else []),
               legend=[dict(tone=t, text=n) for k, (n, t) in LABELS.items() if any(i["label_key"] == k for i in items)])
    wf2["summary"] = "；".join(f"{i['product_name']} {money_signed(i['change'])}（{i['label']}：{i['reason']}）" for i in items) + \
        (f"；其他 {len(pw['rest'])} 个商品合计 {money_signed(rest)}" if pw["rest"] else "")
    wf2["values"] = [i["change"] for i in items] + [rest, wf2["start"]["value"], wf2["end"]["value"], net]
    k4 = book.add(wf2, chapter="sources")

    # ---- 五、商品结构
    st = C.draw(ds, dict(type="stacked_bar", products=[C.ALL], by="tier", end=ymd(end), title="各分层 GMV 占比（近 4 周）"))
    k5 = book.add(st, chapter="structure")
    tiers = tiering.compute(ds)
    shares = {s["name"]: s["data"] for s in st["series"]}
    tier_members = {}
    for p in pids:
        tier_members.setdefault(tiers[p]["tier_name"], []).append(ds.product(p)["product_name"])
    hero = shares.get("爆品", [0])
    struct_flags = []
    if len(hero) >= 2 and hero[-1] - hero[0] > 0.05:
        struct_flags.append("爆品占比在上升，生意更集中")
    if len(hero) >= 2 and hero[-1] - hero[0] < -0.05:
        struct_flags.append(f"爆品占比从 {hero[0]:.0%} 降到 {hero[-1]:.0%}")
    if shares.get("潜力品") and max(shares["潜力品"]) < 0.02:
        struct_flags.append("没有潜力品在起量，缺少接班的商品")
    if shares.get("利润品") and len(shares["利润品"]) >= 2 and shares["利润品"][-1] - shares["利润品"][0] < -0.05:
        struct_flags.append("利润品占比下滑")
    facts5 = dict(shares={k: [r(x) for x in v] for k, v in shares.items()}, weeks=st["x"],
                  members={k: v for k, v in tier_members.items()}, flags=struct_flags or ["结构没有明显变化"],
                  note="分层按当前数据计算")

    # ---- 六、风险前瞻
    risks = _risks(ds, end)

    # ---- 七、上周动作效果
    wk_s, wk_e = ymd(cur[0]), ymd(cur[1])
    done = []
    for a in actions:
        if a.get("stage") == "done" and a.get("closed_date") and wk_s <= a["closed_date"] <= wk_e:
            e = effect(ds, a["product_id"], a.get("track_metric") or "gmv", a.get("exec_date"),
                       n=int(a.get("track_days") or 5), variant=a.get("variant"))
            done.append(dict(todo=a["name"], product_name=a.get("product_name"), outcome=a.get("outcome_name"),
                             note=a.get("review_note"), metric_name=e.get("metric_name"), metric=e.get("metric"),
                             before=e.get("before"), after=e.get("after"), change_pct=e.get("change_pct")))
    opens = [a for a in actions if a.get("stage") in ("doing", "tracking", "review")]
    counts = dict(doing=sum(a["stage"] == "doing" for a in opens), tracking=sum(a["stage"] == "tracking" for a in opens),
                  review=sum(a["stage"] == "review" for a in opens),
                  overdue=[dict(todo=a["name"], product_name=a.get("product_name"), due=a.get("due_date"))
                           for a in opens if a["stage"] == "doing" and a.get("due_date") and a["due_date"] < ymd(ds.as_of)])
    k7 = None
    if done:
        hb = dict(type="hbar", unit="pct", title="本周复盘完成的待办：跟踪指标执行前后变化",
                  items=[dict(label=f"{d['todo']}（{d['metric_name']}）", value=d["change_pct"],
                              tone="good" if (d["change_pct"] or 0) >= 0 else "bad") for d in done])
        hb["summary"] = "；".join(f"{d['todo']}：{d['metric_name']} {d['before']} → {d['after']}（{pct(d['change_pct'])}），复盘结论{d['outcome']}" for d in done)
        hb["values"] = [x for d in done for x in (d["before"], d["after"], d["change_pct"]) if x is not None]
        k7 = book.add(hb, chapter="todos")

    # ---- 八、下周重点
    nexts = _next_focus(ds, items, risks, counts, actions, diagnoses, today_cards)
    notes += ["未对比行业大盘和竞品销量（平台没有行业数据），「是否跑赢行业」无法判断",
              "「主动调整」只根据事件记录判断；没有推广花费数据，无法评估投放效率",
              "「变化从哪来」按 GMV 变化额排序，逐个解释覆盖总波动 {:.0%} 的商品".format(pw["covered"])]

    period = f"{wk_s} 至 {wk_e}"
    chapters = [
        chapter(1, "summary", "本周结论", "这周生意怎么样、主要原因是什么、下周最该做什么", dict(
            gmv=round(g1), gmv_prev=round(g0), change_pct=r(gchg), verdict=verdict,
            main_source=[dict(product=i["product_name"], label=i["label"], change=i["change"], share_of_net=i["share_of_net"],
                              share_of_swing=i["share_of_swing"]) for i in items[:3]],
            first_next=nexts[0]["title"] if nexts else None), k1),
        chapter(2, "trend", "大盘趋势", "这周是不是异常的一周", facts2, k2,
                judgment=f"环比 {pct(gchg)}，{verdict}（判断标准：环比在 ±{band:.0%} 内且没有未处理的红黄预警 = 平稳）"),
        chapter(3, "factors", "变化拆解", "GMV 变化是流量、转化还是客单价带来的", dict(
            gmv_change=round(delta), factors=factors, main_factors=main, aov_detail=aov, main_share_threshold=share), k3,
            judgment=("主因：" + "、".join(main)) if main else "没有单一主因，三个因子共同作用"),
        chapter(4, "sources", "变化从哪来", "是哪几个商品、各是什么性质", dict(
            items=items, others=dict(count=len(pw["rest"]), change=round(rest)), net_change=round(net),
            covered=r(pw["covered"])), k4,
            judgment="标签规则：预期内 = 活动结束或大促后的回落；主动调整 = 我们自己调了预算或价格；需处理 = 有未恢复的红黄预警；机会 = 访客或 GMV 明显高于正常，或有站外引流 / 新活动"),
        chapter(5, "structure", "商品结构", "生意是不是越来越集中、有没有接班的商品", facts5, k5,
                judgment="；".join(facts5["flags"])),
        chapter(6, "risks", "风险前瞻", "下周哪些商品可能出问题", dict(items=risks), None,
                judgment=f"标准：库存可售天数低于安全库存 {prof.get('constraints', {}).get('safety_days', 7)} 天、比竞品贵 {prof.get('price_gap', 0.05):.0%} 以上、评分比 28 天均值低 {prof.get('rating_drop', 0.1)} 以上"),
        chapter(7, "todos", "上周动作效果", "做了的事有没有用", dict(done=done, counts=counts), k7,
                judgment="跟踪指标朝目标方向变化 {:.0%} 以上建议判为有效；执行前后对比，不等同于严格的因果效果".format(prof.get("review_threshold", 0.03)),
                show=bool(done or opens)),
        chapter(8, "next", "下周重点", "最该做的 3 件事", dict(items=nexts), None),
        chapter(9, "notes", "数据说明", "哪些没有分析、为什么", dict(items=notes), None),
    ]
    pack = dict(scene="weekly", title=f"{prof.get('name', ds.category)}周度经营分析（{period}）", period=period,
                subject=prof.get("name", ds.category), dataset=ds.name, as_of=ymd(ds.as_of),
                chapters=chapters, actions=[n for n in nexts if n.get("can_todo")], data_notes=notes,
                params=dict(week_end=ymd(end), target=target))
    return pack, book


def _risks(ds, end) -> list[dict]:
    prof = ds.profile
    safety = prof.get("constraints", {}).get("safety_days", 7)
    out = []
    future = events_between(ds, None, end + DAY, end + 60 * DAY)
    for pid in ds.product_ids():
        name = ds.product(pid)["product_name"]
        g = ds.pdays(pid)
        g = g[g.index <= end]
        if "stock" in ds.available:
            v = ds.vdays(pid)
            v = v[v["date"] <= end]
            last = v[v["date"] == end]
            for _, row in last.iterrows():
                avg = v[(v["variant_id"] == row["variant_id"]) & (v["date"] > end - 7 * DAY)]["units"].mean()
                if row["stock_units"] <= 0:
                    rs = [e for e in future if e["product_id"] == pid and e["type"] == "restock"]
                    out.append(dict(product_id=pid, product_name=name, kind="断货", level="红",
                                    value=f"{row['variant_name']} 已断货", standard="库存为 0",
                                    note=(f"在途补货：{rs[0]['description']}" if rs else "没有在途补货记录")))
                elif avg and avg > 0:
                    dos = float(row["stock_units"]) / float(avg)
                    if dos < safety:
                        out.append(dict(product_id=pid, product_name=name, kind="断货风险", level="红" if dos < safety / 2 else "黄",
                                        value=f"{row['variant_name']} 可售 {dos:.1f} 天", standard=f"低于 {safety} 天",
                                        days_of_supply=r(dos, 1), note=""))
        if "comp_price" in ds.available and len(g) and g["comp_price"].iloc[-1] > 0:
            idx = float(g["price"].iloc[-1] / g["comp_price"].iloc[-1])
            if idx > 1 + prof.get("price_gap", 0.05):
                out.append(dict(product_id=pid, product_name=name, kind="价格劣势", level="黄",
                                value=f"到手价 {g['price'].iloc[-1]:.0f} 元，竞品 {g['comp_price'].iloc[-1]:.0f} 元",
                                standard=f"贵 {prof.get('price_gap', 0.05):.0%} 以上", price_index=r(idx, 3), note=f"贵 {idx - 1:.1%}"))
        if "rating" in ds.available and len(g) >= 28:
            base = float(g["rating"].iloc[-29:-1].mean())
            now = float(g["rating"].iloc[-1])
            if base - now >= prof.get("rating_drop", 0.1) - 1e-9:
                out.append(dict(product_id=pid, product_name=name, kind="口碑下滑", level="黄",
                                value=f"评分 {now:.2f}（28 天均值 {base:.2f}）", standard=f"下降 {prof.get('rating_drop', 0.1)} 以上", note=""))
    for e in future:
        if e["type"] in ("campaign_start", "promo_day"):
            nm = ds.product(e["product_id"])["product_name"] if e["product_id"] else "全店"
            out.append(dict(product_id=e["product_id"], product_name=nm, kind="即将开始的活动", level="蓝",
                            value=f"{md(e['date'])} {e['description']}", standard="提前备货、确认价格", note=""))
    order = {"红": 0, "黄": 1, "蓝": 2}
    out.sort(key=lambda x: order[x["level"]])
    return out


def _next_focus(ds, items, risks, counts, actions, diagnoses, cards) -> list[dict]:
    open_by_pid = {}
    for a in actions:
        if a.get("stage") in ("doing", "tracking", "review"):
            open_by_pid.setdefault(a["product_id"], []).append(a)
    out = []
    for i in sorted([i for i in items if i["label_key"] == "needs"], key=lambda x: x["change"]):
        pid = i["product_id"]
        d = diagnoses.get(pid) or {}
        plan = (d.get("plans") or [None])[0]
        ex = open_by_pid.get(pid)
        out.append(dict(product_id=pid, product_name=i["product_name"], kind="需处理",
                        title=f"处理{i['product_name']}：{(plan or {}).get('name') or i['reason']}",
                        why=f"本周少卖 {money(-i['change'])}，{i['reason']}",
                        first_step=(plan or {}).get("steps", [None])[0],
                        owners=list(dict.fromkeys((plan or {}).get("step_owners") or ["我"])),
                        existing_todo=dict(id=ex[0]["id"], name=ex[0]["name"], stage=ex[0].get("status")) if ex else None,
                        can_todo=bool(plan) and not ex, plan=plan, card_id=(cards.get(pid) or {}).get("id")))
    for i in [i for i in items if i["label_key"] == "adjust"]:
        pid = i["product_id"]
        out.append(dict(product_id=pid, product_name=i["product_name"], kind="主动调整",
                        title=f"确认{i['product_name']}的调整是否符合预期",
                        why=f"本周少卖 {money(-i['change'])}，{i['reason']}",
                        first_step="和投放运营确认调整后的花费与成交是否在预期内，不符合就恢复",
                        owners=["投放运营"], existing_todo=None, can_todo=True,
                        draft=dict(name=f"确认{i['product_name']}的调整是否符合预期",
                                   steps=[dict(text="复核调整后的花费与成交，反馈是否在预期内；不符合预期时给出恢复建议", by="投放运营"),
                                          dict(text="根据反馈决定维持还是恢复", by="我")],
                                   track_metric="gmv", track_days=7,
                                   note=f"本周少卖 {money(-i['change'])}，{i['reason']}")))
    covered = {x["product_id"] for x in out}
    for rk in risks:
        if rk["level"] == "红" and rk["product_id"] and rk["product_id"] not in covered:
            covered.add(rk["product_id"])
            ex = open_by_pid.get(rk["product_id"])
            out.append(dict(product_id=rk["product_id"], product_name=rk["product_name"], kind=rk["kind"],
                            title=f"{rk['product_name']}{rk['kind']}：{rk['value']}", why=rk.get("note") or rk["standard"],
                            first_step="和供应链确认补货数量与到货时间", owners=["供应链"],
                            existing_todo=dict(id=ex[0]["id"], name=ex[0]["name"], stage=ex[0].get("status")) if ex else None,
                            can_todo=not ex,
                            draft=dict(name=f"{rk['product_name']}补货", steps=[
                                dict(text=f"{rk['value']}，确认补货数量与到货日期，反馈最终到仓时间", by="供应链"),
                                dict(text="到货前在详情页提示，引导选择有货规格", by="我")], track_metric="cvr", track_days=7,
                                note=f"{rk['value']}（{rk['standard']}）")))
    for i in [i for i in items if i["label_key"] == "opportunity"]:
        out.append(dict(product_id=i["product_id"], product_name=i["product_name"], kind="机会",
                        title=f"放大{i['product_name']}的增长", why=f"本周多卖 {money(i['change'])}，{i['reason']}",
                        first_step="确认库存能否撑住增长，考虑追加投放", owners=["我", "供应链"], existing_todo=None, can_todo=False))
    for o in counts["overdue"]:
        out.append(dict(product_id=None, product_name=o["product_name"], kind="逾期待办", title=f"推进逾期待办：{o['todo']}",
                        why=f"截止 {o['due']}，已逾期", first_step="在待办中心催办或调整截止日期", owners=["我"],
                        existing_todo=None, can_todo=False))
    return out[:3]


def render(pack: dict, book: C.ChartBook) -> str:
    """规则版周报（不调用大模型时）：按剧本的章节与句式写出。"""
    ch = {c["key"]: c for c in pack["chapters"]}
    L = [f"# {pack['title']}", ""]

    def head(c):
        L.extend([f"## {c['heading']}", ""])

    # 一
    c = ch["summary"]
    f = c["facts"]
    head(c)
    src = f["main_source"]
    s1 = f"本周 GMV {money(f['gmv'])}，环比 {pct(f['change_pct'])}，{f['verdict']}。"
    parts = [f"{x['product']}（{x['label']}，{money_signed(x['change'])}"
             + (f"，占净变化 {x['share_of_net']:.0%}" if x["share_of_net"] else f"，占总波动 {x['share_of_swing']:.0%}") + "）"
             for x in src[:2]]
    s2 = ("变化主要来自" + "、".join(parts) + "。") if parts else ""
    s3 = f"下周最该做的是：{f['first_next']}。" if f["first_next"] else "下周没有需要特别处理的商品。"
    L += [f"**{s1}{s2}{s3}**", "", f"[图表:{c['chart']}]", ""]

    # 二
    c = ch["trend"]
    f = c["facts"]
    head(c)
    L += [f"**本周 GMV {money(f['gmv'])}，在近 8 周里排第 {f['rank_in_8w']}，8 周平均 {money(f['avg_8w'])}。**", "",
          f"[图表:{c['chart']}]", "", c["judgment"] + "。", ""]
    if f.get("target"):
        t = f["target"]
        L += [f"{t['month']}目标 {money(t['target'])}，截至本周累计 {money(t['mtd'])}，完成 {pct(t['progress'], False)}，"
              f"时间过去 {pct(t['time_progress'], False)}；按目前速度全月预计 {money(t['forecast'])}，"
              + (f"比目标少 {money(-t['gap'])}。" if t["gap"] < 0 else f"超出目标 {money(t['gap'])}。"), ""]

    # 三
    c = ch["factors"]
    f = c["facts"]
    head(c)
    fs = f["factors"]
    fmt_v = {"访客数": num, "支付转化率": rate, "客单价": lambda v: f"{v:.2f} 元"}
    lead = "、".join(f"{x['factor']}（贡献 {money_signed(x['contribution'])}）" for x in fs if x["factor"] in f["main_factors"])
    L += [f"**GMV 变化 {money_signed(f['gmv_change'])}，" + (f"主因是{lead}。**" if lead else "三个因子贡献接近，没有单一主因。**"), "",
          f"[图表:{c['chart']}]", ""]
    L += [f"- {x['factor']}：{fmt_v[x['factor']](x['prev'])} → {fmt_v[x['factor']](x['cur'])}（{pct(x['change_pct'])}），贡献 {money_signed(x['contribution'])}"
          + ((f"，占变化 {x['share']:.0%}" if x["share"] >= 0 else "，方向相反，抵消了一部分变化") if x["share"] is not None else "") for x in fs]
    a = f["aov_detail"]
    if a["cur"] is not None and a["prev"] is not None and abs(a["cur"] - a["prev"]) >= 0.5:
        L += ["", f"客单价 {a['prev']:.2f} 元 → {a['cur']:.2f} 元，{a['reading']}：各商品自身客单价变化的影响 {a['price_effect']:+.2f} 元，"
              f"商品组合变化的影响 {a['mix_effect']:+.2f} 元。"
              + (f"主要是{a['mix_driver']}（客单价 {a['mix_driver_aov']:.2f} 元）的买家占比从 {a['mix_driver_share_prev']:.1%} 变为 {a['mix_driver_share_cur']:.1%}。"
                 if abs(a["mix_effect"]) > abs(a["price_effect"]) else "")]
    L.append("")

    # 四
    c = ch["sources"]
    f = c["facts"]
    head(c)
    top = f["items"]
    needs = [i for i in top if i["label_key"] == "needs"]
    L += [f"**{len(top)} 个商品覆盖了 {f['covered']:.0%} 的波动"
          + (f"，其中真正需要处理的是{'、'.join(i['product_name'] for i in needs)}。**" if needs else "，没有需要处理的问题商品。**"), "",
          f"[图表:{c['chart']}]", "",
          "| 商品 | 上周 → 本周 | 变化 | 性质 | 原因 |", "| --- | --- | --- | --- | --- |"]
    L += [f"| {i['product_name']} | {money(i['gmv_prev'])} → {money(i['gmv_cur'])} | {money_signed(i['change'])}（{pct(i['change_pct'])}） "
          f"| {i['label']} | {i['reason']} |" for i in top]
    if f["others"]["count"]:
        L.append(f"| 其他 {f['others']['count']} 个商品 | — | {money_signed(f['others']['change'])} | 正常波动 | 合计 |")
    L += ["", f"注：{c['judgment']}。", ""]

    # 五
    c = ch["structure"]
    f = c["facts"]
    head(c)
    sh = f["shares"]
    L += [f"**{c['judgment']}。**", "", f"[图表:{c['chart']}]", ""]
    L += [f"- {k}：{' → '.join(pct(x, False, 0) for x in v)}（{'、'.join(f['members'].get(k, [])[:4]) or '无'}{' 等' if len(f['members'].get(k, [])) > 4 else ''}）"
          for k, v in sh.items()]
    L += ["", f"注：{f['note']}。", ""]

    # 六
    c = ch["risks"]
    head(c)
    rk = c["facts"]["items"]
    if rk:
        L += [f"**下周有 {len(rk)} 项风险需要关注，其中红色 {sum(x['level'] == '红' for x in rk)} 项。**", "",
              "| 商品 | 风险 | 当前情况 | 判断标准 | 说明 |", "| --- | --- | --- | --- | --- |"]
        L += [f"| {x['product_name']} | 【{x['level']}】{x['kind']} | {x['value']} | {x['standard']} | {x['note'] or '—'} |" for x in rk]
    else:
        L.append("**下周没有发现断货、价格或口碑风险。**")
    L += ["", f"{c['judgment']}。", ""]

    # 七
    c = ch["todos"]
    if c["show"]:
        head(c)
        f = c["facts"]
        k = f["counts"]
        if f["done"]:
            eff = sum(1 for d in f["done"] if d["outcome"] == "有效")
            L += [f"**本周复盘完成 {len(f['done'])} 条待办，{eff} 条有效。**", "", f"[图表:{c['chart']}]", ""]
            L += [f"- {d['todo']}（{d['product_name']}）：{d['metric_name']} 执行前后 {pct(d['change_pct'])}，复盘结论「{d['outcome']}」" +
                  (f"：{d['note']}" if d["note"] else "") for d in f["done"]]
        else:
            L.append("**本周没有复盘完成的待办。**")
        L += ["", f"另有执行中 {k['doing']} 条（其中逾期 {len(k['overdue'])} 条）、跟踪中 {k['tracking']} 条、待复盘 {k['review']} 条。"
              + ("逾期：" + "；".join(f"{o['todo']}（截止 {o['due']}）" for o in k["overdue"]) + "。" if k["overdue"] else ""), "",
              f"注：{c['judgment']}。", ""]

    # 八
    c = ch["next"]
    head(c)
    xs = c["facts"]["items"]
    if xs:
        for n, x in enumerate(xs, 1):
            ex = x.get("existing_todo")
            L.append(f"{n}. **{x['title']}**。{x['why']}。第一步：{x['first_step'] or '—'}（负责：{'、'.join(x['owners'])}）"
                     + (f"。已有待办「{ex['name']}」在处理" if ex else "") + "。")
    else:
        L.append("下周没有需要特别处理的事项，保持日常经营。")
    L.append("")

    # 九
    c = ch["notes"]
    head(c)
    L += [f"- {x}" for x in c["facts"]["items"]]
    return "\n".join(L)
