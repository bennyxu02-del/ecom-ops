"""经营总览 v13：一句话结论、目标进度、变化拆解、拉动 / 拖累商品、待办提醒、节前库存检查。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DB", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ["REMINDERS"] = "off"

from fastapi.testclient import TestClient  # noqa: E402

from server import app as A, data, overview, state  # noqa: E402
from server.agent.report import target_key  # noqa: E402

C = TestClient(A.app)


class Overview(unittest.TestCase):
    def setUp(self):
        state.reset(data.seed)
        state.x("DELETE FROM settings WHERE key=?", (target_key("3c", "2026-09"),))

    def test_focus_vs_all(self):
        f = C.get("/api/overview?ds=3c&scope=focus").json()
        a = C.get("/api/overview?ds=3c&scope=all").json()
        self.assertEqual((f["scope_name"], a["scope_name"]), ("重点商品", "全店"))
        self.assertLess(f["kpi"]["gmv"]["cur"], a["kpi"]["gmv"]["cur"])
        self.assertEqual(len(a["tier_trend"]), 4)                              # 全店含长尾品
        for s in a["tier_trend"]:
            self.assertEqual(len(s["values"]), len(a["dates"]))                # 按日期对齐
        self.assertNotIn("health", f)
        self.assertEqual(C.get("/api/overview?ds=3c&scope=x").status_code, 422)

    def test_headline_and_factors(self):
        o = overview.build("3c")
        self.assertIn("重点商品 GMV 138.08 万元", o["headline"])
        self.assertIn("-3.3%", o["headline"])
        self.assertIn("支付转化率", o["headline"])                              # 影响最大的因子
        self.assertIn("20000mAh 快充充电宝（-5.41 万元，需处理）", o["headline"])
        self.assertIn("磁吸无线充电宝（+5.87 万元，机会）", o["headline"])
        total = sum(f["amount"] for f in o["factors"])
        self.assertAlmostEqual(total, o["kpi"]["gmv"]["cur"] - o["kpi"]["gmv"]["prev"], delta=5)
        self.assertEqual(o["waterfall"]["type"], "waterfall")

    def test_movers_labels(self):
        o = overview.build("3c")
        down = {x["product_id"]: x for x in o["movers"]["down"]}
        self.assertEqual(down["P01"]["label"], "需处理")
        self.assertEqual(down["P03"]["label"], "主动调整")
        self.assertEqual(down["P01"]["alert"]["group"], "pending")
        self.assertEqual(o["movers"]["up"][0]["label"], "机会")
        self.assertTrue(all(x["change"] < 0 for x in o["movers"]["down"]))

    def test_target_progress(self):
        self.assertIsNone(overview.build("3c")["target"]["target"])
        C.put("/api/reports/target?ds=3c", json={"month": "2026-09", "target": 6500000})
        t = overview.build("3c")["target"]
        self.assertEqual(t["target"], 6500000)
        self.assertAlmostEqual(t["time_progress"], 20 / 30, places=3)
        self.assertAlmostEqual(t["forecast"], t["mtd"] / 20 * 30, delta=1)
        self.assertEqual(t["on_track"], t["forecast"] >= 6500000)

    def test_todos_and_upcoming(self):
        o = overview.build("3c")
        self.assertEqual(o["todos"]["review"], 1)                               # 预置的数据线补货待办
        up = {e["name"]: e for e in o["upcoming"]}
        self.assertEqual(up["中秋节"]["days"], 5)
        p01 = next(x for x in up["中秋节"]["short"] if x["product_id"] == "P01")
        self.assertEqual(p01["transit"], "在途补货 9/22 到")                     # 断货但节前有货到
        self.assertEqual(up["中秋节"]["short_total"], 0)
        p06 = next(x for x in up["国庆节"]["short"] if x["product_id"] == "P06")
        self.assertEqual(p06["status"], "来得及补货")

    def test_pending_alerts_listed(self):
        o = overview.build("3c")
        ids = [x["id"] for x in o["alerts"]["items"]]
        self.assertEqual(ids[0], "3c-P01-0909")
        self.assertEqual(o["alerts"]["pending"], 3)
        self.assertEqual(o["alerts"]["opportunity"], 1)


if __name__ == "__main__":
    unittest.main()
