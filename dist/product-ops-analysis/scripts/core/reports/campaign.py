"""活动复盘：一场活动值不值、增量从哪来、下次怎么做。分析剧本见 methods/playbooks/campaign_review.md。"""
from __future__ import annotations

import pandas as pd

from .. import charts as C
from .. import config
from ..metrics import DAY
from .common import chapter, md, money, money_signed, num, pct, price, r, rate, ymd


def list_campaigns(ds) -> list[dict]:
    """从事件记录里找活动：单品活动（开始 + 结束）与全店大促日。"""
    ev = ds.events
    out = []
    if ev.empty:
        return out
    starts = ev[ev["event_type"] == "campaign_start"]
    for _, s in starts.iterrows():
        pid = s["product_id"] or None
        ends = ev[(ev["event_type"] == "campaign_end") & (ev["product_id"] == s["product_id"]) & (ev["date"] >= s["date"])]
        if ends.empty:
            continue
        e = ends.sort_values("date").iloc[0]
        if e["date"] > ds.as_of:
            continue
        name = str(s["description"]).replace("活动开始", "").replace("开始", "").strip() or "活动"
        out.append(dict(id=f"{pid or 'store'}-{s['date']:%Y%m%d}", name=name, product_id=pid,
                        product_name=ds.product(pid)["product_name"] if pid else "全店",
                        start=ymd(s["date"]), end=ymd(e["date"]), kind="单品活动", description=s["description"]))
    for _, s in ev[ev["event_type"] == "promo_day"].iterrows():
        if s["date"] > ds.as_of:
            continue
        out.append(dict(id=f"promo-{s['date']:%Y%m%d}", name=str(s["description"]), product_id=s["product_id"] or None,
                        product_name="全店", start=ymd(s["date"]), end=ymd(s["date"]), kind="全店大促", description=s["description"]))
    out.sort(key=lambda x: x["end"], reverse=True)
    return out


def _agg(f: pd.DataFrame) -> dict:
    n = len(f)
    s = f.sum(numeric_only=True)
    g, u, b = float(s["gmv"]), float(s["uv"]), float(s["buyers"])
    return dict(days=n, gmv=g, gmv_daily=g / n if n else None, uv_daily=u / n if n else None,
                cvr=b / u if u else None, aov=g / b if b else None,
                refund_rate=float(s["refund_amount"]) / g if g else None,
                margin=1 - float(s["cost"]) / g if g else None, units=float(s["units"]))


def _festival(camp: dict, ds) -> dict | None:
    text = f"{camp['name']}{camp.get('description', '')}{camp.get('product_name', '')}"
    for f in config.calendar().get("festivals", []):
        d = pd.Timestamp(f["date"])
        if any(k in text for k in f.get("keywords", [])) and d >= pd.Timestamp(camp["start"]):
            return dict(name=f["name"], date=ymd(d))
    return None


