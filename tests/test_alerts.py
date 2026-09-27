"""预警中心 v12：状态闭环、恢复判断、在途、降噪、规则设置、自定义预警、推送去重、对话调方案（模拟模型）。"""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STATE_DB", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ["REMINDERS"] = "off"
os.environ["PREDIAGNOSE"] = "off"
os.environ.setdefault("FEISHU_APP_ID", "cli_test")
os.environ.setdefault("FEISHU_APP_SECRET", "x")
os.environ.setdefault("PUBLIC_BASE_URL", "http://demo.local/")

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import actions as ca  # noqa: E402
from core import alerts, custom_alerts  # noqa: E402
from server import alert_flow as F, alert_push, data, feishu, state, todos  # noqa: E402
from server import app as A  # noqa: E402
from server.agent import alert_chat  # noqa: E402

CARDS = []
C = TestClient(A.app)
_SAVED = {}
P01, P02, P03, P05, P06 = "3c-P01-0909", "3c-P02-0913", "3c-P03-0918", "3c-P05-0907", "3c-P06-0918"


def fresh():
    for name in data.DATASETS:
        scan = any(state.get_setting(f"{k}__{name}") for k in ("alert_config", "custom_alerts"))
        for k in ("alert_config", "custom_alerts", "alert_push", "alert_pushed", "alert_push_log", "alert_push_day"):
            state.x("DELETE FROM settings WHERE key=?", (f"{k}__{name}",))
        if scan:
            data.rescan(name)
    state.x("DELETE FROM ai_cache WHERE key LIKE 'alertchat__%'")
    state.reset(data.seed)
    _SAVED.update(token=feishu.token, send_card=feishu.send_card, roles=state.get_setting("feishu_roles"),
                  llm=os.environ.get("LLM_MODE"))
    state.set_setting("feishu_roles", {"我": dict(name="我", open_id="ou_me")})
    feishu.token = lambda: "t"
    feishu.send_card = lambda oid, card: CARDS.append(card) or f"om_{len(CARDS)}"
    CARDS.clear()


def restore():
    """其他测试模块共用同一个进程：还原飞书桩、角色绑定、模型模式和预警设置。"""
    for name in data.DATASETS:
        if any(state.get_setting(f"{k}__{name}") for k in ("alert_config", "custom_alerts")):
            state.x("DELETE FROM settings WHERE key IN (?, ?)", (f"alert_config__{name}", f"custom_alerts__{name}"))
            data.rescan(name)
    feishu.token, feishu.send_card = _SAVED["token"], _SAVED["send_card"]
    state.set_setting("feishu_roles", _SAVED["roles"] or {})
    if _SAVED["llm"] is None:
        os.environ.pop("LLM_MODE", None)
    else:
        os.environ["LLM_MODE"] = _SAVED["llm"]


class Base(unittest.TestCase):
    def setUp(self):
        fresh()

    def tearDown(self):
        restore()


def card(cid, name="3c"):
    return next(c for c in data.cards(name, include_suppressed=True) if c["id"] == cid)


def mk_todo(cid, pid):
    return todos.create("3c", pid, title="测试方案", steps=[{"text": "做一件事", "by": "我"}], source="alert", card_id=cid)


def finish(aid, outcome):
    state.update_action(aid, status="review")
    todos.review("3c", aid, outcome)


