import { useState } from "react";
import { App, Button, Checkbox, Tag, Tooltip } from "antd";
import { SendOutlined } from "@ant-design/icons";
import { api } from "../api";
import HandoffSendModal, { HandoffTag } from "./HandoffSendModal";
import { money, signed } from "../format";

export const MATERIAL: Record<string, string> = {
  "卖点文案": "selling_points", "主图角标文案": "selling_points", "断货提示文案": "stockout_notice", "差评回复模板": "review_reply",
  "客服话术": "review_reply", "标题关键词建议": "title_keywords", "活动报名理由": "campaign_pitch",
};

function Estimate({ e }: { e: any }) {
  if (!e) return null;
  if (e.type === "price") return (
    <>
      <div className="est">
        <div><small>单件毛利</small><b>¥{e.unit_margin_before} → ¥{e.unit_margin_after}</b></div>
        <div><small>执行后毛利率</small><b>{(e.margin_rate_after * 100).toFixed(0)}%</b></div>
        <div><small>到手价降幅</small><b>{(e.price_drop * 100).toFixed(1)}%</b></div>
        <div><small>保本需销量提升</small><b>{e.breakeven_lift != null ? (e.breakeven_lift * 100).toFixed(1) + "%" : "—"}</b></div>
      </div>
      {e.note && <div className="muted small">{e.note}</div>}
    </>
  );
  const val = e.daily_gmv_recoverable ? ["恢复后每天可挽回 GMV", e.daily_gmv_recoverable] : e.daily_gmv_at_risk ? ["断货规格日均损失", e.daily_gmv_at_risk] : null;
  if (!val) return null;
  return <><div className="est"><div><small>{val[0]}</small><b>{money(val[1])}</b></div></div>{e.note && <div className="muted small">{e.note}</div>}</>;
}

