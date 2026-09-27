"""从方法内核（methods/）与计算层（core/）构建标准 Skill 包：dist/product-ops-analysis/

平台与 Skill 使用同一份方法与代码：改 methods/ 或 core/ 后重新运行本脚本即可。
用法：python skill/build_skill.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "product-ops-analysis"
M = ROOT / "methods"


def y(name):
    return yaml.safe_load((M / name).read_text(encoding="utf-8"))


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]
    return "\n".join(out)


def metrics_md():
    m = y("metrics.yaml")
    return "# 指标字典\n\n所有窗口类比率先对分子、分母分别求和再相除，不对日比率求平均。\n\n" + \
        table(["指标", "字段", "公式", "口径说明"], [[x["name"], x["id"], x["formula"], x.get("note", "")] for x in m["metrics"]]) + \
        "\n\n渠道：" + "、".join(f"{k}（{v}）" for k, v in m["channels"].items()) + "\n"


def tree_md():
    t = y("tree.yaml")
    return f"""# 指标拆解树

**GMV = 访客数 × 支付转化率 × 客单价**，在任何窗口内严格成立（由指标定义保证）。

- 访客数 → 按渠道相加（搜索、推荐、付费投放、活动会场、站外）
- 支付转化率 → 按规格看销量结构；影响因素：{'；'.join(t['factors'].values())}。检查顺序由品类配置 `factor_priority` 决定
- 客单价 → 件单价 × 人均件数；影响因素：促销、优惠券、规格结构

两类分支：
- **数学拆解**：可精确计算每个因子的贡献额；
- **影响因素**：不能用公式算，需要找证据判断（库存、价格、口碑、活动）。

## 贡献度算法（LMDI 对数平均法）

各因子贡献之和严格等于 GMV 变化额，且与因子顺序无关：

C_x = L(GMV₁, GMV₀) × ln(x₁ / x₀)，其中 L(a, b) = (a − b) / (ln a − ln b)，x ∈ {{访客数, 支付转化率, 客单价}}

任一值为 0 时改用连环替代法（访客 → 转化 → 客单价）。

渠道贡献 = 访客贡献 × 该渠道访客变化 ÷ 总访客变化。断货损失估算 = 该规格基准日均 GMV × 断货天数。
"""


def tiering_md():
    t = y("tiering.yaml")
    return "# 商品分层与生命周期\n\n按近 28 天数据计算，按顺序匹配。重点商品池 = 爆品 + 潜力品 + 利润品 + 人工加入。\n\n" + \
        table(["分层", "规则", "进入重点池"], [[x["name"], x["rule"], "是" if x["focus"] else "否"] for x in t["tiers"]]) + "\n\n" + \
        table(["生命周期", "规则", "预警阈值系数"], [[x["name"], x["rule"], f"×{x['coef']}"] for x in t["lifecycles"]]) + \
        "\n\n增长率比较剔除大促日、按日均计算。阈值系数作用于 GMV 下滑的百分比阈值，以及访客、转化率、增长机会的 z 值阈值：同样下降 10%，对新品是正常波动，对成熟爆品是严重问题。\n"


def alerts_md():
    a = y("alert_rules.yaml")
    return "# 预警规则\n\n近 7 日对比前 7 日（R01）；近 3 日日均对比前 28 天基线（R02 / R03 / R08，基线剔除大促日，z 值标准差下限为均值的 5%）。\n\n" + \
        table(["规则", "名称", "类型", "触发条件", "严重度"], [[x["id"], x["name"], x["type"], x["condition"], x["severity"]] for x in a["rules"]]) + \
        "\n\n## 附加规则\n\n" + "\n".join(f"- {x}" for x in a["extra"]) + "\n\n## 合并与降噪\n\n" + "\n".join(f"- {x}" for x in a["merge"] if not any(w in x for w in ("飞书", "待办", "处理状态"))) + "\n"


def actions_md():
    lib = y("action_library.yaml")
    d = y("category_profiles/default.yaml")
    rows = [[a["name"], a["id"], "、".join(lib["cause_names"].get(c, c) for c in a["causes"]), a["params"], a["constraints"],
             a["exec_type"] + ("（品类启用）" if a.get("optional") else "")] for a in lib["actions"]]
    c = d["constraints"]
    return "# 动作库\n\n电商运营可用的工具由电商平台后台决定，而不是由品类决定，所以动作库通用；品类差异只调整顺序与启用范围。**方案只能从这里选择**，参数按公式用真实数据计算（`plan_actions` 会自动完成）。所需数据缺失、参数算不出或约束不通过的动作不输出。\n\n" + \
        table(["动作", "编号", "适用根因", "参数计算", "约束检查", "执行类型"], rows) + \
        "\n\n## 根因代码\n\n" + table(["代码", "含义"], [[k, v] for k, v in lib["cause_names"].items()]) + \
        f"""

