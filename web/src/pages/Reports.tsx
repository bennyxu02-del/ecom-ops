import { useState } from "react";
import { Button, Card, Spin, Table, Tag } from "antd";
import { useNavigate } from "react-router-dom";
import { sse } from "../api";
import Markdown from "../components/Markdown";
import { Loading, useLoad } from "../hooks";

export default function Reports() {
  const { data, error } = useLoad<any[]>("/api/reports");
  const [text, setText] = useState<string | null>(null);
  const [step, setStep] = useState("");
  const [err, setErr] = useState("");
  const nav = useNavigate();

  const gen = async () => {
    setText(""); setErr(""); setStep("正在汇总本周数据…");
    let t = "";
    try {
      await sse("/api/reports/weekly", {}, ev => {
        if (ev.type === "step") setStep(ev.summary);
        if (ev.type === "delta") { t += ev.text; setText(t); }
        if (ev.type === "result") nav("/report/" + ev.id);
        if (ev.type === "error") setErr(ev.message);
      });
    } catch (e: any) { setErr(e.message); }
  };

  return (
    <>
      <div className="page-head">
        <div><h1>报告中心</h1><div className="sub">AI 生成初稿，运营编辑确认后发布；报告中的数字只能来自平台计算的数据包</div></div>
        <div className="right"><Button type="primary" onClick={gen} loading={text !== null && !err}>生成本周周报</Button></div>
      </div>
      {text !== null && (
        <Card style={{ marginBottom: 16 }} title={<div className="ai-title"><span className="spark">AI</span>正在撰写周报</div>}
          extra={err ? <Tag color="error">{err}</Tag> : <span className="muted small"><Spin size="small" /> {text ? "撰写中" : step}</span>}>
          <Markdown text={text} />
        </Card>
      )}
      {!data ? <Loading error={error} /> : (
        <Card styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" dataSource={data} pagination={false} rowClassName={() => "clickable-row"}
            onRow={r => ({ onClick: () => nav("/report/" + r.id) })}
            locale={{ emptyText: "还没有报告，点击右上角生成本周周报" }}
            columns={[
              { title: "标题", dataIndex: "title", render: t => <b>{t}</b> },
              { title: "类型", dataIndex: "type", render: t => t === "weekly" ? "周报" : "单品诊断",
                filters: [{ text: "周报", value: "weekly" }, { text: "单品诊断", value: "product" }], onFilter: (v, r) => r.type === v },
              { title: "周期", dataIndex: "period", render: p => <span className="num">{p}</span> },
              { title: "来源", dataIndex: "source", render: s => s === "llm" ? "AI 生成" : s === "rules" ? "规则模板" : s },
              { title: "状态", dataIndex: "status", render: s => <Tag bordered={false} color={s === "published" ? "success" : "default"}>{s === "published" ? "已发布" : "草稿"}</Tag> },
              { title: "生成时间", dataIndex: "created_at", render: t => <span className="num muted">{new Date(t * 1000).toLocaleString("zh-CN")}</span>,
                sorter: (a, b) => a.created_at - b.created_at, defaultSortOrder: "descend" },
            ]} />
        </Card>
      )}
    </>
  );
}
