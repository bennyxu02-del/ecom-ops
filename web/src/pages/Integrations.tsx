import { useState } from "react";
import { App, Button, Card, Descriptions, Form, Input, Modal, Space, Table, Tag } from "antd";
import { api } from "../api";
import { Loading, useLoad } from "../hooks";

const ROLE_DESC: Record<string, string> = {
  "我": "发起人（商品运营），接收协同进展通知",
  "供应链": "补货、到货确认、批次质量排查",
  "投放运营": "投放预算、推广计划调整",
  "商品主管": "审批超出权限的方案（如大幅降价）",
};

/** 集成：飞书连接状态、角色与飞书成员的对应关系 */
export default function Integrations() {
  const { data: s, error, setData } = useLoad<any>("/api/integrations/feishu");
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [probe, setProbe] = useState<{ at: number; to?: string } | null>(null);
  const [form] = Form.useForm();
  const { message } = App.useApp();
  if (!s) return <Loading error={error} />;

  const save = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      setData(await api(`/api/integrations/feishu/roles/${encodeURIComponent(editing!)}`, { method: "PUT", body: v }));
      message.success("已在飞书中找到该成员并保存");
      setEditing(null);
    } catch (e: any) { message.error(e.message); } finally { setBusy(false); }
  };
  const test = async (role: string) => {
    try { await api("/api/integrations/feishu/test", { method: "POST", body: { role } }); message.success("测试消息已发送，请在飞书中查看"); }
    catch (e: any) { message.error(e.message); }
  };
  const testCard = async () => {
    try {
      const r = await api<any>("/api/integrations/feishu/test-card", { method: "POST" });
      setProbe({ at: r.sent_at, to: r.to });
      message.success(`测试卡片已发给${r.to || "你"}，请在飞书里点「测试回调」`);
      for (let i = 0; i < 40; i++) {           // 最多等 2 分钟
        await new Promise(res => setTimeout(res, 3000));
        const x = await api<any>("/api/integrations/feishu");
        setData(x);
        if ((x.last_callback || 0) > r.sent_at) { message.success("按钮回调正常"); break; }
      }
    } catch (e: any) { message.error(e.message); }
  };
  const clear = async (role: string) => setData(await api(`/api/integrations/feishu/roles/${encodeURIComponent(role)}`, { method: "DELETE" }));

  const conn = !s.enabled ? <Tag>未配置</Tag> : s.ready ? <Tag color="success">已连接</Tag> : <Tag color="error">连接失败</Tag>;
  const cb = !s.enabled ? <Tag>—</Tag> : s.callback_online ? <Tag color="success">在线</Tag> : <Tag color="warning">离线</Tag>;

  const lastCb = s.last_callback ? new Date(s.last_callback * 1000) : null;
  const tested = probe && (s.last_callback || 0) > probe.at;
  const cbTest = !s.enabled ? null : tested ? <Tag color="success">测试通过 · {lastCb!.toLocaleTimeString("zh-CN", { hour12: false })}</Tag>
    : probe ? <Tag color="processing">等待你在飞书点击…</Tag>
    : lastCb ? <span className="muted small">最近一次回调 {lastCb.toLocaleString("zh-CN", { hour12: false })}</span> : null;

  return (
    <>
      <div className="page-head"><div><h1>集成</h1><div className="sub">把协同请求、审批和进展通知直接发到同事的飞书</div></div></div>
      <Card title="飞书" extra={s.ready && <Button size="small" onClick={testCard}>发送测试卡片</Button>}>
        <Descriptions size="small" column={{ xs: 1, md: 3 }} items={[
          { key: "c", label: "连接状态", children: conn },
          { key: "a", label: "应用", children: s.app_id || "—" },
          { key: "b", label: "卡片按钮回调", children: <Space size={6}>{cb}{cbTest}</Space> },
        ]} />
        {s.error && <div className="verify bad" style={{ marginTop: 8 }}>{s.error}</div>}
        {!s.enabled && <div className="verify bad" style={{ marginTop: 8 }}>尚未配置飞书应用凭证。</div>}
      </Card>
      <Card className="mt" title="角色与飞书成员" styles={{ body: { padding: 0 } }}>
        <Table<any> rowKey="role" pagination={false} dataSource={s.role_list.map((r: string): any => ({ role: r, ...(s.roles[r] || {}) }))}
          columns={[
            { title: "角色", dataIndex: "role", width: 110, render: r => <b>{r}</b> },
            { title: "负责什么", key: "d", render: (_, x) => <span className="sec small">{ROLE_DESC[x.role]}</span> },
            { title: "飞书成员", dataIndex: "name", width: 200, render: (n, x) => n ? <>{n}<div className="muted small">{x.contact}</div></> : <span className="muted">未设置</span> },
            { title: "", key: "op", width: 260, render: (_, x) => (
              <Space>
                <Button size="small" disabled={!s.ready} onClick={() => { setEditing(x.role); form.setFieldsValue({ name: x.name || "", contact: "" }); }}>{x.open_id ? "修改" : "设置"}</Button>
                {x.open_id && <Button size="small" onClick={() => test(x.role)}>发测试消息</Button>}
                {x.open_id && <Button size="small" type="text" danger onClick={() => clear(x.role)}>清除</Button>}
              </Space>
            ) },
          ]} />
      </Card>
      <Modal open={!!editing} title={`设置「${editing}」对应的飞书成员`} okText="查找并保存" cancelText="取消" confirmLoading={busy} onOk={save} onCancel={() => setEditing(null)} destroyOnClose>
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="显示名称"><Input placeholder="例如：张三（供应链）" /></Form.Item>
          <Form.Item name="contact" label="飞书账号绑定的手机号或邮箱" rules={[{ required: true, message: "请填写手机号或邮箱" }]}>
            <Input placeholder="只用于在飞书里找到这个人，页面上只显示部分号码" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
