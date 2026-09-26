import { Fragment } from "react";
import { Tooltip } from "antd";
import { Delta, money } from "../format";

/** 指标拆解树：GMV = 访客数 × 支付转化率 × 客单价，展示每个因子的贡献额及下钻 */
export default function DecomposeTree({ d }: { d: any }) {
  const dec = d.decompose;
  const maxAbs = Math.max(...dec.factors.map((f: any) => Math.abs(f.amount)), Math.abs(dec.gmv_change), 1);
  const bar = (v: number) => (
    <div className="cbar"><div className="axis" /><div className={"b " + (v < 0 ? "neg" : "pos")} style={{ width: `${Math.abs(v) / maxAbs * 50}%` }} /></div>
  );
  const fmtv = (f: string, v: number) => f === "cvr" ? (v * 100).toFixed(2) + "%" : f === "aov" ? "¥" + v.toFixed(2) : Math.round(v).toLocaleString("zh-CN");
  const worst = dec.gmv_change < 0 ? dec.factors.reduce((a: any, b: any) => (a.amount < b.amount ? a : b)) : null;
  return (
    <div className="tree">
      <div className="tree-node head"><span>指标</span><span>前 7 日</span><span>近 7 日</span><span>变化</span><span>对 GMV 的贡献</span><span className="r">贡献额</span></div>
      <div className="tree-node root">
        <span className="nm">GMV</span><span className="num">{money(dec.gmv_prev)}</span><span className="num">{money(dec.gmv_cur)}</span>
        <span><Delta v={dec.gmv_change_pct} /></span>{bar(dec.gmv_change)}<span className="num r">{money(dec.gmv_change)}</span>
      </div>
      {dec.factors.map((f: any, i: number) => {
        const hi = worst && worst.factor === f.factor && (f.share || 0) >= 0.3;
        return (
          <Fragment key={f.factor}>
            <div className={"tree-node" + (hi ? " hi" : "")}>
              <span className="nm"><span className="op">{i === 0 ? "=" : "×"}</span><span>{f.name}{hi && <Tooltip title="对 GMV 下滑贡献最大的因子"><div style={{ color: "#d03b3b", fontSize: 12 }}>主因</div></Tooltip>}</span></span>
              <span className="num">{fmtv(f.factor, f.prev)}</span><span className="num">{fmtv(f.factor, f.cur)}</span>
              <span><Delta v={f.change_pct} /></span>{bar(f.amount)}
              <span className="num r">{money(f.amount)}{f.share != null && dec.gmv_change ? <div className="muted small">{(f.share * 100).toFixed(0)}%</div> : null}</span>
            </div>
            {f.factor === "uv" && d.channels.available && (
              <div className="sub-rows">
                {d.channels.channels.filter((c: any) => c.uv_prev || c.uv_cur).map((c: any) => (
                  <div className="srow" key={c.channel}>
                    <span>└ {c.name}</span>
                    <span className="muted">日均 {c.daily_prev.toLocaleString("zh-CN")} → {c.daily_cur.toLocaleString("zh-CN")}</span>
                    <span><Delta v={c.change_pct} /></span><span className="num">{money(c.gmv_contrib)}</span>
                    <span className="muted">占比 {(c.share_cur * 100).toFixed(0)}%</span>
                  </div>
                ))}
              </div>
            )}
            {f.factor === "cvr" && (
              <div className="sub-rows"><div className="srow"><span>└ 影响因素</span><span className="muted">库存 · 价格 · 口碑 · 活动（由 AI 诊断逐项检查）</span></div></div>
            )}
            {f.factor === "aov" && (
              <div className="sub-rows">
                <div className="srow"><span>└ 件单价</span><span className="muted">¥{dec.aov_detail.unit_price_prev} → ¥{dec.aov_detail.unit_price_cur}</span></div>
                <div className="srow"><span>└ 人均件数</span><span className="muted">{dec.aov_detail.units_per_buyer_prev} → {dec.aov_detail.units_per_buyer_cur}</span></div>
              </div>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}
