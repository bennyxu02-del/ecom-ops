"""按口径取数、库存查询、方案测算（Skill 用）的测试。"""
import unittest
from pathlib import Path

from core import loader, query as Q
from core.actions import evaluate_plan

ROOT = Path(__file__).resolve().parents[1]


class QueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = loader.load(ROOT / "data" / "datasets" / "3c")

    def test_total_equals_sum_of_products(self):
        q = Q.query(self.ds, ["gmv", "uv", "buyers"], by="product")
        for m in ("gmv", "uv", "buyers"):
            s = sum(r["values"][m]["cur"] for r in q["rows"])
            self.assertAlmostEqual(s, q["total"]["values"][m]["cur"], places=1)

    def test_cvr_is_ratio_of_sums(self):
        q = Q.query(self.ds, ["cvr", "uv", "buyers"])
        v = q["rows"][0]["values"]
        self.assertAlmostEqual(v["cvr"]["cur"], v["buyers"]["cur"] / v["uv"]["cur"], places=4)

    def test_default_period_and_compare(self):
        q = Q.query(self.ds, ["gmv"])
        self.assertEqual(q["period"]["end"], f"{self.ds.as_of:%Y-%m-%d}")
        self.assertEqual(q["period"]["days"], 7)
        self.assertEqual(q["compare_period"]["days"], 7)

    def test_top_and_sort(self):
        q = Q.query(self.ds, ["gmv"], by="product", sort_by="diff", order="asc", top=3)
        diffs = [r["values"]["gmv"]["diff"] for r in q["rows"]]
        self.assertEqual(len(diffs), 3)
        self.assertEqual(diffs, sorted(diffs))

    def test_channel_only_uv(self):
        with self.assertRaises(Q.QueryError):
            Q.query(self.ds, ["gmv"], by="channel")
        q = Q.query(self.ds, ["uv"], by="channel", products=["P01"])
        self.assertAlmostEqual(sum(r["values"]["uv"]["share"] for r in q["rows"]), 1.0, places=2)

    def test_product_name_match(self):
        self.assertEqual(Q.match_products(self.ds, ["快充充电宝"]), ["P01"])
        with self.assertRaises(Q.QueryError):
            Q.match_products(self.ds, ["不存在的商品"])

    def test_stock_flags_stockout_with_transit(self):
        s = Q.stock_status(self.ds)
        oos = [v for v in s["variants"] if v["status"] == "断货"]
        self.assertTrue(oos)
        self.assertEqual(s["variants"][0]["status"], "断货")

    def test_evaluate_plan_unchanged_and_without_comp(self):
        full = evaluate_plan(self.ds, "P01", coupon=10)
        self.assertTrue(full["ok"])
        self.assertIsNotNone(full["gap_after"])
        ds = loader.load(ROOT / "data" / "datasets" / "3c")
        ds.available.discard("comp_price")
        ds.dp["comp_price"] = None
        ds.build_cache()
        res = evaluate_plan(ds, "P01", coupon=10)
        self.assertTrue(res["ok"])
        self.assertIsNone(res["gap_after"])
        self.assertEqual(res["estimate"], full["estimate"])


if __name__ == "__main__":
    unittest.main()
