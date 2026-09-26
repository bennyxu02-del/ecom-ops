import { useEffect, useState } from "react";
import { App, Button, Checkbox, DatePicker, Descriptions, Drawer, Empty, Input, Popconfirm, Space, Tag, Timeline, Typography } from "antd";
import dayjs from "dayjs";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../App";
import { Delta, num } from "../format";
import { HandoffTag } from "./HandoffSendModal";
import { HandoffOps, ago } from "./TodoViews";

const SOURCE_COLOR: Record<string, string> = { diagnosis: "blue", chat: "purple", manual: "default" };
export const SourceTag = ({ a }: { a: any }) => <Tag bordered={false} color={SOURCE_COLOR[a.source] || "default"}>{a.source_name}</Tag>;

export const statusColor = (s: string) => s === "executed" ? "success" : ["rejected", "declined", "cancelled"].includes(s) ? "default" : "processing";

export function isOverdue(a: any, asOf: string) {
  return !!a.due_date && !!asOf && a.due_date < asOf && ["adopted", "transferred"].includes(a.status);
}

const fmtT = (t: number) => dayjs(t * 1000).format("MM-DD HH:mm");

/** 待办详情：轻量编辑（名称、截止日期、备注、状态），查看步骤、协同、效果与操作记录 */
export default function ActionDrawer({ action, onClose, onChanged }: { action: any | null; onClose: () => void; onChanged: (a?: any) => void }) {
  const [a, setA] = useState<any>(action);
  const [note, setNote] = useState("");
  const [cancelReason, setCancelReason] = useState("");
  const { asOf, refreshMeta } = useApp();
  const { message } = App.useApp();

  useEffect(() => { setA(action); setNote(action?.note || ""); }, [action]);
  if (!a) return <Drawer open={false} />;

  const patch = async (body: any, ok?: string) => {
    try {
      const x = await api(`/api/actions/${a.id}`, { method: "PATCH", body });
      setA(x); setNote(x.note || ""); onChanged(x); refreshMeta();
      if (ok) message.success(ok);
    } catch (e: any) { message.error(e.message); }
  };
  const reload = async () => {
    const xs = await api<any[]>(`/api/actions?product_id=${a.product_id}`);
    const x = xs.find(y => y.id === a.id);
    if (x) { setA(x); onChanged(x); }
    refreshMeta();
  };
  const toggleStep = async (i: number, done: boolean) => {
    await api(`/api/actions/${a.id}/steps`, { method: "PATCH", body: { index: i, done } });
    reload();
  };

  const plan = a.plan || {};
  const steps: string[] = plan.steps || [];
  const owners: string[] = plan.step_owners || [];
  const open = ["adopted", "transferred"].includes(a.status);
  const e = a.effect;
  const fv = (v: any) => v == null ? "—" : a.track_metric === "cvr" ? (v * 100).toFixed(2) + "%" : num(v);
  const overdue = isOverdue(a, asOf);

  return (
    <Drawer open={!!action} onClose={onClose} width={Math.min(560, window.innerWidth)} destroyOnClose
      title={<Typography.Text editable={open ? { onChange: v => v.trim() && v !== a.name && patch({ name: v }, "名称已更新"), tooltip: "修改名称" } : false}
        style={{ fontSize: 16, fontWeight: 600 }}>{a.name}</Typography.Text>}
      footer={
        <Space wrap>
          {open && <Button onClick={() => patch({ status: "executed" }, "已标记执行，平台开始跟踪效果")} style={{ borderColor: "#0ca30c", color: "#0ca30c" }}>标记已执行</Button>}
          {open && (
            <Popconfirm title="取消这条待办？" okText="确认取消" cancelText="再想想"
              description={<Input size="small" placeholder="原因（可选）" value={cancelReason} onChange={x => setCancelReason(x.target.value)} style={{ width: 220 }} />}
              onConfirm={() => patch({ status: "cancelled", reason: cancelReason || undefined }, "已取消")}>
              <Button danger>取消待办</Button>
            </Popconfirm>
          )}
          {a.status === "cancelled" && <Button onClick={() => patch({ status: "adopted" }, "已重新打开")}>重新打开</Button>}
        </Space>
      }>
      <Descriptions size="small" column={2} items={[
        { key: "p", label: "商品", children: <Link to={"/product/" + a.product_id} onClick={onClose}>{a.product_name}</Link> },
        { key: "s", label: "状态", children: <Tag bordered={false} color={statusColor(a.status)}>{a.status_name}</Tag> },
        { key: "src", label: "来源", children: <SourceTag a={a} /> },
        { key: "c", label: "原因", children: a.cause_name || "—" },
        { key: "d", label: "截止", children: open
            ? <Space size={6}><DatePicker size="small" value={a.due_date ? dayjs(a.due_date) : null} allowClear={false}
                onChange={d => d && patch({ due_date: d.format("YYYY-MM-DD") }, "截止日期已更新")} />{overdue && <Tag color="error" bordered={false}>已逾期</Tag>}</Space>
            : (a.due_date || "—") },
        { key: "t", label: "采纳 / 执行", children: <span className="num small">{a.adopted_date || "—"} / {a.exec_date || "—"}</span> },
      ]} />
      {a.reject_reason && <div className="muted small" style={{ marginTop: 4 }}>驳回原因：{a.reject_reason}</div>}

      <div className="sec-title">步骤</div>
      {steps.length ? (
        <div className="steps-owned">
          {steps.map((s, i) => {
            const who = owners[i] || "我"; const mine = who === "我";
            const done = mine ? (a.step_done || []).includes(i) || a.status === "executed" : false;
            return (
              <div key={i} className={"so" + (done ? " done" : "")}>
                <span className="idx">{i + 1}</span>
                <Tag bordered={false} color={mine ? "blue" : "orange"} className="who">{who}</Tag>
                <span className="txt">{s}</span>
                {mine && open && <Checkbox checked={done} disabled={a.progress?.approval_pending} onChange={x => toggleStep(i, x.target.checked)}>完成</Checkbox>}
              </div>
            );
          })}
        </div>
      ) : <div className="muted small">{plan.params_text?.join("；") || "—"}</div>}

      {(a.handoffs || []).length > 0 && <>
        <div className="sec-title">协同</div>
        <div style={{ display: "grid", gap: 6 }}>
          {a.handoffs.map((h: any) => (
            <div key={h.id} className="collab-row">
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, flexWrap: "wrap" }}>
                <span>{h.kind_name} · {h.role}{h.assignee ? `（${h.assignee}）` : ""}</span><HandoffTag h={h} />
                <span className="muted small">截止 {h.due}{h.sent_at ? ` · ${h.channel === "feishu" ? "飞书" : "复制"}发出于 ${ago(h.sent_at)}` : ""}</span>
                <span style={{ flex: 1 }} />
                {h.status !== "question" && <HandoffOps h={{ ...h, action: a }} onDone={reload} compact />}
              </div>
              {h.status === "question" && <HandoffOps h={{ ...h, action: a }} onDone={reload} />}
            </div>
          ))}
        </div>
      </>}

      <div className="sec-title">备注</div>
      <Input.TextArea rows={3} value={note} onChange={x => setNote(x.target.value)} placeholder="补充进展、与同事沟通的结果等" maxLength={300} />
      {note !== (a.note || "") && <Button size="small" type="primary" style={{ marginTop: 6 }} onClick={() => patch({ note }, "备注已保存")}>保存备注</Button>}

      <div className="sec-title">效果跟踪 <span className="muted small" style={{ fontWeight: 400 }}>执行前后对比，不等同于严格的因果效果</span></div>
      {e?.metric_name ? (
        <div className="small">{e.metric_name}：<span className="num">{fv(e.before)} → {fv(e.after)}</span>{" "}
          {e.status === "已完成" ? <Delta v={e.change_pct} /> : <span className="muted">{e.status}{e.days_needed ? `（已观察 ${e.days_observed}/${e.days_needed} 天）` : ""}</span>}</div>
      ) : <div className="muted small">执行后开始跟踪</div>}

      <div className="sec-title">操作记录</div>
      {(a.log || []).length ? (
        <Timeline style={{ marginTop: 8 }} items={[...a.log].reverse().map((l: any) => ({
          color: "gray", children: <div className="small"><span className="muted num" style={{ marginRight: 8 }}>{fmtT(l.t)}</span>{l.by !== "我" && <Tag bordered={false}>{l.by}</Tag>}{l.text}</div>,
        }))} />
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无记录" />}

    </Drawer>
  );
}
