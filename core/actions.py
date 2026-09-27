"""动作方案：从动作库选动作 → 用真实数据算参数 → 检查经营约束 → 测算。

规则见 methods/action_library.yaml 与需求文档 9.8。
- 所需数据缺失、参数算不出或约束不通过的动作不进入候选，并记录原因。
- 不预测刺激类动作（优惠券、赠品、跟价）的销量提升，只给保本线。
"""
from __future__ import annotations

import math

import pandas as pd

from . import config
from .decompose import breakdown_channels, breakdown_variants, variant_baseline_share
from .loader import Dataset
from .metrics import DAY, agg, r, slice_, windows

METRIC_NAMES = {"cvr": "支付转化率", "uv": "访客数", "uv_paid": "付费访客", "uv_search": "搜索访客", "aov": "客单价",
                "gmv": "GMV", "rating": "评分", "variant_units": "规格销量", "days_of_supply": "库存可售天数",
                "units": "销量"}


class Skip(Exception):
    """动作不适用或约束不通过。"""


def _lib() -> dict:
    return {a["id"]: a for a in config.action_library()["actions"]}


def _cn(d) -> str:
    d = pd.Timestamp(d)
    return f"{d.month} 月 {d.day} 日"


def _money(x: float) -> str:
    return f"{x:,.0f}"


def _baseline(ds: Dataset, pid: str, window: int = 7):
    """对比窗口开始前 28 天（剔除大促日）的日均指标。"""
    cur_w, _ = windows(ds.as_of, window)
    g = ds.pdays(pid)
    b = g[(g.index < cur_w[0]) & (g.index >= cur_w[0] - 28 * DAY)]
    b = b[~b.index.isin(ds.promo_days())]
    a = agg(b)
    n = max(len(b), 1)
    return dict(daily_gmv=a["gmv"] / n, daily_units=a["units"] / n, daily_uv=a["uv"] / n, cvr=a["cvr"], aov=a["aov"])


def _in_transit(ds: Dataset, pid: str) -> list[dict]:
    ev = ds.events
    if ev.empty:
        return []
    x = ev[(ev["product_id"] == pid) & (ev["event_type"] == "restock") & (ev["date"] > ds.as_of)]
    return [dict(date=row["date"].strftime("%Y-%m-%d"), qty=int(float(row["value"])) if str(row["value"]) not in ("", "nan") else 0,
                 description=row["description"]) for _, row in x.iterrows()]


def _price_series(ds: Dataset, pid: str):
    g = ds.pdays(pid)
    if "price" not in ds.available or "comp_price" not in ds.available or g.empty:
        raise Skip("缺少到手价或竞品到手价数据")
    return g


def _cost(ds: Dataset, pid: str) -> float:
    c = ds.product(pid).get("cost_price")
    if c is None or (isinstance(c, float) and math.isnan(c)):
        raise Skip("缺少成本价，无法测算毛利")
    return float(c)


def _price_estimate(price, cost, new_price, extra_cost=0.0):
    m0 = price - cost
    m1 = new_price - cost - extra_cost
    return dict(type="price", price_before=round(price, 2), price_after=round(new_price, 2),
                unit_margin_before=round(m0, 2), unit_margin_after=round(m1, 2),
                margin_rate_before=r(m0 / price), margin_rate_after=r(m1 / new_price if new_price else 0),
                price_drop=r(1 - new_price / price), breakeven_lift=r(m0 / m1 - 1) if m1 > 0 else None,
                note="不预测销量提升；「保本销量增幅」指销量至少提升多少才能保住总毛利")


