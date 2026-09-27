"""图表库：报告里的每张图都由这里生成。

- AI 只能通过 draw() 画图，并且只传参数（画什么图、哪些商品、哪些指标、时间范围、标注）；
  图里的每个数字都由这里从平台数据中取出，AI 不能直接传数字。
- 参数先经过检查，不合格时返回 {"error": ...}，并说明可以怎么改。
- 输出是「图表配置」（JSON）：前端 / Skill 的 HTML 报告用同一段渲染代码（web/src/lib/reportCharts.js）画成 ECharts 图。
- 每张图附带 summary（给 AI 写解读用的数据摘要）和 values（图里全部数字，用于核对报告正文）。

平台与 Skill 共用本文件。
"""
from __future__ import annotations

import math
import re

import pandas as pd

from . import config, tiering
from .decompose import lmdi
from .loader import Dataset
from .metrics import DAY

TYPES = {"kpi": "指标卡", "trend": "趋势折线", "waterfall": "瀑布图", "stacked_bar": "堆叠柱",
         "grouped_bar": "分组柱", "hbar": "横向条形", "dual_line": "双折线"}

# id: (名称, 单位, 是否可以按「全部商品」汇总, 是否是可加总的量)
METRICS = {
    "gmv": ("GMV", "money", True, True),
    "uv": ("访客数", "num", True, True),
    "buyers": ("支付买家数", "num", True, True),
    "units": ("销量", "num", True, True),
    "cvr": ("支付转化率", "pct", True, False),
    "aov": ("客单价", "price", True, False),
    "refund_rate": ("退款率", "pct", True, False),
    "margin": ("毛利率", "pct", True, False),
    "price": ("到手价", "price", False, False),
    "comp_price": ("竞品到手价", "price", False, False),
    "price_index": ("价格指数", "dec", False, False),
    "rating": ("评分", "dec", False, False),
    "stock": ("库存", "num", False, True),
}
UNIT_NAME = {"money": "元", "num": "", "pct": "%", "price": "元", "dec": ""}
ALL = "ALL"

TOOL_SPEC = {
    "name": "draw_chart",
    "description": (
        "画一张图支撑你的结论。你只传参数，图里的数据由平台提供，你不能传数字。"
        "图表类型：trend 趋势折线（1 个商品的 1–2 个同单位指标，或 1–5 个商品的 1 个指标，按天）；"
        "dual_line 双折线（1 个商品的 2 个指标，单位不同时左右两个坐标轴，如 rating + refund_rate、price + comp_price + cvr）；"
        "hbar 横向条形（多个商品的 1 个指标，对比期 → 本期的变化）；"
        "grouped_bar 分组柱（by=channel 渠道访客 / by=variant 规格销量 / by=product 多个商品的 1 个指标，对比期 vs 本期日均）；"
        "waterfall 瀑布图（GMV 变化拆解：by=factor 拆成访客、转化、客单价，by=product 拆到商品）；"
        "stacked_bar 堆叠柱（by=tier 各分层 GMV 占比，按周；by=variant 1 个商品各规格销量，按周）；"
        "kpi 指标卡（1 个商品或全部商品的 2–6 个指标，本期 vs 对比期）。"
        "调用成功返回 chart_id 和数据摘要，请在正文相应位置写 [图表:chart_id]，并在图下根据摘要写解读。"),
    "parameters": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(TYPES)},
            "products": {"type": "array", "items": {"type": "string"},
                         "description": "商品编号列表，如 [\"S01\"]；全部商品写 [\"ALL\"]"},
            "metrics": {"type": "array", "items": {"type": "string", "enum": list(METRICS)},
                        "description": "指标，只能从枚举中选"},
            "by": {"type": "string", "enum": ["factor", "product", "channel", "variant", "tier"],
                   "description": "waterfall / grouped_bar / stacked_bar 的拆分方式"},
            "start": {"type": "string", "description": "本期开始日期 YYYY-MM-DD，默认数据截止日往前 28 天"},
            "end": {"type": "string", "description": "本期结束日期 YYYY-MM-DD，默认数据截止日"},
            "compare_start": {"type": "string", "description": "对比期开始日期，默认本期之前等长的一段"},
            "compare_end": {"type": "string", "description": "对比期结束日期"},
            "mark_date": {"type": "string", "description": "可选：在这一天画一条竖线，如事件日期"},
            "mark_text": {"type": "string", "description": "竖线的说明，10 个字以内"},
            "band_start": {"type": "string", "description": "可选：给一段时间加底色（如活动期）"},
            "band_end": {"type": "string"},
            "band_text": {"type": "string"},
            "title": {"type": "string", "description": "图的标题，写结论而不是主题，25 个字以内"},
        },
        "required": ["type", "products", "title"],
    },
}


