"""自定义预警：像 BI 一样「选对象 + 选指标 + 条件 + 门槛」，指标过线就出预警。

定义示例：{"id": 1, "name": "全店 GMV 周环比下降", "scope": "store", "target": null, "metric": "gmv",
          "cond": "drop", "threshold": 0.2, "severity": "red", "enabled": true}
- scope：store 全店汇总 / all 全部商品（逐个商品判断）/ tier 某个分层（target=分层 id）/ product 单个商品（target=商品编号）
- cond：below 低于 / above 高于（看当天的值）；drop 比前 7 天下降超过 / rise 比前 7 天上涨超过（看近 7 天 vs 前 7 天）
- 比率类指标（转化率、退款率、毛利率）和 drop / rise 的门槛都用小数（0.2 = 20%）
"""
from __future__ import annotations

import pandas as pd

from . import charts as C
from .loader import Dataset
from .metrics import DAY

STORE = "__store__"
SCOPES = {"store": "全店汇总", "all": "全部商品（逐个判断）", "tier": "某个分层（逐个判断）", "product": "单个商品"}
CONDS = {"below": "低于", "above": "高于", "drop": "比前 7 天下降超过", "rise": "比前 7 天上涨超过"}
METRICS = ["gmv", "uv", "units", "cvr", "aov", "refund_rate", "margin", "price", "price_index", "rating"]


def validate(d: dict, ds: Dataset | None = None) -> dict:
    d = dict(d)
    if not (d.get("name") or "").strip():
        raise ValueError("请填写预警名称")
    if d.get("scope") not in SCOPES:
        raise ValueError("对象只能是：" + "、".join(SCOPES.values()))
    if d.get("metric") not in METRICS:
        raise ValueError("指标只能从指标字典里选")
    if d.get("cond") not in CONDS:
        raise ValueError("条件只能是：" + "、".join(CONDS.values()))
    if d["scope"] == "store" and not C.METRICS[d["metric"]][2]:
        raise ValueError(f"{C.METRICS[d['metric']][0]}只能按单个商品看，不能选全店汇总")
    if d["scope"] in ("tier", "product") and not d.get("target"):
        raise ValueError("请选择分层或商品")
    if ds is not None and d["scope"] == "product" and d["target"] not in ds.product_ids():
        raise ValueError(f"商品 {d['target']} 不存在")
    try:
        d["threshold"] = float(d["threshold"])
    except (TypeError, ValueError):
        raise ValueError("请填写门槛数值")
    d["severity"] = d.get("severity") if d.get("severity") in ("red", "yellow", "blue") else "yellow"
    d["enabled"] = bool(d.get("enabled", True))
    return d


def targets(ds: Dataset, d: dict, tiers: dict) -> list[str]:
    if d["scope"] == "store":
        return [STORE]
    if d["scope"] == "product":
        return [d["target"]] if d["target"] in ds.product_ids() else []
    if d["scope"] == "tier":
        return [p for p in ds.product_ids() if tiers.get(p, {}).get("tier") == d["target"]]
    return ds.product_ids()


def describe(d: dict) -> str:
    unit = C.METRICS[d["metric"]][1]
    if d["cond"] in ("drop", "rise"):
        th = f"{d['threshold']:.0%}"
    else:
        th = C.fmt(d["threshold"], unit)
    return f"{C.METRICS[d['metric']][0]}{CONDS[d['cond']]} {th}"


def check(ds: Dataset, d: dict, pid: str, day: pd.Timestamp) -> dict | None:
    pids = ds.product_ids() if pid == STORE else [pid]
    m = d["metric"]
    unit = C.METRICS[m][1]
    name = C.METRICS[m][0]
    try:
        if d["cond"] in ("below", "above"):
            v = C.window_value(ds, pids, m, (day, day))
            if v is None:
                return None
            hit = v < d["threshold"] if d["cond"] == "below" else v > d["threshold"]
            if not hit:
                return None
            return dict(rule=f"C{d['id']}", severity=d["severity"], value=round(v, 4), custom=True,
                        text=f"{d['name']}：当天{name} {C.fmt(v, unit)}，{CONDS[d['cond']]} {C.fmt(d['threshold'], unit)}")
        v1 = C.window_value(ds, pids, m, (day - 6 * DAY, day))
        v0 = C.window_value(ds, pids, m, (day - 13 * DAY, day - 7 * DAY))
    except C.ChartError:
        return None
    if not v0 or v1 is None:
        return None
    chg = v1 / v0 - 1
    if (d["cond"] == "drop" and chg <= -d["threshold"]) or (d["cond"] == "rise" and chg >= d["threshold"]):
        return dict(rule=f"C{d['id']}", severity=d["severity"], value=round(chg, 4), custom=True,
                    text=f"{d['name']}：近 7 天{name} {C.fmt(v1, unit)}，前 7 天 {C.fmt(v0, unit)}（{chg:+.1%}）")
    return None
