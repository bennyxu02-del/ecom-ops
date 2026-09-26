"""周报数据包与动作效果对比。周报中的数字只能来自这里。"""
from __future__ import annotations

import pandas as pd

from . import config, health, tiering
from .loader import Dataset
from .metrics import DAY, agg, r, slice_

METRIC_NAMES = {"cvr": "支付转化率", "uv": "访客数", "uv_paid": "付费访客", "uv_search": "搜索访客", "aov": "客单价",
                "gmv": "GMV", "rating": "评分", "variant_units": "规格销量", "days_of_supply": "库存可售天数",
                "units": "销量"}


def _daily_metric(ds: Dataset, pid: str, metric: str, variant: str | None = None) -> pd.Series:
    g = ds.pdays(pid)
    if metric in ("cvr",):
        return g["buyers"] / g["uv"].replace(0, pd.NA)
    if metric == "aov":
        return g["gmv"] / g["buyers"].replace(0, pd.NA)
    if metric in ("uv", "gmv", "units", "rating"):
        return g[metric]
    if metric in ("uv_paid", "uv_search"):
        ch = metric.split("_")[1]
        dc = ds.cdays(pid)
        return dc[dc["channel"] == ch].set_index("date")["uv"]
    if metric == "variant_units":
        v = ds.vdays(pid)
        if variant:
            v = v[v["variant_name"] == variant]
        return v.groupby("date")["units"].sum()
    return g["gmv"]


def effect(ds: Dataset, pid: str, metric: str, exec_date: str | None, n: int = 5, variant: str | None = None) -> dict:
    """执行前后 N 天均值对比。"""
    name = METRIC_NAMES.get(metric, metric)
    if not exec_date:
        return dict(metric=metric, metric_name=name, status="待执行")
    d = pd.Timestamp(exec_date)
    s = _daily_metric(ds, pid, metric, variant).dropna().astype(float)
    after = s[(s.index > d) & (s.index <= d + n * DAY)]
    before = s[(s.index < d) & (s.index >= d - n * DAY)]
    if len(after) < n:
        return dict(metric=metric, metric_name=name, status="跟踪中", days_observed=len(after), days_needed=n,
                    before=r(before.mean()) if len(before) else None)
    b, a = float(before.mean()), float(after.mean())
    return dict(metric=metric, metric_name=name, status="已完成", before=r(b), after=r(a),
                change_pct=r(a / b - 1 if b else None), days=n,
                note="执行前后各 N 天均值对比，不等同于严格的因果效果")


def weekly_pack(ds: Dataset, cards: list[dict], actions: list[dict], diagnoses: dict | None = None) -> dict:
    as_of = ds.as_of
    start = as_of - pd.Timedelta(days=as_of.dayofweek)          # 本周一
    end = start + 6 * DAY
    if end > as_of:
        end = as_of
    wk = (start, end)
    pwk = (start - 7 * DAY, start - DAY)
    tiers = tiering.compute(ds)
    focus = [pid for pid, t in tiers.items() if t["focus"]]
    frames = [ds.pdays(pid) for pid in focus]
    allf = pd.concat(frames)
    cur, prev = agg(slice_(allf.sort_index(), wk)), agg(slice_(allf.sort_index(), pwk))

    def chg(a, b):
        return r(a / b - 1) if b else None

    core = dict(gmv=dict(cur=round(cur["gmv"]), prev=round(prev["gmv"]), change_pct=chg(cur["gmv"], prev["gmv"])),
                uv=dict(cur=cur["uv"], prev=prev["uv"], change_pct=chg(cur["uv"], prev["uv"])),
                cvr=dict(cur=r(cur["cvr"]), prev=r(prev["cvr"]), change_pct=chg(cur["cvr"], prev["cvr"])),
                aov=dict(cur=round(cur["aov"], 2), prev=round(prev["aov"], 2), change_pct=chg(cur["aov"], prev["aov"])))
    items = []
    for pid in focus:
        g = ds.pdays(pid)
        a1, a0 = agg(slice_(g, wk)), agg(slice_(g, pwk))
        h = health.score(ds, pid)
        items.append(dict(product_id=pid, product_name=ds.product(pid)["product_name"], tier=tiers[pid]["tier_name"],
                          gmv=round(a1["gmv"]), gmv_prev=round(a0["gmv"]), change_pct=chg(a1["gmv"], a0["gmv"]),
                          health=h["score"], health_level=h["level"]))
    up = sorted([x for x in items if (x["change_pct"] or 0) > 0], key=lambda x: -(x["change_pct"] or 0))[:3]
    down = sorted([x for x in items if (x["change_pct"] or 0) < 0], key=lambda x: (x["change_pct"] or 0))[:3]
    risk = [x for x in items if x["health_level"] in ("风险", "关注")]

    wk_s, wk_e = wk[0].strftime("%Y-%m-%d"), wk[1].strftime("%Y-%m-%d")
    week_cards = [c for c in cards if c["last_trigger"] >= wk_s or c["first_date"] >= wk_s]
    alert_rows = []
    for c in week_cards:
        acts = [a for a in actions if a.get("card_id") == c["id"]]
        diag = (diagnoses or {}).get(c["product_id"]) or {}
        causes = [x.get("cause_name") for x in diag.get("root_causes", [])] or \
            sorted({a.get("cause_name") for a in acts if a.get("cause_name")})
        alert_rows.append(dict(product_name=c["product_name"], severity=config_sev(c["severity"]), rules=c["rule_names"],
                               first_date=c["first_date"], status=c.get("status_name") or c.get("status") or c["auto_status"],
                               causes=causes or None,
                               actions=[dict(name=a["name"], status=a["status"], exec_date=a.get("exec_date")) for a in acts]))
    sev_count = {"红": 0, "黄": 0, "蓝": 0}
    for c in week_cards:
        sev_count[config_sev(c["severity"])] += 1
    effects = []
    for a in actions:
        if a.get("exec_date"):
            e = effect(ds, a["product_id"], a.get("track_metric") or "gmv", a["exec_date"], variant=a.get("variant"))
            effects.append(dict(product_name=a.get("product_name"), action=a["name"], exec_date=a["exec_date"], **e))
    next_focus = []
    for c in cards:
        if c["is_today"] and c.get("status") not in ("ignored",):
            next_focus.append(dict(product_name=c["product_name"], severity=config_sev(c["severity"]), rules=c["rule_names"]))
    return dict(dataset=ds.name, category=ds.profile.get("name"), period=f"{wk_s} 至 {wk_e}",
                prev_period=f"{pwk[0]:%Y-%m-%d} 至 {pwk[1]:%Y-%m-%d}", focus_count=len(focus), core=core,
                top_up=up, top_down=down, health_watch=risk, alerts=dict(count=sev_count, items=alert_rows),
                effects=effects, next_week=next_focus)


