"""提示词：运行时从 methods/ 读取方法内核拼装，不在代码中写死业务规则。"""
from __future__ import annotations

import json

from core import config

OUTPUT_SPEC = """最后一轮只输出一个 JSON 对象（不要 Markdown 代码块，不要其他文字），结构如下：
{
  "summary": "一句话结论，先说 GMV 变化，再说主要原因",
  "root_causes": [
    {"cause": "根因代码", "confidence": "强|中|弱", "evidence": [{"text": "一条证据，引用工具返回的数字"}]}
  ],
  "plans": [
    {"action_id": "来自 plan_actions 返回的 action_id", "cause": "对应根因代码", "rationale": "为什么选这个方案（一两句，业务语言）"}
  ],
  "limitations": ["未检查的项目或数据缺口，至少一条"],
  "notes": ["可选：例如变化属于正常波动的说明"]
}"""


def _library_brief() -> str:
    lines = []
    for a in config.action_library()["actions"]:
        lines.append(f"- {a['id']}（{a['name']}）：适用 {', '.join(a['causes'])}；执行类型 {a['exec_type']}")
    return "\n".join(lines)


def diagnose_system(ds) -> str:
    prof = ds.profile
    return f"""{config.read_text('attribution_sop.md')}

## 当前品类配置
- 品类：{prof.get('name')}（{'已匹配专属配置' if prof.get('matched') else '未匹配，使用通用默认'}）
- 影响因素检查顺序：{' → '.join(prof.get('factor_priority', []))}
- 季节性：{prof.get('seasonality_note')}
- 经营约束：{json.dumps(prof.get('constraints'), ensure_ascii=False)}

## 动作库概要（方案必须通过 plan_actions 获取）
{_library_brief()}

## 输出格式
{OUTPUT_SPEC}

## 写作要求
- 数字只能引用工具返回的结果；百分比保留一位小数，金额用「元」。
- 证据要具体：哪个规格、哪一天、多少。
- 不出现工具名、字段名等技术术语；面向商品运营，用业务语言。
- 不要预测优惠券、赠品等方案能多卖多少。"""


def diagnose_user(ds, pid, card) -> str:
    p = ds.product(pid)
    s = f"请诊断商品「{p['product_name']}」（商品编号 {pid}），对比近 7 日与前 7 日，数据截至 {ds.as_of:%Y-%m-%d}。"
    if card:
        s += f"\n当前问题卡：{card['severity_name'] if 'severity_name' in card else card['severity']}色，触发规则 {'、'.join(card['rule_names'])}，首次触发 {card['first_date']}。"
    else:
        s += "\n该商品当前没有问题卡，是运营主动发起的诊断。"
    return s


def evidence_user(ds, pid, card, steps) -> str:
    ev = [dict(tool=s["tool"], result=s["result"]) for s in steps]
    return diagnose_user(ds, pid, card) + "\n\n以下是按 SOP 调用分析工具得到的全部结果（证据包），请据此完成诊断并按格式输出：\n" + \
        json.dumps(ev, ensure_ascii=False, default=str)[:60000]


def chat_system(ds, pid, diagnosis) -> str:
    p = ds.product(pid)
    return f"""你是一名资深电商商品运营分析师，正在回答运营关于商品「{p['product_name']}」（编号 {pid}）的追问。
数据截至 {ds.as_of:%Y-%m-%d}。只讨论这个商品；需要数字时调用工具获取，不编造数字。
超出这个商品范围的问题，礼貌说明你只负责当前商品的分析。
回答简洁，用业务语言，不出现工具名和字段名。
需要用图说明变化时，可以调用 draw_chart 画图（最多 2 张，只传参数，数据由平台提供），并在回答里单独一行写 [图表:编号]。

已有诊断结论（供参考）：
{json.dumps(diagnosis, ensure_ascii=False, default=str)[:8000] if diagnosis else '暂无'}"""


MATERIAL_PRESETS = {
    "selling_points": "请基于当前方案，为这个商品起草 3 条主图或详情页首屏的卖点文案，每条不超过 20 字，突出与竞品的差异，不要只拼价格。",
    "stockout_notice": "请为断货规格起草一段商品页提示文案（不超过 40 字），说明预计到货时间，并引导顾客选择有货规格。",
    "review_reply": "请起草一份差评回复模板：先致歉，说明正在排查批次问题，给出补偿方案，语气真诚，不超过 120 字；再给出 3 条统一的客服解释话术。",
    "title_keywords": "请给出标题关键词优化建议：保留哪些核心词、补充哪些高热度词，并给出一个优化后的标题示例（不超过 30 字）。",
    "campaign_pitch": "请起草一段活动报名理由（不超过 100 字），说明商品近期表现与参加活动的价值。",
}
