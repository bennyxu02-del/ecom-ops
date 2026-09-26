import { useState } from "react";
import { App, Button, Card, Checkbox, Empty, Input, Space, Table, Tag, Tooltip } from "antd";
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

/** 待办下的协同事项（附带所属待办），供两个视图共用 */
export function flatHandoffs(actions: any[]) {
  return actions.flatMap(a => (a.handoffs || []).map((h: any) => ({
    ...h, action: a,
    step_texts: h.kind === "approval" ? (a.plan?.approval_reasons || []) : (h.steps || []).map((i: number) => (a.plan?.steps || [])[i]).filter(Boolean),
  })));
}

/** 协同事项的操作：发送 / 催一下 / 回复疑问 */
export function HandoffOps({ h, onDone, compact }: { h: any; onDone: () => void; compact?: boolean }) {
  const [sending, setSending] = useState<any>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const { message } = App.useApp();
  const { refreshMeta } = useApp();
  const closedAction = ["cancelled", "rejected", "executed"].includes(h.action?.status);

  const remind = async () => {
    setBusy(true);
    try { await api(`/api/handoffs/${h.id}/remind`, { method: "POST" }); message.success(`已在飞书提醒${h.assignee || h.role}`); onDone(); }
    catch (e: any) { message.error(e.message); } finally { setBusy(false); }
  };
  const doReply = async () => {
    if (!reply.trim()) return;
    setBusy(true);
    try {
      const x = await api<any>(`/api/handoffs/${h.id}/reply`, { method: "POST", body: { text: reply } });
      message.success(x.reply_pushed ? `回复已推送给${h.assignee || h.role}` : "已记录回复（对方未绑定飞书，请另行转告）");
      setReply(""); onDone(); refreshMeta();
    } catch (e: any) { message.error(e.message); } finally { setBusy(false); }
  };

  if (closedAction) return null;
  return (
    <>
      {h.status === "draft" && <Button size="small" type="primary" ghost onClick={() => setSending(h)}>{h.kind === "approval" ? "提交审批" : "发送"}</Button>}
      {["sent", "received"].includes(h.status) && (
        <Tooltip title={h.channel === "feishu" ? "给对方飞书发一条提醒" : "对方没有通过飞书接收，无法推送提醒"}>
          <Button size="small" icon={<BellOutlined />} loading={busy} disabled={h.channel !== "feishu"} onClick={remind}>
            催一下{h.remind_count ? `（${h.remind_count}）` : ""}
          </Button>
        </Tooltip>
      )}
      {h.status === "question" && (
        <div style={{ display: "grid", gap: 6, marginTop: compact ? 0 : 4 }}>
          <div className="small" style={{ color: "#8a5a00" }}>对方疑问：{h.note || "（未填写）"}</div>
          <Space.Compact style={{ width: "100%" }}>
            <Input size="small" value={reply} onChange={e => setReply(e.target.value)} onPressEnter={doReply} placeholder="回复对方，会推送到对方飞书" />
            <Button size="small" type="primary" loading={busy} onClick={doReply}>回复</Button>
          </Space.Compact>
        </div>
      )}
      <HandoffSendModal handoff={sending} onClose={() => setSending(null)} onSent={() => { setSending(null); onDone(); refreshMeta(); }} />
    </>
  );
}

const Who = ({ h }: { h: any }) => <>{h.role}{h.assignee && <div className="muted small">{h.assignee}</div>}</>;
const What = ({ h, onOpen }: { h: any; onOpen: (id: number) => void }) => (
  <>
    <a onClick={() => onOpen(h.action.id)}>{h.action.name}</a>
    <div className="muted small">{h.action.product_name}</div>
    <div className="small sec" style={{ marginTop: 2 }}>{h.step_texts.join("；")}</div>
  </>
);

