import { useEffect, useState } from "react";
import { App, Button, Card, InputNumber, Modal, Select, Space, Spin, Table, Tag } from "antd";
import { AimOutlined, CalendarOutlined, FlagOutlined, LoadingOutlined, CheckOutlined, BarChartOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { api, sse } from "../api";
import Markdown from "../components/Markdown";
import { Loading, useLoad } from "../hooks";

const SCENES = [
  { key: "weekly", icon: <CalendarOutlined />, title: "周度经营分析", who: "给业务线负责人、运营主管 · 每周一次",
    qs: ["这周生意怎么样，是流量、转化还是客单价带来的", "变化是谁带来的：预期内、需处理，还是机会", "上周动作有没有用，下周最该做哪 3 件事"] },
  { key: "campaign", icon: <FlagOutlined />, title: "活动复盘", who: "给运营负责人、活动运营、供应链 · 每场活动后",
    qs: ["活动带来多少真实增量（剔除自然增长）", "增量从哪来、让了多少利、有没有透支或挤占", "下次保持什么、改什么、不再做什么"] },
  { key: "product", icon: <AimOutlined />, title: "单品诊断", who: "给商品运营（及协同的同事）· 预警触发时",
    qs: ["问题有多大、是否还在恶化", "根因是什么、证据够不够、排除了哪些原因", "怎么办、谁来办、怎么判断有效"] },
];
const SCENE_COLOR: Record<string, string> = { weekly: "blue", campaign: "orange", product: "green" };
const md = (s: string) => { const [, m, d] = s.split("-"); return `${+m}/${+d}`; };
const weekLabel = (end: string) => { const e = new Date(end); const s = new Date(e.getTime() - 6 * 86400000); return `${s.getMonth() + 1}/${s.getDate()}–${e.getMonth() + 1}/${e.getDate()}`; };

export default function Reports() {
  const { data, error, reload } = useLoad<any[]>("/api/reports");
  const { data: sc, reload: reloadSc } = useLoad<any>("/api/reports/scenes");
  const [week, setWeek] = useState<string>();
  const [camp, setCamp] = useState<string>();
  const [prod, setProd] = useState<string>();
  const [gen, setGen] = useState<{ scene: string; steps: { t: string; ai?: boolean; done?: boolean }[]; text: string; err?: string } | null>(null);
  const [tgtOpen, setTgtOpen] = useState(false);
  const [tgt, setTgt] = useState<number | null>(null);
  const [filter, setFilter] = useState<string>("all");
  const { message } = App.useApp();
  const nav = useNavigate();

  useEffect(() => {
    if (!sc) return;
    setWeek(w => w || sc.weekly.weeks[0]);
    setCamp(c => c || sc.campaign.campaigns[0]?.id);
    setProd(p => p || sc.product.products[0]?.id);
    setTgt(sc.weekly.target ? sc.weekly.target / 10000 : null);
  }, [sc]);

  const run = async (scene: string) => {
    const params = scene === "weekly" ? { week_end: week } : scene === "campaign" ? { campaign_id: camp } : { product_id: prod };
    setGen({ scene, steps: [], text: "" });
    let text = "";
    try {
      await sse("/api/reports/generate", { scene, params }, ev => {
        if (ev.type === "step") setGen(g => g && ({ ...g, steps: [...g.steps.map(s => ({ ...s, done: true })), { t: ev.summary, ai: ev.tool === "draw_chart" }] }));
        if (ev.type === "delta") { text += ev.text; setGen(g => g && ({ ...g, text })); }
        if (ev.type === "meta" && ev.fallback_reason) setGen(g => g && ({ ...g, steps: [...g.steps, { t: "大模型暂时不可用，改用规则版报告：" + ev.fallback_reason }] }));
        if (ev.type === "result") { reload(); nav("/report/" + ev.id); }
        if (ev.type === "error") setGen(g => g && ({ ...g, err: ev.message }));
      });
    } catch (e: any) { setGen(g => g && ({ ...g, err: e.message })); }
  };
  const saveTarget = async () => {
    await api("/api/reports/target", { method: "PUT", body: { month: sc.weekly.month, target: tgt ? tgt * 10000 : null } });
    setTgtOpen(false); reloadSc(); message.success(tgt ? "已设置月度目标" : "已清除月度目标");
  };

  const busy = !!gen && !gen.err;
  const param = (key: string) => {
    if (!sc) return <Spin size="small" />;
    if (key === "weekly") return <>
      <Select size="small" value={week} onChange={setWeek} style={{ width: 150 }}
        options={sc.weekly.weeks.map((w: string, i: number) => ({ value: w, label: (i === 0 ? "本周 " : "") + weekLabel(w) }))} />
      <Button size="small" type="link" onClick={() => setTgtOpen(true)} style={{ padding: 0 }}>
        {sc.weekly.target ? `月目标 ${(sc.weekly.target / 10000).toFixed(0)} 万` : "设置月度目标"}</Button>
    </>;
    if (key === "campaign") return sc.campaign.campaigns.length
      ? <Select size="small" value={camp} onChange={setCamp} style={{ width: 230 }}
          options={sc.campaign.campaigns.map((c: any) => ({ value: c.id, label: `${c.name}（${md(c.start)}${c.end !== c.start ? "–" + md(c.end) : ""}）` }))} />
      : <span className="muted small">数据里没有已结束的活动</span>;
    return <Select size="small" value={prod} onChange={setProd} style={{ width: 260 }} popupMatchSelectWidth={false} showSearch optionFilterProp="label"
      options={sc.product.products.map((p: any) => ({ value: p.id, label: `${p.name}${p.severity_name ? (p.expected ? "（预期内）" : `（${p.severity_name}色预警）`) : ""}` }))} />;
  };

  const rows = (data || []).filter(r => filter === "all" || r.type === filter);
  return (
    <>
      <div className="page-head">
        <div><h1>报告中心</h1><div className="sub">三个报告场景，每个背后有一套分析剧本：平台算好数据和必备图，AI 按剧本写结论并按需补充图表，正文数字全部核对</div></div>
      </div>
      <div className="scene-cards">
        {SCENES.map(s => (
          <div className="scene-card" key={s.key}>
            <h3>{s.icon}{s.title}</h3>
            <div className="who">{s.who}</div>
            <ol>{s.qs.map(q => <li key={q}>{q}</li>)}</ol>
            <div className="foot">
              <Space size={6} wrap>{param(s.key)}</Space>
              <Button type="primary" size="small" style={{ marginLeft: "auto" }} disabled={busy || (s.key === "campaign" && !camp)} onClick={() => run(s.key)}>生成</Button>
            </div>
          </div>
        ))}
      </div>
      {gen && (
        <Card style={{ marginBottom: 16 }} title={<div className="ai-title"><span className="spark">AI</span>正在生成{SCENES.find(x => x.key === gen.scene)?.title}</div>}
          extra={gen.err ? <Tag color="error">{gen.err}</Tag> : <Button size="small" onClick={() => setGen(null)} disabled={busy}>关闭</Button>}>
          <div className="gen-steps">
            {gen.steps.map((s, i) => (
              <div key={i} className={"s" + (s.ai ? " ai" : "")}>
                {s.done || gen.err ? <CheckOutlined style={{ color: "#1f9d6b" }} /> : <LoadingOutlined />}
                {s.ai && <BarChartOutlined />}{s.t}
              </div>))}
          </div>
          {gen.text && <div style={{ maxHeight: 320, overflow: "auto", borderTop: "1px solid var(--line)", paddingTop: 8 }}>
            <Markdown text={gen.text.replace(/\[图表[:：]\s*c\d+\s*\]/g, "*（图表）*")} /></div>}
        </Card>
      )}
      {!data ? <Loading error={error} /> : (
        <Card title="报告列表" styles={{ body: { padding: 0 } }}
          extra={<Select size="small" value={filter} onChange={setFilter} style={{ width: 140 }}
            options={[{ value: "all", label: "全部场景" }, ...SCENES.map(s => ({ value: s.key, label: s.title }))]} />}>
          <Table rowKey="id" dataSource={rows} pagination={{ pageSize: 12, hideOnSinglePage: true }} rowClassName={() => "clickable-row"}
            onRow={r => ({ onClick: () => nav("/report/" + r.id) })}
            locale={{ emptyText: "还没有报告，选一个场景生成" }}
            columns={[
              { title: "标题", dataIndex: "title", render: t => <b>{t}</b> },
              { title: "场景", dataIndex: "type", width: 130, render: (t, r) => <Tag bordered={false} color={SCENE_COLOR[t]}>{r.scene_name || t}</Tag> },
              { title: "来源", dataIndex: "source", width: 110, render: s => s === "llm" ? "AI 撰写" : s === "rules" ? "规则生成" : "系统生成" },
              { title: "状态", dataIndex: "status", width: 90, render: s => <Tag bordered={false} color={s === "published" ? "success" : "default"}>{s === "published" ? "已发布" : "草稿"}</Tag> },
              { title: "生成时间", dataIndex: "created_at", width: 170, render: t => <span className="num muted">{new Date(t * 1000).toLocaleString("zh-CN")}</span>,
                sorter: (a, b) => a.created_at - b.created_at, defaultSortOrder: "descend" },
            ]} />
        </Card>
      )}
      <Modal open={tgtOpen} title={`${sc?.weekly.month?.slice(5) ? +sc.weekly.month.slice(5) : ""} 月 GMV 目标`} onCancel={() => setTgtOpen(false)} onOk={saveTarget} okText="保存">
        <p className="sec small">设置后，周度经营分析的「大盘趋势」一章会显示目标完成进度和全月预计。平台没有目标数据，需要手动填写。</p>
        <InputNumber value={tgt} onChange={v => setTgt(v as number | null)} min={0} addonAfter="万元" style={{ width: 200 }} placeholder="例如 650" />
      </Modal>
    </>
  );
}
