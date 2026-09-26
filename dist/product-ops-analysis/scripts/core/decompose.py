"""贡献度拆解：一级（乘法，LMDI）与二级（渠道、规格）。"""
from __future__ import annotations

import math

import pandas as pd

from . import config
from .loader import Dataset
from .metrics import DAY, agg, fmt_window, r, slice_, windows


def _L(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    if abs(a - b) < 1e-9:
        return a
    return (a - b) / (math.log(a) - math.log(b))


def lmdi(v1: dict, v0: dict, factors: list[str], total_key: str) -> tuple[dict, str]:
    """返回 {factor: 贡献额}, 方法名。任一值为 0 时改用连环替代法。"""
    t1, t0 = v1[total_key], v0[total_key]
    if all(v1[f] > 0 and v0[f] > 0 for f in factors) and t1 > 0 and t0 > 0:
        L = _L(t1, t0)
        return {f: L * math.log(v1[f] / v0[f]) for f in factors}, "LMDI"
    out, cur = {}, dict(v0)
    prod = lambda d: math.prod(d[f] for f in factors)
    base = prod(cur)
    for f in factors:
        cur[f] = v1[f]
        nxt = prod(cur)
        out[f] = nxt - base
        base = nxt
    return out, "连环替代"


def decompose_gmv(ds: Dataset, pid: str, window: int = 7, as_of=None) -> dict:
    as_of = as_of or ds.as_of
    cur_w, prev_w = windows(as_of, window)
    g = ds.pdays(pid)
    a1, a0 = agg(slice_(g, cur_w)), agg(slice_(g, prev_w))
    v1 = {"gmv": a1["gmv"], "uv": a1["uv"], "cvr": a1["cvr"], "aov": a1["aov"]}
    v0 = {"gmv": a0["gmv"], "uv": a0["uv"], "cvr": a0["cvr"], "aov": a0["aov"]}
    contrib, method = lmdi(v1, v0, ["uv", "cvr", "aov"], "gmv")
    delta = a1["gmv"] - a0["gmv"]
    names = {"uv": "访客数", "cvr": "支付转化率", "aov": "客单价"}
    factors = []
    for f in ["uv", "cvr", "aov"]:
        c = contrib[f]
        factors.append(dict(factor=f, name=names[f], prev=r(v0[f]), cur=r(v1[f]),
                            change_pct=r((v1[f] / v0[f] - 1) if v0[f] else None),
                            amount=round(c, 2), share=r(c / delta if delta else 0.0)))
    # 客单价二级
    s1 = {"aov": a1["aov"], "unit_price": a1["unit_price"], "upb": a1["units_per_buyer"]}
    s0 = {"aov": a0["aov"], "unit_price": a0["unit_price"], "upb": a0["units_per_buyer"]}
    sub, _ = lmdi(s1, s0, ["unit_price", "upb"], "aov") if a0["buyers"] and a1["buyers"] else ({}, "")
    return dict(product_id=pid, window_days=window, cur_window=fmt_window(cur_w), prev_window=fmt_window(prev_w),
                gmv_prev=round(a0["gmv"], 2), gmv_cur=round(a1["gmv"], 2), gmv_change=round(delta, 2),
                gmv_change_pct=r(delta / a0["gmv"] if a0["gmv"] else None), method=method, factors=factors,
                aov_detail=dict(unit_price_prev=r(a0["unit_price"], 2), unit_price_cur=r(a1["unit_price"], 2),
                                units_per_buyer_prev=r(a0["units_per_buyer"], 3),
                                units_per_buyer_cur=r(a1["units_per_buyer"], 3),
                                contrib_unit_price=r(sub.get("unit_price"), 4) if sub else None,
                                contrib_units_per_buyer=r(sub.get("upb"), 4) if sub else None),
                check_sum_error=r(abs(sum(contrib.values()) - delta), 4))


def breakdown_channels(ds: Dataset, pid: str, window: int = 7, as_of=None) -> dict:
    if "channel" not in ds.available:
        return dict(product_id=pid, available=False, note="数据中没有渠道访客，无法按渠道下钻")
    as_of = as_of or ds.as_of
    cur_w, prev_w = windows(as_of, window)
    dc = ds.cdays(pid)
    c1 = dc[(dc["date"] >= cur_w[0]) & (dc["date"] <= cur_w[1])].groupby("channel")["uv"].sum()
    c0 = dc[(dc["date"] >= prev_w[0]) & (dc["date"] <= prev_w[1])].groupby("channel")["uv"].sum()
    chans = sorted(set(c1.index) | set(c0.index))
    tot1, tot0 = float(c1.sum()), float(c0.sum())
    dec = decompose_gmv(ds, pid, window, as_of)
    c_uv = next(f["amount"] for f in dec["factors"] if f["factor"] == "uv")
    rows = []
    for ch in chans:
        u1, u0 = float(c1.get(ch, 0)), float(c0.get(ch, 0))
        rows.append(dict(channel=ch, name=config.channel_name(ch), uv_prev=int(u0), uv_cur=int(u1),
                         daily_prev=round(u0 / window, 1), daily_cur=round(u1 / window, 1),
                         change=int(u1 - u0), change_pct=r((u1 / u0 - 1) if u0 else None),
                         share_prev=r(u0 / tot0 if tot0 else 0), share_cur=r(u1 / tot1 if tot1 else 0),
                         gmv_contrib=round(c_uv * (u1 - u0) / (tot1 - tot0), 2) if tot1 != tot0 else 0.0))
    rows.sort(key=lambda x: x["change"])
    return dict(product_id=pid, available=True, cur_window=dec["cur_window"], prev_window=dec["prev_window"],
                uv_prev=int(tot0), uv_cur=int(tot1), channels=rows)


def variant_baseline_share(ds: Dataset, pid: str, before: pd.Timestamp, days: int = 28) -> dict:
    """规格基准销量占比：取 before 之前 days 天中该规格有货（库存>0 或当日有销量）的日子。"""
    v = ds.vdays(pid)
    if v.empty:
        return {}
    w = v[(v["date"] < before) & (v["date"] >= before - days * DAY)]
    tot = w.groupby("date")["units"].sum()
    out = {}
    for vid, g in w.groupby("variant_id"):
        if "stock" in ds.available:
            ok = g[(g["stock_units"] > 0) | (g["units"] > 0)]
        else:
            ok = g
        denom = tot.reindex(ok["date"]).sum()
        out[vid] = float(ok["units"].sum() / denom) if denom else 0.0
    return out


def breakdown_variants(ds: Dataset, pid: str, window: int = 7, as_of=None) -> dict:
    if "variant" not in ds.available:
        return dict(product_id=pid, available=False, note="数据中没有规格明细，无法按规格下钻")
    as_of = as_of or ds.as_of
    cur_w, prev_w = windows(as_of, window)
    v = ds.vdays(pid)
    base_share = variant_baseline_share(ds, pid, cur_w[0])
    g = ds.pdays(pid)
    base = g[(g.index < cur_w[0]) & (g.index >= cur_w[0] - 28 * DAY)]
    promo = ds.promo_days()
    base = base[~base.index.isin(promo)]
    base_daily_gmv = float(base["gmv"].mean()) if len(base) else 0.0
    rows = []
    has_stock = "stock" in ds.available
    for vid, gv in v.groupby("variant_id"):
        gv = gv.set_index("date").sort_index()
        a1 = gv.loc[(gv.index >= cur_w[0]) & (gv.index <= cur_w[1])]
        a0 = gv.loc[(gv.index >= prev_w[0]) & (gv.index <= prev_w[1])]
        name = str(gv["variant_name"].iloc[0])
        row = dict(variant_id=vid, name=name, units_prev=int(a0["units"].sum()), units_cur=int(a1["units"].sum()),
                   baseline_share=r(base_share.get(vid, 0.0)))
        tot1 = v[(v["date"] >= cur_w[0]) & (v["date"] <= cur_w[1])]["units"].sum()
        tot0 = v[(v["date"] >= prev_w[0]) & (v["date"] <= prev_w[1])]["units"].sum()
        row["share_prev"] = r(row["units_prev"] / tot0 if tot0 else 0)
        row["share_cur"] = r(row["units_cur"] / tot1 if tot1 else 0)
        if has_stock:
            so = a1[(a1["stock_units"] == 0) & (a1["units"] == 0)]
            last7 = gv.loc[gv.index > as_of - 7 * DAY, "units"].mean()
            stock_now = int(gv["stock_units"].iloc[-1])
            row.update(stock_now=stock_now,
                       stockout_days=int(len(so)),
                       stockout_dates=[x.strftime("%m-%d") for x in so.index],
                       avg_daily_units_7d=round(float(last7), 1),
                       days_of_supply=(round(stock_now / last7, 1) if last7 > 0 else None),
                       loss_estimate=round(base_daily_gmv * base_share.get(vid, 0.0) * len(so), 2))
        rows.append(row)
    return dict(product_id=pid, available=True, has_stock=has_stock, cur_window=fmt_window(cur_w),
                prev_window=fmt_window(prev_w), variants=rows)
