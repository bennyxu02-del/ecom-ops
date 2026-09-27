import { useState } from "react";
import { App, Button, Card, Empty, InputNumber, Modal, Progress, Segmented, Tag, Tooltip } from "antd";
import { ArrowDownOutlined, ArrowUpOutlined, QuestionCircleOutlined } from "@ant-design/icons";
import { useNavigate, Link } from "react-router-dom";
import { api } from "../api";
import ReportChart from "../components/ReportChart";
import TrendChart from "../components/TrendChart";
import { Delta, Sev, money, wan, EV_SHORT } from "../format";
import { Loading, useLoad } from "../hooks";

function Kpi({ label, value, change, tip }: { label: string; value: string; change: number; tip: string }) {
  return (
    <Card className="kpi" size="small" styles={{ body: { padding: 16 } }}>
      <div className="label">{label}<Tooltip title={tip}><QuestionCircleOutlined style={{ color: "#8a8f98", fontSize: 12 }} /></Tooltip></div>
      <div className="value">{value}</div>
      <div className="foot"><Delta v={change} /> <span>较前 7 天</span></div>
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

const TONE: Record<string, string> = { bad: "error", warn: "warning", good: "success", main: "processing", muted: "default" };

function Target({ t, onSet }: { t: any; onSet: () => void }) {
  if (!t?.target) return (
    <div className="ov-target empty">
      <div className="small muted">{t?.month_name}全店 GMV 目标</div>
      <Button size="small" onClick={onSet}>设置目标</Button>
      <div className="small muted">设置后显示完成进度和月底预计</div>
    </div>
  );
  return (
    <div className="ov-target">
      <div className="small muted">{t.month_name}全店目标 {wan(t.target)} · 已完成 {wan(t.mtd)}
        <Button type="link" size="small" onClick={onSet} style={{ padding: "0 4px", height: "auto" }}>修改</Button></div>
      <Progress percent={Math.round(t.progress * 1000) / 10} success={{ percent: Math.round(t.time_progress * 1000) / 10 }}
        showInfo={false} strokeColor={t.on_track ? "#1baf7a" : "#eb6834"} size="small" />
      <div className="small">完成 <b>{(t.progress * 100).toFixed(1)}%</b>（时间过去 {(t.time_progress * 100).toFixed(0)}%）</div>
      <div className="small" style={{ color: t.on_track ? "#17613f" : "#a14a14" }}>
        按现在的节奏月底约 {wan(t.forecast)}，{t.on_track ? "能完成" : `还差 ${wan(-t.gap)}`}（剩 {t.days_left} 天）</div>
    </div>
  );
}

function Mover({ x, nav }: { x: any; nav: (p: string) => void }) {
  return (
    <div className="mover" onClick={() => nav(`/product/${x.product_id}`)} role="button">
      <div className="mt1">
        <b>{x.product_name}</b>
        <Tag bordered={false} color={TONE[x.tone]}>{x.label}</Tag>
        <span className={"amt " + (x.change < 0 ? "neg" : "pos")}>{x.change < 0 ? "-" : "+"}{money(Math.abs(x.change)).slice(1)} 元
          <span className="muted small">（{x.change_pct != null ? (x.change_pct > 0 ? "+" : "") + (x.change_pct * 100).toFixed(1) + "%" : "—"}）</span></span>
      </div>
      <div className="small sec">{x.reason}</div>
      {x.alert && ["pending", "opportunity"].includes(x.alert.group) && (
        <Link to={`/alerts?open=${x.alert.id}`} onClick={e => e.stopPropagation()} className="small">预警{x.alert.status_name}，去处理 →</Link>)}
      {x.alert && x.alert.group === "doing" && <span className="small muted">预警处理中</span>}
    </div>
  );
}

export default function Overview() {
  const [scope, setScope] = useState<string>(() => { try { return localStorage.getItem("ov_scope") || "focus"; } catch { return "focus"; } });
  const { data: o, error, reload } = useLoad<any>("/api/overview?scope=" + scope);
  const [trendBy, setTrendBy] = useState("sum");
  const [tgtOpen, setTgtOpen] = useState(false);
  const [tgt, setTgt] = useState<number | null>(null);
  const nav = useNavigate();
  const { message } = App.useApp();
  if (!o) return <Loading error={error} />;
  const k = o.kpi;
  const events = o.events.map((e: any) => ({ date: e.date, label: e.description, short: EV_SHORT[e.type] || "事" }));
  const tv = o.todos;
  const saveTarget = async () => {
    await api("/api/reports/target", { method: "PUT", body: { month: o.target.month, target: tgt ? tgt * 10000 : null } });
    setTgtOpen(false); reload(); message.success(tgt ? "已设置月度目标" : "已清除月度目标");
  };
  const changeScope = (v: string) => { setScope(v); try { localStorage.setItem("ov_scope", v); } catch { /* 忽略 */ } };
  const TODO_TILES: [string, number, string, string][] = [
    ["逾期", tv.overdue, "/actions?view=mine", "bad"], ["今天到期", tv.due_today, "/actions?view=mine", "warn"],
    ["待复盘", tv.review, "/actions?view=all&filter=review", "main"], ["同事有疑问", tv.question, "/actions?view=mine", "warn"],
    ["等同事处理", tv.waiting, "/actions?view=all&filter=waiting", "muted"],
  ];

  return (
    <>
      <div className="page-head">
        <div><h1>经营总览</h1>
          <div className="sub">{o.scope === "focus" ? `重点商品池 ${o.focus_count} 个商品（共 ${o.product_count} 个）` : `全店 ${o.product_count} 个商品`} · 近 7 天 {o.cur_window} 对比 {o.prev_window}</div></div>
        <div className="right"><Segmented value={scope} onChange={v => changeScope(String(v))}
          options={[{ value: "focus", label: "重点商品" }, { value: "all", label: "全店" }]} /></div>
      </div>

      {/* ① 生意怎么样 */}
      <Card className="ov-head" styles={{ body: { padding: 0 } }}>
        <div className="ov-head-in">
          <div className="ov-verdict">
            <div className="small muted" style={{ marginBottom: 6 }}>今日结论<Tooltip title="由平台按数据规则生成，数字全部来自计算结果"><QuestionCircleOutlined style={{ marginLeft: 6 }} /></Tooltip></div>
            <div className="txt">{o.headline}</div>
          </div>
          <Target t={o.target} onSet={() => { setTgt(o.target?.target ? o.target.target / 10000 : null); setTgtOpen(true); }} />
        </div>
      </Card>
      <div className="grid g4 mt">
        <Kpi label={`${o.scope_name} GMV`} value={money(k.gmv.cur)} change={k.gmv.change} tip="GMV = 支付金额，按支付时间归日，不含运费，不扣退款" />
        <Kpi label="访客数" value={k.uv.cur.toLocaleString("zh-CN")} change={k.uv.change} tip="商品详情页按日去重访客，多日为日访客之和" />
        <Kpi label="支付转化率" value={(k.cvr.cur * 100).toFixed(2) + "%"} change={k.cvr.change} tip="支付买家数 ÷ 访客数，窗口内先汇总再相除" />
        <Kpi label="客单价" value={"¥" + k.aov.cur.toFixed(2)} change={k.aov.change} tip="GMV ÷ 支付买家数" />
      </div>

      {/* ② 为什么变 */}
      <div className="grid g-main mt">
        <Card title={<>GMV 趋势<span className="hint">浅色底纹为近 7 天 · 虚线为事件</span></>}
          extra={<Segmented size="small" value={trendBy} onChange={v => setTrendBy(String(v))} options={[{ value: "sum", label: "合计" }, { value: "tier", label: "按分层" }]} />}>
          <TrendChart dates={o.dates} kind="money" height={280} events={events}
            series={trendBy === "sum" ? [{ name: "GMV", values: o.trend.map((t: any) => t.gmv) }] : o.tier_trend} />
        </Card>
        <Card title={<>变化拆解<span className="hint">GMV = 访客 × 转化率 × 客单价</span></>}>
          <div className="ov-wf"><ReportChart spec={o.waterfall} compact /></div>
          <div className="ov-factors">
            {o.factors.map((f: any) => (
              <div key={f.key}><span>{f.name}</span><span className={f.change_pct >= 0 ? "pos" : "neg"}>{f.change_pct != null ? (f.change_pct > 0 ? "+" : "") + (f.change_pct * 100).toFixed(1) + "%" : "—"}</span>
                <span className={f.amount >= 0 ? "pos" : "neg"}>{f.amount >= 0 ? "+" : "-"}{wan(Math.abs(f.amount))}</span></div>))}
          </div>
        </Card>
      </div>
      <div className="grid g2 mt">
        <Card title={<><ArrowDownOutlined style={{ color: "#d03b3b", marginRight: 6 }} />拖累最多的商品<span className="hint">近 7 天 GMV 变化</span></>} styles={{ body: { padding: "4px 0" } }}>
          {o.movers.down.length ? o.movers.down.map((x: any) => <Mover key={x.product_id} x={x} nav={nav} />) : <Empty style={{ padding: 20 }} description="没有下滑的商品" />}
        </Card>
        <Card title={<><ArrowUpOutlined style={{ color: "#1baf7a", marginRight: 6 }} />拉动最多的商品<span className="hint">近 7 天 GMV 变化</span></>} styles={{ body: { padding: "4px 0" } }}>
          {o.movers.up.length ? o.movers.up.map((x: any) => <Mover key={x.product_id} x={x} nav={nav} />) : <Empty style={{ padding: 20 }} description="没有增长的商品" />}
        </Card>
      </div>

      {/* ③ 今天做什么 */}
      <div className="grid g-main mt">
        <Card title={<>待你决定的预警<span className="hint">待决定 {o.alerts.pending} 条{o.alerts.opportunity ? `，机会 ${o.alerts.opportunity} 条` : ""}；点击打开处理面板</span></>}
          styles={{ body: { padding: 0 } }} extra={<Link to="/alerts">预警中心 →</Link>}>
          {o.alerts.items.length ? o.alerts.items.map((c: any) => (
            <AlertRow key={c.id} c={c} onClick={() => nav(`/alerts?open=${c.id}`)}
              extra={<Button size="small" type="primary">去处理</Button>}>
              <div className="t"><Sev s={c.severity} /> {c.product_name} <Tag bordered={false}>{c.rule_names.join(" · ")}</Tag>
                {c.group === "opportunity" && <Tag bordered={false} color="green">机会</Tag>}
                {c.reopen_name && <Tag bordered={false} color="warning">{c.reopen_name}</Tag>}</div>
              <div className="d">已持续 {c.days_open} 天{c.gmv_impact > 0 && c.group !== "opportunity" ? ` · 近 7 天影响 GMV 约 ${money(c.gmv_impact)}` : ""}</div>
            </AlertRow>
          )) : <Empty style={{ padding: 32 }} description="没有待决定的预警" />}
        </Card>
        <div className="grid">
          <Card title="待办提醒" extra={<Link to="/actions">待办中心 →</Link>}>
            <div className="ov-todos">
              {TODO_TILES.map(([l, n, to, tone]) => (
                <div key={l} className={"tile" + (n ? " on t-" + tone : "")} onClick={() => nav(to)} role="button">
                  <b>{n}</b><span>{l}</span></div>))}
            </div>
          </Card>
          <Card title={<>节前库存检查<span className="hint">未来 30 天的节日 / 活动</span></>}>
            {o.upcoming.length ? o.upcoming.map((e: any) => (
              <div key={e.name} className="ov-up">
                <div className="h"><b>{e.name}</b><span className="muted small">{e.date.slice(5).replace("-", "/")} · 还有 {e.days} 天</span>
                  {e.short_total ? <Tag bordered={false} color="error">{e.short_total} 个商品库存撑不到</Tag> : <Tag bordered={false} color="success">库存够</Tag>}</div>
                {e.short.map((x: any) => (
                  <div key={x.product_id} className="small row" onClick={() => nav(`/product/${x.product_id}`)} role="button">
                    <span>{x.product_name}</span><span className="muted">可售 {x.days_of_supply} 天</span>
                    <Tag bordered={false} color={x.transit ? "success" : x.can_restock ? "warning" : "error"}>{x.transit || x.status}</Tag>
                  </div>))}
              </div>)) : <div className="muted small">未来 30 天没有节日或活动</div>}
            {o.upcoming.length > 0 && <div className="small muted" style={{ marginTop: 6 }}>按主力规格的库存可售天数判断；补货周期 {o.upcoming[0].lead_days} 天</div>}
          </Card>
        </div>
      </div>
      <Modal open={tgtOpen} title={`${o.target?.month_name} 全店 GMV 目标`} onCancel={() => setTgtOpen(false)} onOk={saveTarget} okText="保存">
        <p className="sec small">设置后，总览显示完成进度和月底预计，周度经营分析也会用到。平台没有目标数据，需要手动填写。</p>
        <InputNumber value={tgt} onChange={v => setTgt(v as number | null)} min={0} addonAfter="万元" style={{ width: 200 }} placeholder="例如 650" />
      </Modal>
    </>
  );
}
