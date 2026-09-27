"""指标计算：窗口汇总。所有比率先对分子分母求和再相除。"""
from __future__ import annotations

import math

import pandas as pd

DAY = pd.Timedelta(days=1)


def windows(as_of: pd.Timestamp, days: int = 7):
    cur = (as_of - (days - 1) * DAY, as_of)
    prev = (as_of - (2 * days - 1) * DAY, as_of - days * DAY)
    return cur, prev


def fmt_window(w) -> str:
    return f"{w[0]:%m-%d}–{w[1]:%m-%d}"


def slice_(df: pd.DataFrame, w) -> pd.DataFrame:
    """df 以 date 为索引。"""
    return df.loc[(df.index >= w[0]) & (df.index <= w[1])]


def safe_div(a, b):
    return float(a) / float(b) if b else 0.0


def agg(df: pd.DataFrame) -> dict:
    """df：某商品（或多商品）日数据，列含 uv/buyers/units/gmv。"""
    if df is None or df.empty:
        return dict(days=0, gmv=0.0, uv=0, buyers=0, units=0, cvr=0.0, aov=0.0, unit_price=0.0,
                    units_per_buyer=0.0, refund_rate=0.0)
    gmv = float(df["gmv"].sum())
    uv = int(df["uv"].sum())
    buyers = int(df["buyers"].sum())
    units = int(df["units"].sum())
    refund = float(df["refund_amount"].sum()) if "refund_amount" in df.columns else 0.0
    return dict(days=int(df.index.nunique()) if isinstance(df.index, pd.DatetimeIndex) else len(df),
                gmv=round(gmv, 2), uv=uv, buyers=buyers, units=units,
                cvr=safe_div(buyers, uv), aov=safe_div(gmv, buyers), unit_price=safe_div(gmv, units),
                units_per_buyer=safe_div(units, buyers), refund_rate=safe_div(refund, gmv))


def pct(a: float, b: float) -> float | None:
    if not b:
        return None
    return a / b - 1


def r(x, n=4):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), n)
