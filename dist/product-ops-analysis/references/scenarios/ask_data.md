# 场景一：问数

用户想知道「是多少」「谁最多」「占多少」「怎么算的」。**所有数字都用 `query` 取**，不要自己写代码汇总，因为口径容易出错。

## 先定时间

以数据的最后一天（输出里的 `as_of`）作为「今天」，不要用系统日期。

| 用户说 | 参数 | 对比期（自动） |
| --- | --- | --- |
| 没说时间 / 最近 / 近 7 天 | 不用加（默认最近 7 天） | 前 7 天 |
| 上周 | `--period last_week`（最近一个完整的自然周，周一至周日） | 再往前一周 |
| 这周 / 本周 | `--period this_week` | 上周同期 |
| 近 30 天 / 近一个月 | `--days 30` | 前 30 天 |
| 本月 | `--period this_month` | 上月同期 |
| 上个月 | `--period last_month` | 再上一个月 |
| 具体日期、某两个时间段比 | `--start --end`，对比期用 `--compare-start --compare-end` | 默认前一个等长时段 |
| 趋势（每天 / 每周） | `--by day` 或 `--by week`，配合 `--days 56` 等 | 不对比 |

回答时写清本期和对比期的日期（输出里的 `period` 和 `compare_period`）。输出的 `notes` 里有大促日、数据不完整、两段天数不同等提示，要转告用户。两段天数不同时，变化幅度按日均算，引用 `cur_daily`、`prev_daily`。

## 常见问法和命令

命令前缀都是 `python <SKILL>/scripts/run.py`，数据目录默认 `workdata`。

| 问法 | 命令 |
| --- | --- |
| 上周卖了多少、转化率和客单价怎么样 | `query --metrics gmv,uv,cvr,aov --period last_week` |
| 跌得最多的 5 个商品（默认按少卖的金额） | `query --metrics gmv --by product --sort-by diff --order asc --top 5` |
| 跌幅最大的商品（按百分比） | `query --metrics gmv --by product --sort-by change --order asc --top 5` |
| 转化率最低的商品 | `query --metrics cvr,uv --by product --sort cvr --sort-by value --order asc --top 10` |
| 涨幅最大的商品（按百分比） | `query --metrics gmv --by product --sort-by change --top 5` |
| GMV 最高的 10 个商品和占比 | `query --metrics gmv --by product --top 10`（`share` 为占比） |
| 各品类 / 子品类 / 商品层级的表现 | `query --metrics gmv,cvr --by category`（或 `sub_category`、`tier`） |
| 某商品的访客从哪来 | `query --metrics uv --by channel --products 充电宝` |
| 某商品各规格卖得怎么样 | `query --metrics units,gmv --by variant --products 充电宝` |
| 近 8 周 GMV 走势 | `query --metrics gmv --by week --days 56` |
| 某商品的到手价、竞品价、评分、毛利率 | `query --metrics price,comp_price,price_index,rating,margin --by product --products 充电宝` |
| 某个品类的数据 | 任何查询加 `--category 蓝牙耳机` |
| 有哪些商品、某商品叫什么 | `products`，或 `products --keyword 坚果` |

「跌得最多」没说按金额还是百分比时，默认按金额（`--sort-by diff`）；两种排法结果差别大时，一句话补充另一种的结果。

## 可查的指标

| 编号 | 指标 | 说明 |
| --- | --- | --- |
| gmv | GMV（支付金额） | 不扣退款 |
| uv | 访客数 | 多日为日访客之和 |
| buyers | 支付买家数 | |
| units | 支付件数 | |
| cvr | 支付转化率 | 买家数 ÷ 访客数，窗口内先求和再相除 |
| aov | 客单价 | GMV ÷ 买家数 |
| unit_price | 件单价 | GMV ÷ 件数 |
| units_per_buyer | 人均件数 | |
| refund_amount / refund_rate | 退款金额 / 退款率 | 需要退款数据 |
| price / comp_price / price_index | 到手价 / 竞品到手价 / 价格指数 | 单个商品的期末值 |
| rating | 评分 | 单个商品的期末值 |
| margin | 毛利率 | （到手价 − 成本价）÷ 到手价，单个商品的期末值 |

按渠道拆分只能查访客数；按规格拆分只能查件数和 GMV。数据里没有的，`query` 会在 `error` 或 `notes` 里说明。

## 口径问题

用户问「转化率怎么算」「GMV 含不含退款」这类问题时，查 `references/metrics.md` 回答：给出公式和口径说明，再补一句为什么这样算（例如「多天的转化率要先把买家和访客各自加总再相除，直接平均每天的转化率会被小流量的日子带偏」）。`query` 输出里的 `definitions` 也带有口径。

## 怎么写回答

- 第一句给结论，格式如：「上周（X 月 X 日至 X 日）GMV XX 万元，比前一周少 X 万元，下降 X%。」
- 排行、分组用小表格：商品、本期、对比期、变化、占比，最多 10 行。用户要全部明细时，把完整结果另存成表格文件交付。
- 金额变化看 `diff`，百分比变化看 `change`，占全部的比例看 `share`，占整体变化的比例看 `diff_share`（例如「这个商品少卖的金额占全店下滑的 70%」）。
- 比率类指标写百分点，百分点取 `diff`：「转化率从 4.58% 降到 4.46%，下降 0.12 个百分点」。
- 有明显异常的商品，结尾提一句「要不要看看它为什么跌了？」。

## 做不了的

- **同比去年**：数据里没有去年同期时，说明原因，改用环比。
- **投放 ROI、花费**：数据里没有投放花费，只能看付费渠道的访客。
- **订单级、用户级分析**（复购率、新老客）：数据是商品按天汇总的，做不了，直接说明。
