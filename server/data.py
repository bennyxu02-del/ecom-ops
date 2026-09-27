"""数据集注册与业务视图：总览、商品、问题卡（叠加用户状态）。"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pandas as pd

from core import charts, alerts, config, health, loader, tiering
from core.decompose import breakdown_channels, breakdown_variants, decompose_gmv
from core.metrics import DAY, agg, r, slice_, windows

from . import state

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data" / "datasets"))
DATASETS = {"3c": "3C 数码配件", "snacks": "休闲零食"}

STATUS_NAMES = {"pending": "待处理", "processing": "处理中", "done": "已处理", "ignored": "已忽略", "recovered": "已恢复"}
_lock = threading.Lock()
_cache: dict = {}


def get(ds_name: str):
    if ds_name not in DATASETS:
        ds_name = "3c"
    with _lock:
        if ds_name not in _cache:
            ds = loader.load(DATA_DIR / ds_name, name=ds_name)
            _cache[ds_name] = {"ds": ds, "base_cards": alerts.scan(ds), "health": {}}
        return _cache[ds_name]


def ds_of(name):
    return get(name)["ds"]


def tiers(name):
    return tiering.compute(ds_of(name), focus_override=state.focus_overrides(name))


def health_of(name, pid):
    c = get(name)
    if pid not in c["health"]:
        c["health"][pid] = health.score(c["ds"], pid)
    return c["health"][pid]


def cards(name) -> list[dict]:
    base = get(name)["base_cards"]
    st = state.card_states(name)
    out = []
    for c in base:
        c = dict(c)
        s = st.get(c["id"])
        if s:
            status = s["status"]
            c["ignore_reason"] = s.get("reason")
        else:
            status = "recovered" if c["auto_status"] == "recovered" else "pending"
        c["status"] = status
        c["status_name"] = STATUS_NAMES[status]
        c["severity_name"] = alerts.SEV_NAME[c["severity"]]
        out.append(c)
    return out


def card_for(name, pid, today_only=False):
    cs = [c for c in cards(name) if c["product_id"] == pid]
    if today_only:
        cs = [c for c in cs if c["is_today"]]
    return cs[0] if cs else None


def datasets_info():
    out = []
    for k, v in DATASETS.items():
        ds = ds_of(k)
        out.append(dict(id=k, name=v, as_of=ds.as_of.strftime("%Y-%m-%d"), products=len(ds.product_ids()),
                        profile=ds.profile.get("name")))
    return out


def _chg(a, b):
    return r(a / b - 1) if b else None


def product_row(name, pid, t, cmap):
    ds = ds_of(name)
    g = ds.pdays(pid)
    cur_w, prev_w = windows(ds.as_of, 7)
    a1, a0 = agg(slice_(g, cur_w)), agg(slice_(g, prev_w))
    p = ds.product(pid)
    h = health_of(name, pid)
    dos = None
    if "stock" in ds.available:
        bv = breakdown_variants(ds, pid)
        vals = [v["days_of_supply"] for v in bv["variants"] if v.get("days_of_supply") is not None and (v.get("baseline_share") or 0) >= 0.05]
        oos = any(v.get("stock_now") == 0 and (v.get("baseline_share") or 0) >= 0.15 for v in bv["variants"])
        dos = 0.0 if oos else (min(vals) if vals else None)
    c = cmap.get(pid)
    return dict(product_id=pid, product_name=p["product_name"], sub_category=p.get("sub_category"),
                tier=t["tier_name"], tier_id=t["tier"], lifecycle=t["lifecycle_name"], focus=t["focus"],
                gmv=round(a1["gmv"]), gmv_change=_chg(a1["gmv"], a0["gmv"]), uv=a1["uv"], uv_change=_chg(a1["uv"], a0["uv"]),
                cvr=r(a1["cvr"]), cvr_change=_chg(a1["cvr"], a0["cvr"]), aov=round(a1["aov"], 2),
                price=float(g["price"].iloc[-1]) if "price" in ds.available else None,
                days_of_supply=dos, health=h["score"], health_level=h["level"],
                alert=None if not c else dict(id=c["id"], severity=c["severity"], status=c["status"], status_name=c["status_name"]),
                spark=[round(float(v)) for v in g["gmv"].tail(28).tolist()])


def products(name, focus_only=False):
    t = tiers(name)
    cmap = {c["product_id"]: c for c in cards(name) if c["is_today"]}
    rows = [product_row(name, pid, t[pid], cmap) for pid in ds_of(name).product_ids()]
    if focus_only:
        rows = [x for x in rows if x["focus"]]
    order = {"hero": 0, "rising": 1, "profit": 2, "tail": 3}
    rows.sort(key=lambda x: (order[x["tier_id"]], -x["gmv"]))
    return rows


def overview(name, window=7):
    ds = ds_of(name)
    t = tiers(name)
    focus = [pid for pid, v in t.items() if v["focus"]]
    allf = pd.concat([ds.pdays(pid) for pid in focus]).sort_index()
    cur_w, prev_w = windows(ds.as_of, window)
    a1, a0 = agg(slice_(allf, cur_w)), agg(slice_(allf, prev_w))
    daily = allf.groupby(level=0)[["gmv", "uv", "buyers"]].sum()
    trend = [dict(date=d.strftime("%Y-%m-%d"), gmv=round(float(v["gmv"])), uv=int(v["uv"])) for d, v in daily.tail(90).iterrows()]
    by_tier = {}
    for pid in focus:
        by_tier.setdefault(t[pid]["tier_name"], []).append(pid)
    tier_trend = {}
    for tn, pids in by_tier.items():
        f = pd.concat([ds.pdays(p) for p in pids]).groupby(level=0)["gmv"].sum()
        tier_trend[tn] = [round(float(v)) for v in f.tail(90).tolist()]
    cs = cards(name)
    today = [c for c in cs if c["is_today"]]
    sev = {"red": 0, "yellow": 0, "blue": 0}
    for c in today:
        sev[c["severity"]] += 1
    hl = {"健康": 0, "关注": 0, "风险": 0}
    for pid in focus:
        lv = health_of(name, pid)["level"]
        if lv:
            hl[lv] += 1
    todo = [c for c in today if c["status"] in ("pending", "processing")]
    events = []
    ev = ds.events
    if not ev.empty:
        for _, e in ev[ev["date"] <= ds.as_of].iterrows():
            events.append(dict(date=e["date"].strftime("%Y-%m-%d"), product_id=e["product_id"], type=e["event_type"],
                               description=e["description"]))
    return dict(dataset=name, dataset_name=DATASETS.get(name), as_of=ds.as_of.strftime("%Y-%m-%d"), window=window,
                cur_window=f"{cur_w[0]:%m-%d}~{cur_w[1]:%m-%d}", prev_window=f"{prev_w[0]:%m-%d}~{prev_w[1]:%m-%d}",
                focus_count=len(focus), product_count=len(ds.product_ids()),
                kpi=dict(gmv=dict(cur=round(a1["gmv"]), change=_chg(a1["gmv"], a0["gmv"])),
                         uv=dict(cur=a1["uv"], change=_chg(a1["uv"], a0["uv"])),
                         cvr=dict(cur=r(a1["cvr"]), change=_chg(a1["cvr"], a0["cvr"])),
                         aov=dict(cur=round(a1["aov"], 2), change=_chg(a1["aov"], a0["aov"]))),
                alerts=dict(today=sev, pending=len(todo)), health=hl, trend=trend, tier_trend=tier_trend,
                dates=[x["date"] for x in trend],
                todo=[dict(id=c["id"], product_id=c["product_id"], product_name=c["product_name"], severity=c["severity"],
                           rule_names=c["rule_names"], gmv_impact=c["gmv_impact"], status_name=c["status_name"],
                           first_date=c["first_date"], trigger_days=c["trigger_days"]) for c in todo][:6],
                events=events, profile_name=ds.profile.get("name"))


def product_detail(name, pid):
    ds = ds_of(name)
    t = tiers(name)[pid]
    p = ds.product(pid)
    g = ds.pdays(pid)
    series = dict(dates=[d.strftime("%Y-%m-%d") for d in g.index], gmv=[round(float(v)) for v in g["gmv"]],
                  uv=[int(v) for v in g["uv"]], cvr=[round(float(b / u), 4) if u else 0 for b, u in zip(g["buyers"], g["uv"])],
                  aov=[round(float(m / b), 2) if b else 0 for m, b in zip(g["gmv"], g["buyers"])])
    if "price" in ds.available:
        series["price"] = [float(v) for v in g["price"]]
    if "comp_price" in ds.available:
        series["comp_price"] = [float(v) for v in g["comp_price"]]
    if "rating" in ds.available:
        series["rating"] = [float(v) for v in g["rating"]]
    if "stock" in ds.available:
        st = ds.vdays(pid).groupby("date")["stock_units"].sum()
        series["stock"] = [int(st.get(d, 0)) for d in g.index]
    ev = ds.events
    events = []
    if not ev.empty:
        for _, e in ev[ev["product_id"].isin([pid, ""])].sort_values("date").iterrows():
            events.append(dict(date=e["date"].strftime("%Y-%m-%d"), type=e["event_type"], description=e["description"],
                               future=bool(e["date"] > ds.as_of)))
    card = card_for(name, pid)
    all_cards = [c for c in cards(name) if c["product_id"] == pid]
    return dict(product_id=pid, product_name=p["product_name"], category=DATASETS.get(name), sub_category=p.get("sub_category"),
                launch_date=str(pd.Timestamp(p["launch_date"]).date()), cost_price=p.get("cost_price"),
                tier=t["tier_name"], lifecycle=t["lifecycle_name"], coef=t["coef"], focus=t["focus"], margin=t["margin"],
                health=health_of(name, pid), as_of=ds.as_of.strftime("%Y-%m-%d"),
                decompose=decompose_gmv(ds, pid), channels=breakdown_channels(ds, pid), variants=breakdown_variants(ds, pid),
                series=series, events=events, card=card, cards=all_cards,
                actions=state.list_actions(name, pid))


def methods(name):
    ds = ds_of(name)
    return dict(profile=ds.profile, metrics=config.metrics(), tree=config.tree(), tiering=config.tiering_cfg(),
                alert_rules=config.alert_rules(), action_library=config.action_library(),
                sop=config.read_text("attribution_sop.md"),
                playbooks=dict(weekly=config.read_text("playbooks/weekly_review.md"),
                               campaign=config.read_text("playbooks/campaign_review.md"),
                               product=config.read_text("playbooks/product_diagnosis.md"),
                               writing=config.read_text("playbooks/writing_rules.md")),
                chart_library=charts.library_doc(), chart_tool=charts.TOOL_SPEC, calendar=config.calendar())


def seed():
    """演示预置：案例 E（编织快充数据线）上周采纳并完成的补货待办，跟踪期已满，演示开始时处于「待复盘」。"""
    import json
    import time
    from core import actions as core_actions
    name = "3c"
    ds = ds_of(name)
    c = next((c for c in get(name)["base_cards"] if c["product_id"] == "P05"), None)
    if not c:
        return
    state.set_card(name, c["id"], "done")
    plan = core_actions.annotate(dict(
        action_id="replenish", name="紧急补货", cause="stockout", cause_name="规格断货",
        target="编织快充数据线 · 1m 白色", params=dict(variant="1m 白色"),
        steps=["确认 1m 白色补货量，安排 9 月 14 日前到货", "到货后恢复 1m 白色的正常售卖与投放"],
        step_owners=["供应链", "我"], track=dict(metric="cvr", metric_name="支付转化率", days=5), source="diagnosis"))
    t0 = time.time() - 6 * 86400
    log = [dict(t=t0, by="我", text="采纳 AI 诊断方案"),
           dict(t=t0 + 3600, by="系统", text="供应链 已完成"),
           dict(t=t0 + 7200, by="系统", text="所有步骤完成，开始跟踪效果（5 天）")]
    aid = state.add_action(ds=name, card_id=c["id"], product_id="P05", product_name=ds.product("P05")["product_name"],
                           action_id="replenish", name="紧急补货", cause="stockout", cause_name="规格断货",
                           target=plan["target"], plan_json=json.dumps(plan, ensure_ascii=False), exec_type=plan["exec_type"],
                           owner_role=plan["owner_role"], status="tracking", track_metric="cvr", track_days=5,
                           variant="1m 白色", adopted_date="2026-09-13", exec_date="2026-09-14", due_date="2026-09-14",
                           step_done=json.dumps([1]), source="diagnosis",
                           context_json=json.dumps(dict(summary="1m 白色 9 月 13 日断货，转化率明显下滑"), ensure_ascii=False),
                           log_json=json.dumps(log, ensure_ascii=False))
    hist = [dict(t=t0, status="notified", by="我", note="飞书推送"), dict(t=t0 + 3600, status="done", by="供应链", note=None)]
    state.add_handoff(ds=name, action_row=aid, product_id="P05", kind="transfer", role="供应链", assignee=None,
                      steps_json=json.dumps([0]), message="【协同请求】编织快充数据线 · 紧急补货", status="done",
                      channel="copy", due="2026-09-14", sent_at=t0, history_json=json.dumps(hist, ensure_ascii=False))
