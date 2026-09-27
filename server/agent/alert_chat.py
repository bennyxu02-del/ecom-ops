"""处理面板里的对话：业务用几句话把 AI 的方案调到能用（业务调教）。

- 只围绕这条预警和这个商品；可以调用诊断工具、方案测算工具（evaluate_plan）和画图工具。
- AI 在回答末尾用 <plan> 给出调整后的完整方案、用 <decision> 给出建议的决定。
- 方案里的到手价、毛利率、保本增幅由平台按 <plan> 里的参数重新测算，不采用 AI 写的数字；低于毛利底线的方案不接受。
"""
from __future__ import annotations

import copy
import json
import re

from core import actions as core_actions
from core import charts as C
from core import sop, tools
from core.weekly import METRIC_NAMES

from .. import alert_detail, alert_flow, data, llm_client, state, todos
from . import diagnose, verify

MAX_CALLS = 6
PLAN_RE = re.compile(r"<plan>\s*(\{.*?\})\s*</plan>", re.S)
DEC_RE = re.compile(r"<decision>\s*(\{.*?\})\s*</decision>", re.S)

SYSTEM = """你是电商商品运营助手，现在在「处理预警」面板里，帮运营把这条预警的处理方案调到能用。
运营比你更了解业务实际情况（补货、预算、活动安排等），你的任务是听懂他的要求，调整方案或判断要不要处理。

## 这条预警
{card}

## AI 初判
{diag}

## 当前方案卡
{plan}

## 经营约束
- 毛利底线 {floor:.0%}：执行后毛利率低于底线的方案不能采用。
- 自主调价权限：到手价单次降幅不超过 {auth:.0%}，超出可以采用，但要提醒风险。
- 可用赠品：{gifts}
{failed}
## 规则
1. 回答简短，2–5 句，只讨论这条预警和这个商品；需要数据时调用工具，不要编造数字。
2. 运营要改价格类参数（券面额、到手价、赠品）时，必须先调用 evaluate_plan 测算；到手价、价差、毛利率、保本销量增幅只能用工具结果。
   ok=false 表示低于毛利底线，不能采用：说明原因，并测算一个能接受的替代给运营。
3. 方案有变化时，在回答最后另起一行附上调整后的完整方案（整段放在 <plan> 标签内），格式：
<plan>{{"name": "方案名称，15 字以内", "type": "price|other", "coupon": 15, "new_price": null, "gift_cost": 6, "gift_name": "数据线",
"steps": [{{"text": "具体步骤", "by": "我|供应链|投放运营"}}], "track_metric": "gmv|cvr|uv|aov|units|rating", "track_days": 5, "due_days": 1,
"params_text": ["非价格类方案的关键参数"], "changes": ["相比 AI 初版改了什么，一句一条"]}}</plan>
   - type=price 时填 coupon / new_price / gift_cost（与 evaluate_plan 的参数一致），平台会按这些参数重新测算；type=other 时不填。
   - 分给同事的步骤会原样发给对方，写成直接对对方说的话：做什么、针对哪个商品 / 规格、要反馈什么。
4. 运营补充了实际情况、结论是不需要处理或暂时观察时，在回答最后附上建议的决定：
<decision>{{"type": "known|ignore|watch", "reason": "一句话原因（引用运营说的情况和核对过的数字）", "days": 3}}</decision>
   known = 已知原因（例如补货已安排、主动减的预算）；ignore = 正常波动 / 阈值太严；watch = 先观察 days 天（1、3 或 7）。
5. 标签里的内容不要在正文里重复；正文不要写「好的，这就为你更新」这类话。"""


def _card_text(c: dict) -> str:
    lines = [f"商品：{c['product_name']}（{c['tier']}）；{c['severity_name']}色；规则：{'、'.join(c['rule_names'])}",
             f"{c['first_date']} 起，已持续 {c['days_open']} 天；近 7 天影响 GMV 约 {c['gmv_impact']:,.0f} 元"]
    lines += [f"- {h['text']}" for h in c.get("latest") or []]
    if c.get("plateau"):
        lines.append(f"- {c['plateau']}")
    return "\n".join(lines)


