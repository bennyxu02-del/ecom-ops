import { useEffect, useState } from "react";
import { App, Badge, Button, Card, Segmented, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { PlusOutlined } from "@ant-design/icons";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../App";
import { Delta, num } from "../format";
import { HandoffTag } from "../components/HandoffSendModal";
import ActionDrawer, { SourceTag, isOverdue, statusColor } from "../components/ActionDrawer";
import TodoModal from "../components/TodoModal";
import { MineView, WaitingView, flatHandoffs } from "../components/TodoViews";
import { Loading, useLoad } from "../hooks";

const uniq = (xs: any[]) => [...new Set(xs)].filter(Boolean).map(v => ({ text: v, value: v }));

export default function Actions() {
  const { data, error, reload, setData } = useLoad<any[]>("/api/actions");
  const { message } = App.useApp();
  const { refreshMeta, asOf } = useApp();
  const [sp, setSp] = useSearchParams();
  const [openId, setOpenId] = useState<number | null>(sp.get("open") ? Number(sp.get("open")) : null);
  const [creating, setCreating] = useState(false);
  const [view, setView] = useState(sp.get("view") || "all");

  useEffect(() => {      // 链接参数（?view= / ?open=）用过即清，页面内切换视图不受影响
    const v = sp.get("view"), o = sp.get("open");
    if (!v && !o) return;
    if (v) setView(v);
    if (o) setOpenId(Number(o));
    if (data) reload();
    sp.delete("view"); sp.delete("open"); setSp(sp, { replace: true });
  }, [sp]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return <Loading error={error} />;

  const exec = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    await api(`/api/actions/${id}`, { method: "PATCH", body: { status: "executed" } });
    message.success("已标记执行，平台将从今天起跟踪效果");
    reload(); refreshMeta();
  };
  const upsert = (a: any) => setData(xs => { const has = (xs || []).some(x => x.id === a.id); return has ? xs!.map(x => x.id === a.id ? a : x) : [a, ...(xs || [])]; });
  const f = (a: any, v: any) => v == null ? "—" : a.track_metric === "cvr" ? (v * 100).toFixed(2) + "%" : num(v);
  const open = data.find(a => a.id === openId) || null;

  const columns: ColumnsType<any> = [
    { title: "商品", dataIndex: "product_name", width: 160, render: (n, a) => <Link to={"/product/" + a.product_id} onClick={e => e.stopPropagation()}>{n}</Link> },
    { title: "待办", dataIndex: "name", width: 250, render: (n, a) => <>
        <div className="todo-name">{n}</div>
        <div className="muted small" style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 2 }}><SourceTag a={a} />{a.cause_name}</div>
      </> },
    { title: "分工与进度", key: "prog", width: 210, render: (_, a) => {
        const p = a.progress || {};
        if (["rejected", "cancelled"].includes(a.status)) return <span className="muted">—</span>;
        return <div style={{ display: "grid", gap: 4 }}>
          {p.mine?.length > 0 && <span className="small">我的步骤 {a.status === "executed" && !a.step_done?.length ? p.mine.length : p.mine_done.length}/{p.mine.length}</span>}
          {(a.handoffs || []).map((h: any) => <span key={h.id} className="small">{h.kind_name} · {h.role} <HandoffTag h={h} /></span>)}
          {!p.mine?.length && !(a.handoffs || []).length && <span className="small muted">{a.exec_type}{a.owner_role ? ` · ${a.owner_role}` : ""}</span>}
        </div>;
      } },
    { title: "状态", dataIndex: "status_name", width: 120, filters: uniq(data.map(a => a.status_name)), onFilter: (v, a) => a.status_name === v,
      render: (s, a) => <><Tag bordered={false} color={statusColor(a.status)}>{s}</Tag>
        {a.reject_reason && <div className="muted small">{a.reject_reason}</div>}</> },
    { title: "截止", dataIndex: "due_date", width: 110, sorter: (a, b) => (a.due_date || "9").localeCompare(b.due_date || "9"),
      render: (d, a) => d ? <span className="num small">{d}{isOverdue(a, asOf) && <div><Tag color="error" bordered={false} style={{ marginTop: 2 }}>已逾期</Tag></div>}</span> : <span className="muted">—</span> },
    { title: "采纳 / 执行", dataIndex: "adopted_date", width: 130, sorter: (a, b) => (a.adopted_date || "").localeCompare(b.adopted_date || ""),
      render: (_, a) => <span className="num small" style={{ whiteSpace: "nowrap" }}>采纳 {a.adopted_date || "—"}<br />执行 {a.exec_date || "—"}</span> },
    { title: "效果跟踪（执行前 → 后）", key: "effect", width: 200,
      render: (_, a) => {
        const e = a.effect;
        if (!e?.metric_name) return <span className="muted">—</span>;
        return <><div className="small sec">{e.metric_name}</div>
          <span className="num">{f(a, e.before)} → {f(a, e.after)}</span>{" "}
          {e.status === "已完成" ? <Delta v={e.change_pct} /> : <span className="muted small">{e.status}</span>}</>;
      } },
    { title: "", key: "op", width: 110,
      render: (_, a) => (a.status === "adopted" || (a.status === "transferred" && !a.exec_date))
        ? <Button size="small" onClick={e => exec(e, a.id)} style={{ borderColor: "#0ca30c", color: "#0ca30c" }}>标记已执行</Button> : null },
  ];

  const live = (a: any) => ["adopted", "transferred"].includes(a.status);
  const hs = flatHandoffs(data);
  const nMine = data.filter(live).reduce((n, a) => n + (a.progress?.mine?.length || 0) - (a.progress?.mine_done?.length || 0), 0)
    + hs.filter(h => ["question", "draft"].includes(h.status) && live(h.action)).length;
  const nWait = hs.filter(h => ["sent", "received", "question"].includes(h.status) && live(h.action)).length;
  const reloadAll = () => { reload(); refreshMeta(); };

  return (
    <>
      <div className="page-head">
        <div><h1>待办中心</h1>
          <div className="sub">AI 诊断方案、追问中产生的事项和手动新建的待办都在这里；分给同事的步骤通过飞书协同</div></div>
        <div className="right"><Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>新建待办</Button></div>
      </div>
      <Segmented className="view-tabs" value={view} onChange={v => setView(String(v))} options={[
        { value: "mine", label: <span>我要做的 <Badge count={nMine} size="small" color="#2a78d6" /></span> },
        { value: "waiting", label: <span>等别人的 <Badge count={nWait} size="small" color="#fab219" /></span> },
        { value: "all", label: <span>全部待办 <span className="muted small">{data.length}</span></span> },
      ]} />
      {view === "mine" && <MineView actions={data} onOpen={setOpenId} reload={reloadAll} />}
      {view === "waiting" && <WaitingView actions={data} onOpen={setOpenId} reload={reloadAll} />}
      {view === "all" && (
        <Card styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" dataSource={data} columns={columns} pagination={false} scroll={{ x: 1290 }} locale={{ emptyText: "暂无待办" }}
            rowClassName={() => "clickable"} onRow={a => ({ onClick: () => setOpenId(a.id) })} />
        </Card>
      )}
      <ActionDrawer action={open} onClose={() => setOpenId(null)} onChanged={a => { if (a) upsert(a); else reload(); }} />
      <TodoModal open={creating} source="manual" onClose={() => setCreating(false)}
        onCreated={a => { setCreating(false); upsert(a); setOpenId(a.id); }} />
    </>
  );
}
