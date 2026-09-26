"""商品分层与生命周期。"""
from __future__ import annotations

import pandas as pd

from . import config
from .loader import Dataset
from .metrics import DAY, safe_div

TIER_NAMES = {"hero": "爆品", "rising": "潜力品", "profit": "利润品", "tail": "长尾品"}
LC_NAMES = {"new": "新品期", "growth": "成长期", "decline": "衰退期", "mature": "成熟期"}


def compute(ds: Dataset, as_of: pd.Timestamp | None = None, focus_override: dict | None = None) -> dict:
    """返回 {pid: {tier, lifecycle, coef, focus, gmv28, growth28, margin, cum_prev, launch_days}}"""
    as_of = as_of or ds.as_of
    prof = ds.profile
    cfg = config.tiering_cfg()
    n = cfg.get("window_days", 28)
    cur0, prev0 = as_of - (n - 1) * DAY, as_of - (2 * n - 1) * DAY
    rows = []
    promo = ds.promo_days()
    for pid in ds.product_ids():
        g = ds.pdays(pid)
        gc = g.loc[(g.index >= cur0) & (g.index <= as_of), "gmv"]
        gp = g.loc[(g.index >= prev0) & (g.index < cur0), "gmv"]
        cur = gc.sum()
        # 增长率：剔除大促日后按日均比较，与预警基线口径一致
        gc_n, gp_n = gc[~gc.index.isin(promo)], gp[~gp.index.isin(promo)]
        cur_m = gc_n.mean() if len(gc_n) else 0.0
        prev_m = gp_n.mean() if len(gp_n) >= 7 else 0.0
        prev = gp.sum()
        p = ds.product(pid)
        price = float(g["price"].iloc[-1]) if "price" in g.columns and len(g) else None
        cost = p.get("cost_price")
        margin = safe_div(price - cost, price) if price and cost == cost and cost is not None else None
        launch_days = int((as_of - pd.Timestamp(p["launch_date"])).days)
        growth = (cur_m / prev_m - 1) if prev_m > 0 else (float("inf") if cur > 0 else 0.0)
        rows.append(dict(pid=pid, gmv28=float(cur), prev28=float(prev), growth28=growth, margin=margin,
                         launch_days=launch_days))
    total = sum(x["gmv28"] for x in rows) or 1.0
    rows.sort(key=lambda x: x["gmv28"], reverse=True)
    cum = 0.0
    out = {}
    growth_days = prof["growth_days"]
    for x in rows:
        cum_prev = cum / total
        cum += x["gmv28"]
        if cum_prev < 0.5:
            tier = "hero"
        elif x["launch_days"] <= growth_days and x["growth28"] >= 0.30:
            tier = "rising"
        elif x["margin"] is not None and x["margin"] >= prof["margin_high"] and 0.5 <= cum_prev < 0.85:
            tier = "profit"
        else:
            tier = "tail"
        if x["launch_days"] < prof["new_product_days"]:
            lc = "new"
        elif x["launch_days"] < growth_days and x["growth28"] >= 0.10:
            lc = "growth"
        elif x["launch_days"] >= 180 and x["growth28"] <= -0.20:
            lc = "decline"
        else:
            lc = "mature"
        coef = {c["id"]: c["coef"] for c in cfg["lifecycles"]}[lc]
        focus = tier != "tail"
        if focus_override and x["pid"] in focus_override:
            focus = bool(focus_override[x["pid"]])
        out[x["pid"]] = dict(tier=tier, tier_name=TIER_NAMES[tier], lifecycle=lc, lifecycle_name=LC_NAMES[lc],
                             coef=coef, focus=focus, gmv28=round(x["gmv28"], 2),
                             growth28=None if x["growth28"] == float("inf") else round(x["growth28"], 4),
                             margin=None if x["margin"] is None else round(x["margin"], 4),
                             cum_share_before=round(cum_prev, 4), launch_days=x["launch_days"])
    return out
