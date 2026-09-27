"""数据集注册与业务视图：总览、商品、问题卡（叠加用户状态）。"""
from __future__ import annotations

import copy
import os
import threading
import time
from pathlib import Path

import pandas as pd

from core import charts, alerts, config, health, loader, tiering
from core.decompose import breakdown_channels, breakdown_variants, decompose_gmv
from core.metrics import DAY, agg, r, slice_, windows

from . import state

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data" / "datasets"))
DATASETS = {"3c": "3C 数码配件", "snacks": "休闲零食"}

_lock = threading.RLock()
_cache: dict = {}

# 预警设置页可以调整的参数：(配置键, 名称, 单位, 影响的规则)。单位：pct 百分比 / x 倍数 / day 天 / yuan 元 / num 数值 / bool 开关
ALERT_PARAMS = [
    ("gmv_drop_yellow", "GMV 下滑 · 黄色门槛", "pct", ["R01"]),
    ("gmv_drop_red", "GMV 下滑 · 红色门槛", "pct", ["R01"]),
    ("z_threshold", "偏离正常波动的倍数", "x", ["R02", "R03", "R08"]),
    ("stockout_share_min", "断货规格平时至少占销量", "pct", ["R04"]),
    ("replenish_lead_days", "补货周期", "day", ["R05"]),
    ("price_gap", "比竞品贵多少算劣势", "pct", ["R06"]),
    ("rating_drop", "评分下降多少", "num", ["R07"]),
    ("alert.min_impact", "影响金额下限", "yuan", []),
    ("alert.tail_stock_only", "长尾商品只监控断货类规则", "bool", []),
    ("alert.recover_ratio", "恢复标准：回到出问题前水平的比例", "pct", []),
    ("alert.ignore_silence_days", "忽略后同类预警静默", "day", []),
]
PARAM_KEYS = {k for k, *_ in ALERT_PARAMS}


def _get_path(d: dict, key: str):
    for k in key.split("."):
        d = (d or {}).get(k)
    return d


def _set_path(d: dict, key: str, v):
    ks = key.split(".")
    for k in ks[:-1]:
        d = d.setdefault(k, {})
    d[ks[-1]] = v


def alert_config(name: str) -> dict:
    """预警设置：关掉的规则、调整过的参数、调整记录。"""
    c = state.get_setting(f"alert_config__{name}", {}) or {}
    return dict(disabled=list(c.get("disabled") or []), params=dict(c.get("params") or {}), log=list(c.get("log") or []))


def custom_alerts(name: str) -> list[dict]:
    return list(state.get_setting(f"custom_alerts__{name}", []) or [])


def _scan(name: str):
    """按预警设置重新计算品类配置并扫描。"""
    c = _cache[name]
    ds = c["ds"]
    cfg = alert_config(name)
    prof = copy.deepcopy(c["base_profile"])
    for k, v in cfg["params"].items():
        if k in PARAM_KEYS:
            _set_path(prof, k, v)
    ds.profile = prof
    enabled = set(alerts.RULE_NAMES) - set(cfg["disabled"])
    c["base_cards"] = alerts.scan(ds, enabled=enabled, custom=custom_alerts(name))
    c["scanned_at"] = time.time()


def get(ds_name: str):
    if ds_name not in DATASETS:
        ds_name = "3c"
    with _lock:
        if ds_name not in _cache:
            ds = loader.load(DATA_DIR / ds_name, name=ds_name)
            _cache[ds_name] = {"ds": ds, "base_profile": copy.deepcopy(ds.profile), "health": {}}
            _scan(ds_name)
        return _cache[ds_name]


def rescan(name: str):
    get(name)
    with _lock:
        _scan(name)
    return _cache[name]


def default_param(name: str, key: str):
    return _get_path(get(name)["base_profile"], key)


def ds_of(name):
    return get(name)["ds"]


def tiers(name):
    return tiering.compute(ds_of(name), focus_override=state.focus_overrides(name))


def health_of(name, pid):
    c = get(name)
    if pid not in c["health"]:
        c["health"][pid] = health.score(c["ds"], pid)
    return c["health"][pid]


def cards(name, include_suppressed: bool = False, include_store: bool = True) -> list[dict]:
    """问题卡叠加处理状态（见 alert_flow）。低于影响金额下限的「量」类预警默认不出卡（已经处理过的保留）。"""
    from . import alert_flow
    from core.custom_alerts import STORE
    base = get(name)["base_cards"]
    if not include_suppressed:
        st = state.card_states(name)
        base = [c for c in base if not c.get("suppressed") or (st.get(c["id"]) or {}).get("status")]
    if not include_store:
        base = [c for c in base if c["product_id"] != STORE]
    return alert_flow.decorate(name, base)


def product_cards(name) -> list[dict]:
    return cards(name, include_store=False)


def card_for(name, pid, today_only=False):
    cs = [c for c in cards(name, include_store=False) if c["product_id"] == pid]
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
    cmap = {c["product_id"]: c for c in cards(name, include_store=False) if c["is_today"]}
    rows = [product_row(name, pid, t[pid], cmap) for pid in ds_of(name).product_ids()]
    if focus_only:
        rows = [x for x in rows if x["focus"]]
    order = {"hero": 0, "rising": 1, "profit": 2, "tail": 3}
    rows.sort(key=lambda x: (order[x["tier_id"]], -x["gmv"]))
    return rows


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
    all_cards = [c for c in cards(name, include_store=False) if c["product_id"] == pid]
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
    state.upsert_card(name, c["id"], status="doing", decision="todo", action_row=aid, product_id="P05", severity=c["severity"],
                      rules_json=json.dumps(c["rules"]),
                      log_json=json.dumps([dict(t=t0, date="2026-09-13", by="我", text="转待办：紧急补货")], ensure_ascii=False))
    state.add_handoff(ds=name, action_row=aid, product_id="P05", kind="transfer", role="供应链", assignee=None,
                      steps_json=json.dumps([0]), message="【协同请求】编织快充数据线 · 紧急补货", status="done",
                      channel="copy", due="2026-09-14", sent_at=t0, history_json=json.dumps(hist, ensure_ascii=False))
