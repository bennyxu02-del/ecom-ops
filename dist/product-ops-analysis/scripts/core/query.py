"""按口径取数、查商品、查库存。平台与 Skill 共用；只读数据，不改变任何已有计算。

口径全部来自 methods/metrics.yaml：窗口类比率先对分子、分母分别求和再相除，不对日比率求平均；
多日访客、买家为日值之和，不跨日去重。
"""
from __future__ import annotations

import math

import pandas as pd

from . import config
from .loader import Dataset
from .metrics import DAY, agg, r, safe_div

ADDITIVE = ["gmv", "uv", "buyers", "units", "refund_amount"]
RATIO = ["cvr", "aov", "unit_price", "units_per_buyer", "refund_rate"]
POINT = ["price", "comp_price", "price_index", "rating", "margin"]      # 期末值，只对单个商品有意义
ALL = ADDITIVE + RATIO + POINT
EXTRA_NAMES = {"refund_amount": ("退款金额", "元", "Σ 退款金额", "按退款发生日归日"),
               "comp_price": ("竞品到手价", "元", "对标竞品当日到手价", "单个商品的期末值")}
PERIODS = ["last_7d", "last_30d", "this_week", "last_week", "this_month", "last_month"]
BY = ["none", "product", "category", "sub_category", "tier", "channel", "variant", "day", "week"]


class QueryError(Exception):
    pass


def metric_def(mid: str) -> dict:
    for m in config.metrics()["metrics"]:
        if m["id"] == mid:
            return dict(id=mid, name=m["name"], unit=m.get("unit", ""), formula=m["formula"], note=m.get("note", ""))
    if mid in EXTRA_NAMES:
        n, u, f, note = EXTRA_NAMES[mid]
        return dict(id=mid, name=n, unit=u, formula=f, note=note)
    raise QueryError(f"不支持的指标 {mid}，可用：{', '.join(ALL)}")


# ---------------------------------------------------------------------------
# 时间与范围
# ---------------------------------------------------------------------------
def _day(s) -> pd.Timestamp:
    try:
        return pd.Timestamp(s).normalize()
    except Exception as e:  # noqa: BLE001
        raise QueryError(f"日期格式不对：{s}（应为 YYYY-MM-DD）") from e


def named_period(ds: Dataset, name: str):
    """常用时间口径，以数据最后一天为「今天」。返回 (本期, 对比期或 None)。自然周从周一开始。"""
    t = ds.as_of
    if name not in PERIODS:
        raise QueryError(f"不支持的时间口径 {name}，可用：{', '.join(PERIODS)}")
    if name == "last_7d":
        return (t - 6 * DAY, t), None
    if name == "last_30d":
        return (t - 29 * DAY, t), None
    mon = t - pd.Timedelta(days=t.weekday())
    if name == "this_week":
        return (mon, t), (mon - 7 * DAY, t - 7 * DAY)
    if name == "last_week":
        # 数据最后一天正好是周日时，「上周」就是刚结束的这一周
        end = t if t.weekday() == 6 else mon - DAY
        return (end - 6 * DAY, end), (end - 13 * DAY, end - 7 * DAY)
    m1 = t.replace(day=1)
    if name == "this_month":
        pm1 = (m1 - DAY).replace(day=1)
        pe = min(pm1 + (t - m1), m1 - DAY)
        return (m1, t), (pm1, pe)
    e = m1 - DAY
    s = e.replace(day=1)
    pe = s - DAY
    return (s, e), (pe.replace(day=1), pe)


def period(ds: Dataset, start=None, end=None, days: int | None = None):
    lo, hi = ds.dp["date"].min(), ds.as_of
    e = _day(end) if end else hi
    s = _day(start) if start else e - ((days or 7) - 1) * DAY
    if s > e:
        raise QueryError("开始日期晚于结束日期")
    notes = []
    if e > hi:
        notes.append(f"数据截至 {hi:%Y-%m-%d}，结束日期按 {hi:%Y-%m-%d} 计")
        e = hi
    if s < lo:
        notes.append(f"数据从 {lo:%Y-%m-%d} 开始，开始日期按 {lo:%Y-%m-%d} 计")
        s = lo
    return (s, e), notes


