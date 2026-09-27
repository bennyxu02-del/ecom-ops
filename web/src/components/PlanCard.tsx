import { Button, Tag, Tooltip } from "antd";
import { Link } from "react-router-dom";
import { money } from "../format";

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

/** 单个动作方案卡：对象、参数、测算、约束检查、步骤分工；只有「采纳 / 驳回」两个动作 */
export default function PlanCard({ p, i, act, rej, onAdopt, onReject, onUndoReject, onMaterial }: {
  p: any; i: number; act?: any; rej?: any;
  onAdopt: (plan: any) => void;
  onReject: (plan: any) => void;
  onUndoReject: (rej: any) => void;
  onMaterial: (preset: string, plan: any, label: string) => void;
}) {
  const mats = [...new Set<string>((p.materials || []).filter((m: string) => MATERIAL[m]))];
  const owners: string[] = p.step_owners || [];
  const others = [...new Set(owners.filter(o => o !== "我"))];
  const risk: string[] = p.risk_notes || p.approval_reasons || [];

  return (
    <div className={"plan" + (rej ? " rejected" : "")}>
      <div className="ph">
        <span className="n">方案 {i + 1} · {p.name}</span>
        <Tag bordered={false}>{p.cause_name}</Tag>
        <Tag bordered={false} color="processing">{p.exec_type}</Tag>
        <Tag bordered={false}>{p.owner_role}</Tag>
      </div>
      <div className="pb">
        <div className="sec">对象：<b style={{ color: "#16181d" }}>{p.target}</b></div>
        <div className="params">{(p.params_text || []).map((t: string) => <span key={t}>{t}</span>)}</div>
        <Estimate e={p.estimate} />
        {risk.length > 0 && <div className="verify warn">风险提示：{risk.join("；")}</div>}
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
            {(p.steps || []).map((s: string, k: number) => {
              const who = owners[k] || "我";
              return (
                <div key={k} className="so">
                  <span className="idx">{k + 1}</span>
                  <Tag bordered={false} color={who === "我" ? "blue" : "orange"} className="who">{who}</Tag>
                  <span className="txt">{s}</span>
                </div>
              );
            })}
          </div>
        </div>
        <div className="small sec">跟踪：完成后 {p.track?.days} 天看{p.track?.metric_name}{p.risks?.length ? `　·　风险：${p.risks.join("；")}` : ""}</div>
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
            <Tag bordered={false} color="success">已加入待办</Tag>
            <span className="small sec">{act.stage_name}{act.outcome_name ? ` · ${act.outcome_name}` : ""}</span>
            <Link to={`/actions?open=${act.id}`} className="small">查看</Link>
          </>
        ) : rej ? (
          <>
            <Tag bordered={false}>已驳回</Tag>
            <span className="small muted">{rej.reason}</span>
            <Button size="small" type="link" onClick={() => onUndoReject(rej)}>撤销</Button>
          </>
        ) : (
          <>
            <Button size="small" type="primary" onClick={() => onAdopt(p)}>采纳</Button>
            <Button size="small" onClick={() => onReject(p)}>驳回</Button>
            {others.length > 0 && <span className="small muted">采纳后，{others.join("、")}的步骤会推送到对方飞书</span>}
          </>
        )}
      </div>
    </div>
  );
}
