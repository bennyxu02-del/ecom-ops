"""预警扫描：逐日评估规则，合并为问题卡。规则定义见 methods/alert_rules.yaml。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import tiering
from .decompose import variant_baseline_share
from .loader import Dataset
from .metrics import DAY, agg, r, slice_, windows

SEV_ORDER = {"blue": 1, "yellow": 2, "red": 3}
SEV_NAME = {"red": "红", "yellow": "黄", "blue": "蓝"}
RULE_NAMES = {"R01": "GMV 下滑", "R02": "访客异常", "R03": "转化率异常", "R04": "规格断货",
              "R05": "断货风险", "R06": "价格劣势", "R07": "口碑下滑", "R08": "增长机会"}


def _baseline(g: pd.DataFrame, obs_start: pd.Timestamp, days: int, promo: set) -> pd.DataFrame:
    b = g[(g.index < obs_start) & (g.index >= obs_start - days * DAY)]
    return b[~b.index.isin(promo)]


def _z(obs: float, series: pd.Series) -> float | None:
    if len(series) < 14:
        return None
    mu = float(series.mean())
    sd = float(series.std(ddof=1))
    sd = max(sd, abs(mu) * 0.05, 1e-9)
    return (obs - mu) / sd


def evaluate(ds: Dataset, pid: str, day: pd.Timestamp, tiers: dict) -> list[dict]:
    """返回当天触发的规则列表。"""
    prof = ds.profile
    t = tiers[pid]
    coef = t["coef"]
    g = ds.pdays(pid)
    g = g[g.index <= day]
    if g.empty or g.index.max() < day:
        return []
    out = []
    launch = pd.Timestamp(ds.product(pid)["launch_date"])
    promo = ds.promo_days()

    # R01
    cur_w, prev_w = windows(day, 7)
    if launch <= prev_w[0]:
        a1, a0 = agg(slice_(g, cur_w)), agg(slice_(g, prev_w))
        if a0["gmv"] > 0:
            drop = 1 - a1["gmv"] / a0["gmv"]
            yl, rd = prof["gmv_drop_yellow"] * coef, prof["gmv_drop_red"] * coef
            if drop >= yl:
                sev = "red" if drop >= rd else "yellow"
                if sev == "yellow" and t["tier"] == "hero":
                    sev = "red"
                out.append(dict(rule="R01", severity=sev, value=r(-drop), threshold=r(-yl),
                                text=f"近 7 日 GMV 较前 7 日下降 {drop:.1%}（阈值 {yl:.0%}）"))

    # 统计型 R02 / R03 / R08
    obs = g[(g.index > day - 3 * DAY)]
    base = _baseline(g, day - 2 * DAY, prof.get("baseline_days", 28), promo)
    zt = prof["z_threshold"] * coef
    if len(obs) == 3 and len(base) >= 14:
        z_uv = _z(float(obs["uv"].mean()), base["uv"])
        cvr_series = base["buyers"] / base["uv"].replace(0, np.nan)
        z_cvr = _z(float(obs["buyers"].sum() / max(obs["uv"].sum(), 1)), cvr_series.dropna())
        z_gmv = _z(float(obs["gmv"].mean()), base["gmv"])
        if z_uv is not None and z_uv <= -zt:
            out.append(dict(rule="R02", severity="yellow", value=r(z_uv, 2), threshold=r(-zt, 2),
                            text=f"近 3 日访客明显低于正常水平（偏离正常波动 {abs(z_uv):.1f} 倍）"))
        if z_cvr is not None and z_cvr <= -zt:
            sev = "red" if t["tier"] == "hero" else "yellow"
            out.append(dict(rule="R03", severity=sev, value=r(z_cvr, 2), threshold=r(-zt, 2),
                            text=f"近 3 日支付转化率明显低于正常水平（偏离正常波动 {abs(z_cvr):.1f} 倍）"))
        zmax = max([z for z in (z_uv, z_gmv) if z is not None], default=None)
        if zmax is not None and zmax >= zt:
            out.append(dict(rule="R08", severity="blue", value=r(zmax, 2), threshold=r(zt, 2),
                            text=f"近 3 日访客或 GMV 明显高于正常水平（偏离正常波动 {zmax:.1f} 倍）"))

    # 库存类 R04 / R05
    if "stock" in ds.available:
        v = ds.vdays(pid)
        vt = v[v["date"] == day]
        if not vt.empty:
            shares = variant_baseline_share(ds, pid, day)
            lead = prof["replenish_lead_days"]
            for _, row in vt.iterrows():
                vid, name = row["variant_id"], row["variant_name"]
                share = shares.get(vid, 0.0)
                if row["stock_units"] <= 0:
                    if share >= prof["stockout_share_min"]:
                        sev = "red" if share >= 0.30 else "yellow"
                        out.append(dict(rule="R04", severity=sev, value=r(share), variant=name,
                                        text=f"{name} 已断货，该规格平时占销量 {share:.0%}"))
                    continue
                last7 = v[(v["variant_id"] == vid) & (v["date"] > day - 7 * DAY) & (v["date"] <= day)]["units"].mean()
                if last7 and last7 > 0:
                    dos = row["stock_units"] / last7
                    if dos < lead and share >= 0.05:
                        sev = "red" if dos < lead / 2 else "yellow"
                        out.append(dict(rule="R05", severity=sev, value=r(dos, 1), threshold=lead, variant=name,
                                        text=f"{name} 库存仅够卖约 {dos:.1f} 天，低于补货周期 {lead} 天"))

    # R06 价格劣势
    if "price" in ds.available and "comp_price" in ds.available:
        last2 = g.tail(2)
        if len(last2) == 2 and (last2["comp_price"] > 0).all():
            idx = last2["price"] / last2["comp_price"]
            if (idx > 1 + prof["price_gap"]).all():
                pi = float(idx.iloc[-1])
                out.append(dict(rule="R06", severity="yellow", value=r(pi, 3), threshold=r(1 + prof["price_gap"], 3),
                                text=f"价格指数 {pi:.2f}，比竞品贵 {pi - 1:.1%}"))

    # R07 口碑
    if "rating" in ds.available:
        prev28 = g[(g.index < day) & (g.index >= day - 28 * DAY)]
        if len(prev28) >= 14:
            rt_drop = float(prev28["rating"].mean() - g["rating"].iloc[-1])
            hit = rt_drop >= prof["rating_drop"] - 1e-9
            ref_hit = False
            if "refund_amount" in ds.available:
                l7 = g[g.index > day - 7 * DAY]
                b28 = g[(g.index <= day - 7 * DAY) & (g.index > day - 35 * DAY)]
                rr7 = l7["refund_amount"].sum() / max(l7["gmv"].sum(), 1)
                rrb = b28["refund_amount"].sum() / max(b28["gmv"].sum(), 1)
                ref_hit = rrb > 0 and rr7 > rrb * 1.5
            if hit or ref_hit:
                txt = []
                if hit:
                    txt.append(f"评分较前 28 天均值下降 {rt_drop:.2f}")
                if ref_hit:
                    txt.append(f"近 7 日退款率 {rr7:.1%}，基线 {rrb:.1%}")
                out.append(dict(rule="R07", severity="yellow", value=r(rt_drop, 3), text="；".join(txt)))
    return out


def gmv_impact(ds: Dataset, pid: str, day: pd.Timestamp) -> float:
    g = ds.pdays(pid)
    cur_w, prev_w = windows(day, 7)
    a1, a0 = agg(slice_(g, cur_w)), agg(slice_(g, prev_w))
    return round(max(a0["gmv"] - a1["gmv"], 0.0), 2)


EXPECTED_RULES = {"R01", "R02"}      # 只有「量」的下滑（GMV、访客）才可能是预期内


def expected_reason(ds: Dataset, pid: str, c: dict) -> dict | None:
    """活动结束或大促后的回落属于预期内：只触发了 GMV / 访客类规则，且之前 10 天内有该商品的活动结束（或全店大促）。"""
    if not c["rules"] or not c["rules"] <= EXPECTED_RULES:
        return None
    ev = ds.events
    if ev.empty:
        return None
    lo, hi = c["first_date"] - 10 * DAY, c["last_trigger"]
    x = ev[(ev["date"] >= lo) & (ev["date"] <= hi)]
    pidcol = x["product_id"].fillna("")
    hit = x[((x["event_type"] == "campaign_end") & (pidcol == pid)) | ((x["event_type"] == "promo_day") & (pidcol.isin(["", pid])))]
    if hit.empty:
        return None
    e = hit.sort_values("date").iloc[-1]
    d = e["date"]
    return dict(reason=f"{d.month}/{d.day} {e['description']}，属于活动后的正常回落，不作为问题处理", event_date=d.strftime("%Y-%m-%d"),
                event_type=e["event_type"])


def scan(ds: Dataset, days: int = 14, as_of=None, tiers: dict | None = None, pids: list | None = None) -> list[dict]:
    """逐日扫描近 N 天，按合并规则生成问题卡。"""
    as_of = as_of or ds.as_of
    pids = pids or ds.product_ids()
    day_list = [as_of - (days - 1 - i) * DAY for i in range(days)]
    tiers_by_day = {dd: tiering.compute(ds, dd) for dd in day_list}
    if tiers:
        tiers_by_day[as_of] = tiers
    cards = []
    for pid in pids:
        open_card = None
        quiet = 0
        for dd in day_list:
            hits = evaluate(ds, pid, dd, tiers_by_day[dd])
            if hits:
                sev = max((h["severity"] for h in hits), key=lambda s: SEV_ORDER[s])
                if open_card is None or open_card["status"] == "recovered":
                    open_card = dict(id=f"{ds.name}-{pid}-{dd:%m%d}", product_id=pid, first_date=dd,
                                     last_trigger=dd, trigger_days=0, rules=set(), max_severity=sev,
                                     status="active", history=[])
                    cards.append(open_card)
                open_card["last_trigger"] = dd
                open_card["trigger_days"] += 1
                open_card["rules"] |= {h["rule"] for h in hits}
                if SEV_ORDER[sev] > SEV_ORDER[open_card["max_severity"]]:
                    open_card["max_severity"] = sev
                open_card["severity"] = sev
                open_card["latest"] = hits
                open_card["history"].append(dict(date=dd.strftime("%Y-%m-%d"), severity=sev,
                                                 rules=[h["rule"] for h in hits]))
                quiet = 0
            elif open_card is not None and open_card["status"] == "active":
                quiet += 1
                if quiet >= 3:
                    open_card["status"] = "recovered"
                    open_card["recovered_date"] = dd
    t_now = tiers_by_day[as_of]
    res = []
    for c in cards:
        pid = c["product_id"]
        p = ds.product(pid)
        exp = expected_reason(ds, pid, c)
        if exp and c["status"] == "active":
            c["severity"] = "blue"
        res.append(dict(
            id=c["id"], product_id=pid, product_name=p["product_name"],
            tier=t_now[pid]["tier_name"], lifecycle=t_now[pid]["lifecycle_name"],
            severity=c["severity"] if c["status"] == "active" else c["max_severity"],
            max_severity=c["max_severity"], auto_status=c["status"],
            first_date=c["first_date"].strftime("%Y-%m-%d"), last_trigger=c["last_trigger"].strftime("%Y-%m-%d"),
            recovered_date=c.get("recovered_date").strftime("%Y-%m-%d") if c.get("recovered_date") is not None else None,
            trigger_days=c["trigger_days"], rules=sorted(c["rules"]),
            rule_names=[RULE_NAMES[x] for x in sorted(c["rules"])],
            latest=c["latest"], history=c["history"],
            is_today=c["last_trigger"] == as_of,
            gmv_impact=gmv_impact(ds, pid, c["last_trigger"]),
            expected=exp,
        ))
    res.sort(key=lambda x: (-int(x["is_today"]), -SEV_ORDER[x["severity"]], -x["gmv_impact"]))
    return res
