"""按归因 SOP 执行的确定性诊断。

用途：
1. 大模型不可用时的「规则生成」降级；
2. 证据包模式（evidence_pack）下给大模型的完整证据；
3. 开发与测试时的模拟模型脚本。
输出结构与大模型诊断一致（需求文档 10.4）。
"""
from __future__ import annotations

from . import config, tools
from .actions import completeness
from .loader import Dataset

FACTOR_CAUSE_ORDER = ["stockout", "price_disadvantage", "reputation_drop"]


def _pct(x):
    return "—" if x is None else f"{x:+.1%}"


def share_text(f) -> str:
    sh = f.get("share") or 0
    if sh > 1.0:
        return f"{f['name']} {_pct(f['change_pct'])}，是 GMV 下降的全部来源（其他因素为正向）"
    return f"{f['name']} {_pct(f['change_pct'])}，贡献了 GMV 变化的 {sh:.0%}"


def run(ds: Dataset, pid: str, card: dict | None = None, tiers: dict | None = None, window: int = 7):
    """返回 (steps, result)。steps 为工具调用记录：[{tool, args, result, summary}]"""
    steps = []

    def call(name, **args):
        res = tools.call(ds, name, dict(product_id=pid, **args), card=card, tiers=tiers)
        steps.append(dict(tool=name, args=dict(product_id=pid, **args), result=res, summary=summarize(name, res)))
        return res

    ctx = call("get_context")
    dec = call("decompose_gmv", window=window)
    delta = dec["gmv_change"]
    causes = []   # (cause, confidence, evidence list, weight)
    limits = []
    if ctx["missing_data"]:
        limits.append(f"数据中缺少{'、'.join(ctx['missing_data'])}，相关分析未进行")

    rules = set((card or {}).get("rules", []))
    drivers = []
    t = (tiers or {}).get(pid) or {}
    coef = ctx.get("alert_threshold_coef", 1.0)
    decline_th = ds.profile["gmv_drop_yellow"] * coef
    chg = dec["gmv_change_pct"] or 0.0
    notes = []
    significant = bool(rules & {"R01", "R02", "R03"}) or (chg <= -decline_th)
    if delta < 0 and significant:
        drivers = [f for f in dec["factors"] if f["amount"] < 0 and (f["share"] or 0) >= 0.3]
    elif delta < 0:
        notes.append(f"近 7 日 GMV {_pct(chg)}，未超过预警阈值 {decline_th:.0%}"
                     + (f"（{ctx['lifecycle']}阈值已放宽 {coef:g} 倍）" if coef > 1 else "") + "，属于正常波动，不做下滑归因")
    drivers.sort(key=lambda f: f["amount"])

    ev = None
    for f in drivers:
        if f["factor"] == "cvr":
            bv = call("breakdown_variants", window=window) if "variant" in ds.available else None
            cf = call("check_factors", window=window)
            ev = ev or call("get_events")
            fac = cf["factors"]
            st = fac.get("stock", {})
            if st.get("checked") and st.get("stockout_variants"):
                v = st["stockout_variants"][0]
                if (v.get("baseline_share") or 0) >= ds.profile["stockout_share_min"] and (v.get("stockout_days") or 0) > 0:
                    causes.append(("stockout", "强", [
                        f"{v['name']} 在 {'、'.join(v['dates'] or [])} 断货 {v['stockout_days']} 天，该规格平时占销量 {v['baseline_share']:.0%}",
                        share_text(f)], abs(f["amount"])))
            pr = fac.get("price", {})
            if pr.get("checked") and pr.get("abnormal"):
                strong = bool(pr.get("competitor_events"))
                evid = [f"到手价 {pr['price']:g} 元，竞品 {pr['comp_price']:g} 元，价格指数 {pr['price_index']:.2f}"]
                for e in pr.get("competitor_events", []):
                    evid.append(f"{e['date'][5:]} {e['description']}")
                evid.append(share_text(f))
                causes.append(("price_disadvantage", "强" if strong else "中", evid, abs(f["amount"]) * 0.99))
            rt = fac.get("rating", {})
            if rt.get("checked") and rt.get("abnormal"):
                strong = bool(rt.get("review_events"))
                evid = [f"评分 {rt['rating_base_28d']:.2f} → {rt['rating_now']:.2f}"]
                if rt.get("refund_rate_base"):
                    evid.append(f"近 7 日退款率 {rt['refund_rate_7d']:.1%}，基线 {rt['refund_rate_base']:.1%}")
                for e in rt.get("review_events", []):
                    evid.append(f"{e['date'][5:]} {e['description']}")
                causes.append(("reputation_drop", "强" if strong else "中", evid, abs(f["amount"]) * 0.98))
            for k in ("stock", "price", "rating", "campaign"):
                if k in fac and not fac[k].get("checked"):
                    limits.append(f"未检查{fac[k]['name']}：{fac[k].get('note')}")
        elif f["factor"] == "uv":
            bc = call("breakdown_channels", window=window)
            ev = ev or call("get_events")
            if not bc.get("available"):
                limits.append("没有渠道数据，访客下滑无法定位到渠道")
                continue
            worst = bc["channels"][0]
            evs = ev["events"]
            if worst["change_pct"] is not None and worst["change_pct"] > -0.10:
                limits.append("访客下降分散在各渠道，单个渠道变化不超过 10%，未定位到明确原因")
                continue
            base_ev = [share_text(f),
                       f"{worst['name']}渠道日均访客 {worst['daily_prev']:,.0f} → {worst['daily_cur']:,.0f}（{_pct(worst['change_pct'])}），是下滑最多的渠道"]
            if worst["channel"] == "paid":
                e = [x for x in evs if x["type"] == "ad_budget_change"]
                causes.append(("paid_traffic_drop", "强" if e else "中",
                               base_ev + [f"{x['date'][5:]} {x['description']}" for x in e], abs(f["amount"])))
            elif worst["channel"] == "campaign":
                e = [x for x in evs if x["type"] in ("campaign_end",)]
                causes.append(("campaign_end", "强" if e else "中",
                               base_ev + [f"{x['date'][5:]} {x['description']}" for x in e], abs(f["amount"])))
            elif worst["channel"] == "search":
                causes.append(("search_traffic_drop", "中", base_ev + ["同期没有找到对应事件，需人工核对搜索排名"], abs(f["amount"])))
            else:
                limits.append(f"{worst['name']}渠道访客下滑，动作库暂无对应动作")
        elif f["factor"] == "aov":
            ad = dec["aov_detail"]
            causes.append(("aov_drop", "中", [f"客单价 {_pct(f['change_pct'])}；件单价 {ad['unit_price_prev']} → {ad['unit_price_cur']} 元，"
                                              f"人均件数 {ad['units_per_buyer_prev']} → {ad['units_per_buyer_cur']}"], abs(f["amount"])))

    # 预警直接给出的问题（不依赖 GMV 下滑）
    if "R05" in rules and not any(c[0] == "stockout" for c in causes):
        bv = next((s["result"] for s in steps if s["tool"] == "breakdown_variants"), None) or call("breakdown_variants", window=window)
        low = [v for v in bv.get("variants", []) if v.get("days_of_supply") is not None and v["stock_now"] > 0
               and v["days_of_supply"] < ds.profile["replenish_lead_days"]]
        if low:
            v = min(low, key=lambda x: x["days_of_supply"])
            causes.append(("stockout_risk", "强", [f"{v['name']} 库存 {v['stock_now']:,} 件，按近 7 日销量只够卖 {v['days_of_supply']} 天，"
                                                   f"低于补货周期 {ds.profile['replenish_lead_days']} 天"], 0.5))
    if "R08" in rules:
        bc = next((s["result"] for s in steps if s["tool"] == "breakdown_channels"), None) or call("breakdown_channels", window=window)
        ev = ev or call("get_events")
        best = max(bc.get("channels", []), key=lambda c: c["change"]) if bc.get("available") else None
        evid = []
        if best:
            evid.append(f"{best['name']}渠道日均访客 {best['daily_prev']:,.0f} → {best['daily_cur']:,.0f}（{_pct(best['change_pct'])}）")
        evid += [f"{x['date'][5:]} {x['description']}" for x in ev["events"] if x["type"] in ("external_content", "campaign_start")]
        causes.insert(0, ("growth_opportunity", "强", evid, 1.0))

    # 去重
    seen, uniq = set(), []
    for c in causes:
        if c[0] not in seen:
            uniq.append(c)
            seen.add(c[0])
    causes = uniq

    plans = []
    used_ids, stock_targets = set(), set()
    for cause, conf, evid, w in causes:
        pa = call("plan_actions", cause=cause)
        cands = [p for p in pa["candidates"] if not completeness(p)]
        fresh = []
        for p in cands:
            if p["action_id"] in used_ids:
                continue
            if p["action_id"] in ("replenish", "boost_stock"):
                if p["target"] in stock_targets:
                    continue
                stock_targets.add(p["target"])
            fresh.append(p)
        keep = fresh[:3] if cause in ("stockout", "price_disadvantage") else fresh[:2]
        for p in keep:
            used_ids.add(p["action_id"])
            p["rationale"] = rationale(p, conf)
            plans.append(p)
        if not cands:
            limits.append(f"「{config.cause_name(cause)}」没有可执行的方案：" +
                          "；".join(f"{x['name']}（{x['reason']}）" for x in pa["excluded"][:3]))

    if not causes and not notes:
        limits.append("未找到明确原因，建议补充搜索排名、竞品动态等数据后再判断")
    limits.append("优惠券、赠品等刺激类方案不预测销量提升，只给出保本线")

    result = dict(
        summary=summary_text(ctx, dec, causes, notes if (notes or drivers) else ["no_decline"], card),
        severity=(card or {}).get("severity", "yellow" if causes else "blue"),
        contribution=[dict(factor=f["factor"], name=f["name"], change_pct=f["change_pct"], amount=f["amount"],
                           share=f["share"]) for f in dec["factors"]],
        gmv=dict(prev=dec["gmv_prev"], cur=dec["gmv_cur"], change=dec["gmv_change"], change_pct=dec["gmv_change_pct"],
                 cur_window=dec["cur_window"], prev_window=dec["prev_window"]),
        root_causes=[dict(cause=c, cause_name=config.cause_name(c), confidence=conf, evidence=[dict(text=t) for t in evid])
                     for c, conf, evid, _ in causes],
        plans=plans,
        limitations=limits,
        notes=notes,
    )
    return steps, result


