import { useState } from "react";
import { App, Button, Card, Checkbox, Empty, Input, Popconfirm, Space, Table, Tag, Tooltip } from "antd";
import { BellOutlined } from "@ant-design/icons";
import { api } from "../api";
import { useApp } from "../App";
import HandoffSendModal, { HandoffTag } from "./HandoffSendModal";

export const ago = (t?: number | null) => {
  if (!t) return "—";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
};

export const STAGE_COLOR: Record<string, string> = { doing: "processing", tracking: "cyan", review: "gold", done: "success", cancelled: "default" };
export const StageTag = ({ a }: { a: any }) => (
  <Tag bordered={false} color={STAGE_COLOR[a.status] || "default"}>{a.stage_name}{a.status === "done" && a.outcome_name ? ` · ${a.outcome_name}` : ""}</Tag>
);

/** 所有待办里分给同事的协同事项（附带所属待办与步骤文字） */
export function flatHandoffs(actions: any[]) {
  return actions.flatMap(a => (a.handoffs || []).map((h: any) => ({
    ...h, action: a, step_texts: (h.steps || []).map((i: number) => (a.plan?.steps || [])[i]).filter(Boolean),
  })));
}

/** 协同事项的操作：未通知 → 发送；已通知 → 催一下 / 代为标记完成；有疑问 → 回复 / 代为标记完成 */
export function HandoffOps({ h, onDone }: { h: any; onDone: () => void }) {
  const [sending, setSending] = useState<any>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const { message } = App.useApp();
  const { refreshMeta } = useApp();
  if (h.action?.status !== "doing") return null;

  const call = async (key: string, path: string, body: any, ok: (x: any) => string) => {
    setBusy(key);
    try { const x = await api<any>(path, { method: "POST", body }); message.success(ok(x)); onDone(); refreshMeta(); }
    catch (e: any) { message.error(e.message); } finally { setBusy(null); }
  };
  const who = h.assignee || h.role;
  const proxy = (
    <Popconfirm title={`代${who}标记完成？`} description="对方飞书卡片会同步为「发起人已确认完成」" okText="确认" cancelText="取消"
      onConfirm={() => call("proxy", `/api/handoffs/${h.id}/proxy-done`, {}, () => "已代为标记完成")}>
      <Button size="small" type="text" loading={busy === "proxy"}>代为标记完成</Button>
    </Popconfirm>
  );

  return (
    <>
      {h.status === "pending" && <Button size="small" type="primary" ghost onClick={() => setSending(h)}>发送</Button>}
      {h.status === "notified" && <Space size={4} wrap>
        <Tooltip title={h.channel === "feishu" ? "给对方飞书发一条提醒" : "对方没有通过飞书接收，无法推送提醒"}>
          <Button size="small" icon={<BellOutlined />} loading={busy === "remind"} disabled={h.channel !== "feishu"}
            onClick={() => call("remind", `/api/handoffs/${h.id}/remind`, undefined, () => `已在飞书提醒${who}`)}>
            催一下{h.remind_count ? `（${h.remind_count}）` : ""}
          </Button>
        </Tooltip>
        {proxy}
      </Space>}
      {h.status === "question" && (
        <div style={{ display: "grid", gap: 6, width: "100%" }}>
          <div className="small" style={{ color: "#8a5a00" }}>{who}的疑问：{h.note || "（未填写）"}</div>
          <Space.Compact style={{ width: "100%" }}>
            <Input size="small" value={reply} onChange={e => setReply(e.target.value)} placeholder="回复对方，会推送到对方飞书"
              onPressEnter={() => reply.trim() && call("reply", `/api/handoffs/${h.id}/reply`, { text: reply }, x => x.reply_pushed ? `回复已推送给${who}` : "已记录回复（对方未绑定飞书，请另行转告）")} />
            <Button size="small" type="primary" loading={busy === "reply"} disabled={!reply.trim()}
              onClick={() => call("reply", `/api/handoffs/${h.id}/reply`, { text: reply }, x => x.reply_pushed ? `回复已推送给${who}` : "已记录回复（对方未绑定飞书，请另行转告）")}>回复</Button>
          </Space.Compact>
          <div>{proxy}</div>
        </div>
      )}
      <HandoffSendModal handoff={sending} onClose={() => setSending(null)} onSent={() => { setSending(null); onDone(); refreshMeta(); }} />
    </>
  );
}