def _plan_text(p: dict | None) -> str:
    if not p:
        return "（还没有方案）"
    keep = {k: p.get(k) for k in ("name", "action_id", "target", "params", "params_text", "track", "changes") if p.get(k)}
    keep["steps"] = [dict(text=t, by=(p.get("step_owners") or [])[i] if i < len(p.get("step_owners") or []) else "我")
                     for i, t in enumerate(p.get("steps") or [])]
    if p.get("estimate"):
        keep["estimate"] = p["estimate"]
    return json.dumps(keep, ensure_ascii=False)


def system_prompt(name: str, d: dict, plan: dict | None) -> str:
    ds = data.ds_of(name)
    c = d["card"]
    diag = d.get("diagnosis") or {}
    dtext = diag.get("summary") or "（全店预警，没有单品诊断）"
    for rc in diag.get("root_causes") or []:
        dtext += f"\n- 根因：{rc['cause_name']}（把握：{rc.get('confidence')}）：" + "；".join(e.get("text", "") for e in rc.get("evidence") or [])
    if diag.get("excluded"):
        dtext += "\n- 已排除：" + "；".join(diag["excluded"])
    others = [p["name"] for p in d.get("plans") or []]
    if others:
        dtext += "\n- 动作库给出的方案：" + "、".join(others)
    cons = ds.profile.get("constraints", {})
    gifts = "、".join(f"{g['name']}（成本 {g['cost']} 元）" for g in (ds.profile.get("action_prefs") or {}).get("gifts") or []) or "无"
    failed = (c.get("flags") or {}).get("failed_plan")
    return SYSTEM.format(card=_card_text(c), diag=dtext, plan=_plan_text(plan), floor=cons.get("margin_floor", 0.25),
                         auth=cons.get("price_authority", 0.1), gifts=gifts,
                         failed=f"- 上次的方案「{failed}」复盘无效，这次要换思路。\n" if failed else "")


# ---------------------------------------------------------------------------
# 方案合并：平台重新测算价格类参数
# ---------------------------------------------------------------------------
def _num(v):
    try:
        return float(v) if v not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


def price_params_text(ev: dict, coupon, gift_cost, gift_name) -> list[str]:
    out = [f"当前到手价 {ev['price_before']:g} 元" + (f"，竞品 {ev['comp_price']:g} 元" if ev.get("comp_price") else "")]
    if coupon:
        out.append(f"券面额 {coupon:g} 元，券后 {ev['price_after']:g} 元")
    elif ev["price_after"] != ev["price_before"]:
        out.append(f"到手价调整为 {ev['price_after']:g} 元")
    if gift_cost:
        out.append(f"加赠{gift_name or '赠品'}，成本 {gift_cost:g} 元/件")
    return out


def merge_plan(name: str, pid: str, base: dict | None, raw: dict) -> tuple[dict | None, str | None, dict | None]:
    """返回（新方案，不接受的原因，测算结果）。"""
    ds = data.ds_of(name)
    p = copy.deepcopy(base) if base else dict(action_id="custom", cause="custom", cause_name="业务调整",
                                              target=ds.product(pid)["product_name"], exec_type="自己执行",
                                              owner_role="商品运营", params_text=[], risks=[])
    if raw.get("name"):
        p["name"] = str(raw["name"]).strip()[:30]
    steps = todos._clean_steps(raw.get("steps") or [])
    if steps:
        p["steps"] = [s["text"] for s in steps]
        p["step_owners"] = [s["by"] for s in steps]
    track = dict(p.get("track") or {})
    if raw.get("track_metric") in METRIC_NAMES:
        track.update(metric=raw["track_metric"], metric_name=METRIC_NAMES[raw["track_metric"]])
    if raw.get("track_days"):
        try:
            track["days"] = max(3, min(30, int(raw["track_days"])))
        except (TypeError, ValueError):
            pass
    track.setdefault("metric", "gmv")
    track.setdefault("metric_name", METRIC_NAMES["gmv"])
    track.setdefault("days", 7)
    p["track"] = track
    if raw.get("due_days") is not None:
        try:
            p["due_days"] = max(0, min(30, int(raw["due_days"])))
        except (TypeError, ValueError):
            pass
    ev = None
    coupon, new_price, gift = _num(raw.get("coupon")), _num(raw.get("new_price")), _num(raw.get("gift_cost"))
    if raw.get("type") == "price" or coupon or new_price or gift:
        ev = core_actions.evaluate_plan(ds, pid, coupon=coupon, new_price=new_price, gift_cost=gift)
        if not ev.get("ok"):
            return None, ev.get("reason") or "测算没有通过", ev
        p["estimate"] = ev["estimate"]
        p["checks"] = ev.get("checks") or []
        p["risk_notes"] = ev.get("risk_notes") or []
        p["params"] = dict(p.get("params") or {}, coupon=coupon, new_price=ev["price_after"], gift_cost=gift,
                           gift_name=raw.get("gift_name"))
        p["params_text"] = price_params_text(ev, coupon, gift, raw.get("gift_name"))
        p["category"] = "价格"
    else:
        for k in ("estimate", "checks"):
            p.pop(k, None)
        p["risk_notes"] = []
        if raw.get("params_text"):
            p["params_text"] = [str(x)[:80] for x in raw["params_text"]][:4]
        elif base and base.get("category") == "价格":
            p["params_text"] = []
    changes = [str(x).strip()[:60] for x in raw.get("changes") or [] if str(x).strip()]
    p["changes"] = list(dict.fromkeys(list((base or {}).get("changes") or []) + changes))
    p["base_name"] = (base or {}).get("base_name") or (base or {}).get("name")
    p["adjusted"] = True
    p.pop("rationale", None)
    p.pop("materials", None)
    p["step_owners"] = p.get("step_owners") or ["我"] * len(p.get("steps") or [])
    return core_actions.annotate(p), None, ev


