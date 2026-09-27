"""校验模拟数据中的演示案例是否按需求文档第 13 章触发预期结果。

用法：python scripts/check_cases.py        全部通过时退出码为 0
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import alerts, loader, sop, tiering  # noqa: E402

EXPECT = {
    "3c": {
        "P01": dict(tier="爆品", sev="red", rules={"R04", "R01"}, cause="stockout",
                    plans={"replenish", "redirect_variant"}, plan_name="确认在途补货到货时间"),
        "P02": dict(tier="利润品", sev="yellow", rules={"R06", "R03"}, cause="price_disadvantage",
                    plans={"store_coupon", "gift"}, exec_type={"store_coupon": "自己执行"}),
        "P03": dict(tier="潜力品", lifecycle="成长期", sev="yellow", rules={"R01"}, cause="paid_traffic_drop",
                    plans={"review_ad_budget"}),
        "P04": dict(tier="潜力品", lifecycle="新品期", no_alert=True),
        "P05": dict(tier="爆品", recovered=True),
        "P06": dict(tier="利润品", sev="yellow", rules={"R05", "R08"}, cause="growth_opportunity",
                    plans={"boost_stock"}),
    },
    "snacks": {
        "S01": dict(tier="爆品", sev="red", rules={"R07", "R03"}, cause="reputation_drop", plans={"review_handling"}),
        "S02": dict(tier="爆品", sev="blue", rules={"R01"}, cause="campaign_end", plans={"mark_known"}, expected=True),
        "S04": dict(tier="利润品", sev="yellow", rules={"R05"}, cause="stockout_risk", plans={"replenish"}),
    },
}


def main() -> int:
    fails, passes = [], 0

    def ok(cond, msg):
        nonlocal passes
        if cond:
            passes += 1
        else:
            fails.append(msg)

    for name, cases in EXPECT.items():
        ds = loader.load(ROOT / "data" / "datasets" / name)
        tiers = tiering.compute(ds)
        cards = alerts.scan(ds)
        today = {c["product_id"]: c for c in cards if c["is_today"]}
        allc = {c["product_id"]: c for c in cards}
        # 案例以外不应出现今日红黄预警
        for pid, c in today.items():
            if pid not in cases and c["severity"] in ("red", "yellow"):
                fails.append(f"[{name}] 非案例商品 {pid} 出现今日{c['severity']}预警 {c['rules']}")
        for pid, e in cases.items():
            t = tiers[pid]
            ok(t["tier_name"] == e["tier"], f"[{name}] {pid} 分层应为 {e['tier']}，实际 {t['tier_name']}")
            if "lifecycle" in e:
                ok(t["lifecycle_name"] == e["lifecycle"], f"[{name}] {pid} 生命周期应为 {e['lifecycle']}，实际 {t['lifecycle_name']}")
            if e.get("no_alert"):
                ok(pid not in allc, f"[{name}] {pid} 不应触发预警")
                continue
            if e.get("recovered"):
                c = allc.get(pid)
                ok(c is not None and c["auto_status"] == "recovered", f"[{name}] {pid} 应有已恢复的历史问题卡")
                continue
            c = today.get(pid)
            ok(c is not None, f"[{name}] {pid} 应有今日问题卡")
            if not c:
                continue
            ok(c["severity"] == e["sev"], f"[{name}] {pid} 严重度应为 {e['sev']}，实际 {c['severity']}")
            ok(bool(c.get("expected")) == bool(e.get("expected")), f"[{name}] {pid} 预期内标记应为 {bool(e.get('expected'))}")
            ok(e["rules"] <= set(c["rules"]), f"[{name}] {pid} 规则应包含 {e['rules']}，实际 {c['rules']}")
            _, res = sop.run(ds, pid, card=c, tiers=tiers)
            causes = [x["cause"] for x in res["root_causes"]]
            ok(causes and causes[0] == e["cause"], f"[{name}] {pid} 首要根因应为 {e['cause']}，实际 {causes}")
            ids = {p["action_id"] for p in res["plans"]}
            ok(e["plans"] <= ids, f"[{name}] {pid} 方案应包含 {e['plans']}，实际 {ids}")
            if "plan_name" in e:
                ok(any(p["name"] == e["plan_name"] for p in res["plans"]), f"[{name}] {pid} 应有方案「{e['plan_name']}」")
            for aid, et in (e.get("exec_type") or {}).items():
                p = next((p for p in res["plans"] if p["action_id"] == aid), None)
                ok(p is not None and p["exec_type"] == et, f"[{name}] {pid} {aid} 执行类型应为 {et}")
    print(f"通过 {passes} 项，失败 {len(fails)} 项")
    for f in fails:
        print("  ✗", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
