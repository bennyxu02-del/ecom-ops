"""飞书卡片按钮回调（长连接）。独立进程运行，不需要公网 HTTPS 回调地址。

启动：python -m server.feishu_ws        （部署脚本会注册为 systemd 服务 ecom-ops-feishu）
收到按钮点击后调用本机平台接口更新协同状态，并把卡片刷新为最新状态。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

import lark_oapi as lark  # noqa: E402
from lark_oapi.event.callback.model.p2_card_action_trigger import (  # noqa: E402
    P2CardActionTrigger, P2CardActionTriggerResponse)

from server import feishu, state  # noqa: E402

PORT = os.environ.get("PORT", "8000")
LOCAL = f"http://127.0.0.1:{PORT}"
NAMES = {"done": "已完成", "question": "有疑问"}


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(LOCAL + path, method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _role_of(open_id: str) -> str | None:
    for role, v in feishu.roles().items():
        if v.get("open_id") == open_id and role != "我":
            return role
    return None


def on_card(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    try:
        v = data.event.action.value or {}
        state.set_setting("feishu_last_callback", time.time())
        if v.get("test"):
            return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "回调正常"},
                                                "card": {"type": "raw", "data": feishu.build_test_done_card()}})
        hid, st = int(v["hid"]), v["status"]
        h0 = state.get_handoff(hid)
        by = _role_of(data.event.operator.open_id) or (h0 or {}).get("role")
        try:
            h = _post(f"/api/handoffs/{hid}/respond", {"status": st, "by": by})
        except urllib.error.HTTPError as e:
            msg = json.loads(e.read() or b"{}").get("detail", "操作失败")
            return P2CardActionTriggerResponse({"toast": {"type": "warning", "content": msg}})
        base = state.get_setting("public_base_url")
        link = f"{base.rstrip('/')}/#/h/{hid}" if base else None
        card = feishu.build_card(h, link)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": f"已记录：{NAMES.get(st, st)}"},
                                            "card": {"type": "raw", "data": card}})
    except Exception as e:  # noqa: BLE001
        print("[feishu_ws] 处理回调出错：", repr(e), flush=True)
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "平台暂时无法处理，请稍后再试"}})


def heartbeat():
    while True:
        try:
            state.set_setting("feishu_ws_heartbeat", time.time())
        except Exception:  # noqa: BLE001
            pass
        time.sleep(30)


def main():
    app_id, secret = feishu.creds()
    if not (app_id and secret):
        print("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，退出", flush=True)
        sys.exit(0)
    threading.Thread(target=heartbeat, daemon=True).start()
    handler = lark.EventDispatcherHandler.builder("", "").register_p2_card_action_trigger(on_card).build()
    cli = lark.ws.Client(app_id, secret, event_handler=handler, log_level=lark.LogLevel.INFO)
    print("飞书长连接已启动，等待卡片回调…", flush=True)
    cli.start()


if __name__ == "__main__":
    main()
