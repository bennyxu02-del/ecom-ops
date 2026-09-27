import { useEffect, useState } from "react";
import { App, Button, Input, Modal, Space, Tag } from "antd";
import { CopyOutlined, SendOutlined } from "@ant-design/icons";
import { api } from "../api";

export const HANDOFF_COLOR: Record<string, string> = {
  pending: "default", notified: "processing", question: "warning", done: "success", cancelled: "default",
};

export function HandoffTag({ h }: { h: any }) {
  return <Tag bordered={false} color={HANDOFF_COLOR[h.status] || "default"}>{h.status_name}</Tag>;
}

/** 发出未通知的协同：预览、修改文字、选择飞书推送或复制发送 */
export default function HandoffSendModal({ handoff, onClose, onSent }: { handoff: any | null; onClose: () => void; onSent: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [feishu, setFeishu] = useState<any>(null);
  const { message } = App.useApp();

  useEffect(() => {
    if (!handoff) return;
    setText(handoff.message || "");
    api("/api/integrations/feishu").then(setFeishu).catch(() => setFeishu(null));
  }, [handoff]);

  if (!handoff) return null;
  const target = feishu?.roles?.[handoff.role]?.name;
  const feishuReady = feishu?.ready && !!feishu?.roles?.[handoff.role]?.open_id;

  const send = async (channel: "copy" | "feishu") => {
    setBusy(true);
    try {
      if (channel === "copy") {
        try { await navigator.clipboard.writeText(text); } catch { /* 浏览器不允许时仍可手动复制 */ }
      }
      await api(`/api/handoffs/${handoff.id}/send`, { method: "POST", body: { channel, message: text } });
      message.success(channel === "feishu" ? `已通过飞书发送给${target || handoff.role}` : "已复制内容并标记为已通知，粘贴到聊天工具发给对方即可");
      onSent();
    } catch (e: any) {
      message.error(e.message);
    } finally { setBusy(false); }
  };

  return (
    <Modal open width={560} title={`发给${handoff.role}`} onCancel={onClose}
      footer={<Space>
        <Button onClick={onClose}>取消</Button>
        <Button icon={<CopyOutlined />} loading={busy} onClick={() => send("copy")}>复制文字，标记已通知</Button>
        {feishu?.enabled && (
          <Button type="primary" icon={<SendOutlined />} loading={busy} disabled={!feishuReady} onClick={() => send("feishu")}>
            {feishuReady ? `飞书发送给 ${target || handoff.role}` : `飞书未配置${handoff.role}`}
          </Button>
        )}
      </Space>}>
      <div className="small muted" style={{ marginBottom: 8 }}>
        内容已自动生成，可以直接修改。对方点「已完成」或「有疑问」后，状态会同步回平台。
      </div>
      <Input.TextArea value={text} onChange={e => setText(e.target.value)} autoSize={{ minRows: 10, maxRows: 18 }} />
    </Modal>
  );
}
