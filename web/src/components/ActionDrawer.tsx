import { useEffect, useState } from "react";
import { App, Button, Checkbox, DatePicker, Descriptions, Drawer, Empty, Input, Popconfirm, Radio, Space, Steps, Tag, Timeline, Typography } from "antd";
import dayjs from "dayjs";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../App";
import { Delta, num } from "../format";
import { HandoffTag } from "./HandoffSendModal";
import { HandoffOps, StageTag, ago } from "./TodoViews";

const SOURCE_COLOR: Record<string, string> = { diagnosis: "blue", chat: "purple", manual: "default" };
export const SourceTag = ({ a }: { a: any }) => <Tag bordered={false} color={SOURCE_COLOR[a.source] || "default"}>{a.source_name}</Tag>;
const STAGES = ["doing", "tracking", "review", "done"];
const OUTCOMES: [string, string][] = [["effective", "有效"], ["ineffective", "无效"], ["unknown", "无法判断"]];
const fmtT = (t: number) => dayjs(t * 1000).format("MM-DD HH:mm");

/** 待办详情：阶段条 → 当前阶段的操作 → 步骤与分工 → 基本信息与背景 → 效果 → 备注与记录 */
export default function ActionDrawer({ action, onClose, onChanged }: { action: any | null; onClose: () => void; onChanged: (a?: any) => void }) {
  const [a, setA] = useState<any>(action);
  const [note, setNote] = useState("");
  const [cancelReason, setCancelReason] = useState("");
  const [outcome, setOutcome] = useState<string>("");
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  const { refreshMeta } = useApp();
  const { message } = App.useApp();

  useEffect(() => {
    setA(action); setNote(action?.note || "");
    setOutcome(action?.suggestion?.outcome || ""); setSummary(action?.suggestion?.summary || "");
  }, [action]);
  if (!a) return <Drawer open={false} />;

  const apply = (x: any, ok?: string) => { setA(x); setNote(x.note || ""); onChanged(x); refreshMeta(); if (ok) message.success(ok); };
  const call = async (method: string, path: string, body?: any, ok?: string) => {
    setBusy(true);
    try { apply(await api(path, { method, body }), ok); } catch (e: any) { message.error(e.message); } finally { setBusy(false); }
  };
  const reload = async () => {
    const xs = await api<any[]>(`/api/actions?product_id=${a.product_id}`);
    const x = xs.find(y => y.id === a.id);
    if (x) apply(x);
  };

  const plan = a.plan || {};
  const steps: string[] = plan.steps || [];
  const owners: string[] = plan.step_owners || [];
  const doing = a.status === "doing";
  const hOf = (k: number) => (a.handoffs || []).find((h: any) => (h.steps || []).includes(k));
  const firstOf = (h: any, k: number) => h && (h.steps || [])[0] === k;
  const e = a.effect;
  const fv = (v: any) => v == null ? "—" : a.track_metric === "cvr" ? (v * 100).toFixed(2) + "%" : num(v);
  const sg = a.suggestion;

  return (
    <Drawer open={!!action} onClose={onClose} width={Math.min(600, window.innerWidth)} destroyOnClose
      title={<Typography.Text editable={!["done", "cancelled"].includes(a.status) ? { onChange: v => v.trim() && v !== a.name && call("PATCH", `/api/actions/${a.id}`, { name: v }, "名称已更新"), tooltip: "修改名称" } : false}
        style={{ fontSize: 16, fontWeight: 600 }}>{a.name}</Typography.Text>}
      footer={doing ? (
        <Popconfirm title="取消这条待办？" okText="确认取消" cancelText="再想想"
          description={<Input size="small" placeholder="原因（可选）" value={cancelReason} onChange={x => setCancelReason(x.target.value)} style={{ width: 220 }} />}
          onConfirm={() => call("POST", `/api/actions/${a.id}/cancel`, { reason: cancelReason || undefined }, "已取消")}>
          <Button danger>取消待办</Button>
        </Popconfirm>
      ) : null}>

      {a.status === "cancelled"
        ? <div className="verify bad">已取消{a.cancel_reason ? `：${a.cancel_reason}` : ""}</div>
        : <Steps size="small" current={STAGES.indexOf(a.status)} status={a.status === "done" ? "finish" : "process"}
            items={[{ title: "执行中" }, { title: "跟踪中" }, { title: "待复盘" }, { title: a.status === "done" && a.outcome_name ? `已完成 · ${a.outcome_name}` : "已完成" }]} />}

      {a.status === "tracking" && (
        <div className="stage-box">
          <div className="small">完成后跟踪 {a.track_days} 天{e ? `，已观察 ${e.days_observed ?? 0} 天` : ""}；期满自动进入待复盘。</div>
          <Popconfirm title="提前结束跟踪？" description="比如期间有大促干扰，没法判断效果" okText="提前结束" cancelText="继续跟踪"
            onConfirm={() => call("POST", `/api/actions/${a.id}/end-tracking`, undefined, "已进入待复盘")}>
            <Button size="small">提前结束跟踪</Button>
          </Popconfirm>
        </div>
      )}

      {a.status === "review" && sg && (
        <div className="stage-box review">
          <div className="small sec">执行前 {sg.days} 天 vs 执行后 {sg.days} 天</div>
          <div style={{ fontSize: 15, fontWeight: 600, margin: "4px 0" }}>{sg.summary || "没有可比数据"}</div>
          <div className="small">AI 建议：<Tag bordered={false} color={sg.outcome === "effective" ? "success" : sg.outcome === "ineffective" ? "error" : "default"}>{sg.outcome_name}</Tag>{sg.reason}</div>
          {sg.events?.length > 0 && <div className="small muted">跟踪期内的事件：{sg.events.map((x: any) => `${x.date.slice(5)} ${x.description}`).join("；")}</div>}
          <div className="small muted">执行前后对比，不等同于严格的因果效果。</div>
          <Radio.Group value={outcome} onChange={x => setOutcome(x.target.value)} style={{ marginTop: 8 }}
            options={OUTCOMES.map(([v, l]) => ({ value: v, label: l }))} optionType="button" buttonStyle="solid" size="small" />
          <Input.TextArea rows={2} value={summary} onChange={x => setSummary(x.target.value)} placeholder="一句话总结" style={{ marginTop: 8 }} maxLength={200} />
          <Button type="primary" size="small" loading={busy} disabled={!outcome} style={{ marginTop: 8 }}
            onClick={() => call("POST", `/api/actions/${a.id}/review`, { outcome, note: summary }, "复盘完成，待办已完成")}>确认复盘</Button>
        </div>
      )}

      <div className="sec-title">步骤与分工</div>
      <div className="steps-owned">
        {steps.map((s, k) => {
          const who = owners[k] || "我";
          const mine = who === "我";
          const h = mine ? null : hOf(k);
          const done = mine ? (a.step_done || []).includes(k) || !doing && a.status !== "cancelled" : h?.status === "done";
          return (
            <div key={k}>
              <div className={"so" + (done ? " done" : "")}>
                <span className="idx">{k + 1}</span>
                <Tag bordered={false} color={mine ? "blue" : "orange"} className="who">{who}</Tag>
                <span className="txt">{s}</span>
                {mine && doing && <Checkbox checked={(a.step_done || []).includes(k)}
                  onChange={x => call("PATCH", `/api/actions/${a.id}/steps`, { index: k, done: x.target.checked })}>完成</Checkbox>}
                {h && <HandoffTag h={h} />}
              </div>
              {h && firstOf(h, k) && h.status !== "done" && h.status !== "cancelled" && (
                <div className="so-ops">
                  <span className="muted small">{h.assignee ? `${h.assignee} · ` : ""}截止 {h.due}{h.sent_at ? ` · ${h.channel === "feishu" ? "飞书" : "复制"}通知于 ${ago(h.sent_at)}` : ""}</span>
                  <HandoffOps h={{ ...h, action: a }} onDone={reload} />
                </div>
              )}
            </div>
          );
        })}
      </div>

      <Descriptions size="small" column={2} style={{ marginTop: 16 }} items={[
        { key: "p", label: "商品", children: <Link to={"/product/" + a.product_id} onClick={onClose}>{a.product_name}</Link> },
        { key: "st", label: "阶段", children: <StageTag a={a} /> },
        { key: "src", label: "来源", children: <SourceTag a={a} /> },
        { key: "c", label: "原因", children: a.cause_name || "—" },
        { key: "d", label: "截止", children: doing
            ? <Space size={6}><DatePicker size="small" value={a.due_date ? dayjs(a.due_date) : null} allowClear={false}
                onChange={d => d && call("PATCH", `/api/actions/${a.id}`, { due_date: d.format("YYYY-MM-DD") }, "截止日期已更新，已同步给同事")} />
                {a.overdue && <Tag color="error" bordered={false}>已逾期</Tag>}</Space>
            : (a.due_date || "—") },
        { key: "t", label: "跟踪", children: `完成后 ${a.track_days} 天看${plan.track?.metric_name || e?.metric_name || ""}` },
        { key: "x", label: "创建 / 执行", children: <span className="num small">{a.adopted_date || "—"} / {a.exec_date || "—"}</span> },
        { key: "cl", label: "完成日期", children: a.closed_date || "—" },
      ]} />

      {(a.context?.summary || a.context?.evidence?.length > 0) && <>
        <div className="sec-title">背景</div>
        <div className="small">{a.context.summary}</div>
        {a.context.evidence?.length > 0 && <ul className="small sec" style={{ margin: "4px 0 0", paddingLeft: 18 }}>{a.context.evidence.slice(0, 4).map((x: string, k: number) => <li key={k}>{x}</li>)}</ul>}
      </>}

      {e?.metric_name && a.status !== "review" && <>
        <div className="sec-title">效果 <span className="muted small" style={{ fontWeight: 400 }}>执行前后对比，不等同于严格的因果效果</span></div>
        <div className="small">{e.metric_name}：<span className="num">{fv(e.before)} → {fv(e.after)}</span>{" "}
          {e.status === "已完成" ? <Delta v={e.change_pct} /> : <span className="muted">{e.status}</span>}</div>
        {a.review_note && <div className="small sec" style={{ marginTop: 4 }}>复盘总结：{a.review_note}</div>}
      </>}

      <div className="sec-title">备注</div>
      <Input.TextArea rows={2} value={note} onChange={x => setNote(x.target.value)} placeholder="补充进展、与同事沟通的结果等" maxLength={300} />
      {note !== (a.note || "") && <Button size="small" type="primary" style={{ marginTop: 6 }} onClick={() => call("PATCH", `/api/actions/${a.id}`, { note }, "备注已保存")}>保存备注</Button>}

      <div className="sec-title">操作记录</div>
      {(a.log || []).length ? (
        <Timeline style={{ marginTop: 8 }} items={[...a.log].reverse().map((l: any) => ({
          color: "gray", children: <div className="small"><span className="muted num" style={{ marginRight: 8 }}>{fmtT(l.t)}</span>{l.by !== "我" && <Tag bordered={false}>{l.by}</Tag>}{l.text}</div>,
        }))} />
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无记录" />}
    </Drawer>
  );
}
