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


STOCK_RULES = {"R04", "R05"}
VOLUME_RULES = {"R01", "R02", "R03"}          # 「量」的下滑：受影响金额下限约束
RULE_METRIC = {"R01": "gmv", "R02": "uv", "R03": "cvr"}   # 恢复判断看的指标
RULE_PLAIN = {   # 给业务看的规则说明（预警设置页）
    "R01": "近 7 天 GMV 比前 7 天下降超过门槛；新品、成长期商品门槛自动放宽，爆品黄色直接升为红色",
    "R02": "近 3 天访客明显低于过去 28 天的正常波动范围",
    "R03": "近 3 天支付转化率明显低于过去 28 天的正常波动范围",
    "R04": "某个规格卖断货，且它平时占销量不低于门槛；在途补货 3 天内到的降为黄色",
    "R05": "某个规格的库存只够卖不到一个补货周期；到货前够卖的降为提示",
    "R06": "到手价比竞品贵超过门槛，连续 2 天",
    "R07": "评分比前 28 天均值下降超过门槛，或近 7 天退款率超过平时的 1.5 倍",
    "R08": "近 3 天访客或 GMV 明显高于正常波动范围（增长机会）",
}


def rule_availability(ds: Dataset) -> dict:
    """每条规则需要的数据是否具备：{规则: (是否可用, 缺什么)}。"""
    av = ds.available
    need = {"R04": ["stock", "variant"], "R05": ["stock", "variant"], "R06": ["price", "comp_price"], "R07": ["rating"]}
    names = {"stock": "库存", "variant": "规格", "price": "到手价", "comp_price": "竞品价", "rating": "评分"}
    out = {}
    for r in RULE_NAMES:
        miss = [names[x] for x in need.get(r, []) if x not in av]
        out[r] = (not miss, "缺少" + "、".join(miss) if miss else "")
    return out


def _restocks(ds: Dataset, pid: str, day: pd.Timestamp) -> list[dict]:
    ev = ds.events
    if ev.empty:
        return []
    x = ev[(ev["event_type"] == "restock") & (ev["product_id"] == pid) & (ev["date"] > day)]
    return [dict(date=e["date"], qty=float(e["value"]) if pd.notna(e["value"]) else None, text=str(e["description"]))
            for _, e in x.sort_values("date").iterrows()]


def _md(date: str) -> str:
    return f"{int(date[5:7])}/{int(date[8:10])}"


def in_transit(ds: Dataset, pid: str, variant: str, day: pd.Timestamp) -> dict | None:
    """该规格在途的补货（事件表里 day 之后到货的 restock）。描述里写了规格名时按规格匹配，否则算整个商品。"""
    rs = _restocks(ds, pid, day)
    names = set(ds.vdays(pid)["variant_name"].unique()) if not ds.vdays(pid).empty else set()
    for r in rs:
        named = [n for n in names if n and n in r["text"]]
        if not named or variant in named:
            d = r["date"]
            return dict(qty=r["qty"], date=d.strftime("%Y-%m-%d"), days=int((d - day).days), text=r["text"], variant=variant)
    return None


