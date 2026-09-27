import { useEffect, useRef, useState } from "react";
import { App, Button, Drawer, Dropdown, Input, InputNumber, Modal, Select, Spin, Tag, Timeline, Tooltip } from "antd";
import { ArrowRightOutlined, CheckOutlined, LoadingOutlined, RedoOutlined, SendOutlined, CalculatorOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { api, sse } from "../api";
import { useApp } from "../App";
import { Sev } from "../format";
import Markdown from "./Markdown";
import { Estimate } from "./PlanCard";
import ReasonModal from "./ReasonModal";
import ReportChart from "./ReportChart";
import TodoModal, { TRACK_METRICS } from "./TodoModal";

type Msg = { role: "user" | "assistant"; content: string; steps?: string[]; plan_updated?: boolean; decision?: any; pending?: boolean; unmatched?: string[] };

export const STATUS_COLOR: Record<string, string> = {
  pending: "error", doing: "processing", watch: "warning", resolved: "success", closed: "default", recovered: "success",
};

/** 当前方案卡：AI 初版或对话调整后的方案 */
function CurrentPlan({ p, flash, rec, onTrack }: { p: any; flash: boolean; rec?: string; onTrack?: (metric: string, name: string, days: number) => void }) {
  const owners: string[] = p.step_owners || [];
  return (
    <div className={"plan cur-plan" + (flash ? " flash" : "")}>
      <div className="ph">
        <span className="n">{p.name}</span>
        {p.adjusted ? <Tag bordered={false} color="purple">按你的要求调整过</Tag> : <Tag bordered={false}>AI 推荐</Tag>}
        {p.cause_name && <Tag bordered={false}>{p.cause_name}</Tag>}
      </div>
      <div className="pb">
        {p.adjusted && p.changes?.length > 0 && (
          <div className="changes">相比 AI 初版{p.base_name ? `「${p.base_name}」` : ""}：{p.changes.join("；")}</div>)}
        {(p.params_text || []).length > 0 && <div className="params">{p.params_text.map((t: string) => <span key={t}>{t}</span>)}</div>}
        <Estimate e={p.estimate} />
        {(p.risk_notes || []).length > 0 && <div className="verify warn">风险提示：{p.risk_notes.join("；")}</div>}
        {p.checks?.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {p.checks.map((c: any) => (
              <Tooltip key={c.name} title={c.detail}><Tag bordered={false} color={c.passed ? "success" : "warning"}>{c.passed ? "✓" : "!"} {c.name}</Tag></Tooltip>))}
          </div>)}
        <div className="steps-owned">
          {(p.steps || []).map((s: string, k: number) => {
            const who = owners[k] || "我";
            return (
              <div key={k} className="so">
                <span className="idx">{k + 1}</span>
                <Tag bordered={false} color={who === "我" ? "blue" : "orange"} className="who">{who}</Tag>
                <span className="txt">{s}</span>
              </div>);
          })}
        </div>
        {onTrack ? (() => {
          const opts = [...(p.track?.metric && !TRACK_METRICS.some(([v]) => v === p.track.metric) ? [[p.track.metric, p.track.metric_name]] : []), ...TRACK_METRICS];
          return (
            <div className="small sec track-edit">截止：{p.due_days ?? 3} 天内　·　跟踪：完成后
              <InputNumber size="small" min={3} max={30} value={p.track?.days} style={{ width: 64 }}
                onChange={v => v && onTrack(p.track.metric, p.track.metric_name, Number(v))} />天看
              <Select size="small" value={p.track?.metric} style={{ width: 130 }} popupMatchSelectWidth={false}
                options={opts.map(([v, l]) => ({ value: v, label: l }))}
                onChange={v => onTrack(v, (opts.find(([k]) => k === v) || [v, v])[1] as string, p.track?.days || 7)} />
              {rec && p.track?.metric === rec ? <Tag bordered={false} color="purple">AI 推荐</Tag>
                : <Tooltip title="复盘时用这个指标对比执行前后"><span className="muted">已手动修改</span></Tooltip>}
            </div>);
        })() : <div className="small sec">截止：{p.due_days ?? 3} 天内　·　跟踪：完成后 {p.track?.days} 天看{p.track?.metric_name}</div>}
      </div>
    </div>
  );
}