def compare_period(ds: Dataset, cur, compare="prev", cstart=None, cend=None):
    """compare：prev（上一个等长周期，默认）/ none / custom（配合 cstart、cend）。"""
    if compare == "none":
        return None, []
    if cstart or cend:
        if not (cstart and cend):
            raise QueryError("对比期需要同时给出开始和结束日期")
        w = (_day(cstart), _day(cend))
    else:
        n = (cur[1] - cur[0]).days + 1
        w = (cur[0] - n * DAY, cur[0] - DAY)
    lo = ds.dp["date"].min()
    if w[1] < lo:
        return None, [f"对比期 {w[0]:%Y-%m-%d} 至 {w[1]:%Y-%m-%d} 早于数据开始日期，无法对比"]
    if w[0] < lo:
        return (lo, w[1]), [f"对比期数据不完整：只有 {lo:%Y-%m-%d} 之后的数据，对比期按 {lo:%Y-%m-%d} 至 {w[1]:%Y-%m-%d} 计，天数少于本期"]
    return w, []


def match_products(ds: Dataset, products=None, category=None) -> list[str]:
    """products：商品编号或名称关键词列表；category：品类或子品类关键词。"""
    p = ds.products
    ids = list(p["product_id"])
    if products:
        keep = []
        for k in products:
            k = str(k).strip()
            if not k:
                continue
            hit = p[(p["product_id"] == k) | p["product_name"].astype(str).str.contains(k, regex=False)]
            if hit.empty:
                raise QueryError(f"找不到商品「{k}」，可先用 products 命令查看商品列表")
            keep += [x for x in hit["product_id"] if x not in keep]
        ids = keep
    if category:
        c = str(category)
        m = p[p["category"].astype(str).str.contains(c, regex=False) | p["sub_category"].astype(str).str.contains(c, regex=False)]
        ids = [x for x in ids if x in set(m["product_id"])]
        if not ids:
            raise QueryError(f"没有品类或子品类包含「{c}」的商品")
    return ids


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------
def _slice(df: pd.DataFrame, w) -> pd.DataFrame:
    return df[(df["date"] >= w[0]) & (df["date"] <= w[1])]


def _values(ds: Dataset, dp: pd.DataFrame, pids: list[str], w, single: bool) -> dict:
    d = _slice(dp[dp["product_id"].isin(pids)], w).set_index("date")
    a = agg(d)
    out = {k: a[k] for k in ("gmv", "uv", "buyers", "units", "cvr", "aov", "unit_price", "units_per_buyer", "refund_rate")}
    out["refund_amount"] = round(float(d["refund_amount"].sum()), 2) if "refund_amount" in d.columns else None
    if "refund_amount" not in ds.available:
        out["refund_amount"] = out["refund_rate"] = None
    for k in POINT:
        out[k] = None
    if single and len(d):
        last = d.sort_index().iloc[-1]
        price = float(last["price"]) if "price" in d.columns and pd.notna(last["price"]) else None
        comp = float(last["comp_price"]) if "comp_price" in d.columns and pd.notna(last["comp_price"]) else None
        out["price"], out["comp_price"] = price, comp
        out["price_index"] = r(price / comp, 3) if price and comp else None
        out["rating"] = float(last["rating"]) if "rating" in d.columns and pd.notna(last["rating"]) else None
        cost = ds.product(pids[0]).get("cost_price")
        if price and cost is not None and not (isinstance(cost, float) and math.isnan(cost)):
            out["margin"] = r(safe_div(price - float(cost), price))
    out["days"] = int(d.index.nunique())
    return out


def _clean(v, mid):
    if v is None:
        return None
    if mid in ("gmv", "refund_amount", "aov", "unit_price", "price", "comp_price"):
        return round(float(v), 2)
    if mid in ("uv", "buyers", "units"):
        return int(v)
    return r(v, 4)