class Flow(Base):
    def setUp(self):
        fresh()
        os.environ["LLM_MODE"] = "off"

    def test_initial_groups(self):
        g = {c["id"]: c["group"] for c in data.cards("3c")}
        self.assertEqual(g[P01], "pending")
        self.assertEqual(g[P06], "opportunity")          # 增长机会 + 断货风险 → 机会
        self.assertEqual(g[P05], "doing")                # 演示预置的补货待办
        self.assertEqual(card(P01)["status_name"], "待决定")

    def test_known_ignore_watch(self):
        c = F.decide("3c", P01, "known", "补货已安排，9/22 到货")
        self.assertEqual((c["status"], c["decision"], c["group"]), ("closed", "known", "finished"))
        with self.assertRaises(ValueError):
            F.decide("3c", P01, "ignore", "正常波动")    # 关闭后不能再决定
        with self.assertRaises(ValueError):
            F.decide("3c", P02, "ignore", "")            # 忽略要原因
        with self.assertRaises(ValueError):
            F.decide("3c", P02, "watch", days=5)          # 只能 1 / 3 / 7 天
        c = F.decide("3c", P02, "watch", days=3)
        self.assertEqual((c["status"], c["watch_until"]), ("watch", "2026-09-23"))
        self.assertIn("先观察 3 天", c["log"][-1]["text"])

    def test_todo_effective_resolves(self):
        self.assertEqual(card(P05)["status"], "doing")
        a = state.list_actions("3c", "P05")[0]
        finish(a["id"], "effective")
        c = card(P05)
        self.assertEqual(c["status"], "resolved")
        self.assertIn("复盘有效", c["log"][-1]["text"])

    def test_todo_ineffective_and_cancel_reopen(self):
        aid = mk_todo(P02, "P02")
        self.assertEqual(card(P02)["status"], "doing")
        finish(aid, "ineffective")
        c = card(P02)
        self.assertEqual((c["status"], c["reopen_name"]), ("pending", "上次方案无效"))
        self.assertEqual(c["flags"]["failed_plan"], "测试方案")
        self.assertEqual(c["signature"], f"{c['severity']}|1")         # 回到待决定 → 会重新推送
        aid2 = mk_todo(P02, "P02")
        todos.cancel("3c", aid2, "不做了")
        c = card(P02)
        self.assertEqual((c["status"], c["reopen_name"]), ("pending", "待办已取消"))
        self.assertEqual(len(c["todos"]), 2)

    def test_todo_unknown_depends_on_metric(self):
        aid = mk_todo(P02, "P02")
        finish(aid, "unknown")
        self.assertEqual(card(P02)["status"], "pending")               # 今天还在触发，指标没回来

    def test_watch_expired(self):
        F.decide("3c", P03, "watch", days=1)
        state.upsert_card("3c", P03, watch_until="2026-09-20")          # 模拟到期
        c = card(P03)
        self.assertEqual((c["status"], c["reopen_name"]), ("pending", "观察到期"))

    def test_escalation_reopens_closed(self):
        F.decide("3c", P02, "ignore", "正常波动")
        self.assertEqual(card(P02)["status"], "closed")
        state.upsert_card("3c", P02, severity="blue")                   # 模拟关闭时是蓝色，现在升级为黄色
        c = card(P02)
        self.assertEqual((c["status"], c["reopen_name"]), ("pending", "严重度升级"))

    def test_silence_after_ignore(self):
        F.decide("3c", P03, "ignore", "阈值太严")
        rows = state.card_states("3c")
        new = dict(card(P03), id="3c-P03-0920")
        self.assertIsNotNone(F.silenced_by("3c", new, rows))            # 同商品同规则 7 天内静默
        self.assertIsNone(F.silenced_by("3c", dict(new, severity="red"), rows))   # 升级不静默
        self.assertIsNone(F.silenced_by("3c", dict(new, rules=["R06"]), rows))    # 不同规则不静默

    def test_expected_auto_closed(self):
        c = next(c for c in data.cards("snacks") if c.get("expected"))
        self.assertEqual((c["status"], c["decision"], c["auto_decided"]), ("closed", "known", True))

    def test_api_decide_and_detail(self):
        d = C.get(f"/api/alerts/{P02}?ds=3c").json()
        self.assertEqual(d["plan"]["action_id"], "store_coupon")
        self.assertTrue(d["diagnosis"]["excluded"])
        self.assertEqual(d["chart"]["type"], "dual_line")
        self.assertIn("少卖", d["facts"]["headline"])
        r = C.post(f"/api/alerts/{P02}/decide?ds=3c", json={"type": "ignore", "reason": "正常波动"}).json()
        self.assertEqual(r["card"]["status"], "closed")
        self.assertEqual(C.post(f"/api/alerts/{P01}/decide?ds=3c", json={"type": "known"}).status_code, 400)
        m = C.get("/api/alerts/meta?ds=3c").json()
        self.assertEqual(m["counts"]["finished"], 1)
        self.assertEqual(C.get("/api/overview?ds=3c").json()["alerts"]["pending"], m["counts"]["pending"])