def evaluate(ds: Dataset, pid: str, day: pd.Timestamp, tiers: dict, enabled: set | None = None) -> list[dict]:
    """返回当天触发的规则列表。enabled：启用的规则（None = 全部）。"""
    prof = ds.profile
    t = tiers[pid]
    coef = t["coef"]
    on = set(enabled) if enabled is not None else set(RULE_NAMES)
    if t["tier"] == "tail" and prof.get("alert", {}).get("tail_stock_only", True):
        on &= STOCK_RULES
    g = ds.pdays(pid)
    g = g[g.index <= day]
    if g.empty or g.index.max() < day:
        return []
    out = []
    launch = pd.Timestamp(ds.product(pid)["launch_date"])
    promo = ds.promo_days()

    # R01
    cur_w, prev_w = windows(day, 7)
    if "R01" in on and launch <= prev_w[0]:
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
        if "R02" in on and z_uv is not None and z_uv <= -zt:
            out.append(dict(rule="R02", severity="yellow", value=r(z_uv, 2), threshold=r(-zt, 2),
                            text=f"近 3 日访客明显低于正常水平（偏离正常波动 {abs(z_uv):.1f} 倍）"))
        if "R03" in on and z_cvr is not None and z_cvr <= -zt:
            sev = "red" if t["tier"] == "hero" else "yellow"
            out.append(dict(rule="R03", severity=sev, value=r(z_cvr, 2), threshold=r(-zt, 2),
                            text=f"近 3 日支付转化率明显低于正常水平（偏离正常波动 {abs(z_cvr):.1f} 倍）"))
        zmax = max([z for z in (z_uv, z_gmv) if z is not None], default=None)
        if "R08" in on and zmax is not None and zmax >= zt:
            out.append(dict(rule="R08", severity="blue", value=r(zmax, 2), threshold=r(zt, 2),
                            text=f"近 3 日访客或 GMV 明显高于正常水平（偏离正常波动 {zmax:.1f} 倍）"))

    # 库存类 R04 / R05
    if "stock" in ds.available and on & STOCK_RULES:
        v = ds.vdays(pid)
        vt = v[v["date"] == day]
        if not vt.empty:
            shares = variant_baseline_share(ds, pid, day)
            lead = prof["replenish_lead_days"]
            for _, row in vt.iterrows():
                vid, name = row["variant_id"], row["variant_name"]
                share = shares.get(vid, 0.0)
                if row["stock_units"] <= 0:
                    if "R04" in on and share >= prof["stockout_share_min"]:
                        sev = "red" if share >= 0.30 else "yellow"
                        txt = f"{name} 已断货，该规格平时占销量 {share:.0%}"
                        it = in_transit(ds, pid, name, day)
                        if it:
                            txt += f"；在途 {it['qty']:,.0f} 件，{_md(it['date'])} 到货（还要断约 {it['days']} 天）" if it["qty"] else f"；在途补货 {_md(it['date'])} 到货"
                            if it["days"] <= 3 and sev == "red":
                                sev = "yellow"
                        out.append(dict(rule="R04", severity=sev, value=r(share), variant=name, text=txt, in_transit=it))
                    continue
                last7 = v[(v["variant_id"] == vid) & (v["date"] > day - 7 * DAY) & (v["date"] <= day)]["units"].mean()
                if last7 and last7 > 0:
                    dos = row["stock_units"] / last7
                    if "R05" in on and dos < lead and share >= 0.05:
                        sev = "red" if dos < lead / 2 else "yellow"
                        txt = f"{name} 库存仅够卖约 {dos:.1f} 天，低于补货周期 {lead} 天"
                        it = in_transit(ds, pid, name, day)
                        if it:
                            if dos >= it["days"]:
                                sev = "blue"
                                txt += f"；在途补货 {_md(it['date'])} 到货，到货前库存够卖"
                            else:
                                txt += f"；在途补货 {_md(it['date'])} 到货，到货前约断 {it['days'] - dos:.0f} 天"
                        out.append(dict(rule="R05", severity=sev, value=r(dos, 1), threshold=lead, variant=name, text=txt,
                                        in_transit=it))

    # R06 价格劣势
    if "R06" in on and "price" in ds.available and "comp_price" in ds.available:
        last2 = g.tail(2)
        if len(last2) == 2 and (last2["comp_price"] > 0).all():
            idx = last2["price"] / last2["comp_price"]
            if (idx > 1 + prof["price_gap"]).all():
                pi = float(idx.iloc[-1])
                out.append(dict(rule="R06", severity="yellow", value=r(pi, 3), threshold=r(1 + prof["price_gap"], 3),
                                text=f"价格指数 {pi:.2f}，比竞品贵 {pi - 1:.1%}"))

    # R07 口碑
    if "R07" in on and "rating" in ds.available:
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


def back_to_base(ds: Dataset, pid: str, first: pd.Timestamp, rules: set, day: pd.Timestamp) -> tuple[bool, str | None]:
    """规则不再触发时，还要看指标是否回到出问题前的水平（首次触发前 7 天），避免「低位成了新常态」被当成恢复。"""
    from . import charts as C
    from .custom_alerts import STORE
    ms = sorted({RULE_METRIC[x] for x in rules if x in RULE_METRIC})
    if pid == STORE or not ms:
        return True, None
    ratio = ds.profile.get("alert", {}).get("recover_ratio", 0.95)
    base_w, cur_w = (first - 7 * DAY, first - DAY), (day - 2 * DAY, day)
    for m in ms:
        b = C.window_value(ds, [pid], m, base_w)
        c = C.window_value(ds, [pid], m, cur_w)
        if b and c is not None and c < b * ratio:
            return False, f"规则已不再触发，但{C.METRICS[m][0]}仍比出问题前低 {1 - c / b:.0%}"
    return True, None


def card_kind(rules: set) -> str:
    """问题还是机会：只有增长机会（可以带断货风险）的是机会，其余是问题。"""
    builtin = {x for x in rules if x in RULE_NAMES}
    if "R08" in builtin and not (builtin - {"R08"} - STOCK_RULES) and builtin == set(rules):
        return "opportunity"
    return "problem"


