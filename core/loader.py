"""加载标准数据模型并做一致性校验。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import config

TABLES = ["products", "variants", "daily_product", "daily_variant", "daily_channel", "events"]
REQUIRED = {"daily_product": ["date", "product_id", "uv", "buyers", "gmv"], "products": ["product_id"]}


class DataError(Exception):
    pass


@dataclass
class Dataset:
    name: str
    products: pd.DataFrame
    variants: pd.DataFrame
    dp: pd.DataFrame          # daily_product
    dv: pd.DataFrame          # daily_variant（含 product_id、variant_name）
    dc: pd.DataFrame          # daily_channel
    events: pd.DataFrame
    as_of: pd.Timestamp
    category: str
    profile: dict
    available: set            # 可用数据：stock / channel / price / comp_price / cost_price / rating / events / variant

    # ------------------------------------------------------------------
    def product(self, pid: str) -> dict:
        r = self.products[self.products["product_id"] == pid]
        if r.empty:
            raise KeyError(f"未知商品 {pid}")
        return r.iloc[0].to_dict()

    def product_ids(self) -> list[str]:
        return list(self.products["product_id"])

    def pdays(self, pid: str) -> pd.DataFrame:
        return self._pday_cache[pid]

    def promo_days(self) -> set:
        ev = self.events
        if ev.empty:
            return set()
        return set(ev.loc[ev["event_type"] == "promo_day", "date"])

    def build_cache(self):
        self._pday_cache = {pid: g.set_index("date").sort_index()
                            for pid, g in self.dp.groupby("product_id")}
        for pid in self.product_ids():
            self._pday_cache.setdefault(pid, self.dp.iloc[0:0].set_index("date"))
        self._vday_cache = {pid: g for pid, g in self.dv.groupby("product_id")} if not self.dv.empty else {}
        self._cday_cache = {pid: g for pid, g in self.dc.groupby("product_id")} if not self.dc.empty else {}

    def vdays(self, pid: str) -> pd.DataFrame:
        return self._vday_cache.get(pid, self.dv.iloc[0:0])

    def cdays(self, pid: str) -> pd.DataFrame:
        return self._cday_cache.get(pid, self.dc.iloc[0:0])


def _read(folder: Path, name: str) -> pd.DataFrame | None:
    p = folder / f"{name}.csv"
    if not p.exists():
        return None
    return pd.read_csv(p, dtype={"product_id": str, "variant_id": str})


def load(folder: str | Path, name: str | None = None, validate: bool = True) -> Dataset:
    folder = Path(folder)
    t = {n: _read(folder, n) for n in TABLES}
    if t["daily_product"] is None:
        raise DataError(f"{folder} 缺少 daily_product.csv")
    dp = t["daily_product"]
    for c in REQUIRED["daily_product"]:
        if c not in dp.columns:
            raise DataError(f"daily_product 缺少必填字段 {c}")
    dp["date"] = pd.to_datetime(dp["date"])
    if "units" not in dp.columns:
        dp["units"] = dp["buyers"]

    products = t["products"]
    if products is None:
        products = pd.DataFrame({"product_id": sorted(dp["product_id"].unique())})
    for col, default in [("product_name", None), ("category", ""), ("sub_category", ""),
                         ("launch_date", None), ("cost_price", None), ("list_price", None)]:
        if col not in products.columns:
            products[col] = default
    products["product_name"] = products["product_name"].fillna(products["product_id"])
    for col in ("category", "sub_category"):
        products[col] = products[col].fillna("")
    first_day = dp.groupby("product_id")["date"].min()
    products["launch_date"] = pd.to_datetime(products["launch_date"]).fillna(
        products["product_id"].map(first_day))

    variants = t["variants"] if t["variants"] is not None else pd.DataFrame(
        columns=["variant_id", "product_id", "variant_name"])
    dv = t["daily_variant"]
    if dv is not None and not dv.empty:
        dv["date"] = pd.to_datetime(dv["date"])
        dv = dv.merge(variants[["variant_id", "product_id", "variant_name"]], on="variant_id", how="left")
    else:
        dv = pd.DataFrame(columns=["date", "variant_id", "units", "gmv", "stock_units", "product_id", "variant_name"])
    dc = t["daily_channel"]
    if dc is not None and not dc.empty:
        dc["date"] = pd.to_datetime(dc["date"])
    else:
        dc = pd.DataFrame(columns=["date", "product_id", "channel", "uv"])
    ev = t["events"]
    if ev is not None and not ev.empty:
        ev["date"] = pd.to_datetime(ev["date"])
        ev["product_id"] = ev["product_id"].fillna("")
        ev["description"] = ev["description"].fillna("")
    else:
        ev = pd.DataFrame(columns=["date", "product_id", "event_type", "description", "value"])

    available = {"variant"} if not dv.empty else set()
    if not dv.empty and "stock_units" in dv.columns and dv["stock_units"].notna().any():
        available.add("stock")
    if not dc.empty:
        available.add("channel")
    for c in ["price", "comp_price", "rating", "refund_amount"]:
        if c in dp.columns and dp[c].notna().any():
            available.add(c)
    if products["cost_price"].notna().any():
        available.add("cost_price")
    if not ev.empty:
        available.add("events")

    cats = products["category"].dropna()
    category = str(cats.mode().iloc[0]) if not cats.empty else ""
    ds = Dataset(name=name or folder.name, products=products, variants=variants, dp=dp, dv=dv, dc=dc,
                 events=ev, as_of=dp["date"].max(), category=category,
                 profile=config.profile(category), available=available)
    ds.build_cache()
    if validate:
        errs = check_consistency(ds)
        if errs:
            raise DataError("数据一致性校验未通过：\n" + "\n".join(errs[:20]))
    return ds


def check_consistency(ds: Dataset) -> list[str]:
    errs = []
    dp = ds.dp
    bad = dp[dp["buyers"] > dp["uv"]]
    if len(bad):
        errs.append(f"{len(bad)} 行支付买家数大于访客数")
    bad = dp[dp["units"] < dp["buyers"]]
    if len(bad):
        errs.append(f"{len(bad)} 行支付件数小于买家数")
    if "channel" in ds.available:
        s = ds.dc.groupby(["product_id", "date"])["uv"].sum()
        j = dp.set_index(["product_id", "date"])["uv"].to_frame("uv").join(s.rename("ch"), how="inner")
        bad = j[j["uv"] != j["ch"]]
        if len(bad):
            errs.append(f"{len(bad)} 行渠道访客之和不等于访客数")
    if "variant" in ds.available:
        s = ds.dv.groupby(["product_id", "date"])[["units", "gmv"]].sum()
        j = dp.set_index(["product_id", "date"])[["units", "gmv"]].join(s, rsuffix="_v", how="inner")
        bad = j[(j["units"] != j["units_v"]) | ((j["gmv"] - j["gmv_v"]).abs() > 1.0)]
        if len(bad):
            errs.append(f"{len(bad)} 行规格销量或金额之和不等于商品值")
        if "stock" in ds.available:
            if (ds.dv["stock_units"] < 0).any():
                errs.append("存在负库存")
    return errs
