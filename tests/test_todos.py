"""待办 v9：执行中 → 跟踪中 → 待复盘 → 已完成（执行中可取消），同事步骤走飞书（接口用桩，不发真实消息）。"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["STATE_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["REMINDERS"] = "off"
os.environ["LLM_MODE"] = "off"
os.environ["FEISHU_APP_ID"] = "cli_test"
os.environ["FEISHU_APP_SECRET"] = "x"
os.environ["PUBLIC_BASE_URL"] = "http://demo.local/"

from fastapi.testclient import TestClient  # noqa: E402

from server import app as A, data, feishu, notify, state, todos  # noqa: E402

SENT = []
feishu.token = lambda: "t"
feishu.send_card = lambda oid, card: SENT.append(("card", oid, card)) or f"om_{len(SENT)}"
feishu.update_card = lambda mid, card: SENT.append(("update", mid, card))
feishu.send_text = lambda oid, text: SENT.append(("text", oid, text)) or "m"
state.set_setting("feishu_roles", {"我": dict(name="我", open_id="ou_me"), "供应链": dict(name="小王", open_id="ou_sc")})
C = TestClient(A.app)
STEPS = [{"text": "详情页加到货提示", "by": "我"}, {"text": "确认能否提前到货", "by": "供应链"},
         {"text": "暂停白色款推广", "by": "投放运营"}]


def wait():
    time.sleep(0.3)   # 飞书同步在后台线程


def new(**kw):
    body = dict(product_id="P01", name="核对白色款到货", steps=STEPS, source="manual", note="白色断货", notify=True)
    body.update(kw)
    r = C.post("/api/todos?ds=3c", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def tick_mine(a):
    for i, o in enumerate(a["plan"]["step_owners"]):
        if o == "我":
            a = C.patch(f"/api/actions/{a['id']}/steps?ds=3c", json=dict(index=i, done=True)).json()
    return a


class TestTodos(unittest.TestCase):
    def setUp(self):
        SENT.clear()

    def test_create_notify(self):
        a = new()
        self.assertEqual(a["status"], "doing")
        self.assertEqual(a["stage_name"], "执行中")
        hs = {h["role"]: h for h in a["handoffs"]}
        self.assertEqual(hs["供应链"]["status"], "notified")
        self.assertEqual(hs["投放运营"]["status"], "pending")          # 未绑定飞书 → 未通知
        self.assertEqual([s[0] for s in SENT], ["card"])
        self.assertEqual({x["role"]: x["ok"] for x in a["notified"]}, {"供应链": True, "投放运营": False})

    def test_preview_sends_nothing(self):
        p = C.post("/api/todos/preview?ds=3c", json=dict(product_id="P01", name="x", steps=STEPS)).json()
        self.assertEqual([(x["role"], x["channel"]) for x in p], [("供应链", "feishu"), ("投放运营", "copy")])
        self.assertEqual(SENT, [])

    def test_full_lifecycle_to_tracking(self):
        a = new()
        hs = {h["role"]: h for h in a["handoffs"]}
        C.post(f"/api/handoffs/{hs['投放运营']['id']}/send", json=dict(channel="copy"))
        a = tick_mine(a)
        self.assertEqual(a["status"], "doing")                          # 同事还没完成
        C.post(f"/api/handoffs/{hs['供应链']['id']}/respond", json=dict(status="done"))
        wait()
        self.assertTrue(any(s[0] == "text" and s[1] == "ou_me" and "已完成" in s[2] for s in SENT))
        C.post(f"/api/handoffs/{hs['投放运营']['id']}/proxy-done")
        a = C.get("/api/actions?ds=3c").json()
        a = next(x for x in a if x["id"] == hs["供应链"]["action_row"])
        self.assertEqual(a["status"], "tracking")
        self.assertEqual(a["exec_date"], todos.today("3c"))
        self.assertEqual(C.post(f"/api/actions/{a['id']}/cancel?ds=3c", json={}).status_code, 400)   # 跟踪中不能取消
        a = C.post(f"/api/actions/{a['id']}/end-tracking?ds=3c").json()
        self.assertEqual(a["status"], "review")
        self.assertEqual(a["suggestion"]["outcome"], "unknown")        # 提前结束，数据不足
        a = C.post(f"/api/actions/{a['id']}/review?ds=3c", json=dict(outcome="unknown", note="数据不足")).json()
        self.assertEqual((a["status"], a["outcome_name"]), ("done", "无法判断"))

    def test_question_reply(self):
        a = new()
        h = next(x for x in a["handoffs"] if x["role"] == "供应链")
        self.assertEqual(C.post(f"/api/handoffs/{h['id']}/respond", json=dict(status="question")).status_code, 400)
        C.post(f"/api/handoffs/{h['id']}/respond", json=dict(status="question", note="要不要走加急？"))
        wait()
        self.assertTrue(any(s[0] == "text" and s[1] == "ou_me" and "加急" in s[2] for s in SENT))
        SENT.clear()
        r = C.post(f"/api/handoffs/{h['id']}/reply", json=dict(text="走加急")).json()
        self.assertEqual(r["status"], "notified")
        self.assertTrue(any(s[0] == "text" and s[1] == "ou_sc" and "走加急" in s[2] for s in SENT))

    def test_edit_and_cancel_sync(self):
        a = new()
        SENT.clear()
        a = C.patch(f"/api/actions/{a['id']}?ds=3c", json=dict(due_date="2026-09-26", name="改名")).json()
        wait()
        self.assertEqual((a["name"], a["due_date"]), ("改名", "2026-09-26"))
        self.assertTrue(any(s[0] == "text" and "2026-09-26" in s[2] for s in SENT))
        SENT.clear()
        a = C.post(f"/api/actions/{a['id']}/cancel?ds=3c", json=dict(reason="已由方案 1 覆盖")).json()
        wait()
        self.assertEqual(a["status"], "cancelled")
        self.assertTrue(all(h["status"] == "cancelled" for h in a["handoffs"]))
        self.assertTrue(any(s[0] == "update" and s[2]["header"]["template"] == "grey" for s in SENT))
        h = a["handoffs"][0]
        self.assertEqual(C.post(f"/api/handoffs/{h['id']}/respond", json=dict(status="done")).status_code, 400)

    def test_remind_once_on_due_date(self):
        a = new(due_date=todos.today("3c"))
        h = next(x for x in state.list_handoffs(action_row=a["id"]) if x["role"] == "供应链")
        SENT.clear()
        self.assertIn(h["id"], notify.tick())
        self.assertNotIn(h["id"], notify.tick())                        # 只自动提醒一次
        b = new()                                                       # 截止在 3 天后，今天不提醒
        hb = next(x for x in state.list_handoffs(action_row=b["id"]) if x["role"] == "供应链")
        self.assertNotIn(hb["id"], notify.tick())
        r = C.post(f"/api/handoffs/{hb['id']}/remind").json()            # 催一下随时可用
        self.assertEqual(r["remind_count"], 1)

    def test_adopt_plan_and_reject(self):
        from core import actions as ca
        plan = ca.plan_actions(data.ds_of("3c"), "P01", "stockout")["candidates"][0]
        card = data.card_for("3c", "P01", today_only=True)
        a = new(name=plan["name"], steps=[], plan=plan, source="diagnosis", card_id=card["id"], notify=False)
        self.assertEqual(a["source_name"], "AI 诊断")
        self.assertEqual(a["track_days"], plan["track"]["days"])
        self.assertEqual(state.card_states("3c")[card["id"]]["status"], "processing")
        r = C.post("/api/rejections?ds=3c", json=dict(product_id="P01", plan=plan, reason="成本过高")).json()
        self.assertTrue(r["ok"])
        self.assertEqual(len(C.get("/api/rejections?ds=3c&product_id=P01").json()), 1)

    def test_seed_p05_in_review(self):
        state.reset(data.seed)
        a = next(x for x in C.get("/api/actions?ds=3c").json() if x["product_id"] == "P05")
        self.assertEqual(a["status"], "review")
        self.assertEqual(a["suggestion"]["outcome"], "effective")
        self.assertAlmostEqual(a["suggestion"]["change_pct"], 0.0681, places=3)

    def test_chat_suggestion_parsing(self):
        body, d = todos.split_suggestion('先确认到货。\n<todo>{"name": "确认到货", "steps": [{"text": "问供应链", "by": "供应链"}], '
                                         '"track_metric": "cvr"}</todo>', "3c")
        self.assertEqual(body, "先确认到货。")
        self.assertEqual((d["steps"][0]["by"], d["track_metric"], d["track_days"]), ("供应链", "cvr", 7))
        self.assertTrue(d["due_is_default"])
        self.assertEqual(todos.split_suggestion("<todo>{坏的</todo>", "3c")[1], None)


if __name__ == "__main__":
    unittest.main()