class ChartError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------
def _pids(ds: Dataset, products) -> list[str]:
    if not products:
        raise ChartError("缺少 products：请传商品编号列表，全部商品写 [\"ALL\"]")
    if isinstance(products, str):
        products = [products]
    if ALL in products:
        return ds.product_ids()
    known = set(ds.product_ids())
    bad = [p for p in products if p not in known]
    if bad:
        opts = "、".join(f"{p} {ds.product(p)['product_name']}" for p in ds.product_ids())
        raise ChartError(f"商品 {'、'.join(bad)} 不存在。可选商品：{opts}")
    return list(dict.fromkeys(products))


def is_all(ds: Dataset, pids: list[str]) -> bool:
    return len(pids) > 1 and set(pids) == set(ds.product_ids())


def name_of(ds: Dataset, pids: list[str]) -> str:
    if is_all(ds, pids):
        return "全部商品"
    return "、".join(ds.product(p)["product_name"] for p in pids)


def frame(ds: Dataset, pids: list[str]) -> pd.DataFrame:
    """按天汇总：uv / buyers / units / gmv / refund / cost；单个商品时带 price / comp_price / rating。"""
    parts = []
    for pid in pids:
        g = ds.pdays(pid).copy()
        cost = float(ds.product(pid).get("cost_price") or 0)
        g["cost"] = g["units"] * cost
        parts.append(g)
    cols = ["uv", "buyers", "units", "gmv", "refund_amount", "cost"]
    df = pd.concat(parts)
    out = df[cols].groupby(level=0).sum().sort_index()
    if len(pids) == 1:
        for c in ("price", "comp_price", "rating"):
            if c in df.columns:
                out[c] = df[c]
    return out


def series(ds: Dataset, pids: list[str], metric: str) -> pd.Series:
    """按天的指标序列。"""
    if metric == "stock":
        raise ChartError("库存按规格看，请用 trend 并只传 1 个商品、metrics=[\"stock\"]")
    f = frame(ds, pids)
    return _metric_from(f, metric)


def _metric_from(f: pd.DataFrame, metric: str) -> pd.Series:
    nz = lambda s: s.replace(0, float("nan"))  # noqa: E731
    if metric in ("gmv", "uv", "buyers", "units"):
        return f[metric]
    if metric == "cvr":
        return f["buyers"] / nz(f["uv"])
    if metric == "aov":
        return f["gmv"] / nz(f["buyers"])
    if metric == "refund_rate":
        return f["refund_amount"] / nz(f["gmv"])
    if metric == "margin":
        return 1 - f["cost"] / nz(f["gmv"])
    if metric in ("price", "comp_price", "rating"):
        if metric not in f.columns:
            raise ChartError(f"{METRICS[metric][0]}只能按单个商品看")
        return f[metric]
    if metric == "price_index":
        if "price" not in f.columns:
            raise ChartError("价格指数只能按单个商品看")
        return f["price"] / nz(f["comp_price"])
    raise ChartError(f"未知指标 {metric}")


def window_value(ds: Dataset, pids: list[str], metric: str, w) -> float | None:
    """窗口值：可加总的量取日均，比率先分别求和再相除，价格与评分取均值。"""
    f = frame(ds, pids)
    f = f[(f.index >= w[0]) & (f.index <= w[1])]
    if f.empty:
        return None
    n = len(f)
    s = f.sum(numeric_only=True)
    div = lambda a, b: float(a) / float(b) if b else None  # noqa: E731
    if metric in ("gmv", "uv", "buyers", "units"):
        return float(s[metric]) / n
    if metric == "cvr":
        return div(s["buyers"], s["uv"])
    if metric == "aov":
        return div(s["gmv"], s["buyers"])
    if metric == "refund_rate":
        return div(s["refund_amount"], s["gmv"])
    if metric == "margin":
        return None if not s["gmv"] else 1 - float(s["cost"]) / float(s["gmv"])
    return float(_metric_from(f, metric).mean())


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
def _date(ds: Dataset, s, what: str) -> pd.Timestamp | None:
    if s in (None, ""):
        return None
    try:
        d = pd.Timestamp(str(s)[:10])
    except Exception:  # noqa: BLE001
        raise ChartError(f"{what}「{s}」不是日期，请用 YYYY-MM-DD")
    lo = ds.dp["date"].min()
    if d < lo or d > ds.as_of:
        raise ChartError(f"{what} {d:%Y-%m-%d} 超出数据范围（{lo:%Y-%m-%d} 至 {ds.as_of:%Y-%m-%d}）")
    return d


