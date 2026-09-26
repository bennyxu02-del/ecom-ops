import { useState } from "react";
import { Button, Collapse, Modal, Space, Tag } from "antd";
import { SendOutlined } from "@ant-design/icons";

export type NotifyItem = { kind: string; role: string; to?: string | null; channel: "feishu" | "copy"; message?: string };

/** 保存前确认：哪些步骤会通知谁、通过什么渠道、消息内容 */
export default function NotifyConfirm({ items, onConfirm, onCancel }: {
  items: NotifyItem[] | null;
  onConfirm: (notify: boolean) => Promise<void> | void;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  if (!items) return null;
  const fs = items.filter(i => i.channel === "feishu");
  const run = async (notify: boolean) => {
    setBusy(notify ? "n" : "s");
    try { await onConfirm(notify); } finally { setBusy(null); }
  };
  return (
    <Modal open width={600} title="保存前确认通知" onCancel={onCancel} footer={
      <Space>
        <Button onClick={onCancel}>返回修改</Button>
        <Button loading={busy === "s"} disabled={!!busy} onClick={() => run(false)}>仅保存，稍后发送</Button>
        {fs.length > 0 && <Button type="primary" icon={<SendOutlined />} loading={busy === "n"} disabled={!!busy} onClick={() => run(true)}>保存并通知 {fs.length} 人</Button>}
      </Space>}>
      <div className="small sec" style={{ marginBottom: 10 }}>以下步骤需要其他同事处理，保存后会生成协同请求：</div>
      <div className="notify-list">
        {items.map((it, i) => (
          <div className="ni" key={i}>
            <div className="nh">
              <Tag bordered={false} color={it.kind === "approval" ? "blue" : "orange"}>{it.kind === "approval" ? "审批" : "转交"}</Tag>
              <b>{it.role}</b>
              {it.channel === "feishu"
                ? <span className="small" style={{ color: "#0b7a0b" }}>→ 飞书推送给 {it.to}</span>
                : <span className="small muted">未绑定飞书，保存后可在待办中心复制发送</span>}
            </div>
            {it.message && <Collapse size="small" ghost items={[{ key: "m", label: <span className="small">查看消息内容</span>,
              children: <pre className="nmsg">{it.message}</pre> }]} />}
          </div>
        ))}
      </div>
      <div className="muted small" style={{ marginTop: 8 }}>对方在飞书卡片上点「已收到 / 已完成 / 有疑问」，状态会同步回平台并通知你。</div>
    </Modal>
  );
}
