"""商品经营分析 Skill 的统一命令行入口。所有输出为 JSON（打印到标准输出）。

python scripts/run.py adapt <文件或目录> [--out workdata] [--category 品类] [--map 标准字段=原列名 ...]
python scripts/run.py tier        [--data workdata]
python scripts/run.py scan        [--data workdata] [--days 14]
python scripts/run.py tool <工具名> --product <商品编号> [--cause 根因] [--window 7] [--data workdata]
python scripts/run.py diagnose    --product <商品编号> [--data workdata]      按 SOP 一次跑完全部工具，输出证据包
python scripts/run.py weekly-pack [--data workdata]
python scripts/run.py verify <文本文件> [--product 商品编号] [--data workdata]

报告（三个场景，分析剧本见 references/playbooks/）：
python scripts/run.py campaigns   [--data workdata]                                   列出可复盘的活动
python scripts/run.py report --scene weekly|campaign|product [--product 商品编号] [--campaign 活动编号]
                             [--week-end YYYY-MM-DD] [--target 月度目标元] [--pack report_pack.json] [--data workdata]
python scripts/run.py chart  --pack report_pack.json --type 图表类型 --products S01 [--metrics rating,refund_rate] [--by ...]
                             [--start --end --compare-start --compare-end --mark-date --mark-text --band-start --band-end --band-text] --title 标题
python scripts/run.py render <报告.md> --pack report_pack.json [--out report.html]      核对数字并导出带图表的 HTML
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

from core import adapter, alerts, loader, sop, tiering, tools  # noqa: E402


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
    c = sub.add_parser("campaigns")
    c.add_argument("--data", default="workdata")
    rp = sub.add_parser("report")
    rp.add_argument("--scene", required=True, choices=["weekly", "campaign", "product"])
    rp.add_argument("--product")
    rp.add_argument("--campaign")
    rp.add_argument("--week-end")
    rp.add_argument("--target", type=float)
    rp.add_argument("--pack", default="report_pack.json")
    rp.add_argument("--draft", help="同时写出规则版初稿（仅供参考）到这个文件")
    rp.add_argument("--data", default="workdata")
    ch = sub.add_parser("chart")
    ch.add_argument("--pack", help="报告数据包文件；不传时只输出图表配置")
    ch.add_argument("--type", required=True)
    ch.add_argument("--products", required=True, help="逗号分隔；全部商品写 ALL")
    ch.add_argument("--metrics", default="")
    for k in ("by", "start", "end", "compare-start", "compare-end", "mark-date", "mark-text", "band-start", "band-end", "band-text", "title"):
        ch.add_argument("--" + k)
    ch.add_argument("--data", default="workdata")
    rd = sub.add_parser("render")
    rd.add_argument("mdfile")
    rd.add_argument("--pack", required=True)
    rd.add_argument("--out", default="report.html")
    rd.add_argument("--data", default="workdata")
    v = sub.add_parser("verify")
    v.add_argument("textfile")
    v.add_argument("--product")
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
    if args.cmd == "render":
        return render_cmd(args)
    ds = load(args)
    if args.cmd == "campaigns":
        from core.reports import campaign as RC
        return out(RC.list_campaigns(ds))
    if args.cmd == "report":
        return report_cmd(ds, args)
    if args.cmd == "chart":
        return chart_cmd(ds, args)
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
        args.scene, args.product, args.campaign, args.week_end, args.target, args.draft = "weekly", None, None, None, None, None
        args.pack = "report_pack.json"
        return report_cmd(ds, args)
    if args.cmd == "verify":
        text = Path(args.textfile).read_text(encoding="utf-8")
        allowed = set()
        from core import verify_numbers as vn
        if args.product:
            card = next((c for c in alerts.scan(ds) if c["product_id"] == args.product and c["is_today"]), None)
            steps, res = sop.run(ds, args.product, card=card)
            for s in steps:
                vn.collect(s["result"], allowed)
        bad = sorted(set(vn.check_text(text, allowed)))
        return out(dict(unmatched_numbers=bad, ok=not bad,
                        note="未核对到的数字需要删除或改为引用工具结果" if bad else "数字均可在计算结果中找到"))


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
PLAYBOOK = {"weekly": "references/playbooks/weekly_review.md", "campaign": "references/playbooks/campaign_review.md",
            "product": "references/playbooks/product_diagnosis.md"}


def report_cmd(ds, args):
    from core import charts as C
    from core.reports import campaign as RC, product as RP, weekly as RW
    from core.reports.common import default, for_llm
    cards = alerts.scan(ds)
    mod = {"weekly": RW, "campaign": RC, "product": RP}[args.scene]
    if args.scene == "weekly":
        pack, book = RW.build(ds, cards, [], None, week_end=args.week_end, target=args.target)
    elif args.scene == "campaign":
        camps = RC.list_campaigns(ds)
        cid = args.campaign or (camps[0]["id"] if camps else None)
        if not cid:
            raise SystemExit("数据里没有可复盘的活动（需要事件表里的活动开始 / 结束，或全店大促日）")
        pack, book = RC.build(ds, cid)
    else:
        if not args.product:
            raise SystemExit("单品诊断需要 --product")
        card = next((c for c in cards if c["product_id"] == args.product and c["is_today"]), None)
        pack, book = RP.build(ds, args.product, card=card)
    pack.pop("diagnosis", None)
    saved = dict(scene=args.scene, pack=pack, book=book.dump())
    Path(args.pack).write_text(json.dumps(saved, ensure_ascii=False, default=default), encoding="utf-8")
    if args.draft:
        Path(args.draft).write_text(mod.render(pack, book), encoding="utf-8")
    return out(dict(note="按分析剧本写报告：章节标题与顺序照数据包，必备图写 [图表:编号]，补充图用 chart 命令（最多 3 张），写完用 render 命令核对数字并导出 HTML",
                    playbook=PLAYBOOK[args.scene], writing_rules="references/playbooks/writing_rules.md", pack_file=args.pack,
                    report=for_llm(pack, book)))


def _book(path):
    from core import charts as C
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    return saved, C.ChartBook.load(saved["book"])


def chart_cmd(ds, args):
    from core import charts as C
    from core.reports.common import default
    a = dict(type=args.type, products=[x.strip() for x in args.products.split(",") if x.strip()],
             metrics=[x.strip() for x in (args.metrics or "").split(",") if x.strip()])
    for k in ("by", "start", "end", "compare_start", "compare_end", "mark_date", "mark_text", "band_start", "band_end", "band_text", "title"):
        v = getattr(args, k)
        if v:
            a[k] = v
    if not args.pack:
        return out(C.draw(ds, a))
    saved, book = _book(args.pack)
    res = book.draw_extra(ds, a)
    saved["book"] = book.dump()
    Path(args.pack).write_text(json.dumps(saved, ensure_ascii=False, default=default), encoding="utf-8")
    return out(res)


def render_cmd(args):
    from core import report_html, verify_numbers as vn
    from core.reports.common import finalize
    saved, book = _book(args.pack)
    pack = saved["pack"]
    text = Path(args.mdfile).read_text(encoding="utf-8")
    text, charts = finalize(text, pack, book)
    allowed = set()
    vn.collect(pack, allowed)
    for v in book.values():
        vn.collect(v, allowed)
    bad = sorted(set(vn.check_text(text, allowed)))
    total = len(vn.NUM_RE.findall(text))
    Path(args.mdfile).write_text(text, encoding="utf-8")
    report_html.write(args.out, pack["title"], text, charts, bad, total)
    return out(dict(html=args.out, numbers=total, unmatched_numbers=bad, ok=not bad,
                    note="未核对到的数字需要改为引用数据包或图表工具的结果" if bad else "数字均可在数据包和图表中找到"))


if __name__ == "__main__":
    main()
