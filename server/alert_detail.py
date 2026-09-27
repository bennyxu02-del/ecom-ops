"""预警处理面板的数据：出了什么事 → AI 初判和方案 → 对话记录 → 决定与处理记录。"""
from __future__ import annotations

import copy

import pandas as pd

from core import actions as core_actions
from core import charts as C
from core import sop, tools
from core.metrics import DAY
from core.reports.common import events_between
from core.reports.product import CAUSE_CHART, MARK_TYPES, excluded_causes

from . import alert_flow, data, state

QUICK = {
    "price_disadvantage": ["券改成 15 元行不行", "换一个不降价的方案", "为什么不是流量问题"],
    "stockout": ["补货已经安排了，先不用处理", "供应链那步写细一点，要他反馈到货数量", "到货前能做点什么"],
    "stockout_risk": ["补货已经安排了，先不用处理", "供应链那步写细一点，要他反馈到货数量"],
    "paid_traffic_drop": ["这是我们主动减的预算", "投放那步写细一点"],
    "growth_opportunity": ["库存撑得住吗", "怎么把这波流量接住"],
}
QUICK_DEFAULT = ["这个问题我们已经知道原因了", "为什么是这个原因", "换一个方案"]


def diagnosis_now(name: str, pid: str) -> dict:
    """面板里的 AI 初判：优先用已经跑好的 AI 诊断（缓存），没有时用规则诊断（不等模型）。"""
    from .agent import diagnose
    key = diagnose.cache_key(name, pid)
    hit = state.cache_get(key)
    if hit:
        res = copy.deepcopy(hit["result"])
        res["cached"] = True
    else:
        rules = state.cache_get(key + "__rules")
        if rules:
            res = copy.deepcopy(rules["result"])
        else:
            steps, res = sop.run(data.ds_of(name), pid, card=data.card_for(name, pid), tiers=data.tiers(name))
            res["source"] = "rules"
            state.cache_put(key + "__rules", dict(steps=[dict(type="step", tool=s["tool"], args=s["args"], summary=s["summary"])
                                                         for s in steps], result=res))
        res["cached"] = False
    for p in res.get("plans") or []:
        core_actions.annotate(p)
    return res


def _chart(ds, pid: str, cause: str | None) -> dict | None:
    end = ds.as_of
    if cause in CAUSE_CHART:
        types = MARK_TYPES.get(cause)
        ev = {}
        evs = events_between(ds, pid, end - 27 * DAY, end)
        mk = [e for e in evs if types and e["type"] in types and pd.Timestamp(e["date"]) <= end]
        if mk:
            ev = dict(mark_date=mk[-1]["date"], mark_text=mk[-1]["description"][:12])
        spec = C.draw(ds, CAUSE_CHART[cause](pid, ev))
        if "error" not in spec:
            return spec
    spec = C.draw(ds, dict(type="trend", products=[pid], metrics=["gmv"], title="近 28 天每日 GMV"))
    return None if "error" in spec else spec


def _public(spec: dict | None) -> dict | None:
    if not spec:
        return None
    return dict({k: v for k, v in spec.items() if k != "values"}, id="c1", origin="required")


def _store_chart(ds, metric="gmv") -> dict | None:
    spec = C.draw(ds, dict(type="trend", products=ds.product_ids(), metrics=[metric], title="全店近 28 天走势"))
    return None if "error" in spec else spec


def facts(name: str, c: dict, res: dict | None) -> dict:
    """① 出了什么事：一句话 + 三个关键数字。"""
    parts = []
    g = (res or {}).get("gmv") or {}
    if g.get("change_pct") is not None and g["change_pct"] < 0:
        parts.append(f"近 7 天 GMV {g['change_pct']:+.1%}，少卖 {C.fmt(-g['change'], 'money')}")
    elif g.get("change_pct") is not None and c.get("kind") == "opportunity":
        parts.append(f"近 7 天 GMV {g['change_pct']:+.1%}")
    has_gmv = bool(parts)
    parts += [h["text"] for h in (c.get("latest") or []) if not (has_gmv and h["rule"] == "R01")][:2]
    top = None
    for f in (res or {}).get("contribution") or []:
        if f.get("change_pct") is not None and (top is None or abs(f["change_pct"]) > abs(top["change_pct"])):
            top = f
    kpis = [dict(label="影响金额（近 7 天）", value=C.fmt(c["gmv_impact"], "money") if c["gmv_impact"] else "—"),
            dict(label="已持续", value=f"{c['days_open']} 天（触发 {c['trigger_days']} 天）")]
    if top:
        kpis.append(dict(label="变化最大的指标", value=f"{top['name']} {top['change_pct']:+.1%}"))
    return dict(headline="；".join(parts), kpis=kpis, in_transit=c.get("in_transit"), plateau=c.get("plateau"),
                expected=c.get("expected"))


def next_card(cards: list[dict], cid: str) -> str | None:
    todo = [x for x in cards if x["group"] in ("pending", "opportunity")]
    ids = [x["id"] for x in todo]
    if cid in ids:
        i = ids.index(cid)
        rest = ids[i + 1:] + ids[:i]
    else:
        rest = ids
    return rest[0] if rest else None


def quick_prompts(cause: str | None, c: dict) -> list[str]:
    if c.get("store"):
        return ["这是正常波动", "哪些商品拖累最多"]
    return QUICK.get(cause or "", QUICK_DEFAULT)


def chat_key(name: str, cid: str) -> str:
    return f"alertchat__{name}__{cid}"


def detail(name: str, cid: str) -> dict:
    cards = data.cards(name, include_suppressed=True)
    c = next((x for x in cards if x["id"] == cid), None)
    if not c:
        raise KeyError("预警不存在")
    ds = data.ds_of(name)
    pid = c["product_id"]
    res, excluded, cause, chart = None, [], None, None
    if c["store"]:
        chart = _store_chart(ds)
    else:
        res = diagnosis_now(name, pid)
        causes = res.get("root_causes") or []
        cause = causes[0]["cause"] if causes else None
        try:
            excluded = excluded_causes(tools.check_factors(ds, pid), res.get("contribution") or [], {x["cause"] for x in causes},
                                       ds.profile.get("report", {}).get("main_factor_share", 0.30))
        except Exception:  # noqa: BLE001
            excluded = []
        chart = _chart(ds, pid, cause)
    chat = state.cache_get(chat_key(name, cid)) or {}
    plans = (res or {}).get("plans") or []
    failed = (c.get("flags") or {}).get("failed_plan")
    base = plans[0] if plans else None
    if failed and len(plans) > 1 and plans[0].get("name") == failed:
        base = plans[1]          # 上次方案无效：默认换下一个方案
    product = None if c["store"] else dict(id=pid, name=ds.product(pid)["product_name"], tier=c["tier"], lifecycle=c["lifecycle"])
    return dict(card=c, product=product, facts=facts(name, c, res),
                diagnosis=None if res is None else dict(summary=res.get("summary"), root_causes=res.get("root_causes") or [],
                                                         excluded=excluded, source=res.get("source"), cached=res.get("cached"),
                                                         limitations=res.get("limitations") or []),
                plans=plans, plan=chat.get("plan") or base, plan_is_adjusted=bool(chat.get("plan")),
                messages=chat.get("messages") or [], suggested=chat.get("decision"),
                chart=_public(chart), quick=quick_prompts(cause, c),
                ignore_reasons=alert_flow.IGNORE_REASONS, watch_days=list(alert_flow.WATCH_DAYS),
                next_id=next_card(cards, cid), can_todo=not c["store"])
