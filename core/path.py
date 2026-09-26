"""分析路径：按指标拆解树一层层往下找原因，把 AI 诊断走过的路整理成可读的层级。

GMV → 拆成 访客数 × 支付转化率 × 客单价 → 对主因下钻（渠道 / 规格 / 件单价）
→ 按品类顺序检查影响因素 → 对齐同期事件 → 定位根因。
数字全部来自分析工具的计算结果，与诊断结论同源。
"""
from __future__ import annotations

from . import tools

FACTOR_TXT = {"uv": "访客数", "cvr": "支付转化率", "aov": "客单价"}


def _pct(x, digits=1):
    return "—" if x is None else f"{x * 100:+.{digits}f}%"


def _money(x):
    return f"{x:,.0f} 元"


def _fv(f, v):
    if f == "cvr":
        return f"{v * 100:.2f}%"
    if f == "aov":
        return f"{v:.2f} 元"
    return f"{v:,.0f}"


def _row(name, value="", delta=None, amount=None, tag=None, tone=None):
    return dict(name=name, value=value, delta=delta, amount=amount, tag=tag, tone=tone)


def build(ds, pid: str, result: dict, card: dict | None = None, tiers: dict | None = None) -> list:
    call = lambda n, **a: tools.call(ds, n, dict(product_id=pid, **a), card=card, tiers=tiers)  # noqa: E731
    ctx = call("get_context")
    dec = call("decompose_gmv")
    rules = set((card or {}).get("rules", []))
    chg, delta = dec["gmv_change_pct"], dec["gmv_change"]
    th = -(ctx.get("decline_alert_threshold") or 0)
    layers = []

    # 0 现象
    word = "少卖" if delta < 0 else "多卖"
    layers.append(dict(key="gmv", label="发现变化",
                       title=f"GMV {_pct(chg)}，{word} {_money(abs(delta))}",
                       note=f"近 7 日（{dec['cur_window']}）{_money(dec['gmv_cur'])}，前 7 日（{dec['prev_window']}）{_money(dec['gmv_prev'])}"))

    # 1 一级拆解
    growth = delta > 0 and "R08" in rules
    decline = delta < 0 and not ctx["within_normal_range"]
    sign = 1 if growth else -1
    drivers = []
    if growth or decline:
        drivers = [f for f in dec["factors"] if f["amount"] * sign > 0 and (f["share"] or 0) >= 0.3]
        drivers.sort(key=lambda f: -abs(f["amount"]))
    rows = []
    for f in dec["factors"]:
        main = f in drivers
        rows.append(_row(f["name"], f"{_fv(f['factor'], f['prev'])} → {_fv(f['factor'], f['cur'])}", f["change_pct"], f["amount"],
                         tag="主因" if main else None, tone=("bad" if sign < 0 else "good") if main else None))
    if drivers:
        d0 = drivers[0]
        only = (d0["share"] or 0) > 1
        verdict = (f"{'增长' if growth else '下降'}{'全部' if only else '主要'}来自{('、'.join(d['name'] for d in drivers))}"
                   + (f"（贡献 {min(d0['share'], 1):.0%}）" if not only else "，其他因子为正向") + "，继续往下找")
    elif delta < 0:
        verdict = f"跌幅未超过预警阈值 {th:.0%}" + (f"（{ctx['lifecycle']}阈值已放宽）" if (ctx.get("alert_threshold_coef") or 1) > 1 else "") + "，属于正常波动，不再往下拆"
    else:
        verdict = "没有明显下滑，不再往下拆"
    layers.append(dict(key="l1", label="第一层 · 拆成三个因子", title="GMV = 访客数 × 支付转化率 × 客单价", rows=rows, verdict=verdict))

    # 2 下钻
    events_needed = False
    for f in drivers:
        if f["factor"] == "uv":
            events_needed = True
            bc = call("breakdown_channels")
            if not bc.get("available"):
                layers.append(dict(key="uv", label="第二层 · 访客按渠道看", title="没有渠道数据", verdict="访客变化无法定位到渠道"))
                continue
            chs = [c for c in bc["channels"] if c["uv_prev"] or c["uv_cur"]]
            pick = (max if growth else min)(chs, key=lambda c: c["change"]) if chs else None
            rows = [_row(c["name"], f"日均 {c['daily_prev']:,.0f} → {c['daily_cur']:,.0f}", c["change_pct"], None,
                         tag=("增长最多" if growth else "下滑最多") if c is pick else None,
                         tone=("good" if growth else "bad") if c is pick else None)
                    for c in sorted(chs, key=lambda c: c["change"] * (-1 if growth else 1))]
            v = f"{pick['name']}渠道变化最大（{_pct(pick['change_pct'])}）" if pick else "各渠道变化不大"
            layers.append(dict(key="uv", label="第二层 · 访客按渠道看", title=v, rows=rows))
        elif f["factor"] == "cvr":
            events_needed = True
            if "variant" in ds.available:
                bv = call("breakdown_variants")
                rows, oos = [], []
                for v in bv["variants"]:
                    bad = v.get("stockout_days", 0) > 0
                    if bad:
                        oos.append(v)
                    rows.append(_row(v["name"], f"销量占比 {v['share_prev']:.0%} → {v['share_cur']:.0%}", None, None,
                                     tag=f"断货 {v['stockout_days']} 天" if bad else None, tone="bad" if bad else None))
                if oos:
                    v = max(oos, key=lambda x: x["baseline_share"])
                    title = f"{v['name']}断货 {v['stockout_days']} 天（{v['stockout_dates'][0]}~{v['stockout_dates'][-1]}），平时占销量 {v['baseline_share']:.0%}"
                else:
                    title = "各规格都有货，占比稳定"
                layers.append(dict(key="variant", label="第二层 · 转化率按规格看", title=title, rows=rows))
            cf = call("check_factors")
            rows, bad_names = [], []
            for k in cf["order"]:
                x = cf["factors"].get(k)
                if not x:
                    continue
                if not x.get("checked"):
                    rows.append(_row(x["name"], x.get("note", ""), tag="未检查", tone="muted"))
                    continue
                if k == "stock":
                    so = x.get("stockout_variants") or []
                    lo = x.get("low_stock_variants") or []
                    val = "；".join([f"{s['name']}断货 {s['stockout_days']} 天" for s in so if s.get("stockout_days")] +
                                   [f"{s['name']}只够卖 {s['days_of_supply']} 天" for s in lo]) or "各规格有货"
                elif k == "price":
                    val = f"到手价 {x['price']:g} 元，竞品 {x['comp_price']:g} 元"
                    if x.get("competitor_events"):
                        val += f"；{x['competitor_events'][0]['date'][5:]} {x['competitor_events'][0]['description']}"
                elif k == "rating":
                    val = f"评分 {x['rating_base_28d']:.2f} → {x['rating_now']:.2f}"
                    if x.get("refund_rate_base"):
                        val += f"，退款率 {x['refund_rate_7d']:.1%}（平时 {x['refund_rate_base']:.1%}）"
                else:
                    cc = x.get("campaign_channel") or {}
                    val = (f"活动会场访客 {_pct(cc.get('change_pct'))}" if cc.get("uv_prev") else "近期没有参加活动")
                if x.get("abnormal"):
                    bad_names.append(x["name"])
                rows.append(_row(x["name"], val, tag="异常" if x.get("abnormal") else "正常", tone="bad" if x.get("abnormal") else "ok"))
            title = ("、".join(bad_names) + "异常，其余正常") if bad_names else "未发现异常因素"
            layers.append(dict(key="factors", label="第三层 · 逐项检查影响因素", title=title, rows=rows,
                               note="检查顺序按品类配置：" + " → ".join(r["name"] for r in rows)))
        elif f["factor"] == "aov":
            ad = dec["aov_detail"]
            layers.append(dict(key="aov", label="第二层 · 客单价拆开看", title="件单价 × 人均件数", rows=[
                _row("件单价", f"{ad['unit_price_prev']} → {ad['unit_price_cur']} 元"),
                _row("人均件数", f"{ad['units_per_buyer_prev']} → {ad['units_per_buyer_cur']}")]))

    # 预警直接提示的问题（不依赖 GMV 变化）
    if "R05" in rules and not any(x["key"] == "factors" for x in layers) and "variant" in ds.available:
        bv = call("breakdown_variants")
        lead = ds.profile["replenish_lead_days"]
        rows = []
        for v in bv["variants"]:
            low = v.get("days_of_supply") is not None and v["stock_now"] > 0 and v["days_of_supply"] < lead
            rows.append(_row(v["name"], f"库存 {v['stock_now']:,} 件，够卖 {v['days_of_supply'] if v['days_of_supply'] is not None else '—'} 天",
                             tag="低于补货周期" if low else None, tone="bad" if low else None))
        layers.append(dict(key="stock", label="预警提示 · 检查库存", title=f"补货周期 {lead} 天，按近 7 日销量测算可售天数", rows=rows))
        events_needed = True

    # 3 事件
    if events_needed or growth:
        ev = call("get_events")
        rows = [_row(e["date"][5:], e["description"]) for e in ev["events"]]
        rows += [_row(e["date"][5:], e["description"], tag="在途", tone="muted") for e in ev.get("in_transit", [])]
        if rows:
            n = len(ev["events"])
            title = f"{ev['start'][5:]} 以来记录到 {n} 个事件，与指标拐点对照" if n else f"{ev['start'][5:]} 以来没有其他事件记录"
            layers.append(dict(key="events", label="对齐同期事件", title=title, rows=rows))

    # 4 找到的原因在「根因与证据」中展开；找不到时在路径末尾说明
    if drivers and not result.get("root_causes"):
        layers.append(dict(key="cause", label="定位原因", title="未找到明确原因", note="建议补充搜索排名、竞品动态等数据后再判断"))
    return layers
