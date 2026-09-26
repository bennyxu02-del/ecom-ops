import { useCallback, useEffect, useRef, useState } from "react";
import { App, Button, Card, Input, Spin, Tag } from "antd";
import { CheckOutlined, FileTextOutlined, ReloadOutlined, SendOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { api, sse } from "../api";
import { useApp } from "../App";
import Markdown from "./Markdown";
import PlanCard from "./PlanCard";
import ReasonModal, { ReasonSpec } from "./ReasonModal";

type Step = { text: string; done: boolean };
type Msg = { role: "user" | "assistant"; text: string; pending?: string | null; unmatched?: string[] };

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
  const [modal, setModal] = useState<{ spec: ReasonSpec; resolve: (v: { option: string; text: string } | null) => void } | null>(null);
  const history = useRef<{ role: string; content: string }[]>([]);
  const abort = useRef<AbortController | null>(null);
  const msgBox = useRef<HTMLDivElement>(null);
  const { message } = App.useApp();
  const { refreshMeta } = useApp();
  const nav = useNavigate();

  const loadActs = useCallback(async () => {
    const xs = await api<any[]>(`/api/actions?product_id=${pid}`);
    setActs(Object.fromEntries(xs.map(a => [a.action_id, a])));
  }, [pid]);

  const run = useCallback(async (refresh: boolean) => {
    abort.current?.abort();
    const ac = new AbortController();
    abort.current = ac;
    setPhase("running"); setSteps([]); setResult(null); setErr(null);
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

  const decide = async (plan: any, decision: "adopt" | "reject") => {
    const body: any = { plan, product_id: pid, card_id: card ? card.id : null, decision };
    if (decision === "reject") {
      const m = await ask({ title: "驳回方案", options: ["方案不适用当前情况", "已有其他处理方式", "成本过高", "其他"], input: true, placeholder: "补充说明（可选）", okText: "确认驳回" });
      if (!m) return;
      body.reason = m.option + (m.text ? "：" + m.text : "");
    } else {
      // 转交单要用到的诊断结论与证据（只取与该方案同一原因的证据）
      const causes = (result?.root_causes || []).filter((c: any) => c.cause === plan.cause || c.cause_name === plan.cause_name);
      body.context = { summary: result?.summary, evidence: (causes.length ? causes : result?.root_causes || []).flatMap((c: any) => c.evidence.map((e: any) => e.text)) };
    }
    await api("/api/actions", { method: "POST", body });
    const needHelp = (plan.handoffs || []).length > 0;
    message.success(decision === "reject" ? "已驳回" : needHelp ? "已采纳：你的步骤已列为待办，需要协同的部分请点「发送」" : "已采纳：完成全部步骤后自动开始跟踪效果");
    loadActs(); refreshMeta();
  };

  const chat = async (text: string | null, preset?: string, plan?: any, label?: string) => {
    if (busy) return;
    if (!preset && !(text || "").trim()) return;
    setBusy(true);
    const shown = preset ? `请起草：${label}` : text!;
    if (!preset) history.current.push({ role: "user", content: text! });
    setQ("");
    setMsgs(m => [...m, { role: "user", text: shown }, { role: "assistant", text: "", pending: "正在思考…" }]);
    const upd = (f: (m: Msg) => Msg) => setMsgs(ms => ms.map((m, i) => (i === ms.length - 1 ? f(m) : m)));
    let out = "";
    try {
      await sse(`/api/chat/${pid}`, { messages: history.current, preset, plan }, ev => {
        if (ev.type === "step") upd(m => ({ ...m, pending: ev.summary }));
        if (ev.type === "delta") { out += ev.text; upd(m => ({ ...m, text: out, pending: null })); }
        if (ev.type === "result") upd(m => ({ ...m, pending: null, unmatched: ev.unmatched_numbers || [] }));
        if (ev.type === "error") upd(m => ({ ...m, pending: null, text: "出错了：" + ev.message }));
      });
    } catch (e: any) { upd(m => ({ ...m, pending: null, text: "出错了：" + e.message })); }
    if (!preset) history.current.push({ role: "assistant", content: out });
    setBusy(false);
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
      <div className="sec-title" style={{ marginTop: 0 }}>分析过程</div>
      <ul className="steps">
        {steps.length === 0 && phase === "running" && <li className="run"><span className="ic"><Spin size="small" /></span>正在读取数据…</li>}
        {steps.map((s, k) => (
          <li key={k} className={s.done ? "" : "run"}><span className="ic">{s.done ? <CheckOutlined /> : <Spin size="small" />}</span><span>{s.text}</span></li>
        ))}
      </ul>
      {err && <div className="verify bad" style={{ marginTop: 12 }}>诊断出错：{err}</div>}
      {r && (
        <>
          <div className="sec-title">结论</div>
          <div className="summary">{r.summary}</div>
          {r.notes?.length > 0 && <div className="small sec" style={{ marginTop: 6 }}>{r.notes.map((n: string, k: number) => <div key={k}>{n}</div>)}</div>}
          {r.root_causes.length > 0 && <div className="sec-title">根因与证据</div>}
          {r.root_causes.map((c: any, k: number) => (
            <div className="cause" key={k}>
              <div className="h">{c.cause_name}
                <Tag bordered={false} color={c.confidence === "强" ? "success" : c.confidence === "中" ? "warning" : "default"}>把握：{c.confidence}</Tag></div>
              <ul>{c.evidence.map((e: any, j: number) => <li key={j}>{e.text}</li>)}</ul>
            </div>
          ))}
          {r.plans.length > 0 && <div className="sec-title">动作方案 <span className="muted small" style={{ fontWeight: 400 }}>来自动作库，参数按本商品数据计算，已检查经营约束</span></div>}
          {r.plans.map((p: any, k: number) => (
            <PlanCard key={p.action_id + k} p={p} i={k} act={acts[p.action_id]} onDecide={decide} onChanged={() => { loadActs(); refreshMeta(); }} onMaterial={(pr, pl, lb) => chat(null, pr, pl, lb)} />
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
          <div className="chat">
            <div className="sec-title" style={{ marginTop: 0 }}>追问</div>
            <div className="msgs" ref={msgBox}>
              {msgs.map((m, k) => (
                <div key={k} className={"m " + (m.role === "user" ? "u" : "a")}>
                  {m.role === "user" ? m.text : (
                    <>
                      {m.pending && <span className="muted"><Spin size="small" /> {m.pending}</span>}
                      {m.text && <Markdown text={m.text} marks={m.unmatched} />}
                      {m.unmatched && m.unmatched.length > 0 && <div className="small" style={{ color: "#7a5000", marginTop: 4 }}>⚠ 未核对到的数字：{m.unmatched.join("、")}</div>}
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
        </>
      )}
      <ReasonModal spec={modal?.spec || null}
        onOk={(option, text) => { modal?.resolve({ option, text }); setModal(null); }}
        onCancel={() => { modal?.resolve(null); setModal(null); }} />
    </Card>
  );
}
