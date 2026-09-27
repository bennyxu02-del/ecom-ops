# 图表库

报告里的图只能用 `scripts/run.py chart` 画：只传参数，数据由脚本从数据里取，参数不合格会返回错误说明。不要自己写画图代码。

| 图表 | 类型 | 用来说明什么 | 参数 |
| --- | --- | --- | --- |
| 指标卡 | kpi | 本期 vs 对比期的 2–6 个核心指标 | products、metrics、start/end |
| 趋势折线 | trend | 按天看走势，可加事件竖线、活动底色 | products、metrics（1–2 个）、start/end、mark、band |
| 双折线 | dual_line | 两个相关指标一起看，单位不同时左右两个坐标轴（评分 vs 退款率、我方价 vs 竞品价 vs 转化率） | 1 个商品、metrics（2–3 个）、mark |
| 瀑布图 | waterfall | GMV 变化从哪来：拆成访客、转化、客单价，或拆到商品 | products、by=factor / product、start/end |
| 横向条形 | hbar | 多个商品同一指标的变化，正负对比 | products、1 个指标、start/end |
| 分组柱 | grouped_bar | 对比期 vs 本期：按渠道、按规格或按商品 | products、by=channel / variant / product |
| 堆叠柱 | stacked_bar | 结构占比按周变化：分层 GMV 占比、规格销量占比 | products、by=tier / variant |

## 可用指标

| 指标 | 名称 | 按全部商品（ALL）汇总 |
| --- | --- | --- |
| gmv | GMV | 可以 |
| uv | 访客数 | 可以 |
| buyers | 支付买家数 | 可以 |
| units | 销量 | 可以 |
| cvr | 支付转化率 | 可以 |
| aov | 客单价 | 可以 |
| refund_rate | 退款率 | 可以 |
| margin | 毛利率 | 可以 |
| price | 到手价 | 只能单个商品 |
| comp_price | 竞品到手价 | 只能单个商品 |
| price_index | 价格指数 | 只能单个商品 |
| rating | 评分 | 只能单个商品 |
| stock | 库存 | 只能单个商品 |

## 参数

- `--products`：商品编号，逗号分隔；全部商品写 `ALL`
- `--metrics`：指标，逗号分隔
- `--by`：瀑布图 factor / product，分组柱 channel / variant / product，堆叠柱 tier / variant
- `--start` `--end`：本期（默认截止日前 28 天，瀑布图 / 条形 / 分组柱 / 指标卡默认 7 天）；`--compare-start` `--compare-end`：对比期（默认本期之前等长）
- `--mark-date` `--mark-text`：竖线标注；`--band-start` `--band-end` `--band-text`：底色区间
- `--title`：写结论，不写主题
