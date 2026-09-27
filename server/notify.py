"""协同通知：把分给同事的步骤推送到对方飞书，并在待办变化时同步。

- send / send_all：推送协同卡片（对方没绑定飞书时，留给运营复制文字手动发送）
- sync：对方处理后刷新卡片，并把进展通知发起人（「我」）
- changed：截止日期调整、名称修改、待办取消时，刷新卡片并给对方发说明
- proxied：发起人代为标记完成后，刷新卡片并告知对方
- remind：催一下（手动），截止日当天自动提醒一次（按业务日期）
- reply：对方提出疑问后，发起人的回复推回给对方

飞书调用失败不影响平台主流程，结果写入协同记录。
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import collab, data, feishu, state

WAITING = ("notified",)          # 等对方处理的状态（question 时轮到发起人回复，不催）


class NotifyError(Exception):
    pass


def link(hid: int) -> str | None:
    b = os.environ.get("PUBLIC_BASE_URL") or state.get_setting("public_base_url")
    return f"{b.rstrip('/')}/#/h/{hid}" if b else None


def _h(hid: int) -> dict:
    return collab.decorate_handoff(state.get_handoff(hid), with_context=True)


def recipient(role: str) -> dict | None:
    """角色对应的飞书成员；飞书未启用或未绑定时返回 None。"""
    if not feishu.enabled():
        return None
    r = feishu.roles().get(role)
    return r if r and r.get("open_id") else None


def _log(hid: int, text: str, by: str = "系统"):
    h = state.get_handoff(hid)
    hist = json.loads(h.get("history_json") or "[]")
    hist.append(dict(t=time.time(), status=h["status"], by=by, note=text))
    state.update_handoff(hid, history_json=json.dumps(hist, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 推送
# ---------------------------------------------------------------------------
def send(hid: int, channel: str = "auto", message: str | None = None) -> dict:
    """channel：feishu / copy / auto（绑定了飞书就推送，否则标记为已复制发送）。"""
    h0 = state.get_handoff(hid)
    if h0["status"] != "pending":
        raise NotifyError("这条协同请求已经发出")
    if message:
        state.update_handoff(hid, message=message)
    r = recipient(h0["role"])
    if channel == "auto":
        channel = "feishu" if r else "copy"
    if channel == "feishu":
        if not r:
            raise NotifyError(f"飞书未配置，或还没有设置「{h0['role']}」对应的飞书成员")
        h = _h(hid)
        h["status"] = "notified"
        try:
            mid = feishu.send_card(r["open_id"], feishu.build_card(h, link(hid)))
        except feishu.FeishuError as e:
            raise NotifyError(str(e))
        state.update_handoff(hid, assignee=r.get("name"))
        collab.mark_sent(hid, "feishu", message, ext_id=mid)
    else:
        collab.mark_sent(hid, "copy", message)
    return _h(hid)


def send_all(action_row: int) -> list[dict]:
    """保存并通知：把一条待办下所有未通知的协同推出去（未绑定飞书的保持「未通知」）。"""
    out = []
    for h in state.list_handoffs(action_row=action_row):
        if h["status"] != "pending":
            continue
        r = recipient(h["role"])
        if not r:
            out.append(dict(id=h["id"], role=h["role"], ok=False, error="未绑定飞书"))
            continue
        try:
            send(h["id"], "feishu")
            out.append(dict(id=h["id"], role=h["role"], ok=True, to=r.get("name")))
        except NotifyError as e:
            out.append(dict(id=h["id"], role=h["role"], ok=False, error=str(e)))
    return out


def preview(items: list[dict]) -> list[dict]:
    """保存前预览：每个协同事项会发给谁、通过什么渠道、消息内容。"""
    res = []
    for h in items:
        r = recipient(h["role"])
        res.append(dict(role=h["role"], steps=h.get("steps", []), to=(r or {}).get("name"),
                        channel="feishu" if r else "copy", message=h.get("message")))
    return res


# ---------------------------------------------------------------------------
# 变化同步
# ---------------------------------------------------------------------------
def _refresh_card(h: dict):
    if h.get("channel") == "feishu" and h.get("ext_id") and feishu.enabled():
        try:
            feishu.update_card(h["ext_id"], feishu.build_card(h, link(h["id"])))
        except feishu.FeishuError as e:
            print("[notify] 更新卡片失败：", e, flush=True)


def _text_to(role: str, text: str) -> bool:
    r = recipient(role)
    if not r:
        return False
    try:
        feishu.send_text(r["open_id"], text)
        return True
    except feishu.FeishuError as e:
        print("[notify] 发送消息失败：", e, flush=True)
        return False


def sync(hid: int, notify_me: bool = True):
    """对方处理后：刷新卡片，并把进展告诉发起人。"""
    h = _h(hid)
    _refresh_card(h)
    if notify_me and h["status"] in ("done", "question"):
        a = h.get("action") or {}
        last = (h.get("history") or [{}])[-1]
        text = (f"【协同进展】{h['role']} {h['status_name']}：{a.get('product_name', '')} · {a.get('name', '')}"
                + (f"\n说明：{last.get('note')}" if last.get("note") else "")
                + ("\n请到待办中心回复对方。" if h["status"] == "question" else ""))
        _text_to("我", text)


def changed(action_row: int, what: str, detail: str = ""):
    """待办本身变化后同步到已推送的协同事项（what：due / rename / cancel）。"""
    a = state.get_action(action_row)
    for h0 in state.list_handoffs(action_row=action_row):
        if h0["status"] == "pending" or (h0["status"] == "done" and what != "rename"):
            continue
        h = _h(h0["id"])
        _refresh_card(h)
        if h0.get("channel") != "feishu":
            continue
        head = f"【待办变更】{a['product_name']} · {a['name']}"
        msg = {"due": f"{head}\n截止日期调整为 {detail}，请按新的时间安排。",
               "cancel": f"{head}\n这条协同请求已取消，无需继续处理。" + (f"\n原因：{detail}" if detail else "")}.get(what)
        if msg and _text_to(h0["role"], msg):
            _log(h0["id"], {"due": "已通知对方截止日期调整", "cancel": "已通知对方待办取消"}[what])


def proxied(hid: int):
    """我代为标记完成后：刷新卡片，并告诉对方无需再处理。"""
    h = _h(hid)
    _refresh_card(h)
    if h.get("channel") == "feishu":
        a = h.get("action") or {}
        _text_to(h["role"], f"【协同进展】{a.get('product_name', '')} · {a.get('name', '')}\n发起人已确认这部分完成，无需再处理，谢谢！")


def remind(hid: int, auto: bool = False, now: float | None = None) -> dict:
    h = _h(hid)
    if h["status"] not in WAITING:
        raise NotifyError("该事项当前不需要催办")
    a = h.get("action") or {}
    steps = "\n".join(f"{i}. {t}" for i, t in enumerate(h.get("step_texts") or [], 1))
    text = (f"【{'到期提醒' if auto else '催办'}】{a.get('product_name', '')} · {a.get('name', '')}\n"
            + (f"需要{h['role']}协助：\n{steps}\n" if steps else "")
            + f"截止 {h.get('due') or '—'}，目前还没有完成。处理后请在协同卡片上点「已完成」。"
            + (f"\n处理入口：{link(hid)}" if link(hid) else ""))
    if not _text_to(h["role"], text):
        raise NotifyError(f"「{h['role']}」没有绑定飞书，请复制内容手动提醒")
    state.update_handoff(hid, reminded_at=now or time.time(), remind_count=int(h.get("remind_count") or 0) + 1,
                         auto_reminds=int(h.get("auto_reminds") or 0) + (1 if auto else 0))
    _log(hid, "到期自动提醒" if auto else "催一下", by="系统" if auto else "我")
    return _h(hid)


def reply(hid: int, text: str) -> dict:
    """回复对方的疑问：推送给对方，事项回到「已通知」。"""
    h0 = state.get_handoff(hid)
    if h0["status"] != "question":
        raise NotifyError("对方没有提出疑问")
    if not text.strip():
        raise NotifyError("请填写回复内容")
    hist = json.loads(h0.get("history_json") or "[]")
    hist.append(dict(t=time.time(), status="notified", by="我", note="回复：" + text.strip()))
    state.update_handoff(hid, status="notified", history_json=json.dumps(hist, ensure_ascii=False))
    collab.add_log(h0["action_row"], f"回复{h0['role']}：{text.strip()}")
    h = _h(hid)
    a = h.get("action") or {}
    _refresh_card(h)
    h["reply_pushed"] = _text_to(h["role"], f"【回复】{a.get('product_name', '')} · {a.get('name', '')}\n{text.strip()}")
    return h


# ---------------------------------------------------------------------------
# 截止日自动提醒（业务日期，只提醒一次）
# ---------------------------------------------------------------------------
def due_for_reminder(h: dict) -> bool:
    if h["status"] not in WAITING or h.get("channel") != "feishu" or int(h.get("auto_reminds") or 0) >= 1:
        return False
    a = state.get_action(h["action_row"])
    if not a or a["status"] != "doing" or not h.get("due"):
        return False
    return data.ds_of(h["ds"]).as_of.strftime("%Y-%m-%d") >= h["due"]


def tick() -> list[int]:
    done = []
    for name in data.DATASETS:
        for h in state.list_handoffs(name):
            if due_for_reminder(h):
                try:
                    remind(h["id"], auto=True)
                    done.append(h["id"])
                except NotifyError as e:
                    print("[notify] 自动提醒失败：", e, flush=True)
    return done


def start_scheduler(interval: float | None = None):
    interval = interval or float(os.environ.get("REMINDER_INTERVAL", "600"))

    def loop():
        while True:
            time.sleep(interval)
            try:
                tick()
            except Exception as e:  # noqa: BLE001
                print("[notify] 提醒检查出错：", repr(e), flush=True)
    threading.Thread(target=loop, daemon=True, name="reminders").start()
