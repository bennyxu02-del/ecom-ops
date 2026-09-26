"""诊断工具：平台以 function calling 暴露给大模型，Skill 以命令行暴露给 Agent。两者返回同样的 JSON。"""
from __future__ import annotations

import pandas as pd

from . import alerts as alerts_mod
from . import config, health, tiering
from .actions import plan_actions as _plan
from .decompose import breakdown_channels as _bc
from .decompose import breakdown_variants as _bv
from .decompose import decompose_gmv as _dg
from .loader import Dataset
from .metrics import DAY, agg, r, slice_, windows

AVAILABLE_NAMES = {"variant": "规格明细", "stock": "库存", "channel": "渠道访客", "price": "到手价",
                   "comp_price": "竞品价格", "rating": "评分", "refund_amount": "退款", "cost_price": "成本价",
                   "events": "事件记录"}


def get_context(ds: Dataset, product_id: str, card: dict | None = None, tiers: dict | None = None) -> dict:
    p = ds.product(product_id)
    tiers = tiers or tiering.compute(ds)
    t = tiers[product_id]
    prof = ds.profile
    g = ds.pdays(product_id)
    cur_w, prev_w = windows(ds.as_of, 7)
    a1, a0 = agg(slice_(g, cur_w)), agg(slice_(g, prev_w))
    chg = (a1["gmv"] / a0["gmv"] - 1) if a0["gmv"] else None
    th = prof["gmv_drop_yellow"] * t["coef"]
    rules = set((card or {}).get("rules", []))
    within = (not rules & {"R01", "R02", "R03"}) and (chg is None or chg > -th)
    return dict(
        product_id=product_id, gmv_change_7d=r(chg), decline_alert_threshold=r(-th),
        within_normal_range=bool(within), product_name=p["product_name"], category=ds.category,
        sub_category=p.get("sub_category"), launch_date=str(pd.Timestamp(p["launch_date"]).date()),
        as_of=ds.as_of.strftime("%Y-%m-%d"), tier=t["tier_name"], lifecycle=t["lifecycle_name"],
        alert_threshold_coef=t["coef"], health=health.score(ds, product_id),
        alert_card=None if card is None else dict(severity=card["severity"], rules=card["rule_names"],
                                                  first_date=card["first_date"], trigger_days=card["trigger_days"],
                                                  details=[h["text"] for h in card.get("latest", [])]),
        category_profile=dict(name=prof.get("name"), matched=prof.get("matched"),
                              factor_priority=prof.get("factor_priority"),
                              seasonality_note=prof.get("seasonality_note"),
                              replenish_lead_days=prof.get("replenish_lead_days")),
        constraints=prof.get("constraints"),
        available_data=[AVAILABLE_NAMES[k] for k in AVAILABLE_NAMES if k in ds.available],
        missing_data=[AVAILABLE_NAMES[k] for k in AVAILABLE_NAMES if k not in ds.available],
    )


def decompose_gmv(ds: Dataset, product_id: str, window: int = 7) -> dict:
    return _dg(ds, product_id, window)


def breakdown_channels(ds: Dataset, product_id: str, window: int = 7) -> dict:
    return _bc(ds, product_id, window)


def breakdown_variants(ds: Dataset, product_id: str, window: int = 7) -> dict:
    return _bv(ds, product_id, window)


def get_events(ds: Dataset, product_id: str, start: str | None = None, end: str | None = None) -> dict:
    ev = ds.events
    cur_w, prev_w = windows(ds.as_of, 7)
    s = pd.Timestamp(start) if start else prev_w[0] - 3 * DAY
    e = pd.Timestamp(end) if end else ds.as_of
    out, transit = [], []
    if not ev.empty:
        x = ev[ev["product_id"].isin([product_id, ""])]
        for _, row in x.sort_values("date").iterrows():
            item = dict(date=row["date"].strftime("%Y-%m-%d"), type=row["event_type"], description=row["description"],
                        value=None if str(row["value"]) in ("", "nan") else row["value"],
                        scope="全店" if row["product_id"] == "" else "本商品")
            if row["event_type"] == "restock" and row["date"] > ds.as_of:
                transit.append(item)
            elif s <= row["date"] <= e:
                out.append(item)
    return dict(product_id=product_id, start=s.strftime("%Y-%m-%d"), end=e.strftime("%Y-%m-%d"),
                events=out, in_transit=transit,
                note=None if "events" in ds.available else "数据中没有事件记录，无法做事件关联")