/** 我要做的：待复盘、同事的疑问、未通知的协同、我没勾的步骤 */
export function MineView({ actions, onOpen, reload }: { actions: any[]; onOpen: (id: number) => void; reload: () => void }) {
  const { asOf, refreshMeta } = useApp();
  const review = actions.filter(a => a.status === "review");
  const hs = flatHandoffs(actions).filter(h => h.action.status === "doing" && ["question", "pending"].includes(h.status));
  const steps = actions.filter(a => a.status === "doing").flatMap(a => {
    const owners: string[] = a.plan?.step_owners || [];
    return owners.map((o, i) => ({ o, i })).filter(x => x.o === "我" && !(a.step_done || []).includes(x.i))
      .map(x => ({ key: `${a.id}-${x.i}`, a, i: x.i, text: (a.plan?.steps || [])[x.i] }));
  }).sort((x, y) => (x.a.due_date || "9").localeCompare(y.a.due_date || "9"));
  const tick = async (aid: number, i: number) => {
    await api(`/api/actions/${aid}/steps`, { method: "PATCH", body: { index: i, done: true } });
    reload(); refreshMeta();
  };
  const TodoCell = ({ a }: { a: any }) => <><a onClick={() => onOpen(a.id)}>{a.name}</a><div className="muted small">{a.product_name}</div></>;
  const Due = ({ a }: { a: any }) => <span className="num small">{a.due_date || "—"}{a.overdue && <Tag color="error" bordered={false} style={{ marginLeft: 4 }}>已逾期</Tag>}</span>;
  const empty = !review.length && !hs.length && !steps.length;

  return (
    <div className="grid">
      {review.length > 0 && (
        <Card title={<>待复盘<span className="hint">跟踪期已满，确认结论后待办完成</span></>} styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" size="middle" dataSource={review} pagination={false} scroll={{ x: 760 }} columns={[
            { title: "待办", key: "a", render: (_, a) => <TodoCell a={a} /> },
            { title: "效果", key: "e", width: 260, render: (_, a) => <span className="small">{a.suggestion?.summary || "—"}</span> },
            { title: "AI 建议", key: "s", width: 120, render: (_, a) => <Tag bordered={false} color={a.suggestion?.outcome === "effective" ? "success" : a.suggestion?.outcome === "ineffective" ? "error" : "default"}>{a.suggestion?.outcome_name}</Tag> },
            { title: "", key: "op", width: 100, render: (_, a) => <Button size="small" type="primary" onClick={() => onOpen(a.id)}>去复盘</Button> },
          ]} />
        </Card>
      )}
      {hs.length > 0 && (
        <Card title={<>需要我处理的协同<span className="hint">同事提了疑问，或对方没绑定飞书需要手动发送</span></>} styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" size="middle" dataSource={hs} pagination={false} scroll={{ x: 820 }} columns={[
            { title: "同事", key: "r", width: 110, render: (_, h) => <>{h.role}{h.assignee && <div className="muted small">{h.assignee}</div>}</> },
            { title: "事项", key: "w", render: (_, h) => <><TodoCell a={h.action} /><div className="small sec" style={{ marginTop: 2 }}>{h.step_texts.join("；")}</div></> },
            { title: "状态", key: "s", width: 90, render: (_, h) => <HandoffTag h={h} /> },
            { title: "", key: "op", width: 320, render: (_, h) => <HandoffOps h={h} onDone={reload} /> },
          ]} />
        </Card>
      )}
      {(steps.length > 0 || empty) && (
        <Card title={<>我的步骤<span className="hint">勾选完成；所有步骤完成后自动进入跟踪</span></>} styles={{ body: { padding: 0 } }}>
          {steps.length ? (
            <Table rowKey="key" size="middle" dataSource={steps} pagination={false} scroll={{ x: 760 }} columns={[
              { title: "", key: "c", width: 48, render: (_, x) => <Checkbox onChange={() => tick(x.a.id, x.i)} aria-label="完成" /> },
              { title: "步骤", dataIndex: "text" },
              { title: "所属待办", key: "a", width: 260, render: (_, x) => <TodoCell a={x.a} /> },
              { title: "截止", key: "d", width: 130, render: (_, x) => <Due a={x.a} /> },
            ]} />
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={asOf ? "今天没有要处理的事" : "暂无"} style={{ padding: 24 }} />}
        </Card>
      )}
    </div>
  );
}