class Compute(Base):
    def test_in_transit(self):
        c = card(P01)
        self.assertEqual(c["in_transit"][0]["date"], "2026-09-22")
        r04 = next(h for h in c["latest"] if h["rule"] == "R04")
        self.assertIn("9/22 到货", r04["text"])

    def test_back_to_base_plateau(self):
        ds = data.ds_of("3c")
        ok, why = alerts.back_to_base(ds, "P02", pd.Timestamp("2026-09-13"), {"R01", "R03"}, ds.as_of)
        self.assertFalse(ok)
        self.assertIn("仍比出问题前低", why)

    def test_noise_floor_and_rule_switch(self):
        r = C.put("/api/alerts/settings?ds=3c", json={"params": {"alert.min_impact": 30000}, "disabled": ["R06"]}).json()
        self.assertIn("影响金额下限：3,000 元 → 30,000 元", r["log"][0]["changes"])
        ids = {c["id"] for c in data.cards("3c")}
        self.assertNotIn(P03, ids)                                       # 只有 GMV 下滑、影响 2 万 < 3 万 → 不出卡
        self.assertIn(P03, {c["id"] for c in data.cards("3c", include_suppressed=True)})
        p02 = next(c for c in data.cards("3c") if c["product_id"] == "P02")
        self.assertNotIn("R06", p02["rules"])
        self.assertEqual(C.put("/api/alerts/settings?ds=3c", json={"params": {"price_gap": 5}}).status_code, 400)
        C.put("/api/alerts/settings?ds=3c", json={"params": {"alert.min_impact": 3000}, "disabled": []})
        self.assertIn(P03, {c["id"] for c in data.cards("3c")})

    def test_state_follows_card_after_rescan(self):
        aid = mk_todo(P02, "P02")
        C.put("/api/alerts/settings?ds=3c", json={"disabled": ["R06"]})   # 首次触发日变化 → 预警编号变化
        c = next(c for c in data.cards("3c") if c["product_id"] == "P02")
        self.assertNotEqual(c["id"], P02)
        self.assertEqual((c["status"], c["todo"]["id"]), ("doing", aid))
        C.put("/api/alerts/settings?ds=3c", json={"disabled": []})
        self.assertEqual(card(P02)["status"], "doing")

    def test_tail_only_stock_rules(self):
        ds = data.ds_of("3c")
        t = data.tiers("3c")
        tail = [p for p, v in t.items() if v["tier"] == "tail"]
        for pid in tail:
            for h in alerts.evaluate(ds, pid, ds.as_of, t):
                self.assertIn(h["rule"], alerts.STOCK_RULES)

    def test_rule_availability(self):
        av = alerts.rule_availability(data.ds_of("3c"))
        self.assertTrue(all(ok for ok, _ in av.values()))

    def test_custom_alert(self):
        ds = data.ds_of("3c")
        with self.assertRaises(ValueError):
            custom_alerts.validate(dict(name="x", scope="store", metric="price", cond="below", threshold=1), ds)
        r = C.post("/api/alerts/custom?ds=3c", json=dict(name="耳机转化率过低", scope="product", target="P02", metric="cvr",
                                                          cond="below", threshold=0.5, severity="red")).json()
        self.assertGreater(r["hits"], 0)
        c = next(c for c in data.cards("3c") if f"C{r['id']}" in c["rules"])
        self.assertIn("耳机转化率过低", c["rule_names"])
        self.assertEqual(c["severity"], "red")
        C.post("/api/alerts/custom?ds=3c", json=dict(name="全店 GMV 下降", scope="store", metric="gmv", cond="drop", threshold=0.0001))
        C.post("/api/alerts/custom?ds=3c", json=dict(name="全店 GMV 上涨", scope="store", metric="gmv", cond="rise", threshold=0.0001))
        store = [c for c in data.cards("3c") if c["store"]]
        self.assertTrue(store and store[0]["product_name"] == "全店")
        self.assertFalse(any(c["product_id"] == custom_alerts.STORE for c in data.product_cards("3c")))
        s = C.get("/api/alerts/settings?ds=3c").json()
        self.assertEqual(len(s["custom"]), 3)
        self.assertEqual(C.delete(f"/api/alerts/custom/{r['id']}?ds=3c").status_code, 200)


