import { useState } from "react";
import { App, Button, Card, Input, Tag } from "antd";
import { CopyOutlined, EditOutlined, EyeOutlined, PrinterOutlined } from "@ant-design/icons";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import Markdown from "../components/Markdown";
import { Loading, useLoad } from "../hooks";

export default function ReportView() {
  const { rid } = useParams();
  const { data: r, error, setData } = useLoad<any>("/api/reports/" + rid);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const { message } = App.useApp();
  const nav = useNavigate();
  if (!r) return <Loading error={error} />;

  const published = r.status === "published";
  const save = async () => {
    await api("/api/reports/" + rid, { method: "PUT", body: { content: draft } });
    setData({ ...r, content: draft }); setEditing(false); message.success("已保存");
  };
  const publish = async () => {
    await api(`/api/reports/${rid}/publish`, { method: "POST" });
    setData({ ...r, status: "published" }); message.success("已发布");
  };
  const copy = async () => { const x = await api<any>(`/api/reports/${rid}/copy`, { method: "POST" }); nav("/report/" + x.id); };
  const copyMd = async () => {
    try { await navigator.clipboard.writeText(r.content); message.success("已复制 Markdown"); }
    catch { message.warning("浏览器不允许访问剪贴板，请在编辑模式下手动复制"); }
  };

  return (
    <>
      <div className="no-print" style={{ marginBottom: 10 }}><Link to="/reports" className="small">← 报告中心</Link></div>
      <div className="page-head no-print">
        <div><h1>{r.title}</h1>
          <div className="sub">{r.source === "llm" ? "AI 生成初稿" : "系统生成"} · <Tag bordered={false} color={published ? "success" : "default"}>{published ? "已发布" : "草稿，确认后发布"}</Tag></div></div>
        <div className="right">
          {!published ? <>
            <Button icon={editing ? <EyeOutlined /> : <EditOutlined />} onClick={() => { setDraft(r.content); setEditing(!editing); }}>{editing ? "预览" : "编辑"}</Button>
            <Button type="primary" onClick={publish} disabled={editing}>确认发布</Button>
          </> : <Button onClick={copy}>复制为新草稿</Button>}
          <Button icon={<CopyOutlined />} onClick={copyMd}>复制 Markdown</Button>
          <Button icon={<PrinterOutlined />} onClick={() => window.print()}>打印 / 导出 PDF</Button>
        </div>
      </div>
      <Card>
        {editing ? (
          <>
            <Input.TextArea className="editor" value={draft} onChange={e => setDraft(e.target.value)} autoSize={{ minRows: 24 }} />
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <Button type="primary" onClick={save}>保存</Button><Button onClick={() => setEditing(false)}>取消</Button>
            </div>
          </>
        ) : <Markdown text={r.content} />}
      </Card>
    </>
  );
}
