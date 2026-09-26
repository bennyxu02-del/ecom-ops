import { useState } from "react";
import { Input, Modal, Radio, Space } from "antd";

export type ReasonSpec = { title: string; options: string[]; input?: boolean; placeholder?: string; okText?: string; requireTextFor?: string };

/** 选择原因 + 补充说明的弹窗（驳回方案、转交、忽略预警共用） */
export default function ReasonModal({ spec, onOk, onCancel }: { spec: ReasonSpec | null; onOk: (option: string, text: string) => void; onCancel: () => void }) {
  const [opt, setOpt] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  if (!spec) return null;
  const cur = opt ?? spec.options[0];
  return (
    <Modal open title={spec.title} okText={spec.okText || "确定"} cancelText="取消" destroyOnClose width={420}
      onCancel={() => { setOpt(null); setText(""); setErr(""); onCancel(); }}
      onOk={() => {
        if (spec.requireTextFor && cur === spec.requireTextFor && !text.trim()) { setErr("请填写原因"); return; }
        onOk(cur, text.trim()); setOpt(null); setText(""); setErr("");
      }}>
      <Radio.Group value={cur} onChange={e => setOpt(e.target.value)} style={{ width: "100%", margin: "8px 0 12px" }}>
        <Space direction="vertical">{spec.options.map(o => <Radio key={o} value={o}>{o}</Radio>)}</Space>
      </Radio.Group>
      {spec.input && <Input.TextArea rows={3} placeholder={spec.placeholder} value={text} onChange={e => setText(e.target.value)} status={err ? "error" : undefined} />}
      {err && <div style={{ color: "#d03b3b", fontSize: 12, marginTop: 4 }}>{err}</div>}
    </Modal>
  );
}
