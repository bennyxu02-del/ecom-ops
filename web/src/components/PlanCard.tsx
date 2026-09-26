import { Button, Tag, Tooltip } from "antd";
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

/** 单个动作方案卡：对象、参数、测算、约束检查、步骤、跟踪、处理按钮 */
export default function PlanCard({ p, i, act, onDecide, onExec, onMaterial }: {
  p: any; i: number; act?: any;
  onDecide: (plan: any, decision: "adopt" | "reject" | "transfer") => void;
  onExec: (actionId: number) => void;
  onMaterial: (preset: string, plan: any, label: string) => void;
}) {
  const transfer = String(p.exec_type).includes("转交");
  const mats = [...new Set<string>((p.materials || []).filter((m: string) => MATERIAL[m]))];
  const canExec = act && (act.status === "adopted" || (act.status === "transferred" && !act.exec_date));
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
        <div><div className="small muted" style={{ marginBottom: 4 }}>执行步骤</div><ol>{p.steps.map((s: string, k: number) => <li key={k}>{s}</li>)}</ol></div>
        <div className="small sec">跟踪：执行后 {p.track.days} 天看{p.track.metric_name}　·　风险：{p.risks.join("；")}</div>
        {p.rationale && <div className="why">{p.rationale}</div>}
        {mats.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <span className="small muted">AI 起草物料：</span>
            {mats.map(m => <Button key={m} size="small" onClick={() => onMaterial(MATERIAL[m], p, m)}>{m}</Button>)}
          </div>
        )}
      </div>
      <div className="pf">
        {act ? (
          <>
            <Tag bordered={false} color={act.status === "rejected" ? "default" : "success"}>{act.status_name}</Tag>
            {act.reject_reason && <span className="small muted">{act.reject_reason}</span>}
            {canExec && <Button size="small" onClick={() => onExec(act.id)} style={{ borderColor: "#0ca30c", color: "#0ca30c" }}>标记已执行</Button>}
            {act.effect?.status && (
              <span className="st muted">效果跟踪：{act.effect.status === "已完成" ? `${act.effect.metric_name} ${signed(act.effect.change_pct)}` : act.effect.status}</span>
            )}
          </>
        ) : (
          <>
            <Button size="small" type="primary" onClick={() => onDecide(p, transfer ? "transfer" : "adopt")}>{transfer ? `采纳并转交${p.owner_role}` : "采纳"}</Button>
            <Button size="small" onClick={() => onDecide(p, "reject")}>驳回</Button>
            {!transfer && <Button size="small" type="text" onClick={() => onDecide(p, "transfer")}>转交他人</Button>}
          </>
        )}
      </div>
    </div>
  );
}