def rationale(p: dict, conf: str) -> str:
    aid = p["action_id"]
    if aid in ("store_coupon",):
        return "优惠券可以随时撤回，能先测试价格敏感度，不会像直接降价那样难以恢复原价。"
    if aid == "price_match":
        return "价格劣势已持续一周以上，竞品可能是长期调价，直接跟价更彻底，但降价后难以恢复。"
    if aid == "gift":
        return "不改变到手价，保护价格体系，同时用赠品弥补价差。"
    if aid == "bundle_discount":
        return "只对多买的顾客让利，毛利损失可控。"
    if aid in ("replenish",):
        return "断货是转化率下降的直接原因，库存恢复是根本解决办法。"
    if aid == "redirect_variant":
        return "补货到位之前，先把想买断货规格的顾客引导到有货规格，减少流失。"
    if aid == "pause_variant_ads":
        return "断货期间继续为该规格投放会浪费推广费。"
    if aid == "review_ad_budget":
        return "付费访客减少与预算调整时间吻合，需要先确认这是不是计划内的调整。"
    if aid == "review_handling":
        return "差评集中在同一时间出现，需要先止住差评源头，再挽回口碑。"
    if aid == "boost_stock":
        return "热度带来的销量上涨会很快消耗库存，要先保证不断货再考虑放量。"
    if aid == "mark_known":
        return "活动结束后回落属于预期内现象，标记后避免重复提醒。"
    if aid == "campaign_signup":
        return "如果有合适的下一档活动，可以延续流量。"
    return "动作库中与该原因匹配的方案。"


