import { useMemo, useState } from "react";
import { Card, Empty, Segmented, Table, Tag, Timeline } from "antd";
import { Link, useParams, useSearchParams } from "react-router-dom";
import AIPanel from "../components/AIPanel";
import DecomposeTree from "../components/DecomposeTree";
import TrendChart, { Series } from "../components/TrendChart";
import { EV_SHORT, Sev, healthColor } from "../format";
import { Loading, useLoad } from "../hooks";

const METRICS: [string, string][] = [["gmv", "GMV"], ["uv", "访客数"], ["cvr", "支付转化率"], ["price", "到手价 vs 竞品"], ["stock", "库存"], ["rating", "评分"]];
const SEVN: Record<string, string> = { red: "红", yellow: "黄", blue: "蓝" };

function Variants({ v }: { v: any }) {
  if (!v.available) return <div className="muted">{v.note}</div>;
  return (
    <Table size="small" rowKey="variant_id" pagination={false} dataSource={v.variants} columns={[
      { title: "规格", dataIndex: "name", render: (n, x: any) => <>{n}{x.stockout_days ? <Tag color="error" bordered={false} style={{ marginLeft: 6 }}>断货 {x.stockout_days} 天</Tag> : null}</> },
      { title: "近 7 日销量", dataIndex: "units_cur", align: "right", render: n => <span className="num">{n.toLocaleString("zh-CN")}</span> },
      { title: "占比", dataIndex: "share_cur", align: "right", render: (s, x: any) => <span className="num">{(s * 100).toFixed(0)}%<div className="muted small">平时 {(x.baseline_share * 100).toFixed(0)}%</div></span> },
      { title: "库存", dataIndex: "stock_now", align: "right", render: n => n != null ? n.toLocaleString("zh-CN") : "—" },
      { title: "可售天数", dataIndex: "days_of_supply", align: "right", render: (n, x: any) => n ?? (x.stock_now === 0 ? "0" : "—") },
    ]} />
  );
}

function Events({ d }: { d: any }) {
  const items = [
    ...d.events.map((e: any) => ({ date: e.date, txt: e.description + (e.future ? "（预计）" : ""), tag: "事件", color: "gray" })),
    ...d.actions.map((a: any) => ({ date: a.exec_date || a.adopted_date || "", txt: `${a.name}（${a.status_name}）`, tag: "动作", color: "green" })),
    ...(d.cards || []).map((c: any) => ({ date: c.first_date, txt: `${SEVN[c.severity]}色预警：${c.rule_names.join("、")}（${c.status_name}）`, tag: "预警", color: c.severity === "red" ? "red" : "orange" })),
  ].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 10);
  if (!items.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无" />;
  return (
    <Timeline style={{ marginTop: 8 }} items={items.map(i => ({
      color: i.color,
      children: <div style={{ fontSize: 13 }}><span className="muted num" style={{ marginRight: 8 }}>{i.date.slice(5)}</span><Tag bordered={false}>{i.tag}</Tag>{i.txt}</div>,
    }))} />
  );
}

export default function ProductDetail() {
  const { pid } = useParams();
  const [sp] = useSearchParams();
  const { data: d, error } = useLoad<any>("/api/products/" + pid);
  const [metric, setMetric] = useState("gmv");

  const chart = useMemo(() => {
    if (!d) return null;
    const s = d.series;
    const events = d.events.filter((e: any) => !e.future).map((e: any) => ({ date: e.date, label: e.description, short: EV_SHORT[e.type] || "事" }));
    let series: Series[]; let kind: any = "num"; let zeroBase = true;
    if (metric === "price") { series = [{ name: "自家到手价", values: s.price }, { name: "竞品到手价", values: s.comp_price, dash: true, color: "#eb6834" }]; kind = "price"; zeroBase = false; }
    else if (metric === "cvr") { series = [{ name: "支付转化率", values: s.cvr }]; kind = "pct"; zeroBase = false; }
    else if (metric === "rating") { series = [{ name: "评分", values: s.rating }]; kind = "dec"; zeroBase = false; }
    else { series = [{ name: METRICS.find(m => m[0] === metric)![1], values: s[metric] }]; kind = metric === "gmv" ? "money" : "num"; }
    return { dates: s.dates, series, kind, zeroBase, events };
  }, [d, metric]);

  if (!d) return <Loading error={error} />;
  const h = d.health;
  const card = d.card && d.card.is_today ? d.card : null;
  const dec = d.decompose;
  const avail = METRICS.filter(m => m[0] === "gmv" || d.series[m[0]]);

  return (
    <>
      <div style={{ marginBottom: 10 }}><Link to="/products" className="small">← 商品</Link></div>
      <Card>
        <div className="detail-head">
          <div>
            <h1>{d.product_name}</h1>
            <div className="tags">
              <Tag color="blue" bordered={false}>{d.tier}</Tag>
              <Tag bordered={false}>{d.lifecycle}{d.coef > 1 ? ` · 预警阈值 ×${d.coef}` : ""}</Tag>
              <Tag bordered={false}>{d.category} · {d.sub_category || ""}</Tag>
              <Tag bordered={false}>上市 {d.launch_date}</Tag>
              {d.margin != null && <Tag bordered={false}>毛利率 {(d.margin * 100).toFixed(0)}%</Tag>}
              {card && <><Sev s={card.severity} /><Tag bordered={false}>{card.rule_names.join(" · ")}</Tag></>}
            </div>
          </div>
          <div className="hs">
            <div className="muted small">健康度</div>
            <div className="big" style={{ color: healthColor(h.level) }}>{h.score}</div>
            <div className="parts">{h.parts.map((p: any) => <span key={p.key}>{p.name} {p.score}/{p.full}</span>)}</div>
          </div>
        </div>
      </Card>
      <div className="grid g-main mt">
        <div className="grid">
          <Card title={<>指标趋势<span className="hint">浅色底纹为近 7 日 · 虚线为事件</span></>}
            extra={<Segmented size="small" value={metric} onChange={v => setMetric(String(v))} options={avail.map(m => ({ label: m[1], value: m[0] }))} />}>
            {chart && <TrendChart dates={chart.dates} series={chart.series} kind={chart.kind} zeroBase={chart.zeroBase} events={chart.events} height={260} />}
          </Card>
          <Card styles={{ body: { padding: "12px 12px 16px" } }} title={<>指标拆解树<span className="hint">GMV = 访客数 × 支付转化率 × 客单价 · {dec.cur_window} 对比 {dec.prev_window}</span></>}>
            <DecomposeTree d={d} />
          </Card>
          <div className="grid g2">
            <Card title="规格" styles={{ body: { paddingTop: 8 } }}><Variants v={d.variants} /></Card>
            <Card title="事件与动作" styles={{ body: { paddingTop: 8 } }}><Events d={d} /></Card>
          </div>
        </div>
        <div><AIPanel detail={d} card={card} autoRun={sp.get("diag") === "1" || !!d.diagnosis_cached} /></div>
      </div>
    </>
  );
}