def periods(ds: Dataset, a: dict, default_days: int = 28):
    end = _date(ds, a.get("end"), "end") or ds.as_of
    start = _date(ds, a.get("start"), "start") or end - (default_days - 1) * DAY
    if start > end:
        raise ChartError("start 不能晚于 end")
    n = (end - start).days + 1
    ce = _date(ds, a.get("compare_end"), "compare_end") or start - DAY
    cs = _date(ds, a.get("compare_start"), "compare_start") or ce - (n - 1) * DAY
    lo = ds.dp["date"].min()
    cs = max(cs, lo)
    if cs > ce:
        raise ChartError("对比期没有数据，请指定 compare_start / compare_end，或把本期往后放")
    return (start, end), (cs, ce)


def _mm(d) -> str:
    return pd.Timestamp(d).strftime("%m-%d")


def wlabel(w) -> str:
    return f"{_mm(w[0])}~{_mm(w[1])}" if w[0] != w[1] else _mm(w[0])


def _metrics(a: dict, n_min=1, n_max=1, allow=None) -> list[str]:
    ms = a.get("metrics") or []
    if isinstance(ms, str):
        ms = [ms]
    bad = [m for m in ms if m not in METRICS]
    if bad:
        raise ChartError(f"指标 {'、'.join(bad)} 不在指标字典里。可选：{'、'.join(f'{k}（{v[0]}）' for k, v in METRICS.items())}")
    if allow:
        bad = [m for m in ms if m not in allow]
        if bad:
            raise ChartError(f"这种图不支持指标 {'、'.join(bad)}，可选：{'、'.join(allow)}")
    if not (n_min <= len(ms) <= n_max):
        raise ChartError(f"这种图需要 {n_min}{'' if n_min == n_max else f'–{n_max}'} 个指标，你传了 {len(ms)} 个")
    return ms


def _r(x, n=4):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, n)