/** 我要做的：需要我处理的协同（对方有疑问、还没发出去的）+ 我的步骤 */
export function MineView({ actions, onOpen, reload }: { actions: any[]; onOpen: (id: number) => void; reload: () => void }) {
  const { asOf, refreshMeta } = useApp();
  const hs = flatHandoffs(actions).filter(h => ["question", "draft"].includes(h.status) && ["adopted", "transferred"].includes(h.action.status));
  const steps = actions.filter(a => ["adopted", "transferred"].includes(a.status)).flatMap(a => {
    const owners: string[] = a.plan?.step_owners || [];
    return owners.map((o, i) => ({ a, i, o })).filter(x => x.o === "我" && !(a.step_done || []).includes(x.i))
      .map(x => ({ key: `${a.id}-${x.i}`, a, i: x.i, text: (a.plan?.steps || [])[x.i] }));
  }).sort((x, y) => (x.a.due_date || "9").localeCompare(y.a.due_date || "9"));
  const tick = async (aid: number, i: number) => {
    await api(`/api/actions/${aid}/steps`, { method: "PATCH", body: { index: i, done: true } });
    reload(); refreshMeta();
  };

  return (
    <div className="grid">
      {hs.length > 0 && (
        <Card title={<>需要我处理<span className="hint">对方提出了疑问，或协同请求还没有发出</span></>} styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" size="middle" dataSource={hs} pagination={false} scroll={{ x: 820 }} columns={[
            { title: "对方", key: "r", width: 110, render: (_, h) => <Who h={h} /> },
            { title: "事项", key: "w", render: (_, h) => <What h={h} onOpen={onOpen} /> },
            { title: "状态", key: "s", width: 90, render: (_, h) => <HandoffTag h={h} /> },
            { title: "", key: "op", width: 320, render: (_, h) => <HandoffOps h={h} onDone={reload} compact /> },
          ]} />
        </Card>
      )}
      <Card title={<>我的步骤<span className="hint">勾选完成；全部步骤和协同都完成后自动记为已执行</span></>} styles={{ body: { padding: 0 } }}>
        {steps.length ? (
          <Table rowKey="key" size="middle" dataSource={steps} pagination={false} scroll={{ x: 760 }} columns={[
            { title: "", key: "c", width: 48, render: (_, x) => <Checkbox disabled={x.a.progress?.approval_pending} onChange={() => tick(x.a.id, x.i)} aria-label="完成" /> },
            { title: "步骤", dataIndex: "text", render: (t, x) => <>{t}{x.a.progress?.approval_pending && <Tag bordered={false} color="warning" style={{ marginLeft: 6 }}>等待审批</Tag>}</> },
            { title: "所属待办", key: "a", width: 260, render: (_, x) => <><a onClick={() => onOpen(x.a.id)}>{x.a.name}</a><div className="muted small">{x.a.product_name}</div></> },
            { title: "截止", key: "d", width: 120, render: (_, x) => <span className="num small">{x.a.due_date || "—"}
              {x.a.due_date && asOf && x.a.due_date < asOf && <Tag color="error" bordered={false} style={{ marginLeft: 4 }}>已逾期</Tag>}</span> },
          ]} />
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有待完成的步骤" style={{ padding: 24 }} />}
      </Card>
    </div>
  );
}

/** 等别人的：已发给同事、还没完成的协同事项 */
export function WaitingView({ actions, onOpen, reload }: { actions: any[]; onOpen: (id: number) => void; reload: () => void }) {
  const { asOf } = useApp();
  const [scope, setScope] = useState<"open" | "all">("open");
  const all = flatHandoffs(actions).filter(h => h.status !== "draft");
  const rows = all.filter(h => scope === "all" || (["sent", "received", "question"].includes(h.status) && ["adopted", "transferred"].includes(h.action.status)))
    .sort((a, b) => (a.due || "").localeCompare(b.due || ""));
  return (
    <Card styles={{ body: { padding: 0 } }} title={<>等别人的<span className="hint">飞书推送后，对方在卡片上的处理会自动同步到这里；到截止时间还没完成会自动提醒</span></>}
      extra={<Space size={4}><Button size="small" type={scope === "open" ? "primary" : "text"} onClick={() => setScope("open")}>进行中</Button>
        <Button size="small" type={scope === "all" ? "primary" : "text"} onClick={() => setScope("all")}>全部</Button></Space>}>
      <Table rowKey="id" dataSource={rows} pagination={false} scroll={{ x: 1000 }} locale={{ emptyText: "没有等待同事处理的事项" }} columns={[
        { title: "对方", key: "r", width: 110, render: (_, h) => <Who h={h} /> },
        { title: "事项", key: "w", render: (_, h) => <What h={h} onOpen={onOpen} /> },
        { title: "状态", key: "s", width: 110, render: (_, h) => <><HandoffTag h={h} />{h.status === "question" && <div className="small" style={{ color: "#8a5a00" }}>等你回复</div>}</> },
        { title: "发出", key: "t", width: 110, render: (_, h) => <span className="small">{ago(h.sent_at)}<div className="muted">{h.channel === "feishu" ? "飞书" : "复制发送"}</div></span> },
        { title: "截止", dataIndex: "due", width: 110, render: (d, h) => <span className="num small">{d || "—"}
          {d && asOf && d < asOf && ["sent", "received"].includes(h.status) && <div><Tag color="error" bordered={false}>已逾期</Tag></div>}</span> },
        { title: "", key: "op", width: 260, render: (_, h) => <HandoffOps h={h} onDone={reload} compact /> },
      ]} />
    </Card>
  );
}