class Push(Base):
    def test_digest_and_dedupe(self):
        r = alert_push.push("3c", manual=True)
        self.assertTrue(r["sent"])
        dg = r["digest"]
        self.assertEqual([x["id"] for x in dg["new"]], [P01, P02, P03])     # 红在前
        self.assertEqual([x["id"] for x in dg["opportunities"]], [P06])
        self.assertIn("open=" + P01, dg["new"][0]["link"])
        self.assertIn("9/22 到货", dg["new"][0]["line"])
        self.assertEqual(CARDS[-1]["header"]["template"], "red")
        self.assertIn("已推送到飞书", card(P01)["log"][-1]["text"])
        r2 = alert_push.push("3c")                                          # 第二次：没有新预警
        self.assertTrue(r2["digest"]["empty"])
        self.assertIn("今天没有新预警", r2["text"])
        aid = mk_todo(P02, "P02")
        finish(aid, "ineffective")                                         # 回到待决定 → 重新推送
        r3 = alert_push.push("3c")
        self.assertEqual([x["id"] for x in r3["digest"]["new"]], [P02])
        self.assertEqual(r3["digest"]["new"][0]["tag"], "上次方案无效")

    def test_decided_not_pushed_and_send_empty(self):
        F.decide("3c", P01, "known", "补货已安排")
        dg = alert_push.digest("3c")
        self.assertNotIn(P01, [x["id"] for x in dg["new"]])
        alert_push.push("3c")
        alert_push.set_config("3c", send_empty=False)
        n = len(CARDS)
        r = alert_push.push("3c")
        self.assertFalse(r["sent"])
        self.assertEqual(len(CARDS), n)

    def test_not_bound(self):
        state.set_setting("feishu_roles", {})
        r = C.post("/api/alerts/push-now?ds=3c").json()
        self.assertFalse(r["sent"])
        self.assertIn("绑定飞书", r["reason"])
        self.assertIn("【今日预警】3C 数码配件", r["text"])

    def test_schedule(self):
        alert_push.set_config("3c", time="09:00")
        self.assertFalse(alert_push.due("3c", dt.datetime(2026, 9, 28, 8, 59)))
        self.assertTrue(alert_push.due("3c", dt.datetime(2026, 9, 28, 9, 0)))
        self.assertIn("3c", alert_push.tick(dt.datetime(2026, 9, 28, 9, 1)))
        self.assertFalse(alert_push.due("3c", dt.datetime(2026, 9, 28, 15, 0)))   # 一天一次
        self.assertTrue(alert_push.due("3c", dt.datetime(2026, 9, 29, 9, 5)))
        with self.assertRaises(ValueError):
            alert_push.set_config("3c", time="25:00")


