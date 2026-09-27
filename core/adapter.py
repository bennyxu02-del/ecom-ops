"""数据适配：把用户上传的各种格式（标准多表 / 电商后台导出的单张宽表）转换为标准数据模型。

原则：能确定的自动映射；有歧义或缺失时如实报告，由使用者确认，不擅自猜测。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pandas as pd

from .loader import TABLES

SYNONYMS = {
    "date": ["统计日期", "日期", "数据日期", "date", "日期时间", "统计时间"],
    "product_id": ["商品ID", "商品id", "商品编号", "宝贝ID", "SPU", "SPU ID", "product_id", "商品编码"],
    "product_name": ["商品名称", "商品标题", "宝贝名称", "product_name", "商品"],
    "category": ["类目", "品类", "一级类目", "category", "行业"],
    "uv": ["商品访客数", "访客数", "UV", "uv", "访客", "浏览人数"],
    "buyers": ["支付买家数", "买家数", "成交人数", "支付人数", "buyers"],
    "cvr": ["支付转化率", "转化率", "成交转化率"],
    "units": ["支付件数", "成交件数", "销量", "units", "支付商品件数"],
    "gmv": ["支付金额", "成交金额", "销售额", "GMV", "gmv", "支付GMV"],
    "refund_amount": ["成功退款金额", "退款金额", "refund_amount"],
    "price": ["到手价", "成交均价", "price", "售价"],
    "comp_price": ["竞品到手价", "竞品价格", "对标竞品价", "comp_price"],
    "rating": ["商品评分", "评分", "DSR", "rating", "评价分"],
    "cost_price": ["成本价", "成本", "采购价", "cost_price"],
    "launch_date": ["上架日期", "上市日期", "首次上架时间", "launch_date", "上新日期"],
    "stock": ["可售库存", "库存", "库存数量", "stock", "stock_units"],
    "uv_search": ["搜索访客数", "搜索访客"],
    "uv_recommend": ["推荐访客数", "推荐访客"],
    "uv_paid": ["付费访客数", "付费访客", "推广访客"],
    "uv_campaign": ["活动访客数", "活动访客"],
    "uv_external": ["站外访客数", "站外访客"],
}
REQUIRED = ["date", "product_id", "gmv", "uv", "buyers"]


def _norm(s: str) -> str:
    return re.sub(r"[\s_（）()]", "", str(s)).lower()


def _to_num(x: pd.Series) -> pd.Series:
    if x.dtype.kind in "if":
        return x
    s = x.astype(str).str.replace(",", "").str.replace("¥", "").str.replace("￥", "").str.strip()
    pct = s.str.endswith("%")
    v = pd.to_numeric(s.str.rstrip("%"), errors="coerce")
    v[pct] = v[pct] / 100
    return v


def _hits(cols) -> int:
    allsyn = {_norm(x) for v in SYNONYMS.values() for x in v}
    return sum(1 for c in cols if _norm(c) in allsyn)


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法读取 {path}：文件编码无法识别")


def read_any(path: Path) -> pd.DataFrame:
    """读取 CSV / Excel。后台导出常在表头上方带几行说明文字：自动找到字段名所在的行。"""
    import io
    if path.suffix.lower() in (".xlsx", ".xls"):
        probe = pd.read_excel(path, header=None, nrows=20)
        rows = [probe.iloc[i].astype(str).tolist() for i in range(len(probe))]
        best = max(range(len(rows)), key=lambda i: _hits(rows[i]), default=0)
        return pd.read_excel(path, header=best if rows and _hits(rows[best]) >= 3 else 0).dropna(how="all")
    text = _decode(path)
    lines = text.splitlines()[:20]
    sep = "\t" if sum(x.count("\t") for x in lines) > sum(x.count(",") for x in lines) else ","
    best = max(range(len(lines)), key=lambda i: _hits(lines[i].split(sep)), default=0)
    skip = best if lines and _hits(lines[best].split(sep)) >= 3 else 0
    return pd.read_csv(io.StringIO(text), sep=sep, skiprows=skip).dropna(how="all")


def map_columns(cols: list[str]) -> tuple[dict, dict]:
    """返回 (映射 {标准字段: 原列名}, 歧义 {标准字段: [候选列]})"""
    mapping, ambiguous = {}, {}
    ncols = {c: _norm(c) for c in cols}
    for std, syns in SYNONYMS.items():
        nsyn = [_norm(s) for s in syns]
        exact = [c for c, n in ncols.items() if n in nsyn]
        if len(exact) == 1:
            mapping[std] = exact[0]
        elif len(exact) > 1:
            ambiguous[std] = exact
    return mapping, ambiguous


def adapt(inputs: list[str], out: str, category: str | None = None, overrides: dict | None = None) -> dict:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [Path(p) for p in inputs]
    # 情况一：已经是标准格式的目录
    if len(paths) == 1 and paths[0].is_dir() and (paths[0] / "daily_product.csv").exists():
        for t in TABLES:
            f = paths[0] / f"{t}.csv"
            if f.exists():
                shutil.copy(f, out_dir / f.name)
        return dict(format="standard", out=str(out_dir), mapped="已是标准数据模型，直接使用",
                    missing=[], ambiguous={}, note=None)
    # 情况二：商品日报宽表（一张，或同样格式的多张，例如按周分别导出）
    if any(p.is_dir() for p in paths):
        raise ValueError("请提供一个标准数据目录，或商品日报表文件（CSV / Excel，可以多张）")
    frames = [read_any(p) for p in paths]
    if len(frames) > 1:
        cols = [set(f.columns) for f in frames]
        if any(c != cols[0] for c in cols[1:]):
            raise ValueError("多张表的列不一致，无法合并：请确认是同一种报表的导出")
    df = pd.concat(frames, ignore_index=True).drop_duplicates()
    mapping, ambiguous = map_columns(list(df.columns))
    mapping.update(overrides or {})
    for k in list(ambiguous):
        if k in mapping:
            ambiguous.pop(k)
    derived = []
    if "buyers" not in mapping and "cvr" in mapping and "uv" in mapping:
        derived.append("支付买家数 = 访客数 × 支付转化率")
    missing_req = [k for k in REQUIRED if k not in mapping and not (k == "buyers" and derived)]
    if "product_id" not in mapping and "product_name" in mapping:
        missing_req.remove("product_id")
        derived.append("商品编号取自商品名称")
    report = dict(format="wide_table", source=[str(p) for p in paths] if len(paths) > 1 else str(paths[0]), rows=len(df),
                  mapped={k: v for k, v in mapping.items()}, ambiguous=ambiguous, derived=derived,
                  unmapped_columns=[c for c in df.columns if c not in mapping.values()])
    if missing_req or ambiguous:
        report.update(ok=False, missing_required=missing_req,
                      note="存在缺失的必填字段或有歧义的字段，请向用户确认后用 --map 标准字段=原列名 指定")
        return report

    g = pd.DataFrame()
    g["date"] = pd.to_datetime(df[mapping["date"]]).dt.strftime("%Y-%m-%d")
    g["product_id"] = df[mapping.get("product_id", mapping.get("product_name"))].astype(str)
    g["uv"] = _to_num(df[mapping["uv"]]).fillna(0).round().astype(int)
    if "buyers" in mapping:
        g["buyers"] = _to_num(df[mapping["buyers"]]).fillna(0).round().astype(int)
    else:
        g["buyers"] = (g["uv"] * _to_num(df[mapping["cvr"]])).round().astype(int)
    g["gmv"] = _to_num(df[mapping["gmv"]]).fillna(0)
    g["units"] = _to_num(df[mapping["units"]]).fillna(0).round().astype(int) if "units" in mapping else g["buyers"]
    g["units"] = g[["units", "buyers"]].max(axis=1)
    g["buyers"] = g[["buyers", "uv"]].min(axis=1)
    for k in ("refund_amount", "price", "comp_price", "rating"):
        if k in mapping:
            g[k] = _to_num(df[mapping[k]])
    g = g.groupby(["date", "product_id"], as_index=False).agg(
        {c: ("mean" if c in ("price", "comp_price", "rating") else "sum") for c in g.columns if c not in ("date", "product_id")})
    g.to_csv(out_dir / "daily_product.csv", index=False)

    prod = pd.DataFrame({"product_id": df[mapping.get("product_id", mapping.get("product_name"))].astype(str)})
    prod["product_name"] = df[mapping["product_name"]].astype(str) if "product_name" in mapping else prod["product_id"]
    cat = category or (str(df[mapping["category"]].dropna().mode().iloc[0]) if "category" in mapping else "")
    prod["category"] = cat
    prod["sub_category"] = ""
    prod["launch_date"] = pd.to_datetime(df[mapping["launch_date"]]).dt.strftime("%Y-%m-%d") if "launch_date" in mapping else None
    prod["cost_price"] = _to_num(df[mapping["cost_price"]]) if "cost_price" in mapping else None
    prod = prod.drop_duplicates("product_id")
    prod.to_csv(out_dir / "products.csv", index=False)

    ch_cols = {k[3:]: mapping[k] for k in ("uv_search", "uv_recommend", "uv_paid", "uv_campaign", "uv_external") if k in mapping}
    if ch_cols:
        rows = []
        for _, r in df.iterrows():
            for ch, col in ch_cols.items():
                rows.append(dict(date=pd.to_datetime(r[mapping["date"]]).strftime("%Y-%m-%d"),
                                 product_id=str(r[mapping.get("product_id", mapping.get("product_name"))]), channel=ch,
                                 uv=int(_to_num(pd.Series([r[col]])).fillna(0).iloc[0])))
        dc = pd.DataFrame(rows).groupby(["date", "product_id", "channel"], as_index=False)["uv"].sum()
        # 渠道之和须等于访客数：差额计入「其他」不在标准渠道内，按比例校正
        tot = dc.groupby(["date", "product_id"])["uv"].transform("sum")
        dc = dc.merge(g[["date", "product_id", "uv"]].rename(columns={"uv": "uv_all"}), on=["date", "product_id"])
        dc["uv"] = (dc["uv"] / tot.replace(0, 1) * dc["uv_all"]).round().astype(int)
        diff = dc.groupby(["date", "product_id"])["uv"].transform("sum") - dc["uv_all"]
        first = ~dc.duplicated(["date", "product_id"])
        dc.loc[first, "uv"] -= diff[first]
        dc.drop(columns="uv_all").to_csv(out_dir / "daily_channel.csv", index=False)
    if "stock" in mapping:
        v = pd.DataFrame({"variant_id": prod["product_id"] + "V1", "product_id": prod["product_id"], "variant_name": "全部规格"})
        v.to_csv(out_dir / "variants.csv", index=False)
        dv = g[["date", "product_id", "units", "gmv"]].copy()
        st = df[[mapping["date"], mapping.get("product_id", mapping.get("product_name")), mapping["stock"]]].copy()
        st.columns = ["date", "product_id", "stock_units"]
        st["date"] = pd.to_datetime(st["date"]).dt.strftime("%Y-%m-%d")
        st["product_id"] = st["product_id"].astype(str)
        st["stock_units"] = _to_num(st["stock_units"]).fillna(0).astype(int)
        dv = dv.merge(st.groupby(["date", "product_id"], as_index=False)["stock_units"].sum(), on=["date", "product_id"], how="left")
        dv["variant_id"] = dv["product_id"] + "V1"
        dv[["date", "variant_id", "units", "gmv", "stock_units"]].to_csv(out_dir / "daily_variant.csv", index=False)
    missing_opt = {"stock": "库存（查不了库存和补货，诊断时无法判断断货）", "comp_price": "竞品到手价（看不了价差，诊断时无法判断价格劣势）",
                   "cost_price": "成本价（算不了毛利，价格测算需要用户提供成本价）", "price": "到手价（价格测算需要用户提供当前到手价）",
                   "rating": "评分（诊断时无法判断口碑问题）", "refund_amount": "退款金额",
                   "launch_date": "上架日期（用数据中首次出现日期代替）"}
    report.update(ok=True, out=str(out_dir), category=cat,
                  missing_optional=[v for k, v in missing_opt.items() if k not in mapping],
                  channel_breakdown=bool(ch_cols), variant_breakdown=False,
                  note="表里没有规格与渠道明细时，规格按「全部规格」处理，渠道拆分跳过")
    return report
