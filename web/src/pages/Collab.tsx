import { useState } from "react";
import { Button, Card, Segmented, Table, Tag } from "antd";
import { Link } from "react-router-dom";
import HandoffSendModal, { HandoffTag } from "../components/HandoffSendModal";
import { Loading, useLoad } from "../hooks";
import { useApp } from "../App";

const OPEN = ["draft", "sent", "received", "question"];

/** 协同中心：所有转交与审批事项的进度 */
export default function Collab() {
  const { data, error, reload } = useLoad<any[]>("/api/handoffs");
  const [scope, setScope] = useState("open");
  const [sending, setSending] = useState<any>(null);
  const { refreshMeta } = useApp();
  if (!data) return <Loading error={error} />;
  const rows = data.filter(h => scope === "all" || (scope === "open" ? OPEN.includes(h.status) : !OPEN.includes(h.status)));
  const roles = [...new Set(data.map(h => h.role))];

  return (
    <>
      <div className="page-head">
        <div><h1>协同中心</h1><div className="sub">方案里需要其他团队配合或主管审批的事项，发出后在这里跟进</div></div>
        <div className="right">
          <Segmented value={scope} onChange={v => setScope(String(v))}
            options={[{ label: "进行中", value: "open" }, { label: "已结束", value: "closed" }, { label: "全部", value: "all" }]} />
        </div>
      </div>
      <Card styles={{ body: { padding: 0 } }}>
        <Table rowKey="id" dataSource={rows} pagination={false} scroll={{ x: 1000 }} locale={{ emptyText: "暂无协同事项。采纳带有转交或审批步骤的方案后，会出现在这里" }}
          columns={[
            { title: "商品 / 方案", key: "p", width: 240, render: (_, h) => <><Link to={"/product/" + h.action?.product_id}>{h.action?.product_name}</Link><div className="muted small">{h.action?.name}</div></> },
            { title: "类型", dataIndex: "kind_name", width: 80, render: t => <Tag bordered={false}>{t}</Tag>,
              filters: [{ text: "转交", value: "transfer" }, { text: "审批", value: "approval" }], onFilter: (v, h) => h.kind === v },
            { title: "发给", dataIndex: "role", width: 110, filters: roles.map(r => ({ text: r, value: r })), onFilter: (v, h) => h.role === v,
              render: (r, h) => <>{r}{h.assignee && <div className="muted small">{h.assignee}</div>}</> },
            { title: "请对方做什么", key: "s", render: (_, h) => h.kind === "approval" ? <span className="small">{(h.plan?.approval_reasons || []).join("；")}</span>
                : <span className="small">{(h.step_texts || []).join("；")}</span> },
            { title: "状态", dataIndex: "status", width: 100, render: (_, h) => <HandoffTag h={h} /> },
            { title: "最新反馈", key: "n", width: 180, render: (_, h) => <span className="small sec">{h.note || "—"}</span> },
            { title: "期望反馈", dataIndex: "due", width: 110, render: d => <span className="num small">{d}</span>, sorter: (a, b) => (a.due || "").localeCompare(b.due || "") },
            { title: "", key: "op", width: 120, fixed: "right", render: (_, h) => h.status === "draft"
                ? <Button size="small" type="primary" onClick={() => setSending(h)}>{h.kind === "approval" ? "提交审批" : "发送"}</Button>
                : <Button size="small" type="link" href={`#/h/${h.id}`} target="_blank">查看</Button> },
          ]} />
      </Card>
      <HandoffSendModal handoff={sending} onClose={() => setSending(null)} onSent={() => { setSending(null); reload(); refreshMeta(); }} />
    </>
  );
}
