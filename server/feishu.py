"""飞书集成：以应用机器人身份给同事发协同卡片、给发起人发进展通知。

只用标准库调用飞书开放平台接口；按钮回调走长连接，由独立进程 server/feishu_ws.py 处理。
凭证放在 .env：FEISHU_APP_ID / FEISHU_APP_SECRET；角色与同事的对应关系存在运行状态库（settings 表）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import state

API = os.environ.get("FEISHU_API", "https://open.feishu.cn/open-apis")
ROLES = ["我", "供应链", "投放运营"]
_tok = {"v": None, "exp": 0.0}
_lock = threading.Lock()


class FeishuError(Exception):
    pass


def creds():
    return os.environ.get("FEISHU_APP_ID", "").strip(), os.environ.get("FEISHU_APP_SECRET", "").strip()


def enabled() -> bool:
    a, s = creds()
    return bool(a and s)


def _call(method: str, path: str, body=None, token: str | None = None, timeout=15) -> dict:
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(API + path, method=method, headers=h,
                                 data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read())
        except Exception:
            raise FeishuError(f"飞书接口 HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise FeishuError(f"连不上飞书：{e}")
    if d.get("code") not in (0, None):
        raise FeishuError(_explain(d))
    return d


def _explain(d: dict) -> str:
    code, msg = d.get("code"), d.get("msg", "")
    hints = {99991672: "应用缺少权限，请在飞书开放平台「权限管理」开通后重新发布版本",
             99991663: "应用凭证无效，请检查 App ID / App Secret",
             230013: "对方不在应用的可用范围内，请在「版本管理与发布」里把对方加入可用范围后重新发布",
             99992361: "找不到这个用户"}
    return f"{hints.get(code, msg)}（飞书错误码 {code}）"


def token() -> str:
    with _lock:
        if _tok["v"] and time.time() < _tok["exp"] - 120:
            return _tok["v"]
        a, s = creds()
        if not (a and s):
            raise FeishuError("未配置飞书应用凭证")
        d = _call("POST", "/auth/v3/tenant_access_token/internal", {"app_id": a, "app_secret": s})
        _tok.update(v=d["tenant_access_token"], exp=time.time() + int(d.get("expire", 7200)))
        return _tok["v"]


def lookup(contact: str) -> str:
    """手机号或邮箱 → open_id。"""
    contact = contact.strip()
    body = {"emails": [contact]} if "@" in contact else {"mobiles": [contact]}
    d = _call("POST", "/contact/v3/users/batch_get_id?user_id_type=open_id", body, token())
    users = (d.get("data") or {}).get("user_list") or []
    oid = users[0].get("user_id") if users else None
    if not oid:
        raise FeishuError("在飞书里没有找到这个手机号 / 邮箱对应的成员，请确认对方已加入你的飞书团队")
    return oid


def send_card(open_id: str, card: dict) -> str:
    d = _call("POST", "/im/v1/messages?receive_id_type=open_id",
              {"receive_id": open_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)}, token())
    return d["data"]["message_id"]


def update_card(message_id: str, card: dict):
    _call("PATCH", f"/im/v1/messages/{message_id}", {"content": json.dumps(card, ensure_ascii=False)}, token())


def send_text(open_id: str, text: str) -> str:
    d = _call("POST", "/im/v1/messages?receive_id_type=open_id",
              {"receive_id": open_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}, token())
    return d["data"]["message_id"]


# ---------------------------------------------------------------------------
# 角色 ↔ 同事
# ---------------------------------------------------------------------------
def roles() -> dict:
    return state.get_setting("feishu_roles", {}) or {}


def set_role(role: str, name: str, contact: str) -> dict:
    oid = lookup(contact)
    masked = contact if "@" in contact else (contact[:3] + "****" + contact[-4:] if len(contact) >= 7 else contact)
    rs = roles()
    rs[role] = dict(name=name.strip() or role, open_id=oid, contact=masked, updated_at=time.time())
    state.set_setting("feishu_roles", rs)
    return rs[role]


def clear_role(role: str):
    rs = roles()
    rs.pop(role, None)
    state.set_setting("feishu_roles", rs)


def status() -> dict:
    ok, err = False, None
    if enabled():
        try:
            token()
            ok = True
        except FeishuError as e:
            err = str(e)
    rs = roles()
    return dict(enabled=enabled(), ready=ok, error=err, app_id=creds()[0][:8] + "…" if creds()[0] else None,
                roles={k: {kk: vv for kk, vv in v.items() if kk != "updated_at"} for k, v in rs.items()},
                role_list=ROLES, callback_online=callback_online(),
                last_callback=state.get_setting("feishu_last_callback"))


def build_test_card() -> dict:
    return {"config": {"wide_screen_mode": True, "update_multi": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "【测试】卡片按钮回调"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "点击下方按钮，平台「集成」页会显示回调是否正常。"}},
                         {"tag": "action", "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "测试回调"},
                                                        "type": "primary", "value": {"test": 1}}]}]}


def build_test_done_card() -> dict:
    return {"config": {"wide_screen_mode": True, "update_multi": True},
            "header": {"template": "green", "title": {"tag": "plain_text", "content": "【测试】卡片按钮回调"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "✅ **回调正常**：平台已收到按钮点击。"}}]}


def callback_online() -> bool:
    """长连接回调进程最近 90 秒内有心跳。"""
    hb = state.get_setting("feishu_ws_heartbeat", 0) or 0
    return time.time() - float(hb) < 90


# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------
STATUS_TEXT = {"notified": "待处理", "question": "有疑问，等待发起人回复", "done": "已完成",
               "cancelled": "已取消，无需处理", "pending": "未发出"}


def _md(s: str) -> str:
    return s.replace("<", "&lt;").replace(">", "&gt;")


def build_card(h: dict, link: str | None) -> dict:
    """h：decorate_handoff(with_context=True) 的结果。按钮只有「已完成」和「有疑问」。"""
    a = h.get("action") or {}
    closed = h["status"] in ("done", "cancelled")
    body = h.get("message") or ""
    lines = [x for x in body.split("\n") if not x.startswith("处理入口：")]
    if lines and lines[0].startswith("【"):
        lines = lines[1:]
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": _md("\n".join(lines).strip())}}]
    status = "发起人已确认完成" if h.get("proxy") else STATUS_TEXT.get(h["status"], h["status"])
    line = f"**当前状态：{status}**" + (f"　截止 {h['due']}" if h.get("due") and not closed else "")
    if h.get("note") and h["status"] == "question":
        line += f"\n你的疑问：{_md(h['note'])}"
    elements += [{"tag": "hr"}, {"tag": "div", "text": {"tag": "lark_md", "content": line}}]
    actions = []
    if not closed:
        actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "已完成"}, "type": "primary",
                        "value": {"hid": h["id"], "status": "done"}})
        if link:
            actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "有疑问"}, "type": "default", "url": link})
    elif link:
        actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "查看详情"}, "type": "default", "url": link})
    if actions:
        elements.append({"tag": "action", "actions": actions})
    template = "grey" if h["status"] == "cancelled" else "green" if closed else "orange"
    return {"config": {"wide_screen_mode": True, "update_multi": True},
            "header": {"template": template, "title": {"tag": "plain_text", "content": "【协同请求】" + (a.get("product_name") or "")}},
            "elements": elements}