def _price_constraints(ds, pid, price, new_price, margin_after):
    c = ds.profile["constraints"]
    checks, approval = [], []
    ok_margin = margin_after >= c["margin_floor"]
    checks.append(dict(name="毛利底线", passed=ok_margin,
                       detail=f"执行后毛利率 {margin_after:.0%}，底线 {c['margin_floor']:.0%}"))
    if not ok_margin:
        raise Skip(f"执行后毛利率 {margin_after:.0%} 低于毛利底线 {c['margin_floor']:.0%}")
    drop = 1 - new_price / price
    ok_auth = drop <= c["price_authority"] + 1e-9
    checks.append(dict(name="调价权限", passed=ok_auth,
                       detail=f"降幅 {drop:.1%}，自主权限 {c['price_authority']:.0%}"))
    if not ok_auth:
        approval.append(f"降幅 {drop:.1%} 超过自主调价权限 {c['price_authority']:.0%}")
    g = ds.pdays(pid)
    win = g[g.index > ds.as_of - c["min_price_window"] * DAY]
    min_p = float(win["price"].min()) if len(win) else price
    ok_min = new_price >= min_p - 1e-9
    checks.append(dict(name="最低价保护", passed=ok_min,
                       detail=f"新到手价 {new_price:g} 元，近 {c['min_price_window']} 天最低 {min_p:g} 元"))
    if not ok_min:
        approval.append(f"低于近 {c['min_price_window']} 天最低成交价 {min_p:g} 元")
    return checks, approval


# ---------------------------------------------------------------------------
# 各动作的参数计算
# ---------------------------------------------------------------------------
def _stock_targets(ds, pid, mode):
    bv = breakdown_variants(ds, pid)
    if not bv.get("available") or not bv.get("has_stock"):
        raise Skip("缺少规格库存数据")
    prof = ds.profile
    vs = bv["variants"]
    if mode == "oos":
        t = [v for v in vs if v["stock_now"] == 0 and (v["baseline_share"] or 0) >= prof["stockout_share_min"]]
    else:
        t = [v for v in vs if v["stock_now"] > 0 and v["days_of_supply"] is not None
             and v["days_of_supply"] < prof["replenish_lead_days"] and (v["baseline_share"] or 0) >= 0.05]
    if not t:
        raise Skip("没有需要处理的规格")
    return bv, t


def act_replenish(ds, pid, cause, window):
    mode = "oos" if cause == "stockout" else "low"
    bv, targets = _stock_targets(ds, pid, mode)
    prof, c = ds.profile, ds.profile["constraints"]
    lead, safety = prof["replenish_lead_days"], c["safety_days"]
    base = _baseline(ds, pid, window)
    transit = _in_transit(ds, pid)
    tq = sum(x["qty"] for x in transit)
    t = targets[0]
    daily = base["daily_units"] * (t["baseline_share"] or 0)
    need = daily * (lead + safety)
    qty = int(math.ceil(need - t["stock_now"] - tq))
    params = dict(variant=t["name"], baseline_daily_units=round(daily, 1), lead_days=lead, safety_days=safety,
                  stock_now=t["stock_now"], in_transit=tq, qty=max(qty, 0))
    loss = base["daily_gmv"] * (t["baseline_share"] or 0)
    est = dict(type="recovery", daily_gmv_recoverable=round(loss, 2),
               note="恢复到货后，该规格按基线每天可恢复的 GMV")
    fill = dict(variant=t["name"], qty=max(qty, 0), lead=lead)
    if qty <= 0 and transit:
        eta = transit[0]["date"]
        steps = [f"在途补货 {tq:,} 件，预计 {eta} 到货；按公式（日均 {daily:.0f} 件 ×（补货周期 {lead} 天 + 安全库存 {safety} 天））无需追加",
                 f"确认 {t['name']} 能否按 {eta} 准时到货，能否提前",
                 "到货当天恢复该规格的正常售卖与投放"]
        step_by = ["我", "供应链", "我"]
        name_override = "确认在途补货到货时间"
    elif qty <= 0:
        raise Skip("按公式计算无需补货")
    else:
        steps = None
        step_by = None
        name_override = None
        if transit:
            fill["qty"] = qty
    return dict(params=params, estimate=est, fill=fill, steps=steps, step_by=step_by, name_override=name_override,
                target=f"{ds.product(pid)['product_name']} · {t['name']}",
                params_text=[f"基线日均销量 {daily:.0f} 件", f"当前库存 {t['stock_now']:,} 件", f"在途 {tq:,} 件",
                             (f"建议补货 {qty:,} 件" if qty > 0 else f"需求 {need:,.0f} 件，在途已覆盖，无需追加")])


