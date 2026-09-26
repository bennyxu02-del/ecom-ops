"""模拟数据生成器

生成两个数据集（3C 数码配件、休闲零食），输出标准数据模型的 6 张 CSV 表，
并按需求文档第 13 章埋入演示案例。固定随机种子，结果可复现。

用法：python data/generator/generate.py
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "datasets"
START = pd.Timestamp("2026-06-23")
END = pd.Timestamp("2026-09-20")
DATES = pd.date_range(START, END, freq="D")
CHANNELS = ["search", "recommend", "paid", "campaign", "external"]


def d(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


@dataclass
class Product:
    pid: str
    name: str
    sub: str
    launch: str
    cost: float
    price: float
    comp: float
    uv: float
    cvr: float
    upb: float
    variants: list  # [(name, share)]
    ch: dict = field(default_factory=lambda: {"search": .38, "recommend": .30, "paid": .20, "campaign": 0.0, "external": .12})
    rating: float = 4.8
    refund: float = 0.03
    growth: str | None = None     # "ramp" 表示上市后爬坡
    noise: bool = True
    case: str | None = None


# ---------------------------------------------------------------------------
# 3C 数码配件
# ---------------------------------------------------------------------------
def products_3c() -> list[Product]:
    P = Product
    return [
        P("P01", "20000mAh 快充充电宝", "充电宝", "2025-03-01", 62, 129, 135, 9000, .045, 1.05,
          [("黑色", .50), ("白色", .40), ("蓝色", .10)], case="A"),
        P("P02", "降噪蓝牙耳机 Pro", "蓝牙耳机", "2025-05-10", 150, 289, 299, 3000, .035, 1.0,
          [("曜石黑", .60), ("云雾白", .40)], case="B"),
        P("P03", "65W 氮化镓充电器", "充电器", "2026-07-25", 48, 99, 105, 2600, .045, 1.05,
          [("白色", .70), ("黑色", .30)],
          ch={"search": .25, "recommend": .20, "paid": .50, "campaign": 0.0, "external": .05}, growth="ramp", case="C"),
        P("P04", "开放式蓝牙耳机", "蓝牙耳机", "2026-09-01", 95, 199, 209, 1300, .03, 1.0,
          [("星光白", .55), ("深空灰", .45)], noise=False, case="D"),
        P("P05", "编织快充数据线", "数据线", "2024-11-01", 12, 39, 42, 12000, .06, 1.3,
          [("1m 白色", .40), ("2m 白色", .33), ("1m 黑色", .27)], case="E"),
        P("P06", "磁吸无线充电宝", "充电宝", "2025-08-15", 85, 159, 169, 3500, .04, 1.02,
          [("白色", .55), ("黑色", .45)],
          ch={"search": .38, "recommend": .30, "paid": .20, "campaign": 0.0, "external": .12}, case="F"),
        P("P07", "20W 快充头", "充电器", "2024-09-01", 30, 69, 72, 8000, .05, 1.15,
          [("白色", .75), ("黑色", .25)]),
        P("P08", "10000mAh 迷你充电宝", "充电宝", "2025-01-10", 48, 79, 85, 1600, .035, 1.05,
          [("粉色", .40), ("白色", .35), ("黑色", .25)]),
        P("P09", "户外大容量充电宝", "充电宝", "2025-04-01", 170, 269, 289, 500, .025, 1.0,
          [("军绿", .55), ("黑色", .45)]),
        P("P10", "入门真无线耳机", "蓝牙耳机", "2024-12-01", 52, 89, 95, 1700, .04, 1.0,
          [("白色", .60), ("黑色", .40)]),
        P("P11", "运动挂耳耳机", "蓝牙耳机", "2025-06-01", 90, 149, 159, 700, .03, 1.0,
          [("黑色", .50), ("橙色", .50)]),
        P("P12", "头戴式蓝牙耳机", "蓝牙耳机", "2025-02-15", 150, 239, 249, 450, .025, 1.0,
          [("黑色", .60), ("米白", .40)]),
        P("P13", "多口桌面充电站", "充电器", "2025-07-01", 105, 169, 179, 450, .03, 1.0,
          [("白色", 1.0)]),
        P("P14", "车载快充充电器", "充电器", "2024-10-01", 25, 45, 49, 1500, .045, 1.1,
          [("黑色", 1.0)]),
        P("P15", "Type-C 转 Lightning 数据线", "数据线", "2024-08-01", 16, 29, 32, 1700, .05, 1.2,
          [("1m", .60), ("2m", .40)]),
        P("P16", "三合一数据线", "数据线", "2025-01-01", 18, 35, 38, 1100, .045, 1.15,
          [("黑色", .55), ("白色", .45)]),
        P("P17", "磁吸数据线", "数据线", "2025-09-01", 20, 39, 42, 800, .04, 1.1,
          [("黑色", .50), ("白色", .50)]),
        P("P18", "桌面手机支架", "支架", "2024-06-01", 18, 29.9, 32, 1400, .04, 1.1,
          [("银色", .60), ("黑色", .40)]),
        P("P19", "车载磁吸支架", "支架", "2024-10-15", 32, 49, 55, 1000, .04, 1.05,
          [("黑色", 1.0)]),
        P("P20", "折叠平板支架", "支架", "2025-05-01", 36, 59, 65, 450, .035, 1.0,
          [("银色", .50), ("深空灰", .50)]),
    ]


# ---------------------------------------------------------------------------
# 休闲零食
# ---------------------------------------------------------------------------
def products_snacks() -> list[Product]:
    P = Product
    base_ch = {"search": .40, "recommend": .33, "paid": .15, "campaign": 0.0, "external": .12}
    return [
        P("S01", "挂耳咖啡 精品混合装", "咖啡", "2025-01-15", 28, 59, 62, 10000, .07, 1.4,
          [("10 包装", .55), ("20 包装", .45)], ch=dict(base_ch), rating=4.8, refund=.03, case="G"),
        P("S02", "坚果中秋礼盒", "坚果", "2026-08-01", 95, 168, 178, 5400, .05, 1.2,
          [("经典款", .60), ("尊享款", .40)], ch=dict(base_ch), growth="ramp", case="H"),
        P("S03", "每日坚果 30 包", "坚果", "2024-10-01", 40, 79, 82, 7800, .06, 1.3,
          [("原味", .65), ("益生菌款", .35)], ch=dict(base_ch)),
        P("S04", "苏打饼干 家庭装", "饼干", "2025-02-01", 14.5, 29.9, 32, 6000, .06, 1.6,
          [("海盐味", .60), ("葱香味", .40)], ch=dict(base_ch), case="I"),
        P("S05", "夏威夷果 奶油味", "坚果", "2025-03-01", 22, 39.9, 42, 2200, .05, 1.3,
          [("250g", .60), ("500g", .40)], ch=dict(base_ch)),
        P("S06", "盐焗腰果", "坚果", "2025-05-01", 26, 45, 48, 1300, .045, 1.25,
          [("250g", 1.0)], ch=dict(base_ch)),
        P("S07", "冻干咖啡粉", "咖啡", "2025-04-01", 40, 69, 72, 1800, .05, 1.2,
          [("经典", .60), ("浓醇", .40)], ch=dict(base_ch)),
        P("S08", "冷萃咖啡液", "咖啡", "2025-06-01", 38, 59, 64, 1200, .045, 1.3,
          [("原味", .50), ("燕麦拿铁", .50)], ch=dict(base_ch)),
        P("S09", "速溶三合一咖啡", "咖啡", "2024-09-01", 20, 32.9, 35, 1500, .05, 1.4,
          [("原味", .60), ("特浓", .40)], ch=dict(base_ch)),
        P("S10", "全麦饼干", "饼干", "2025-01-01", 12, 22.9, 25, 1600, .05, 1.5,
          [("原味", 1.0)], ch=dict(base_ch)),
        P("S11", "巧克力夹心饼干", "饼干", "2024-11-01", 11, 19.9, 21, 1400, .055, 1.5,
          [("巧克力", .60), ("香草", .40)], ch=dict(base_ch)),
        P("S12", "手工蛋卷礼盒", "饼干", "2025-03-15", 30, 49, 52, 700, .04, 1.2,
          [("原味", .60), ("芝麻", .40)], ch=dict(base_ch)),
        P("S13", "猪肉脯", "肉脯果干", "2024-12-01", 22, 39.9, 42, 1400, .05, 1.3,
          [("原味", .60), ("蜜汁", .40)], ch=dict(base_ch)),
        P("S14", "风干牛肉干", "肉脯果干", "2025-02-01", 45, 79, 85, 700, .04, 1.2,
          [("五香", .50), ("麻辣", .50)], ch=dict(base_ch)),
        P("S15", "芒果干", "肉脯果干", "2025-04-01", 13, 25.9, 28, 1300, .05, 1.4,
          [("200g", 1.0)], ch=dict(base_ch)),
        P("S16", "冻干草莓", "肉脯果干", "2025-07-01", 20, 35.9, 38, 700, .04, 1.3,
          [("100g", 1.0)], ch=dict(base_ch)),
    ]


# ---------------------------------------------------------------------------
# 案例修饰：返回当天的倍率与覆盖
# ---------------------------------------------------------------------------
D4 = [0.6, 0.9, 1.3, 0.8, 1.2, 1.4, 0.9,           # 09-01 ~ 09-07 前 6 天 + 07
      1.35, 0.7, 1.2, 1.4, 0.75, 1.3, 1.1,          # 前一周后段（09-08 ~ 09-14 与窗口对齐见下）
      0.8, 1.2, 0.65, 1.1, 0.9, 1.25, 0.7]
# D4 索引 0 = 09-01。窗口：前 7 日 = 09-07~09-13（索引 6~12），近 7 日 = 09-14~09-20（索引 13~19）
# 调整使 近7日/前7日 ≈ 0.85：
D4 = [0.6, 0.9, 1.3, 0.8, 1.2, 1.4,
      1.35, 0.7, 1.2, 1.4, 0.75, 1.3, 1.1,          # 09-07 ~ 09-13 合计 7.8
      0.8, 1.2, 0.65, 1.1, 0.9, 1.25, 0.7]          # 09-14 ~ 09-20 合计 6.6


def modifiers(p: Product, day: pd.Timestamp) -> dict:
    m = {"uv": 1.0, "cvr": 1.0, "ch": {}, "comp": None, "rating": None, "refund": None,
         "oos": set()}
    c = p.case
    if c == "A":
        if day >= d("2026-09-17"):
            m["oos"] = {"白色"}
    elif c == "B":
        if day >= d("2026-09-12"):
            m["comp"] = 269.0
        if day >= d("2026-09-13"):
            m["cvr"] = 0.82
    elif c == "C":
        if day >= d("2026-09-15"):
            m["ch"]["paid"] = 0.40
    elif c == "D":
        idx = (day - d("2026-09-01")).days
        if 0 <= idx < len(D4):
            m["uv"] = D4[idx]
    elif c == "E":
        if d("2026-09-13") <= day <= d("2026-09-14"):
            m["oos"] = {"1m 白色"}
    elif c == "F":
        if day >= d("2026-09-18"):
            m["ch"]["external"] = 7.5
    elif c == "G":
        rating_path = {"2026-09-12": 4.78, "2026-09-13": 4.68, "2026-09-14": 4.60}
        key = day.strftime("%Y-%m-%d")
        if key in rating_path:
            m["rating"] = rating_path[key]
        elif day >= d("2026-09-15"):
            m["rating"] = 4.55
        if day >= d("2026-09-12"):
            m["refund"] = 0.06
        if day >= d("2026-09-13"):
            m["cvr"] = 0.80
    elif c == "H":
        if d("2026-08-25") <= day <= d("2026-09-14"):
            m["ch"]["campaign_add"] = 0.67
    return m


def ramp(p: Product, day: pd.Timestamp) -> float:
    t = (day - d(p.launch)).days
    if p.growth == "ramp":
        if p.case == "C":
            return 0.25 + 0.75 * (1 - math.exp(-t / 30))
        return 0.35 + 0.65 * (1 - math.exp(-t / 45))
    return 1.0


def alloc_int(total: int, weights: dict) -> dict:
    """按权重把整数分配到各键（最大余数法），保证总和一致。"""
    keys = [k for k, w in weights.items() if w > 0]
    if total <= 0 or not keys:
        return {k: 0 for k in weights}
    s = sum(weights[k] for k in keys)
    raw = {k: total * weights[k] / s for k in keys}
    out = {k: int(math.floor(v)) for k, v in raw.items()}
    rest = total - sum(out.values())
    for k in sorted(keys, key=lambda k: raw[k] - out[k], reverse=True)[:rest]:
        out[k] += 1
    for k in weights:
        out.setdefault(k, 0)
    return out


def generate(products: list[Product], promo_days: list[str], seed: int):
    rng = np.random.default_rng(seed)
    rows_p, rows_v, rows_c = [], [], []
    promo = {d(x) for x in promo_days}

    for p in products:
        launch = d(p.launch)
        for day in DATES:
            if day < launch:
                continue
            m = modifiers(p, day)
            weekend = day.dayofweek >= 5
            nu = rng.lognormal(0, 0.05) if p.noise else 1.0
            nc = rng.lognormal(0, 0.035) if p.noise else 1.0
            uv_base = p.uv * ramp(p, day) * (1.10 if weekend else 1.0) * nu * m["uv"]
            cvr = p.cvr * (1.03 if weekend else 1.0) * nc * m["cvr"]
            if day in promo:
                uv_base *= 2.5
                cvr *= 1.4

            # 渠道
            ch_w = dict(p.ch)
            add = m["ch"].pop("campaign_add", 0.0) if "campaign_add" in m["ch"] else 0.0
            for k, v in m["ch"].items():
                ch_w[k] = ch_w[k] * v
            ch_w["campaign"] = ch_w.get("campaign", 0.0) + add
            ch_total = sum(ch_w.values())
            if p.noise:   # 各渠道独立的日波动（只影响渠道结构，不改变总访客）
                ch_w = {k: v * rng.lognormal(0, 0.06) for k, v in ch_w.items()}
            uv = int(round(uv_base * ch_total))
            ch_uv = alloc_int(uv, ch_w)

            # 买家与规格
            potential_buyers = uv * cvr
            shares = {n: s for n, s in p.variants}
            oos = m["oos"]
            lost = sum(shares[n] for n in oos) * potential_buyers
            kept_share = {n: (0.0 if n in oos else s) for n, s in shares.items()}
            buyers_f = potential_buyers - lost * 0.85
            buyers = int(round(buyers_f))
            units = int(round(buyers * p.upb)) if buyers > 0 else 0
            units = max(units, buyers)
            var_units = alloc_int(units, kept_share)

            price = p.price
            comp = m["comp"] if m["comp"] is not None else p.comp
            if day in promo:
                price = round(p.price * 0.9)     # 大促日到手价 9 折
                comp = round(comp * 0.9)
            gmv = round(units * price, 2)
            rating = m["rating"] if m["rating"] is not None else round(p.rating + rng.normal(0, 0.005), 2)
            refund_rate = m["refund"] if m["refund"] is not None else p.refund * (1 + rng.normal(0, 0.03))
            rows_p.append(dict(date=day, product_id=p.pid, uv=uv, buyers=buyers, units=units, gmv=gmv,
                               refund_amount=round(gmv * refund_rate, 2), price=price, comp_price=comp,
                               rating=rating))
            for k, v in ch_uv.items():
                rows_c.append(dict(date=day, product_id=p.pid, channel=k, uv=v))
            for i, (n, _) in enumerate(p.variants):
                vid = f"{p.pid}V{i + 1}"
                rows_v.append(dict(date=day, variant_id=vid, units=var_units[n],
                                   gmv=round(var_units[n] * price, 2), stock_units=0))

    dp = pd.DataFrame(rows_p)
    dv = pd.DataFrame(rows_v)
    dc = pd.DataFrame(rows_c)
    return dp, dv, dc


# ---------------------------------------------------------------------------
# 库存模拟
# ---------------------------------------------------------------------------
def simulate_stock(dv: pd.DataFrame, products: list[Product], special: dict):
    """普通规格：低于 15 天覆盖时补到 45 天覆盖（次日到货）。
    special：{variant_id: {"zero_from": date, "restart": date, "end_target_days": x, "freeze_from": date}}"""
    dv = dv.sort_values(["variant_id", "date"]).reset_index(drop=True)
    out = []
    for vid, g in dv.groupby("variant_id", sort=False):
        g = g.copy()
        units = g["units"].to_numpy(dtype=float)
        dates = list(g["date"])
        base = max(np.median(units[-28:]), 1.0)
        stock = np.zeros(len(g))
        s = base * 30
        for i in range(len(g)):
            s -= units[i]
            if s < base * 15:
                s += base * 30
            stock[i] = round(s)
        sp = special.get(vid)
        if sp:
            if "zero_at" in sp:
                # 期末为 0 的日期区间，往前回推到 restock_at（当日到货）
                z0 = dates.index(d(sp["zero_at"]))
                for i in range(z0, len(g)):
                    if "restart_at" in sp and dates[i] >= d(sp["restart_at"]):
                        break
                    stock[i] = 0
                r0 = dates.index(d(sp["refill_at"]))
                # 严格回推：前一日期末库存 = 当日期末库存 + 当日销量
                for i in range(z0, r0, -1):
                    stock[i - 1] = stock[i] + units[i]
                if "restart_at" in sp:
                    r1 = dates.index(d(sp["restart_at"]))
                    s = base * 30
                    for i in range(r1, len(g)):
                        s -= units[i]
                        if s < base * 15:
                            s += base * 30
                        stock[i] = round(s)
            if "end_days" in sp:
                last7 = units[-7:].mean()
                end = round(sp["end_days"] * last7)
                r0 = dates.index(d(sp["refill_at"]))
                stock[-1] = end
                for i in range(len(g) - 1, r0, -1):
                    stock[i - 1] = stock[i] + units[i]
        g["stock_units"] = stock.astype(int)
        out.append(g)
    return pd.concat(out).sort_values(["date", "variant_id"]).reset_index(drop=True)


def write(name: str, category: str, products: list[Product], dp, dv, dc, events):
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dict(product_id=p.pid, product_name=p.name, category=category, sub_category=p.sub,
                       launch_date=p.launch, cost_price=p.cost, list_price=p.price) for p in products]
                 ).to_csv(folder / "products.csv", index=False)
    pd.DataFrame([dict(variant_id=f"{p.pid}V{i + 1}", product_id=p.pid, variant_name=n)
                  for p in products for i, (n, _) in enumerate(p.variants)]
                 ).to_csv(folder / "variants.csv", index=False)
    for df, fn in [(dp, "daily_product.csv"), (dv, "daily_variant.csv"), (dc, "daily_channel.csv")]:
        df = df.copy()
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        df.to_csv(folder / fn, index=False)
    pd.DataFrame(events, columns=["date", "product_id", "event_type", "description", "value"]
                 ).to_csv(folder / "events.csv", index=False)


def vid_of(products, pid, vname):
    p = next(x for x in products if x.pid == pid)
    return f"{pid}V{[n for n, _ in p.variants].index(vname) + 1}"


def build_3c():
    ps = products_3c()
    dp, dv, dc = generate(ps, ["2026-08-08"], seed=20260920)
    special = {
        vid_of(ps, "P01", "白色"): {"zero_at": "2026-09-16", "refill_at": "2026-09-09"},
        vid_of(ps, "P05", "1m 白色"): {"zero_at": "2026-09-12", "refill_at": "2026-09-06",
                                       "restart_at": "2026-09-15"},
        vid_of(ps, "P06", "白色"): {"end_days": 6.0, "refill_at": "2026-09-10"},
        vid_of(ps, "P06", "黑色"): {"end_days": 6.5, "refill_at": "2026-09-10"},
    }
    dv = simulate_stock(dv, ps, special)
    events = [
        ["2026-08-08", "", "promo_day", "数码节大促", ""],
        ["2026-09-12", "P02", "competitor_price_change", "主要竞品到手价由 299 元降至 269 元", 269],
        ["2026-09-22", "P01", "restock", "白色款补货在途，预计 9 月 22 日到货", 3200],
        ["2026-09-15", "P03", "ad_budget_change", "付费推广日预算下调 50%", -0.5],
        ["2026-09-15", "P05", "restock", "1m 白色补货到货", 12000],
        ["2026-09-18", "P06", "external_content", "达人测评视频发布，站外引流", ""],
        ["2026-09-01", "P04", "campaign_start", "新品上市", ""],
    ]
    write("3c", "3c", ps, dp, dv, dc, events)
    return ps


def build_snacks():
    ps = products_snacks()
    dp, dv, dc = generate(ps, [], seed=20260921)
    special = {
        vid_of(ps, "S04", "海盐味"): {"end_days": 3.0, "refill_at": "2026-09-12"},
    }
    dv = simulate_stock(dv, ps, special)
    events = [
        ["2026-09-12", "S01", "review_issue", "某批次集中出现「结块、口感发酸」差评", ""],
        ["2026-08-25", "S02", "campaign_start", "中秋礼盒会场活动开始", ""],
        ["2026-09-15", "S02", "campaign_end", "中秋礼盒会场活动结束", ""],
    ]
    write("snacks", "snacks", ps, dp, dv, dc, events)
    return ps


def build_skill_raw():
    """Skill 测试用：零食数据的「非标准格式」版本——单张宽表、中文字段名、近 45 天。"""
    folder = OUT / "snacks"
    dp = pd.read_csv(folder / "daily_product.csv")
    pr = pd.read_csv(folder / "products.csv")
    dv = pd.read_csv(folder / "daily_variant.csv")
    va = pd.read_csv(folder / "variants.csv")
    stock = dv.merge(va, on="variant_id").groupby(["date", "product_id"], as_index=False)["stock_units"].sum()
    df = dp.merge(pr, on="product_id").merge(stock, on=["date", "product_id"], how="left")
    df = df[df["date"] >= "2026-08-07"]
    raw = pd.DataFrame({
        "统计日期": df["date"], "商品ID": df["product_id"], "商品名称": df["product_name"],
        "类目": "休闲零食", "商品访客数": df["uv"], "支付买家数": df["buyers"], "支付件数": df["units"],
        "支付金额": df["gmv"], "成功退款金额": df["refund_amount"], "到手价": df["price"],
        "竞品到手价": df["comp_price"], "商品评分": df["rating"], "成本价": df["cost_price"],
        "上架日期": df["launch_date"], "可售库存": df["stock_units"],
    })
    out = OUT / "skill_test"
    out.mkdir(parents=True, exist_ok=True)
    raw.to_csv(out / "零食店铺_商品日报_导出.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    build_3c()
    build_snacks()
    build_skill_raw()
    print("datasets written to", OUT)
