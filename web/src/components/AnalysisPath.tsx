import { Delta, money } from "../format";

type Row = { name: string; value: string; delta?: number | null; amount?: number | null; tag?: string | null; tone?: string | null };
type Layer = { key: string; label: string; title: string; note?: string; verdict?: string; rows?: Row[] };

/** 分析路径：AI 按指标拆解思路一层层往下找原因的过程 */
export default function AnalysisPath({ layers }: { layers: Layer[] }) {
  if (!layers?.length) return null;
  return (
    <ol className="apath">
      {layers.map((L, i) => (
        <li key={L.key + i} className={"ap-" + L.key}>
          <span className="n">{i + 1}</span>
          <div className="lb">{L.label}</div>
          <div className="tt">{L.title}</div>
          {L.note && <div className="nt">{L.note}</div>}
          {!!L.rows?.length && (
            <div className="rows">
              {L.rows.map((r, j) => {
                const nums = r.delta != null || r.amount != null;
                return (
                  <div key={j} className={"r" + (nums ? " nums" : "") + (r.tone ? " t-" + r.tone : "")}>
                    <span className="nm">{r.name}</span>
                    <span className="v">{r.value}{r.tag && <em className={"tg " + (r.tone || "")}>{r.tag}</em>}</span>
                    {nums && <span className="d">{r.delta != null ? <Delta v={r.delta} /> : null}</span>}
                    {nums && <span className="a num">{r.amount != null ? (r.amount > 0 ? "+" : r.amount < 0 ? "−" : "") + money(Math.abs(r.amount)) : ""}</span>}
                  </div>
                );
              })}
            </div>
          )}
          {L.verdict && <div className="vd">→ {L.verdict}</div>}
        </li>
      ))}
    </ol>
  );
}
