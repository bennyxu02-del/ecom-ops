import { useEffect, useMemo, useState } from "react";
import { App, Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Radio, Select, Space, Switch, Table, Tag, TimePicker, Timeline, Tooltip } from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined, SendOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../App";
import { Loading, useLoad } from "../hooks";
import { pushText } from "./Alerts";

const SUFFIX: Record<string, string> = { pct: "%", x: "倍", day: "天", yuan: "元", num: "" };
const toView = (unit: string, v: any) => unit === "pct" && v != null ? Math.round(v * 1000) / 10 : v;
const fromView = (unit: string, v: any) => unit === "pct" && v != null ? Math.round(v * 10) / 1000 : v;

function ParamInput({ p, value, onChange }: { p: any; value: any; onChange: (v: any) => void }) {
  if (p.unit === "bool") return <Switch checked={!!value} onChange={onChange} />;
  return <InputNumber size="small" value={toView(p.unit, value)} onChange={v => onChange(fromView(p.unit, v))} style={{ width: 120 }}
    min={0} step={p.unit === "yuan" ? 500 : p.unit === "day" ? 1 : p.unit === "x" ? 0.1 : p.unit === "num" ? 0.05 : 1}
    addonAfter={SUFFIX[p.unit] || undefined} />;
}

function CustomModal({ open, init, options, onClose, onSaved }: { open: boolean; init: any; options: any; onClose: () => void; onSaved: () => void }) {
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  const scope = Form.useWatch("scope", form);
  const metric = Form.useWatch("metric", form);
  const cond = Form.useWatch("cond", form);
  const m = (options?.metrics || []).find((x: any) => x.id === metric);
  const asPct = cond === "drop" || cond === "rise" || m?.unit === "pct";
  useEffect(() => {
    if (!open) return;
    const d = init || { scope: "store", metric: "gmv", cond: "drop", threshold: 0.2, severity: "yellow", enabled: true };
    const pct = d.cond === "drop" || d.cond === "rise" || (options?.metrics || []).find((x: any) => x.id === d.metric)?.unit === "pct";
    form.setFieldsValue({ ...d, threshold: pct ? Math.round(d.threshold * 1000) / 10 : d.threshold });
  }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps
  const save = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      const r = await api<any>("/api/alerts/custom", { method: "POST", body: { ...v, id: init?.id, threshold: asPct ? v.threshold / 100 : v.threshold } });
      message.success(`已保存并重新扫描，近 14 天命中 ${r.hits} 条`);
      onSaved();
    } catch (e: any) { message.error(e.message); }
    setBusy(false);
  };
  const metrics = (options?.metrics || []).filter((x: any) => scope !== "store" || x.store);
  return (
    <Modal open={open} title={init ? "修改自定义预警" : "新建自定义预警"} onCancel={onClose} onOk={save} okText="保存并扫描" confirmLoading={busy} destroyOnClose width={520}>
      <p className="small sec">像 BI 一样「选对象 + 选指标 + 条件 + 门槛」，指标过线就出预警，和预设规则走同一条推送和处理流程。自定义预警只判断指标过线，没有根因提示；处理时 AI 仍会按归因 SOP 诊断。</p>
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item name="name" label="预警名称" rules={[{ required: true, message: "请填写名称" }]}><Input placeholder="例如：全店 GMV 比上周下降" maxLength={30} /></Form.Item>
        <Space.Compact block>
          <Form.Item name="scope" label="对象" style={{ width: 200 }}>
            <Select options={Object.entries(options?.scopes || {}).map(([k, v]) => ({ value: k, label: v as string }))}
              onChange={() => form.setFieldValue("target", undefined)} />
          </Form.Item>
          {scope === "tier" && <Form.Item name="target" label="分层" style={{ flex: 1 }} rules={[{ required: true, message: "请选择分层" }]}>
            <Select options={(options?.tiers || []).map((t: any) => ({ value: t.id, label: t.name }))} /></Form.Item>}
          {scope === "product" && <Form.Item name="target" label="商品" style={{ flex: 1 }} rules={[{ required: true, message: "请选择商品" }]}>
            <Select showSearch optionFilterProp="label" options={(options?.products || []).map((p: any) => ({ value: p.id, label: p.name }))} /></Form.Item>}
        </Space.Compact>
        <Space.Compact block>
          <Form.Item name="metric" label="指标" style={{ width: 160 }}><Select options={metrics.map((x: any) => ({ value: x.id, label: x.name }))} /></Form.Item>
          <Form.Item name="cond" label="条件" style={{ width: 190 }}>
            <Select options={Object.entries(options?.conds || {}).map(([k, v]) => ({ value: k, label: v as string }))} /></Form.Item>
          <Form.Item name="threshold" label="门槛" style={{ flex: 1 }} rules={[{ required: true, message: "请填写门槛" }]}>
            <InputNumber style={{ width: "100%" }} min={0} addonAfter={asPct ? "%" : m?.unit === "money" || m?.unit === "price" ? "元" : undefined} /></Form.Item>
        </Space.Compact>
        <Form.Item name="severity" label="严重度">
          <Radio.Group options={[{ value: "red", label: "红" }, { value: "yellow", label: "黄" }, { value: "blue", label: "蓝" }]} optionType="button" /></Form.Item>
        <Form.Item name="enabled" hidden><Input /></Form.Item>
      </Form>
    </Modal>
  );
}

