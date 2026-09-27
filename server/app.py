"""平台服务（FastAPI）。

启动：python -m server.app                 默认端口 8000，可用 PORT 覆盖
接口文档：http://<地址>/docs
"""
from __future__ import annotations

import io
import threading
import json
import math
import os
import sys
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import actions as core_actions  # noqa: E402
from core import sop, weekly  # noqa: E402
from core.reports import SCENES  # noqa: E402

from . import collab, data, feishu, llm_client, notify, state, todos  # noqa: E402
from .agent import chat as chat_agent  # noqa: E402
from .agent import diagnose, report  # noqa: E402

WEB_DIST = ROOT / "web" / "dist"
SKILL_DIST = ROOT / "dist" / "product-ops-analysis"
DS = Query("3c", description="数据集：3c / snacks")


# ---------------------------------------------------------------------------
# JSON：兼容 numpy / pandas 类型
# ---------------------------------------------------------------------------
def _default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        v = float(o)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(o, pd.Timestamp):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def _clean(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def dumps(o) -> str:
    return json.dumps(_clean(o), ensure_ascii=False, default=_default)


class JSON(JSONResponse):
    def render(self, content: Any) -> bytes:
        return dumps(content).encode("utf-8")


def ds_name(ds: str) -> str:
    return ds if ds in data.DATASETS else "3c"


def sse(gen):
    """把事件生成器包装为 Server-Sent Events。"""
    def body():
        try:
            for ev in gen:
                yield f"data: {dumps(ev)}\n\n"
            yield 'data: {"type":"done"}\n\n'
        except Exception as e:  # noqa: BLE001
            yield f"data: {dumps({'type': 'error', 'message': str(e)})}\n\n"
    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class FocusBody(BaseModel):
    focus: bool


class AlertUpdate(BaseModel):
    status: Literal["pending", "processing", "done", "ignored"]
    reason: Optional[str] = None
    note: Optional[str] = None


class StepUpdate(BaseModel):
    index: int
    done: bool


class HandoffSend(BaseModel):
    channel: Literal["auto", "copy", "feishu"] = "auto"
    message: Optional[str] = None


class HandoffReply(BaseModel):
    text: str


class HandoffRespond(BaseModel):
    status: Literal["done", "question"]
    note: Optional[str] = None
    by: Optional[str] = None


class FeishuRole(BaseModel):
    name: str = ""
    contact: str = Field(..., description="对方飞书账号绑定的手机号或邮箱")


class FeishuTest(BaseModel):
    role: str


class TodoStep(BaseModel):
    text: str
    by: str = "我"


class TodoCreate(BaseModel):
    product_id: str
    name: str = ""
    steps: list[TodoStep] = []
    due_date: Optional[str] = None
    track_metric: Optional[str] = None
    track_days: Optional[int] = None
    note: Optional[str] = None
    source: Literal["diagnosis", "chat", "manual", "report"] = "manual"
    context: Optional[dict] = None
    plan: Optional[dict] = Field(None, description="采纳 AI 方案时传入诊断结果中的方案对象")
    card_id: Optional[str] = None
    notify: bool = Field(False, description="保存后立即把同事的步骤推送到对方飞书")


class TodoUpdate(BaseModel):
    name: Optional[str] = None
    due_date: Optional[str] = None
    note: Optional[str] = None


class TodoCancel(BaseModel):
    reason: Optional[str] = None


class TodoReview(BaseModel):
    outcome: Literal["effective", "ineffective", "unknown"]
    note: Optional[str] = None


class PlanReject(BaseModel):
    product_id: str
    plan: dict
    reason: str
    card_id: Optional[str] = None


class TodoDraft(BaseModel):
    messages: list[dict] = []
    reply: str


class ReportPut(BaseModel):
    content: str


class ChatBody(BaseModel):
    messages: list[dict] = []
    preset: Optional[str] = None
    plan: Optional[dict] = None


# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app):
    for name in data.DATASETS:
        data.get(name)                      # 预加载与预警扫描
    if not state.list_actions("3c"):
        data.seed()
    if os.environ.get("REMINDERS", "on") != "off":
        notify.start_scheduler()
    yield


app = FastAPI(title="重点商品经营作战台 API", version="1.0", default_response_class=JSON, lifespan=lifespan,
              description="平台接口。数字均由计算层（core/）计算，AI 负责判断与表达。")


@app.exception_handler(KeyError)
async def _keyerr(request: Request, exc: KeyError):
    return JSON({"error": str(exc).strip("'")}, status_code=404)


@app.exception_handler(ValueError)
async def _valerr(request: Request, exc: ValueError):
    return JSON({"error": str(exc)}, status_code=400)


# ---------------- 基础 ----------------
@app.get("/api/health", tags=["基础"])
def api_health():
    return dict(ok=True, llm=llm_client.health(), agent_mode=llm_client.agent_mode(),
                time=time.strftime("%Y-%m-%d %H:%M:%S"))


@app.get("/api/datasets", tags=["基础"])
def api_datasets():
    return data.datasets_info()


@app.get("/api/methods", tags=["基础"])
def api_methods(ds: str = DS):
    return data.methods(ds_name(ds))


# ---------------- 总览与商品 ----------------
@app.get("/api/overview", tags=["经营总览"])
def api_overview(ds: str = DS, window: int = 7):
    return data.overview(ds_name(ds), window)


@app.get("/api/products", tags=["商品"])
def api_products(ds: str = DS, focus: bool = False):
    return data.products(ds_name(ds), focus_only=focus)


def _decorate_action(name, a):
    return todos.decorate(name, a)


@app.get("/api/products/{pid}", tags=["商品"])
def api_product(pid: str, ds: str = DS):
    name = ds_name(ds)
    if pid not in data.ds_of(name).product_ids():
        raise HTTPException(404, f"未知商品 {pid}")
    d = data.product_detail(name, pid)
    d["actions"] = [_decorate_action(name, a) for a in d["actions"]]
    d["diagnosis_cached"] = bool(state.cache_get(diagnose.cache_key(name, pid)))
    return d


@app.post("/api/products/{pid}/focus", tags=["商品"])
def api_focus(pid: str, body: FocusBody, ds: str = DS):
    state.set_focus(ds_name(ds), pid, body.focus)
    return dict(ok=True)


# ---------------- 预警 ----------------
@app.get("/api/alerts", tags=["预警中心"])
def api_alerts(ds: str = DS, today: bool = False, status: Optional[str] = None):
    name = ds_name(ds)
    cs = data.cards(name)
    if today:
        cs = [c for c in cs if c["is_today"]]
    if status:
        cs = [c for c in cs if c["status"] in status.split(",")]
    for c in cs:
        if c["is_today"]:
            hit = state.cache_get(diagnose.cache_key(name, c["product_id"]))
            c["ai_summary"] = hit["result"]["summary"] if hit else None
    return cs


@app.patch("/api/alerts/{cid}", tags=["预警中心"])
def api_alert_update(cid: str, body: AlertUpdate, ds: str = DS):
    if body.status == "ignored" and not body.reason:
        raise HTTPException(400, "忽略时需要选择原因")
    state.set_card(ds_name(ds), cid, body.status, reason=body.reason, note=body.note)
    return dict(ok=True)


# ---------------- AI ----------------
@app.post("/api/diagnose/{pid}", tags=["AI"], summary="单品诊断（SSE 流式）")
def api_diagnose(pid: str, ds: str = DS, refresh: bool = False):
    name = ds_name(ds)
    if pid not in data.ds_of(name).product_ids():
        raise HTTPException(404, f"未知商品 {pid}")
    c = data.card_for(name, pid, today_only=True)
    if c and c["status"] == "pending":
        state.set_card(name, c["id"], "processing")
    return sse(diagnose.run(name, pid, refresh=refresh))


@app.post("/api/chat/{pid}", tags=["AI"], summary="追问与执行物料（SSE 流式）")
def api_chat(pid: str, body: ChatBody, ds: str = DS):
    return sse(chat_agent.run(ds_name(ds), pid, body.messages, body.preset, body.plan))


# ---------------- 动作 ----------------
@app.get("/api/actions", tags=["待办中心"])
def api_actions(ds: str = DS, product_id: Optional[str] = None):
    name = ds_name(ds)
    return [_decorate_action(name, a) for a in state.list_actions(name, product_id)]


def _base_url(request: Request | None = None) -> str | None:
    """对外访问地址：优先环境变量；否则取浏览器访问的地址并记住（供飞书回调等内部请求复用）。"""
    if os.environ.get("PUBLIC_BASE_URL"):
        return os.environ["PUBLIC_BASE_URL"]
    if request is not None:
        host = request.url.hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            url = str(request.base_url)
            if state.get_setting("public_base_url") != url:
                state.set_setting("public_base_url", url)
            return url
    return state.get_setting("public_base_url") or (str(request.base_url) if request is not None else None)


def _action_or_404(aid: int) -> dict:
    a = state.get_action(aid)
    if not a:
        raise HTTPException(404, "待办不存在")
    return a


def _todo_plan(name: str, body: TodoCreate) -> dict:
    return todos.build_plan(name, body.product_id, body.name, [s.model_dump() for s in body.steps], body.track_metric,
                            body.track_days, body.source, base=body.plan)


@app.post("/api/todos/preview", tags=["待办中心"], summary="保存前预览：哪些步骤要通知谁、消息内容")
def api_todo_preview(body: TodoCreate, ds: str = DS):
    name = ds_name(ds)
    if body.product_id not in data.ds_of(name).product_ids():
        raise HTTPException(400, "请选择商品")
    plan = _todo_plan(name, body)
    ctx = dict(body.context or {})
    if not ctx.get("summary") and body.note:
        ctx["summary"] = body.note
    due = body.due_date or todos.default_due(name, int(plan.get("due_days") or todos.DEFAULT_DUE_DAYS))
    return notify.preview(todos.preview(name, body.product_id, plan, ctx, due, body.card_id))


@app.post("/api/todos", tags=["待办中心"], summary="新建待办（采纳 AI 方案 / 对话生成 / 手动新建）")
def api_todo_create(body: TodoCreate, request: Request, ds: str = DS):
    name = ds_name(ds)
    try:
        aid = todos.create(name, body.product_id, title=body.name, steps=[s.model_dump() for s in body.steps],
                           due_date=body.due_date, track_metric=body.track_metric, track_days=body.track_days,
                           note=body.note, source=body.source, context=body.context, plan=body.plan,
                           card_id=body.card_id, base_url=_base_url(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    notified = notify.send_all(aid) if body.notify else []
    return dict(_decorate_action(name, state.get_action(aid)), notified=notified)


@app.patch("/api/actions/{aid}", tags=["待办中心"], summary="编辑待办：名称、截止日期、备注")
def api_action_update(aid: int, body: TodoUpdate, ds: str = DS):
    name = ds_name(ds)
    _action_or_404(aid)
    try:
        todos.update(name, aid, title=body.name, due_date=body.due_date, note=body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _decorate_action(name, state.get_action(aid))


@app.patch("/api/actions/{aid}/steps", tags=["待办中心"], summary="勾选 / 取消勾选我的步骤")
def api_action_step(aid: int, body: StepUpdate, ds: str = DS):
    name = ds_name(ds)
    a = _action_or_404(aid)
    if a["status"] != "doing":
        raise HTTPException(400, "只有执行中的待办可以勾选步骤")
    collab.set_step(aid, body.index, body.done)
    return _decorate_action(name, state.get_action(aid))


@app.post("/api/actions/{aid}/cancel", tags=["待办中心"], summary="取消待办（仅执行中）")
def api_action_cancel(aid: int, body: TodoCancel, ds: str = DS):
    name = ds_name(ds)
    _action_or_404(aid)
    try:
        todos.cancel(name, aid, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _decorate_action(name, state.get_action(aid))


@app.post("/api/actions/{aid}/end-tracking", tags=["待办中心"], summary="提前结束跟踪，进入待复盘")
def api_action_end_tracking(aid: int, ds: str = DS):
    name = ds_name(ds)
    _action_or_404(aid)
    try:
        todos.end_tracking(name, aid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _decorate_action(name, state.get_action(aid))


@app.post("/api/actions/{aid}/review", tags=["待办中心"], summary="复盘：确认结论，待办完成")
def api_action_review(aid: int, body: TodoReview, ds: str = DS):
    name = ds_name(ds)
    _action_or_404(aid)
    try:
        todos.review(name, aid, body.outcome, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _decorate_action(name, state.get_action(aid))


@app.get("/api/rejections", tags=["待办中心"], summary="驳回的 AI 方案")
def api_rejections(ds: str = DS, product_id: Optional[str] = None):
    return state.list_rejections(ds_name(ds), product_id)


@app.post("/api/rejections", tags=["待办中心"], summary="驳回 AI 方案（不生成待办）")
def api_reject(body: PlanReject, ds: str = DS):
    try:
        rid = todos.reject_plan(ds_name(ds), body.product_id, body.plan, body.reason, body.card_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return dict(ok=True, id=rid)


@app.delete("/api/rejections/{rid}", tags=["待办中心"], summary="撤销驳回")
def api_reject_undo(rid: int):
    state.delete_rejection(rid)
    return dict(ok=True)


# ---------------- 集成 ----------------
@app.get("/api/integrations/feishu", tags=["集成"])
def api_feishu_status():
    return feishu.status()


@app.put("/api/integrations/feishu/roles/{role}", tags=["集成"], summary="设置角色对应的飞书成员（按手机号或邮箱查找）")
def api_feishu_role(role: str, body: FeishuRole):
    if role not in feishu.ROLES:
        raise HTTPException(400, "未知角色")
    try:
        feishu.set_role(role, body.name, body.contact)
    except feishu.FeishuError as e:
        raise HTTPException(400, str(e))
    return feishu.status()


@app.delete("/api/integrations/feishu/roles/{role}", tags=["集成"])
def api_feishu_role_del(role: str):
    feishu.clear_role(role)
    return feishu.status()


@app.post("/api/integrations/feishu/test", tags=["集成"], summary="给某个角色发一条测试消息")
def api_feishu_test(body: FeishuTest):
    r = feishu.roles().get(body.role)
    if not r:
        raise HTTPException(400, f"还没有设置「{body.role}」对应的飞书成员")
    try:
        feishu.send_text(r["open_id"], f"【经营作战台】测试消息：你已被设置为「{body.role}」，之后相关的协同请求会发到这里。")
    except feishu.FeishuError as e:
        raise HTTPException(400, str(e))
    return dict(ok=True)


@app.post("/api/integrations/feishu/test-card", tags=["集成"], summary="发一张带按钮的测试卡片，验证按钮回调")
def api_feishu_test_card():
    r = feishu.roles().get("我") or next(iter(feishu.roles().values()), None)
    if not r:
        raise HTTPException(400, "请先给「我」设置对应的飞书成员")
    try:
        feishu.send_card(r["open_id"], feishu.build_test_card())
    except feishu.FeishuError as e:
        raise HTTPException(400, str(e))
    return dict(ok=True, sent_at=time.time(), to=r.get("name"))


def _feishu_sync(hid: int, request: Request | None = None, notify_me: bool = True):
    """协同状态变化后：刷新对方飞书里的卡片；把进展通知发起人。失败不影响主流程。"""
    if feishu.enabled():
        notify.sync(hid, notify_me)


# ---------------- 协同（分给同事的步骤） ----------------
@app.get("/api/handoffs", tags=["协同"])
def api_handoffs(ds: str = DS):
    return [collab.decorate_handoff(h, with_context=True) for h in state.list_handoffs(ds_name(ds))]


def _handoff_or_404(hid: int) -> dict:
    h = state.get_handoff(hid)
    if not h:
        raise HTTPException(404, "协同事项不存在")
    return h


@app.get("/api/handoffs/{hid}", tags=["协同"])
def api_handoff(hid: int):
    return collab.decorate_handoff(_handoff_or_404(hid), with_context=True)


@app.post("/api/handoffs/{hid}/send", tags=["协同"], summary="发出未通知的协同（飞书推送或复制文字）")
def api_handoff_send(hid: int, body: HandoffSend, request: Request):
    _handoff_or_404(hid)
    _base_url(request)
    try:
        return notify.send(hid, body.channel, body.message)
    except notify.NotifyError as e:
        raise HTTPException(400, str(e))


@app.post("/api/handoffs/{hid}/remind", tags=["协同"], summary="催一下：给对方飞书发提醒")
def api_handoff_remind(hid: int, request: Request):
    _handoff_or_404(hid)
    _base_url(request)
    try:
        return notify.remind(hid)
    except notify.NotifyError as e:
        raise HTTPException(400, str(e))


@app.post("/api/handoffs/{hid}/reply", tags=["协同"], summary="回复对方的疑问")
def api_handoff_reply(hid: int, body: HandoffReply, request: Request):
    _handoff_or_404(hid)
    _base_url(request)
    try:
        return notify.reply(hid, body.text)
    except notify.NotifyError as e:
        raise HTTPException(400, str(e))


@app.post("/api/handoffs/{hid}/proxy-done", tags=["协同"], summary="代为标记完成（同事迟迟未处理时）")
def api_handoff_proxy(hid: int, request: Request):
    _handoff_or_404(hid)
    _base_url(request)
    try:
        h = collab.proxy_done(hid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    threading.Thread(target=notify.proxied, args=(hid,), daemon=True).start()
    return collab.decorate_handoff(h, with_context=True)


@app.post("/api/handoffs/{hid}/respond", tags=["协同"], summary="同事处理：已完成 / 有疑问")
def api_handoff_respond(hid: int, body: HandoffRespond, request: Request):
    _handoff_or_404(hid)
    try:
        h = collab.respond(hid, body.status, body.note, by=body.by)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 飞书卡片刷新与通知放到后台，避免拖慢响应（飞书要求按钮回调 3 秒内返回）
    _base_url(request)
    threading.Thread(target=_feishu_sync, args=(hid, None), daemon=True).start()
    return collab.decorate_handoff(h, with_context=True)


@app.post("/api/chat/{pid}/todo-draft", tags=["AI"], summary="把一条对话回复整理成待办草稿")
def api_todo_draft(pid: str, body: TodoDraft, ds: str = DS):
    name = ds_name(ds)
    if pid not in data.ds_of(name).product_ids():
        raise HTTPException(404, f"未知商品 {pid}")
    return todos.draft_from_chat(name, pid, body.messages, body.reply)


# ---------------- 报告 ----------------
class ReportGen(BaseModel):
    scene: Literal["weekly", "campaign", "product"]
    params: dict = {}


class TargetPut(BaseModel):
    month: str
    target: float | None = None


def _report_out(r):
    r = dict(r)
    for k in ("params_json", "charts_json", "extra_json"):
        v = r.pop(k, None)
        try:
            r[k[:-5]] = json.loads(v) if v else ({} if k != "charts_json" else {})
        except json.JSONDecodeError:
            r[k[:-5]] = {}
    r["scene_name"] = SCENES.get(r["type"], r["type"])
    return r


@app.get("/api/reports/scenes", tags=["报告中心"], summary="三个报告场景与可选参数")
def api_report_scenes(ds: str = DS):
    return report.scenes(ds_name(ds))


@app.post("/api/reports/generate", tags=["报告中心"], summary="按场景生成报告（SSE 流式）")
def api_report_generate(body: ReportGen, ds: str = DS):
    return sse(report.run(ds_name(ds), body.scene, body.params))


@app.put("/api/reports/target", tags=["报告中心"], summary="设置月度 GMV 目标（周报显示完成进度）")
def api_report_target(body: TargetPut, ds: str = DS):
    name = ds_name(ds)
    state.set_setting(report.target_key(name, body.month), body.target or "")
    return dict(ok=True, month=body.month, target=body.target)


@app.post("/api/reports/weekly", tags=["报告中心"], summary="生成周度经营分析（SSE 流式，兼容旧入口）")
def api_weekly(ds: str = DS):
    return sse(report.run(ds_name(ds), "weekly", {}))


@app.post("/api/reports/product/{pid}", tags=["报告中心"], summary="生成单品诊断报告")
def api_product_report(pid: str, ds: str = DS):
    name = ds_name(ds)
    if pid not in data.ds_of(name).product_ids():
        raise HTTPException(404, f"未知商品 {pid}")
    res = report.generate(name, "product", dict(product_id=pid))
    return dict(ok=True, id=res["id"])


@app.get("/api/reports", tags=["报告中心"])
def api_reports(ds: str = DS):
    return [dict(r, scene_name=SCENES.get(r["type"], r["type"])) for r in state.list_reports(ds_name(ds))]


@app.get("/api/reports/{rid}", tags=["报告中心"])
def api_report(rid: int):
    r = state.get_report(rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    return _report_out(r)


@app.put("/api/reports/{rid}", tags=["报告中心"])
def api_report_put(rid: int, body: ReportPut):
    r = state.get_report(rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    if r["status"] == "published":
        raise HTTPException(400, "已发布的报告不能编辑，请复制为新草稿")
    state.update_report(rid, content=body.content)
    return dict(ok=True)


@app.post("/api/reports/{rid}/publish", tags=["报告中心"])
def api_report_publish(rid: int):
    state.update_report(rid, status="published", published_at=time.time())
    return dict(ok=True)


@app.post("/api/reports/{rid}/copy", tags=["报告中心"])
def api_report_copy(rid: int):
    r = state.get_report(rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    nid = state.add_report(ds=r["ds"], type=r["type"], title=r["title"] + "（副本）", period=r["period"],
                           content=r["content"], status="draft", source=r["source"], params_json=r.get("params_json"),
                           charts_json=r.get("charts_json"), extra_json=r.get("extra_json"))
    return dict(ok=True, id=nid)


# ---------------- Skill 与管理 ----------------
@app.get("/api/skill/download", tags=["基础"], summary="下载 Skill 包（zip）")
def api_skill_zip():
    if not SKILL_DIST.exists():
        raise HTTPException(404, "Skill 包尚未构建，请运行 python skill/build_skill.py")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in SKILL_DIST.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, Path("product-ops-analysis") / f.relative_to(SKILL_DIST))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="product-ops-analysis.zip"'})


@app.post("/api/admin/reset", tags=["基础"], summary="恢复演示起点")
def api_reset(token: Optional[str] = None, x_admin_token: Optional[str] = Header(None)):
    expect = os.environ.get("ADMIN_TOKEN")
    if expect and expect not in (token, x_admin_token):
        raise HTTPException(403, "口令不正确")
    state.reset(data.seed)
    return dict(ok=True)


# ---------------- 前端 ----------------
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (WEB_DIST / path).resolve()
        if path and str(f).startswith(str(WEB_DIST.resolve())) and f.is_file():
            return FileResponse(f)
        return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-cache"})


def main():
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    print(f"服务启动：http://0.0.0.0:{port}  模型状态：{llm_client.mode()}  接口文档：/docs", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning" if os.environ.get("QUIET") == "1" else "info")


if __name__ == "__main__":
    main()
