# 品类配置

方法主体通用，品类差异只写在配置里。数据中的品类匹配不到专属配置时使用通用默认，并在结论中注明。

| 参数 | 通用消费品 | 3C 数码配件 | 休闲零食 |
| --- | --- | --- | --- |
| 新品期（天） | 30 | 30 | 21 |
| 成长期上限（天） | 90 | 90 | 60 |
| 利润品毛利率门槛 | 0.35 | 0.4 | 0.45 |
| GMV 下滑黄色阈值 | 0.12 | 0.12 | 0.1 |
| GMV 下滑红色阈值 | 0.2 | 0.2 | 0.18 |
| 异常 z 值 | 2.0 | 2.2 | 2.0 |
| 补货周期（天） | 7 | 10 | 5 |
| 价差阈值 | 0.05 | 0.03 | 0.08 |
| 影响因素检查顺序 | stock → price → rating → campaign | price → stock → campaign → rating | stock → rating → campaign → price |
| 季节性 | 无 | 价格敏感度高；618、双 11、数码节等大促前后波动大 | 节日礼盒需求集中（中秋、春节）；活动结束后回落属常见现象 |
| 价格类动作偏好 | store_coupon → gift → bundle_discount → price_match | store_coupon → gift → price_match → bundle_discount | bundle_discount → gift → store_coupon → price_match |
| 惯用赠品 | 小赠品 | 收纳包、数据线 | 小包装试吃装 |

品类匹配关键词：3C 数码配件：3c、3C、数码、数码配件、3C数码配件；休闲零食：snacks、零食、休闲零食、食品

## 新增一个品类

复制 `scripts/config/category_profiles/3c.yaml`，按新品类修改上表参数与 `match` 关键词。可以先让 AI 起草（例如母婴需注意效期与合规），**由懂行的人审核后生效**。
