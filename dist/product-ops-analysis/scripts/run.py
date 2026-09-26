"""商品经营分析 Skill 的统一命令行入口。所有输出为 JSON（打印到标准输出）。

python scripts/run.py adapt <文件或目录> [--out workdata] [--category 品类] [--map 标准字段=原列名 ...]
python scripts/run.py tier        [--data workdata]
python scripts/run.py scan        [--data workdata] [--days 14]
python scripts/run.py tool <工具名> --product <商品编号> [--cause 根因] [--window 7] [--data workdata]
python scripts/run.py diagnose    --product <商品编号> [--data workdata]      按 SOP 一次跑完全部工具，输出证据包
python scripts/run.py weekly-pack [--data workdata]
python scripts/run.py verify <文本文件> [--product 商品编号 | --weekly] [--data workdata]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core import adapter, alerts, loader, sop, tiering, tools, weekly  # noqa: E402


def _default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        v = float(o)
        return None if math.isnan(v) else v
    if isinstance(o, pd.Timestamp):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=_default))


def load(args):
    return loader.load(args.data, validate=False)


def main():
    ap = argparse.ArgumentParser(description="商品经营分析")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("adapt")
    a.add_argument("inputs", nargs="+")
    a.add_argument("--out", default="workdata")
    a.add_argument("--category")
    a.add_argument("--map", nargs="*", default=[])
    for n in ("tier", "scan", "weekly-pack"):
        p = sub.add_parser(n)
        p.add_argument("--data", default="workdata")
        p.add_argument("--days", type=int, default=14)
    t = sub.add_parser("tool")
    t.add_argument("name", choices=list(tools.FUNCS))
    t.add_argument("--product", required=True)
    t.add_argument("--cause")
    t.add_argument("--window", type=int, default=7)
    t.add_argument("--data", default="workdata")
    d = sub.add_parser("diagnose")
    d.add_argument("--product", required=True)
    d.add_argument("--data", default="workdata")
    v = sub.add_parser("verify")
    v.add_argument("textfile")
    v.add_argument("--product")
    v.add_argument("--weekly", action="store_true")
    v.add_argument("--data", default="workdata")
    args = ap.parse_args()

    if args.cmd == "adapt":
        ov = dict(x.split("=", 1) for x in args.map)
        rep = adapter.adapt(args.inputs, args.out, args.category, ov)
        if rep.get("ok", True):
            ds = loader.load(args.out, validate=False)
            rep["as_of"] = ds.as_of.strftime("%Y-%m-%d")
            rep["products"] = len(ds.product_ids())
            rep["category_profile"] = ds.profile.get("name") if ds.profile.get("matched") else "未匹配品类，使用通用默认配置"
            rep["consistency_errors"] = loader.check_consistency(ds)
        return out(rep)
    ds = load(args)
    if args.cmd == "tier":
        t = tiering.compute(ds)
        return out([dict(product_id=k, product_name=ds.product(k)["product_name"], **v) for k, v in t.items()])
    if args.cmd == "scan":
        cards = alerts.scan(ds, days=args.days)
        return out(dict(as_of=ds.as_of.strftime("%Y-%m-%d"), category_profile=ds.profile.get("name"),
                        profile_matched=ds.profile.get("matched"), cards=cards))
    if args.cmd == "tool":
        kw = dict(product_id=args.product)
        if args.name == "plan_actions":
            if not args.cause:
                raise SystemExit("plan_actions 需要 --cause")
            kw["cause"] = args.cause
        if args.name in ("decompose_gmv", "breakdown_channels", "breakdown_variants", "check_factors", "plan_actions"):
            kw["window"] = args.window
        card = next((c for c in alerts.scan(ds) if c["product_id"] == args.product and c["is_today"]), None) \
            if args.name == "get_context" else None
        return out(tools.call(ds, args.name, kw, card=card))
    if args.cmd == "diagnose":
        card = next((c for c in alerts.scan(ds) if c["product_id"] == args.product and c["is_today"]), None)
        steps, res = sop.run(ds, args.product, card=card, tiers=tiering.compute(ds))
        return out(dict(note="以下为按 SOP 计算的证据与规则初判；请据此撰写结论，数字只能引用这里的结果，方案只能从 plans 中选择",
                        card=card, steps=[dict(tool=s["tool"], summary=s["summary"], result=s["result"]) for s in steps],
                        rule_based_result=res))
    if args.cmd == "weekly-pack":
        cards = alerts.scan(ds)
        diags = {}
        for c in cards:
            if c["is_today"]:
                diags[c["product_id"]] = sop.run(ds, c["product_id"], card=c)[1]
        return out(weekly.weekly_pack(ds, cards, [], diags))
    if args.cmd == "verify":
        text = Path(args.textfile).read_text(encoding="utf-8")
        allowed = set()
        from core import verify_numbers as vn
        if args.weekly:
            vn.collect(weekly.weekly_pack(ds, alerts.scan(ds), []), allowed)
        if args.product:
            card = next((c for c in alerts.scan(ds) if c["product_id"] == args.product and c["is_today"]), None)
            steps, res = sop.run(ds, args.product, card=card)
            for s in steps:
                vn.collect(s["result"], allowed)
        bad = sorted(set(vn.check_text(text, allowed)))
        return out(dict(unmatched_numbers=bad, ok=not bad,
                        note="未核对到的数字需要删除或改为引用工具结果" if bad else "数字均可在计算结果中找到"))


if __name__ == "__main__":
    main()