def fmt(v, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "pct":
        return f"{v * 100:.2f}%"
    if unit == "money":
        return f"{v / 10000:.2f} 万元" if abs(v) >= 10000 else f"{v:,.0f} 元"
    if unit == "price":
        return f"{v:,.2f} 元"
    if unit == "dec":
        return f"{v:.2f}"
    return f"{v:,.0f}"


def _decor(ds: Dataset, a: dict, spec: dict):
    md = _date(ds, a.get("mark_date"), "mark_date")
    if md is not None:
        spec["marks"] = [dict(x=_mm(md), text=(a.get("mark_text") or "")[:14])]
    bs, be = _date(ds, a.get("band_start"), "band_start"), _date(ds, a.get("band_end"), "band_end")
    if bs is not None and be is not None:
        spec["bands"] = [dict(**{"from": _mm(bs), "to": _mm(be)}, text=(a.get("band_text") or "")[:14])]


def _title(a: dict, default: str) -> str:
    t = (a.get("title") or "").strip()
    return t[:40] if t else default


# ---------------------------------------------------------------------------
# 各类图（AI 调用）
# ---------------------------------------------------------------------------
def _seq_summary(name: str, s: pd.Series, unit: str, mark: pd.Timestamp | None = None) -> tuple[str, list]:
    s = s.dropna()
    if s.empty:
        return f"{name}：无数据", []
    first, last = float(s.iloc[0]), float(s.iloc[-1])
    hi, lo = s.idxmax(), s.idxmin()
    vals = [first, last, float(s.max()), float(s.min()), float(s.mean())]
    t = (f"{name}：首日 {fmt(first, unit)}，末日 {fmt(last, unit)}，最高 {fmt(s.max(), unit)}（{_mm(hi)}），"
         f"最低 {fmt(s.min(), unit)}（{_mm(lo)}），均值 {fmt(s.mean(), unit)}")
    if mark is not None:
        b, a = s[s.index < mark], s[s.index >= mark]
        if len(b) and len(a):
            vals += [float(b.mean()), float(a.mean())]
            t += f"；{_mm(mark)} 之前均值 {fmt(b.mean(), unit)}，之后均值 {fmt(a.mean(), unit)}"
    return t, vals


def _trend(ds, a, pids):
    cur, _ = periods(ds, a)
    if len(pids) > 1 and not is_all(ds, pids):
        ms = _metrics(a, 1, 1)
    else:
        ms = _metrics(a, 1, 2)
    if len(ms) == 2 and METRICS[ms[0]][1] != METRICS[ms[1]][1]:
        raise ChartError("两个指标单位不同，请改用 dual_line")
    idx = pd.date_range(cur[0], cur[1])
    spec = dict(type="trend", x=[_mm(d) for d in idx], series=[], unit=METRICS[ms[0]][1])
    _decor(ds, a, spec)
    mark = _date(ds, a.get("mark_date"), "mark_date")
    sums, vals = [], []
    if ms == ["stock"]:
        if len(pids) != 1:
            raise ChartError("库存只能按 1 个商品看（每个规格一条线）")
        v = ds.vdays(pids[0])
        for vn, g in v.groupby("variant_name"):
            s = g.set_index("date")["stock_units"].reindex(idx)
            spec["series"].append(dict(name=vn, data=[_r(x, 2) for x in s]))
            t, vv = _seq_summary(f"{vn}库存", s, "num", mark)
            sums.append(t)
            vals += vv
        title = f"{name_of(ds, pids)}各规格库存"
    else:
        groups = [[p] for p in pids] if (len(pids) > 1 and not is_all(ds, pids)) else [pids]
        for g in groups:
            for m in ms:
                s = series(ds, g, m).reindex(idx)
                nm = (ds.product(g[0])["product_name"] if len(groups) > 1 else METRICS[m][0])
                spec["series"].append(dict(name=nm, data=[_r(x) for x in s]))
                t, vv = _seq_summary(f"{name_of(ds, g)}{METRICS[m][0]}", s, METRICS[m][1], mark)
                sums.append(t)
                vals += vv
        title = f"{name_of(ds, pids)}{'、'.join(METRICS[m][0] for m in ms)}走势"
    spec["title"] = _title(a, title)
    spec["summary"] = f"{wlabel(cur)} 按天。" + "；".join(sums)
    spec["values"] = vals
    return spec


def _dual(ds, a, pids):
    if len(pids) != 1:
        raise ChartError("双折线只能看 1 个商品")
    ms = _metrics(a, 2, 3)
    units = list(dict.fromkeys(METRICS[m][1] for m in ms))
    if len(units) > 2:
        raise ChartError("双折线最多两种单位（左右两个坐标轴）")
    cur, _ = periods(ds, a)
    idx = pd.date_range(cur[0], cur[1])
    spec = dict(type="dual_line", x=[_mm(d) for d in idx], series=[], axes=[dict(unit=u) for u in units])
    _decor(ds, a, spec)
    mark = _date(ds, a.get("mark_date"), "mark_date")
    sums, vals = [], []
    for m in ms:
        s = series(ds, pids, m).reindex(idx)
        spec["series"].append(dict(name=METRICS[m][0], data=[_r(x) for x in s], axis=units.index(METRICS[m][1]),
                                   dashed=m == "comp_price"))
        t, vv = _seq_summary(METRICS[m][0], s, METRICS[m][1], mark)
        sums.append(t)
        vals += vv
    spec["title"] = _title(a, f"{name_of(ds, pids)}{'与'.join(METRICS[m][0] for m in ms)}")
    spec["summary"] = f"{name_of(ds, pids)}，{wlabel(cur)} 按天。" + "；".join(sums)
    spec["values"] = vals
    return spec


def change_items(ds, pids, metric, cur, prev, measure="change_pct"):
    items = []
    for p in pids:
        v1, v0 = window_value(ds, [p], metric, cur), window_value(ds, [p], metric, prev)
        chg = (v1 / v0 - 1) if (v0 and v1 is not None) else None
        amt = None
        if METRICS[metric][3] and v1 is not None and v0 is not None:
            amt = (v1 - v0) * ((cur[1] - cur[0]).days + 1)      # 窗口内合计变化
        items.append(dict(pid=p, label=ds.product(p)["product_name"], cur=v1, prev=v0, change_pct=chg, change=amt))
    return items


def _hbar(ds, a, pids):
    m = _metrics(a, 1, 1)[0]
    cur, prev = periods(ds, a, 7)
    measure = "change" if (m == "gmv" and a.get("measure") != "change_pct") else "change_pct"
    items = change_items(ds, pids, m, cur, prev)
    key = (lambda x: x["change"] or 0) if measure == "change" else (lambda x: x["change_pct"] or 0)
    items.sort(key=key)
    unit = "money" if measure == "change" else "pct"
    spec = dict(type="hbar", unit=unit, items=[
        dict(label=x["label"], value=_r(key(x), 4 if unit == "pct" else 0),
             tone="bad" if key(x) < 0 else "good") for x in items])
    lines, vals = [], []
    for x in items:
        lines.append(f"{x['label']} {fmt(x['prev'], METRICS[m][1])} → {fmt(x['cur'], METRICS[m][1])}"
                     "（" + ("—" if x["change_pct"] is None else f"{x['change_pct']:+.1%}")
                     + (f"，合计 {x['change']:+,.0f}" if x["change"] is not None else "") + "）")
        vals += [v for v in (x["cur"], x["prev"], x["change_pct"], x["change"]) if v is not None]
    spec["title"] = _title(a, f"{METRICS[m][0]}变化（{wlabel(prev)} → {wlabel(cur)}）")
    spec["summary"] = f"{METRICS[m][0]}，对比期 {wlabel(prev)}，本期 {wlabel(cur)}（日均）：" + "；".join(lines)
    spec["values"] = vals
    return spec


def _grouped(ds, a, pids):
    by = a.get("by") or ("channel" if len(pids) == 1 or is_all(ds, pids) else "product")
    cur, prev = periods(ds, a, 7)
    if by == "channel":
        if "channel" not in ds.available:
            raise ChartError("数据里没有渠道访客，不能按渠道看")
        cats, v0, v1 = channel_avgs(ds, pids, [prev, cur])
        unit, mname = "num", "日均访客"
    elif by == "variant":
        if len(pids) != 1:
            raise ChartError("按规格看只能传 1 个商品")
        v = ds.vdays(pids[0])
        cats = sorted(v["variant_name"].unique())
        avg = lambda w, n: float(v[(v["variant_name"] == n) & (v["date"] >= w[0]) & (v["date"] <= w[1])]["units"].sum()) / ((w[1] - w[0]).days + 1)  # noqa: E731
        v0, v1 = [avg(prev, c) for c in cats], [avg(cur, c) for c in cats]
        unit, mname = "num", "日均销量"
    elif by == "product":
        m = _metrics(a, 1, 1)[0]
        items = change_items(ds, pids, m, cur, prev)
        cats = [x["label"] for x in items]
        v0, v1 = [x["prev"] for x in items], [x["cur"] for x in items]
        unit, mname = METRICS[m][1], METRICS[m][0] + ("（日均）" if METRICS[m][3] else "")
    else:
        raise ChartError("grouped_bar 的 by 只能是 channel / variant / product")
    spec = dict(type="grouped_bar", unit=unit, x=cats, series=[
        dict(name=f"对比期 {wlabel(prev)}", data=[_r(x) for x in v0], tone="muted"),
        dict(name=f"本期 {wlabel(cur)}", data=[_r(x) for x in v1], tone="main")])
    lines = [f"{c} {fmt(x0, unit)} → {fmt(x1, unit)}" + (f"（{x1 / x0 - 1:+.1%}）" if x0 else "")
             for c, x0, x1 in zip(cats, v0, v1)]
    spec["title"] = _title(a, f"{name_of(ds, pids)}{mname}对比")
    spec["summary"] = f"{name_of(ds, pids)}{mname}，对比期 {wlabel(prev)} → 本期 {wlabel(cur)}：" + "；".join(lines)
    spec["values"] = [x for x in v0 + v1 if x is not None] + [x1 / x0 - 1 for x0, x1 in zip(v0, v1) if x0]
    return spec


def channel_avgs(ds, pids, wins):
    names = config.metrics().get("channels", {})
    rows = pd.concat([ds.cdays(p) for p in pids]) if pids else ds.dc.iloc[0:0]
    chans = [c for c in names if c in set(rows["channel"])]
    out = []
    for w in wins:
        n = (w[1] - w[0]).days + 1
        x = rows[(rows["date"] >= w[0]) & (rows["date"] <= w[1])].groupby("channel")["uv"].sum()
        out.append([float(x.get(c, 0)) / n for c in chans])
    return [names[c] for c in chans], *out


def factor_waterfall(ds, pids, cur, prev) -> dict:
    """GMV 变化拆成访客、转化、客单价（LMDI），返回瀑布图配置的核心字段。"""
    def tot(w):
        f = frame(ds, pids)
        f = f[(f.index >= w[0]) & (f.index <= w[1])]
        g, u, b = float(f["gmv"].sum()), float(f["uv"].sum()), float(f["buyers"].sum())
        return dict(gmv=g, uv=u, cvr=b / u if u else 0, aov=g / b if b else 0)
    v1, v0 = tot(cur), tot(prev)
    c, _ = lmdi(v1, v0, ["uv", "cvr", "aov"], "gmv")
    names = {"uv": "访客数", "cvr": "支付转化率", "aov": "客单价"}
    items = [dict(label=names[k], value=_r(c[k], 0), tone="good" if c[k] >= 0 else "bad",
                  note=f"{v1[k] / v0[k] - 1:+.1%}" if v0[k] else "") for k in ("uv", "cvr", "aov")]
    return dict(start=dict(label=wlabel(prev), value=_r(v0["gmv"], 0)), end=dict(label=wlabel(cur), value=_r(v1["gmv"], 0)),
                items=items, raw=dict(v0=v0, v1=v1, contrib=c))


def product_waterfall(ds, pids, cur, prev, cover=0.8, max_items=6) -> dict:
    items = change_items(ds, pids, "gmv", cur, prev)
    total_abs = sum(abs(x["change"] or 0) for x in items) or 1
    items.sort(key=lambda x: -abs(x["change"] or 0))
    top, acc = [], 0.0
    for x in items:
        if top and (acc >= cover * total_abs or len(top) >= max_items):
            break
        top.append(x)
        acc += abs(x["change"] or 0)
    rest = [x for x in items if x not in top]
    return dict(top=top, rest=rest, total_abs=total_abs, covered=acc / total_abs)


def _waterfall(ds, a, pids):
    by = a.get("by") or "factor"
    cur, prev = periods(ds, a, 7)
    if by == "factor":
        w = factor_waterfall(ds, pids, cur, prev)
        raw = w.pop("raw")
        spec = dict(type="waterfall", unit="money", **w)
        d = raw["v1"]["gmv"] - raw["v0"]["gmv"]
        lines = [f"{i['label']} {fmt(raw['v0'][k], METRICS[k][1])} → {fmt(raw['v1'][k], METRICS[k][1])}，贡献 {raw['contrib'][k]:+,.0f} 元"
                 + (f"（占变化 {raw['contrib'][k] / d:.0%}）" if d else "")
                 for i, k in zip(spec["items"], ("uv", "cvr", "aov"))]
        spec["summary"] = (f"{name_of(ds, pids)} GMV {wlabel(prev)} {raw['v0']['gmv']:,.0f} 元 → {wlabel(cur)} {raw['v1']['gmv']:,.0f} 元"
                           f"（{d:+,.0f}，{(raw['v1']['gmv'] / raw['v0']['gmv'] - 1) if raw['v0']['gmv'] else 0:+.1%}）。" + "；".join(lines))
        spec["values"] = [raw["v0"]["gmv"], raw["v1"]["gmv"], d] + [raw["v0"][k] for k in ("uv", "cvr", "aov")] + \
            [raw["v1"][k] for k in ("uv", "cvr", "aov")] + list(raw["contrib"].values()) + \
            ([raw["contrib"][k] / d for k in raw["contrib"]] if d else [])
        spec["title"] = _title(a, f"{name_of(ds, pids)} GMV 变化拆解")
        return spec
    if by == "product":
        if len(pids) < 2:
            raise ChartError("按商品拆解需要多个商品，传 [\"ALL\"] 即可")
        prof = ds.profile.get("report", {})
        w = product_waterfall(ds, pids, cur, prev, prof.get("cover_share", 0.8), prof.get("max_items", 6))
        g0 = sum((x["prev"] or 0) for x in w["top"] + w["rest"]) * ((cur[1] - cur[0]).days + 1)
        g1 = sum((x["cur"] or 0) for x in w["top"] + w["rest"]) * ((cur[1] - cur[0]).days + 1)
        items = [dict(label=x["label"], value=_r(x["change"], 0), tone="good" if (x["change"] or 0) >= 0 else "bad")
                 for x in w["top"]]
        rest = sum(x["change"] or 0 for x in w["rest"])
        if w["rest"]:
            items.append(dict(label=f"其他 {len(w['rest'])} 个商品", value=_r(rest, 0), tone="muted"))
        spec = dict(type="waterfall", unit="money", start=dict(label=wlabel(prev), value=_r(g0, 0)),
                    end=dict(label=wlabel(cur), value=_r(g1, 0)), items=items)
        spec["summary"] = (f"GMV {g0:,.0f} → {g1:,.0f} 元（{g1 - g0:+,.0f}）。逐个商品："
                           + "；".join(f"{x['label']} {x['change']:+,.0f} 元（占总波动 {abs(x['change']) / w['total_abs']:.0%}）" for x in w["top"])
                           + (f"；其他 {len(w['rest'])} 个商品合计 {rest:+,.0f} 元" if w["rest"] else ""))
        spec["values"] = [g0, g1, g1 - g0, rest] + [x["change"] for x in w["top"]] + [abs(x["change"]) / w["total_abs"] for x in w["top"]]
        spec["title"] = _title(a, "GMV 变化来自哪些商品")
        return spec
    raise ChartError("waterfall 的 by 只能是 factor 或 product")


def weeks_back(ds: Dataset, n: int, end=None):
    end = pd.Timestamp(end or ds.as_of)
    return [(end - (7 * (n - 1 - i) + 6) * DAY, end - 7 * (n - 1 - i) * DAY) for i in range(n)]


def _stacked(ds, a, pids):
    by = a.get("by") or ("variant" if len(pids) == 1 else "tier")
    end = _date(ds, a.get("end"), "end") or ds.as_of
    wins = weeks_back(ds, 4, end)
    if by == "tier":
        tiers = tiering.compute(ds)
        cats = [t["name"] for t in config.tiering_cfg()["tiers"]]
        data = {c: [] for c in cats}
        for w in wins:
            tot = sum(window_value(ds, [p], "gmv", w) or 0 for p in ds.product_ids())
            for c in cats:
                v = sum(window_value(ds, [p], "gmv", w) or 0 for p in ds.product_ids() if tiers[p]["tier_name"] == c)
                data[c].append(v / tot if tot else 0)
        unit, title = "pct", "各分层 GMV 占比（按周）"
    elif by == "variant":
        if len(pids) != 1:
            raise ChartError("按规格看只能传 1 个商品")
        v = ds.vdays(pids[0])
        cats = sorted(v["variant_name"].unique())
        data = {c: [] for c in cats}
        for w in wins:
            x = v[(v["date"] >= w[0]) & (v["date"] <= w[1])]
            tot = float(x["units"].sum())
            for c in cats:
                data[c].append(float(x[x["variant_name"] == c]["units"].sum()) / tot if tot else 0)
        unit, title = "pct", f"{name_of(ds, pids)}各规格销量占比（按周）"
    else:
        raise ChartError("stacked_bar 的 by 只能是 tier 或 variant")
    spec = dict(type="stacked_bar", unit=unit, percent=True, x=[wlabel(w) for w in wins],
                series=[dict(name=c, data=[_r(x) for x in data[c]]) for c in cats])
    spec["summary"] = "；".join(f"{c}：" + " → ".join(f"{x:.0%}" for x in data[c]) for c in cats) + f"（{spec['x'][0]} 至 {spec['x'][-1]}，每周一根）"
    spec["values"] = [x for c in cats for x in data[c]]
    spec["title"] = _title(a, title)
    return spec


def _kpi(ds, a, pids):
    ms = _metrics(a, 1, 6)
    cur, prev = periods(ds, a, 7)
    items, vals = [], []
    for m in ms:
        if not METRICS[m][2] and len(pids) > 1:
            raise ChartError(f"{METRICS[m][0]}只能按单个商品看")
        v1, v0 = window_value(ds, pids, m, cur), window_value(ds, pids, m, prev)
        if METRICS[m][3] and v1 is not None:
            n = (cur[1] - cur[0]).days + 1
            v1, v0 = v1 * n, (v0 or 0) * n
        items.append(kpi_item(METRICS[m][0], v1, v0, METRICS[m][1], better="down" if m == "refund_rate" else "up"))
        vals += [v for v in (v1, v0) if v is not None]
    spec = dict(type="kpi", items=items, title=_title(a, f"{name_of(ds, pids)}核心指标（{wlabel(cur)} vs {wlabel(prev)}）"))
    spec["summary"] = "；".join(f"{i['label']} {fmt(i['prev'], i['unit'])} → {fmt(i['value'], i['unit'])}"
                               + (f"（{i['change']:+.1%}）" if i.get("change") is not None else "") for i in items)
    spec["values"] = vals + [i["change"] for i in items if i.get("change") is not None]
    return spec


def kpi_item(label, value, prev, unit, better="up", note=None, change=None):
    if change is None and prev not in (None, 0) and value is not None:
        change = value / prev - 1
    return dict(label=label, value=_r(value), prev=_r(prev), unit=unit, change=_r(change), better=better, note=note)


_DISPATCH = {"trend": _trend, "dual_line": _dual, "hbar": _hbar, "grouped_bar": _grouped,
             "waterfall": _waterfall, "stacked_bar": _stacked, "kpi": _kpi}


def draw(ds: Dataset, args: dict) -> dict:
    """AI 调用的画图入口。成功返回图表配置；参数不合格返回 {"error": 说明}。"""
    a = dict(args or {})
    t = a.get("type")
    if t not in TYPES:
        return {"error": f"图表类型「{t}」不在图表库里。可选：{'、'.join(f'{k}（{v}）' for k, v in TYPES.items())}"}
    try:
        pids = _pids(ds, a.get("products"))
        spec = _DISPATCH[t](ds, a, pids)
    except ChartError as e:
        return {"error": str(e)}
    allowed = TOOL_SPEC["parameters"]["properties"]
    spec["params"] = {k: v for k, v in a.items() if k in allowed and v not in (None, "", [])}
    return spec


# ---------------------------------------------------------------------------
# 一份报告里的图表
# ---------------------------------------------------------------------------
class ChartBook:
    """一份报告里的所有图表：必备图由平台生成，补充图由 AI 调用 draw() 生成（有上限）。"""

    def __init__(self, extra_limit: int = 3):
        self.charts: dict[str, dict] = {}
        self.extra_limit = extra_limit
        self.extra = 0

    def add(self, spec: dict, origin: str = "required", chapter: str | None = None) -> str:
        cid = f"c{len(self.charts) + 1}"
        spec = dict(spec, id=cid, origin=origin)
        if chapter:
            spec["chapter"] = chapter
        self.charts[cid] = spec
        return cid

    def draw_extra(self, ds: Dataset, args: dict, chapter: str | None = None) -> dict:
        if self.extra >= self.extra_limit:
            return {"error": f"补充图已达上限（每份报告最多 {self.extra_limit} 张），请直接写结论"}
        spec = draw(ds, args)
        if "error" in spec:
            return spec
        self.extra += 1
        cid = self.add(spec, "extra", chapter)
        return {"chart_id": cid, "title": spec["title"], "summary": spec["summary"]}

    @classmethod
    def load(cls, d: dict) -> "ChartBook":
        b = cls(d.get("limit", 3))
        b.charts = dict(d.get("charts") or {})
        b.extra = int(d.get("extra") or 0)
        return b

    def dump(self) -> dict:
        return dict(limit=self.extra_limit, extra=self.extra, charts=self.charts)

    def values(self) -> list:
        out = []
        for c in self.charts.values():
            out += [v for v in c.get("values", []) if v is not None]
        return out

    def public(self) -> dict:
        """存储与前端用：去掉仅用于核对的 values。"""
        return {k: {kk: vv for kk, vv in v.items() if kk != "values"} for k, v in self.charts.items()}


PLACEHOLDER = re.compile(r"\[图表[:：]\s*(c\d+)\s*\]")


def library_doc() -> list[dict]:
    """方法库页面展示的图表库说明。"""
    return [
        dict(type="kpi", name="指标卡", use="本期 vs 对比期的 2–6 个核心指标", params="products、metrics、start/end"),
        dict(type="trend", name="趋势折线", use="按天看走势，可加事件竖线、活动底色", params="products、metrics（1–2 个）、start/end、mark、band"),
        dict(type="dual_line", name="双折线", use="两个相关指标一起看，单位不同时左右两个坐标轴（评分 vs 退款率、我方价 vs 竞品价 vs 转化率）", params="1 个商品、metrics（2–3 个）、mark"),
        dict(type="waterfall", name="瀑布图", use="GMV 变化从哪来：拆成访客、转化、客单价，或拆到商品", params="products、by=factor / product、start/end"),
        dict(type="hbar", name="横向条形", use="多个商品同一指标的变化，正负对比", params="products、1 个指标、start/end"),
        dict(type="grouped_bar", name="分组柱", use="对比期 vs 本期：按渠道、按规格或按商品", params="products、by=channel / variant / product"),
        dict(type="stacked_bar", name="堆叠柱", use="结构占比按周变化：分层 GMV 占比、规格销量占比", params="products、by=tier / variant"),
    ]