def act_redirect_variant(ds, pid, cause, window):
    bv, targets = _stock_targets(ds, pid, "oos")
    t = targets[0]
    alts = [v for v in bv["variants"] if v["stock_now"] > 0 and v["variant_id"] != t["variant_id"]]
    if not alts:
        raise Skip("没有有货的替代规格")
    alt = max(alts, key=lambda v: v["baseline_share"] or 0)
    transit = _in_transit(ds, pid)
    eta = transit[0]["date"] if transit else None
    need_days = (pd.Timestamp(eta) - ds.as_of).days if eta else ds.profile["replenish_lead_days"]
    if alt["days_of_supply"] is not None and alt["days_of_supply"] < need_days:
        raise Skip(f"替代规格 {alt['name']} 库存只够 {alt['days_of_supply']} 天，撑不到恢复")
    base = _baseline(ds, pid, window)
    loss = base["daily_gmv"] * (t["baseline_share"] or 0)
    return dict(params=dict(variant=t["name"], alt_variant=alt["name"], alt_days_of_supply=alt["days_of_supply"],
                            eta=eta), target=f"{ds.product(pid)['product_name']} · {t['name']} → {alt['name']}",
                estimate=dict(type="recovery", daily_gmv_at_risk=round(loss, 2),
                              note="断货规格每天约损失的 GMV；引导替代只能挽回其中一部分，平台不预测比例"),
                fill=dict(variant=t["name"], alt_variant=alt["name"], eta=eta or "到货日"),
                params_text=[f"断货规格 {t['name']}（平时占销量 {t['baseline_share']:.0%}）",
                             f"替代规格 {alt['name']} 库存可售 {alt['days_of_supply']} 天",
                             f"断货规格日均损失约 {_money(loss)} 元"])


def act_pause_variant_ads(ds, pid, cause, window):
    bv, targets = _stock_targets(ds, pid, "oos")
    bc = breakdown_channels(ds, pid, window)
    if not bc.get("available"):
        raise Skip("缺少渠道数据")
    paid = next((c for c in bc["channels"] if c["channel"] == "paid"), None)
    if not paid or paid["uv_cur"] <= 0:
        raise Skip("近 7 日没有付费访客")
    t = targets[0]
    alts = [v for v in bv["variants"] if v["stock_now"] > 0]
    alt = max(alts, key=lambda v: v["baseline_share"] or 0)["name"] if alts else "有货规格"
    return dict(params=dict(variant=t["name"], paid_uv_7d=paid["uv_cur"]), estimate=dict(type="none"),
                target=f"{ds.product(pid)['product_name']} · {t['name']}",
                fill=dict(variant=t["name"], alt_variant=alt),
                params_text=[f"近 7 日付费访客 {paid['uv_cur']:,} 人，日均 {paid['daily_cur']:,.0f} 人"])


def _price_ctx(ds, pid):
    g = _price_series(ds, pid)
    cost = _cost(ds, pid)
    price = float(g["price"].iloc[-1])
    comp = float(g["comp_price"].iloc[-1])
    if comp <= 0 or price <= comp:
        raise Skip("当前没有价格劣势")
    return g, cost, price, comp


def act_store_coupon(ds, pid, cause, window):
    g, cost, price, comp = _price_ctx(ds, pid)
    coupon = int(math.ceil((price - comp) / 5.0) * 5)
    new = price - coupon
    est = _price_estimate(price, cost, new)
    checks, approval = _price_constraints(ds, pid, price, new, est["margin_rate_after"])
    until = _cn(ds.as_of + 10 * DAY)
    return dict(params=dict(coupon=coupon, new_price=new, comp_price=comp), estimate=est, checks=checks,
                approval=approval, target=ds.product(pid)["product_name"],
                fill=dict(coupon=coupon, product=ds.product(pid)["product_name"], valid_until=until, new_price=f"{new:g}"),
                params_text=[f"当前到手价 {price:g} 元，竞品 {comp:g} 元", f"券面额 {coupon} 元，券后 {new:g} 元"])


def _disadvantage_days(g, gap):
    idx = (g["price"] / g["comp_price"]).iloc[::-1]
    n = 0
    for v in idx:
        if v > 1 + gap:
            n += 1
        else:
            break
    return n


def act_price_match(ds, pid, cause, window):
    g, cost, price, comp = _price_ctx(ds, pid)
    days = _disadvantage_days(g, ds.profile["price_gap"])
    if days < 7:
        raise Skip(f"价格劣势仅持续 {days} 天，不足 7 天，先用可撤回的方式应对")
    new = comp
    est = _price_estimate(price, cost, new)
    checks, approval = _price_constraints(ds, pid, price, new, est["margin_rate_after"])
    return dict(params=dict(new_price=new, disadvantage_days=days), estimate=est, checks=checks, approval=approval,
                target=ds.product(pid)["product_name"], fill=dict(new_price=f"{new:g}"),
                params_text=[f"价格劣势已持续 {days} 天", f"新到手价 {new:g} 元（与竞品持平）"])