def normalize_decision(raw: dict | None) -> dict | None:
    if not raw or raw.get("type") not in ("known", "ignore", "watch"):
        return None
    d = dict(type=raw["type"], reason=str(raw.get("reason") or "").strip()[:120])
    if d["type"] == "watch":
        try:
            days = int(raw.get("days") or 3)
        except (TypeError, ValueError):
            days = 3
        d["days"] = days if days in alert_flow.WATCH_DAYS else 3
    d["type_name"] = alert_flow.DECISIONS[d["type"]]
    return d


def split_tags(text: str) -> tuple[str, dict | None, dict | None]:
    raw_plan = raw_dec = None
    m = PLAN_RE.search(text or "")
    if m:
        try:
            raw_plan = json.loads(m.group(1))
        except json.JSONDecodeError:
            raw_plan = None
    m2 = DEC_RE.search(text or "")
    if m2:
        try:
            raw_dec = json.loads(m2.group(1))
        except json.JSONDecodeError:
            raw_dec = None
    body = DEC_RE.sub("", PLAN_RE.sub("", text or "")).strip()
    return body, raw_plan, raw_dec


# ---------------------------------------------------------------------------
# 模拟模型（LLM_MODE=mock）：按关键词给出与真实模型同格式的输出，用于没有模型时走通整条链路
# ---------------------------------------------------------------------------
def _gift(ds, text):
    gifts = (ds.profile.get("action_prefs") or {}).get("gifts") or []
    return next((g for g in gifts if g["name"] in text), gifts[0] if gifts else None)


def _md(date: str) -> str:
    return f"{int(date[5:7])}/{int(date[8:10])}"


