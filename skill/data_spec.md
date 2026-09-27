# 数据规范

## 最小数据要求

只要有下面 5 个字段就能分析：**日期、商品、支付金额（GMV）、访客数、支付买家数**（没有买家数但有支付转化率时，自动用「访客数 × 转化率」推算）。

其他字段越全，分析越深：

| 可选数据 | 缺失时的影响 |
| --- | --- |
| 支付件数 | 用买家数代替，件单价、人均件数不准 |
| 可售库存（最好到规格） | 查不了库存和补货；诊断时无法判断断货 |
| 成本价 | 算不了毛利；价格方案测算需要用户在对话里提供成本价 |
| 到手价 | 价格方案测算需要用户提供当前到手价 |
| 竞品到手价 | 看不了价差；诊断时无法判断价格劣势（测算照常，只是不给价差） |
| 评分、退款金额 | 诊断时无法判断口碑问题 |
| 渠道访客（搜索 / 推荐 / 付费 / 活动 / 站外） | 访客下滑无法定位到渠道 |
| 事件记录（调价、活动、投放调整、补货、差评） | 无法关联同期事件，证据把握度下降；做不了活动复盘 |
| 上架日期 | 用数据中首次出现的日期代替，新品判断可能不准 |

数据时长：问数、测算有几天数据就能做；单品诊断和周报至少 14 天；建议提供 8 周（56 天）左右，基线和趋势会更完整。

## 从电商后台导出

在店铺后台的数据分析 / 商品分析模块，导出**按天、按商品**的商品明细（常见名称：商品明细、单品分析、商品效果），时间选近 8 周。一次导不了这么长时，可以分几次导出，把多张表一起交给 `adapt`，会自动合并。表头上方有说明文字也没关系，会自动找到列名所在的行。

## 标准数据模型（6 张表）

| 表 | 粒度 | 字段 |
| --- | --- | --- |
| `products.csv` | 商品 | product_id, product_name, category, sub_category, launch_date, cost_price, list_price |
| `variants.csv` | 规格 | variant_id, product_id, variant_name |
| `daily_product.csv` | 商品 × 日 | date, product_id, uv, buyers, units, gmv, refund_amount, price, comp_price, rating |
| `daily_variant.csv` | 规格 × 日 | date, variant_id, units, gmv, stock_units |
| `daily_channel.csv` | 商品 × 渠道 × 日 | date, product_id, channel（search / recommend / paid / campaign / external）, uv |
| `events.csv` | 事件 | date, product_id（空 = 全店）, event_type, description, value |

`event_type`：price_change、competitor_price_change、campaign_start、campaign_end、ad_budget_change、restock（日期晚于数据截止日的视为在途）、review_issue、promo_day（大促日，计算基线时剔除）、external_content。

一致性要求：各渠道访客之和 = 访客数；各规格件数、金额之和 = 商品值；买家数 ≤ 访客数；件数 ≥ 买家数。

## 字段同义词（宽表自动识别）

| 标准字段 | 可识别的列名 |
| --- | --- |
| date | 统计日期、日期、数据日期 |
| product_id | 商品ID、商品编号、宝贝ID、SPU、商品编码 |
| product_name | 商品名称、商品标题、宝贝名称 |
| category | 类目、品类、一级类目 |
| uv | 商品访客数、访客数、UV、浏览人数 |
| buyers | 支付买家数、买家数、成交人数、支付人数 |
| cvr | 支付转化率、转化率 |
| units | 支付件数、成交件数、销量 |
| gmv | 支付金额、成交金额、销售额、GMV |
| refund_amount | 成功退款金额、退款金额 |
| price | 到手价、成交均价、售价 |
| comp_price | 竞品到手价、竞品价格、对标竞品价 |
| rating | 商品评分、评分、DSR |
| cost_price | 成本价、成本、采购价 |
| launch_date | 上架日期、上市日期、首次上架时间 |
| stock | 可售库存、库存、库存数量 |
| 渠道访客 | 搜索访客数、推荐访客数、付费访客数、活动访客数、站外访客数 |

识别不了或有多个候选时，`adapt` 会报告出来，请向用户确认后用 `--map 标准字段=原列名` 指定。
