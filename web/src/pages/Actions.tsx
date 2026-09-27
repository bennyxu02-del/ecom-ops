import { useEffect, useState } from "react";
import { Badge, Button, Card, Segmented, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { PlusOutlined } from "@ant-design/icons";
import { Link, useSearchParams } from "react-router-dom";
import { useApp } from "../App";
import { Delta, num } from "../format";
import ActionDrawer, { SourceTag } from "../components/ActionDrawer";
import TodoModal from "../components/TodoModal";
import { MineView, StageTag, flatHandoffs } from "../components/TodoViews";
import { Loading, useLoad } from "../hooks";

/** 「我要做的」数量：待复盘 + 同事疑问 + 未通知的协同 + 我没勾的步骤（导航角标同此口径） */
export function mineCount(actions: any[]) {
  const doing = actions.filter(a => a.status === "doing");
  const steps = doing.reduce((n, a) => n + (a.progress?.mine?.length || 0) - (a.progress?.mine_done?.length || 0), 0);
  const hs = flatHandoffs(doing).filter(h => ["question", "pending"].includes(h.status)).length;
  return steps + hs + actions.filter(a => a.status === "review").length;
}

const FILTERS: [string, string, (a: any) => boolean][] = [
  ["open", "未结束", a => ["doing", "tracking", "review"].includes(a.status)],
  ["doing", "执行中", a => a.status === "doing"],
  ["waiting", "等同事", a => a.status === "doing" && (a.handoffs || []).some((h: any) => ["notified", "question", "pending"].includes(h.status))],
  ["tracking", "跟踪中", a => a.status === "tracking"],
  ["review", "待复盘", a => a.status === "review"],
  ["done", "已完成", a => a.status === "done"],
  ["cancelled", "已取消", a => a.status === "cancelled"],
  ["all", "全部", () => true],
];

export default function Actions() {
  const { data, error, reload, setData } = useLoad<any[]>("/api/actions");
  const { refreshMeta } = useApp();
  const [sp, setSp] = useSearchParams();
  const [openId, setOpenId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [view, setView] = useState("mine");
  const [filter, setFilter] = useState("open");

  useEffect(() => {      // 链接参数（?view= / ?filter= / ?open=）用过即清
    const v = sp.get("view"), o = sp.get("open"), f = sp.get("filter");
    if (!v && !o && !f) return;
    if (v === "waiting") { setView("all"); setFilter("waiting"); } else if (v) setView(v);
    if (f) { setView("all"); setFilter(f); }
    if (o) setOpenId(Number(o));
    if (data) reload();
    sp.delete("view"); sp.delete("open"); sp.delete("filter"); setSp(sp, { replace: true });
  }, [sp]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return <Loading error={error} />;

  const upsert = (a: any) => setData(xs => { const has = (xs || []).some(x => x.id === a.id); return has ? xs!.map(x => x.id === a.id ? a : x) : [a, ...(xs || [])]; });
  const f = (a: any, v: any) => v == null ? "—" : a.track_metric === "cvr" ? (v * 100).toFixed(2) + "%" : num(v);
  const open = data.find(a => a.id === openId) || null;
  const rows = data.filter(FILTERS.find(x => x[0] === filter)![2]);
  const reloadAll = () => { reload(); refreshMeta(); };

  const columns: ColumnsType<any> = [
    { title: "待办", dataIndex: "name", width: 250, render: (n, a) => <>
        <div className="todo-name">{n}</div>
        <div className="muted small" style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 2 }}><SourceTag a={a} />{a.cause_name}</div>
      </> },
    { title: "商品", dataIndex: "product_name", width: 150, render: (n, a) => <Link to={"/product/" + a.product_id} onClick={e => e.stopPropagation()}>{n}</Link> },
    { title: "阶段", key: "stage", width: 130, render: (_, a) => <StageTag a={a} /> },
    { title: "分工进度", key: "prog", width: 210, render: (_, a) => {
        const p = a.progress || {};
        if (a.status !== "doing") return <span className="muted small">{a.status === "cancelled" ? "—" : "全部完成"}</span>;
        return <div style={{ display: "grid", gap: 4 }}>
          {p.mine?.length > 0 && <span className="small">我 {p.mine_done.length}/{p.mine.length}</span>}
          {(a.handoffs || []).map((h: any) => <span key={h.id} className="small">{h.role} · {h.status_name}</span>)}
        </div>;
      } },
    { title: "截止", dataIndex: "due_date", width: 110, sorter: (a, b) => (a.due_date || "9").localeCompare(b.due_date || "9"),
      render: (d, a) => d ? <span className="num small">{d}{a.overdue && <div><Tag color="error" bordered={false} style={{ marginTop: 2 }}>已逾期</Tag></div>}</span> : <span className="muted">—</span> },
    { title: "效果（执行前 → 后）", key: "effect", width: 210,
      render: (_, a) => {
        const e = a.effect;
        if (!e?.metric_name) return <span className="muted small">完成后跟踪 {a.track_days} 天</span>;
        return <><div className="small sec">{e.metric_name}</div>
          <span className="num">{f(a, e.before)} → {f(a, e.after)}</span>{" "}
          {e.status === "已完成" ? <Delta v={e.change_pct} /> : <span className="muted small">{e.status}</span>}</>;
      } },
  ];

  return (
    <>
      <div className="page-head">
        <div><h1>待办中心</h1>
          <div className="sub">待办走四步：执行中 → 跟踪中 → 待复盘 → 已完成；分给同事的步骤通过飞书协同</div></div>
        <div className="right"><Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>新建待办</Button></div>
      </div>
      <Segmented className="view-tabs" value={view} onChange={v => setView(String(v))} options={[
        { value: "mine", label: <span>我要做的 <Badge count={mineCount(data)} size="small" color="#2a78d6" /></span> },
        { value: "all", label: <span>全部待办 <span className="muted small">{data.length}</span></span> },
      ]} />
      {view === "mine" && <MineView actions={data} onOpen={setOpenId} reload={reloadAll} />}
      {view === "all" && <>
        <div className="filter-chips">
          {FILTERS.map(([k, label, fn]) => (
            <Tag.CheckableTag key={k} checked={filter === k} onChange={() => setFilter(k)}>{label} {data.filter(fn).length}</Tag.CheckableTag>
          ))}
        </div>
        <Card styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" dataSource={rows} columns={columns} pagination={false} scroll={{ x: 1070 }} locale={{ emptyText: "暂无待办" }}
            rowClassName={() => "clickable"} onRow={a => ({ onClick: () => setOpenId(a.id) })} />
        </Card>
      </>}
      <ActionDrawer action={open} onClose={() => setOpenId(null)} onChanged={a => { if (a) upsert(a); else reload(); }} />
      <TodoModal open={creating} source="manual" onClose={() => setCreating(false)}
        onCreated={a => { setCreating(false); upsert(a); setOpenId(a.id); }} />
    </>
  );
}