def _mock(name, d, plan, history):
    """返回（正文含标签，工具步骤列表）。"""
    ds = data.ds_of(name)
    c = d["card"]
    pid = c["product_id"]
    q = (history[-1]["content"] if history else "").strip()
    last_ai = next((m["content"] for m in reversed(history[:-1]) if m["role"] == "assistant"), "")
    steps = []
    it = (c.get("in_transit") or [None])[0]
    pname = c["product_name"]

    if re.match(r"^(要|好|可以|行|换成|那就)", q) and "到货提示" in last_ai and it:
        raw = dict(name="详情页加到货提示", type="other", steps=[
            dict(text=f"详情页首屏加{it['variant']}到货时间提示（{_md(it['date'])} 到货），引导先买其他规格", by="我"),
            dict(text=f"确认{pname} {it['variant']} 在途 {it['qty']:,.0f} 件能否在 {_md(it['date'])} 前到仓，反馈最终到仓日期和数量", by="供应链")],
            track_metric="cvr", track_days=5, due_days=1, params_text=[f"{it['variant']}在途 {it['qty']:,.0f} 件，{_md(it['date'])} 到货"],
            changes=["改为到货前的过渡动作：详情页加到货提示"])
        return ("好，方案卡已换成到货前的过渡动作：详情页首屏加到货提示，同时请供应链确认到仓时间。"
                + "\n<plan>" + json.dumps(raw, ensure_ascii=False) + "</plan>"), steps

    if re.search(r"已经安排|已安排|主动|知道原因|已知|不用处理|先不处理|计划内|预期内", q):
        if it:
            reason = f"{it['variant']}补货 {it['qty']:,.0f} 件在途，{_md(it['date'])} 到货"
        elif "预算" in q:
            reason = "主动下调了投放预算，访客下降在预期内"
        else:
            reason = re.sub(r"[，。,.!！]*(这个)?(先)?不用处理.*$", "", q)[:60] or "业务已知原因"
        text = f"明白。建议标为「已知原因：{reason}」。"
        if it and it["days"] > 0:
            text += (f"另外，到货前还有 {it['days']} 天，要不要顺手做一个小动作：详情页首屏加到货提示，引导先买其他规格？"
                     "要的话我把方案卡换成这个，你可以直接转待办。")
        return text + "\n<decision>" + json.dumps(dict(type="known", reason=reason), ensure_ascii=False) + "</decision>", steps

    if re.search(r"观察|再看看|等几天", q):
        n = next((int(x) for x in re.findall(r"(\d+)\s*天", q) if int(x) in alert_flow.WATCH_DAYS), 3)
        return (f"可以，先观察 {n} 天。到期如果还在触发，会重新进入飞书推送；指标回来了就自动关闭。"
                + "\n<decision>" + json.dumps(dict(type="watch", reason="业务判断先观察", days=n), ensure_ascii=False) + "</decision>"), steps

    m_coupon = None
    if "券" in q:
        m_coupon = (list(re.finditer(r"(?:改成|改为|换成|给|用|只给)\s*(\d+(?:\.\d+)?)\s*元?", q)) or [None])[-1] \
            or re.search(r"(\d+(?:\.\d+)?)\s*元?\s*的?\s*(?:优惠)?券", q) or re.search(r"券[^\d]{0,8}(\d+(?:\.\d+)?)", q)
    m_price = re.search(r"(?:到手价|降到|价格改成|改成卖)\s*(\d+(?:\.\d+)?)", q)
    want_gift = bool(re.search(r"赠|送", q))
    no_cut = bool(re.search(r"不降价|不想降价|别的办法|不打折", q))
    if m_coupon or m_price or want_gift or no_cut:
        coupon = None if no_cut or not m_coupon else float(m_coupon.group(1))
        new_price = None if no_cut or not m_price else float(m_price.group(1))
        g = _gift(ds, q) if (want_gift or no_cut) else None
        base_coupon = ((plan or {}).get("params") or {}).get("coupon")
        if no_cut:
            coupon = None
        elif coupon is None and new_price is None and base_coupon:
            coupon = float(base_coupon)
        args = dict(product_id=pid, coupon=coupon, new_price=new_price, gift_cost=g["cost"] if g else None)
        ev = tools.call(ds, "evaluate_plan", args)
        steps.append(dict(tool="evaluate_plan", args=args, result=ev))
        alt = None
        if not ev.get("ok") and coupon:
            for x in range(int(coupon) - 1, 0, -1):
                a2 = dict(args, coupon=float(x))
                e2 = tools.call(ds, "evaluate_plan", a2)
                if e2.get("ok"):
                    alt, ev, coupon = x, e2, float(x)
                    steps.append(dict(tool="evaluate_plan", args=a2, result=e2))
                    break
        if not ev.get("ok"):
            return f"按这个调整测算没有通过：{ev.get('reason')}。这个方案不能采用，可以换一个不降价的办法，比如加赠品。", steps
        est = ev["estimate"]
        nm = (f"{coupon:g} 元券" if coupon else f"到手价 {ev['price_after']:g} 元" if new_price else "") + \
             ((" + " if (coupon or new_price) else "") + f"加赠{g['name']}" if g else "")
        if no_cut:
            nm += "（不降价）"
        st = []
        if coupon:
            st += [dict(text=f"后台「营销工具 → 优惠券」新建 {coupon:g} 元商品券，适用 {pname}", by="我"),
                   dict(text=f"券后到手价 {ev['price_after']:g} 元，在主图角标或详情页首屏标注「领券立减 {coupon:g} 元」", by="我")]
        elif new_price:
            st.append(dict(text=f"到手价调整为 {ev['price_after']:g} 元，同步修改主图价格角标", by="我"))
        if g:
            st += [dict(text=f"设置下单加赠{g['name']}，详情页首屏写明赠品", by="我"),
                   dict(text=f"确认{g['name']}库存能支撑两周的赠送量（按{pname}近期日销估算），反馈可用数量", by="供应链")]
        changes = []
        if coupon and base_coupon and float(base_coupon) != coupon:
            changes.append(f"券面额 {float(base_coupon):g} 元 → {coupon:g} 元")
        if no_cut:
            changes.append("不降价，改为加赠品")
        elif g:
            changes.append(f"加赠{g['name']}")
        raw = dict(name=nm, type="price", coupon=coupon, new_price=new_price, gift_cost=g["cost"] if g else None,
                   gift_name=g["name"] if g else None, steps=st, track_metric="cvr", track_days=5, due_days=1, changes=changes)
        gap = ev.get("gap_after")
        thr = ev.get("price_gap_threshold")
        text = ("按你的要求测算没有通过毛利底线，已换成能接受的 " + f"{alt} 元券。" if alt else "已按你的要求测算：")
        text += f"{'券后' if coupon else ''}到手价 {ev['price_after']:g} 元"
        if gap is not None:
            text += "，和竞品持平" if abs(gap) < 0.0005 else f"，比竞品{'贵' if gap > 0 else '便宜'} {abs(gap):.1%}"
            if thr is not None and gap <= thr:
                text += f"，已经低于价格劣势门槛（{thr:.0%}）"
        text += f"；{'加上赠品后' if g else '执行后'}毛利率 {est['margin_rate_after']:.2%}"
        if est.get("breakeven_lift") is not None and est["breakeven_lift"] > 0:
            text += f"，销量至少要涨 {est['breakeven_lift']:.1%} 才能保住总毛利"
        auth = next((x for x in ev.get("checks") or [] if x["name"] == "调价权限"), None)
        if est.get("price_drop"):
            text += f"；降幅 {est['price_drop']:.1%}，" + ("在调价权限内" if not auth or auth["passed"] else "超出自主调价权限，方案卡上已标出风险")
        text += f"。方案卡已更新为「{nm}」。"
        return text + "\n<plan>" + json.dumps(raw, ensure_ascii=False) + "</plan>", steps

    if re.search(r"写细|细一点|具体一点", q) and plan:
        raw = dict(name=plan.get("name"), type="price" if (plan.get("estimate") or {}).get("type") == "price" else "other",
                   coupon=(plan.get("params") or {}).get("coupon"), gift_cost=(plan.get("params") or {}).get("gift_cost"),
                   gift_name=(plan.get("params") or {}).get("gift_name"),
                   steps=[dict(text=t + ("" if (plan.get("step_owners") or ["我"] * 9)[i] == "我" or "反馈" in t else "，完成后反馈到货数量和到仓日期"),
                               by=(plan.get("step_owners") or ["我"] * 9)[i]) for i, t in enumerate(plan.get("steps") or [])],
                   params_text=plan.get("params_text"), changes=["同事的步骤写明要反馈的结果"])
        return "已把分给同事的步骤写细，写明要反馈的结果。\n<plan>" + json.dumps(raw, ensure_ascii=False) + "</plan>", steps

    diag = d.get("diagnosis") or {}
    if re.search(r"为什么|依据|凭什么|怎么判断", q):
        rc = (diag.get("root_causes") or [{}])[0]
        ev_t = "；".join(e.get("text", "") for e in rc.get("evidence") or [])
        ex = "；".join(diag.get("excluded") or [])
        return f"主要原因是{rc.get('cause_name', '—')}：{ev_t}。" + (f"已排除：{ex}。" if ex else ""), steps

    return (f"这条预警的初判是：{diag.get('summary') or c['rule_names'][0]}。你可以告诉我实际情况（比如补货、预算安排），"
            "或者直接说要怎么改方案，我来重新测算。"), steps


