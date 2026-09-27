"""数字后置校验：AI 输出中的数字必须能在工具结果中找到。"""
from __future__ import annotations

import re

NUM_RE = re.compile(r"(?<![\w.])([-+]?\d[\d,]*(?:\.\d+)?)\s*(%|万)?")


def collect(obj, out: set):
    if obj is None or isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
        return
    if isinstance(obj, str):
        for m in NUM_RE.finditer(obj):
            try:
                v = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            out.add(v)
            if m.group(2) == "%":
                out.add(v / 100)
            if m.group(2) == "万":
                out.add(v * 10000)
        return
    if isinstance(obj, dict):
        for v in obj.values():
            collect(v, out)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            collect(v, out)


def _match(n: float, decimals: int, pct: bool, allowed: list[float]) -> bool:
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    for a in allowed:
        cands = (a * 100, abs(a * 100)) if pct else (a, abs(a))
        for c in cands:
            if abs(c - n) <= tol or abs(abs(c) - abs(n)) <= tol:
                return True
            if pct is False and abs(c) >= 1000 and abs(c - n) / max(abs(c), 1) < 0.005:
                return True
    return False


def check_text(text: str, allowed_set: set) -> list[str]:
    allowed = list(allowed_set)
    bad = []
    for m in NUM_RE.finditer(text or ""):
        raw, unit = m.group(1), m.group(2)
        try:
            n = float(raw.replace(",", ""))
        except ValueError:
            continue
        dec = len(raw.split(".")[1]) if "." in raw else 0
        if unit == "万":
            n *= 10000
            tol = 0.5 * 10 ** (-dec) * 10000 + 1e-6       # 15.7 万 允许 ±500，8.88 万 只允许 ±50
            if _match(n, 0, False, allowed) or any(abs(abs(a) - abs(n)) <= tol for a in allowed):
                continue
            bad.append(raw + unit)
            continue
        pct = unit == "%" or text[m.end():m.end() + 6].lstrip().startswith("个百分点")
        if not pct and dec == 0 and abs(n) <= 31:
            continue            # 日期、天数、条数等小整数不校验
        if not pct and n in (2025, 2026):
            continue
        if _match(n, dec, pct, allowed):
            continue
        bad.append(raw + (unit or ""))
    return bad


def texts_of_result(res: dict) -> list[str]:
    t = [res.get("summary", "")]
    for c in res.get("root_causes", []):
        t += [e.get("text", "") for e in c.get("evidence", [])]
    for p in res.get("plans", []):
        t.append(p.get("rationale", ""))
    t += res.get("limitations", [])
    return t