def check_factors(ds: Dataset, product_id: str, window: int = 7, factors: list | None = None) -> dict:
    prof = ds.profile
    order = factors or prof.get("factor_priority") or ["stock", "price", "rating", "campaign"]
    g = ds.pdays(product_id)
    cur_w, prev_w = windows(ds.as_of, window)
    res = {}
    for f in order:
        if f == "stock":
            if "stock" not in ds.available:
                res[f] = dict(name="库存", checked=False, note="缺少库存数据")
                continue
            bv = _bv(ds, product_id, window)
            oos = [v for v in bv["variants"] if v.get("stockout_days", 0) > 0 or v.get("stock_now") == 0]
            low = [v for v in bv["variants"] if v.get("stock_now", 0) > 0 and v.get("days_of_supply") is not None
                   and v["days_of_supply"] < prof["replenish_lead_days"]]
            res[f] = dict(name="库存", checked=True, abnormal=bool(oos or low),
                          stockout_variants=[dict(name=v["name"], stockout_days=v.get("stockout_days"),
                                                  dates=v.get("stockout_dates"), baseline_share=v["baseline_share"])
                                             for v in oos],
                          low_stock_variants=[dict(name=v["name"], days_of_supply=v["days_of_supply"]) for v in low],
                          replenish_lead_days=prof["replenish_lead_days"])
        elif f == "price":
            if "price" not in ds.available or "comp_price" not in ds.available:
                res[f] = dict(name="价格", checked=False, note="缺少竞品价格数据")
                continue
            last = g.iloc[-1]
            pi = float(last["price"] / last["comp_price"]) if last["comp_price"] else None
            p_prev = slice_(g, prev_w)
            pi_prev = float((p_prev["price"] / p_prev["comp_price"]).mean()) if len(p_prev) else None
            ev = get_events(ds, product_id)["events"]
            comp_ev = [e for e in ev if e["type"] == "competitor_price_change"]
            own_ev = [e for e in ev if e["type"] == "price_change"]
            res[f] = dict(name="价格", checked=True, abnormal=bool(pi and pi > 1 + prof["price_gap"]),
                          price=float(last["price"]), comp_price=float(last["comp_price"]), price_index=r(pi, 3),
                          price_index_prev_window=r(pi_prev, 3), threshold=r(1 + prof["price_gap"], 3),
                          competitor_events=comp_ev, own_price_events=own_ev)
        elif f == "rating":
            if "rating" not in ds.available:
                res[f] = dict(name="口碑", checked=False, note="缺少评分数据")
                continue
            prev28 = g[(g.index < ds.as_of) & (g.index >= ds.as_of - 28 * DAY)]
            base = float(prev28["rating"].mean())
            now = float(g["rating"].iloc[-1])
            a1 = agg(slice_(g, cur_w))
            b = g[(g.index <= ds.as_of - 7 * DAY) & (g.index > ds.as_of - 35 * DAY)]
            rrb = float(b["refund_amount"].sum() / max(b["gmv"].sum(), 1)) if "refund_amount" in ds.available else None
            ev = [e for e in get_events(ds, product_id)["events"] if e["type"] == "review_issue"]
            abnormal = (base - now) >= prof["rating_drop"] or (rrb and a1["refund_rate"] > rrb * 1.5)
            res[f] = dict(name="口碑", checked=True, abnormal=bool(abnormal), rating_now=round(now, 2),
                          rating_base_28d=round(base, 2), refund_rate_7d=r(a1["refund_rate"]),
                          refund_rate_base=r(rrb), review_events=ev)
        elif f == "campaign":
            ev = [e for e in get_events(ds, product_id, (prev_w[0] - 21 * DAY).strftime("%Y-%m-%d"))["events"]
                  if e["type"] in ("campaign_start", "campaign_end")]
            ch = None
            if "channel" in ds.available:
                bc = _bc(ds, product_id, window)
                ch = next((c for c in bc["channels"] if c["channel"] == "campaign"), None)
            abnormal = bool(ch and ch["change"] < 0 and ch["uv_prev"] > 0 and ch["change_pct"] is not None
                            and ch["change_pct"] <= -0.3)
            res[f] = dict(name="活动", checked=True, abnormal=abnormal, campaign_events=ev,
                          campaign_channel=None if ch is None else dict(uv_prev=ch["uv_prev"], uv_cur=ch["uv_cur"],
                                                                         change_pct=ch["change_pct"]))
    return dict(product_id=product_id, order=order, factors=res)


