# 场景四：出报告

用户说「出个周报」「这周生意怎么样，写成报告」「复盘一下国庆活动」「出一份某商品的诊断报告」时使用。三个场景各有一份**分析剧本**（`references/playbooks/`），写清楚了给谁看、要回答哪些问题、固定的章节、每章的计算和图表、判断标准和结论句式。

**动笔前，先读对应剧本和写作规则 `references/playbooks/writing_rules.md`。** 剧本里说的「平台」，在这里指本 Skill 的计算脚本；写作规则里的「draw_chart 图表工具」就是下面的 `chart` 命令。剧本里提到的待办、协同卡片、处理记录，是配套系统的功能，在这里没有：对应章节不会出现在数据包里，直接跳过；「一键转待办」改为在报告结尾列出「需要跟进的事」（事项、负责人、第一步）。

命令前缀都是 `python <SKILL>/scripts/run.py`。

## 1. 生成数据包

数据包里已经算好了每章的数据、判断标签和必备图：

```bash
python <SKILL>/scripts/run.py report --scene weekly  --pack report_pack.json                       # 周度经营分析
python <SKILL>/scripts/run.py campaigns                                                            # 先列出可复盘的活动
python <SKILL>/scripts/run.py report --scene campaign --campaign <活动编号> --pack report_pack.json  # 活动复盘
python <SKILL>/scripts/run.py report --scene product --product <商品> --pack report_pack.json      # 单品诊断报告
```

- 周报默认写数据最后一天所在的那一周。可以用 `--week-end YYYY-MM-DD` 指定周报的最后一天；用户给了月度目标时，加 `--target 目标金额（元）`，报告里会写目标进度。
- 用户说「复盘国庆活动」时，先运行 `campaigns`，按名称和日期找到对应活动。找不到时把列表给用户选。
- 数据里没有活动记录时，活动复盘做不了，直接说明，并告诉用户需要什么数据（活动起止日期、参与商品）。

## 2. 按剧本写 Markdown 报告

- 章节标题和顺序照数据包里的 `heading`（没有数据的章节已经去掉，编号是连续的）。每章第一句加粗，写结论。
- 数据包里某项是空值（null）时，不要硬写。需要的话用问数场景的 `query` 补查（例如活动前后的件单价、到手价），查到的数字同样能通过核对。
- 有必备图的章节，在结论下单独一行写 `[图表:编号]`，图下写解读。
- 数字只能来自数据包或图表工具的返回，不要自己计算新数字（环比、占比、差额都已经算好）。
- 报告存成文件，例如 `report.md`。

## 3. 需要补充图时（每份最多 3 张）

**只能用图表工具**，只传参数，不要自己写画图代码，也不要传数字：

```bash
python <SKILL>/scripts/run.py chart --pack report_pack.json --type dual_line --products S01 --metrics rating,refund_rate \
    --mark-date 2026-09-12 --mark-text 差评集中 --title "差评出现后评分下滑、退款率翻倍"
```

工具返回 `chart_id` 和数据摘要：把 `[图表:chart_id]` 写进正文，按摘要写解读。返回 `error` 时按说明改参数重试。图表类型和参数见 `references/chart_library.md`。补充图画了就会留在数据包里；画错了不想要，正文里不引用它即可，或者重新运行第 1 步生成数据包再画。

## 4. 核对数字，导出 HTML

```bash
python <SKILL>/scripts/run.py render report.md --pack report_pack.json --out 周报_0914-0920.html
```

- 核对范围是数据包、图表摘要，以及本次对话里其他命令（`query`、`diagnose`、`stock` 等）的结果；商品名里的数字不算。`unmatched_numbers` 不为空时，把这些数字改为引用计算结果里的数，再运行一次，直到为空。
- **把 HTML 文件交给用户**，文件名写清楚报告类型和日期。图表可以悬停查看数值；打开时需要联网（图表组件从网上加载），要告诉用户。
- 用户要修改时，改 Markdown，重新 `render`，再交一次最新的 HTML。
- 用户想发给同事看、想要在线文档时，可以把 Markdown 内容转成当前环境支持的在线文档（图表部分用文字或表格代替，并说明完整图表见 HTML）。

## 其他报告需求

- **月报、大促总结**：没有专门的剧本。可以用问数场景的 `query` 取整月数据（按周、按品类、TOP 商品），结构参照周报剧本，并告诉用户这是按周报结构整理的。
- **只想要一句话总结**：不用出完整报告，用问数场景回答即可。