def summary_text(ctx, dec, causes, notes=None, card=None) -> str:
    name = ctx["product_name"]
    chg = dec["gmv_change_pct"]
    head = f"{name}近 7 日 GMV {_pct(chg)}"
    notes = [n for n in (notes or []) if n != "no_decline"] if not causes else notes
    if not causes:
        if notes:
            pre = "当前未触发预警，" if card is None else ""
            return f"{head}，{pre}变化在正常波动范围内（{ctx['lifecycle']}预警阈值已按生命周期调整）。" if ctx.get("alert_threshold_coef", 1) > 1 \
                else f"{head}，{pre}变化在正常波动范围内。"
        return head + "，未找到明确原因。"
    main = causes[0]
    cname = config.cause_name(main[0])
    if notes and main[0] != "growth_opportunity":
        return f"{head}，销售变化在正常范围内；需注意" + "、".join(config.cause_name(c[0]) for c in causes) + "。"
    if main[0] == "growth_opportunity":
        src = main[2][0] if main[2] else ""
        txt = f"{head}，出现增长机会：{src}"
        if len(causes) > 1:
            txt += "；需注意" + "、".join(config.cause_name(c[0]) for c in causes[1:])
        return txt + "。"
    drivers = [f for f in dec["factors"] if f["amount"] < 0 and (f["share"] or 0) >= 0.3]
    if chg is not None and chg < 0 and drivers:
        d = min(drivers, key=lambda f: f["amount"])
        if (d["share"] or 0) > 1:
            head += f"，下降全部来自{d['name']}"
        else:
            head += f"，约 {d['share']:.0%} 来自{d['name']}下降"
    tail = f"，主要原因是{cname}"
    if len(causes) > 1:
        tail += "，同时存在" + "、".join(config.cause_name(c[0]) for c in causes[1:])
    return head + tail + "。"


