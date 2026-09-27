import { useEffect, useState } from "react";
import { App, Button, DatePicker, Form, Input, InputNumber, Modal, Select, Space, Tag } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { api } from "../api";
import { useApp } from "../App";

export const ROLES = ["我", "供应链", "投放运营"];
export const TRACK_METRICS: [string, string][] = [
  ["gmv", "GMV"], ["cvr", "支付转化率"], ["uv", "访客数"], ["aov", "客单价"], ["units", "销量"], ["rating", "评分"],
];

export type TodoDraft = {
  name?: string; steps?: { text: string; by: string }[]; due_date?: string; track_metric?: string; track_days?: number;
  note?: string; due_is_default?: boolean;
};

type Props = {
  open: boolean;
  source: "diagnosis" | "chat" | "manual" | "report" | "alert";
  productId?: string;          // 在商品页创建时固定商品
  draft?: TodoDraft | null;
  plan?: any;                  // 采纳 AI 方案时传入
  cardId?: string | null;
  context?: any;               // 背景：诊断结论与证据 / 对话摘要
  onClose: () => void;
  onCreated: (a: any) => void;
};

const TITLE = { diagnosis: "采纳方案，加入待办", chat: "从对话创建待办", manual: "新建待办", report: "从报告创建待办", alert: "用当前方案转待办" };