class Chat(Base):
    def setUp(self):
        fresh()
        os.environ["LLM_MODE"] = "mock"

    def run_chat(self, cid, text, history=None):
        res = None
        for ev in alert_chat.run("3c", cid, (history or []) + [{"role": "user", "content": text}]):
            if ev["type"] == "result":
                res = ev
        return res

    def test_adjust_coupon_and_gift(self):
        r = self.run_chat(P02, "券别给 20，给 15，再加个数据线赠品。")
        p = r["plan"]
        self.assertEqual(p["estimate"]["price_after"], 274)
        self.assertAlmostEqual(p["estimate"]["margin_rate_after"], 0.4307, places=4)
        self.assertAlmostEqual(p["estimate"]["breakeven_lift"], 0.1781, places=3)
        self.assertIn("券面额 20 元 → 15 元", p["changes"])
        self.assertIn("供应链", p["step_owners"])
        self.assertEqual(r["unmatched_numbers"], [])
        d = C.get(f"/api/alerts/{P02}?ds=3c").json()                     # 调整后的方案和对话保存下来
        self.assertTrue(d["plan_is_adjusted"])
        self.assertEqual(len(d["messages"]), 2)
        a = C.post("/api/todos?ds=3c", json=dict(product_id="P02", name=p["name"], steps=[], plan=p, source="alert",
                                                 card_id=P02, context={"summary": "按业务要求调整过：" + "；".join(p["changes"])})).json()
        self.assertEqual(a["source_name"], "预警处理")
        self.assertIn("按业务要求调整过", a["context"]["summary"])
        self.assertEqual(card(P02)["status"], "doing")
        C.delete(f"/api/alerts/{P02}/chat?ds=3c")
        self.assertFalse(C.get(f"/api/alerts/{P02}?ds=3c").json()["plan_is_adjusted"])

    def test_track_metric_can_be_changed(self):
        plan = C.get(f"/api/alerts/{P01}?ds=3c").json()["plan"]
        self.assertEqual(plan["track"]["metric"], "variant_units")          # AI 推荐：断货规格销量
        a = C.post("/api/todos?ds=3c", json=dict(product_id="P01", name=plan["name"], steps=[], plan=plan, source="alert",
                                                 card_id=P01, track_metric="cvr", track_days=7)).json()
        self.assertEqual((a["track_metric"], a["track_days"]), ("cvr", 7))
        self.assertEqual(a["plan"]["track"]["metric_name"], "支付转化率")
        b = C.post("/api/todos?ds=3c", json=dict(product_id="P02", name="不改指标", steps=[], plan=plan, source="alert",
                                                 track_metric="bad")).json()
        self.assertEqual(b["track_metric"], "variant_units")                  # 不认识的指标保持 AI 推荐

    def test_margin_floor_not_bypassed(self):
        p, why, ev = alert_chat.merge_plan("3c", "P02", None, {"type": "price", "coupon": 120})
        self.assertIsNone(p)
        self.assertIn("毛利底线", why)
        r = self.run_chat(P02, "券改成 120 元")                           # 模拟模型给出能接受的替代
        self.assertIsNotNone(r["plan"])
        self.assertGreaterEqual(r["plan"]["estimate"]["margin_rate_after"], 0.25)
        self.assertIn("能接受", r["text"])
        risky = ca.evaluate_plan(data.ds_of("3c"), "P02", coupon=40)      # 超出调价权限：可以用，但标风险
        self.assertTrue(risky["ok"])
        self.assertTrue(risky["risk_notes"])

    def test_known_reason_suggestion(self):
        r = self.run_chat(P01, "白色款补货已经安排了，9/22 到，这个先不用处理。")
        self.assertEqual(r["decision"]["type"], "known")
        self.assertIn("3,200 件在途", r["decision"]["reason"])
        r2 = self.run_chat(P01, "要", [{"role": "user", "content": "补货已经安排了"}, {"role": "assistant", "content": r["text"]}])
        self.assertEqual(r2["plan"]["name"], "详情页加到货提示")
        self.assertNotIn("estimate", r2["plan"])


if __name__ == "__main__":
    unittest.main()
