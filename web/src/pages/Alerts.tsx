import { useMemo, useState } from "react";
import { App, Button, Card, Empty, Segmented, Tag } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../App";
import ReasonModal from "../components/ReasonModal";
import { Sev, money } from "../format";
import { Loading, useLoad } from "../hooks";
import { AlertRow } from "./Overview";

export default function Alerts() {
  const [sp] = useSearchParams();
  const [scope, setScope] = useState(sp.get("scope") || "today");
  const [sev, setSev] = useState("all");
  const [st, setSt] = useState("all");
  const [ignoring, setIgnoring] = useState<string | null>(null);
  const { data, error, reload } = useLoad<any[]>("/api/alerts");
  const nav = useNavigate();
  const { message } = App.useApp();
  const { refreshMeta } = useApp();

  const xs = useMemo(() => (data || []).filter(c =>
    (scope === "today" ? c.is_today : true) && (sev === "all" || c.severity === sev) && (st === "all" || c.status === st)), [data, scope, sev, st]);

  const ignore = async (option: string, text: string) => {
    const id = ignoring; setIgnoring(null);
    await api(`/api/alerts/${id}`, { method: "PATCH", body: { status: "ignored", reason: option + (text ? "：" + text : "") } });
    message.success("已忽略，原因将用于优化预警阈值");
    reload(); refreshMeta();
  };

  return (
    <>
      <div className="page-head"><div><h1>预警中心</h1>
        <div className="sub">每天自动扫描全部商品；同一商品同一天的多条规则合并为一张问题卡；阈值随商品生命周期调整</div></div></div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <Segmented value={scope} onChange={v => setScope(String(v))} options={[{ label: "今日", value: "today" }, { label: "近 14 天", value: "all" }]} />
        <Segmented value={sev} onChange={v => setSev(String(v))} options={[{ label: "全部严重度", value: "all" }, { label: "红", value: "red" }, { label: "黄", value: "yellow" }, { label: "蓝", value: "blue" }]} />
        <Segmented value={st} onChange={v => setSt(String(v))} options={[
          { label: "全部状态", value: "all" }, { label: "待处理", value: "pending" }, { label: "处理中", value: "processing" },
          { label: "已处理", value: "done" }, { label: "已忽略", value: "ignored" }, { label: "已恢复", value: "recovered" }]} />
      </div>
      {!data ? <Loading error={error} /> : (
        <Card styles={{ body: { padding: 0 } }}>
          {xs.length ? xs.map(c => (
            <AlertRow key={c.id} c={c} onClick={() => nav(`/product/${c.product_id}?diag=1`)}
              extra={<>
                <Tag bordered={false}>{c.status_name}</Tag>
                <div style={{ display: "flex", gap: 6 }}>
                  {c.is_today && c.status !== "ignored" && <Button size="small" onClick={e => { e.stopPropagation(); setIgnoring(c.id); }}>忽略</Button>}
                  <Button size="small" type="primary">{c.is_today ? "AI 诊断" : "查看"}</Button>
                </div>
              </>}>
              <div className="t"><Sev s={c.severity} /> {c.product_name} <Tag bordered={false}>{c.tier} · {c.lifecycle}</Tag></div>
              <div className="d">{c.latest.map((x: any) => x.text).join("；")}</div>
              {c.ai_summary && <div className="ai"><b style={{ color: "#2a78d6" }}>AI：</b>{c.ai_summary}</div>}
              <div className="d muted small">首次触发 {c.first_date} · 最近触发 {c.last_trigger} · 共 {c.trigger_days} 天
                {c.gmv_impact > 0 ? ` · 影响 GMV 约 ${money(c.gmv_impact)}（估算）` : ""}{c.ignore_reason ? " · 忽略原因：" + c.ignore_reason : ""}</div>
            </AlertRow>
          )) : <Empty style={{ padding: 32 }} description="没有符合条件的问题卡" />}
        </Card>
      )}
      <ReasonModal spec={ignoring ? { title: "忽略该预警", options: ["正常波动", "已知原因", "阈值过严", "其他"], input: true, placeholder: "补充说明（选「其他」时必填）", okText: "确认忽略", requireTextFor: "其他" } : null}
        onOk={ignore} onCancel={() => setIgnoring(null)} />
    </>
  );
}