/** 新建待办确认窗：三个入口共用。同事的步骤在这里直接看到会通知谁，保存即推送。 */
export default function TodoModal({ open, source, productId, draft, plan, cardId, context, onClose, onCreated }: Props) {
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const [products, setProducts] = useState<any[]>([]);
  const [fs, setFs] = useState<any>(null);
  const [preview, setPreview] = useState<any[] | null>(null);
  const { asOf, refreshMeta } = useApp();
  const { message } = App.useApp();
  const steps: { text: string; by: string }[] = Form.useWatch("steps", form) || [];

  useEffect(() => {
    if (!open) return;
    const base = asOf ? dayjs(asOf) : dayjs();
    const d = draft || {};
    form.setFieldsValue({
      product_id: productId,
      name: d.name || plan?.name || "",
      steps: d.steps?.length ? d.steps : plan?.steps?.length
        ? plan.steps.map((t: string, k: number) => ({ text: t, by: (plan.step_owners || [])[k] || "我" }))
        : [{ text: "", by: "我" }],
      due: d.due_date ? dayjs(d.due_date) : base.add(plan?.due_days ?? 3, "day"),
      track_metric: d.track_metric || plan?.track?.metric || "gmv",
      track_days: d.track_days || plan?.track?.days || 7,
      note: d.note || "",
    });
    setPreview(null);
    api("/api/integrations/feishu").then(setFs).catch(() => setFs(null));
    if (!productId && !products.length) api<any[]>("/api/products").then(setProducts).catch(() => {});
  }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps

  const others = [...new Set(steps.filter(s => s?.text?.trim() && s.by !== "我").map(s => s.by))];
  const bound = (r: string) => fs?.ready && fs?.roles?.[r]?.open_id;
  const nFeishu = others.filter(bound).length;

  const body = async () => {
    const v = await form.validateFields();
    return {
      product_id: productId || v.product_id, name: v.name, steps: v.steps.filter((s: any) => s?.text?.trim()),
      due_date: v.due ? v.due.format("YYYY-MM-DD") : null, track_metric: v.track_metric, track_days: v.track_days,
      note: v.note, source, context, plan: plan || null, card_id: cardId || null,
    };
  };
  const loadPreview = async () => {
    try { setPreview(await api<any[]>("/api/todos/preview", { method: "POST", body: await body() })); }
    catch (e: any) { if (e?.message) message.error(e.message); }
  };
  const save = async () => {
    const b = await body();
    setBusy(true);
    try {
      const a = await api<any>("/api/todos", { method: "POST", body: { ...b, notify: true } });
      const ok = (a.notified || []).filter((r: any) => r.ok).map((r: any) => `${r.to}（${r.role}）`);
      const left = (a.handoffs || []).filter((h: any) => h.status === "pending").length;
      message.success("已加入待办" + (ok.length ? `，已通过飞书通知 ${ok.join("、")}` : "") + (left ? `；${left} 项需要复制文字手动发送` : ""));
      refreshMeta();
      onCreated(a);
    } catch (e: any) { message.error(e.message); } finally { setBusy(false); }
  };

  return (
    <Modal open={open} title={TITLE[source]} width={640} onCancel={onClose} destroyOnClose
      footer={<Space>
        <Button onClick={onClose}>取消</Button>
        <Button type="primary" loading={busy} onClick={save}>{nFeishu ? `保存并通知 ${nFeishu} 人` : "保存"}</Button>
      </Space>}>
      <Form form={form} layout="vertical" style={{ marginTop: 12 }} requiredMark={false}>
        {!productId && (
          <Form.Item name="product_id" label="商品" rules={[{ required: true, message: "请选择商品" }]}>
            <Select showSearch optionFilterProp="label" placeholder="选择商品"
              options={products.map(p => ({ value: p.product_id, label: `${p.product_name}（${p.tier}）` }))} />
          </Form.Item>
        )}
        <Form.Item name="name" label="待办名称" rules={[{ required: true, message: "请填写待办名称" }]}>
          <Input placeholder="例如：确认白色款到货并更新详情页提示" maxLength={60} />
        </Form.Item>
        <Form.Item label="步骤与负责人" required>
          <Form.List name="steps" rules={[{ validator: async (_, v) => { if (!v?.some((s: any) => s?.text?.trim())) throw new Error("至少填写一个步骤"); } }]}>
            {(fields, { add, remove }, { errors }) => (
              <div className="todo-steps">
                {fields.map((f, i) => (
                  <div className="ts" key={f.key}>
                    <span className="idx">{i + 1}</span>
                    <Form.Item name={[f.name, "text"]} noStyle><Input placeholder="做什么、对象是什么" /></Form.Item>
                    <Form.Item name={[f.name, "by"]} noStyle><Select style={{ width: 112 }} options={ROLES.map(r => ({ value: r, label: r }))} /></Form.Item>
                    <Button type="text" icon={<DeleteOutlined />} disabled={fields.length === 1} onClick={() => remove(f.name)} aria-label="删除步骤" />
                  </div>
                ))}
                <Button type="dashed" size="small" icon={<PlusOutlined />} onClick={() => add({ text: "", by: "我" })} disabled={fields.length >= 8}>添加步骤</Button>
                <Form.ErrorList errors={errors} />
              </div>
            )}
          </Form.List>
        </Form.Item>
        <Space size={16} wrap style={{ width: "100%" }} align="start">
          <Form.Item name="due" label={<>截止日期{draft?.due_is_default && <span className="muted small" style={{ marginLeft: 6 }}>默认 3 天</span>}</>} style={{ marginBottom: 12 }}>
            <DatePicker allowClear={false} style={{ width: 160 }} />
          </Form.Item>
          {plan ? (
            <Form.Item label="完成后跟踪" style={{ marginBottom: 12 }}>
              <span className="sec">{plan.track?.days} 天看{plan.track?.metric_name}</span>
            </Form.Item>
          ) : <>
            <Form.Item name="track_metric" label="完成后跟踪" style={{ marginBottom: 12 }}>
              <Select style={{ width: 140 }} options={TRACK_METRICS.map(([v, l]) => ({ value: v, label: l }))} />
            </Form.Item>
            <Form.Item name="track_days" label="跟踪天数" style={{ marginBottom: 12 }}>
              <InputNumber min={3} max={30} addonAfter="天" style={{ width: 110 }} />
            </Form.Item>
          </>}
        </Space>
        <Form.Item name="note" label="备注" style={{ marginBottom: 8 }}><Input.TextArea rows={2} placeholder="为什么要做，可选" maxLength={300} /></Form.Item>
      </Form>
      {others.length > 0 && (
        <div className="notify-list">
          <div className="small sec" style={{ marginBottom: 6 }}>保存后通知：</div>
          {others.map(r => (
            <div className="nh" key={r}>
              <Tag bordered={false} color="orange">{r}</Tag>
              {bound(r) ? <span className="small" style={{ color: "#0b7a0b" }}>飞书推送给 {fs.roles[r].name}</span>
                : <span className="small muted">未绑定飞书，保存后复制文字手动发送</span>}
            </div>
          ))}
          {preview ? preview.map((x, k) => <pre key={k} className="nmsg">{x.message}</pre>)
            : <a className="small" onClick={loadPreview}>查看消息内容</a>}
        </div>
      )}
    </Modal>
  );
}