def _row(key, name, cur: dict, prev: dict | None, metrics: list[str]) -> dict:
    row = dict(key=key, name=name, days=cur.get("days"), values={})
    for m in metrics:
        c = _clean(cur.get(m), m)
        item = dict(cur=c)
        if prev is not None:
            p = _clean(prev.get(m), m)
            item["prev"] = p
            if c is not None and p is not None:
                item["diff"] = _clean(c - p, m) if m in ADDITIVE + ["aov", "unit_price", "price", "comp_price"] else r(c - p, 4)
                item["change"] = r(c / p - 1, 4) if p else None
        row["values"][m] = item
    return row


def _groups(ds: Dataset, pids: list[str], by: str) -> list[tuple[str, str, list[str]]]:
    p = ds.products[ds.products["product_id"].isin(pids)]
    if by == "none":
        return [("ALL", "全部商品" if len(pids) == len(ds.products) else "所选商品", pids)]
    if by == "product":
        return [(x.product_id, str(x.product_name), [x.product_id]) for x in p.itertuples()]
    if by in ("category", "sub_category"):
        return [(str(k) or "未填", str(k) or "未填", list(g["product_id"])) for k, g in p.groupby(p[by].fillna("").astype(str))]
    if by == "tier":
        from .tiering import compute
        t = compute(ds)
        out = {}
        for pid in pids:
            out.setdefault(t[pid]["tier_name"], []).append(pid)
        order = ["爆品", "潜力品", "利润品", "长尾品"]
        return [(k, k, out[k]) for k in order if k in out]
    raise QueryError(f"不支持的分组 {by}")


