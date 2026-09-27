# 场景二：分析

用户想知道「为什么」：某个商品为什么变了、店铺整体为什么变了、哪些商品有问题。命令前缀都是 `python <SKILL>/scripts/run.py`。

## 单品：某商品为什么跌了 / 涨了

按 `references/attribution_sop.md` 的步骤归因。两种方式任选，结论标准相同。

**方式 1：一次拿到全部证据（推荐）**

```bash
python <SKILL>/scripts/run.py diagnose --product 坚果中秋礼盒
```

输出里有每一步的计算结果，以及一份规则初判（`rule_based_result`）。你在这个基础上审核、补充判断，再写结论。

**方式 2：逐步调用，适合用户想看分析过程**

```bash
python <SKILL>/scripts/run.py tool get_context        --product <商品>
python <SKILL>/scripts/run.py tool decompose_gmv      --product <商品>
python <SKILL>/scripts/run.py tool breakdown_channels --product <商品>
python <SKILL>/scripts/run.py tool breakdown_variants --product <商品>
python <SKILL>/scripts/run.py tool check_factors      --product <商品>
python <SKILL>/scripts/run.py tool get_events         --product <商品>
python <SKILL>/scripts/run.py tool plan_actions       --product <商品> --cause <根因代码>
```

如果 `get_context` 返回 `within_normal_range: true`，说明变化属于正常波动：如实说明，不要硬找下滑原因。

**诊断给不出明确原因时**（例如访客下滑、但数据里没有分渠道访客）：
- 仍然用 `tool check_factors` 和 `query --by day` 把库存、价格、口碑、每天的走势查一遍，说清楚哪些原因已经排除，以及变化是从哪一天开始的。
- 节日商品（礼盒、月饼等）要结合节日日期判断：节前需求集中、节后回落是常态，并提醒节后库存风险。
- 方案部分写「需要人工判断」，列出要去后台核对的具体事项，以及补哪份数据能判断得更准。不要编造动作库以外的方案。

**回答分五段：**

1. **结论**：一句话，先说 GMV 变化，再说主要原因。
2. **贡献度**：访客数、支付转化率、客单价各贡献了多少，给出金额和占比。
3. **根因与证据**：每个根因标注把握程度（强 / 中 / 弱），证据要具体到规格、日期和数字。
4. **方案**：1～3 个，只能从 `plans` / `candidates` 里选。每个方案写清楚：
   - 做什么、对谁做（`target`），谁来执行
   - 关键数值和测算：毛利变化、保本销量增幅，或可挽回的 GMV
   - 风险提示（超出调价权限、可能破价）
   - 执行步骤，以及用什么指标、看几天来判断有没有效
   - 为什么选它（一两句业务语言）
5. **数据局限**：哪些没检查、缺什么数据。

**交付前核对数字**：把回答存成文本文件，运行：

```bash
python <SKILL>/scripts/run.py verify answer.txt --product <商品>
```

它会拿本次对话里所有命令的结果比对。`unmatched_numbers` 不为空时，把这些数字改成引用计算结果里的数，或者删掉。

方案里执行人写「我」的，指用户本人（商品运营）；负责人要写清楚是商品运营、供应链还是投放。

方案是价格类的（优惠券、降价、赠品），结尾可以提一句：「可以帮你测算不同券面额的毛利和保本线。」

## 店铺整体：为什么 GMV 下滑 / 上涨

1. 先看整体和三要素：`query --metrics gmv,uv,cvr,aov`。判断变化主要来自访客、转化还是客单价。
2. 再看是哪些商品带来的：`query --metrics gmv --by product --sort-by diff --order asc --top 5`（上涨时去掉 `--order asc`）。用 `diff_share`（该商品的变化占整体变化的比例）说明「谁贡献了多少」。
3. 客单价变化时，看是单个商品的客单价变了，还是高价商品卖少了（结构变化）：`query --metrics aov,gmv --by product`。
4. 需要时按品类或分层看：`--by category` / `--by tier`。
5. 对贡献最大的 1～2 个商品，用上面的单品诊断说明原因，或者问用户要不要深入看。

回答结构：整体变化 → 主要来自哪几个商品（小表格） → 这几个商品各自的原因（一句话，已诊断的写结论，没诊断的写「可以进一步分析」）。

## 哪些商品有问题（异常排查）

```bash
python <SKILL>/scripts/run.py scan
```

- 只汇报 `is_today` 为 true 且 `suppressed` 为 false 的问题。按严重度（红 > 黄 > 蓝）和影响金额排序；`suppressed` 为 true 的是金额很小的波动，不单独汇报。
- 先写问题（`kind` = problem），再单独写机会（`kind` = opportunity）。
- 每个问题写一行：商品、严重程度、发生了什么、持续几天、影响 GMV 约多少（估算）。有 `in_transit` 的写上在途数量和到货日。
- `expected` 不为空的，是活动结束后的正常回落，一句带过，不算问题。
- `plateau` 不为空的，是指标还没回到出问题前的水平，要提醒用户「还没完全恢复」。
- `auto_status` 为 recovered 的，是已经恢复的问题，一句带过。
- 判断规则见 `references/alert_rules.md`。新品期的阈值更宽，所以没报出来不代表没有波动，而是在正常范围内。

结尾可以问用户要不要看其中某个商品的原因。

## 商品分层

```bash
python <SKILL>/scripts/run.py tier
```

按爆品、潜力品、利润品、长尾品汇总，说明每层的判定规则（`references/tiering.md`），以及每个商品所处的生命周期。