def summarize(name: str, res: dict) -> str:
    """把工具结果压缩成一句话，供页面展示分析过程。"""
    try:
        if name == "get_context":
            return f"读取商品概况：{res['tier']} · {res['lifecycle']}，健康度 {res['health']['score']}"
        if name == "decompose_gmv":
            fs = sorted(res["factors"], key=lambda f: f["amount"])
            top = fs[0] if res["gmv_change"] < 0 else fs[-1]
            if (top["share"] or 0) > 1:
                return f"拆解 GMV（{_pct(res['gmv_change_pct'])}）：变化全部来自{top['name']}"
            return f"拆解 GMV（{_pct(res['gmv_change_pct'])}）：{top['name']}贡献 {top['share']:.0%}"
        if name == "breakdown_channels":
            if not res.get("available"):
                return res.get("note", "")
            w = max(res["channels"], key=lambda c: abs(c["change"]))
            return f"按渠道下钻：{w['name']}变化最大（{_pct(w['change_pct'])}）"
        if name == "breakdown_variants":
            if not res.get("available"):
                return res.get("note", "")
            so = [v for v in res["variants"] if v.get("stockout_days")]
            if so:
                return f"按规格下钻：{so[0]['name']} 断货 {so[0]['stockout_days']} 天"
            low = [v for v in res["variants"] if v.get("days_of_supply") is not None]
            if low:
                v = min(low, key=lambda x: x["days_of_supply"])
                return f"按规格下钻：库存最紧的是 {v['name']}，可售 {v['days_of_supply']} 天"
            return "按规格下钻：各规格正常"
        if name == "check_factors":
            ab = [v["name"] for v in res["factors"].values() if v.get("abnormal")]
            return "检查影响因素：" + ("异常 " + "、".join(ab) if ab else "未发现异常")
        if name == "get_events":
            n = len(res["events"])
            return f"查询事件：{n} 条" + (f"，在途补货 {len(res['in_transit'])} 批" if res.get("in_transit") else "")
        if name == "plan_actions":
            return f"生成「{res['cause_name']}」方案：{len(res['candidates'])} 个候选，排除 {len(res['excluded'])} 个"
    except Exception:  # noqa: BLE001
        pass
    return name
