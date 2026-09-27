"""经营总览（v13）：给运营负责人每天早上看盘用，按三个问题排——
① 生意怎么样：一句话结论（规则拼出来，数字全部来自平台）+ 月目标进度 + 核心指标
② 为什么变：GMV 变化拆成访客 / 转化 / 客单价；拉动和拖累最多的商品，贴上周报同样的标签
③ 今天做什么：待决定的预警、待办提醒、即将到来的节日与库存
看的范围可以切换：重点商品池 / 全店。
"""
from __future__ import annotations

import calendar
import datetime as dt

import pandas as pd

from core import alerts
from core import charts as C
from core.metrics import DAY, agg, r, slice_, windows
from core.reports.weekly import LABELS, _label

from . import data, state

SCOPES = {"focus": "重点商品", "all": "全店"}
MOVERS = 3


def _money(v) -> str:
    return C.fmt(abs(v), "money")


def _signed(v) -> str:
    return ("+" if v >= 0 else "-") + _money(v)


def _pct(v) -> str:
    return f"{v:+.1%}" if v is not None else "—"


def _dos(name: str, pid: str) -> float | None:
    """重点规格（平时占销量 ≥5%）里最短的库存可售天数；主力规格断货记 0。"""
    from core.decompose import breakdown_variants
    ds = data.ds_of(name)
    if "stock" not in ds.available:
        return None
    bv = breakdown_variants(ds, pid)
    vals = [v["days_of_supply"] for v in bv["variants"] if v.get("days_of_supply") is not None and (v.get("baseline_share") or 0) >= 0.05]
    if any(v.get("stock_now") == 0 and (v.get("baseline_share") or 0) >= 0.15 for v in bv["variants"]):
        return 0.0
    return min(vals) if vals else None


def target_progress(name: str) -> dict:
    """月度 GMV 目标（全店口径，与周报一致；在报告中心设置）。"""
    from .agent.report import get_target
    ds = data.ds_of(name)
    end = ds.as_of
    month = end.strftime("%Y-%m")
    t = get_target(name, month)
    out = dict(month=month, month_name=f"{end.month} 月", target=t)
    if not t:
        return out
    m0 = end.replace(day=1)
    dim = calendar.monthrange(end.year, end.month)[1]
    el = (end - m0).days + 1
    mtd = sum(float(slice_(ds.pdays(p), (m0, end))["gmv"].sum()) for p in ds.product_ids())
    fc = mtd / el * dim
    out.update(mtd=round(mtd), progress=r(mtd / t), time_progress=r(el / dim), forecast=round(fc), gap=round(fc - t),
               days_left=dim - el, on_track=fc >= t)
    return out


def movers(name: str, pids: list[str], cur, prev, cards: dict) -> tuple[list, list]:
    ds = data.ds_of(name)
    rows = []
    for p in pids:
        g = ds.pdays(p)
        a1, a0 = float(slice_(g, cur)["gmv"].sum()), float(slice_(g, prev)["gmv"].sum())
        rows.append(dict(product_id=p, product_name=ds.product(p)["product_name"], gmv=round(a1), gmv_prev=round(a0),
                         change=round(a1 - a0), change_pct=r(a1 / a0 - 1) if a0 else None))
    down = sorted([x for x in rows if x["change"] < 0], key=lambda x: x["change"])[:MOVERS]
    up = sorted([x for x in rows if x["change"] > 0], key=lambda x: -x["change"])[:MOVERS]
    for x in down + up:
        c = cards.get(x["product_id"])
        diag = None
        if c and c["severity"] in ("red", "yellow") and not c.get("expected"):
            from . import alert_detail
            try:
                diag = alert_detail.diagnosis_now(name, x["product_id"])
            except Exception:  # noqa: BLE001
                diag = None
        lab, why = _label(ds, x["product_id"], x["change"], cur, prev, c, diag)
        x.update(label=LABELS[lab][0], label_key=lab, tone=LABELS[lab][1], reason=why,
                 alert=None if not c else dict(id=c["id"], status=c["status"], status_name=c["status_name"],
                                                severity=c["severity"], group=c["group"]))
    return down, up


def headline(scope_name: str, win: str, k: dict, verdict: str, main_factor: dict | None, down: list, up: list) -> str:
    g = k["gmv"]
    s = f"近 7 天（{win}）{scope_name} GMV {_money(g['cur'])}，比前 7 天 {_pct(g['change'])}，{verdict}。"
    if main_factor and g["change"] is not None and abs(g["change"]) >= 0.005:
        s += f"变化主要来自{main_factor['name']}（{_pct(main_factor['change_pct'])}，影响 {_signed(main_factor['amount'])}）。"
    drag = [x for x in down if x["label_key"] in ("needs", "adjust", "expected")][:2] or down[:1]
    if drag:
        s += "拖累最多的是" + "、".join(f"{x['product_name']}（{_signed(x['change'])}，{x['label']}）" for x in drag)
        s += "；" if up else "。"
    if up:
        x = up[0]
        s += f"{'拉动' if drag else '拉动最多的是'}{x['product_name']}（{_signed(x['change'])}，{x['label']}）。"
    return s


