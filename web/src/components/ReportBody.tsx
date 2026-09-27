import Markdown from "./Markdown";
import ReportChart from "./ReportChart";

const PH = /\[图表[:：]\s*(c\d+)\s*\]/g;

/** 报告正文：Markdown 里的 [图表:c3] 占位符换成对应的图 */
export default function ReportBody({ text, charts = {}, marks = [], compact = false }: { text: string; charts?: Record<string, any>; marks?: string[]; compact?: boolean }) {
  const parts: { md?: string; chart?: string }[] = [];
  let last = 0;
  for (const m of (text || "").matchAll(PH)) {
    parts.push({ md: text.slice(last, m.index) });
    parts.push({ chart: m[1] });
    last = (m.index || 0) + m[0].length;
  }
  parts.push({ md: (text || "").slice(last) });
  return (
    <div className="report-body">
      {parts.map((p, i) => p.chart
        ? (charts[p.chart] ? <ReportChart key={i} spec={charts[p.chart]} compact={compact} /> : null)
        : (p.md && p.md.trim() ? <Markdown key={i} text={p.md} marks={marks} /> : null))}
    </div>
  );
}