def config_sev(s):
    return {"red": "红", "yellow": "黄", "blue": "蓝"}.get(s, s)


def render_rules(pack: dict) -> str:
    """不调用大模型时的周报：按模板拼出。"""
    c = pack["core"]

    def p(x):
        return "—" if x is None else f"{x:+.1%}"
    lines = [f"# {pack['category']}重点商品经营周报（{pack['period']}）", "",
             "> 本报告由规则模板生成（大模型不可用时的降级版本），数字均来自平台计算。", "",
             "## 一、本周结论", ""]
    worst = pack["top_down"][0] if pack["top_down"] else None
    best = pack["top_up"][0] if pack["top_up"] else None
    lines.append(f"重点商品池 {pack['focus_count']} 个商品，本周 GMV {c['gmv']['cur']:,} 元，环比 {p(c['gmv']['change_pct'])}。"
                 + (f"下滑最多的是{worst['product_name']}（{p(worst['change_pct'])}）。" if worst else "")
                 + (f"增长最多的是{best['product_name']}（{p(best['change_pct'])}）。" if best else ""))
    lines += ["", "## 二、核心指标", "", "| 指标 | 本周 | 上周 | 环比 |", "| --- | --- | --- | --- |",
              f"| GMV（元） | {c['gmv']['cur']:,} | {c['gmv']['prev']:,} | {p(c['gmv']['change_pct'])} |",
              f"| 访客数 | {c['uv']['cur']:,} | {c['uv']['prev']:,} | {p(c['uv']['change_pct'])} |",
              f"| 支付转化率 | {c['cvr']['cur']:.2%} | {c['cvr']['prev']:.2%} | {p(c['cvr']['change_pct'])} |",
              f"| 客单价（元） | {c['aov']['cur']:.2f} | {c['aov']['prev']:.2f} | {p(c['aov']['change_pct'])} |", "",
              "## 三、商品表现", ""]
    for title, xs in (("增长 Top 3", pack["top_up"]), ("下滑 Top 3", pack["top_down"])):
        lines.append(f"**{title}**")
        lines += [f"- {x['product_name']}：GMV {x['gmv']:,} 元（{p(x['change_pct'])}）" for x in xs] or ["- 本周无"]
        lines.append("")
    if pack["health_watch"]:
        lines.append("**健康度需关注**")
        lines += [f"- {x['product_name']}：{x['health']} 分（{x['health_level']}）" for x in pack["health_watch"]]
        lines.append("")
    a = pack["alerts"]
    lines += ["## 四、本周预警与处置", "", f"本周问题卡：红 {a['count']['红']} 张、黄 {a['count']['黄']} 张、蓝 {a['count']['蓝']} 张。", ""]
    for x in a["items"]:
        acts = "；".join(f"{y['name']}（{y['status']}）" for y in x["actions"]) or "暂无动作"
        cause = "、".join(x["causes"]) if x["causes"] else "待诊断"
        lines.append(f"- 【{x['severity']}】{x['product_name']}：{'、'.join(x['rules'])} → 原因：{cause} → 方案：{acts}")
    lines += ["", "## 五、动作效果", ""]
    if pack["effects"]:
        lines += ["| 商品 | 动作 | 执行日 | 跟踪指标 | 执行前 | 执行后 | 变化 |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for e in pack["effects"]:
            fmt = (lambda v: f"{v:.2%}") if e["metric"] == "cvr" else (lambda v: f"{v:,.1f}")
            if e["status"] == "已完成":
                lines.append(f"| {e['product_name']} | {e['action']} | {e['exec_date']} | {e['metric_name']} | {fmt(e['before'])} | {fmt(e['after'])} | {p(e['change_pct'])} |")
            else:
                lines.append(f"| {e['product_name']} | {e['action']} | {e['exec_date']} | {e['metric_name']} | — | — | {e['status']} |")
        lines += ["", "注：执行前后对比，不等同于严格的因果效果。"]
    else:
        lines.append("本周无已执行动作。")
    lines += ["", "## 六、下周关注", ""]
    lines += [f"- 【{x['severity']}】{x['product_name']}：{'、'.join(x['rules'])}" for x in pack["next_week"]] or ["- 本周无"]
    return "\n".join(lines)
