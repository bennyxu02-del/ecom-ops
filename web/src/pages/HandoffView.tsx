import { useState } from "react";
import { App, Button, Card, Empty, Input, Result, Space, Spin, Timeline } from "antd";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { HandoffTag } from "../components/HandoffSendModal";
import { useLoad } from "../hooks";

const fmtTime = (t: number) => new Date(t * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const ACT: Record<string, string> = { draft: "创建", sent: "发出", received: "已接收", done: "已完成", question: "提出疑问", approved: "批准", declined: "驳回", cancelled: "取消" };

/** 协同方处理页：从飞书卡片或转交单链接打开，独立页面，适配手机 */
export default function HandoffView() {
  const { hid } = useParams();
  const { data: h, error, setData } = useLoad<any>("/api/handoffs/" + hid);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const { message } = App.useApp();

  if (error) return <div className="hv"><Result status="warning" title="打不开这个协同事项" subTitle={error} /></div>;
  if (!h) return <div className="hv"><div className="empty"><Spin /></div></div>;
  const approval = h.kind === "approval";
  const closed = ["done", "approved", "declined", "cancelled"].includes(h.status);

  const respond = async (status: string) => {
    if ((status === "question" || status === "declined") && !note.trim()) { message.warning(status === "declined" ? "请填写驳回原因" : "请写下你的疑问"); return; }
    setBusy(status);
    try {
      const x = await api(`/api/handoffs/${hid}/respond`, { method: "POST", body: { status, note: note.trim() || undefined } });
      setData(x); setNote("");
      message.success("已更新，发起人会在平台上看到");
    } catch (e: any) { message.error(e.message); } finally { setBusy(null); }
  };

  return (
    <div className="hv">
      <div className="hv-brand"><div className="logo">作</div>经营作战台 · {approval ? "审批申请" : "协同请求"}</div>
      <Card>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>{h.action?.product_name}</h2>
          <HandoffTag h={h} />
        </div>
        <div className="small muted" style={{ marginTop: 4 }}>
          发给：{h.role}{h.assignee ? `（${h.assignee}）` : ""} · 方案：{h.action?.name} · 希望在 {h.due} 前反馈
        </div>
        <pre className="hv-msg">{h.message}</pre>
        {h.status === "draft" && <div className="verify bad">发起人还没有发送这个事项。</div>}
        {h.status === "cancelled" && <div className="verify bad">发起人已取消这条协同请求，无需继续处理。</div>}
        {!closed && h.status !== "draft" && (
          <>
            <Input.TextArea rows={2} placeholder={approval ? "备注（驳回时必填）" : "处理说明或疑问（选填，提出疑问时必填）"} value={note} onChange={e => setNote(e.target.value)} style={{ marginTop: 12 }} />
            <Space wrap style={{ marginTop: 10 }}>
              {approval ? <>
                <Button type="primary" loading={busy === "approved"} onClick={() => respond("approved")}>批准</Button>
                <Button danger loading={busy === "declined"} onClick={() => respond("declined")}>驳回</Button>
              </> : <>
                {h.status === "sent" && <Button loading={busy === "received"} onClick={() => respond("received")}>已收到，处理中</Button>}
                <Button type="primary" loading={busy === "done"} onClick={() => respond("done")}>已完成</Button>
              </>}
              <Button loading={busy === "question"} onClick={() => respond("question")}>有疑问</Button>
            </Space>
          </>
        )}
        {closed && <div className="verify ok" style={{ marginTop: 12 }}>这个事项已经处理完毕，感谢配合。</div>}
      </Card>
      <Card size="small" title="处理记录" style={{ marginTop: 12 }}>
        {h.history?.length ? (
          <Timeline style={{ marginTop: 8 }} items={h.history.map((x: any) => ({
            children: <div className="small"><span className="muted">{fmtTime(x.t)}</span>　{x.by} {ACT[x.status] || x.status}{x.note ? `：${x.note}` : ""}</div>,
          }))} />
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}
      </Card>
    </div>
  );
}
