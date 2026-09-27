import { useMemo, useState } from "react";
import { Button, Card, Empty, Segmented, Tag } from "antd";
import { SettingOutlined } from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import AlertPanel, { STATUS_COLOR } from "../components/AlertPanel";
import { Sev, money } from "../format";
import { Loading, useLoad } from "../hooks";
import { AlertRow } from "./Overview";

const GROUPS: [string, string][] = [["pending", "待决定"], ["doing", "处理中"], ["watch", "观察中"], ["opportunity", "机会"], ["finished", "已完结"]];
const EMPTY: Record<string, string> = {
  pending: "没有待决定的预警", doing: "没有处理中的预警", watch: "没有观察中的预警", opportunity: "没有增长机会", finished: "近 14 天没有已完结的预警",
};

export function pushText(p: any) {
  if (!p) return "还没有推送过";
  const t = new Date(p.t * 1000);
  const when = `${t.getMonth() + 1}/${t.getDate()} ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
  return p.sent ? `${when} 已推送到飞书${p.to ? `（${p.to}）` : ""}：新增 ${p.new} 条${p.expired ? `、观察到期 ${p.expired} 条` : ""}${p.opportunities ? `、机会 ${p.opportunities} 条` : ""}`
    : `${when} 没有推送：${p.reason}`;
}

export default function Alerts() {
  const [sp, setSp] = useSearchParams();
  const [group, setGroup] = useState(sp.get("group") || "pending");
  const { data, error, reload } = useLoad<any[]>("/api/alerts");
  const { data: meta, reload: reloadMeta } = useLoad<any>("/api/alerts/meta");
  const nav = useNavigate();
  const open = sp.get("open");
  const setOpen = (id: string | null) => {
    const n = new URLSearchParams(sp);
    if (id) n.set("open", id); else n.delete("open");
    n.delete("ds");
    setSp(n, { replace: true });
  };

  const xs = useMemo(() => (data || []).filter(c => c.group === group), [data, group]);
  const counts = meta?.counts || {};
  const refresh = () => { reload(); reloadMeta(); };

  return (
    <>
      <div className="page-head">
        <div><h1>预警中心</h1>
          <div className="sub">每天自动扫描全部商品，按设定时间把「今日预警」推送到飞书；点一条预警打开处理面板，做了决定才算处理完</div></div>
        <Button icon={<SettingOutlined />} onClick={() => nav("/alerts/settings")}>预警设置</Button>
      </div>
      {meta?.missing?.length > 0 && (
        <div className="verify warn" style={{ marginBottom: 10 }}>
          {meta.missing.map((m: any) => `「${m.name}」${m.missing}，这条规则没有生效`).join("；")}。没有预警不代表没有问题。</div>)}
      <div className="alert-toolbar">
        <Segmented value={group} onChange={v => setGroup(String(v))}
          options={GROUPS.map(([k, l]) => ({ value: k, label: <span>{l}{counts[k] ? <b className={"cnt" + (k === "pending" ? " hot" : "")}>{counts[k]}</b> : null}</span> }))} />
        <span className="small muted push-line">最近推送：{pushText(meta?.last_push)}</span>
      </div>
      {!data ? <Loading error={error} /> : (
        <Card styles={{ body: { padding: 0 } }}>
          {xs.length ? xs.map(c => (
            <AlertRow key={c.id} c={c} onClick={() => setOpen(c.id)}
              extra={<>
                <Tag bordered={false} color={STATUS_COLOR[c.status]}>{c.status_name}</Tag>
                {c.todo && <span className="small muted">待办 · {c.todo.stage_name}</span>}
                <Button size="small" type={c.group === "pending" || c.group === "opportunity" ? "primary" : "default"}>
                  {c.group === "pending" || c.group === "opportunity" ? "去处理" : "查看"}</Button>
              </>}>
              <div className="t"><Sev s={c.severity} /> {c.product_name}
                {!c.store && <Tag bordered={false}>{c.tier} · {c.lifecycle}</Tag>}
                <Tag bordered={false} color={c.custom ? "purple" : undefined}>{c.rule_names.join(" · ")}</Tag>
                {c.expected && <Tag bordered={false}>预期内</Tag>}
                {c.reopen_name && <Tag bordered={false} color="warning">{c.reopen_name}</Tag>}
              </div>
              <div className="d">{c.latest.map((x: any) => x.text).join("；")}</div>
              {c.plateau && <div className="d" style={{ color: "#8a5a00" }}>{c.plateau}</div>}
              {c.ai_summary && c.group !== "finished" && <div className="ai"><b style={{ color: "#2a78d6" }}>AI：</b>{c.ai_summary}</div>}
              <div className="d muted small">首次触发 {c.first_date} · 已持续 {c.days_open} 天
                {c.gmv_impact > 0 && c.kind !== "opportunity" ? ` · 近 7 天影响 GMV 约 ${money(c.gmv_impact)}` : ""}
                {c.status === "watch" ? ` · 观察到 ${c.watch_until}` : ""}
                {c.status === "closed" && c.reason ? ` · ${c.auto_decided ? "自动关闭" : c.decision_name}：${c.reason}` : ""}</div>
            </AlertRow>
          )) : <Empty style={{ padding: 32 }} description={EMPTY[group]} />}
        </Card>
      )}
      <AlertPanel cid={open} onClose={() => setOpen(null)} onChanged={refresh} onOpen={id => setOpen(id)} />
    </>
  );
}
