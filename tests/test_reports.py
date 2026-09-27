"""报告中心（v11）：图表工具、三个场景的验收案例（与分析剧本里的「验收案例」一致）、生成流程。

python -m unittest tests.test_reports
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DB", str(Path(tempfile.mkdtemp()) / "state.db"))
os.environ["LLM_MODE"] = "mock"
os.environ["REMINDERS"] = "off"

from core import alerts, charts as C, loader, report_html  # noqa: E402
from core.reports import campaign as RC, product as RP, weekly as RW  # noqa: E402
from core.reports.common import finalize  # noqa: E402

DS = {n: loader.load(ROOT / "data" / "datasets" / n) for n in ("3c", "snacks")}
CARDS = {n: alerts.scan(ds) for n, ds in DS.items()}


def chap(pack, key):
    return next(c for c in pack["chapters"] if c["key"] == key)


class TestChartTool(unittest.TestCase):
    def test_draw_returns_data_from_platform(self):
        ds = DS["snacks"]
        spec = C.draw(ds, dict(type="dual_line", products=["S01"], metrics=["rating", "refund_rate"],
                               mark_date="2026-09-12", mark_text="差评集中", title="差评后评分下滑"))
        self.assertNotIn("error", spec)
        self.assertEqual(len(spec["axes"]), 2)             # 单位不同 → 左右两个坐标轴
        self.assertEqual(spec["marks"][0]["x"], "09-12")
        self.assertIn("09-12 之前均值", spec["summary"])
        self.assertTrue(spec["values"])

    def test_bad_params_are_rejected_with_hint(self):
        ds = DS["snacks"]
        for a, hint in [(dict(type="pie", products=["S01"]), "不在图表库"),
                        (dict(type="trend", products=["S99"], metrics=["gmv"]), "可选商品"),
                        (dict(type="trend", products=["S01"], metrics=["foo"]), "指标字典"),
                        (dict(type="dual_line", products=["ALL"], metrics=["rating", "gmv"]), "1 个商品"),
                        (dict(type="trend", products=["S01"], metrics=["gmv"], start="2030-01-01"), "超出数据范围")]:
            r = C.draw(ds, a)
            self.assertIn("error", r, a)
            self.assertIn(hint, r["error"])

    def test_ai_cannot_pass_numbers(self):
        spec = C.draw(DS["3c"], dict(type="kpi", products=["P02"], metrics=["gmv"], values=[1, 2, 3], title="x"))
        self.assertNotIn("values", spec["params"])            # 多余参数被忽略，数据只来自平台
        self.assertGreater(spec["items"][0]["value"], 1000)

    def test_extra_chart_limit(self):
        book = C.ChartBook(extra_limit=1)
        ok = book.draw_extra(DS["3c"], dict(type="trend", products=["P02"], metrics=["cvr"], title="a"))
        self.assertIn("chart_id", ok)
        again = book.draw_extra(DS["3c"], dict(type="trend", products=["P02"], metrics=["cvr"], title="b"))
        self.assertIn("上限", again["error"])

    def test_waterfall_sums(self):
        spec = C.draw(DS["snacks"], dict(type="waterfall", products=["ALL"], by="factor", start="2026-09-14", end="2026-09-20"))
        total = spec["start"]["value"] + sum(i["value"] for i in spec["items"])
        self.assertAlmostEqual(total, spec["end"]["value"], delta=3)


class TestWeekly(unittest.TestCase):
    def test_snacks_acceptance(self):
        pack, book = RW.build(DS["snacks"], CARDS["snacks"])
        f = chap(pack, "summary")["facts"]
        self.assertAlmostEqual(f["change_pct"], -0.133, delta=0.002)
        items = {i["product_id"]: i for i in chap(pack, "sources")["facts"]["items"]}
        self.assertEqual(items["S02"]["label"], "预期内")
        self.assertAlmostEqual(items["S02"]["share_of_net"], 0.70, delta=0.01)
        self.assertEqual(items["S01"]["label"], "需处理")
        self.assertIn("口碑", items["S01"]["reason"])
        aov = chap(pack, "factors")["facts"]["aov_detail"]
        self.assertGreater(abs(aov["mix_effect"]), abs(aov["price_effect"]))   # 客单价下降来自商品组合变化
        nexts = chap(pack, "next")["facts"]["items"]
        self.assertIn("挂耳咖啡", nexts[0]["title"])
        self.assertEqual(len([c for c in book.charts.values() if c["origin"] == "required"]), 5)

    def test_3c_acceptance(self):
        pack, _ = RW.build(DS["3c"], CARDS["3c"])
        labels = {i["product_id"]: i["label"] for i in chap(pack, "sources")["facts"]["items"]}
        self.assertEqual(labels["P01"], "需处理")
        self.assertEqual(labels["P02"], "需处理")
        self.assertEqual(labels["P03"], "主动调整")
        self.assertEqual(labels["P06"], "机会")
        self.assertIn("预警", chap(pack, "summary")["facts"]["verdict"])

    def test_target_progress(self):
        pack, _ = RW.build(DS["snacks"], CARDS["snacks"], target=6_500_000)
        t = chap(pack, "trend")["facts"]["target"]
        self.assertAlmostEqual(t["time_progress"], 20 / 30, places=3)
        self.assertGreater(t["forecast"], 0)


class TestCampaign(unittest.TestCase):
    def test_s02_acceptance(self):
        ds = DS["snacks"]
        camps = RC.list_campaigns(ds)
        self.assertEqual(camps[0]["id"], "S02-20260825")
        pack, book = RC.build(ds, "S02-20260825")
        f = chap(pack, "summary")["facts"]
        self.assertTrue(f["worth"])
        self.assertAlmostEqual(f["multiple"], 2.2, delta=0.05)
        self.assertAlmostEqual(f["attrib_share"], 0.70, delta=0.02)
        self.assertIn("流量型", chap(pack, "sources")["facts"]["style"])
        self.assertEqual(chap(pack, "others")["facts"]["verdict"], "没有挤占，也没有带动")
        st = chap(pack, "stock")["facts"]
        self.assertEqual(st["stockout_days"], 0)
        self.assertGreater(st["leftover_after"], 0)
        self.assertTrue(any("中秋" in x for x in chap(pack, "lessons")["facts"]["improve"]))
        self.assertTrue(pack["actions"])                      # 节后库存 → 可转待办

    def test_store_promo_day(self):
        pack, _ = RC.build(DS["3c"], "promo-20260808")
        self.assertFalse(chap(pack, "others")["show"])       # 全店大促没有对照商品
        self.assertIsNotNone(chap(pack, "post")["facts"]["vs_pre"])


class TestProduct(unittest.TestCase):
    def test_p02_price(self):
        card = next(c for c in CARDS["3c"] if c["product_id"] == "P02" and c["is_today"])
        pack, book = RP.build(DS["3c"], "P02", card=card)
        f = chap(pack, "summary")["facts"]
        self.assertEqual(f["main_cause"], "价格劣势")
        self.assertEqual(f["confidence"], "强")
        ex = chap(pack, "excluded")["facts"]["items"]
        self.assertTrue(any("不是流量问题" in x for x in ex))
        self.assertTrue(any("不是口碑问题" in x for x in ex))
        cause_chart = book.charts[chap(pack, "cause")["chart"]]
        self.assertEqual(cause_chart["type"], "dual_line")
        self.assertEqual(len(chap(pack, "plans")["facts"]["plans"]), 3)

    def test_s01_reputation(self):
        card = next(c for c in CARDS["snacks"] if c["product_id"] == "S01" and c["is_today"])
        pack, _ = RP.build(DS["snacks"], "S01", card=card)
        self.assertEqual(chap(pack, "summary")["facts"]["main_cause"], "口碑下滑")
        self.assertTrue(any("不是价格问题" in x for x in chap(pack, "excluded")["facts"]["items"]))


class TestAlertsExpected(unittest.TestCase):
    def test_campaign_end_is_expected(self):
        s02 = next(c for c in CARDS["snacks"] if c["product_id"] == "S02" and c["is_today"])
        self.assertIsNotNone(s02["expected"])
        self.assertEqual(s02["severity"], "blue")
        s01 = next(c for c in CARDS["snacks"] if c["product_id"] == "S01" and c["is_today"])
        self.assertIsNone(s01["expected"])


class TestFinalizeAndHtml(unittest.TestCase):
    def test_missing_required_chart_is_inserted(self):
        pack, book = RW.build(DS["snacks"], CARDS["snacks"])
        text = RW.render(pack, book).replace("[图表:c4]", "") + "\n\n[图表:c99]"
        out, charts = finalize(text, pack, book)
        self.assertIn("[图表:c4]", out)
        self.assertNotIn("c99", out)
        self.assertEqual(set(charts), set(book.charts))

    def test_html_export(self):
        pack, book = RC.build(DS["snacks"], "S02-20260825")
        text, charts = finalize(RC.render(pack, book), pack, book)
        with tempfile.TemporaryDirectory() as d:
            p = report_html.write(Path(d) / "r.html", pack["title"], text, charts, [], 10)
            html = Path(p).read_text(encoding="utf-8")
        self.assertIn("function toOption", html)
        self.assertIn("rc-kpi", html)
        self.assertNotIn("export function", html)


class TestGenerate(unittest.TestCase):
    def setUp(self):
        self._mode = os.environ.get("LLM_MODE")
        os.environ["LLM_MODE"] = "mock"

    def tearDown(self):
        os.environ["LLM_MODE"] = self._mode or ""

    def test_generate_all_scenes_mock(self):
        from server import state
        from server.agent import report
        for name, scene, params in [("snacks", "weekly", {}), ("snacks", "campaign", {}), ("3c", "product", {"product_id": "P02"}),
                                    ("3c", "campaign", {"campaign_id": "promo-20260808"})]:
            evs = list(report.run(name, scene, params))
            res = evs[-1]
            self.assertEqual(res["type"], "result")
            self.assertEqual(res["unmatched_numbers"], [], (name, scene))
            r = state.get_report(res["id"])
            charts = json.loads(r["charts_json"])
            for cid in charts:
                self.assertIn(f"[图表:{cid}]", r["content"])
            if scene == "weekly":
                self.assertTrue(any(c["origin"] == "extra" for c in charts.values()))   # 模拟 AI 调用了图表工具
                self.assertTrue(any(e.get("tool") == "draw_chart" for e in evs))


    def test_live_path_with_tool_calls(self):
        """模拟真实模型：先传错参数被退回，再补一张图，最后写正文（漏写的必备图由平台补上）。"""
        from server import llm_client, state
        from server.agent import diagnose, report
        calls = []

        def fake_chat(msgs, tools=None, **kw):
            calls.append(msgs[-1])
            n = len(calls)
            if n == 1:
                return {"content": None, "tool_calls": [{"id": "a", "function": {"name": "draw_chart", "arguments": json.dumps(
                    {"type": "trend", "products": ["S99"], "metrics": ["gmv"], "title": "x"})}}]}
            if n == 2:
                assert "不存在" in msgs[-1]["content"]
                return {"content": None, "tool_calls": [{"id": "b", "function": {"name": "draw_chart", "arguments": json.dumps(
                    {"type": "dual_line", "products": ["S01"], "metrics": ["rating", "refund_rate"], "mark_date": "2026-09-12",
                     "title": "差评后评分下滑、退款率翻倍"})}}]}
            cid = json.loads(msgs[-1]["content"])["chart_id"]
            return {"content": f"# 报告\n\n## 一、本周结论\n\n**本周整体下滑。**\n\n[图表:c1]\n\n## 四、变化从哪来\n\n**挂耳咖啡需处理。**\n\n[图表:{cid}]\n"}

        old_mode, old_backend = llm_client.mode, diagnose.backend
        llm_client.mode = lambda: "live"
        diagnose.backend = lambda name: (fake_chat, None)
        try:
            evs = list(report.run("snacks", "weekly", {}))
        finally:
            llm_client.mode, diagnose.backend = old_mode, old_backend
        steps = [e["summary"] for e in evs if e.get("tool") == "draw_chart"]
        self.assertTrue(any("已退回" in x for x in steps))
        self.assertTrue(any("AI 补充图" in x for x in steps))
        r = state.get_report(evs[-1]["id"])
        charts = json.loads(r["charts_json"])
        self.assertEqual(r["source"], "llm")
        self.assertIn("[图表:c4]", r["content"])        # 模型漏写的必备图被补上
        self.assertEqual(sum(c["origin"] == "extra" for c in charts.values()), 1)


if __name__ == "__main__":
    unittest.main()
