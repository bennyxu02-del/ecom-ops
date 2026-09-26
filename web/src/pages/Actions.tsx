import { App, Button, Card, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../App";
import { Delta, num } from "../format";
import { HandoffTag } from "../components/HandoffSendModal";
import { Loading, useLoad } from "../hooks";

const uniq = (xs: any[]) => [...new Set(xs)].filter(Boolean).map(v => ({ text: v, value: v }));

export default function Actions() {
  const { data, error, reload } = useLoad<any[]>("/api/actions");
  const { message } = App.useApp();
  const { refreshMeta } = useApp();
  if (!data) return <Loading error={error} />;

  const exec = async (id: number) => {
    await api(`/api/actions/${id}`, { method: "PATCH", body: { status: "executed" } });
    message.success("已标记执行，平台将从今天起跟踪效果");
    reload(); refreshMeta();
  };
  const f = (a: any, v: any) => v == null ? "—" : a.track_metric === "cvr" ? (v * 100).toFixed(2) + "%" : num(v);

  const columns: ColumnsType<any> = [
    { title: "商品", dataIndex: "product_name", width: 170, render: (n, a) => <Link to={"/product/" + a.product_id}>{n}</Link> },
    { title: "方案", dataIndex: "name", width: 230, render: (n, a) => <>{n}<div className="muted small">{a.target || ""}</div></> },
    { title: "原因", dataIndex: "cause_name", width: 110, filters: uniq(data.map(a => a.cause_name)), onFilter: (v, a) => a.cause_name === v },
    { title: "分工与进度", key: "prog", width: 230, render: (_, a) => {
        const p = a.progress || {};
        if (a.status === "rejected") return <span className="muted">—</span>;
        return <div style={{ display: "grid", gap: 4 }}>
          {p.mine?.length > 0 && <span className="small">我的步骤 {a.status === "executed" && !a.step_done?.length ? p.mine.length : p.mine_done.length}/{p.mine.length}</span>}
          {(a.handoffs || []).map((h: any) => <span key={h.id} className="small">{h.kind_name} · {h.role} <HandoffTag h={h} /></span>)}
          {!p.mine?.length && !(a.handoffs || []).length && <span className="small muted">{a.exec_type}{a.owner_role ? ` · ${a.owner_role}` : ""}</span>}
        </div>;
      } },
    { title: "状态", dataIndex: "status_name", width: 130, filters: uniq(data.map(a => a.status_name)), onFilter: (v, a) => a.status_name === v,
      render: (s, a) => <><Tag bordered={false} color={a.status === "executed" ? "success" : a.status === "rejected" || a.status === "declined" ? "default" : "processing"}>{s}</Tag>
        {a.reject_reason && <div className="muted small">{a.reject_reason}</div>}</> },
    { title: "采纳 / 执行", dataIndex: "adopted_date", width: 140, sorter: (a, b) => (a.adopted_date || "").localeCompare(b.adopted_date || ""),
      render: (_, a) => <span className="num small" style={{ whiteSpace: "nowrap" }}>采纳 {a.adopted_date || "—"}<br />执行 {a.exec_date || "—"}</span> },
    { title: "效果跟踪（执行前 → 后）", key: "effect", width: 220,
      render: (_, a) => {
        const e = a.effect;
        if (!e?.metric_name) return <span className="muted">—</span>;
        return <><div className="small sec">{e.metric_name}</div>
          <span className="num">{f(a, e.before)} → {f(a, e.after)}</span>{" "}
          {e.status === "已完成" ? <Delta v={e.change_pct} /> : <span className="muted small">{e.status}</span>}</>;
      } },
    { title: "", key: "op", width: 110,
      render: (_, a) => (a.status === "adopted" || (a.status === "transferred" && !a.exec_date))
        ? <Button size="small" onClick={() => exec(a.id)} style={{ borderColor: "#0ca30c", color: "#0ca30c" }}>标记已执行</Button> : null },
  ];

  return (
    <>
      <div className="page-head"><div><h1>行动跟踪</h1>
        <div className="sub">记录每个方案的处理结果；我的步骤和协同事项全部完成后，自动对比执行前后的跟踪指标（前后对比，不等同于严格的因果效果）</div></div></div>
      <Card styles={{ body: { padding: 0 } }}>
        <Table rowKey="id" dataSource={data} columns={columns} pagination={false} scroll={{ x: 1270 }} locale={{ emptyText: "暂无动作记录" }} />
      </Card>
    </>
  );
}
