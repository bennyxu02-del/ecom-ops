import { useEffect, useState } from "react";
import { App, Button, DatePicker, Form, Input, Modal, Select, Space } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { api } from "../api";
import { useApp } from "../App";
import NotifyConfirm, { NotifyItem } from "./NotifyConfirm";

export const ROLES = ["我", "供应链", "投放运营", "商品主管"];
export const TRACK_METRICS: [string, string][] = [
  ["gmv", "GMV"], ["cvr", "支付转化率"], ["uv", "访客数"], ["aov", "客单价"], ["units", "销量"], ["rating", "评分"],
];

export type TodoDraft = { name?: string; steps?: { text: string; by: string }[]; due_date?: string; track_metric?: string; note?: string };

type Props = {
  open: boolean;
  source: "chat" | "manual";
  productId?: string;          // 在商品页创建时固定商品
  draft?: TodoDraft | null;
  context?: any;               // 转交单用到的背景（如追问的结论）
  onClose: () => void;
  onCreated: (a: any) => void;
};

/** 保存后的提示：通知了谁、哪些还要手动发送 */
export function notifiedText(res: any[] | undefined, total: number, notify: boolean) {
  const ok = (res || []).filter(r => r.ok).map(r => `${r.to}（${r.role}）`);
  const left = total - ok.length;
  if (!total) return "已保存待办";
  if (!notify) return `已保存待办，${total} 项协同稍后在待办中心发送`;
  return "已保存" + (ok.length ? `，已通过飞书通知 ${ok.join("、")}` : "") + (left ? `；另有 ${left} 项未推送，请在待办中心发送` : "");
}

/** 新建待办：名称、步骤与负责人、截止日期、跟踪指标、备注 */
export default function TodoModal({ open, source, productId, draft, context, onClose, onCreated }: Props) {
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const [products, setProducts] = useState<any[]>([]);
  const { asOf, refreshMeta } = useApp();
  const { message } = App.useApp();

  useEffect(() => {
    if (!open) return;
    const base = asOf ? dayjs(asOf) : dayjs();
    form.setFieldsValue({
      product_id: productId,
      name: draft?.name || "",
      steps: draft?.steps?.length ? draft.steps : [{ text: "", by: "我" }],
      due: draft?.due_date ? dayjs(draft.due_date) : base.add(3, "day"),
      track_metric: draft?.track_metric || "gmv",
      note: draft?.note || "",
    });
    if (!productId && !products.length) api<any[]>("/api/products").then(setProducts).catch(() => {});
  }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps

  const [confirm, setConfirm] = useState<{ body: any; items: NotifyItem[] } | null>(null);

  const collect = async () => {
    const v = await form.validateFields();
    return {
      product_id: productId || v.product_id, name: v.name, steps: v.steps.filter((s: any) => s?.text?.trim()),
      due_date: v.due ? v.due.format("YYYY-MM-DD") : null, track_metric: v.track_metric, note: v.note, source, context,
    };
  };
  const create = async (body: any, notify: boolean) => {
    const a = await api("/api/todos", { method: "POST", body: { ...body, notify } });
    message.success(notifiedText(a.notified, (a.handoffs || []).length, notify));
    refreshMeta();
    setConfirm(null);
    onCreated(a);
  };
  const save = async () => {
    const body = await collect();
    setBusy(true);
    try {
      const items = await api<NotifyItem[]>("/api/todos/preview", { method: "POST", body });
      if (items.length) setConfirm({ body, items });
      else await create(body, false);
    } catch (e: any) { message.error(e.message); } finally { setBusy(false); }
  };

  return (
    <Modal open={open} title={source === "chat" ? "加入待办" : "新建待办"} width={620} okText="保存待办" cancelText="取消"
      confirmLoading={busy} onOk={save} onCancel={onClose} destroyOnClose>
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
                <div className="muted small" style={{ marginTop: 6 }}>负责人不是「我」的步骤会生成协同请求，保存时可以直接推送到对方飞书。</div>
              </div>
            )}
          </Form.List>
        </Form.Item>
        <Space size={16} wrap style={{ width: "100%" }}>
          <Form.Item name="due" label="截止日期" style={{ marginBottom: 12 }}><DatePicker allowClear={false} style={{ width: 170 }} /></Form.Item>
          <Form.Item name="track_metric" label="完成后跟踪" style={{ marginBottom: 12 }}>
            <Select style={{ width: 170 }} options={TRACK_METRICS.map(([v, l]) => ({ value: v, label: l }))} />
          </Form.Item>
        </Space>
        <Form.Item name="note" label="备注" style={{ marginBottom: 0 }}><Input.TextArea rows={2} placeholder="为什么要做，可选" maxLength={300} /></Form.Item>
      </Form>
      <NotifyConfirm items={confirm?.items || null} onCancel={() => setConfirm(null)}
        onConfirm={async n => { try { await create(confirm!.body, n); } catch (e: any) { message.error(e.message); } }} />
    </Modal>
  );
}