## 经营约束（默认值；用户说出自己公司的规则后，用 `settings` 命令调整，不改文件）

- 毛利底线：价格类方案执行后毛利率 ≥ {c['margin_floor']:.0%}，否则不输出
- 自主调价权限：单次到手价降幅 ≤ {c['price_authority']:.0%}，超出时在方案上提示风险
- 最低价保护：不低于近 {c['min_price_window']} 天最低成交价（含大促价），否则提示破价风险
- 安全库存：{c['safety_days']} 天；补货量 = 基准日均销量 ×（补货周期 + 安全天数）− 当前库存 − 在途量
- 活动报名需提前 {c['campaign_signup_lead']} 天

## 测算口径

- 价格类：单件毛利 = 到手价 − 成本价 − 赠品成本；保本销量增幅 = 原单件毛利 ÷ 新单件毛利 − 1。**不预测销量提升。**
- 恢复类：可挽回日均 GMV = 断货损失估算，或 付费访客缺口 × 基线转化率 × 基线客单价。
"""


def profiles_md():
    ps = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((M / "category_profiles").glob("*.yaml"))}
    keys = [("new_product_days", "新品期（天）"), ("growth_days", "成长期上限（天）"), ("margin_high", "利润品毛利率门槛"),
            ("gmv_drop_yellow", "GMV 下滑黄色阈值"), ("gmv_drop_red", "GMV 下滑红色阈值"), ("z_threshold", "异常 z 值"),
            ("replenish_lead_days", "补货周期（天）"), ("price_gap", "价差阈值"), ("factor_priority", "影响因素检查顺序"),
            ("seasonality_note", "季节性")]
    order = ["default"] + [k for k in ps if k != "default"]
    rows = []
    for k, n in keys:
        row = [n]
        for p in order:
            v = ps[p].get(k, ps["default"].get(k))
            row.append(" → ".join(v) if isinstance(v, list) else v)
        rows.append(row)
    rows.append(["价格类动作偏好"] + [" → ".join(ps[p].get("action_prefs", ps["default"]["action_prefs"])["price_actions"]) for p in order])
    rows.append(["惯用赠品"] + ["、".join(g["name"] for g in ps[p].get("action_prefs", ps["default"]["action_prefs"])["gifts"]) for p in order])
    return "# 品类配置\n\n方法主体通用，品类差异只写在配置里。数据中的品类匹配不到专属配置时使用通用默认，并在结论中注明。\n\n" + \
        table(["参数"] + [ps[p].get("name", p) for p in order], rows) + \
        "\n\n品类匹配关键词：" + "；".join(f"{ps[p]['name']}：{'、'.join(ps[p].get('match', []))}" for p in order if p != "default") + \
        "\n\n## 新增一个品类\n\n日常使用不需要改任何文件：数据里的品类匹配不到时按通用标准判断；毛利底线、调价权限、补货周期等经营参数，用户在对话里说一句，用 `settings` 命令调整即可。\n\n如果要长期沉淀一个新品类的判断标准：复制 `scripts/config/category_profiles/3c.json`，按新品类修改上表参数与 `match` 关键词，**由懂行的人审核后生效**。\n"


def chart_md():
    sys.path.insert(0, str(ROOT))
    from core import charts
    rows = [[x["name"], x["type"], x["use"], x["params"]] for x in charts.library_doc()]
    ms = [[k, v[0], "可以" if v[2] else "只能单个商品"] for k, v in charts.METRICS.items()]
    return ("# 图表库\n\n报告里的图只能用 `scripts/run.py chart` 画：只传参数，数据由脚本从数据里取，参数不合格会返回错误说明。"
            "不要自己写画图代码。\n\n" + table(["图表", "类型", "用来说明什么", "参数"], rows)
            + "\n\n## 可用指标\n\n" + table(["指标", "名称", "按全部商品（ALL）汇总"], ms)
            + "\n\n## 参数\n\n- `--products`：商品编号，逗号分隔；全部商品写 `ALL`\n- `--metrics`：指标，逗号分隔\n"
              "- `--by`：瀑布图 factor / product，分组柱 channel / variant / product，堆叠柱 tier / variant\n"
              "- `--start` `--end`：本期（默认截止日前 28 天，瀑布图 / 条形 / 分组柱 / 指标卡默认 7 天）；`--compare-start` `--compare-end`：对比期（默认本期之前等长）\n"
              "- `--mark-date` `--mark-text`：竖线标注；`--band-start` `--band-end` `--band-text`：底色区间\n"
              "- `--title`：写结论，不写主题\n")


def build():
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "references").mkdir(parents=True)
    shutil.copy(ROOT / "skill" / "SKILL.md", DIST / "SKILL.md")
    refs = {"metrics.md": metrics_md(), "decomposition_tree.md": tree_md(), "tiering.md": tiering_md(),
            "alert_rules.md": alerts_md(), "action_library.md": actions_md(), "category_profiles.md": profiles_md()}
    for k, v in refs.items():
        (DIST / "references" / k).write_text(v, encoding="utf-8")
    shutil.copy(M / "attribution_sop.md", DIST / "references" / "attribution_sop.md")
    shutil.copy(ROOT / "skill" / "data_spec.md", DIST / "references" / "data_spec.md")
    shutil.copytree(M / "playbooks", DIST / "references" / "playbooks")
    # 剧本里的「验收案例」是平台用演示数据自测的预期结论，放进通用 Skill 会误导 Agent：打包时去掉（平台上的剧本不变）
    import re
    for f in (DIST / "references" / "playbooks").glob("*.md"):
        t = f.read_text(encoding="utf-8")
        t = re.sub(r"\n## 验收案例[^\n]*\n.*?(?=\n## |\Z)", "\n", t, flags=re.S)
        f.write_text(t.rstrip() + "\n", encoding="utf-8")
    (DIST / "references" / "chart_library.md").write_text(chart_md(), encoding="utf-8")
    (DIST / "scripts").mkdir()
    shutil.copy(ROOT / "skill" / "run.py", DIST / "scripts" / "run.py")
    shutil.copytree(ROOT / "core", DIST / "scripts" / "core", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(M, DIST / "scripts" / "config")
    # 配置转成 JSON：运行时不依赖 PyYAML（通用 Agent 的环境不一定装了）
    for f in (DIST / "scripts" / "config").rglob("*.yaml"):
        f.with_suffix(".json").write_text(json.dumps(yaml.safe_load(f.read_text(encoding="utf-8")), ensure_ascii=False, indent=1, default=str),
                                          encoding="utf-8")
        f.unlink()
    if (ROOT / "skill" / "scenarios").exists():
        shutil.copytree(ROOT / "skill" / "scenarios", DIST / "references" / "scenarios")
    (DIST / "scripts" / "core" / "assets").mkdir(exist_ok=True)
    shutil.copy(ROOT / "web" / "src" / "lib" / "reportCharts.js", DIST / "scripts" / "core" / "assets" / "report_charts.js")
    # 示例数据：3C 标准格式近 60 天；零食后台导出宽表
    src = ROOT / "data" / "datasets" / "3c"
    ex = DIST / "examples" / "3c_sample"
    ex.mkdir(parents=True)
    for f in src.glob("*.csv"):
        df = pd.read_csv(f)
        if "date" in df.columns and f.name != "events.csv":
            df = df[df["date"] >= "2026-07-23"]
        df.to_csv(ex / f.name, index=False)
    shutil.copy(ROOT / "data" / "datasets" / "skill_test" / "零食店铺_商品日报_导出.csv", DIST / "examples" / "snacks_raw.csv")
    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"Skill 已构建：{DIST}（{size / 1024:.0f} KB）")


if __name__ == "__main__":
    sys.exit(build())