def todo_summary(name: str) -> dict:
    from . import todos
    today = data.ds_of(name).as_of.strftime("%Y-%m-%d")
    acts = [todos.advance(name, a) for a in state.list_actions(name)]
    hs = state.list_handoffs(name)
    doing = [a for a in acts if a["status"] == "doing"]
    return dict(overdue=sum(1 for a in doing if a.get("due_date") and a["due_date"] < today),
                due_today=sum(1 for a in doing if a.get("due_date") == today),
                review=sum(1 for a in acts if a["status"] == "review"),
                question=sum(1 for h in hs if h["status"] == "question"),
                waiting=sum(1 for h in hs if h["status"] == "notified"),
                open=sum(1 for a in acts if a["status"] in ("doing", "tracking", "review")))


def upcoming(name: str, pids: list[str], horizon: int = 30) -> list[dict]:
    """未来 horizon 天内的节日 / 大促，以及库存撑不到那天的商品（可售天数 < 距离天数 + 补货周期里还能补的余量）。"""
    from core import config
    ds = data.ds_of(name)
    today = ds.as_of.date()
    lead = int(ds.profile.get("replenish_lead_days", 7))
    out = []
    evs = []
    for f in (config.calendar().get("festivals") or []):
        d = f["date"] if isinstance(f["date"], dt.date) else pd.Timestamp(f["date"]).date()
        evs.append(dict(name=f["name"], date=d, kind="节日"))
    ev = ds.events
    if not ev.empty:
        for _, e in ev[(ev["date"] > ds.as_of) & (ev["event_type"].isin(["promo_day", "campaign_start"]))].iterrows():
            evs.append(dict(name=str(e["description"]), date=e["date"].date(), kind="活动"))
    seen = set()
    for e in sorted(evs, key=lambda x: x["date"]):
        days = (e["date"] - today).days
        if days <= 0 or days > horizon or e["name"] in seen:
            continue
        seen.add(e["name"])
        short = []
        for p in pids:
            v = _dos(name, p)
            if v is None or v >= days:
                continue
            rs = [x for x in alerts._restocks(ds, p, ds.as_of) if x["date"].date() < e["date"]]
            transit = f"在途补货 {rs[0]['date'].month}/{rs[0]['date'].day} 到" if rs else None
            short.append(dict(product_id=p, product_name=ds.product(p)["product_name"], days_of_supply=r(v, 1),
                              transit=transit, can_restock=days > lead,
                              status="节前有货到" if transit else "来得及补货" if days > lead else "补货来不及"))
        short.sort(key=lambda x: (x["transit"] is not None, x["days_of_supply"]))
        out.append(dict(name=e["name"], date=e["date"].strftime("%Y-%m-%d"), days=days, kind=e["kind"], short=short[:5],
                        short_total=sum(1 for x in short if not x["transit"]), lead_days=lead))
    return out[:3]


