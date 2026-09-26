import { Button, Card, Empty, Tag, Tooltip } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { useNavigate, Link } from "react-router-dom";
import TrendChart from "../components/TrendChart";
import { Delta, Sev, healthColor, money, EV_SHORT } from "../format";
import { Loading, useLoad } from "../hooks";

function Kpi({ label, value, change, tip }: { label: string; value: string; change: number; tip: string }) {
  return (
    <Card className="kpi" size="small" styles={{ body: { padding: 16 } }}>
      <div className="label">{label}<Tooltip title={tip}><QuestionCircleOutlined style={{ color: "#8a8f98", fontSize: 12 }} /></Tooltip></div>
      <div className="value">{value}</div>
      <div className="foot"><Delta v={change} /> <span>较前 7 日</span></div>
    </Card>
  );
}

export function AlertRow({ c, onClick, extra, children }: { c: any; onClick: () => void; extra?: React.ReactNode; children?: React.ReactNode }) {
  const bar = c.severity === "red" ? "#d03b3b" : c.severity === "yellow" ? "#fab219" : "#2a78d6";
  return (
    <div className="alert-card" onClick={onClick} role="button" tabIndex={0} onKeyDown={e => e.key === "Enter" && onClick()}>
      <div className="bar" style={{ background: bar }} />
      <div>{children}</div>
      <div className="side-r">{extra}</div>
    </div>
  );
}

export default function Overview() {
  const { data: o, error } = useLoad<any>("/api/overview");
  const nav = useNavigate();
  if (!o) return <Loading error={error} />;
  const k = o.kpi;
  const hsum = (o.health.健康 + o.health.关注 + o.health.风险) || 1;
  const events = o.events.map((e: any) => ({ date: e.date, label: e.description, short: EV_SHORT[e.type] || "事" }));
  return (
    <>
      <div className="page-head"><div><h1>经营总览</h1>
        <div className="sub">重点商品池 {o.focus_count} 个商品（共 {o.product_count} 个）· 近 7 日 {o.cur_window} 对比 {o.prev_window}</div></div></div>
      <div className="grid g4">
        <Kpi label="重点商品 GMV" value={money(k.gmv.cur)} change={k.gmv.change} tip="GMV = 支付金额，按支付时间归日，不含运费，不扣退款" />
        <Kpi label="访客数" value={k.uv.cur.toLocaleString("zh-CN")} change={k.uv.change} tip="商品详情页按日去重访客，多日为日访客之和" />
        <Kpi label="支付转化率" value={(k.cvr.cur * 100).toFixed(2) + "%"} change={k.cvr.change} tip="支付买家数 ÷ 访客数，窗口内先汇总再相除" />
        <Kpi label="客单价" value={"¥" + k.aov.cur.toFixed(2)} change={k.aov.change} tip="GMV ÷ 支付买家数" />
      </div>
      <div className="grid g-main mt">
        <Card title={<>重点商品池 GMV 趋势<span className="hint">浅色底纹为近 7 日 · 虚线为事件 · 可拖动下方滑块缩放</span></>}>
          <TrendChart dates={o.dates} kind="money" height={260} events={events}
            series={[{ name: "GMV", values: o.trend.map((t: any) => t.gmv) }]} />
        </Card>
        <div className="grid">
          <Card title="今日预警" extra={<Link to="/alerts">全部 →</Link>}>
            <div style={{ display: "flex", gap: 18, alignItems: "center" }}>
              {["red", "yellow", "blue"].map(s => (
                <div key={s}><div className="num" style={{ fontSize: 24, fontWeight: 650 }}>{o.alerts.today[s]}</div><Sev s={s} /></div>
              ))}
              <div style={{ marginLeft: "auto", textAlign: "right" }}>
                <div className="num" style={{ fontSize: 24, fontWeight: 650 }}>{o.alerts.pending}</div><span className="muted small">待处理</span>
              </div>
            </div>
          </Card>
          <Card title={<>健康度分布<span className="hint">重点商品池</span></>}>
            <div className="health-bar">
              {["健康", "关注", "风险"].map(l => o.health[l] ? <div key={l} style={{ width: `${o.health[l] / hsum * 100}%`, background: healthColor(l) }} /> : null)}
            </div>
            <div style={{ display: "flex", gap: 16, marginTop: 10 }} className="small">
              {["健康", "关注", "风险"].map(l => <span key={l} className="hscore"><i style={{ background: healthColor(l) }} />{l} {o.health[l]}</span>)}
            </div>
          </Card>
        </div>
      </div>
      <Card className="mt" title={<>今日待办<span className="hint">AI 已完成扫描，按严重度与影响金额排序；点击进入诊断</span></>} styles={{ body: { padding: 0 } }}>
        {o.todo.length ? o.todo.map((c: any) => (
          <AlertRow key={c.id} c={c} onClick={() => nav(`/product/${c.product_id}?diag=1`)}
            extra={<><Tag bordered={false}>{c.status_name}</Tag><Button size="small" type="primary">AI 诊断</Button></>}>
            <div className="t"><Sev s={c.severity} /> {c.product_name} <Tag bordered={false}>{c.rule_names.join(" · ")}</Tag></div>
            <div className="d">首次触发 {c.first_date} · 持续 {c.trigger_days} 天{c.gmv_impact > 0 ? ` · 影响 GMV 约 ${money(c.gmv_impact)}（估算）` : ""}</div>
          </AlertRow>
        )) : <Empty style={{ padding: 32 }} description="今天没有待处理的预警" />}
      </Card>
    </>
  );
}
