"""协同通知：保存并通知、变更同步、催办、回复、到期自动提醒。飞书接口用桩替代，不发真实消息。"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["STATE_DB"] = os.path.join(tempfile.mkdtemp(), "n.db")
os.environ["REMINDERS"] = "off"
os.environ["FEISHU_APP_ID"] = "cli_test"
os.environ["FEISHU_APP_SECRET"] = "x"
os.environ["PUBLIC_BASE_URL"] = "http://demo.local/"

from fastapi.testclient import TestClient  # noqa: E402

from server import app as A, feishu, notify, state  # noqa: E402

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
    time.sleep(0.3)   # 变更同步在后台线程


class TestNotify(unittest.TestCase):
    def setUp(self):
        SENT.clear()

    def _new(self, notify_=True):
        r = C.post("/api/todos?ds=3c", json=dict(product_id="P01", name="核对白色款到货", steps=STEPS, source="manual",
                                                 note="白色断货", notify=notify_))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_preview(self):
        p = C.post("/api/todos/preview?ds=3c", json=dict(product_id="P01", name="x", steps=STEPS)).json()
        self.assertEqual([(x["role"], x["channel"]) for x in p], [("供应链", "feishu"), ("投放运营", "copy")])
        self.assertEqual(p[0]["to"], "小王")
        self.assertIn("确认能否提前到货", p[0]["message"])
        self.assertEqual(SENT, [])            # 预览不发消息

    def test_save_and_notify(self):
        a = self._new()
        res = {x["role"]: x for x in a["notified"]}
        self.assertTrue(res["供应链"]["ok"])
        self.assertFalse(res["投放运营"]["ok"])
        self.assertEqual([s[0] for s in SENT], ["card"])
        hs = {h["role"]: h for h in a["handoffs"]}
        self.assertEqual(hs["供应链"]["status"], "sent")
        self.assertEqual(hs["投放运营"]["status"], "draft")

    def test_no_notify_keeps_draft(self):
        a = self._new(notify_=False)
        self.assertEqual(SENT, [])
        self.assertTrue(all(h["status"] == "draft" for h in a["handoffs"]))

    def test_due_change_and_cancel_sync(self):
        a = self._new()
        SENT.clear()
        C.patch(f"/api/actions/{a['id']}?ds=3c", json=dict(due_date="2026-09-26"))
        wait()
        kinds = [s[0] for s in SENT]
        self.assertIn("update", kinds)
        self.assertTrue(any(s[0] == "text" and "2026-09-26" in s[2] for s in SENT))
        h = next(x for x in state.list_handoffs(action_row=a["id"]) if x["role"] == "供应链")
        self.assertEqual(h["due"], "2026-09-26")
        self.assertIn("2026-09-26 前", h["message"])
        SENT.clear()
        C.patch(f"/api/actions/{a['id']}?ds=3c", json=dict(status="cancelled", reason="已由方案 1 覆盖"))
        wait()
        self.assertTrue(all(x["status"] == "cancelled" for x in state.list_handoffs(action_row=a["id"])))
        self.assertTrue(any(s[0] == "text" and "已取消" in s[2] for s in SENT))
        card = [s for s in SENT if s[0] == "update"][-1][2]
        self.assertEqual(card["header"]["template"], "grey")
        self.assertEqual(C.post(f"/api/handoffs/{h['id']}/respond", json=dict(status="done")).status_code, 400)
        C.patch(f"/api/actions/{a['id']}?ds=3c", json=dict(status="adopted"))
        wait()
        st = {x["role"]: x["status"] for x in state.list_handoffs(action_row=a["id"])}
        self.assertEqual(st, {"供应链": "sent", "投放运营": "draft"})

    def test_remind_and_reply(self):
        a = self._new()
        h = next(x for x in a["handoffs"] if x["role"] == "供应链")
        SENT.clear()
        r = C.post(f"/api/handoffs/{h['id']}/remind").json()
        self.assertEqual(r["remind_count"], 1)
        self.assertTrue(SENT[-1][0] == "text" and "催办" in SENT[-1][2] and SENT[-1][1] == "ou_sc")
        d = next(x for x in a["handoffs"] if x["role"] == "投放运营")
        self.assertEqual(C.post(f"/api/handoffs/{d['id']}/remind").status_code, 400)
        C.post(f"/api/handoffs/{h['id']}/respond", json=dict(status="question", note="要不要走加急？"))
        wait()
        self.assertTrue(any(s[0] == "text" and s[1] == "ou_me" and "有疑问" in s[2] and "加急" in s[2] for s in SENT))
        self.assertEqual(state.get_handoff(h["id"])["note"], "要不要走加急？")
        SENT.clear()
        r = C.post(f"/api/handoffs/{h['id']}/reply", json=dict(text="需要，费用我来申请")).json()
        self.assertEqual(r["status"], "sent")
        self.assertTrue(any(s[0] == "text" and s[1] == "ou_sc" and "费用我来申请" in s[2] for s in SENT))

    def test_auto_reminder_real_time(self):
        a = self._new()
        h = next(x for x in state.list_handoffs(action_row=a["id"]) if x["role"] == "供应链")
        now = time.time()
        self.assertFalse(notify.due_for_reminder(h, now))                  # 刚发出，没到期
        dl = notify.deadline(h)
        self.assertGreater(dl, now)
        SENT.clear()
        self.assertIn(h["id"], notify.tick(dl + 60))                       # 到期未完成 → 提醒
        self.assertNotIn(h["id"], notify.tick(dl + 3600))                  # 24 小时内不重复
        self.assertIn(h["id"], notify.tick(dl + 86400 + 120))
        self.assertTrue(all(s[1] == "ou_sc" and "自动提醒" in s[2] for s in SENT))
        C.post(f"/api/handoffs/{h['id']}/respond", json=dict(status="done"))
        self.assertNotIn(h["id"], notify.tick(dl + 3 * 86400))            # 完成后不再提醒


if __name__ == "__main__":
    unittest.main()