def act_gift(ds, pid, cause, window):
    g, cost, price, comp = _price_ctx(ds, pid)
    gap = price - comp
    gifts = ds.profile["action_prefs"].get("gifts") or []
    if not gifts:
        raise Skip("品类配置中没有可用赠品")
    gift = min(gifts, key=lambda x: abs(x["value"] - gap))
    est = _price_estimate(price, cost, price, extra_cost=gift["cost"])
    checks, approval = _price_constraints(ds, pid, price, price, est["margin_rate_after"])
    checks = [c for c in checks if c["name"] == "毛利底线"]
    return dict(params=dict(gift=gift["name"], gift_cost=gift["cost"], gift_value=gift["value"], price_gap=gap),
                estimate=est, checks=checks, approval=[], target=ds.product(pid)["product_name"],
                fill=dict(product=ds.product(pid)["product_name"], gift=gift["name"], gift_value=gift["value"]),
                params_text=[f"价差 {gap:g} 元", f"赠品 {gift['name']}（成本 {gift['cost']} 元，标价 {gift['value']} 元）"])


def act_bundle_discount(ds, pid, cause, window):
    g = _price_series(ds, pid) if "price" in ds.available else None
    if g is None:
        raise Skip("缺少到手价数据")
    cost = _cost(ds, pid)
    price = float(g["price"].iloc[-1])
    comp = float(g["comp_price"].iloc[-1]) if "comp_price" in ds.available else 0
    if cause == "price_disadvantage" and not (comp and price > comp):
        raise Skip("当前没有价格劣势")
    rate =math.floor(comp / price * 100) / 100 if comp and comp < price else 0.90
    rate = min(rate, 0.95)
    new_unit = round(price * rate, 2)
    est = _price_estimate(price, cost, new_unit)
    checks, approval = _price_constraints(ds, pid, price, new_unit, est["margin_rate_after"])
    txt = f"{rate * 10:g} 折"
    return dict(params=dict(discount_rate=rate, unit_price_after=new_unit), estimate=est, checks=checks,
                approval=approval, target=ds.product(pid)["product_name"],
                fill=dict(product=ds.product(pid)["product_name"], discount_text=txt, unit_price_after=f"{new_unit:g}"),
                params_text=[f"满 2 件 {txt}", f"折合每件 {new_unit:g} 元"])


def _events(ds, pid, types, start=None):
    ev = ds.events
    if ev.empty:
        return ev
    x = ev[(ev["product_id"].isin([pid, ""])) & (ev["event_type"].isin(types)) & (ev["date"] <= ds.as_of)]
    if start is not None:
        x = x[x["date"] >= start]
    return x.sort_values("date")


def act_review_ad_budget(ds, pid, cause, window):
    bc = breakdown_channels(ds, pid, window)
    if not bc.get("available"):
        raise Skip("缺少渠道数据")
    dc = ds.cdays(pid)
    paid = dc[dc["channel"] == "paid"].set_index("date")["uv"]
    cur_w, prev_w = windows(ds.as_of, window)
    evs = _events(ds, pid, ["ad_budget_change"], prev_w[0])
    ev_date = evs["date"].iloc[-1] if len(evs) else None
    after = paid[paid.index >= ev_date] if ev_date is not None else paid[paid.index > ds.as_of - 3 * DAY]
    before = paid[(paid.index >= prev_w[0]) & (paid.index <= prev_w[1])]
    gap = float(before.mean() - after.mean())
    if gap <= 0:
        raise Skip("付费访客没有减少")
    base = _baseline(ds, pid, window)
    rec = gap * base["cvr"] * base["aov"]
    return dict(params=dict(daily_paid_before=round(before.mean()), daily_paid_after=round(after.mean()),
                            uv_gap=round(gap), event_date=ev_date.strftime("%Y-%m-%d") if ev_date is not None else None),
                estimate=dict(type="recovery", daily_gmv_recoverable=round(rec, 2),
                              note="按基线转化率与客单价计算，付费访客恢复后每天可挽回的 GMV"),
                target=ds.product(pid)["product_name"],
                fill=dict(event_date=_cn(ev_date) if ev_date is not None else "近期", uv_gap=f"{gap:,.0f}"),
                params_text=[f"付费访客日均 {before.mean():,.0f} → {after.mean():,.0f} 人",
                             f"每天缺口约 {gap:,.0f} 人", f"可挽回约 {_money(rec)} 元/天"])