def query(ds: Dataset, metrics=None, start=None, end=None, days: int | None = None, compare: str = "prev",
          compare_start=None, compare_end=None, by: str = "none", products=None, category=None,
          sort: str | None = None, sort_by: str = "value", order: str = "desc", top: int | None = None,
          period_name: str | None = None) -> dict:
    metrics = [m.strip() for m in (metrics or ["gmv"]) if m and m.strip()]
    for m in metrics:
        metric_def(m)
    if by not in BY:
        raise QueryError(f"不支持的分组 {by}，可用：{', '.join(BY)}")
    if period_name:
        (ps, pe), pc = named_period(ds, period_name)
        start, end, days = f"{ps:%Y-%m-%d}", f"{pe:%Y-%m-%d}", None
        if pc and compare != "none" and not (compare_start or compare_end):
            compare_start, compare_end = f"{pc[0]:%Y-%m-%d}", f"{pc[1]:%Y-%m-%d}"
    cur, notes = period(ds, start, end, days)
    pids = match_products(ds, products, category)
    if by in ("day", "week"):
        compare = "none"
    prev, n2 = compare_period(ds, cur, compare, compare_start, compare_end)
    notes += n2

    if by == "channel":
        rows, total = _by_channel(ds, pids, cur, prev, metrics)
    elif by == "variant":
        rows, total = _by_variant(ds, pids, cur, prev, metrics)
    elif by in ("day", "week"):
        rows, total = _by_time(ds, pids, cur, metrics, by)
    else:
        rows = []
        for key, name, g in _groups(ds, pids, by):
            single = len(g) == 1
            rows.append(_row(key, name, _values(ds, ds.dp, g, cur, single),
                             _values(ds, ds.dp, g, prev, single) if prev else None, metrics))
        single_all = len(pids) == 1
        total = _row("TOTAL", "合计", _values(ds, ds.dp, pids, cur, single_all),
                     _values(ds, ds.dp, pids, prev, single_all) if prev else None, metrics) if by != "none" else None
        if total:
            for row in rows:
                for m in metrics:
                    if m in ADDITIVE and total["values"][m].get("cur"):
                        c = row["values"][m].get("cur")
                        row["values"][m]["share"] = r(c / total["values"][m]["cur"], 4) if c is not None else None
                    td = total["values"][m].get("diff")
                    if m in ADDITIVE and td and row["values"][m].get("diff") is not None:
                        row["values"][m]["diff_share"] = r(row["values"][m]["diff"] / td, 4)   # 占整体变化的比例
    ncur = (cur[1] - cur[0]).days + 1
    nprev = (prev[1] - prev[0]).days + 1 if prev else None
    if prev and nprev != ncur:
        for row in rows + ([total] if total else []):
            for m in metrics:
                it = row["values"][m]
                if m in ADDITIVE and it.get("cur") is not None and it.get("prev") is not None:
                    it["cur_daily"] = _clean(it["cur"] / ncur, m if m != "uv" else "gmv")
                    it["prev_daily"] = _clean(it["prev"] / nprev, m if m != "uv" else "gmv")
                    it["change"] = r(it["cur_daily"] / it["prev_daily"] - 1, 4) if it["prev_daily"] else None
                    it["diff"] = _clean(it["cur_daily"] - it["prev_daily"], m if m != "uv" else "gmv")
                    it["change_basis"] = "日均"
        if total:
            for row in rows:
                for m in metrics:
                    td = total["values"][m].get("diff")
                    if m in ADDITIVE and td and row["values"][m].get("diff") is not None:
                        row["values"][m]["diff_share"] = r(row["values"][m]["diff"] / td, 4)
        notes.append(f"本期 {ncur} 天、对比期 {nprev} 天，天数不同：金额、访客、件数等的变化按日均计算，"
                     f"diff 是日均之差、change 是日均的变化幅度；cur / prev 仍是两段各自的总量")
    if any(m in POINT for m in metrics) and by not in ("product", "day", "week") and len(pids) > 1:
        notes.append("到手价、竞品价、评分、毛利率是单个商品的期末值，多商品汇总时不计算；请按商品分组查看")

    if by not in ("day", "week"):
        key = sort or metrics[0]
        if key not in metrics:
            raise QueryError("排序指标必须在所查指标里")
        field = {"value": "cur", "change": "change", "diff": "diff"}.get(sort_by)
        if field is None:
            raise QueryError("sort_by 只能是 value / change / diff")
        pool = [x for x in rows if x["values"][key].get(field) is not None]
        rest = [x for x in rows if x["values"][key].get(field) is None]
        pool.sort(key=lambda x: x["values"][key][field], reverse=(order != "asc"))
        rows = pool + rest
        if top:
            rows = rows[:int(top)]

    promo = sorted(d for d in ds.promo_days() if cur[0] <= d <= cur[1] or (prev and prev[0] <= d <= prev[1]))
    if promo:
        notes.append("所选时间含大促日 " + "、".join(f"{d:%m-%d}" for d in promo) + "，环比会受大促影响")
    missing = [m for m in metrics if m in ("refund_amount", "refund_rate") and "refund_amount" not in ds.available]
    missing +=[m for m in metrics if m in ("price", "price_index") and "price" not in ds.available]
    missing += [m for m in metrics if m in ("comp_price", "price_index") and "comp_price" not in ds.available]
    missing += [m for m in metrics if m == "rating" and "rating" not in ds.available]
    missing += [m for m in metrics if m == "margin" and ("cost_price" not in ds.available or "price" not in ds.available)]
    if missing:
        notes.append("数据里缺少计算这些指标所需的字段：" + "、".join(sorted({metric_def(m)["name"] for m in missing})))

    return dict(as_of=f"{ds.as_of:%Y-%m-%d}", period=dict(start=f"{cur[0]:%Y-%m-%d}", end=f"{cur[1]:%Y-%m-%d}",
                                                          days=(cur[1] - cur[0]).days + 1),
                compare_period=dict(start=f"{prev[0]:%Y-%m-%d}", end=f"{prev[1]:%Y-%m-%d}", days=(prev[1] - prev[0]).days + 1)
                if prev else None,
                by=by, products_in_scope=len(pids), definitions=[metric_def(m) for m in metrics],
                rows=rows, total=total, notes=notes)


