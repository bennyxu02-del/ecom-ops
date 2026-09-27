"""单品诊断报告：由诊断结果按模板渲染为 Markdown（不调用大模型，数字全部来自诊断结果）。"""


def render_product_report(d, res, card):
    def p(x):
        return "—" if x is None else f"{x:+.1%}"
    L = [f"# {d['product_name']} 诊断报告（{d['as_of']}）", "", "## 一、商品概况", "",
         f"- 分层：{d['tier']} · {d['lifecycle']}；健康度 {d['health']['score']} 分（{d['health']['level']}）",
         f"- 近 7 日 GMV {res['gmv']['cur']:,.0f} 元，前 7 日 {res['gmv']['prev']:,.0f} 元（{p(res['gmv']['change_pct'])}）", "",
         "## 二、问题描述", ""]
    if card:
        L.append(f"- 【{card['severity_name']}】{'、'.join(card['rule_names'])}，{card['first_date']} 首次触发，持续 {card['trigger_days']} 天，影响 GMV 约 {card['gmv_impact']:,.0f} 元（估算）")
        L += [f"  - {h['text']}" for h in card.get("latest", [])]
    else:
        L.append("- 当前没有问题卡")
    L += ["", "## 三、归因与证据", "", f"**结论**：{res['summary']}", "", "| 因子 | 变化 | 贡献额（元） | 占比 |", "| --- | --- | --- | --- |"]
    for f in res["contribution"]:
        L.append(f"| {f['name']} | {p(f['change_pct'])} | {f['amount']:,.0f} | {(f['share'] or 0):.0%} |")
    for rc in res["root_causes"]:
        L += ["", f"**{rc['cause_name']}**（把握：{rc['confidence']}）"] + [f"- {e['text']}" for e in rc["evidence"]]
    L += ["", "## 四、方案", ""]
    for i, pl in enumerate(res["plans"], 1):
        L += [f"### 方案 {i}：{pl['name']}（{pl['exec_type']} · {pl['owner_role']}）", "", f"- 对象：{pl['target']}"]
        L += [f"- {t}" for t in pl["params_text"]]
        est = pl.get("estimate") or {}
        if est.get("type") == "price":
            L.append(f"- 测算：单件毛利 {est['unit_margin_before']:g} → {est['unit_margin_after']:g} 元，毛利率 {est['margin_rate_after']:.0%}，保本需销量提升 {est['breakeven_lift']:.0%}")
        elif est.get("daily_gmv_recoverable"):
            L.append(f"- 测算：恢复后每天可挽回 GMV 约 {est['daily_gmv_recoverable']:,.0f} 元")
        risk = pl.get("risk_notes") or pl.get("approval_reasons")
        if risk:
            L.append(f"- 风险提示：{'；'.join(risk)}")
        L += ["- 执行步骤："] + [f"  {j}. {s}" for j, s in enumerate(pl["steps"], 1)]
        L.append(f"- 风险：{'；'.join(pl['risks'])}")
        if pl.get("rationale"):
            L.append(f"- 选择理由：{pl['rationale']}")
        L.append("")
    L += ["## 五、跟踪计划", ""]
    L += [f"- {pl['name']}：执行后 {pl['track']['days']} 天跟踪{pl['track']['metric_name']}" for pl in res["plans"]] or ["- 无"]
    L += ["", "## 六、数据局限", ""] + [f"- {x}" for x in res["limitations"]]
    return "\n".join(L)


