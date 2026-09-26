"""待办：新建、协同拆分、轻量编辑、状态流转、追问建议解析。使用临时状态库，不影响演示数据。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["STATE_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ.setdefault("LLM_MODE", "off")

from fastapi.testclient import TestClient  # noqa: E402

from server import app as A, todos  # noqa: E402

C = TestClient(A.app)
STEPS = [{"text": "详情页加到货提示", "by": "我"}, {"text": "确认能否提前到货", "by": "供应链"}]


class TestTodos(unittest.TestCase):
    def _new(self, **kw):
        body = dict(product_id="P01", name="核对白色款到货", steps=STEPS, source="chat", note="追问发现")
        body.update(kw)
        r = C.post("/api/todos?ds=3c", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_create_splits_handoffs(self):
        a = self._new()
        self.assertEqual(a["status"], "adopted")
        self.assertEqual(a["source_name"], "追问")
        self.assertEqual([h["role"] for h in a["handoffs"]], ["供应链"])
        self.assertEqual(a["progress"]["mine"], [0])
        self.assertTrue(a["due_date"])

    def test_requires_name_and_steps(self):
        self.assertEqual(C.post("/api/todos?ds=3c", json=dict(product_id="P01", name="", steps=STEPS)).status_code, 400)
        self.assertEqual(C.post("/api/todos?ds=3c", json=dict(product_id="P01", name="x", steps=[{"text": " "}])).status_code, 400)

    def test_light_edit_and_status(self):
        a = self._new()
        x = C.patch(f"/api/actions/{a['id']}?ds=3c", json=dict(name="新名称", due_date="2026-09-25", note="备注")).json()
        self.assertEqual((x["name"], x["due_date"], x["note"]), ("新名称", "2026-09-25", "备注"))
        self.assertEqual(x["plan"]["name"], "新名称")
        x = C.patch(f"/api/actions/{a['id']}?ds=3c", json=dict(status="cancelled", reason="不需要了")).json()
        self.assertEqual(x["status_name"], "已取消")
        self.assertEqual(C.patch(f"/api/actions/{a['id']}?ds=3c", json=dict(status="executed")).status_code, 400)
        x = C.patch(f"/api/actions/{a['id']}?ds=3c", json=dict(status="adopted")).json()
        self.assertEqual(x["status"], "adopted")
        self.assertGreaterEqual(len(x["log"]), 5)

    def test_suggestion_parsing(self):
        body, d = todos.split_suggestion('先确认到货。\n<todo>{"name": "确认到货", "steps": [{"text": "问供应链", "by": "供应链"}], '
                                         '"track_metric": "cvr", "due_days": 2}</todo>', "3c")
        self.assertEqual(body, "先确认到货。")
        self.assertEqual(d["steps"][0]["by"], "供应链")
        self.assertEqual(d["track_metric"], "cvr")
        self.assertEqual(todos.split_suggestion("没有建议", "3c"), ("没有建议", None))
        _, bad = todos.split_suggestion("<todo>{坏的</todo>", "3c")
        self.assertIsNone(bad)


if __name__ == "__main__":
    unittest.main()