def build(ds, campaign_id: str, book: C.ChartBook | None = None):
    rp = ds.profile.get("report", {})
    book = book or C.ChartBook(rp.get("extra_chart_limit", 3))
    camps = {c["id"]: c for c in list_campaigns(ds)}
    if campaign_id not in camps:
        raise ValueError(f"找不到活动 {campaign_id}")
    camp = camps[campaign_id]
    pid = camp["product_id"]
    pids = [pid] if pid else ds.product_ids()
    s0, e0 = pd.Timestamp(camp["start"]), pd.Timestamp(camp["end"])
    pre = (s0 - rp.get("campaign_pre_days", 21) * DAY, s0 - DAY)
    lo = ds.dp["date"].min()
    pre = (max(pre[0], lo), pre[1])
    post_days = rp.get("campaign_post_days", 7)
    post = (e0 + DAY, min(e0 + post_days * DAY, ds.as_of))
    has_post = post[0] <= post[1]
    f = C.frame(ds, pids)
    promo = ds.promo_days()
    fpre = f[(f.index >= pre[0]) & (f.index <= pre[1]) & (~f.index.isin(promo))]
    fdur = f[(f.index >= s0) & (f.index <= e0)]
    fpost = f[(f.index >= post[0]) & (f.index <= post[1])] if has_post else f.iloc[0:0]
    A, D = _agg(fpre), _agg(fdur)
    P = _agg(fpost) if has_post else None
    subject = camp["product_name"]
    chapters, notes = [], []
    lab = dict(pre=f"活动前 {C.wlabel(pre)}", dur=f"活动期 {C.wlabel((s0, e0))}",
               post=f"活动后 {C.wlabel(post)}" if has_post else "活动后")

    # ---- 三、真实增量（先算，第一章要用）
    n = D["days"]
    base_total = A["gmv_daily"] * n
    total_inc = D["gmv"] - base_total
    attrib = None
    rows = pd.concat([ds.cdays(p) for p in pids]) if "channel" in ds.available else None
    camp_uv = None
    if rows is not None and not rows.empty:
        x = rows[(rows["date"] >= s0) & (rows["date"] <= e0) & (rows["channel"] == "campaign")]
        camp_uv = float(x["uv"].sum())
        if camp_uv > 0:
            attrib = camp_uv * D["cvr"] * D["aov"]
    natural = total_inc - attrib if attrib is not None else None
    main_share = rp.get("campaign_main_share", 0.5)
    attrib_share = attrib / total_inc if (attrib is not None and total_inc > 0) else None

    # ---- 一、结论
    margin_floor = ds.profile.get("constraints", {}).get("margin_floor", 0.25)
    lift = D["gmv_daily"] / A["gmv_daily"] - 1 if A["gmv_daily"] else None
    post_vs_pre = (P["gmv_daily"] / A["gmv_daily"] - 1) if (P and A["gmv_daily"]) else None
    post_vs_dur = (P["gmv_daily"] / D["gmv_daily"] - 1) if (P and D["gmv_daily"]) else None
    worth = total_inc > 0 and (D["margin"] is None or D["margin"] >= margin_floor) and (post_vs_pre is None or post_vs_pre >= 0)
    kpi = dict(type="kpi", title=f"{camp['name']}核心结果", items=[
        C.kpi_item("活动期日均 GMV", D["gmv_daily"], A["gmv_daily"], "money", note="对比活动前日均"),
        C.kpi_item("活动带来的增量", total_inc, None, "money", note=f"{n} 天合计，对比活动前水平"),
        C.kpi_item("会场流量贡献", attrib_share, None, "pct", note="增量中来自会场渠道的比例"),
        C.kpi_item("活动期毛利率", D["margin"], A["margin"], "pct", note=f"底线 {margin_floor:.0%}"),
        C.kpi_item("活动后日均 GMV", P["gmv_daily"] if P else None, A["gmv_daily"], "money", note="对比活动前日均"),
    ])
    kpi["items"] = [i for i in kpi["items"] if i["value"] is not None]
    kpi["summary"] = (f"活动期日均 {money(D['gmv_daily'])}，活动前 {money(A['gmv_daily'])}（{pct(lift)}）；{n} 天增量 {money(total_inc)}；"
                      + (f"会场贡献 {attrib_share:.0%}；" if attrib_share is not None else "")
                      + f"活动期毛利率 {rate(D['margin'])}；" + (f"活动后日均 {money(P['gmv_daily'])}（比活动前 {pct(post_vs_pre)}）" if P else ""))
    kpi["values"] = [v for v in (D["gmv_daily"], A["gmv_daily"], lift, total_inc, attrib_share, D["margin"], A["margin"],
                                 P["gmv_daily"] if P else None, post_vs_pre) if v is not None]
    k1 = book.add(kpi, chapter="summary")

    # ---- 二、活动全貌
    lo2 = pre[0]
    hi2 = post[1] if has_post else e0
    idx = pd.date_range(lo2, hi2)
    gs = f["gmv"].reindex(idx)
    tl = dict(type="trend", unit="money", title="活动前、中、后每天的 GMV", x=[C._mm(d) for d in idx],
              series=[dict(name="每日 GMV", data=[r(v, 0) for v in gs])],
              baseline=dict(value=round(A["gmv_daily"]), text="活动前日均"),
              bands=[{"from": C._mm(s0), "to": C._mm(e0), "text": camp["name"][:10]}])
    first7, last7 = fpre["gmv"].head(7).mean(), fpre["gmv"].tail(7).mean()
    pre_trend = (last7 / first7 - 1) if first7 else None
    tl["summary"] = (f"活动前 {C.wlabel(pre)} 日均 {money(A['gmv_daily'])}，其中前 7 天日均 {money(first7)}、后 7 天日均 {money(last7)}（{pct(pre_trend)}）；"
                     f"活动期 {C.wlabel((s0, e0))} 日均 {money(D['gmv_daily'])}，最高 {money(fdur['gmv'].max())}（{C._mm(fdur['gmv'].idxmax())}）；"
                     + (f"活动后 {C.wlabel(post)} 日均 {money(P['gmv_daily'])}" if P else "活动后暂无数据"))
    tl["values"] = [A["gmv_daily"], first7, last7, pre_trend, D["gmv_daily"], float(fdur["gmv"].max())] + ([P["gmv_daily"]] if P else []) + \
        [float(v) for v in gs.dropna()]
    k2 = book.add(tl, chapter="timeline")
    rising = pre_trend is not None and pre_trend > 0.10

    # ---- 三、增量图
    wf = dict(type="waterfall", unit="money", title="活动期多卖的钱从哪来",
              start=dict(label=f"活动前水平 × {n} 天", value=round(base_total)),
              end=dict(label="活动期实际", value=round(D["gmv"])),
              items=([dict(label="会场流量带来", value=round(attrib), tone="good"),
                      dict(label="自然增长及其他", value=round(natural), tone="muted")] if attrib is not None
                     else [dict(label="活动期增量", value=round(total_inc), tone="good")]))
    wf["summary"] = (f"活动前水平按 {n} 天算 {money(base_total)}，活动期实际 {money(D['gmv'])}，多卖 {money(total_inc)}。"
                     + (f"会场渠道 {n} 天共 {num(camp_uv)} 访客 × 活动期转化率 {rate(D['cvr'])} × 客单价 {D['aov']:.2f} 元 ≈ {money(attrib)}（占 {attrib_share:.0%}）；"
                        f"其余 {money(natural)} 视为自然增长及其他" if attrib is not None else "没有会场渠道数据，无法拆分增量来源"))
    wf["values"] = [base_total, D["gmv"], total_inc] + ([camp_uv, attrib, natural, attrib_share, D["cvr"], D["aov"]] if attrib is not None else [])
    k3 = book.add(wf, chapter="uplift")

    # ---- 四、增量从哪来（渠道）
    k4, chan = None, None
    if rows is not None and not rows.empty:
        wins = [pre, (s0, e0)] + ([post] if has_post else [])
        cats, *avgs = C.channel_avgs(ds, pids, wins)
        keep = [i for i in range(len(cats)) if any(av[i] for av in avgs)]
        cats, avgs = [cats[i] for i in keep], [[av[i] for i in keep] for av in avgs]
        names = ["活动前", "活动期", "活动后"][:len(wins)]
        gb = dict(type="grouped_bar", unit="num", title="各渠道日均访客：活动前 / 活动期 / 活动后", x=cats,
                  series=[dict(name=nm, data=[r(v, 0) for v in av], tone=t) for nm, av, t in zip(names, avgs, ["muted", "main", "warn"])])
        gb["summary"] = "；".join(f"{c}：" + " → ".join(num(av[i]) for av in avgs) for i, c in enumerate(cats)) + "（" + " / ".join(names) + "，日均）"
        gb["values"] = [v for av in avgs for v in av]
        k4 = book.add(gb, chapter="sources")
        others = [(c, a0, a1) for c, a0, a1 in zip(cats, avgs[0], avgs[1]) if c != config.channel_name("campaign") and a0]
        chan = dict(channels={c: [r(av[i], 0) for av in avgs] for i, c in enumerate(cats)},
                    other_channels_change=r(sum(a1 for _, _, a1 in others) / sum(a0 for _, a0, _ in others) - 1) if others else None)
    cvr_chg = (D["cvr"] / A["cvr"] - 1) if A["cvr"] else None
    uv_chg = (D["uv_daily"] / A["uv_daily"] - 1) if A["uv_daily"] else None
    aov_chg = (D["aov"] / A["aov"] - 1) if A["aov"] else None
    if cvr_chg is not None and abs(cvr_chg) <= 0.05 and (uv_chg or 0) > 0:
        style = "流量型活动（靠曝光，转化率没怎么变）"
    elif (cvr_chg or 0) > 0.05 and (uv_chg or 0) > 0.05:
        style = "流量和转化都提升（曝光加上优惠一起起作用）"
    elif (cvr_chg or 0) > 0.05:
        style = "转化型活动（转化率明显提升，多半靠优惠）"
    else:
        style = "流量和转化都没有明显提升"

    # ---- 五、代价
    p_pre = p_dur = None
    if pid and "price" in f.columns:
        p_pre, p_dur = float(fpre["price"].mean()), float(fdur["price"].mean())
    discount = (1 - p_dur / p_pre) if (p_pre and p_dur) else None
    refund_up = (P["refund_rate"] / A["refund_rate"] - 1) if (P and A["refund_rate"] and P["refund_rate"] is not None) else None
    k5 = book.add(dict(type="kpi", title="让利与毛利", items=[x for x in [
        C.kpi_item("活动期到手价", p_dur, p_pre, "price", note="对比活动前") if p_dur else C.kpi_item("活动期客单价", D["aov"], A["aov"], "price", note="对比活动前"),
        C.kpi_item("活动期毛利率", D["margin"], A["margin"], "pct", note=f"底线 {margin_floor:.0%}"),
        C.kpi_item("活动期退款率", D["refund_rate"], A["refund_rate"], "pct", better="down"),
        C.kpi_item("活动后退款率", P["refund_rate"] if P else None, A["refund_rate"], "pct", better="down"),
    ] if x["value"] is not None], summary=(f"到手价 {price(p_pre)} → {price(p_dur)}（让利 {pct(discount, False)}）；" if p_dur else "")
        + f"毛利率 {rate(A['margin'])} → {rate(D['margin'])}（底线 {margin_floor:.0%}）；退款率 活动前 {rate(A['refund_rate'])}、活动期 {rate(D['refund_rate'])}"
        + (f"、活动后 {rate(P['refund_rate'])}" if P else ""),
        values=[v for v in (p_pre, p_dur, discount, A["margin"], D["margin"], A["refund_rate"], D["refund_rate"],
                            P["refund_rate"] if P else None, refund_up) if v is not None]), chapter="cost")

    # ---- 六、对其他商品的影响
    k6, other = None, None
    if pid:
        others = [p for p in ds.product_ids() if p != pid]
        items = []
        for p in others:
            a0 = C.window_value(ds, [p], "gmv", pre)
            a1 = C.window_value(ds, [p], "gmv", (s0, e0))
            items.append(dict(pid=p, label=ds.product(p)["product_name"], sub=ds.product(p)["sub_category"],
                              prev=a0, cur=a1, change_pct=(a1 / a0 - 1) if a0 else None))
        items.sort(key=lambda x: x["change_pct"] or 0)
        ob = rp.get("campaign_other_band", 0.05)
        same_sub = ds.product(pid)["sub_category"]
        drop = [x for x in items if (x["change_pct"] or 0) < -ob and x["sub"] == same_sub]
        rise = [x for x in items if (x["change_pct"] or 0) > ob]
        within = [x for x in items if abs(x["change_pct"] or 0) <= ob]
        hb = dict(type="hbar", unit="pct", title="其他商品活动期日均 GMV 变化（对比活动前）",
                  items=[dict(label=x["label"], value=r(x["change_pct"]), tone="bad" if (x["change_pct"] or 0) < -ob else
                              ("good" if (x["change_pct"] or 0) > ob else "muted")) for x in items],
                  band=ob)
        hb["summary"] = f"{len(items)} 个其他商品中，{len(within)} 个变化在 ±{ob:.0%} 以内；" + \
            (f"同类（{same_sub}）明显下滑的：{'、'.join(x['label'] + ' ' + pct(x['change_pct']) for x in drop)}；" if drop else f"同类（{same_sub}）没有明显下滑；") + \
            (f"明显上涨的：{'、'.join(x['label'] + ' ' + pct(x['change_pct']) for x in rise)}" if rise else "没有明显上涨的") + \
            f"；变化范围 {pct(items[0]['change_pct'])} 至 {pct(items[-1]['change_pct'])}"
        hb["values"] = [x["change_pct"] for x in items if x["change_pct"] is not None]
        k6 = book.add(hb, chapter="others")
        other = dict(count=len(items), within_band=len(within), band=ob, same_sub=same_sub,
                     cannibalized=[dict(product=x["label"], change_pct=r(x["change_pct"])) for x in drop],
                     lifted=[dict(product=x["label"], change_pct=r(x["change_pct"])) for x in rise],
                     min=r(items[0]["change_pct"]), max=r(items[-1]["change_pct"]),
                     verdict="疑似挤占同类商品" if drop else ("带动了其他商品" if rise else "没有挤占，也没有带动"))
    else:
        notes.append("全店大促没有「未参加活动的商品」可以对照，不分析挤占与带动")

    # ---- 七、库存
    k7, stock = None, None
    fest = _festival(camp, ds)
    if pid and "stock" in ds.available:
        v = ds.vdays(pid)
        k7 = book.add(C.draw(ds, dict(type="trend", products=[pid], metrics=["stock"], start=ymd(lo2), end=ymd(hi2),
                                      band_start=ymd(s0), band_end=ymd(e0), band_text="活动期", title="各规格库存")), chapter="stock")
        vd = v[(v["date"] >= s0) & (v["date"] <= e0)]
        so_days = int(vd[vd["stock_units"] <= 0]["date"].nunique())
        last = v[v["date"] == ds.as_of]
        remain = float(last["stock_units"].sum())
        daily = float(v[v["date"] > ds.as_of - 7 * DAY]["units"].sum()) / 7
        dos = remain / daily if daily else None
        stock = dict(stockout_days=so_days, remaining=round(remain), daily_units_7d=round(daily), days_of_supply=r(dos, 1),
                     min_stock_during=round(float(vd.groupby("date")["stock_units"].sum().min())) if not vd.empty else None)
        if fest and pd.Timestamp(fest["date"]) > ds.as_of:
            dleft = (pd.Timestamp(fest["date"]) - ds.as_of).days
            sell = daily * dleft
            stock.update(festival=fest["name"], festival_date=fest["date"], days_to_festival=dleft,
                         expected_sales_before=round(sell), leftover_after=round(max(remain - sell, 0)))
    # ---- 八、活动后
    post_verdict = None
    if P:
        post_verdict = "透支（低于活动前）" if post_vs_pre < 0 else "没有透支（仍高于活动前）"
        if P["days"] < post_days:
            notes.append(f"活动后只有 {P['days']} 天数据，观察期还不满 {post_days} 天")
    else:
        notes.append("活动刚结束，还没有活动后的数据")
    fest_gap = None
    if fest:
        fest_gap = (pd.Timestamp(fest["date"]) - e0).days

    # ---- 九、经验
    keep, improve, stop, acts = [], [], [], []
    if attrib_share is not None and attrib_share >= main_share:
        keep.append(f"会场流量是增量主力（约占 {attrib_share:.0%}），同类会场值得继续报名")
    if discount is not None and discount < 0.01 and D["margin"] and D["margin"] >= margin_floor:
        keep.append(f"没有降价（到手价保持 {price(p_dur)}），毛利率 {rate(D['margin'])}，不靠让利也拿到了会场流量")
    if other and not other["cannibalized"]:
        keep.append("没有挤占同类商品，可以放心单独报名")
    if fest and fest_gap is not None and fest_gap > rp.get("festival_cover_days", 3):
        improve.append(f"会场在{fest['name']}前 {fest_gap} 天就结束了，节前送礼高峰没覆盖到，下次让会场覆盖到节前 {rp.get('festival_cover_days', 3)} 天左右（需确认平台会场档期）")
    if stock and stock["stockout_days"] > 0:
        improve.append(f"活动期断货 {stock['stockout_days']} 天，下次按活动期销量提前备货")
    if stock and stock.get("leftover_after", 0) > 0:
        improve.append(f"节后预计还剩约 {num(stock['leftover_after'])} 件（估算），备货要结合节日日期，提前定节后清库存方案")
        acts.append(dict(product_id=pid, product_name=subject, kind="节后库存", title=f"制定{fest['name']}后{subject}的库存处理方案",
                         why=f"现有库存约 {num(stock['remaining'])} 件，按近 7 天销量{fest['name']}前能卖约 {num(stock['expected_sales_before'])} 件，节后预计剩约 {num(stock['leftover_after'])} 件（估算）",
                         owners=["供应链", "我"], can_todo=True,
                         draft=dict(name=f"{fest['name']}后{subject}库存处理", steps=[
                             dict(text=f"盘点{subject}剩余库存与在途（目前约 {num(stock['remaining'])} 件），确认可退换货的数量和期限，反馈给我", by="供应链"),
                             dict(text=f"{fest['name']}前定好节后清库存方案（如组合装、会员价），节后第一天上线", by="我")],
                             track_metric="units", track_days=14,
                             note=f"节后预计剩约 {num(stock['leftover_after'])} 件（估算，按近 7 天日均 {num(stock['daily_units_7d'])} 件、距{fest['name']} {stock['days_to_festival']} 天）")))
    if D["margin"] is not None and D["margin"] < margin_floor:
        stop.append(f"活动期毛利率 {rate(D['margin'])} 低于底线 {margin_floor:.0%}，赚了销售额亏了钱，这种力度的让利不要再做")
    if other and other["cannibalized"]:
        stop.append("活动挤占了同类商品：" + "、".join(f"{x['product']} {pct(x['change_pct'])}" for x in other["cannibalized"]))
    if post_vs_pre is not None and post_vs_pre < 0:
        improve.append(f"活动后日均低于活动前（{pct(post_vs_pre)}），有透支，下次控制活动力度或拉长节奏")
    elif post_vs_pre is not None:
        keep.append(f"活动后没有透支：活动后日均比活动前 {pct(post_vs_pre)}")
    if aov_chg is not None and aov_chg < -0.05:
        improve.append(f"活动期客单价 {pct(aov_chg)}，折扣拉低了客单价，下次关注凑单、满减门槛的设计")
    if (cvr_chg or 0) > 0.05 and D["margin"] is not None and D["margin"] >= margin_floor:
        keep.append(f"转化率 {rate(A['cvr'])} → {rate(D['cvr'])}，毛利率仍有 {rate(D['margin'])}，优惠力度在可承受范围内")
    if rising:
        improve.append(f"活动前已经在自然上涨（后 7 天比前 7 天 {pct(pre_trend)}），评估活动效果时不能把增长全算给会场")
    if not stop:
        stop.append("暂无需要停止的做法")

    notes += ["没有推广花费、活动报名费（坑位费）数据，「值不值」只按毛利判断，算不出投产比和净利润",
              "没有用户数据，不分析活动拉来多少新客、有没有复购",
              "增量拆分方法：会场渠道访客 × 活动期转化率 × 客单价 = 会场带来的成交，其余视为自然增长及其他渠道的溢出"]
    period = f"{camp['start']} 至 {camp['end']}"
    chapters = [
        chapter(1, "summary", "活动结论", "值不值、真实增量多少、最重要的一条经验", dict(
            campaign=camp["name"], subject=subject, period=period, worth=worth, gmv_daily_pre=round(A["gmv_daily"]),
            gmv_daily_during=round(D["gmv_daily"]), lift=r(lift), multiple=r(1 + lift, 1) if lift is not None else None, total_increment=round(total_inc),
            attrib_share=r(attrib_share), margin_during=r(D["margin"]), post_vs_pre=r(post_vs_pre),
            key_lesson=(improve or keep or [None])[0]), k1,
            judgment=f"判断标准：有增量、毛利率不低于底线 {margin_floor:.0%}、活动后没有掉到活动前以下 = 值得"),
        chapter(2, "timeline", "活动全貌", "活动前、中、后生意怎么走的", dict(
            pre=dict(window=C.wlabel(pre), gmv_daily=round(A["gmv_daily"]), first7_daily=round(first7), last7_daily=round(last7),
                     trend=r(pre_trend)),
            during=dict(window=C.wlabel((s0, e0)), days=n, gmv_daily=round(D["gmv_daily"]), peak=round(float(fdur["gmv"].max())),
                        peak_date=C._mm(fdur["gmv"].idxmax())),
            post=dict(window=C.wlabel(post), days=P["days"], gmv_daily=round(P["gmv_daily"])) if P else None,
            pre_rising=rising), k2,
            judgment="活动前后 7 天比前 7 天涨 10% 以上 = 活动前已在上升，增量里有自然增长" if rising else "活动前走势平稳"),
        chapter(3, "uplift", "真实增量", "多卖的钱有多少是活动带来的", dict(
            baseline_total=round(base_total), actual=round(D["gmv"]), increment=round(total_inc),
            campaign_uv=round(camp_uv) if camp_uv else None, cvr_during=r(D["cvr"]), aov_during=r(D["aov"], 2),
            from_campaign=round(attrib) if attrib is not None else None, natural=round(natural) if natural is not None else None,
            share=r(attrib_share), main_share_threshold=main_share), k3,
            judgment=(f"会场带来占 {attrib_share:.0%}，" + ("≥" if attrib_share >= main_share else "<") + f" {main_share:.0%}，"
                      + ("活动是增长主力" if attrib_share >= main_share else "多数增长本来就会发生")) if attrib_share is not None else "无法拆分"),
        chapter(4, "sources", "增量从哪来", "靠流量还是靠转化、哪个渠道涨了", dict(
            uv_daily=dict(pre=round(A["uv_daily"]), during=round(D["uv_daily"]), change=r(uv_chg)),
            cvr=dict(pre=r(A["cvr"]), during=r(D["cvr"]), change=r(cvr_chg)),
            aov=dict(pre=r(A["aov"], 2), during=r(D["aov"], 2), change=r(aov_chg)), channels=chan, style=style), k4,
            judgment=f"{style}（判断标准：转化率变化在 ±5% 以内 = 流量型）"),
        chapter(5, "cost", "让利与毛利", "让了多少利、毛利还剩多少", dict(
            price_pre=r(p_pre, 2), price_during=r(p_dur, 2), discount=r(discount), margin_pre=r(A["margin"]), margin_during=r(D["margin"]),
            margin_floor=margin_floor, refund_pre=r(A["refund_rate"]), refund_during=r(D["refund_rate"]),
            refund_post=r(P["refund_rate"]) if P else None, refund_post_change=r(refund_up)), k5,
            judgment=("毛利率不低于底线" if (D["margin"] or 0) >= margin_floor else "毛利率低于底线：赚了销售额亏了钱")
            + ("；活动后退款率明显升高，冲动消费偏多" if (refund_up or 0) > 0.3 else "；退款率正常")),
        chapter(6, "others", "对其他商品的影响", "有没有挤占或带动店里其他商品", other or {}, k6,
                judgment=(f"{other['verdict']}（判断标准：同类商品日均下滑超过 {other['band']:.0%} = 疑似挤占）" if other else None),
                show=bool(other)),
        chapter(7, "stock", "库存", "备货够不够、剩下的货怎么办", stock or {}, k7,
                judgment=(("活动期没有断货" if stock["stockout_days"] == 0 else f"活动期断货 {stock['stockout_days']} 天，备货不足")
                          + (f"；距{stock['festival']}还有 {stock['days_to_festival']} 天，节后预计剩约 {num(stock['leftover_after'])} 件（估算），有滞销风险"
                             if stock.get("leftover_after") else "")) if stock else None,
                show=bool(stock)),
        chapter(8, "post", "活动后表现", "有没有透支", dict(
            post_daily=round(P["gmv_daily"]) if P else None, pre_daily=round(A["gmv_daily"]), during_daily=round(D["gmv_daily"]),
            vs_pre=r(post_vs_pre), vs_during=r(post_vs_dur), days=P["days"] if P else 0, need_days=post_days,
            festival=fest, days_before_festival_when_ended=fest_gap), None,
            judgment=post_verdict or "暂无活动后数据"),
        chapter(9, "lessons", "经验清单", "下次保持什么、改什么、不再做什么", dict(keep=keep, improve=improve, stop=stop), None),
        chapter(10, "notes", "数据说明", "哪些没有分析、为什么", dict(items=notes), None),
    ]
    pack = dict(scene="campaign", title=f"{camp['name']}活动复盘（{md(s0) if s0 == e0 else md(s0) + '–' + md(e0)}）", period=period, subject=subject,
                dataset=ds.name, as_of=ymd(ds.as_of), campaign=camp, chapters=chapters, actions=acts, data_notes=notes,
                params=dict(campaign_id=campaign_id))
    return pack, book