def plan_actions(ds: Dataset, product_id: str, cause: str, window: int = 7) -> dict:
    return _plan(ds, product_id, cause, window)


# ---------------------------------------------------------------------------
TOOL_SPECS = [
    {"name": "get_context", "description": "读取商品概况：分层、生命周期、健康度、当前问题卡、品类配置、经营约束、可用数据清单。诊断第一步调用。",
     "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}}, "required": ["product_id"]}},
    {"name": "decompose_gmv", "description": "一级贡献度拆解：GMV 变化分摊到访客数、支付转化率、客单价（LMDI），返回两期值、变化率、贡献额与占比。",
     "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, "window": {"type": "integer", "enum": [7, 14, 28]}},
                    "required": ["product_id"]}},
    {"name": "breakdown_channels", "description": "访客按渠道下钻（搜索、推荐、付费投放、活动会场、站外），返回各渠道两期访客与变化。",
     "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, "window": {"type": "integer"}}, "required": ["product_id"]}},
    {"name": "breakdown_variants", "description": "销量按规格下钻，返回各规格销量、占比变化、库存、断货日期、可售天数、断货损失估算。",
     "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, "window": {"type": "integer"}}, "required": ["product_id"]}},
    {"name": "check_factors", "description": "按品类配置顺序检查转化率的影响因素：库存、价格、口碑、活动；返回每个因素是否异常及数值。",
     "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, "window": {"type": "integer"},
                                                     "factors": {"type": "array", "items": {"type": "string", "enum": ["stock", "price", "rating", "campaign"]}}},
                    "required": ["product_id"]}},
    {"name": "get_events", "description": "查询商品及全店事件（调价、竞品调价、活动、投放调整、补货、差评等），以及在途补货。",
     "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}},
                    "required": ["product_id"]}},
    {"name": "plan_actions", "description": "针对一个根因生成候选方案：从动作库选择动作、按数据计算参数、检查经营约束、测算。只返回通过检查的方案及被排除的动作和原因。",
     "parameters": {"type": "object", "properties": {"product_id": {"type": "string"},
                                                     "cause": {"type": "string", "enum": ["stockout", "stockout_risk", "price_disadvantage", "paid_traffic_drop",
                                                                                          "search_traffic_drop", "campaign_end", "reputation_drop", "aov_drop",
                                                                                          "growth_opportunity"]}},
                    "required": ["product_id", "cause"]}},
]

FUNCS = {"get_context": get_context, "decompose_gmv": decompose_gmv, "breakdown_channels": breakdown_channels,
         "breakdown_variants": breakdown_variants, "check_factors": check_factors, "get_events": get_events,
         "plan_actions": plan_actions}


def call(ds: Dataset, name: str, args: dict, **extra) -> dict:
    if name not in FUNCS:
        return {"error": f"未知工具 {name}"}
    args = {k: v for k, v in (args or {}).items() if v is not None}
    if name == "get_context":
        return get_context(ds, args["product_id"], card=extra.get("card"), tiers=extra.get("tiers"))
    return FUNCS[name](ds, **args)
