"""计算层单元测试：python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import actions, alerts, health, loader, tiering  # noqa: E402
from core.decompose import decompose_gmv, lmdi  # noqa: E402

DS = {n: loader.load(ROOT / "data" / "datasets" / n) for n in ("3c", "snacks")}


class TestDecompose(unittest.TestCase):
    def test_lmdi_sums_to_delta(self):
        v1 = dict(gmv=100 * 0.05 * 80, uv=100, cvr=0.05, aov=80)
        v0 = dict(gmv=120 * 0.06 * 75, uv=120, cvr=0.06, aov=75)
        c, m = lmdi(v1, v0, ["uv", "cvr", "aov"], "gmv")
        self.assertEqual(m, "LMDI")
        self.assertAlmostEqual(sum(c.values()), v1["gmv"] - v0["gmv"], places=6)

    def test_zero_falls_back(self):
        v1 = dict(gmv=0, uv=100, cvr=0.0, aov=0)
        v0 = dict(gmv=400, uv=100, cvr=0.05, aov=80)
        c, m = lmdi(v1, v0, ["uv", "cvr", "aov"], "gmv")
        self.assertEqual(m, "连环替代")
        self.assertAlmostEqual(sum(c.values()), -400, places=6)

    def test_all_products_error_below_half_percent(self):
        for ds in DS.values():
            for pid in ds.product_ids():
                d = decompose_gmv(ds, pid)
                if d["gmv_prev"]:
                    total = sum(f["amount"] for f in d["factors"])
                    self.assertLess(abs(total - d["gmv_change"]), max(abs(d["gmv_change"]) * 0.005, 1.0), pid)


class TestData(unittest.TestCase):
    def test_consistency(self):
        for ds in DS.values():
            self.assertEqual(loader.check_consistency(ds), [])

    def test_profiles_matched(self):
        self.assertTrue(DS["3c"].profile["matched"])
        self.assertEqual(DS["snacks"].profile["replenish_lead_days"], 5)
        self.assertEqual(DS["snacks"].profile["constraints"]["margin_floor"], 0.25)  # 继承通用默认


class TestHealth(unittest.TestCase):
    def test_bounds(self):
        for ds in DS.values():
            for pid in ds.product_ids():
                s = health.score(ds, pid)
                self.assertTrue(0 <= s["score"] <= 100)

    def test_stockout_low_score(self):
        self.assertLess(health.score(DS["3c"], "P01")["score"], 60)


class TestActions(unittest.TestCase):
    def test_coupon_math(self):
        pa = actions.plan_actions(DS["3c"], "P02", "price_disadvantage")
        c = next(p for p in pa["candidates"] if p["action_id"] == "store_coupon")
        self.assertEqual(c["params"]["coupon"], 20)
        self.assertEqual(c["params"]["new_price"], 269)
        self.assertEqual(c["estimate"]["unit_margin_before"], 139)
        self.assertEqual(c["estimate"]["unit_margin_after"], 119)
        self.assertAlmostEqual(c["estimate"]["breakeven_lift"], 139 / 119 - 1, places=3)
        self.assertEqual(c["exec_type"], "自己执行")

    def test_every_candidate_complete(self):
        for ds in DS.values():
            for pid in ds.product_ids():
                for cause in ["stockout", "stockout_risk", "price_disadvantage", "paid_traffic_drop",
                              "campaign_end", "reputation_drop", "growth_opportunity"]:
                    for p in actions.plan_actions(ds, pid, cause)["candidates"]:
                        self.assertEqual(actions.completeness(p), [], f"{pid} {p['action_id']}")

    def test_no_price_plan_without_disadvantage(self):
        pa = actions.plan_actions(DS["3c"], "P07", "price_disadvantage")
        self.assertEqual(pa["candidates"], [])
        self.assertTrue(pa["excluded"])

    def test_expiry_promo_needs_data(self):
        pa = actions.plan_actions(DS["snacks"], "S04", "expiring_stock")
        self.assertEqual(pa["candidates"], [])


class TestAlerts(unittest.TestCase):
    def test_new_product_not_alerted(self):
        cards = alerts.scan(DS["3c"])
        self.assertNotIn("P04", {c["product_id"] for c in cards})

    def test_tiers_have_focus(self):
        t = tiering.compute(DS["3c"])
        self.assertTrue(t["P01"]["focus"])
        self.assertFalse(t["P20"]["focus"])


if __name__ == "__main__":
    unittest.main()