def render(pack: dict, book: C.ChartBook) -> str:
    ch = {c["key"]: c for c in pack["chapters"]}
    L = [f"# {pack['title']}", ""]

    def head(c):
        L.extend([f"## {c['heading']}", ""])

    c = ch["summary"]
    f = c["facts"]
    head(c)
    s = (f"**{'值得' if f['worth'] else '不太值得'}。活动期日均 GMV {money(f['gmv_daily_during'])}，是活动前（{money(f['gmv_daily_pre'])}）的 "
         f"{f['multiple']:.1f} 倍，{ch['timeline']['facts']['during']['days']} 天共多卖 {money(f['total_increment'])}")
    s += (f"，其中约 {f['attrib_share']:.0%} 来自会场流量" if f["attrib_share"] is not None else "") + "。"
    if f["key_lesson"]:
        s += f"最重要的一条经验：{f['key_lesson']}。"
    L += [s + "**", "", f"[图表:{c['chart']}]", "", f"注：{c['judgment']}。", ""]

    c = ch["timeline"]
    f = c["facts"]
    head(c)
    L += [f"**活动期日均 {money(f['during']['gmv_daily'])}，最高一天 {money(f['during']['peak'])}（{f['during']['peak_date']}）"
          + (f"；活动后日均回落到 {money(f['post']['gmv_daily'])}" if f["post"] else "") + "。**", "", f"[图表:{c['chart']}]", ""]
    if f["pre_rising"]:
        L.append(f"活动前 {f['pre']['window']} 已经在上涨：后 7 天日均 {money(f['pre']['last7_daily'])}，比前 7 天（{money(f['pre']['first7_daily'])}）{pct(f['pre']['trend'])}，"
                 "所以活动期的增长里有一部分本来就会发生，具体拆分见下一章。")
    else:
        L.append(f"活动前 {f['pre']['window']} 走势平稳，日均 {money(f['pre']['gmv_daily'])}。")
    L.append("")

    c = ch["uplift"]
    f = c["facts"]
    head(c)
    if f["from_campaign"] is not None:
        L += [f"**活动期多卖 {money(f['increment'])}，其中约 {money(f['from_campaign'])}（{f['share']:.0%}）来自会场流量，"
              f"其余 {money(f['natural'])} 是自然增长及其他渠道。**", "", f"[图表:{c['chart']}]", "",
              f"算法：会场渠道共 {num(f['campaign_uv'])} 访客 × 活动期转化率 {rate(f['cvr_during'])} × 客单价 {f['aov_during']:.2f} 元。"
              f"{c['judgment']}。", ""]
    else:
        L += [f"**活动期多卖 {money(f['increment'])}。**", "", f"[图表:{c['chart']}]", "", "没有会场渠道数据，无法拆分增量来源。", ""]

    c = ch["sources"]
    f = c["facts"]
    head(c)
    L += [f"**{f['style']}：日均访客 {num(f['uv_daily']['pre'])} → {num(f['uv_daily']['during'])}（{pct(f['uv_daily']['change'])}），"
          f"转化率 {rate(f['cvr']['pre'])} → {rate(f['cvr']['during'])}，客单价 {f['aov']['pre']:.2f} → {f['aov']['during']:.2f} 元。**", ""]
    if c["chart"]:
        L += [f"[图表:{c['chart']}]", ""]
        chs = f["channels"]["channels"]
        camp_name = config.channel_name("campaign")
        if camp_name in chs and chs[camp_name][1]:
            L.append(f"会场渠道活动期每天带来约 {num(chs[camp_name][1])} 访客；其他渠道活动期合计 {pct(f['channels']['other_channels_change'])}，"
                     + ("说明同期有明显的自然增长或会场溢出。" if (f["channels"]["other_channels_change"] or 0) > 0.1 else "基本没变。"))
            L.append("")

    c = ch["cost"]
    f = c["facts"]
    head(c)
    if f["price_during"] and (f["discount"] or 0) < 0.005:
        ptxt = f"没有降价，到手价保持 {price(f['price_during'])}"
    elif f["price_during"]:
        ptxt = f"到手价 {price(f['price_pre'])} → {price(f['price_during'])}（让利 {pct(f['discount'], False)}）"
    else:
        ptxt = ""
    L += [f"**{ptxt}{'，' if ptxt else ''}活动期毛利率 {rate(f['margin_during'])}（底线 {f['margin_floor']:.0%}）。**", "", f"[图表:{c['chart']}]", "",
          f"退款率：活动前 {rate(f['refund_pre'])}，活动期 {rate(f['refund_during'])}" + (f"，活动后 {rate(f['refund_post'])}" if f["refund_post"] is not None else "")
          + f"。{c['judgment']}。", ""]

    c = ch["others"]
    if c["show"]:
        f = c["facts"]
        head(c)
        L += [f"**{f['verdict']}：{f['count']} 个其他商品中 {f['within_band']} 个日均变化在 ±{f['band']:.0%} 以内（范围 {pct(f['min'])} 至 {pct(f['max'])}）。**", "",
              f"[图表:{c['chart']}]", ""]
        if f["cannibalized"]:
            L.append("同类明显下滑：" + "、".join(f"{x['product']} {pct(x['change_pct'])}" for x in f["cannibalized"]) + "。")
        L += [f"注：{c['judgment']}。", ""]

    c = ch["stock"]
    if c["show"]:
        f = c["facts"]
        head(c)
        L += [f"**{c['judgment']}。**", "", f"[图表:{c['chart']}]", "",
              f"目前剩余约 {num(f['remaining'])} 件，按近 7 天日均 {num(f['daily_units_7d'])} 件能卖 {f['days_of_supply']} 天。"
              + (f"距{f['festival']}（{f['festival_date'][5:]}）还有 {f['days_to_festival']} 天，节前预计能卖约 {num(f['expected_sales_before'])} 件，"
                 f"节后预计剩约 {num(f['leftover_after'])} 件（估算）。节日礼盒过节后需求会快速下降，要提前定处理方案。" if f.get("festival") else ""), ""]

    c = ch["post"]
    f = c["facts"]
    head(c)
    if f["post_daily"] is not None:
        L += [f"**{c['judgment']}：活动后 {f['days']} 天日均 {money(f['post_daily'])}，比活动期 {pct(f['vs_during'])}，比活动前 {pct(f['vs_pre'])}。**", "",
              f"走势见第二章的图。" + (f"活动后数据只有 {f['days']} 天，观察期还不满 {f['need_days']} 天，结论需要再看几天。" if f["days"] < f["need_days"] else ""), ""]
    else:
        L += ["**活动刚结束，还没有活动后的数据。**", ""]

    c = ch["lessons"]
    f = c["facts"]
    head(c)
    L += ["**保持**", ""] + [f"- {x}" for x in f["keep"] or ["—"]] + ["", "**改进**", ""] + [f"- {x}" for x in f["improve"] or ["—"]] + \
        ["", "**不再做**", ""] + [f"- {x}" for x in f["stop"]] + [""]
    if pack["actions"]:
        L += ["需要跟进：" + "；".join(f"{a['title']}（{'、'.join(a['owners'])}）" for a in pack["actions"]) + "。", ""]

    c = ch["notes"]
    head(c)
    L += [f"- {x}" for x in c["facts"]["items"]]
    return "\n".join(L)