export default function AlertSettings() {
  const { data: s, error, reload, setData } = useLoad<any>("/api/alerts/settings");
  const [disabled, setDisabled] = useState<string[]>([]);
  const [params, setParams] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [preview, setPreview] = useState<any>(null);
  const [custom, setCustom] = useState<any>(undefined);
  const { message } = App.useApp();
  const { refreshMeta } = useApp();

  useEffect(() => {
    if (!s) return;
    setDisabled(s.rules.filter((r: any) => !r.enabled).map((r: any) => r.id));
    setParams(Object.fromEntries(s.params.map((p: any) => [p.key, p.value])));
  }, [s]);
  const pmap = useMemo(() => Object.fromEntries((s?.params || []).map((p: any) => [p.key, p])), [s]);
  const dirty = useMemo(() => {
    if (!s) return 0;
    let n = s.params.filter((p: any) => params[p.key] !== p.value).length;
    n += s.rules.filter((r: any) => disabled.includes(r.id) === r.enabled).length;
    return n;
  }, [s, params, disabled]);

  const save = async (extra?: any) => {
    setBusy(true);
    try {
      const changed = Object.fromEntries(s.params.filter((p: any) => params[p.key] !== p.value).map((p: any) => [p.key, params[p.key]]));
      setData(await api("/api/alerts/settings", { method: "PUT", body: { disabled, params: changed, ...(extra || {}) } }));
      message.success("已保存，预警已按新设置重新扫描"); refreshMeta();
    } catch (e: any) { message.error(e.message); }
    setBusy(false);
  };
  const savePush = async (push: any) => {
    try { setData(await api("/api/alerts/settings", { method: "PUT", body: { push } })); message.success("推送设置已保存"); }
    catch (e: any) { message.error(e.message); }
  };
  const pushNow = async () => {
    setPushing(true);
    try {
      const r = await api<any>("/api/alerts/push-now", { method: "POST" });
      if (r.sent) message.success(`已推送到飞书（${r.to}）`);
      setPreview(r); reload(); refreshMeta();
    } catch (e: any) { message.error(e.message); }
    setPushing(false);
  };
  const delCustom = async (id: number) => { await api(`/api/alerts/custom/${id}`, { method: "DELETE" }); message.success("已删除"); reload(); refreshMeta(); };
  const toggleCustom = async (x: any, on: boolean) => {
    await api("/api/alerts/custom", { method: "POST", body: { ...x, enabled: on } }); reload(); refreshMeta();
  };

  if (!s) return <Loading error={error} />;
  const noise = ["alert.min_impact", "alert.tail_stock_only", "alert.recover_ratio", "alert.ignore_silence_days"];
  const NOISE_TIP: Record<string, string> = {
    "alert.min_impact": "只触发「量」的下滑（GMV / 访客 / 转化）、且近 7 天影响低于这个金额的，不单独出预警",
    "alert.tail_stock_only": "长尾商品只看断货和断货风险，避免小商品天天报",
    "alert.recover_ratio": "规则不再触发后，对应指标回到出问题前（首次触发前 7 天）的这个比例以上，才算恢复",
    "alert.ignore_silence_days": "忽略后，同一商品同类预警在这些天内不再提醒（升级为更严重的除外）",
  };

  return (
    <>
      <div style={{ marginBottom: 10 }}><Link to="/alerts" className="small">← 预警中心</Link></div>
      <div className="page-head"><div><h1>预警设置</h1>
        <div className="sub">{s.dataset_name}：预设的预警规则（相当于 BI 里的预警任务）、推送、降噪和自定义预警。门槛写入品类配置，调整会留下记录。</div></div>
        {dirty > 0 && <Space><span className="small" style={{ color: "#8a5a00" }}>有 {dirty} 处修改未保存</span>
          <Button onClick={() => reload()}>撤销</Button><Button type="primary" loading={busy} onClick={() => save()}>保存并重新扫描</Button></Space>}
      </div>

      <Card title={<>1. 预警规则<span className="hint">平台预设的预警任务：监控什么、门槛多少、是否生效；近 14 天数据</span></>} styles={{ body: { padding: 0 } }}>
        <Table rowKey="id" dataSource={s.rules} pagination={false} size="middle"
          rowClassName={(r: any) => (!r.available || disabled.includes(r.id)) ? "row-off" : ""}
          columns={[
            { title: "规则", dataIndex: "name", width: 200, render: (t, r: any) => <><b>{t}</b><div className="small muted">{r.condition}</div></> },
            { title: "门槛", render: (_, r: any) => r.params.length ? (
              <Space direction="vertical" size={4}>{r.params.map((k: string) => (
                <span key={k} className="small">{pmap[k]?.label}：<ParamInput p={pmap[k]} value={params[k]} onChange={v => setParams({ ...params, [k]: v })} />
                  {pmap[k]?.value !== pmap[k]?.default && <Tooltip title={`默认 ${toView(pmap[k].unit, pmap[k].default)}${SUFFIX[pmap[k].unit]}`}><Tag bordered={false} color="blue" style={{ marginLeft: 6 }}>已调整</Tag></Tooltip>}
                </span>))}</Space>) : <span className="muted small">—</span> },
            { title: "近 14 天", width: 150, render: (_, r: any) => (
              <span className="small">预警 {r.stats.cards} 条 · 忽略 {r.stats.ignored} 条
                {r.stats.cards >= 2 && r.stats.ignored / r.stats.cards >= 0.5 && <div style={{ color: "#8a5a00" }}>忽略率高，门槛可能太严</div>}</span>) },
            { title: "数据", width: 150, render: (_, r: any) => r.available ? <Tag bordered={false} color="success">✓ 具备</Tag>
              : <Tooltip title="缺数据时这条规则不运行；没有报警不代表没有问题"><Tag bordered={false}>{r.missing}，未生效</Tag></Tooltip> },
            { title: "开关", width: 70, render: (_, r: any) => <Switch size="small" checked={!disabled.includes(r.id)} disabled={!r.available}
              onChange={on => setDisabled(on ? disabled.filter(x => x !== r.id) : [...disabled, r.id])} /> },
          ]} />
      </Card>

      <div className="grid g2 mt">
        <Card title="2. 推送设置">
          <div className="set-rows">
            <div><span>每天自动推送「今日预警」</span><Switch checked={s.push.enabled} onChange={v => savePush({ enabled: v })} /></div>
            <div><span>推送时间（北京时间）</span>
              <TimePicker format="HH:mm" minuteStep={15} allowClear={false} value={dayjs(s.push.time, "HH:mm")} onChange={v => v && savePush({ time: v.format("HH:mm") })} /></div>
            <div><span>接收人</span>{s.me_bound ? <Tag bordered={false} color="success">我（已绑定飞书）</Tag>
              : <span className="small">「我」还没绑定飞书，<Link to="/integrations">去集成页设置</Link></span>}</div>
            <div><span>没有新预警时也发一句</span><Switch checked={s.push.send_empty} onChange={v => savePush({ send_empty: v })} /></div>
          </div>
          <Button type="primary" icon={<SendOutlined />} loading={pushing} onClick={pushNow} style={{ marginTop: 12 }}>立即扫描并推送</Button>
          {s.push_history?.length > 0 && (
            <div className="mt small">
              <div className="muted" style={{ marginBottom: 4 }}>最近推送</div>
              {s.push_history.map((p: any, i: number) => <div key={i}>{pushText(p)}{p.manual ? "（手动）" : ""}</div>)}
            </div>)}
        </Card>
        <Card title="3. 降噪设置">
          <div className="set-rows">
            {noise.map(k => pmap[k] && (
              <div key={k}><Tooltip title={NOISE_TIP[k]}><span className="dotted">{pmap[k].label}</span></Tooltip>
                <ParamInput p={pmap[k]} value={params[k]} onChange={v => setParams({ ...params, [k]: v })} /></div>))}
          </div>
          <div className="small muted" style={{ marginTop: 10 }}>演示数据比较干净，小商品刷屏在演示里看不出来；真实业务里，这几项决定了每天推送是否可读。</div>
        </Card>
      </div>

      <Card className="mt" title={<>4. 自定义预警<span className="hint">选对象 + 指标 + 条件 + 门槛，指标过线就出预警</span></>}
        extra={<Button size="small" icon={<PlusOutlined />} onClick={() => setCustom(null)}>新建</Button>} styles={{ body: { padding: 0 } }}>
        <Table rowKey="id" dataSource={s.custom} pagination={false} size="middle" locale={{ emptyText: "还没有自定义预警" }}
          columns={[
            { title: "名称", dataIndex: "name", render: t => <b>{t}</b> },
            { title: "对象", width: 150, render: (_, x: any) => x.scope === "tier" ? (s.options.tiers.find((t: any) => t.id === x.target)?.name || x.target)
              : x.scope === "product" ? (s.options.products.find((p: any) => p.id === x.target)?.name || x.target) : s.options.scopes[x.scope] },
            { title: "条件", dataIndex: "desc", width: 220 },
            { title: "严重度", dataIndex: "severity", width: 80, render: v => ({ red: "红", yellow: "黄", blue: "蓝" } as any)[v] },
            { title: "近 14 天", width: 110, render: (_, x: any) => <span className="small">预警 {x.stats.cards} 条</span> },
            { title: "开关", width: 70, render: (_, x: any) => <Switch size="small" checked={x.enabled} onChange={on => toggleCustom(x, on)} /> },
            { title: "", width: 90, render: (_, x: any) => <Space>
              <Button size="small" type="text" icon={<EditOutlined />} onClick={() => setCustom(x)} />
              <Popconfirm title="删除这条自定义预警？" onConfirm={() => delCustom(x.id)}><Button size="small" type="text" icon={<DeleteOutlined />} /></Popconfirm></Space> },
          ]} />
      </Card>

      {s.log?.length > 0 && (
        <Card className="mt" title="调整记录">
          <Timeline items={s.log.map((l: any) => ({ children: <span className="small"><span className="muted">{new Date(l.t * 1000).toLocaleString("zh-CN")} · {l.by}</span>　{l.changes.join("；")}</span> }))} />
        </Card>)}

      <CustomModal open={custom !== undefined} init={custom} options={s.options} onClose={() => setCustom(undefined)}
        onSaved={() => { setCustom(undefined); reload(); refreshMeta(); }} />
      <Modal open={!!preview} title={preview?.sent ? "已推送到飞书" : "没有推送"} footer={<Button onClick={() => setPreview(null)}>关闭</Button>} onCancel={() => setPreview(null)}>
        {preview && !preview.sent && <div className="verify warn" style={{ marginBottom: 10 }}>{preview.reason}</div>}
        <div className="small muted" style={{ marginBottom: 6 }}>{preview?.sent ? "推送内容" : "推送内容预览"}</div>
        <pre className="push-preview">{preview?.text}</pre>
      </Modal>
    </>
  );
}
