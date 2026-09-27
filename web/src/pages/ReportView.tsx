import { useState } from "react";
import { App, Button, Card, Input, Tag } from "antd";
import { CheckCircleOutlined, CopyOutlined, EditOutlined, EyeOutlined, PrinterOutlined, WarningOutlined } from "@ant-design/icons";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import ReportBody from "../components/ReportBody";
import TodoModal from "../components/TodoModal";
import { Loading, useLoad } from "../hooks";

const SCENE_COLOR: Record<string, string> = { weekly: "blue", campaign: "orange", product: "green" };

export default function ReportView() {
  const { rid } = useParams();
  const { data: r, error, setData } = useLoad<any>("/api/reports/" + rid);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [todo, setTodo] = useState<any>(null);
  const [made, setMade] = useState<Record<number, any>>({});
  const { message } = App.useApp();
  const nav = useNavigate();
  if (!r) return <Loading error={error} />;

  const published = r.status === "published";
  const ex = r.extra || {};
  const charts = r.charts || {};
  const nReq = Object.values(charts).filter((c: any) => c.origin === "required").length;
  const nExtra = Object.values(charts).filter((c: any) => c.origin === "extra").length;
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
  const goto = (heading: string) => {
    const h = [...document.querySelectorAll(".report-body h2")].find(x => (x.textContent || "").includes(heading.slice(2)));
    h?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const unmatched: string[] = ex.unmatched || [];
  const actions: any[] = ex.actions || [];

  return (
    <>
      <div className="no-print" style={{ marginBottom: 10 }}><Link to="/reports" className="small">← 报告中心</Link></div>
      <div className="page-head no-print">
        <div><h1>{r.title}</h1>
          <div className="sub">
            <Tag bordered={false} color={SCENE_COLOR[r.type]}>{r.scene_name}</Tag>
            {r.source === "llm" ? "AI 按分析剧本撰写" : r.source === "rules" ? "规则生成（未调用大模型）" : "系统生成"}
            {nReq + nExtra > 0 && ` · 必备图 ${nReq} 张${nExtra ? `、AI 补充图 ${nExtra} 张` : ""}`}
            {" · "}<Tag bordered={false} color={published ? "success" : "default"}>{published ? "已发布" : "草稿，确认后发布"}</Tag>
          </div></div>
        <div className="right">
          {!published ? <>
            <Button icon={editing ? <EyeOutlined /> : <EditOutlined />} onClick={() => { setDraft(r.content); setEditing(!editing); }}>{editing ? "预览" : "编辑"}</Button>
            <Button type="primary" onClick={publish} disabled={editing}>确认发布</Button>
          </> : <Button onClick={copy}>复制为新草稿</Button>}
          <Button icon={<CopyOutlined />} onClick={copyMd}>复制 Markdown</Button>
          <Button icon={<PrinterOutlined />} onClick={() => window.print()}>打印 / 导出 PDF</Button>
        </div>
      </div>
      {ex.numbers > 0 && !editing && (
        unmatched.length
          ? <div className="verify-bar warn no-print"><WarningOutlined />正文 {ex.numbers} 个数字中，{unmatched.length} 个未能在平台数据中核对到（已标黄）：{unmatched.slice(0, 6).join("、")}，发布前请人工确认</div>
          : <div className="verify-bar ok no-print"><CheckCircleOutlined />正文 {ex.numbers} 个数字已全部与平台数据核对一致；图表数据由平台直接提供</div>
      )}
      <div className="report-layout">
        <Card>
          {editing ? (
            <>
              <div className="muted small" style={{ marginBottom: 6 }}>正文里的 [图表:c1] 是图表位置，可以移动或删除，不要改编号。</div>
              <Input.TextArea className="editor" value={draft} onChange={e => setDraft(e.target.value)} autoSize={{ minRows: 24 }} />
              <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                <Button type="primary" onClick={save}>保存</Button><Button onClick={() => setEditing(false)}>取消</Button>
              </div>
            </>
          ) : <ReportBody text={r.content} charts={charts} marks={unmatched} />}
        </Card>
        <div className="report-side no-print">
          {ex.chapters?.length > 0 && (
            <Card size="small" title="章节" className="toc">
              {ex.chapters.map((c: any) => <a key={c.key} onClick={() => goto(c.heading)} title={c.question}>{c.heading}</a>)}
            </Card>
          )}
          {actions.length > 0 && (
            <Card size="small" title="需要跟进的事">
              <div style={{ display: "grid", gap: 8 }}>
                {actions.map((a, i) => (
                  <div className="act" key={i}>
                    <div className="t">{a.title}</div>
                    {a.why && <div className="w">{a.why}</div>}
                    <div className="w">负责：{(a.owners || []).join("、")}</div>
                    {a.existing_todo ? <Link to={`/actions?open=${a.existing_todo.id}`} className="small">已有待办「{a.existing_todo.name}」→</Link>
                      : made[i] ? <Link to={`/actions?open=${made[i].id}`} className="small">已加入待办 · 查看 →</Link>
                        : a.can_todo ? <Button size="small" onClick={() => setTodo({ ...a, idx: i })}>转为待办</Button> : null}
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      </div>
      <TodoModal open={!!todo} source="report" productId={todo?.product_id || undefined} plan={todo?.plan || null}
        cardId={todo?.card_id || null} draft={todo && !todo.plan ? todo.draft : null}
        context={todo ? { summary: `来自报告「${r.title}」：${todo.why || todo.title}` } : null}
        onClose={() => setTodo(null)} onCreated={a => { setMade(m => ({ ...m, [todo.idx]: a })); setTodo(null); }} />
    </>
  );
}