def act_search_optimize(ds, pid, cause, window):
    bc = breakdown_channels(ds, pid, window)
    if not bc.get("available"):
        raise Skip("缺少渠道数据")
    s = next((c for c in bc["channels"] if c["channel"] == "search"), None)
    if not s or s["change"] >= 0:
        raise Skip("搜索访客没有下滑")
    return dict(params=dict(search_uv_prev=s["uv_prev"], search_uv_cur=s["uv_cur"], change_pct=s["change_pct"]),
                estimate=dict(type="none"), target=ds.product(pid)["product_name"],
                fill=dict(product=ds.product(pid)["product_name"]),
                params_text=[f"搜索访客 {s['uv_prev']:,} → {s['uv_cur']:,}（{s['change_pct']:+.0%}）"])


def _campaign_stats(ds, pid):
    if "channel" not in ds.available:
        raise Skip("缺少渠道数据")
    ev = _events(ds, pid, ["campaign_end"])
    if ev.empty:
        raise Skip("没有活动结束事件")
    end = ev["date"].iloc[-1]
    st = _events(ds, pid, ["campaign_start"])
    start = st["date"].iloc[-1] if len(st) else end - 14 * DAY
    g = ds.pdays(pid)
    during = g[(g.index >= start) & (g.index < end)]
    after = g[g.index >= end]
    return end, float(during["uv"].mean()), float(after["uv"].mean()), float(during["gmv"].mean()), float(after["gmv"].mean())


def act_campaign_signup(ds, pid, cause, window):
    end, uv_d, uv_a, g_d, g_a = _campaign_stats(ds, pid)
    lead = ds.profile["constraints"]["campaign_signup_lead"]
    return dict(params=dict(uv_during=round(uv_d), uv_after=round(uv_a), gmv_during=round(g_d), gmv_after=round(g_a)),
                estimate=dict(type="none", note="演示数据未提供平台活动日历，需人工核对下一档活动报名时间"),
                target=ds.product(pid)["product_name"], fill=dict(product=ds.product(pid)["product_name"], lead=lead),
                params_text=[f"活动期日均访客 {uv_d:,.0f} 人，活动后 {uv_a:,.0f} 人",
                             f"活动期日均 GMV {_money(g_d)} 元，活动后 {_money(g_a)} 元", f"需提前约 {lead} 天报名"])


def act_mark_known(ds, pid, cause, window):
    end, uv_d, uv_a, g_d, g_a = _campaign_stats(ds, pid)
    return dict(params=dict(campaign_end=end.strftime("%Y-%m-%d"), gmv_during=round(g_d), gmv_after=round(g_a)),
                estimate=dict(type="none"), target=ds.product(pid)["product_name"], fill={},
                params_text=[f"活动 {end:%m-%d} 结束", f"日均 GMV {_money(g_d)} → {_money(g_a)} 元"])


def act_review_handling(ds, pid, cause, window):
    if "rating" not in ds.available:
        raise Skip("缺少评分数据")
    g = ds.pdays(pid)
    prev28 = g[(g.index < ds.as_of) & (g.index >= ds.as_of - 28 * DAY)]
    rating_base = float(prev28["rating"].mean())
    rating_now = float(g["rating"].iloc[-1])
    ev = _events(ds, pid, ["review_issue"])
    issue_date = ev["date"].iloc[-1] if len(ev) else None
    l7 = g[g.index > ds.as_of - 7 * DAY]
    rr7 = l7["refund_amount"].sum() / max(l7["gmv"].sum(), 1) if "refund_amount" in ds.available else None
    txt = [f"评分 {rating_base:.2f} → {rating_now:.2f}"]
    if rr7 is not None:
        txt.append(f"近 7 日退款率 {rr7:.1%}")
    if issue_date is not None:
        txt.append(f"{issue_date:%m-%d} 起集中差评：{ev['description'].iloc[-1]}")
    return dict(params=dict(rating_base=round(rating_base, 2), rating_now=round(rating_now, 2),
                            refund_rate_7d=r(rr7), issue_date=issue_date.strftime("%Y-%m-%d") if issue_date is not None else None),
                estimate=dict(type="none"), target=ds.product(pid)["product_name"],
                fill=dict(issue_date=_cn(issue_date) if issue_date is not None else "近期"),
                params_text=txt)


