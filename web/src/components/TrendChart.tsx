import { useEffect, useMemo, useRef, useState } from "react";
import { Segmented } from "antd";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { COLORS, wan } from "../format";

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent, MarkAreaComponent, CanvasRenderer]);

export type Series = { name: string; values: (number | null)[]; color?: string; dash?: boolean };
export type ChartEvent = { date: string; label: string; short?: string };
type Kind = "money" | "num" | "pct" | "price" | "dec";

const fmt = (v: number, kind: Kind) => {
  if (v == null || isNaN(v)) return "—";
  if (kind === "pct") return (v * 100).toFixed(2) + "%";
  if (kind === "money") return "¥" + wan(v);
  if (kind === "price") return "¥" + v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  if (kind === "dec") return v.toFixed(2);
  return wan(v);
};

/** 单一坐标轴的时间序列折线图：时间范围切换 + 缩放条 + 近 7 日底纹 + 事件标记 + 悬停提示 */
export default function TrendChart({ dates, series, kind = "num", events = [], height = 280, defaultRange = "42", zeroBase = true }: {
  dates: string[]; series: Series[]; kind?: Kind; events?: ChartEvent[]; height?: number; defaultRange?: string; zeroBase?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const [range, setRange] = useState(defaultRange);

  const option = useMemo(() => {
    const n = dates.length;
    const startIdx = range === "all" ? 0 : Math.max(0, n - Number(range));
    const colors = [COLORS.s1, COLORS.s2, COLORS.s3];
    const evMap = new Map(events.filter(e => dates.includes(e.date)).map(e => [e.date, e]));
    return {
      animationDuration: 300,
      grid: { left: 8, right: 16, top: series.length > 1 ? 36 : 24, bottom: 56, containLabel: true },
      legend: series.length > 1 ? { top: 0, left: 0, icon: "roundRect", itemWidth: 14, itemHeight: 3, textStyle: { color: "#50545c" } } : undefined,
      tooltip: {
        trigger: "axis", axisPointer: { type: "line", lineStyle: { color: "#aab0b8" } },
        backgroundColor: "#fff", borderColor: "#d4d7dd", textStyle: { color: "#16181d", fontSize: 12 },
        formatter: (ps: any[]) => {
          const d = ps[0]?.axisValue;
          let h = `<div style="font-weight:600;margin-bottom:4px">${d}</div>`;
          ps.forEach(p => { h += `<div>${p.marker}${p.seriesName}：<b>${fmt(p.value, kind)}</b></div>`; });
          const ev = evMap.get(d);
          if (ev) h += `<div style="margin-top:4px;color:#50545c">事件：${ev.label}</div>`;
          return h;
        },
      },
      xAxis: { type: "category", data: dates, boundaryGap: false, axisLine: { lineStyle: { color: "#d4d7dd" } },
        axisTick: { show: false }, axisLabel: { color: COLORS.text3, formatter: (v: string) => v.slice(5) } },
      yAxis: { type: "value", scale: !zeroBase, splitLine: { lineStyle: { color: COLORS.grid } },
        axisLabel: { color: COLORS.text3, formatter: (v: number) => fmt(v, kind) } },
      dataZoom: [
        { type: "inside", startValue: startIdx, endValue: n - 1 },
        { type: "slider", startValue: startIdx, endValue: n - 1, height: 18, bottom: 8, borderColor: "transparent",
          backgroundColor: "#f5f6f8", fillerColor: "rgba(42,120,214,.12)", dataBackground: { lineStyle: { color: "#c3c7ce" }, areaStyle: { color: "#eef0f3" } },
          labelFormatter: (_: any, v: string) => (v || "").slice(5) },
      ],
      series: series.map((s, i) => ({
        name: s.name, type: "line", data: s.values, showSymbol: false, symbolSize: 7, connectNulls: false,
        lineStyle: { width: 2, type: s.dash ? "dashed" : "solid", color: s.color || colors[i] },
        itemStyle: { color: s.color || colors[i] },
        emphasis: { focus: "none" },
        ...(i === 0 ? {
          markArea: n >= 7 ? { silent: true, itemStyle: { color: "rgba(42,120,214,.06)" },
            data: [[{ xAxis: dates[n - 7], name: "近 7 日", label: { color: COLORS.text3, fontSize: 11, position: "insideTop" } }, { xAxis: dates[n - 1] }]] } : undefined,
          markLine: { silent: false, symbol: "none",
            lineStyle: { color: "#8a8f98", type: "dashed", width: 1 },
            label: { formatter: (p: any) => p.data.short || "事", position: "end", color: "#50545c", fontSize: 10, fontWeight: 600,
              backgroundColor: "#fff", borderColor: "#8a8f98", borderWidth: 1, borderRadius: 8, padding: [2, 4] },
            tooltip: { show: true, formatter: (p: any) => `${p.data.xAxis}　${p.data.label}` },
            data: [...evMap.values()].map(e => ({ xAxis: e.date, short: e.short, label: e.label })) },
        } : {}),
      })),
    };
  }, [dates, series, kind, events, range, zeroBase]);

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current);
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.current?.dispose(); };
  }, []);
  useEffect(() => { chart.current?.setOption(option as any, true); }, [option]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 4 }}>
        <Segmented size="small" value={range} onChange={v => setRange(String(v))}
          options={[{ label: "近 6 周", value: "42" }, { label: "近 90 天", value: "all" }]} />
      </div>
      <div ref={ref} style={{ height }} role="img" aria-label="趋势图" />
    </div>
  );
}

export function Sparkline({ values, width = 96, height = 26 }: { values: number[]; width?: number; height?: number }) {
  if (!values || values.length < 2) return null;
  const lo = Math.min(...values), hi = Math.max(...values);
  const x = (i: number) => (i / (values.length - 1)) * (width - 4) + 2;
  const y = (v: number) => height - 3 - ((v - lo) / (hi - lo || 1)) * (height - 6);
  const d = values.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join("");
  const last = values.length - 1;
  return (
    <svg width={width} height={height} aria-hidden="true">
      <path d={d} fill="none" stroke={COLORS.s1} strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={x(last)} cy={y(values[last])} r={2.5} fill={COLORS.s1} />
    </svg>
  );
}
