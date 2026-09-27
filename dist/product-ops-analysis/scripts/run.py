"""商品经营分析 Skill 的统一命令行入口。所有输出为 JSON（打印到标准输出）。

在你的工作目录里执行，脚本路径写本 Skill 目录下的 scripts/run.py；数据与产物都写在工作目录。

python scripts/run.py check                                                  环境自检（缺什么、怎么装）
python scripts/run.py adapt <文件或目录 ...> [--out workdata] [--category 品类] [--map 标准字段=原列名 ...]
python scripts/run.py settings [--set 毛利底线=0.3 ...] [--reset] [--data workdata]   查看 / 调整经营参数

问数与测算：
python scripts/run.py products [--keyword 关键词]                             商品列表（编号、名称、分层、近 7 天 GMV）
python scripts/run.py query --metrics gmv,cvr [--start --end | --days 7] [--compare prev|none] [--compare-start --compare-end]
                            [--by none|product|category|sub_category|tier|channel|variant|day|week] [--products 编号或名称,...]
                            [--category 品类] [--sort 指标] [--sort-by value|change|diff] [--order desc|asc] [--top N]
python scripts/run.py stock [--products ...] [--category ...]                库存、可售天数、断货与建议补货量
python scripts/run.py simulate --product 商品 [--coupon 券面额 | --new-price 新到手价] [--gift-cost 赠品成本]
                               [--price-now 当前到手价] [--cost 成本价]         价格类方案测算：毛利、保本线、约束检查

分析：
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
sys.dont_write_bytecode = True            # 不往 Skill 目录里写缓存文件（技能目录可能只读）
sys.path.insert(0, str(HERE))
for _s in (sys.stdout, sys.stderr):      # Windows 控制台默认 GBK，中文 JSON 会报错
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def check() -> dict:
    """环境自检：不依赖 pandas，缺少依赖时也能运行。"""
    import importlib
    import platform
    import tempfile
    items, fix = [], []
    ok_py = sys.version_info >= (3, 9)
    items.append(dict(name="Python", ok=ok_py, detail=platform.python_version() + ("" if ok_py else "（需要 3.9 或以上）")))
    for mod, need, why in (("pandas", True, "所有计算"), ("numpy", True, "所有计算"), ("openpyxl", False, "读取 Excel（.xlsx）文件")):
        try:
            m = importlib.import_module(mod)
            items.append(dict(name=mod, ok=True, detail=getattr(m, "__version__", "")))
        except ImportError:
            items.append(dict(name=mod, ok=not need, detail=f"未安装（用于{why}）"))
            fix.append(mod)
    files = [HERE / "core" / "loader.py", HERE / "config" / "metrics.json"]
    miss = [str(f.relative_to(HERE.parent)) for f in files if not f.exists()]
    items.append(dict(name="Skill 文件", ok=not miss, detail="完整" if not miss else "缺少 " + "、".join(miss)))
    try:
        with tempfile.NamedTemporaryFile(dir=".", prefix=".write_test_", delete=True):
            pass
        items.append(dict(name="工作目录可写", ok=True, detail=str(Path(".").resolve())))
    except Exception as e:  # noqa: BLE001
        items.append(dict(name="工作目录可写", ok=False, detail=f"{e}；请换到可写的目录执行"))
    ok = all(i["ok"] for i in items)
    need = [m for m in fix if m in ("pandas", "numpy")]
    return dict(ok=ok, items=items,
                fix=(f"{Path(sys.executable).name} -m pip install {' '.join(fix)}" if fix else None),
                note=("环境就绪" if ok and not fix else
                      "缺少必需依赖，请先运行 fix 里的命令安装，再重新自检" if need else
                      "可以运行；如果用户上传的是 Excel，需要先安装 openpyxl" if fix else "请按 detail 处理后重新自检"))


if len(sys.argv) > 1 and sys.argv[1] == "check":
    print(json.dumps(check(), ensure_ascii=False, indent=1))
    sys.exit(0)

try:
    import numpy as np  # noqa: E402
    import pandas as pd  # noqa: E402
except ImportError:
    print(json.dumps(dict(ok=False, error="缺少 pandas，请先运行：python -m pip install pandas numpy openpyxl，再重试"),
                     ensure_ascii=False))
    sys.exit(1)

from core import adapter, alerts, config, loader, sop, tiering, tools  # noqa: E402
from core import query as Q  # noqa: E402


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


LOG = None      # 每个命令的输出都记一份，verify / render 核对数字时可以引用这次对话里所有脚本算出的数


def out(obj):
    text = json.dumps(obj, ensure_ascii=False, indent=1, default=_default)
    if LOG is not None:      # 先记录再输出：输出被 head 等截断时，计算结果也已经记下，后面核对数字不受影响
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False, default=_default) + "\n")
        except Exception:  # noqa: BLE001
            pass
    try:
        print(text)
    except BrokenPipeError:
        pass


def logged_numbers(data) -> set:
    from core import verify_numbers as vn
    allowed = set()
    f = Path(data) / ".outputs.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                vn.collect(json.loads(line), allowed)
            except Exception:  # noqa: BLE001
                continue
    return allowed


def strip_names(ds, text: str) -> str:
    """商品名、规格名里的数字（20000mAh、65W、30 包）不是结论数字，核对前先去掉。"""
    names = set(ds.products["product_name"].astype(str))
    if not ds.dv.empty:
        names |= set(ds.dv["variant_name"].dropna().astype(str))
    for n in sorted(names, key=len, reverse=True):
        if any(ch.isdigit() for ch in n):
            text = text.replace(n, "〔商品〕")
    return text


# 经营参数：用户在对话里说一句就能改，存在数据目录的 settings.json 里，不改 Skill 文件
SETTINGS = {
    "毛利底线": ("constraints.margin_floor", "价格类方案执行后毛利率不得低于此值，如 0.25"),
    "调价权限": ("constraints.price_authority", "运营单次自主降价的最大幅度，如 0.10"),
    "最低价保护天数": ("constraints.min_price_window", "新到手价不得低于近 N 天最低成交价"),
    "安全库存天数": ("constraints.safety_days", "补货时额外预留的天数"),
    "补货周期": ("replenish_lead_days", "从下单补货到入库的天数"),
    "价差阈值": ("price_gap", "比竞品贵超过这个比例算价格劣势，如 0.05"),
    "利润品毛利率": ("margin_high", "商品分层时利润品的毛利率门槛，如 0.35"),
}


def _get(d, path):
    for k in path.split("."):
        d = d.get(k) if isinstance(d, dict) else None
    return d


def _settings_file(data) -> Path:
    return Path(data) / "settings.json"


def _apply_settings(ds, data):
    f = _settings_file(data)
    if not f.exists():
        return {}
    saved = json.loads(f.read_text(encoding="utf-8"))
    over = {}
    for name, v in saved.items():
        path = SETTINGS[name][0].split(".")
        cur = over
        for k in path[:-1]:
            cur = cur.setdefault(k, {})
        cur[path[-1]] = v
    ds.profile = config._deep_merge(ds.profile, over)
    return saved


def fail(error: str, **extra):
    print(json.dumps(dict(ok=False, error=error, **extra), ensure_ascii=False, indent=1))
    sys.exit(1)


def load(args):
    if not (Path(args.data) / "daily_product.csv").exists():
        fail(f"找不到数据目录 {args.data}，请先用 adapt 命令导入用户的数据")
    ds = loader.load(args.data, validate=False)
    _apply_settings(ds, args.data)
    return ds


def _standard_inputs(inputs, out_dir):
    """用户把标准格式的几张表（products.csv、daily_product.csv ...）逐个传进来时，归到一个目录里按标准格式导入。"""
    from core.loader import TABLES
    ps = [Path(x) for x in inputs]
    if len(ps) > 1 and all(p.is_file() and p.stem in TABLES for p in ps) and any(p.stem == "daily_product" for p in ps):
        tmp = Path(out_dir + "_src")
        tmp.mkdir(parents=True, exist_ok=True)
        import shutil
        for p in ps:
            shutil.copy(p, tmp / p.name)
        return [str(tmp)]
    return inputs


def scan_cards(ds, **kw) -> list[dict]:
    """预警卡：和平台一样带上严重程度的中文标签（红 / 黄 / 蓝）。"""
    cards = alerts.scan(ds, **kw)
    for c in cards:
        c.setdefault("severity_name", alerts.SEV_NAME.get(c["severity"]))
    return cards


def resolve_pid(ds, s: str) -> str:
    """商品可以写编号，也可以写名称（或名称里的关键词），必须唯一。"""
    if s in set(ds.product_ids()):
        return s
    p = ds.products
    hit = p[p["product_name"].astype(str).str.contains(str(s), regex=False)]
    if len(hit) == 1:
        return hit.iloc[0]["product_id"]
    if hit.empty:
        fail(f"找不到商品「{s}」，可用 products 命令查看商品列表")
    cands = [f"{x.product_id} {x.product_name}" for x in hit.itertuples()]
    fail(f"「{s}」匹配到多个商品，请确认是哪一个", candidates=cands)


def _split(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()] or None


def data_overview(ds) -> dict:
    """给开场引导用：数据覆盖范围、能做哪些分析、可用来举例的真实商品。"""
    first = ds.dp["date"].min()
    days = int((ds.as_of - first).days) + 1
    av = ds.available
    has_price_cost = {"price", "cost_price"} <= av
    camps = []
    try:
        from core.reports import campaign as RC
        camps = RC.list_campaigns(ds)
    except Exception:  # noqa: BLE001
        pass
    caps = [
        dict(scene="问数", ok=True, detail="任意时间段、商品、维度的指标，支持环比和排行"),
        dict(scene="单品诊断", ok=days >= 14, detail="需要至少 14 天数据" if days < 14 else "按 7 步归因，给出根因与可执行方案"),
        dict(scene="周报", ok=days >= 14, detail="需要至少 14 天数据" if days < 14 else ("近 8 周趋势完整" if days >= 56 else f"数据只有 {days} 天，趋势章节会短一些")),
        dict(scene="活动复盘", ok=bool(camps), detail=f"可复盘 {len(camps)} 个活动" if camps else "数据里没有活动记录或大促日"),
        dict(scene="价格方案测算", ok=has_price_cost, detail="已有到手价和成本价" if has_price_cost else "数据里缺到手价或成本价，测算时需要用户提供"),
        dict(scene="库存与补货", ok="stock" in av, detail="有库存数据" if "stock" in av else "数据里没有库存"),
        dict(scene="渠道拆分", ok="channel" in av, detail="有分渠道访客" if "channel" in av else "数据里没有分渠道访客"),
    ]
    q = Q.query(ds, ["gmv"], by="product", sort_by="value")
    top = [x["name"] for x in q["rows"][:3]]
    import datetime as _dt
    behind = (pd.Timestamp(_dt.date.today()) - ds.as_of).days
    movers = sorted([x for x in q["rows"] if x["values"]["gmv"].get("change") is not None],
                    key=lambda x: x["values"]["gmv"]["diff"])
    down = [dict(name=x["name"], change=x["values"]["gmv"]["change"]) for x in movers[:2] if x["values"]["gmv"]["diff"] < 0]
    up = [dict(name=x["name"], change=x["values"]["gmv"]["change"]) for x in movers[::-1][:2] if x["values"]["gmv"]["diff"] > 0]
    return dict(date_range=f"{first:%Y-%m-%d} 至 {ds.as_of:%Y-%m-%d}（{days} 天）", products=len(ds.product_ids()),
                category_profile=ds.profile.get("name") if ds.profile.get("matched") else "未匹配到专属品类，按通用消费品标准判断",
                data_behind_today_days=behind,
                data_note=(f"数据截至 {ds.as_of:%Y-%m-%d}，比今天早 {behind} 天：「这周」「上周」按数据最后一天来算，回答时要说明" if behind > 2 else None),
                capabilities=caps,
                example_products=dict(period=f"{q['period']['start']} 至 {q['period']['end']}，对比 {q['compare_period']['start']} 至 {q['compare_period']['end']}",
                                      top_gmv=top, falling_by_amount=down, rising_by_amount=up),
                business_settings={k: _get(ds.profile, v[0]) for k, v in SETTINGS.items()})


def main():
    ap = argparse.ArgumentParser(description="商品经营分析")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="环境自检")
    a = sub.add_parser("adapt")
    a.add_argument("inputs", nargs="+")
    a.add_argument("--out", default="workdata")
    a.add_argument("--category")
    a.add_argument("--map", nargs="*", default=[])
    se = sub.add_parser("settings")
    se.add_argument("--set", nargs="*", default=[])
    se.add_argument("--reset", action="store_true")
    se.add_argument("--data", default="workdata")
    pr = sub.add_parser("products")
    pr.add_argument("--keyword")
    pr.add_argument("--data", default="workdata")
    q = sub.add_parser("query")
    q.add_argument("--metrics", default="gmv")
    for k in ("start", "end", "compare-start", "compare-end", "products", "category", "sort"):
        q.add_argument("--" + k)
    q.add_argument("--days", type=int)
    q.add_argument("--period", choices=Q.PERIODS, help="常用时间：last_7d / last_30d / this_week / last_week / this_month / last_month")
    q.add_argument("--compare", default="prev", choices=["prev", "none"])
    q.add_argument("--by", default="none", choices=Q.BY)
    q.add_argument("--sort-by", default="value", choices=["value", "change", "diff"])
    q.add_argument("--order", default="desc", choices=["desc", "asc"])
    q.add_argument("--top", type=int)
    q.add_argument("--data", default="workdata")
    sk = sub.add_parser("stock")
    sk.add_argument("--products")
    sk.add_argument("--category")
    sk.add_argument("--data", default="workdata")
    sm = sub.add_parser("simulate")
    sm.add_argument("--product", required=True)
    for k in ("coupon", "new-price", "gift-cost", "price-now", "cost"):
        sm.add_argument("--" + k, type=float)
    sm.add_argument("--data", default="workdata")
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
    global LOG
    d = getattr(args, "data", None) or getattr(args, "out", None)
    if d and Path(d).is_dir():
        LOG = str(Path(d) / ".outputs.jsonl")

    if args.cmd == "adapt":
        ov = dict(x.split("=", 1) for x in args.map)
        inputs = _standard_inputs(args.inputs, args.out)
        try:
            rep = adapter.adapt(inputs, args.out, args.category, ov)
        except (ValueError, KeyError) as e:
            fail(f"无法导入数据：{e}", hint="标准格式的多张表（products.csv、daily_product.csv 等）可以直接传它们所在的目录")
        LOG = str(Path(args.out) / ".outputs.jsonl")
        Path(LOG).unlink(missing_ok=True)      # 新导入的数据，清掉上一份数据的记录
        if rep.get("ok", True):
            ds = loader.load(args.out, validate=False)
            rep["as_of"] = ds.as_of.strftime("%Y-%m-%d")
            rep["products"] = len(ds.product_ids())
            rep["category_profile"] = ds.profile.get("name") if ds.profile.get("matched") else "未匹配品类，使用通用默认配置"
            rep["consistency_errors"] = loader.check_consistency(ds)
            _apply_settings(ds, args.out)
            rep["overview"] = data_overview(ds)
        return out(rep)
    if args.cmd == "settings":
        return settings_cmd(args)
    if args.cmd == "render":
        return render_cmd(args)
    ds = load(args)
    try:
        return dispatch(ds, args)
    except Q.QueryError as e:
        return out(dict(ok=False, error=str(e)))


def dispatch(ds, args):
    for k in ("product",):
        if getattr(args, k, None) and args.cmd != "chart":
            args.product = resolve_pid(ds, args.product)
    if args.cmd == "products":
        return out(Q.list_products(ds, args.keyword))
    if args.cmd == "query":
        return out(Q.query(ds, metrics=_split(args.metrics), start=args.start, end=args.end, days=args.days,
                           compare=args.compare, compare_start=args.compare_start, compare_end=args.compare_end,
                           by=args.by, products=_split(args.products), category=args.category, sort=args.sort,
                           sort_by=args.sort_by, order=args.order, top=args.top, period_name=args.period))
    if args.cmd == "stock":
        return out(Q.stock_status(ds, _split(args.products), args.category))
    if args.cmd == "simulate":
        if not (args.coupon or args.new_price or args.gift_cost):
            return out(dict(ok=False, error="请给出券面额（--coupon）、新到手价（--new-price）或赠品成本（--gift-cost）"))
        from core.actions import evaluate_plan
        res = evaluate_plan(ds, args.product, coupon=args.coupon, new_price=args.new_price, gift_cost=args.gift_cost,
                            price_now=args.price_now, cost=args.cost)
        res["product"] = ds.product(args.product)["product_name"]
        res["rules"] = {k: _get(ds.profile, SETTINGS[k][0]) for k in ("毛利底线", "调价权限", "最低价保护天数")}
        res["note"] = "不预测销量提升；保本销量增幅 = 销量至少要涨多少，总毛利才不降。约束值可用 settings 命令按公司规则调整"
        return out(res)
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
        cards = scan_cards(ds, days=args.days)
        return out(dict(as_of=ds.as_of.strftime("%Y-%m-%d"), category_profile=ds.profile.get("name"),
                        profile_matched=ds.profile.get("matched"), cards=cards))
    if args.cmd == "tool":
        kw = dict(product_id=args.product)
        if args.name == "plan_actions":
            if not args.cause:
                fail("plan_actions 需要 --cause（根因代码）")
            kw["cause"] = args.cause
        if args.name in ("decompose_gmv", "breakdown_channels", "breakdown_variants", "check_factors", "plan_actions"):
            kw["window"] = args.window
        card = next((c for c in scan_cards(ds) if c["product_id"] == args.product and c["is_today"]), None) \
            if args.name == "get_context" else None
        return out(tools.call(ds, args.name, kw, card=card))
    if args.cmd == "diagnose":
        card = next((c for c in scan_cards(ds) if c["product_id"] == args.product and c["is_today"]), None)
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
            card = next((c for c in scan_cards(ds) if c["product_id"] == args.product and c["is_today"]), None)
            steps, res = sop.run(ds, args.product, card=card)
            for s in steps:
                vn.collect(s["result"], allowed)
        allowed |= logged_numbers(args.data)
        bad = sorted(set(vn.check_text(strip_names(ds, text), allowed)))
        return out(dict(unmatched_numbers=bad, ok=not bad,
                        note="这些数字在本次对话的计算结果里找不到：改为引用计算结果里的数，或者删掉" if bad
                        else "数字均可在计算结果中找到"))


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
PLAYBOOK = {"weekly": "references/playbooks/weekly_review.md", "campaign": "references/playbooks/campaign_review.md",
            "product": "references/playbooks/product_diagnosis.md"}


def report_cmd(ds, args):
    from core import charts as C
    from core.reports import campaign as RC, product as RP, weekly as RW
    from core.reports.common import default, for_llm
    cards = scan_cards(ds)
    mod = {"weekly": RW, "campaign": RC, "product": RP}[args.scene]
    if args.scene == "weekly":
        pack, book = RW.build(ds, cards, [], None, week_end=args.week_end, target=args.target)
    elif args.scene == "campaign":
        camps = RC.list_campaigns(ds)
        cid = args.campaign or (camps[0]["id"] if camps else None)
        if not cid:
            fail("数据里没有可复盘的活动（需要事件表里的活动开始 / 结束，或全店大促日）")
        pack, book = RC.build(ds, cid)
    else:
        if not args.product:
            fail("单品诊断需要 --product")
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
    allowed |= logged_numbers(args.data)
    try:
        ds = loader.load(args.data, validate=False)
        check = strip_names(ds, text)
    except Exception:  # noqa: BLE001
        check = text
    bad = sorted(set(vn.check_text(check, allowed)))
    total = len(vn.NUM_RE.findall(text))
    Path(args.mdfile).write_text(text, encoding="utf-8")
    report_html.write(args.out, pack["title"], text, charts, bad, total)
    return out(dict(html=args.out, numbers=total, unmatched_numbers=bad, ok=not bad,
                    note="未核对到的数字需要改为引用数据包或图表工具的结果" if bad else "数字均可在数据包和图表中找到"))


def settings_cmd(args):
    f = _settings_file(args.data)
    saved = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    if args.reset:
        saved = {}
    for kv in args.set:
        if "=" not in kv:
            return out(dict(ok=False, error=f"格式应为 参数名=值：{kv}"))
        k, v = kv.split("=", 1)
        k = k.strip()
        if k not in SETTINGS:
            return out(dict(ok=False, error=f"不支持的参数「{k}」", available=list(SETTINGS)))
        v = v.strip().rstrip("%")
        try:
            num = float(v)
        except ValueError:
            return out(dict(ok=False, error=f"{k} 的值应为数字：{v}"))
        if kv.strip().endswith("%") or (num > 1 and k in ("毛利底线", "调价权限", "价差阈值", "利润品毛利率")):
            num = num / 100
        saved[k] = int(num) if k in ("最低价保护天数", "安全库存天数", "补货周期") else num
    if args.set or args.reset:
        Path(args.data).mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    base = config.profile(loader.load(args.data, validate=False).category) if (Path(args.data) / "daily_product.csv").exists() \
        else config.profile(None)
    rows = []
    for k, (path, desc) in SETTINGS.items():
        rows.append(dict(name=k, value=saved.get(k, _get(base, path)), default=_get(base, path), changed=k in saved, meaning=desc))
    return out(dict(ok=True, settings=rows, note="修改只对这份数据生效，存在数据目录的 settings.json 里；不改动 Skill 文件"))


if __name__ == "__main__":
    main()