def _by_channel(ds, pids, cur, prev, metrics):
    if "channel" not in ds.available:
        raise QueryError("数据里没有分渠道访客，无法按渠道拆分")
    bad = [m for m in metrics if m != "uv"]
    if bad:
        raise QueryError("按渠道只能查访客数（uv）：数据里只有分渠道的访客")
    dc = ds.dc[ds.dc["product_id"].isin(pids)]
    names = config.metrics().get("channels", {})

    def s(w):
        x = _slice(dc, w)
        return x.groupby("channel")["uv"].sum() if len(x) else pd.Series(dtype=float)
    c, p = s(cur), (s(prev) if prev else None)
    tot_c = float(c.sum())
    rows = []
    for ch in sorted(set(c.index) | set(p.index if p is not None else [])):
        row = _row(ch, names.get(ch, ch), dict(uv=c.get(ch, 0)), dict(uv=p.get(ch, 0)) if p is not None else None, ["uv"])
        row["values"]["uv"]["share"] = r(c.get(ch, 0) / tot_c, 4) if tot_c else None
        rows.append(row)
    total = _row("TOTAL", "合计", dict(uv=tot_c), dict(uv=float(p.sum())) if p is not None else None, ["uv"])
    return rows, total


def _by_variant(ds, pids, cur, prev, metrics):
    if "variant" not in ds.available:
        raise QueryError("数据里没有规格明细，无法按规格拆分")
    bad = [m for m in metrics if m not in ("units", "gmv")]
    if bad:
        raise QueryError("按规格只能查支付件数（units）和 GMV（gmv）")
    if len(pids) > 5:
        raise QueryError("按规格拆分请先指定商品（最多 5 个）")
    dv = ds.dv[ds.dv["product_id"].isin(pids)]
    pname = dict(zip(ds.products["product_id"], ds.products["product_name"]))

    def s(w):
        x = _slice(dv, w)
        return x.groupby(["product_id", "variant_name"])[["units", "gmv"]].sum()
    c, p = s(cur), (s(prev) if prev else None)
    rows = []
    for (pid, vn), v in c.iterrows():
        pv = p.loc[(pid, vn)] if p is not None and (pid, vn) in p.index else None
        rows.append(_row(f"{pid}/{vn}", f"{pname.get(pid, pid)} · {vn}", dict(v),
                         dict(pv) if pv is not None else ({"units": 0, "gmv": 0} if p is not None else None), metrics))
    tc = c.sum()
    total = _row("TOTAL", "合计", dict(tc), dict(p.sum()) if p is not None else None, metrics)
    for row in rows:
        for m in metrics:
            row["values"][m]["share"] = r(row["values"][m]["cur"] / tc[m], 4) if tc[m] else None
    return rows, total


def _by_time(ds, pids, cur, metrics, by):
    d = _slice(ds.dp[ds.dp["product_id"].isin(pids)], cur)
    single = len(pids) == 1
    rows = []
    if by == "day":
        keys = sorted(d["date"].unique())
        for k in keys:
            k = pd.Timestamp(k)
            rows.append(_row(f"{k:%Y-%m-%d}", f"{k:%m-%d}", _values(ds, ds.dp, pids, (k, k), single), None, metrics))
    else:
        start = cur[0] - pd.Timedelta(days=cur[0].weekday())     # 自然周，周一开始
        k = start
        while k <= cur[1]:
            w = (max(k, cur[0]), min(k + 6 * DAY, cur[1]))
            row = _row(f"{w[0]:%Y-%m-%d}", f"{w[0]:%m-%d}–{w[1]:%m-%d}", _values(ds, ds.dp, pids, w, single), None, metrics)
            row["partial"] = (w[1] - w[0]).days + 1 < 7
            rows.append(row)
            k += 7 * DAY
    total = _row("TOTAL", "合计", _values(ds, ds.dp, pids, cur, single), None, metrics)
    return rows, total


