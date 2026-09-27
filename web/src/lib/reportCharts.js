// 报告图表渲染：把平台 / Skill 生成的「图表配置」转成 ECharts 配置。
// 平台前端与 Skill 导出的 HTML 报告共用这一个文件（Skill 打包时会复制进去），保证两边画出来的图一样。
// 纯 JavaScript，不依赖其他模块。

export const PALETTE = {
  main: "#2a78d6", muted: "#b9bdc6", good: "#1f9d6b", bad: "#d64541", warn: "#e8913a", ref: "#8a8f98",
  series: ["#2a78d6", "#e8913a", "#1f9d6b", "#8a63d2", "#d64541", "#16a3b8"],
  text: "#1f2329", text2: "#50545c", text3: "#8a8f98", grid: "#eef0f3",
};

export function fmtVal(v, unit, short) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  switch (unit) {
    case "pct": return (v * 100).toFixed(short ? 1 : 2) + "%";
    case "money":
      if (a >= 10000) return (v / 10000).toFixed(a >= 1e6 ? 0 : (short ? 1 : 2)) + (short ? "万" : " 万元");
      return Math.round(v).toLocaleString("zh-CN") + (short ? "" : " 元");
    case "price": return (short ? "" : "") + (Math.abs(v - Math.round(v)) < 1e-6 ? Math.round(v) : v.toFixed(2)) + (short ? "" : " 元");
    case "dec": return v.toFixed(2);
    default:
      if (a >= 100000 && short) return (v / 10000).toFixed(1) + "万";
      return Math.round(v).toLocaleString("zh-CN");
  }
}

function signed(v, unit, short) {
  if (v === null || v === undefined) return "—";
  return (v > 0 ? "+" : v < 0 ? "-" : "") + fmtVal(Math.abs(v), unit, short);
}

const tone = (t, i) => PALETTE[t] || PALETTE.series[i % PALETTE.series.length];

function axisLabel(unit) {
  return { color: PALETTE.text3, fontSize: 11, formatter: (v) => fmtVal(v, unit, true) };
}