def scan(ds: Dataset, days: int = 14, as_of=None, tiers: dict | None = None, pids: list | None = None,
         enabled: set | None = None, custom: list | None = None) -> list[dict]:
    """逐日扫描近 N 天，按合并规则生成问题卡。
    enabled：启用的内置规则；custom：自定义预警定义（见 custom_alerts.py）。"""
    from . import custom_alerts as CA
    as_of = as_of or ds.as_of
    pids = list(pids or ds.product_ids())
    custom = [d for d in (custom or []) if d.get("enabled", True)]
    if any(d["scope"] == "store" for d in custom):
        pids.append(CA.STORE)
    names = dict(RULE_NAMES, **{f"C{d['id']}": d["name"] for d in custom})
    day_list = [as_of - (days - 1 - i) * DAY for i in range(days)]
    tiers_by_day = {dd: tiering.compute(ds, dd) for dd in day_list}
    if tiers:
        tiers_by_day[as_of] = tiers
    prof_alert = ds.profile.get("alert", {})
    cards = []
    for pid in pids:
        open_card = None
        quiet = 0
        for dd in day_list:
            hits = [] if pid == CA.STORE else evaluate(ds, pid, dd, tiers_by_day[dd], enabled)
            for d in custom:
                if pid in CA.targets(ds, d, tiers_by_day[dd]):
                    h = CA.check(ds, d, pid, dd)
                    if h:
                        hits.append(h)
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
                open_card["plateau"] = None
                open_card["history"].append(dict(date=dd.strftime("%Y-%m-%d"), severity=sev,
                                                 rules=[h["rule"] for h in hits]))
                quiet = 0
            elif open_card is not None and open_card["status"] == "active":
                quiet += 1
                if quiet >= 3:
                    ok, why = back_to_base(ds, pid, open_card["first_date"], open_card["rules"], dd)
                    if ok:
                        open_card["status"] = "recovered"
                        open_card["recovered_date"] = dd
                    else:
                        open_card["plateau"] = why
    t_now = tiers_by_day[as_of]
    floor = prof_alert.get("min_impact", 0) or 0
    res = []
    for c in cards:
        pid = c["product_id"]
        store = pid == CA.STORE
        exp = None if store else expected_reason(ds, pid, c)
        if exp and c["status"] == "active":
            c["severity"] = "blue"
        impact = gmv_impact_all(ds, c["last_trigger"]) if store else gmv_impact(ds, pid, c["last_trigger"])
        kind = card_kind(c["rules"])
        transit = [h["in_transit"] for h in c["latest"] if h.get("in_transit")]
        suppressed = (kind == "problem" and not store and c["rules"] <= VOLUME_RULES and not exp and impact < floor)
        res.append(dict(
            id=c["id"], product_id=pid, product_name="全店" if store else ds.product(pid)["product_name"],
            tier="全店" if store else t_now[pid]["tier_name"], lifecycle="—" if store else t_now[pid]["lifecycle_name"],
            severity=c["severity"] if c["status"] == "active" else c["max_severity"],
            max_severity=c["max_severity"], auto_status=c["status"],
            first_date=c["first_date"].strftime("%Y-%m-%d"), last_trigger=c["last_trigger"].strftime("%Y-%m-%d"),
            recovered_date=c.get("recovered_date").strftime("%Y-%m-%d") if c.get("recovered_date") is not None else None,
            trigger_days=c["trigger_days"], rules=sorted(c["rules"]),
            rule_names=[names.get(x, x) for x in sorted(c["rules"])],
            latest=c["latest"], history=c["history"],
            is_today=c["last_trigger"] == as_of,
            gmv_impact=impact,
            expected=exp, kind=kind, in_transit=transit or None, plateau=c.get("plateau") if c["status"] == "active" else None,
            suppressed=suppressed, custom=any(x.startswith("C") for x in c["rules"]),
        ))
    res.sort(key=lambda x: (-int(x["is_today"]), -SEV_ORDER[x["severity"]], -x["gmv_impact"]))
    return res


def gmv_impact_all(ds: Dataset, day: pd.Timestamp) -> float:
    cur_w, prev_w = windows(day, 7)
    a1 = sum(agg(slice_(ds.pdays(p), cur_w))["gmv"] for p in ds.product_ids())
    a0 = sum(agg(slice_(ds.pdays(p), prev_w))["gmv"] for p in ds.product_ids())
    return round(max(a0 - a1, 0.0), 2)
