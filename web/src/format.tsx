import { Tag } from "antd";

export const COLORS = {
  s1: "#2a78d6", s2: "#eb6834", s3: "#1baf7a",
  good: "#0ca30c", warn: "#fab219", crit: "#d03b3b", info: "#2a78d6",
  pos: "#0b8a0b", neg: "#d03b3b", text3: "#8a8f98", grid: "#eef0f3",
};

export const money = (v?: number | null) => v == null ? "—" : "¥" + Math.round(v).toLocaleString("zh-CN");
export const num = (v?: number | null) => v == null ? "—" : v.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
export const pctv = (v?: number | null, d = 2) => v == null ? "—" : (v * 100).toFixed(d) + "%";
export const signed = (v?: number | null, d = 1) => v == null ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(d) + "%";
export const wan = (v: number) => Math.abs(v) >= 10000 ? (v / 10000).toFixed(1) + " 万" : Math.round(v).toLocaleString("zh-CN");

export function Delta({ v, invert = false }: { v?: number | null; invert?: boolean }) {
  if (v == null) return <span className="delta flat">—</span>;
  const flat = Math.abs(v) < 0.005;
  const good = (v > 0) !== invert;
  return <span className={"delta " + (flat ? "flat" : good ? "up" : "down")}>{flat ? "" : v > 0 ? "↑" : "↓"}{Math.abs(v * 100).toFixed(1)}%</span>;
}

const SEV: Record<string, [string, string, string]> = {
  red: ["红色", "#fcebeb", COLORS.crit], yellow: ["黄色", "#fff6e0", "#8a5a00"], blue: ["蓝色", "#eaf2fc", COLORS.info],
};
export function Sev({ s }: { s: string }) {
  const [t, bg, fg] = SEV[s] || [s, "#f5f5f5", "#555"];
  const dot = s === "red" ? COLORS.crit : s === "yellow" ? COLORS.warn : COLORS.info;
  return <span className="sev" style={{ background: bg, color: fg }}><i style={{ background: dot }} />{t}</span>;
}

export const healthColor = (lv?: string | null) => lv === "健康" ? COLORS.good : lv === "关注" ? COLORS.warn : COLORS.crit;
export function Health({ score, level }: { score?: number | null; level?: string | null }) {
  return <span className="hscore"><i style={{ background: healthColor(level) }} />{score ?? "—"}</span>;
}

export function TierTag({ tier, id }: { tier: string; id?: string }) {
  return <Tag color={id === "tail" ? undefined : "blue"} bordered={false}>{tier}</Tag>;
}

export const EV_SHORT: Record<string, string> = {
  competitor_price_change: "竞", price_change: "价", campaign_start: "活", campaign_end: "活", ad_budget_change: "投",
  restock: "补", review_issue: "评", promo_day: "促", external_content: "外",
};
