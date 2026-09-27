import { useCallback, useEffect, useRef, useState } from "react";
import { App, Button, Card, Input, Space, Spin, Tag } from "antd";
import { CheckCircleFilled, CheckOutlined, DownOutlined, PlusCircleOutlined, FileTextOutlined, ReloadOutlined, RightOutlined, SendOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { api, sse } from "../api";
import { useApp } from "../App";
import AnalysisPath from "./AnalysisPath";
import ReportBody from "./ReportBody";
import PlanCard from "./PlanCard";
import ReasonModal, { ReasonSpec } from "./ReasonModal";
import TodoModal, { TodoDraft } from "./TodoModal";

type Step = { text: string; done: boolean };
type Msg = { role: "user" | "assistant"; text: string; pending?: string | null; unmatched?: string[]; preset?: boolean; done?: boolean; noai?: boolean; todo?: TodoDraft | null; created?: any; drafting?: boolean; q?: string; dismissed?: boolean; charts?: Record<string, any> };

function StepList({ steps, running }: { steps: Step[]; running: boolean }) {
  return (
    <ul className="steps">
      {steps.length === 0 && running && <li className="run"><span className="ic"><Spin size="small" /></span>正在读取数据…</li>}
      {steps.map((s, k) => (
        <li key={k} className={s.done ? "" : "run"}><span className="ic">{s.done ? <CheckOutlined /> : <Spin size="small" />}</span><span>{s.text}</span></li>
      ))}
    </ul>
  );
}

const Title = () => <div className="ai-title"><span className="spark">AI</span>AI 商品诊断</div>;

/** AI 诊断面板：流式展示分析过程 → 结论/根因/方案 → 处理方案 → 追问与物料起草 */
export default function AIPanel({ detail, card, autoRun }: { detail: any; card: any; autoRun: boolean }) {
  const pid = detail.product_id;
  const [phase, setPhase] = useState<"idle" | "running" | "done">("idle");
  const [steps, setSteps] = useState<Step[]>([]);
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [acts, setActs] = useState<Record<string, any>>({});
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [showSteps, setShowSteps] = useState(false);
  const [adoptPlan, setAdoptPlan] = useState<any>(null);
  const [rejs, setRejs] = useState<Record<string, any>>({});
  const [todoFor, setTodoFor] = useState<{ idx: number; draft: TodoDraft; context: any } | null>(null);
  const [modal, setModal] = useState<{ spec: ReasonSpec; resolve: (v: { option: string; text: string } | null) => void } | null>(null);
  const history = useRef<{ role: string; content: string }[]>([]);
  const abort = useRef<AbortController | null>(null);
  const msgBox = useRef<HTMLDivElement>(null);
  const { message } = App.useApp();
  const { refreshMeta } = useApp();
  const nav = useNavigate();

  const loadActs = useCallback(async () => {
    const [xs, rs] = await Promise.all([api<any[]>(`/api/actions?product_id=${pid}`), api<any[]>(`/api/rejections?product_id=${pid}`)]);
    // 每个方案对应最近一条未取消的待办 / 最近一条驳回记录（接口按时间倒序）
    const am: Record<string, any> = {}, rm: Record<string, any> = {};
    xs.filter(a => a.status !== "cancelled").forEach(a => { if (!am[a.action_id]) am[a.action_id] = a; });
    rs.forEach(r => { if (!rm[r.action_id]) rm[r.action_id] = r; });
    setActs(am); setRejs(rm);
  }, [pid]);

  const run = useCallback(async (refresh: boolean) => {
    abort.current?.abort();
    const ac = new AbortController();
    abort.current = ac;
    setPhase("running"); setSteps([]); setResult(null); setErr(null); setShowSteps(false);
    try {
      await sse(`/api/diagnose/${pid}${refresh ? "?refresh=true" : ""}`, {}, ev => {
        if (ev.type === "step") setSteps(s => [...s.map(x => ({ ...x, done: true })), { text: ev.summary, done: false }]);
        else if (ev.type === "result") {
          setSteps(s => s.map(x => ({ ...x, done: true })));
          setResult(ev.result);
          loadActs();
          setPhase("done");
        } else if (ev.type === "error") { setErr(ev.message); setPhase("done"); }
      }, ac.signal);
    } catch (e: any) {
      if (e.name !== "AbortError") { setErr(e.message); setPhase("done"); }
    }
  }, [pid, loadActs]);

  useEffect(() => { if (autoRun) run(false); return () => abort.current?.abort(); }, [autoRun, run]);
  useEffect(() => { msgBox.current?.scrollTo({ top: msgBox.current.scrollHeight }); }, [msgs]);

  const ask = (spec: ReasonSpec) => new Promise<{ option: string; text: string } | null>(resolve => setModal({ spec, resolve }));

  const adoptContext = (plan: any) => {
    // 协同卡片里的背景：诊断结论 + 与该方案同一原因的证据
    const causes = (result?.root_causes || []).filter((c: any) => c.cause === plan.cause || c.cause_name === plan.cause_name);
    return { summary: result?.summary, evidence: (causes.length ? causes : result?.root_causes || []).flatMap((c: any) => c.evidence.map((e: any) => e.text)) };
  };
  const reject = async (plan: any) => {
    const m = await ask({ title: "驳回方案", options: ["方案不适用当前情况", "已有其他处理方式", "成本过高", "其他"], input: true, placeholder: "补充说明（可选）", okText: "确认驳回" });
    if (!m) return;
    await api("/api/rejections", { method: "POST", body: { product_id: pid, plan, card_id: card ? card.id : null, reason: m.option + (m.text ? "：" + m.text : "") } });
    message.success("已驳回，不会生成待办");
    loadActs();
  };
  const undoReject = async (r: any) => { await api(`/api/rejections/${r.id}`, { method: "DELETE" }); loadActs(); };

  const chat = async (text: string | null, preset?: string, plan?: any, label?: string) => {
    if (busy) return;
    if (!preset && !(text || "").trim()) return;
    setBusy(true);
    const shown = preset ? `请起草：${label}` : text!;
    if (!preset) history.current.push({ role: "user", content: text! });
    setQ("");
    setMsgs(m => [...m, { role: "user", text: shown }, { role: "assistant", text: "", pending: "正在思考…", preset: !!preset, q: preset ? undefined : text! }]);
    const upd = (f: (m: Msg) => Msg) => setMsgs(ms => ms.map((m, i) => (i === ms.length - 1 ? f(m) : m)));
    let out = "";
    try {
      await sse(`/api/chat/${pid}`, { messages: history.current, preset, plan }, ev => {
        if (ev.type === "step") upd(m => ({ ...m, pending: ev.summary }));
        if (ev.type === "delta") { out += ev.text; upd(m => ({ ...m, text: out, pending: null })); }
        if (ev.type === "result") upd(m => ({ ...m, pending: null, unmatched: ev.unmatched_numbers || [], done: true, noai: ev.source === "none", todo: ev.todo || null, charts: ev.charts || {} }));
        if (ev.type === "error") upd(m => ({ ...m, pending: null, text: "出错了：" + ev.message }));
      });
    } catch (e: any) { upd(m => ({ ...m, pending: null, text: "出错了：" + e.message })); }
    if (!preset) history.current.push({ role: "assistant", content: out });
    setBusy(false);
  };

  const setMsg = (idx: number, f: (m: Msg) => Msg) => setMsgs(ms => ms.map((m, i) => (i === idx ? f(m) : m)));
  // 背景：AI 在待办卡片里写的说明（平台还会自动补上诊断结论、数据和预警）
  const todoContext = (d?: TodoDraft | null) => ({ summary: d?.note || undefined });
  const toTodo = async (idx: number) => {
    const m = msgs[idx];
    if (m.todo) { setTodoFor({ idx, draft: m.todo, context: todoContext(m.todo) }); return; }
    setMsg(idx, x => ({ ...x, drafting: true }));
    try {
      const upto = history.current.slice(0, history.current.findIndex(h => h.role === "assistant" && h.content === m.text) + 1);
      const draft = await api<TodoDraft>(`/api/chat/${pid}/todo-draft`, { method: "POST", body: { messages: upto.length ? upto : [{ role: "user", content: m.q || "" }], reply: m.text } });
      setTodoFor({ idx, draft, context: todoContext(draft) });
    } catch (e: any) { message.error(e.message); } finally { setMsg(idx, x => ({ ...x, drafting: false })); }
  };

  const productReport = async () => {
    const x = await api<any>(`/api/reports/product/${pid}`, { method: "POST" });
    nav("/report/" + x.id);
  };

  if (phase === "idle") return (
    <Card className="ai-panel" title={<Title />}>
      <p className="sec" style={{ marginTop: 0 }}>按指标逐层拆解定位原因，关联库存、价格、口碑、投放等证据，给出可以直接执行的动作方案。</p>
      <Button type="primary" onClick={() => run(false)}>开始诊断</Button>
    </Card>
  );

  const r = result;
  const v = r?.verify || {};

  return (
    <Card className="ai-panel" title={<Title />}>
      {!r && <>
        <div className="sec-title" style={{ marginTop: 0 }}>分析过程</div>
        <StepList steps={steps} running={phase === "running"} />
      </>}
      {err && <div className="verify bad" style={{ marginTop: 12 }}>诊断出错：{err}</div>}
      {r && (
        <>
          <div className="ai-cols">
            <div className="ai-col">
              <div className="sec-title" style={{ marginTop: 0 }}>结论</div>
              <div className="summary">{r.summary}</div>
              {r.notes?.length > 0 && <div className="small sec" style={{ marginTop: 6 }}>{r.notes.map((n: string, k: number) => <div key={k}>{n}</div>)}</div>}
              {r.path?.length > 0 && <>
                <div className="sec-title">分析路径 <span className="muted small" style={{ fontWeight: 400 }}>从 GMV 出发逐层拆解，定位到原因</span></div>
                <AnalysisPath layers={r.path} />
              </>}
              {steps.length > 0 && <>
                <a className="small steps-toggle" onClick={() => setShowSteps(x => !x)}>{showSteps ? <DownOutlined /> : <RightOutlined />} AI 实际执行的 {steps.length} 个分析步骤</a>
                {showSteps && <StepList steps={steps} running={false} />}
              </>}
              {r.root_causes.length > 0 && <div className="sec-title">根因与证据</div>}
              {r.root_causes.map((c: any, k: number) => (
                <div className="cause" key={k}>
                  <div className="h">{c.cause_name}
                    <Tag bordered={false} color={c.confidence === "强" ? "success" : c.confidence === "中" ? "warning" : "default"}>把握：{c.confidence}</Tag></div>
                  <ul>{c.evidence.map((e: any, j: number) => <li key={j}>{e.text}</li>)}</ul>
                </div>
              ))}
              <div className="sec-title">数据局限</div>
              <ul className="lim">{r.limitations.map((x: string, k: number) => <li key={k}>{x}</li>)}</ul>
              <div className={"verify " + (v.unmatched_numbers?.length ? "bad" : "ok")} style={{ marginTop: 12 }}>
                {v.unmatched_numbers?.length ? `⚠ 以下数字未能在计算结果中核对到：${v.unmatched_numbers.join("、")}` : "✓ 结论中的数字已逐一核对，均来自平台计算结果"}
                {v.dropped_plans?.length > 0 && <div>已拦截 {v.dropped_plans.length} 个不合规方案</div>}
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                <Button size="small" icon={<ReloadOutlined />} onClick={() => run(true)}>重新诊断</Button>
                <Button size="small" icon={<FileTextOutlined />} onClick={productReport}>生成单品诊断报告</Button>
              </div>
            </div>
            <div className="ai-col">
              {r.plans.length > 0 && <div className="sec-title" style={{ marginTop: 0 }}>动作方案 <span className="muted small" style={{ fontWeight: 400 }}>来自动作库，参数按本商品数据计算，已检查经营约束</span></div>}
              {r.plans.map((p: any, k: number) => (
                <PlanCard key={p.action_id + k} p={p} i={k} act={acts[p.action_id]} rej={rejs[p.action_id]}
              onAdopt={setAdoptPlan} onReject={reject} onUndoReject={undoReject} onMaterial={(pr, pl, lb) => chat(null, pr, pl, lb)} />
              ))}
              {r.plans.length === 0 && <div className="muted small">没有需要执行的方案。</div>}
              <div className="chat">
                <div className="sec-title" style={{ marginTop: 0 }}>追问</div>
                <div className="msgs" ref={msgBox}>
                  {msgs.map((m, k) => (
                    <div key={k} className={"m " + (m.role === "user" ? "u" : "a")}>
                      {m.role === "user" ? m.text : (
                        <>
                          {m.pending && <span className="muted"><Spin size="small" /> {m.pending}</span>}
                          {m.text && <ReportBody text={m.text} charts={m.charts || {}} marks={m.unmatched} compact />}
                          {m.unmatched && m.unmatched.length > 0 && <div className="small" style={{ color: "#7a5000", marginTop: 4 }}>⚠ 未核对到的数字：{m.unmatched.join("、")}</div>}
                      {m.done && !m.noai && !m.preset && m.text && (m.created ? (
                        <div className="m-ops"><CheckCircleFilled style={{ color: "#0ca30c" }} />已加入待办「{m.created.name}」<Link to={`/actions?open=${m.created.id}`}>查看</Link></div>
                      ) : m.todo && !m.dismissed ? (
                        <div className="todo-sug">
                          <div className="h"><PlusCircleOutlined style={{ color: "#7c5cd6" }} />待办：{m.todo.name}</div>
                          <ol>{(m.todo.steps || []).map((st, j) => <li key={j}><Tag bordered={false} color={st.by === "我" ? "blue" : "orange"} style={{ marginRight: 6 }}>{st.by}</Tag>{st.text}</li>)}</ol>
                          <div className="small muted" style={{ marginBottom: 6 }}>截止 {m.todo.due_date}{m.todo.due_is_default ? "（默认 3 天）" : ""} · 完成后 {m.todo.track_days} 天看{({ gmv: "GMV", cvr: "支付转化率", uv: "访客数", aov: "客单价", units: "销量", rating: "评分" } as any)[m.todo.track_metric || "gmv"]}</div>
                          <Space size={6}><Button size="small" type="primary" onClick={() => toTodo(k)}>创建</Button>
                            <Button size="small" onClick={() => setMsg(k, x => ({ ...x, dismissed: true }))}>忽略</Button></Space>
                        </div>
                      ) : (
                        <div className="m-ops"><Button size="small" type="link" style={{ padding: 0 }} icon={<PlusCircleOutlined />} loading={m.drafting} onClick={() => toTodo(k)}>转为待办</Button></div>
                      ))}
                        </>
                      )}
                    </div>
                  ))}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <Input value={q} onChange={e => setQ(e.target.value)} onPressEnter={() => chat(q)} placeholder="例如：如果明天补上货，大概能恢复多少？" disabled={busy} />
                  <Button type="primary" icon={<SendOutlined />} onClick={() => chat(q)} loading={busy}>发送</Button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
      <TodoModal open={!!todoFor} source="chat" productId={pid} draft={todoFor?.draft} context={todoFor?.context}
        onClose={() => setTodoFor(null)}
        onCreated={a => { if (todoFor) setMsg(todoFor.idx, x => ({ ...x, created: a })); setTodoFor(null); loadActs(); }} />
      <TodoModal open={!!adoptPlan} source="diagnosis" productId={pid} plan={adoptPlan} cardId={card ? card.id : null}
        context={adoptPlan ? adoptContext(adoptPlan) : null}
        onClose={() => setAdoptPlan(null)} onCreated={() => { setAdoptPlan(null); loadActs(); refreshMeta(); }} />
      <ReasonModal spec={modal?.spec || null}
        onOk={(option, text) => { modal?.resolve({ option, text }); setModal(null); }}
        onCancel={() => { modal?.resolve(null); setModal(null); }} />
    </Card>
  );
}
