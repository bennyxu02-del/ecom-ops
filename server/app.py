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

from . import collab, data, feishu, llm_client, state, todos  # noqa: E402
from .agent import chat as chat_agent  # noqa: E402
from .agent import diagnose, report  # noqa: E402
from .product_report import render_product_report  # noqa: E402

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


class ActionCreate(BaseModel):
    plan: dict = Field(..., description="诊断结果中的一个方案对象")
    product_id: str
    card_id: Optional[str] = None
    decision: Literal["adopt", "transfer", "reject"]
    reason: Optional[str] = None
    role: Optional[str] = None
    context: Optional[dict] = Field(None, description="诊断结论与证据，用于生成转交单：{summary, evidence: [..]}")


class StepUpdate(BaseModel):
    index: int
    done: bool


class HandoffPut(BaseModel):
    message: Optional[str] = None
    role: Optional[str] = None


class HandoffSend(BaseModel):
    channel: Literal["copy", "feishu"] = "copy"
    message: Optional[str] = None


class HandoffRespond(BaseModel):
    status: Literal["received", "done", "question", "approved", "declined"]
    note: Optional[str] = None
    by: Optional[str] = None


class FeishuRole(BaseModel):
    name: str = ""
    contact: str = Field(..., description="对方飞书账号绑定的手机号或邮箱")


class FeishuTest(BaseModel):
    role: str


class ActionUpdate(BaseModel):
    status: Optional[Literal["executed", "adopted", "transferred", "rejected", "cancelled"]] = None
    exec_date: Optional[str] = None
    name: Optional[str] = None
    due_date: Optional[str] = None
    note: Optional[str] = None
    reason: Optional[str] = None


class TodoStep(BaseModel):
    text: str
    by: str = "我"


class TodoCreate(BaseModel):
    product_id: str
    name: str
    steps: list[TodoStep]
    due_date: Optional[str] = None
    track_metric: Optional[str] = "gmv"
    note: Optional[str] = None
    source: Literal["chat", "manual"] = "manual"
    context: Optional[dict] = None


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
    ds = data.ds_of(name)
    collab.decorate_action(a)
    try:
        a["plan"] = json.loads(a.get("plan_json") or "{}")
    except json.JSONDecodeError:
        a["plan"] = {}
    a.pop("plan_json", None)
    a.pop("context_json", None)
    if a["status"] in ("executed", "transferred", "adopted", "declined"):
        a["effect"] = weekly.effect(ds, a["product_id"], a.get("track_metric") or "gmv", a.get("exec_date"),
                                    variant=a.get("variant"))
    return a


@app.get("/api/products/{pid}", tags=["商品"])
def api_product(pid: str, ds: str = DS):
    name = ds_name(ds)
    if pid not in data.ds_of(name).product_ids():
        raise HTTPException(404, f"未知商品 {pid}")
    d = data.product_detail(name, pid)
    for a in d["actions"]:
        _decorate_action(name, a)
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
@app.get("/api/actions", tags=["行动跟踪"])
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


def _link(hid: int, request: Request | None = None) -> str | None:
    b = _base_url(request)
    return f"{b.rstrip('/')}/#/h/{hid}" if b else None


@app.post("/api/actions", tags=["行动跟踪"])
def api_action_create(body: ActionCreate, request: Request, ds: str = DS):
    name = ds_name(ds)
    d = data.ds_of(name)
    plan = core_actions.annotate(dict(body.plan))
    if body.decision == "reject" and not body.reason:
        raise HTTPException(400, "驳回时需要选择原因")
    status = "rejected" if body.decision == "reject" else "adopted"
    today = d.as_of.strftime("%Y-%m-%d")
    aid = state.add_action(ds=name, card_id=body.card_id, product_id=body.product_id,
                           product_name=d.product(body.product_id)["product_name"], action_id=plan.get("action_id"),
                           name=plan.get("name"), cause=plan.get("cause"), cause_name=plan.get("cause_name"),
                           target=plan.get("target"), plan_json=dumps(plan), exec_type=plan.get("exec_type"),
                           owner_role=plan.get("owner_role"), status=status, reject_reason=body.reason,
                           transfer_role=None, context_json=dumps(body.context or {}),
                           track_metric=(plan.get("track") or {}).get("metric"),
                           variant=(plan.get("params") or {}).get("variant"),
                           adopted_date=today if body.decision != "reject" else None, source="diagnosis",
                           due_date=todos.default_due(name) if body.decision != "reject" else None)
    collab.add_log(aid, "驳回 AI 诊断方案：" + (body.reason or "") if body.decision == "reject" else "采纳 AI 诊断方案")
    if body.decision != "reject":
        collab.create_for_action(name, aid, plan, body.context or {}, _base_url(request))
    if body.card_id and body.decision != "reject":
        state.set_card(name, body.card_id, "done")
    return dict(ok=True, id=aid)


@app.patch("/api/actions/{aid}/steps", tags=["行动跟踪"], summary="勾选 / 取消勾选我的步骤")
def api_action_step(aid: int, body: StepUpdate):
    if not state.get_action(aid):
        raise HTTPException(404, "动作不存在")
    collab.set_step(aid, body.index, body.done)
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