/** 单个动作方案卡：对象、参数、测算、约束检查、分工到人的步骤、协同进度、效果跟踪 */
export default function PlanCard({ p, i, act, onDecide, onChanged, onMaterial }: {
  p: any; i: number; act?: any;
  onDecide: (plan: any, decision: "adopt" | "reject") => void;
  onChanged: () => void;
  onMaterial: (preset: string, plan: any, label: string) => void;
}) {
  const [sending, setSending] = useState<any>(null);
  const { message } = App.useApp();
  const mats = [...new Set<string>((p.materials || []).filter((m: string) => MATERIAL[m]))];
  const plan = act?.plan?.steps ? act.plan : p;
  const owners: string[] = plan.step_owners || p.step_owners || [];
  const live = act && act.status !== "rejected";
  const done = new Set<number>(act?.step_done || []);
  const hs: any[] = act?.handoffs || [];
  const waitApproval = act?.progress?.approval_pending;
  const stepHandoff = (k: number) => hs.find(h => h.kind === "transfer" && (h.steps || []).includes(k));
  const approvalNeeded = (p.handoffs || []).some((h: any) => h.kind === "approval");

  const toggle = async (k: number, v: boolean) => {
    await api(`/api/actions/${act.id}/steps`, { method: "PATCH", body: { index: k, done: v } });
    onChanged();
  };
  const manualDone = async () => {
    await api(`/api/actions/${act.id}`, { method: "PATCH", body: { status: "executed" } });
    message.success("已标记执行，平台将从今天起跟踪效果");
    onChanged();
  };

  return (
    <div className="plan">
      <div className="ph">
        <span className="n">方案 {i + 1} · {p.name}</span>
        <Tag bordered={false}>{p.cause_name}</Tag>
        <Tag bordered={false} color={p.exec_type === "需审批" ? "warning" : "processing"}>{p.exec_type}</Tag>
        <Tag bordered={false}>{p.owner_role}</Tag>
      </div>
      <div className="pb">
        <div className="sec">对象：<b style={{ color: "#16181d" }}>{p.target}</b></div>
        <div className="params">{p.params_text.map((t: string) => <span key={t}>{t}</span>)}</div>
        <Estimate e={p.estimate} />
        {p.approval_reasons?.length > 0 && <div className="verify bad">需审批：{p.approval_reasons.join("；")}</div>}
        {p.checks?.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {p.checks.map((c: any) => (
              <Tooltip key={c.name} title={c.detail}><Tag bordered={false} color={c.passed ? "success" : "warning"}>{c.passed ? "✓" : "!"} {c.name}</Tag></Tooltip>
            ))}
          </div>
        )}
        <div>
          <div className="small muted" style={{ marginBottom: 4 }}>执行步骤</div>
          <div className="steps-owned">
            {plan.steps.map((s: string, k: number) => {
              const who = owners[k] || "我";
              const mine = who === "我";
              const h = !mine ? stepHandoff(k) : null;
              return (
                <div key={k} className={"so" + (live && mine && done.has(k) ? " done" : "")}>
                  <span className="idx">{k + 1}</span>
                  <Tag bordered={false} color={mine ? "blue" : "orange"} className="who">{who}</Tag>
                  <span className="txt">{s}</span>
                  {live && mine && act.status !== "executed" && (
                    <Tooltip title={waitApproval ? "等待审批通过后再执行" : ""}>
                      <Checkbox checked={done.has(k)} disabled={waitApproval} onChange={e => toggle(k, e.target.checked)}>完成</Checkbox>
                    </Tooltip>
                  )}
                  {live && h && <HandoffTag h={h} />}
                </div>
              );
            })}
          </div>
        </div>
        <div className="small sec">跟踪：执行后 {p.track.days} 天看{p.track.metric_name}　·　风险：{p.risks.join("；")}</div>
        {p.rationale && <div className="why">{p.rationale}</div>}
        {mats.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <span className="small muted">AI 起草物料：</span>
            {mats.map(m => <Button key={m} size="small" onClick={() => onMaterial(MATERIAL[m], p, m)}>{m}</Button>)}
          </div>
        )}
        {live && hs.length > 0 && (
          <div className="collab">
            <div className="small muted" style={{ marginBottom: 6 }}>协同</div>
            {hs.map(h => (
              <div key={h.id} className="crow">
                <Tag bordered={false}>{h.kind_name}</Tag>
                <span className="role">{h.role}</span>
                <span className="small muted">{h.kind === "approval" ? "批准后才能执行" : `负责第 ${h.steps.map((x: number) => x + 1).join("、")} 步`}</span>
                <HandoffTag h={h} />
                {h.note && <span className="small sec">「{h.note}」</span>}
                <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                  {h.status === "draft"
                    ? <Button size="small" type="primary" icon={<SendOutlined />} onClick={() => setSending(h)}>{h.kind === "approval" ? "提交审批" : "发送转交单"}</Button>
                    : <Button size="small" type="link" href={`#/h/${h.id}`} target="_blank">查看</Button>}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="pf">
        {act ? (
          <>
            <Tag bordered={false} color={act.status === "executed" ? "success" : act.status === "rejected" || act.status === "declined" ? "default" : "processing"}>{act.status_name}</Tag>
            {act.reject_reason && <span className="small muted">{act.reject_reason}</span>}
            {act.status === "adopted" && act.progress && (
              <span className="small muted">我的步骤 {act.progress.mine_done.length}/{act.progress.mine.length}{hs.length ? ` · 协同 ${hs.filter(h => ["done", "approved"].includes(h.status)).length}/${hs.length}` : ""}，全部完成后自动开始跟踪效果</span>
            )}
            {act.status === "adopted" && <Button size="small" type="text" onClick={manualDone}>直接标记已执行</Button>}
            {act.effect?.status && act.status === "executed" && (
              <span className="st muted">效果跟踪：{act.effect.status === "已完成" ? `${act.effect.metric_name} ${signed(act.effect.change_pct)}` : act.effect.status}</span>
            )}
          </>
        ) : (
          <>
            <Button size="small" type="primary" onClick={() => onDecide(p, "adopt")}>{approvalNeeded ? "采纳并提交审批" : "采纳"}</Button>
            <Button size="small" onClick={() => onDecide(p, "reject")}>驳回</Button>
            {(p.handoffs || []).filter((h: any) => h.kind === "transfer").length > 0 && (
              <span className="small muted">采纳后，{(p.handoffs || []).filter((h: any) => h.kind === "transfer").map((h: any) => h.role).join("、")}的部分会生成转交单</span>
            )}
          </>
        )}
      </div>
      <HandoffSendModal handoff={sending} onClose={() => setSending(null)} onSent={() => { setSending(null); onChanged(); }} />
    </div>
  );
}