# ---------------------------------------------------------------------------
def run(name: str, cid: str, messages: list[dict], plan: dict | None = None):
    d = alert_detail.detail(name, cid)
    c = d["card"]
    pid = c["product_id"]
    ds = data.ds_of(name)
    cur = plan or d["plan"]
    history = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")][-10:]
    mode = llm_client.mode()
    yield dict(type="meta", mode=mode)
    if not history or history[-1]["role"] != "user":
        raise ValueError("请输入要调整的内容")
    if mode not in ("live", "mock"):
        text = "当前没有连接大模型，对话调方案需要在线模型。可以直接用底部的按钮做决定。"
        yield dict(type="delta", text=text)
        yield dict(type="result", text=text, plan=None, decision=None, charts={}, unmatched_numbers=[])
        return
    steps, text = [], ""
    book = C.ChartBook(1)
    try:
        if mode == "mock":
            text, steps = _mock(name, d, cur, history)
            for s in steps:
                yield dict(type="step", tool=s["tool"], summary=_summ(s["tool"], s["result"]))
        else:
            chat, _ = diagnose.backend(name)
            msgs = [{"role": "system", "content": system_prompt(name, d, cur)}] + history
            specs = tools.TOOL_SPECS + [tools.EVAL_PLAN_SPEC, C.TOOL_SPEC] if not c["store"] else [C.TOOL_SPEC]
            for i in range(MAX_CALLS + 1):
                msg = chat(msgs, tools=specs) if i < MAX_CALLS else chat(msgs)
                tcs = msg.get("tool_calls") or []
                if not tcs:
                    text = msg.get("content") or ""
                    break
                msgs.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tcs})
                for tc in tcs:
                    fn = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if fn == "draw_chart":
                        if not c["store"]:
                            args.setdefault("products", [pid])
                        result = book.draw_extra(ds, args)
                        yield dict(type="step", tool=fn, summary=f"画图：{result['title']}" if "chart_id" in result else "图表参数有误，已退回")
                    else:
                        args["product_id"] = pid
                        try:
                            result = tools.call(ds, fn, args, tiers=data.tiers(name))
                        except Exception as e:  # noqa: BLE001
                            result = {"error": str(e)}
                        steps.append(dict(tool=fn, args=args, result=result))
                        yield dict(type="step", tool=fn, summary=_summ(fn, result))
                    msgs.append({"role": "tool", "tool_call_id": tc.get("id", fn),
                                 "content": json.dumps(result, ensure_ascii=False, default=str)[:6000]})
    except llm_client.LLMError as e:
        text = f"模型暂时不可用：{e}。可以直接用底部的按钮做决定。"

    body, raw_plan, raw_dec = split_tags(text)
    new_plan, rejected, ev = (None, None, None)
    if raw_plan and not c["store"]:
        new_plan, rejected, ev = merge_plan(name, pid, cur, raw_plan)
        if ev:
            steps.append(dict(tool="evaluate_plan", result=ev))
        if rejected:
            body += f"\n\n（平台测算：{rejected}，方案卡没有更新。）"
    decision = normalize_decision(raw_dec)

    for i in range(0, len(body), 30):
        yield dict(type="delta", text=body[i:i + 30])
    allowed = set()
    for s in steps:
        verify.collect(s["result"], allowed)
    for v in book.values():
        verify.collect(v, allowed)
    for x in (d.get("diagnosis"), cur, new_plan, c.get("latest"), c.get("in_transit"), d.get("facts")):
        if x:
            verify.collect(x, allowed)
    verify.collect(history, allowed)
    bad = sorted(set(verify.check_text(body, allowed)))
    used = set(C.PLACEHOLDER.findall(body))
    charts = {k: v for k, v in book.public().items() if k in used}

    saved = state.cache_get(alert_detail.chat_key(name, cid)) or {}
    state.cache_put(alert_detail.chat_key(name, cid), dict(
        messages=history + [dict(role="assistant", content=body, charts=charts, plan_updated=bool(new_plan),
                                 decision=decision)],
        plan=new_plan or saved.get("plan"), decision=decision or saved.get("decision")))
    yield dict(type="result", text=body, plan=new_plan, rejected=rejected, decision=decision, charts=charts,
               unmatched_numbers=bad)


def _summ(fn: str, res: dict) -> str:
    if fn == "evaluate_plan":
        if not res.get("ok"):
            return "方案测算：" + (res.get("reason") or "没有通过")
        e = res["estimate"]
        return f"方案测算：到手价 {e['price_before']:g} → {e['price_after']:g} 元，执行后毛利率 {e['margin_rate_after']:.2%}"
    return sop.summarize(fn, res)


def reset(name: str, cid: str):
    state.cache_del(alert_detail.chat_key(name, cid))
