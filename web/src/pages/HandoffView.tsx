import { useState } from "react";
import { App, Button, Card, Empty, Input, Result, Space, Spin, Timeline } from "antd";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { HandoffTag } from "../components/HandoffSendModal";
import { useLoad } from "../hooks";

const fmtTime = (t: number) => new Date(t * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const ACT: Record<string, string> = { pending: "创建", notified: "通知", question: "提出疑问", done: "已完成", cancelled: "取消" };

/** 协同方处理页：从飞书卡片或协同链接打开，独立页面，适配手机 */
export default function HandoffView() {
  const { hid } = useParams();
  const { data: h, error, setData } = useLoad<any>("/api/handoffs/" + hid);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const { message } = App.useApp();

  if (error) return <div className="hv"><Result status="warning" title="打不开这个协同事项" subTitle={error} /></div>;
  if (!h) return <div className="hv"><div className="empty"><Spin /></div></div>;
  const closed = ["done", "cancelled"].includes(h.status);

  const respond = async (status: string) => {
    if (status === "question" && !note.trim()) { message.warning("请写下你的疑问"); return; }
    setBusy(status);
    try {
      const x = await api(`/api/handoffs/${hid}/respond`, { method: "POST", body: { status, note: note.trim() || undefined } });
      setData(x); setNote("");
      message.success("已更新，发起人会在平台上看到");
    } catch (e: any) { message.error(e.message); } finally { setBusy(null); }
  };

  return (
    <div className="hv">
      <div className="hv-brand"><div className="logo">作</div>经营作战台 · 协同请求</div>
      <Card>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>{h.action?.product_name}</h2>
          <HandoffTag h={h} />
        </div>
        <div className="small muted" style={{ marginTop: 4 }}>
          发给：{h.role}{h.assignee ? `（${h.assignee}）` : ""} · 待办：{h.action?.name} · 希望在 {h.due} 前完成
        </div>
        <pre className="hv-msg">{h.message}</pre>
        {h.status === "pending" && <div className="verify bad">发起人还没有发出这个事项。</div>}
        {h.status === "cancelled" && <div className="verify bad">发起人已取消这条协同请求，无需继续处理。</div>}
        {!closed && h.status !== "pending" && (
          <>
            {h.status === "question" && <div className="verify bad" style={{ marginTop: 12 }}>你的疑问已发给发起人，等待回复：{h.note}</div>}
            <Input.TextArea rows={2} placeholder="处理结果（如时间、数量、排查结论），或写下你的疑问（提出疑问时必填）" value={note} onChange={e => setNote(e.target.value)} style={{ marginTop: 12 }} />
            <Space wrap style={{ marginTop: 10 }}>
              <Button type="primary" loading={busy === "done"} onClick={() => respond("done")}>已完成</Button>
              <Button loading={busy === "question"} onClick={() => respond("question")}>有疑问</Button>
            </Space>
          </>
        )}
        {h.status === "done" && <div className="verify ok" style={{ marginTop: 12 }}>{h.proxy ? "发起人已确认这部分完成，无需再处理，谢谢！" : "这个事项已经处理完毕，感谢配合。"}</div>}
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