# ---------------------------------------------------------------------------
# 商品列表
# ---------------------------------------------------------------------------
def list_products(ds: Dataset, keyword: str | None = None) -> dict:
    from .tiering import compute
    t = compute(ds)
    p = ds.products
    if keyword:
        k = str(keyword)
        p = p[p["product_id"].astype(str).str.contains(k, regex=False) | p["product_name"].astype(str).str.contains(k, regex=False)
              | p["category"].astype(str).str.contains(k, regex=False) | p["sub_category"].astype(str).str.contains(k, regex=False)]
    w = (ds.as_of - 6 * DAY, ds.as_of)
    rows = []
    for x in p.itertuples():
        v = _values(ds, ds.dp, [x.product_id], w, True)
        rows.append(dict(product_id=x.product_id, product_name=str(x.product_name), category=str(x.category or ""),
                         sub_category=str(x.sub_category or ""), launch_date=f"{pd.Timestamp(x.launch_date):%Y-%m-%d}",
                         tier=t[x.product_id]["tier_name"], lifecycle=t[x.product_id]["lifecycle_name"],
                         gmv_7d=round(v["gmv"], 2), units_7d=int(v["units"])))
    rows.sort(key=lambda x: x["gmv_7d"], reverse=True)
    return dict(as_of=f"{ds.as_of:%Y-%m-%d}", count=len(rows), products=rows,
                note="分层按近 28 天 GMV 等规则计算，规则见 references/tiering.md")


# ---------------------------------------------------------------------------
# 库存
# ---------------------------------------------------------------------------
def stock_status(ds: Dataset, products=None, category=None) -> dict:
    if "stock" not in ds.available:
        return dict(available=False, note="数据里没有规格库存（需要规格日报里的库存字段），无法查库存")
    from .actions import _baseline
    from .alerts import in_transit
    from .decompose import breakdown_variants
    prof, c = ds.profile, ds.profile["constraints"]
    lead, safety = prof["replenish_lead_days"], c["safety_days"]
    rows = []
    for pid in match_products(ds, products, category):
        bv = breakdown_variants(ds, pid)
        if not bv.get("available") or not bv.get("has_stock"):
            continue
        base = None
        pname = ds.product(pid)["product_name"]
        for v in bv["variants"]:
            it = in_transit(ds, pid, v["name"], ds.as_of)
            tq = int(it["qty"] or 0) if it else 0
            if v["stock_now"] == 0:
                status = "断货"
                if base is None:
                    base = _baseline(ds, pid)
                daily = base["daily_units"] * (v["baseline_share"] or 0)
                basis = "断货前基线日均（剔除大促日）× 该规格正常销量占比"
            else:
                daily = v["avg_daily_units_7d"]
                basis = "近 7 日日均销量"
                dos = v["days_of_supply"]
                status = "偏低" if dos is not None and dos < lead else "充足"
            qty = int(math.ceil(daily * (lead + safety) - v["stock_now"] - tq)) if status != "充足" else 0
            rows.append(dict(product_id=pid, product_name=pname, variant=v["name"], status=status, stock_now=v["stock_now"],
                             daily_units=round(float(daily), 1), daily_basis=basis, days_of_supply=v["days_of_supply"],
                             stockout_days_7d=v["stockout_days"], in_transit=it,
                             suggest_qty=max(qty, 0) if status != "充足" else None))
    rank = {"断货": 0, "偏低": 1, "充足": 2}
    rows.sort(key=lambda x: (rank[x["status"]], x["days_of_supply"] if x["days_of_supply"] is not None else 1e9))
    return dict(available=True, as_of=f"{ds.as_of:%Y-%m-%d}", replenish_lead_days=lead, safety_days=safety,
                rule=f"可售天数 = 当前库存 ÷ 近 7 日日均销量；低于补货周期 {lead} 天为「偏低」。"
                     f"建议补货量 = 日均销量 ×（补货周期 {lead} 天 + 安全库存 {safety} 天）− 当前库存 − 在途量",
                summary=dict(断货=sum(1 for x in rows if x["status"] == "断货"), 偏低=sum(1 for x in rows if x["status"] == "偏低"),
                             充足=sum(1 for x in rows if x["status"] == "充足")),
                variants=rows)
