"""报告数据包的公共部分：章节、数字格式、给 AI 的精简数据包。"""
from __future__ import annotations

import math

import pandas as pd

from .. import charts as C

CN = "一二三四五六七八九十"


def chapter(no: int, key: str, title: str, question: str, facts: dict | None = None, chart: str | None = None,
            judgment: str | None = None, show: bool = True, notes: list | None = None) -> dict:
    return dict(no=no, key=key, heading=f"{CN[no - 1]}、{title}", title=title, question=question, facts=facts or {},
                chart=chart, judgment=judgment, show=show, notes=notes or [])


def renumber(chapters: list[dict]) -> list[dict]:
    """没有数据、不显示的章节不占编号：显示的章节按顺序连续编号（一、二、三……）。"""
    i = 0
    for c in chapters:
        if c["show"]:
            c["heading"] = f"{CN[i]}、{c['title']}"
            i += 1
    return chapters


def r(x, n=4):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, n)


def money(v) -> str:
    """金额：1 万以上写「x.xx 万元」，否则写「x,xxx 元」。"""
    if v is None:
        return "—"
    return f"{v / 10000:.2f} 万元" if abs(v) >= 10000 else f"{v:,.0f} 元"


def money_signed(v) -> str:
    if v is None:
        return "—"
    s = "+" if v >= 0 else "-"
    return s + money(abs(v))


def pct(v, signed=True, n=1) -> str:
    if v is None:
        return "—"
    return f"{v:+.{n}%}" if signed else f"{v:.{n}%}"


def rate(v) -> str:
    """比率类指标（转化率、退款率、毛利率）：两位小数的百分数。"""
    return "—" if v is None else f"{v:.2%}"


def num(v) -> str:
    return "—" if v is None else f"{v:,.0f}"


def price(v) -> str:
    return "—" if v is None else (f"{v:,.0f} 元" if abs(v - round(v)) < 1e-6 else f"{v:,.2f} 元")


def mmdd(d) -> str:
    return pd.Timestamp(d).strftime("%m-%d")


def md(d) -> str:
    """9/12 这样的写法。"""
    d = pd.Timestamp(d)
    return f"{d.month}/{d.day}"


def ymd(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def events_between(ds, pid, a, b, types=None, include_store=True) -> list[dict]:
    ev = ds.events
    if ev.empty:
        return []
    x = ev[(ev["date"] >= pd.Timestamp(a)) & (ev["date"] <= pd.Timestamp(b))]
    pidcol = x["product_id"].fillna("")
    x = x[(pidcol == pid) | ((pidcol == "") & include_store)] if pid else x
    if types:
        x = x[x["event_type"].isin(types)]
    return [dict(date=ymd(e["date"]), type=e["event_type"], description=e["description"],
                 value=None if pd.isna(e["value"]) else float(e["value"]), product_id=e["product_id"] or None)
            for _, e in x.sort_values("date").iterrows()]


def for_llm(pack: dict, book: C.ChartBook) -> dict:
    """给 AI 的数据包：章节（数据、判断、必备图摘要）+ 建议动作 + 数据说明。"""
    chs = []
    for c in pack["chapters"]:
        if not c["show"]:
            continue
        x = dict(heading=c["heading"], question=c["question"], facts=c["facts"], judgment=c["judgment"], notes=c["notes"])
        if c["chart"]:
            ch = book.charts[c["chart"]]
            x["chart"] = dict(id=c["chart"], type=C.TYPES[ch["type"]], title=ch["title"], summary=ch.get("summary"))
        chs.append(x)
    return dict(title=pack["title"], period=pack.get("period"), subject=pack.get("subject"), chapters=chs,
                actions=[{k: v for k, v in a.items() if k not in ("plan",)} for a in pack.get("actions", [])],
                data_notes=pack.get("data_notes", []))


def default(o):
    import numpy as np
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        v = float(o)
        return None if math.isnan(v) else v
    if isinstance(o, (pd.Timestamp,)):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def insert_after_chapter_chart(text, pack, key, cid) -> str:
    ch = next((c for c in pack["chapters"] if c["key"] == key), None)
    if not ch:
        return text + f"\n\n[图表:{cid}]\n"
    anchor = f"[图表:{ch['chart']}]" if ch["chart"] else None
    if anchor and anchor in text:
        return text.replace(anchor, anchor + f"\n\n[图表:{cid}]", 1)
    return insert_in_chapter(text, ch, f"[图表:{cid}]")


def insert_in_chapter(text, ch, token) -> str:
    """把占位符插到章节标题后第一段之后。"""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and ch["title"] in ln:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            while j < len(lines) and lines[j].strip() and not lines[j].startswith("#"):
                j += 1
            lines[j:j] = ["", token]
            return "\n".join(lines)
    return text


def finalize(text, pack, book) -> tuple[str, dict]:
    """补上遗漏的必备图、去掉不存在的图表编号、只保留正文里用到的补充图。"""
    text = C.PLACEHOLDER.sub(lambda m: f"[图表:{m.group(1)}]", text)
    for ch in pack["chapters"]:
        if ch["show"] and ch["chart"] and f"[图表:{ch['chart']}]" not in text:
            text = insert_in_chapter(text, ch, f"[图表:{ch['chart']}]")
    used = set(C.PLACEHOLDER.findall(text))
    text = C.PLACEHOLDER.sub(lambda m: m.group(0) if m.group(1) in book.charts else "", text)
    charts = {k: v for k, v in book.public().items() if k in used or v["origin"] == "required"}
    return text, charts
