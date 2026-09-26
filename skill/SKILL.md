---
name: product-ops-analysis
description: 消费品电商的商品经营分析。当用户提供电商商品经营数据（商品日报、访客、成交、库存、价格等表格或导出文件），并希望找出异常与预警、分析某个商品为什么卖得不好（或突然变好）、得到可落地的运营方案、生成经营周报，或划分重点商品时使用。适用于服装、零食、3C、家居、个护等消费品；不适用于 B2B、本地生活服务、虚拟商品。
---

# 商品经营分析

这个 Skill 把资深商品运营的分析方法交给你：先定义「重点商品」，再按指标拆解树逐层找原因，用证据说话，最后从动作库给出能直接执行的方案。

## 核心原则（必须遵守）

1. **计算与表达分离**：所有数字由 `scripts/run.py` 计算。你只负责判断、组织和表达，不自己算新数字，不改工具给出的数值。
2. **方法以 references 为准**：指标口径看 `references/metrics.md`，归因步骤看 `references/attribution_sop.md`，方案只能来自动作库 `references/action_library.md`。
3. **方案必须可落地**：每个方案都要有具体对象、具体数值、执行人及权限、执行步骤、跟踪指标、风险。凑不齐就不给，并说明「需要人工判断」。
4. **不预测刺激类动作的效果**：优惠券、赠品、跟价只给毛利测算和保本线，不说「能多卖多少」。
5. **如实说明缺口**：数据缺什么、哪些没检查，写进「数据局限」。

## 环境

需要 Python 3.9+，以及 `pandas`、`pyyaml`（缺少时先 `pip install pandas pyyaml`）。以下命令都在本 Skill 目录下执行；所有命令输出 JSON。

## 第 0 步：数据适配（每次都先做）

```bash
python scripts/run.py adapt <用户的文件或目录> --out workdata
```

- 支持标准多表目录（见 `references/data_spec.md`），也支持电商后台导出的单张宽表（CSV / Excel，中文字段名）。
- 看输出里的 `ok`：
  - `false`：有缺失的必填字段或有歧义的字段。**把 `missing_required` 与 `ambiguous` 告诉用户，请用户确认对应列**，再用 `--map 标准字段=原列名` 重跑。不要自己猜。
  - `true`：向用户简要说明识别结果——数据截至哪天、多少个商品、匹配到的品类配置、`missing_optional` 中哪些分析会跳过。
- 品类没匹配到专属配置时，按通用默认配置分析，并在结论中注明「按通用标准判断」。可以用 `--category` 指定品类。

## 任务 A：扫描预警（「看看有没有问题」「今天要处理什么」）

```bash
python scripts/run.py scan --data workdata
```

- 只汇报 `is_today` 为 true 的问题卡，按严重度（红 > 黄 > 蓝）和影响金额排序。
- 每张卡写一行：商品、严重度、触发了什么、持续几天、影响 GMV 约多少（估算）。
- `auto_status` 为 recovered 的是已恢复的历史问题，可以一句带过。
- 规则说明见 `references/alert_rules.md`。注意：新品期等生命周期会放宽阈值，没报警不代表没波动，而是在正常范围内。

## 任务 B：单品诊断（「这个商品为什么卖得不好」）

两种方式任选，结论标准相同：

**方式 1：逐步调用工具（推荐，能体现分析过程）**，严格按 `references/attribution_sop.md` 的 7 个步骤：

```bash
python scripts/run.py tool get_context       --product <编号> --data workdata
python scripts/run.py tool decompose_gmv     --product <编号> --data workdata
python scripts/run.py tool breakdown_channels --product <编号> --data workdata
python scripts/run.py tool breakdown_variants --product <编号> --data workdata
python scripts/run.py tool check_factors     --product <编号> --data workdata
python scripts/run.py tool get_events        --product <编号> --data workdata
python scripts/run.py tool plan_actions      --product <编号> --cause <根因代码> --data workdata
```

**方式 2：一次拿到全部证据**：`python scripts/run.py diagnose --product <编号> --data workdata`。输出包含每一步的结果和一份规则初判（`rule_based_result`），你在此基础上审核、补充判断并撰写结论。

**诊断输出格式（五段）**：

1. **结论**：一句话，先说 GMV 变化，再说主要原因。
2. **贡献度**：访客数、支付转化率、客单价各贡献了多少（金额与占比）。
3. **根因与证据**：每个根因标注把握程度（强 / 中 / 弱），证据引用具体规格、日期、数字。
4. **方案**：1–3 个，只从 `plan_actions` 的 `candidates` 中选，逐项写出——
   - 方案名、对象（`target`）、执行类型（自己执行 / 需审批 / 转交）与负责角色
   - 关键数值（`params_text`）与测算（`estimate`：毛利变化、保本销量增幅或可挽回 GMV）
   - 需审批的原因（`approval_reasons`）
   - 执行步骤（`steps`）、跟踪指标与天数（`track`）、风险（`risks`）
   - 你选择它的理由（一两句业务语言）
5. **数据局限**：未检查的项目与数据缺口。

如果 `get_context` 返回 `within_normal_range: true`，说明变化属于正常波动：如实说明，不做下滑归因。

## 任务 C：经营周报

```bash
python scripts/run.py weekly-pack --data workdata
```

按 `templates/weekly_report.md` 的六个部分写作，数字只能来自数据包；没有数据的部分写「本周无」。

## 任务 D：商品分层

```bash
python scripts/run.py tier --data workdata
```

按爆品 / 潜力品 / 利润品 / 长尾品汇总，说明每层的判定规则（`references/tiering.md`）和生命周期对预警阈值的影响。

## 交付前自检

把要交付的文字存成文件，运行：

```bash
python scripts/run.py verify <文件> --product <编号> --data workdata     # 诊断
python scripts/run.py verify <文件> --weekly --data workdata             # 周报
```

`unmatched_numbers` 不为空时，删除或改正这些数字后再交付。

## 不能运行代码时

如果当前环境无法执行 Python：按 `references/metrics.md` 的口径和 `references/decomposition_tree.md` 的公式逐步手算并展示过程，在结论开头声明「未使用脚本校验」；方案仍只能从 `references/action_library.md` 中选择，参数按其中公式计算。

## 写作要求

- 面向商品运营，用业务语言；不出现字段名、命令名、工具名。
- 百分比保留一位小数，金额用「元」，日期写「9 月 17 日」或「09-17」。
- 先给结论，再给依据。

## 参考文件

| 文件 | 内容 |
| --- | --- |
| `references/metrics.md` | 指标字典与口径 |
| `references/decomposition_tree.md` | 指标拆解树与贡献度算法 |
| `references/tiering.md` | 商品分层与生命周期 |
| `references/alert_rules.md` | 预警规则、分级、合并降噪 |
| `references/attribution_sop.md` | 归因步骤与根因代码 |
| `references/action_library.md` | 动作库与经营约束 |
| `references/category_profiles.md` | 品类配置（通用默认 + 示例品类）与新增品类方法 |
| `references/data_spec.md` | 标准数据模型、最小字段、字段同义词、缺数据时的降级 |
| `templates/weekly_report.md` | 周报模板 |
| `templates/product_report.md` | 单品诊断报告模板 |
| `examples/` | 标准格式示例（3C）与后台导出宽表示例（零食） |