def build(name: str, scope: str = "focus") -> dict:
    scope = scope if scope in SCOPES else "focus"
    ds = data.ds_of(name)
    t = data.tiers(name)
    focus = [p for p, v in t.items() if v["focus"]]
    pids = focus if scope == "focus" else ds.product_ids()
    cur, prev = windows(ds.as_of, 7)
    win = f"{cur[0]:%m-%d}–{cur[1]:%m-%d}"
    allf = pd.concat([ds.pdays(p) for p in pids]).sort_index()
    a1, a0 = agg(slice_(allf, cur)), agg(slice_(allf, prev))

    def chg(x, y):
        return r(x / y - 1) if y else None
    kpi = dict(gmv=dict(cur=round(a1["gmv"]), prev=round(a0["gmv"]), change=chg(a1["gmv"], a0["gmv"])),
               uv=dict(cur=a1["uv"], prev=a0["uv"], change=chg(a1["uv"], a0["uv"])),
               cvr=dict(cur=r(a1["cvr"]), prev=r(a0["cvr"]), change=chg(a1["cvr"], a0["cvr"])),
               aov=dict(cur=round(a1["aov"], 2), prev=round(a0["aov"], 2), change=chg(a1["aov"], a0["aov"])))

    # ② 为什么变
    fw = C.factor_waterfall(ds, pids, cur, prev)
    raw = fw["raw"]
    delta = raw["v1"]["gmv"] - raw["v0"]["gmv"]
    names = {"uv": "访客数", "cvr": "支付转化率", "aov": "客单价"}
    factors = [dict(key=k, name=names[k], amount=round(raw["contrib"][k]),
                    change_pct=r(raw["v1"][k] / raw["v0"][k] - 1) if raw["v0"][k] else None,
                    share=r(raw["contrib"][k] / delta) if delta else None) for k in ("uv", "cvr", "aov")]
    main_factor = max(factors, key=lambda f: abs(f["amount"])) if delta else None
    wf = dict(type="waterfall", unit="money", title="GMV 变化拆解：访客 × 转化率 × 客单价",
              start=fw["start"], end=fw["end"], items=fw["items"], id="ov_wf", origin="required")
    cards_all = data.cards(name, include_store=False)
    today_cards = {c["product_id"]: c for c in cards_all if c["is_today"]}
    down, up = movers(name, pids, cur, prev, today_cards)

    band = ds.profile.get("report", {}).get("flat_band", 0.05)
    pend = [c for c in cards_all if c["group"] == "pending" and c["product_id"] in pids]
    g = kpi["gmv"]["change"]
    if g is not None and g > band:
        verdict = "整体上升"
    elif g is not None and g < -band:
        verdict = "整体下滑"
    elif pend:
        verdict = f"大盘平稳，但有 {len(pend)} 个商品的预警待你决定"
    else:
        verdict = "整体平稳"

    # 趋势：合计与按分层
    daily = allf.groupby(level=0)["gmv"].sum()
    trend = [dict(date=d.strftime("%Y-%m-%d"), gmv=round(float(v))) for d, v in daily.tail(90).items()]
    by_tier = {}
    for p in pids:
        by_tier.setdefault(t[p]["tier_name"], []).append(p)
    order = ["爆品", "潜力品", "利润品", "长尾品"]
    tier_trend = []
    for tn in sorted(by_tier, key=lambda x: order.index(x) if x in order else 9):
        f = pd.concat([ds.pdays(p) for p in by_tier[tn]]).groupby(level=0)["gmv"].sum()
        f = f.reindex(daily.tail(90).index)                     # 按日期对齐（新品上市前没有数据）
        tier_trend.append(dict(name=f"{tn}（{len(by_tier[tn])}）",
                               values=[None if pd.isna(v) else round(float(v)) for v in f.tolist()]))
    events = []
    ev = ds.events
    if not ev.empty:
        for _, e in ev[ev["date"] <= ds.as_of].iterrows():
            if e["product_id"] in ("", None) or e["product_id"] in pids:
                events.append(dict(date=e["date"].strftime("%Y-%m-%d"), product_id=e["product_id"], type=e["event_type"],
                                   description=e["description"]))

    # ③ 今天做什么
    pending = [c for c in data.cards(name) if c["group"] in ("pending", "opportunity")]
    pending.sort(key=lambda c: (c["group"] != "pending", {"red": 0, "yellow": 1, "blue": 2}[c["severity"]], -c["gmv_impact"]))
    todo_alerts = [dict(id=c["id"], product_id=c["product_id"], product_name=c["product_name"], severity=c["severity"],
                        rule_names=c["rule_names"], gmv_impact=c["gmv_impact"], status_name=c["status_name"], group=c["group"],
                        first_date=c["first_date"], days_open=c["days_open"], reopen_name=c.get("reopen_name"),
                        store=c.get("store"), in_scope=c["product_id"] in pids) for c in pending]

    return dict(dataset=name, dataset_name=data.DATASETS.get(name), as_of=ds.as_of.strftime("%Y-%m-%d"),
                scope=scope, scope_name=SCOPES[scope], scopes=SCOPES, focus_count=len(focus), product_count=len(ds.product_ids()),
                window=7, cur_window=win, prev_window=f"{prev[0]:%m-%d}–{prev[1]:%m-%d}",
                headline=headline(SCOPES[scope], win, kpi, verdict, main_factor, down, up), verdict=verdict,
                target=target_progress(name), kpi=kpi,
                factors=factors, waterfall=wf, movers=dict(down=down, up=up),
                trend=trend, dates=[x["date"] for x in trend], tier_trend=tier_trend, events=events,
                alerts=dict(items=todo_alerts[:6], pending=sum(1 for c in pending if c["group"] == "pending"),
                            opportunity=sum(1 for c in pending if c["group"] == "opportunity")),
                todos=todo_summary(name), upcoming=upcoming(name, pids),
                profile_name=ds.profile.get("name"))
