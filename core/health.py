"""健康度评分（0–100），规则见需求文档 9.3。"""
from __future__ import annotations

from .decompose import breakdown_variants
from .loader import Dataset
from .metrics import DAY, agg, slice_, windows


def _lin(x, full, zero):
    """x 在 full 时得满分比例 1，在 zero 时得 0，之间线性插值。"""
    if full == zero:
        return 1.0
    t = (x - zero) / (full - zero)
    return max(0.0, min(1.0, t))


def score(ds: Dataset, pid: str, as_of=None) -> dict:
    as_of = as_of or ds.as_of
    prof = ds.profile
    g = ds.pdays(pid)
    cur_w, prev_w = windows(as_of, 7)
    a1, a0 = agg(slice_(g, cur_w)), agg(slice_(g, prev_w))
    parts = {}
    # 销售趋势 30
    if a0["gmv"] > 0:
        chg = a1["gmv"] / a0["gmv"] - 1
        parts["trend"] = (30, 30 * _lin(chg, 0.10, -0.20))
    # 转化 20
    base = g[(g.index < cur_w[0]) & (g.index >= cur_w[0] - 28 * DAY)]
    if len(base) >= 7 and a1["uv"]:
        bc = base["buyers"].sum() / max(base["uv"].sum(), 1)
        parts["conversion"] = (20, 20 * _lin(a1["cvr"], bc, bc * 0.7))
    # 库存 20
    if "stock" in ds.available:
        bv = breakdown_variants(ds, pid, 7, as_of)
        lead = prof["replenish_lead_days"]
        main_oos = any(v["stock_now"] == 0 and (v["baseline_share"] or 0) >= prof["stockout_share_min"]
                       for v in bv["variants"])
        dos = [v["days_of_supply"] for v in bv["variants"] if v["days_of_supply"] is not None and v["stock_now"] > 0
               and (v["baseline_share"] or 0) >= 0.05]
        if main_oos:
            parts["stock"] = (20, 0.0)
        elif dos:
            parts["stock"] = (20, 20 * _lin(min(dos), 2 * lead, lead / 2))
    # 价格 15
    if "price" in ds.available and "comp_price" in ds.available and len(g):
        pi = float(g["price"].iloc[-1] / g["comp_price"].iloc[-1]) if g["comp_price"].iloc[-1] else 1.0
        parts["price"] = (15, 15 * _lin(pi, 1.0, 1 + 2 * prof["price_gap"]))
    # 口碑 15
    if "rating" in ds.available and len(g) >= 8:
        prev28 = g[(g.index < as_of) & (g.index >= as_of - 28 * DAY)]
        drop = float(prev28["rating"].mean() - g["rating"].iloc[-1]) if len(prev28) else 0.0
        s = _lin(drop, 0.0, 2 * prof["rating_drop"])
        if "refund_amount" in ds.available:
            b = g[(g.index <= as_of - 7 * DAY) & (g.index > as_of - 35 * DAY)]
            rrb = b["refund_amount"].sum() / max(b["gmv"].sum(), 1)
            rr7 = a1["refund_rate"]
            if rrb > 0:
                s = min(s, _lin(rr7 / rrb, 1.0, 2.0))
        parts["reputation"] = (15, 15 * s)
    full = sum(v[0] for v in parts.values())
    got = sum(v[1] for v in parts.values())
    total = round(100 * got / full) if full else None
    level = None if total is None else ("健康" if total >= 80 else "关注" if total >= 60 else "风险")
    names = {"trend": "销售趋势", "conversion": "转化", "stock": "库存", "price": "价格", "reputation": "口碑"}
    return dict(score=total, level=level,
                parts=[dict(key=k, name=names[k], full=v[0], score=round(v[1], 1)) for k, v in parts.items()],
                missing=[names[k] for k in names if k not in parts])