function StatusLine({ c }: { c: any }) {
  const last = (c.log || []).slice(-1)[0];
  if (c.status === "pending") {
    return c.reopen_name ? <div className="verify warn">回到待决定：{c.reopen_name}{last ? `（${last.text}）` : ""}</div> : null;
  }
  if (c.status === "doing") {
    const t = c.todo;
    return <div className="status-line doing">处理中：已转待办{t ? <>「<Link to={`/actions?open=${t.id}`}>{t.name}</Link>」· {t.stage_name}</> : ""}。待办复盘后，这里会自动更新。</div>;
  }
  if (c.status === "watch") return <div className="status-line watch">观察中：到 {c.watch_until?.slice(5)}。到期还在触发会重新推送；指标回来了自动关闭。</div>;
  if (c.status === "closed") {
    const who = c.auto_decided ? "系统自动关闭" : "已关闭";
    return <div className="status-line closed">{who} · {c.decision_name}：{c.reason}</div>;
  }
  return <div className="status-line ok">{c.status_name}{last ? `：${last.text}` : ""}</div>;
}

/** 预警处理面板：出了什么事 → AI 初判和方案 → 对话调方案 → 做一个决定 */
export default function AlertPanel({ cid, onClose, onChanged, onOpen }: {
  cid: string | null; onClose: () => void; onChanged: () => void; onOpen: (id: string) => void;
}) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [plan, setPlan] = useState<any>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState(false);
  const [suggested, setSuggested] = useState<any>(null);
  const [known, setKnown] = useState<string | null>(null);
  const [ignoring, setIgnoring] = useState(false);
  const [todoOpen, setTodoOpen] = useState(false);
  const [decided, setDecided] = useState(false);
  const [recTrack, setRecTrack] = useState<string | undefined>();
  const chatEnd = useRef<HTMLDivElement>(null);
  const chatN = useRef(-1);
  const { message } = App.useApp();
  const { refreshMeta } = useApp();
  const nav = useNavigate();

  const apply = (x: any) => {
    chatN.current = (x.messages || []).length;
    setD(x); setPlan(x.plan); setRecTrack(x.plan?.track?.metric); setMsgs((x.messages || []).map((m: any) => ({ ...m }))); setSuggested(x.suggested || null);
  };
  const toTop = () => document.querySelector(".alert-panel .ant-drawer-body")?.scrollTo({ top: 0, behavior: "smooth" });
  useEffect(() => {
    if (!cid) return;
    setD(null); setErr(null); setDecided(false); setInput("");
    api(`/api/alerts/${cid}`).then(apply).catch(e => setErr(e.message));
  }, [cid]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {      // 只在对话里新增消息时滚到底部；打开面板时停在顶部
    if (msgs.length > chatN.current && chatN.current >= 0) chatEnd.current?.scrollIntoView({ block: "nearest" });
    chatN.current = Math.max(chatN.current, msgs.length);
  }, [msgs]);

  const c = d?.card;
  const decidable = c && ["pending", "watch"].includes(c.status);

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    const hist = [...msgs.filter(m => !m.pending).map(m => ({ role: m.role, content: m.content })), { role: "user" as const, content: text.trim() }];
    setMsgs([...msgs, { role: "user", content: text.trim() }, { role: "assistant", content: "", steps: [], pending: true }]);
    setInput(""); setBusy(true);
    let acc = "";
    const upd = (f: (m: Msg) => Msg) => setMsgs(ms => ms.map((m, i) => i === ms.length - 1 ? f(m) : m));
    try {
      await sse(`/api/alerts/${cid}/chat`, { messages: hist, plan }, ev => {
        if (ev.type === "step") upd(m => ({ ...m, steps: [...(m.steps || []), ev.summary] }));
        if (ev.type === "delta") { acc += ev.text; upd(m => ({ ...m, content: acc })); }
        if (ev.type === "result") {
          upd(m => ({ ...m, content: ev.text, pending: false, plan_updated: !!ev.plan, decision: ev.decision, unmatched: ev.unmatched_numbers }));
          if (ev.plan) { setPlan(ev.plan); setRecTrack(ev.plan.track?.metric); setFlash(true); setTimeout(() => setFlash(false), 1600); }
          if (ev.decision) setSuggested(ev.decision);
        }
        if (ev.type === "error") upd(m => ({ ...m, content: "出错了：" + ev.message, pending: false }));
      });
    } catch (e: any) { upd(m => ({ ...m, content: "出错了：" + e.message, pending: false })); }
    setBusy(false);
  };

  const resetChat = async () => { apply(await api(`/api/alerts/${cid}/chat`, { method: "DELETE" })); message.success("方案已回到 AI 初版"); };

  const decide = async (type: string, reason?: string, days?: number) => {
    try {
      apply(await api(`/api/alerts/${cid}/decide`, { method: "POST", body: { type, reason, days } }));
      setDecided(true); onChanged(); refreshMeta(); toTop();
      message.success(type === "watch" ? `已设为观察 ${days} 天` : "预警已关闭");
    } catch (e: any) { message.error(e.message); }
  };

  const reload = async () => { apply(await api(`/api/alerts/${cid}`)); onChanged(); refreshMeta(); toTop(); };

  const quick = d?.quick || [];
  const others = (d?.plans || []).filter((p: any) => p.name !== plan?.name);
  const diag = d?.diagnosis;

  return (
    <Drawer open={!!cid} onClose={onClose} width={Math.min(760, window.innerWidth)} destroyOnClose className="alert-panel"
      title={c ? (
        <div className="ap-head">
          <div className="ap-t"><Sev s={c.severity} /><span>{c.product_name}</span>
            <Tag bordered={false} color={STATUS_COLOR[c.status]}>{c.status_name}</Tag>
            {c.kind === "opportunity" && <Tag bordered={false} color="green">机会</Tag>}</div>
          <div className="ap-sub">{c.rule_names.join(" · ")} · {c.first_date.slice(5)} 起，已持续 {c.days_open} 天
            {d.product && <Link to={`/product/${c.product_id}`} onClick={onClose} style={{ marginLeft: 10 }}>查看商品详情 →</Link>}</div>
        </div>) : "预警"}
      footer={c ? (
        <div className="ap-foot">
          {decidable && !decided ? (<>
            <Button type="primary" disabled={!plan || !d.can_todo} onClick={() => setTodoOpen(true)}>用当前方案转待办</Button>
            <Button onClick={() => setKnown(suggested?.type === "known" ? suggested.reason : "")}>已知原因</Button>
            <Button onClick={() => setIgnoring(true)}>忽略</Button>
            <Dropdown menu={{ items: (d.watch_days || [1, 3, 7]).map((n: number) => ({ key: String(n), label: `观察 ${n} 天` })),
              onClick: e => decide("watch", suggested?.type === "watch" ? suggested.reason : undefined, Number(e.key)) }}>
              <Button>先观察…</Button>
            </Dropdown>
          </>) : <span className="small sec"><CheckOutlined style={{ color: "#1f9d6b" }} /> {c.status_name}{c.decision_name ? ` · ${c.decision_name}` : ""}</span>}
          <span style={{ flex: 1 }} />
          {d.next_id && <Button type={decidable && !decided ? "default" : "primary"} onClick={() => onOpen(d.next_id)}>下一条预警 <ArrowRightOutlined /></Button>}
        </div>) : null}>
      {!d ? (err ? <div className="verify bad">{err}</div> : <div className="empty"><Spin /></div>) : (<>
        <StatusLine c={c} />

        <div className="sec-title">① 出了什么事</div>
        <div className="ap-facts">
          <div className="headline">{d.facts.headline}</div>
          <div className="kpis">{d.facts.kpis.map((k: any) => <div key={k.label}><small>{k.label}</small><b>{k.value}</b></div>)}</div>
          {(d.facts.in_transit || []).map((t: any) => (
            <div key={t.variant} className="transit">在途：{t.variant} {t.qty ? `${Math.round(t.qty).toLocaleString("zh-CN")} 件，` : ""}{t.date.slice(5).replace("-", "/")} 到货（{t.days} 天后）</div>))}
          {d.facts.plateau && <div className="verify warn">{d.facts.plateau}</div>}
          {d.facts.expected && <div className="expected-tag">{d.facts.expected.reason}</div>}
          {d.chart && <ReportChart spec={d.chart} compact />}
        </div>

        {diag && (<>
          <div className="sec-title">② AI 初判 {diag.source !== "rules" ? <Tag bordered={false} color="blue">AI 诊断</Tag> : <Tag bordered={false}>规则诊断</Tag>}</div>
          <div className="ap-diag">
            <div>{diag.summary}</div>
            {(diag.root_causes || []).map((rc: any) => (
              <div key={rc.cause} className="rc"><b>{rc.cause_name}</b><Tag bordered={false} style={{ marginLeft: 6 }}>把握：{rc.confidence}</Tag>
                <ul>{(rc.evidence || []).map((e: any, i: number) => <li key={i}>{e.text}</li>)}</ul></div>))}
            {diag.excluded?.length > 0 && <div className="small muted">已排除：{diag.excluded.map((x: string) => x.split("：")[0]).join("、")}</div>}
          </div>
        </>)}

        {d.can_todo && (decidable || c.status === "doing") && (<>
          <div className="sec-title">{decidable ? "当前方案" : "转待办时的方案"}
            {decidable && (d.plan_is_adjusted || plan?.adjusted) ? <Button size="small" type="link" icon={<RedoOutlined />} onClick={resetChat}>回到 AI 初版</Button> : null}</div>
          {plan ? <CurrentPlan p={plan} flash={flash} rec={recTrack}
            onTrack={decidable ? (metric, name, days) => setPlan({ ...plan, track: { ...plan.track, metric, metric_name: name, days, ai_metric: recTrack } }) : undefined} /> : <div className="muted small">动作库里没有匹配的方案，可以在下面告诉 AI 你想怎么做。</div>}
          {others.length > 0 && decidable && (
            <div className="alt-plans small">其他方案：{others.map((p: any) => <Button key={p.name} size="small" onClick={() => { setPlan(p); setRecTrack(p.track?.metric); }}>{p.name}</Button>)}</div>)}
        </>)}

        {decidable && (<>
          <div className="sec-title">③ 和 AI 对话调方案<span className="hint">告诉 AI 实际情况、改参数、换方案；数字由平台重新测算</span></div>
          <div className="ap-chat">
            {msgs.map((m, i) => (
              <div key={i} className={"msg " + m.role}>
                {m.role === "assistant" && (m.steps || []).map((s, k) => <div key={k} className="tool-step"><CalculatorOutlined /> {s}</div>)}
                {m.content ? <Markdown text={m.content} /> : m.pending ? <LoadingOutlined /> : null}
                {m.plan_updated && <div className="note">方案卡已更新</div>}
                {m.decision && <div className="note">AI 建议：{m.decision.type_name}{m.decision.reason ? `：${m.decision.reason}` : ""}{m.decision.days ? `（${m.decision.days} 天）` : ""}</div>}
                {m.unmatched && m.unmatched.length > 0 && <div className="note warn">以下数字未能与数据核对：{m.unmatched.join("、")}</div>}
              </div>))}
            <div ref={chatEnd} />
            {suggested && (
              <div className="suggest">
                <span>AI 建议决定：<b>{suggested.type_name}</b>{suggested.reason ? `：${suggested.reason}` : ""}</span>
                <Button size="small" type="primary" ghost onClick={() => suggested.type === "known" ? setKnown(suggested.reason)
                  : suggested.type === "watch" ? decide("watch", suggested.reason, suggested.days) : setIgnoring(true)}>采用</Button>
              </div>)}
            <div className="quick">{quick.map((q: string) => <Button key={q} size="small" disabled={busy} onClick={() => send(q)}>{q}</Button>)}</div>
            <div className="input">
              <Input.TextArea value={input} onChange={e => setInput(e.target.value)} autoSize={{ minRows: 1, maxRows: 4 }} disabled={busy}
                placeholder="例如：券改成 15 元，再加个赠品 / 补货已经安排了，9/22 到" onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); send(input); } }} />
              <Button type="primary" icon={<SendOutlined />} loading={busy} onClick={() => send(input)} />
            </div>
          </div>
        </>)}

        {c.todos?.length > 0 && (<>
          <div className="sec-title">关联待办</div>
          {c.todos.map((t: any) => (
            <div key={t.id} className="linked-todo" onClick={() => nav(`/actions?open=${t.id}`)}>
              <span>{t.name}</span><Tag bordered={false}>{t.stage_name}{t.outcome_name ? ` · ${t.outcome_name}` : ""}</Tag>
            </div>))}
        </>)}

        <div className="sec-title">处理记录</div>
        {c.log?.length ? (
          <Timeline style={{ marginTop: 8 }} items={[...c.log].reverse().map((x: any) => ({
            children: <span className="small"><span className="muted">{x.date?.slice(5)} · {x.by}</span>　{x.text}</span> }))} />
        ) : <div className="muted small">{c.auto_decided ? c.reason : "还没有处理记录"}</div>}
      </>)}

      <TodoModal open={todoOpen} source="alert" productId={c?.product_id} plan={plan} cardId={cid}
        context={plan?.adjusted && plan?.changes?.length ? { summary: "按业务要求调整过：" + plan.changes.join("；") } : undefined}
        onClose={() => setTodoOpen(false)}
        onCreated={() => { setTodoOpen(false); setDecided(true); reload(); message.success("已转待办，预警变为处理中"); }} />
      <Modal open={known !== null} title="已知原因" okText="关闭预警" cancelText="取消" onCancel={() => setKnown(null)}
        onOk={() => { if (!known?.trim()) { message.warning("请写一句原因"); return; } decide("known", known.trim()); setKnown(null); }}>
        <p className="small sec">写一句原因，预警会关闭；以后同一商品升级了还会重新提醒。</p>
        <Input.TextArea rows={3} value={known || ""} onChange={e => setKnown(e.target.value)} placeholder="例如：补货已安排，9/22 到货" />
      </Modal>
      <ReasonModal spec={ignoring ? { title: "忽略这条预警", options: d?.ignore_reasons || ["正常波动", "阈值太严", "数据有误", "其他"], input: true,
        placeholder: "补充说明（选「其他」时必填）", okText: "确认忽略", requireTextFor: "其他" } : null}
        onOk={(o, t) => { setIgnoring(false); decide("ignore", o + (t ? "：" + t : "")); }} onCancel={() => setIgnoring(false)} />
    </Drawer>
  );
}