def act_boost_stock(ds, pid, cause, window):
    bv = breakdown_variants(ds, pid)
    if not bv.get("available") or not bv.get("has_stock"):
        raise Skip("缺少规格库存数据")
    prof, c = ds.profile, ds.profile["constraints"]
    lead, safety = prof["replenish_lead_days"], c["safety_days"]
    v = ds.vdays(pid)
    transit = _in_transit(ds, pid)
    tq = sum(x["qty"] for x in transit)
    best = None
    for row in bv["variants"]:
        if row["stock_now"] <= 0:
            continue
        l3 = v[(v["variant_id"] == row["variant_id"]) & (v["date"] > ds.as_of - 3 * DAY)]["units"].mean()
        qty = int(math.ceil(l3 * (lead + safety) - row["stock_now"] - tq))
        if qty > 0 and (best is None or qty > best[1]):
            best = (row, qty, l3)
    if best is None:
        raise Skip("按近 3 日销量计算库存充足")
    row, qty, l3 = best
    return dict(params=dict(variant=row["name"], daily_units_3d=round(l3, 1), stock_now=row["stock_now"],
                            days_of_supply=row["days_of_supply"], qty=qty),
                estimate=dict(type="none"), target=f"{ds.product(pid)['product_name']} · {row['name']}",
                fill=dict(variant=row["name"], qty=f"{qty:,}", lead=lead),
                params_text=[f"近 3 日日均销量 {l3:,.0f} 件", f"当前库存 {row['stock_now']:,} 件，可售约 {row['days_of_supply']} 天",
                             f"建议补货 {qty:,} 件"])


def act_expiry_promo(ds, pid, cause, window):
    raise Skip("缺少批次效期数据")


CALC = {k[4:]: v for k, v in globals().items() if k.startswith("act_")}


# ---------------------------------------------------------------------------
def plan_actions(ds: Dataset, pid: str, cause: str, window: int = 7) -> dict:
    lib = _lib()
    prefs = ds.profile.get("action_prefs", {})
    order = [a for a in config.action_library()["actions"] if cause in a["causes"]]
    if cause == "price_disadvantage":
        pref = prefs.get("price_actions") or []
        order.sort(key=lambda a: pref.index(a["id"]) if a["id"] in pref else 99)
    candidates, excluded = [], []
    for a in order:
        if a.get("optional") and a["id"] not in (prefs.get("enabled_optional") or []):
            continue
        need = set(a.get("requires") or [])
        mapping = {"stock": "stock", "channel": "channel", "price": "price", "comp_price": "comp_price",
                   "cost_price": "cost_price", "rating": "rating", "events": "events"}
        missing = [x for x in need if x in mapping and mapping[x] not in ds.available]
        if missing:
            cn = {"stock": "库存", "channel": "渠道访客", "price": "到手价", "comp_price": "竞品价格", "cost_price": "成本价",
                  "rating": "评分", "events": "事件记录"}
            excluded.append(dict(action_id=a["id"], name=a["name"], reason=f"缺少数据：{'、'.join(cn.get(x, x) for x in missing)}"))
            continue
        try:
            res = CALC[a["id"]](ds, pid, cause, window)
        except Skip as e:
            excluded.append(dict(action_id=a["id"], name=a["name"], reason=str(e)))
            continue
        fill = res.get("fill", {})
        steps = res.get("steps") or []
        if not steps:
            for s in a.get("steps", []):
                try:
                    steps.append(s.format(**fill))
                except KeyError:
                    steps.append(s)
        risk_notes = res.get("approval") or []          # 超出调价权限 / 可能破价：只提示，不拦截
        exec_type = a["exec_type"]
        track = dict(a.get("track") or {})
        track["metric_name"] = METRIC_NAMES.get(track.get("metric"), track.get("metric"))
        candidates.append(dict(
            action_id=a["id"], name=res.get("name_override") or a["name"], category=a["category"],
            cause=cause, cause_name=config.cause_name(cause), target=res.get("target"),
            params=res.get("params", {}), params_text=res.get("params_text", []),
            estimate=res.get("estimate", {"type": "none"}), checks=res.get("checks", []),
            exec_type=exec_type, risk_notes=risk_notes, owner_role=a["owner_role"], steps=steps,
            due_days=a.get("due_days", 3),
            step_owners=_step_owners(a, steps, res.get("step_by")),
            track=track, risks=a.get("risks") or [], materials=a.get("materials") or [],
        ))
        _relabel(candidates[-1])
        candidates[-1]["handoffs"] = handoffs_of(candidates[-1])
    return dict(product_id=pid, cause=cause, cause_name=config.cause_name(cause),
                candidates=candidates, excluded=excluded,
                note=None if candidates else "动作库中没有可执行的方案，需要人工判断")