def _feishu_sync(hid: int, request: Request | None = None, notify: bool = True):
    """协同状态变化后：刷新对方飞书里的卡片；把进展通知发起人。失败不影响主流程。"""
    if not feishu.enabled():
        return
    h = collab.decorate_handoff(state.get_handoff(hid), with_context=True)
    try:
        if h.get("ext_id"):
            feishu.update_card(h["ext_id"], feishu.build_card(h, _link(hid, request)))
    except feishu.FeishuError as e:
        print("[feishu] 更新卡片失败：", e, flush=True)
    me = feishu.roles().get("我")
    if notify and me and h["status"] != "sent":
        last = (h.get("history") or [{}])[-1]
        text = (f"【协同进展】{h['role']} {h['status_name']}：{(h.get('action') or {}).get('product_name', '')}"
                f" · {(h.get('action') or {}).get('name', '')}" + (f"\n说明：{last.get('note')}" if last.get("note") else "")
                + (f"\n查看：{_link(hid, request)}" if _link(hid, request) else ""))
        try:
            feishu.send_text(me["open_id"], text)
        except feishu.FeishuError as e:
            print("[feishu] 通知发起人失败：", e, flush=True)


# ---------------- 协同 ----------------
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


@app.put("/api/handoffs/{hid}", tags=["协同"], summary="发送前修改转交单内容")
def api_handoff_put(hid: int, body: HandoffPut):
    _handoff_or_404(hid)
    kw = {k: v for k, v in body.model_dump().items() if v is not None}
    if kw:
        state.update_handoff(hid, **kw)
    return collab.decorate_handoff(state.get_handoff(hid), with_context=True)


@app.post("/api/handoffs/{hid}/send", tags=["协同"])
def api_handoff_send(hid: int, body: HandoffSend, request: Request):
    h0 = _handoff_or_404(hid)
    if body.channel == "feishu":
        r = feishu.roles().get(h0["role"])
        if not feishu.enabled() or not r:
            raise HTTPException(400, f"飞书未配置，或还没有设置「{h0['role']}」对应的飞书成员")
        if body.message:
            state.update_handoff(hid, message=body.message)
        h = collab.decorate_handoff(state.get_handoff(hid), with_context=True)
        h["status"] = "sent"
        try:
            mid = feishu.send_card(r["open_id"], feishu.build_card(h, _link(hid, request)))
        except feishu.FeishuError as e:
            raise HTTPException(400, str(e))
        state.update_handoff(hid, assignee=r.get("name"))
        collab.mark_sent(hid, "feishu", body.message, ext_id=mid)
        return collab.decorate_handoff(state.get_handoff(hid), with_context=True)
    collab.mark_sent(hid, body.channel, body.message)
    return collab.decorate_handoff(state.get_handoff(hid), with_context=True)


@app.post("/api/handoffs/{hid}/respond", tags=["协同"], summary="协同方处理：已接收 / 已完成 / 有疑问 / 批准 / 驳回")
def api_handoff_respond(hid: int, body: HandoffRespond, request: Request):
    _handoff_or_404(hid)
    try:
        h = collab.respond(hid, body.status, body.note, by=body.by)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 飞书卡片刷新与通知放到后台，避免拖慢响应（飞书要求按钮回调 3 秒内返回）
    _base_url(request)  # 在请求线程内记下对外访问地址，供后台线程生成链接
    threading.Thread(target=_feishu_sync, args=(hid, None), daemon=True).start()
    return collab.decorate_handoff(h, with_context=True)


@app.patch("/api/actions/{aid}", tags=["行动跟踪"], summary="编辑待办：名称、截止日期、备注、状态")
def api_action_update(aid: int, body: ActionUpdate, ds: str = DS):
    name = ds_name(ds)
    try:
        todos.update(name, aid, title=body.name, due_date=body.due_date, note=body.note, status=body.status,
                     exec_date=body.exec_date, reason=body.reason)
    except KeyError:
        raise HTTPException(404, "动作不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _decorate_action(name, state.get_action(aid))


@app.post("/api/todos", tags=["行动跟踪"], summary="新建待办（手动 / 来自追问）")
def api_todo_create(body: TodoCreate, request: Request, ds: str = DS):
    name = ds_name(ds)
    try:
        aid = todos.create(name, body.product_id, body.name, [s.model_dump() for s in body.steps], body.due_date,
                           body.track_metric, body.note, body.source, body.context, _base_url(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _decorate_action(name, state.get_action(aid))


@app.post("/api/chat/{pid}/todo-draft", tags=["AI"], summary="把一条追问回复整理成待办草稿")
def api_todo_draft(pid: str, body: TodoDraft, ds: str = DS):
    name = ds_name(ds)
    if pid not in data.ds_of(name).product_ids():
        raise HTTPException(404, f"未知商品 {pid}")
    return todos.draft_from_chat(name, pid, body.messages, body.reply)


# ---------------- 报告 ----------------
@app.post("/api/reports/weekly", tags=["报告中心"], summary="生成周报（SSE 流式）")
def api_weekly(ds: str = DS):
    return sse(report.run(ds_name(ds)))


@app.post("/api/reports/product/{pid}", tags=["报告中心"], summary="生成单品诊断报告")
def api_product_report(pid: str, ds: str = DS):
    name = ds_name(ds)
    d = data.ds_of(name)
    c = state.cache_get(diagnose.cache_key(name, pid))
    card = data.card_for(name, pid, today_only=True)
    res = c["result"] if c else sop.run(d, pid, card=card, tiers=data.tiers(name))[1]
    detail = data.product_detail(name, pid)
    md = render_product_report(detail, res, card)
    rid = state.add_report(ds=name, type="product", title=f"{detail['product_name']} 诊断报告（{detail['as_of']}）",
                           period=detail["as_of"], content=md, status="draft", source=res.get("source", "rules"))
    return dict(ok=True, id=rid)


@app.get("/api/reports", tags=["报告中心"])
def api_reports(ds: str = DS):
    return state.list_reports(ds_name(ds))


@app.get("/api/reports/{rid}", tags=["报告中心"])
def api_report(rid: int):
    r = state.get_report(rid)
    if not r:
        raise HTTPException(404, "报告不存在")
    return r


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
                           content=r["content"], status="draft", source=r["source"])
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