function base(spec) {
  return {
    animationDuration: 300,
    color: PALETTE.series,
    textStyle: { fontFamily: "inherit" },
    grid: { left: 8, right: 24, top: 36, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", confine: true },
  };
}

function markLines(spec) {
  const data = [];
  (spec.marks || []).forEach((m) => data.push({ xAxis: m.x, label: { formatter: m.text || "", position: "insideEndTop", color: PALETTE.text2, fontSize: 11 },
    lineStyle: { color: PALETTE.warn, type: "dashed", width: 1.2 } }));
  if (spec.baseline) data.push({ yAxis: spec.baseline.value, label: { formatter: (spec.baseline.text || "") + " " + fmtVal(spec.baseline.value, spec.unit, true), position: "insideEndTop", color: PALETTE.text2, fontSize: 11 },
    lineStyle: { color: PALETTE.ref, type: "dashed", width: 1.2 } });
  return data.length ? { symbol: "none", silent: true, data } : undefined;
}

function markArea(spec) {
  if (!spec.bands || !spec.bands.length) return undefined;
  return { silent: true, itemStyle: { color: "rgba(42,120,214,0.07)" },
    label: { color: PALETTE.text3, fontSize: 11, position: "insideTop" },
    data: spec.bands.map((b) => [{ xAxis: b.from, name: b.text || "" }, { xAxis: b.to }]) };
}

function legend(n) {
  return n > 1 ? { top: 0, left: 0, icon: "roundRect", itemWidth: 12, itemHeight: 4, textStyle: { color: PALETTE.text2, fontSize: 12 } } : undefined;
}

function trend(spec) {
  const o = base(spec);
  const n = spec.series.length;
  o.legend = legend(n);
  o.grid.top = n > 1 ? 40 : 28;
  o.xAxis = { type: "category", data: spec.x, boundaryGap: spec.series.some((s) => s.style === "bar"),
    axisLabel: { color: PALETTE.text3, fontSize: 11 }, axisLine: { lineStyle: { color: "#d9dce1" } }, axisTick: { show: false } };
  o.yAxis = { type: "value", scale: spec.unit !== "money" && spec.unit !== "num", axisLabel: axisLabel(spec.unit), splitLine: { lineStyle: { color: PALETTE.grid } } };
  o.tooltip.valueFormatter = (v) => fmtVal(v, spec.unit);
  o.series = spec.series.map((s, i) => {
    const isBar = s.style === "bar";
    const c = s.tone ? tone(s.tone, i) : PALETTE.series[i % PALETTE.series.length];
    const x = { name: s.name, type: isBar ? "bar" : "line", data: s.data, connectNulls: false, showSymbol: s.data.length <= 31, symbolSize: 4,
      lineStyle: { width: 2, type: s.dashed ? "dashed" : "solid" }, itemStyle: { color: c }, barMaxWidth: 36 };
    if (isBar && spec.highlight !== undefined) {
      x.data = s.data.map((v, k) => ({ value: v, itemStyle: { color: k === spec.highlight ? PALETTE.main : PALETTE.muted } }));
      x.label = { show: true, position: "top", color: PALETTE.text2, fontSize: 11, formatter: (p) => fmtVal(p.value, spec.unit, true) };
    }
    if (i === 0) { x.markLine = markLines(spec); x.markArea = markArea(spec); }
    return x;
  });
  return o;
}

function dual(spec) {
  const o = base(spec);
  const units = (spec.axes || [{ unit: "num" }]).map((a) => a.unit);
  o.legend = legend(spec.series.length);
  o.grid.top = 40;
  o.xAxis = { type: "category", data: spec.x, boundaryGap: false, axisLabel: { color: PALETTE.text3, fontSize: 11 },
    axisLine: { lineStyle: { color: "#d9dce1" } }, axisTick: { show: false } };
  o.yAxis = units.map((u, i) => ({ type: "value", scale: true, position: i ? "right" : "left", axisLabel: axisLabel(u),
    splitLine: { show: i === 0, lineStyle: { color: PALETTE.grid } } }));
  o.tooltip.formatter = (ps) => {
    const rows = ps.map((p) => `${p.marker}${p.seriesName}：${fmtVal(p.value, units[spec.series[p.seriesIndex].axis || 0])}`);
    return [ps[0] && ps[0].axisValue].concat(rows).join("<br/>");
  };
  o.series = spec.series.map((s, i) => {
    const x = { name: s.name, type: "line", data: s.data, yAxisIndex: s.axis || 0, showSymbol: false,
      lineStyle: { width: 2, type: s.dashed ? "dashed" : "solid" }, itemStyle: { color: PALETTE.series[i % PALETTE.series.length] } };
    if (i === 0) { x.markLine = markLines(spec); x.markArea = markArea(spec); }
    return x;
  });
  return o;
}

function grouped(spec, stacked) {
  const o = base(spec);
  o.legend = legend(spec.series.length);
  o.grid.top = 40;
  o.xAxis = { type: "category", data: spec.x, axisLabel: { color: PALETTE.text2, fontSize: 11, interval: 0 }, axisTick: { show: false },
    axisLine: { lineStyle: { color: "#d9dce1" } } };
  o.yAxis = { type: "value", max: stacked && spec.percent ? 1 : undefined, axisLabel: axisLabel(spec.unit), splitLine: { lineStyle: { color: PALETTE.grid } } };
  o.tooltip.axisPointer = { type: "shadow" };
  o.tooltip.valueFormatter = (v) => fmtVal(v, spec.unit);
  o.series = spec.series.map((s, i) => ({ name: s.name, type: "bar", data: s.data, stack: stacked ? "t" : undefined, barMaxWidth: stacked ? 48 : 28,
    itemStyle: { color: s.tone ? tone(s.tone, i) : PALETTE.series[i % PALETTE.series.length] },
    label: stacked ? { show: true, color: "#fff", fontSize: 10, formatter: (p) => (p.value >= 0.06 ? fmtVal(p.value, spec.unit, true) : "") } : undefined }));
  return o;
}

function hbar(spec) {
  const o = base(spec);
  const items = spec.items || [];
  o.grid = { left: 8, right: 64, top: 12, bottom: 8, containLabel: true };
  o.yAxis = { type: "category", data: items.map((x) => x.label), axisTick: { show: false }, axisLine: { show: false },
    axisLabel: { color: PALETTE.text2, fontSize: 12, width: 180, overflow: "truncate" } };
  o.xAxis = { type: "value", axisLabel: axisLabel(spec.unit), splitLine: { lineStyle: { color: PALETTE.grid } } };
  o.tooltip = { trigger: "item", confine: true, formatter: (p) => `${p.name}：${spec.unit === "pct" && spec.floor === undefined ? signed(p.value, spec.unit) : fmtVal(p.value, spec.unit)}` };
  const signedLabel = !(spec.floor !== undefined && spec.floor !== null);
  const s = { type: "bar", barMaxWidth: 16, data: items.map((x, i) => ({ value: x.value, itemStyle: { color: tone(x.tone || "main", i) } })),
    label: { show: true, position: "right", color: PALETTE.text2, fontSize: 11, formatter: (p) => (signedLabel ? signed(p.value, spec.unit, true) : fmtVal(p.value, spec.unit, true)) } };
  const lines = [];
  if (spec.band) { lines.push({ xAxis: spec.band }, { xAxis: -spec.band }); }
  if (spec.floor !== undefined && spec.floor !== null) lines.push({ xAxis: spec.floor, label: { formatter: "底线 " + fmtVal(spec.floor, spec.unit, true), color: PALETTE.bad, fontSize: 11 } });
  if (lines.length) s.markLine = { symbol: "none", silent: true, lineStyle: { color: PALETTE.ref, type: "dashed" }, label: { show: spec.floor !== undefined && spec.floor !== null, fontSize: 11 }, data: lines };
  o.series = [s];
  return o;
}

function waterfall(spec) {
  const o = base(spec);
  const cats = [spec.start.label].concat(spec.items.map((x) => x.label), [spec.end.label]);
  const assist = [], up = [], labels = [], colors = [];
  let cum = spec.start.value || 0;
  assist.push(0); up.push(cum); colors.push(PALETTE.main); labels.push(fmtVal(cum, spec.unit, true));
  spec.items.forEach((x, i) => {
    const v = x.value || 0;
    const lo = v >= 0 ? cum : cum + v;
    assist.push(lo); up.push(Math.abs(v)); colors.push(tone(x.tone || (v >= 0 ? "good" : "bad"), i));
    labels.push(signed(v, spec.unit, true));
    cum += v;
  });
  const endV = spec.end.value !== undefined ? spec.end.value : cum;
  assist.push(0); up.push(endV); colors.push(PALETTE.main); labels.push(fmtVal(endV, spec.unit, true));
  const minLo = Math.min.apply(null, assist.slice(1, -1).concat([endV, spec.start.value || 0]));
  const maxHi = Math.max.apply(null, assist.map((a, i) => a + up[i]));
  const floor = minLo > 0 ? Math.max(0, minLo - (maxHi - minLo) * 0.6) : 0;
  o.grid.top = spec.legend && spec.legend.length ? 40 : 24;
  if (spec.legend && spec.legend.length) {
    o.legend = { top: 0, left: 0, data: spec.legend.map((l) => ({ name: l.text, itemStyle: { color: tone(l.tone, 0) } })), icon: "roundRect", itemWidth: 12, itemHeight: 8, textStyle: { color: PALETTE.text2, fontSize: 12 }, selectedMode: false };
  }
  o.xAxis = { type: "category", data: cats, axisTick: { show: false }, axisLine: { lineStyle: { color: "#d9dce1" } },
    axisLabel: { color: PALETTE.text2, fontSize: 11, interval: 0, width: 84, overflow: "break" } };
  o.yAxis = { type: "value", min: floor, axisLabel: axisLabel(spec.unit), splitLine: { lineStyle: { color: PALETTE.grid } } };
  o.tooltip = { trigger: "axis", axisPointer: { type: "shadow" }, confine: true,
    formatter: (ps) => { const p = ps.find((x) => x.seriesName === "v"); if (!p) return ""; const i = p.dataIndex; const it = spec.items[i - 1];
      return `${cats[i]}：${labels[i]}${it && it.note ? "（" + it.note + "）" : ""}`; } };
  o.series = [
    { name: "a", type: "bar", stack: "w", silent: true, itemStyle: { color: "transparent" }, data: assist.map((a, i) => (i === 0 || i === assist.length - 1 ? floor : a)) },
    { name: "v", type: "bar", stack: "w", barMaxWidth: 44,
      data: up.map((v, i) => ({ value: i === 0 || i === up.length - 1 ? v - floor : v, itemStyle: { color: colors[i] } })),
      label: { show: true, position: "top", color: PALETTE.text2, fontSize: 11, formatter: (p) => labels[p.dataIndex] } },
  ];
  if (spec.legend && spec.legend.length) {
    spec.legend.forEach((l) => o.series.push({ name: l.text, type: "bar", data: [], itemStyle: { color: tone(l.tone, 0) } }));
  }
  return o;
}

export function toOption(spec) {
  switch (spec.type) {
    case "trend": return trend(spec);
    case "dual_line": return dual(spec);
    case "grouped_bar": return grouped(spec, false);
    case "stacked_bar": return grouped(spec, true);
    case "hbar": return hbar(spec);
    case "waterfall": return waterfall(spec);
    default: return null;
  }
}

export function chartHeight(spec) {
  if (spec.type === "hbar") return Math.max(140, 30 * (spec.items || []).length + 40);
  if (spec.type === "waterfall") return 300;
  return 280;
}

function esc(s) {
  return String(s === null || s === undefined ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/** 指标卡：返回 HTML 字符串（平台和 Skill 的 HTML 报告共用样式类名 rc-kpi）。 */
export function kpiHtml(spec) {
  const cards = (spec.items || []).map((x) => {
    let delta = "";
    if (x.change !== null && x.change !== undefined) {
      const flat = Math.abs(x.change) < 0.005;
      const good = (x.change > 0) === (x.better !== "down");
      const cls = flat ? "flat" : good ? "up" : "down";
      delta = `<span class="rc-d ${cls}">${flat ? "" : x.change > 0 ? "↑" : "↓"}${Math.abs(x.change * 100).toFixed(1)}%</span>`;
    }
    const prev = x.prev !== null && x.prev !== undefined ? `<div class="rc-p">对比 ${esc(fmtVal(x.prev, x.unit))}</div>` : "";
    const note = x.note ? `<div class="rc-n">${esc(x.note)}</div>` : "";
    return `<div class="rc-card"><div class="rc-l">${esc(x.label)}</div><div class="rc-v">${esc(fmtVal(x.value, x.unit))}${delta}</div>${prev}${note}</div>`;
  });
  return `<div class="rc-kpi">${cards.join("")}</div>`;
}

export const KPI_CSS = `.rc-kpi{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.rc-card{border:1px solid #e6e8eb;border-radius:8px;padding:10px 12px;background:#fff}
.rc-l{font-size:12px;color:#8a8f98}.rc-v{font-size:19px;font-weight:600;margin-top:2px;font-variant-numeric:tabular-nums}
.rc-d{font-size:12px;font-weight:500;margin-left:6px}.rc-d.up{color:#1f9d6b}.rc-d.down{color:#d64541}.rc-d.flat{color:#8a8f98}
.rc-p,.rc-n{font-size:11.5px;color:#8a8f98;margin-top:2px}`;