SELF = "我"


def _step_owners(a: dict, steps: list, override=None) -> list[str]:
    """每一步由谁来做：计算结果指定 > 动作库 step_by > 按执行类型推断。"""
    if override and len(override) == len(steps):
        return list(override)
    lib_by = a.get("step_by")
    if lib_by and len(lib_by) == len(steps):
        return list(lib_by)
    et = str(a.get("exec_type", ""))
    owner = SELF if ("自己执行" in et or "转交" not in et) else a.get("owner_role", SELF)
    return [owner] * len(steps)


def _relabel(plan: dict):
    """执行类型与负责角色按实际步骤负责人重新标注。"""
    owners = plan.get("step_owners") or []
    others = list(dict.fromkeys(o for o in owners if o != SELF))
    mine = SELF in owners
    plan["exec_type"] = "自己执行 + 转交" if (mine and others) else ("转交" if others else "自己执行")
    plan["owner_role"] = " / ".join((["商品运营"] if mine else []) + others) or plan.get("owner_role")


def handoffs_of(plan: dict) -> list[dict]:
    """需要同事参与的部分：按角色归并的步骤（同一角色一张飞书卡片）。"""
    roles: dict[str, list[int]] = {}
    for i, who in enumerate(plan.get("step_owners") or []):
        if who != SELF:
            roles.setdefault(who, []).append(i)
    return [dict(kind="transfer", role=role, steps=idx) for role, idx in roles.items()]


def annotate(plan: dict) -> dict:
    """为旧版本生成的方案（如缓存结果）补上步骤负责人与协同事项，幂等。"""
    if plan.get("approval_reasons") and not plan.get("risk_notes"):
        plan["risk_notes"] = plan.pop("approval_reasons")
    plan.pop("approval_reasons", None)
    if not plan.get("step_owners") or len(plan["step_owners"]) != len(plan.get("steps") or []):
        a = next((x for x in config.action_library()["actions"] if x["id"] == plan.get("action_id")), None)
        steps = plan.get("steps") or []
        override = None
        if plan.get("action_id") == "replenish" and len(steps) == 3 and "在途" in steps[0]:
            override = [SELF, "供应链", SELF]
        plan["step_owners"] = _step_owners(a or {"exec_type": plan.get("exec_type", ""), "owner_role": plan.get("owner_role")},
                                           steps, override)
        if a and "due_days" not in plan:
            plan["due_days"] = a.get("due_days", 3)
    plan["step_owners"] = [o if o in ("我", "供应链", "投放运营") else SELF for o in plan["step_owners"]]
    _relabel(plan)
    plan["handoffs"] = handoffs_of(plan)
    return plan


def completeness(plan: dict) -> list[str]:
    """反空话检查：六项要素，返回缺失项。"""
    miss = []
    if not plan.get("target"):
        miss.append("具体对象")
    has_num = any(any(ch.isdigit() for ch in str(t)) for t in plan.get("params_text", []))
    if not has_num:
        miss.append("具体数值")
    if not plan.get("owner_role") or not plan.get("exec_type"):
        miss.append("执行人及权限")
    if not plan.get("steps"):
        miss.append("执行步骤")
    if not (plan.get("track") or {}).get("metric") or not (plan.get("track") or {}).get("days"):
        miss.append("跟踪指标与天数")
    if not plan.get("risks"):
        miss.append("前提或风险")
    return miss
