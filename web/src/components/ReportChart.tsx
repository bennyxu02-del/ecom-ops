import { useEffect, useRef } from "react";
import { Tag, Tooltip } from "antd";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { chartHeight, kpiHtml, toOption } from "../lib/reportCharts";

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, MarkAreaComponent, CanvasRenderer]);

const TYPE_NAMES: Record<string, string> = { kpi: "指标卡", trend: "趋势折线", waterfall: "瀑布图", stacked_bar: "堆叠柱", grouped_bar: "分组柱", hbar: "横向条形", dual_line: "双折线" };

/** 报告里的一张图：图表配置由平台 / 图表工具生成，这里只负责画出来 */
export default function ReportChart({ spec, compact = false }: { spec: any; compact?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || spec.type === "kpi") return;
    const c = echarts.init(ref.current);
    const opt = toOption(spec);
    if (opt) c.setOption(opt);
    const onResize = () => c.resize();
    window.addEventListener("resize", onResize);
    const ro = new ResizeObserver(onResize);
    ro.observe(ref.current);
    return () => { window.removeEventListener("resize", onResize); ro.disconnect(); c.dispose(); };
  }, [spec]);

  const extra = spec.origin === "extra";
  const params = spec.params ? Object.entries(spec.params).filter(([k]) => k !== "title").map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`).join("  ") : "";
  return (
    <figure className={"rchart" + (compact ? " compact" : "")} data-chart={spec.id}>
      <figcaption>
        <span className="rt">{spec.title}</span>
        <span className="rmeta">
          {extra
            ? <Tooltip title={<div><div>AI 根据报告内容调用图表工具补充的图，只传了参数，数据由平台提供。</div>{params && <div style={{ marginTop: 4, opacity: .8, fontFamily: "monospace", fontSize: 11 }}>{params}</div>}</div>}>
                <Tag bordered={false} color="purple">AI 补充图</Tag></Tooltip>
            : <Tag bordered={false}>{TYPE_NAMES[spec.type] || "图表"}</Tag>}
        </span>
      </figcaption>
      {spec.type === "kpi"
        ? <div dangerouslySetInnerHTML={{ __html: kpiHtml(spec) }} />
        : <div ref={ref} style={{ width: "100%", height: compact ? Math.min(chartHeight(spec), 240) : chartHeight(spec) }} />}
    </figure>
  );
}
