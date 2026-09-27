"""把报告（Markdown 正文 + 图表配置）导出成一个 HTML 文件，给 Skill / 外部 Agent 用。

图表用平台前端同一段渲染代码（report_charts.js，打包 Skill 时从 web/src/lib/reportCharts.js 复制过来），
所以 Skill 生成的报告和平台里看到的一模一样。ECharts 与 Markdown 解析从 jsDelivr 加载。
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _chart_js() -> str:
    for p in (HERE / "assets" / "report_charts.js", HERE.parent / "web" / "src" / "lib" / "reportCharts.js"):
        if p.exists():
            return re.sub(r"^export\s+", "", p.read_text(encoding="utf-8"), flags=re.M)
    raise FileNotFoundError("找不到图表渲染代码 report_charts.js")


def _safe(s: str) -> str:
    return s.replace("</", "<\\/")


def build(title: str, markdown: str, charts: dict, unmatched: list | None = None, numbers: int | None = None) -> str:
    unmatched = unmatched or []
    banner = ""
    if numbers:
        banner = (f'<div class="vb warn">正文 {numbers} 个数字中，{len(unmatched)} 个未能核对到：{html.escape("、".join(unmatched[:8]))}</div>'
                  if unmatched else f'<div class="vb ok">正文 {numbers} 个数字已全部与数据核对一致；图表数据由计算脚本直接提供</div>')
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<style>
body{{margin:0;background:#f5f6f8;color:#16181d;font:14px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
main{{max-width:960px;margin:24px auto;background:#fff;border:1px solid #e6e8ec;border-radius:12px;padding:28px 36px}}
h1{{font-size:22px;margin:0 0 14px}} h2{{font-size:17px;margin:26px 0 8px;padding-bottom:6px;border-bottom:1px solid #e6e8ec}}
table{{border-collapse:collapse;margin:8px 0;font-size:13px}} th,td{{border:1px solid #e6e8ec;padding:6px 10px;text-align:left}} th{{background:#f8f9fb}}
figure{{margin:10px 0 14px;padding:12px 14px 8px;border:1px solid #e6e8ec;border-radius:10px;break-inside:avoid}}
figcaption{{font-weight:600;font-size:13.5px;margin-bottom:6px;display:flex;justify-content:space-between}}
figcaption span{{font-weight:400;font-size:12px;color:#7c5cd6}}
mark{{background:#fff1b8}} .vb{{padding:8px 12px;border-radius:8px;font-size:13px;margin-bottom:14px}} .vb.ok{{background:#eaf7f0;color:#17613f}} .vb.warn{{background:#fff6e0;color:#7a5200}}
{{KPI}}
@media print{{body{{background:#fff}} main{{border:none;margin:0;padding:0}}}}
</style></head><body><main>{banner}<div id="report"></div></main>
<script type="text/markdown" id="md">{_safe(markdown)}</script>
<script>
{_chart_js()}
const CHARTS = {_safe(json.dumps(charts, ensure_ascii=False))};
const UNMATCHED = {_safe(json.dumps(unmatched, ensure_ascii=False))};
(function () {{
  const raw = document.getElementById("md").textContent;
  const ph = /\\[图表[:：]\\s*(c\\d+)\\s*\\]/g;
  let out = "", last = 0, m;
  const mark = (h) => UNMATCHED.reduce((s, n) => s.split(n).join("<mark>" + n + "</mark>"), h);
  while ((m = ph.exec(raw))) {{
    out += mark(marked.parse(raw.slice(last, m.index)));
    const c = CHARTS[m[1]];
    if (c) out += '<figure><figcaption>' + c.title + (c.origin === "extra" ? "<span>AI 补充图</span>" : "") + '</figcaption>' +
      (c.type === "kpi" ? kpiHtml(c) : '<div class="ch" data-id="' + c.id + '" style="height:' + chartHeight(c) + 'px"></div>') + '</figure>';
    last = m.index + m[0].length;
  }}
  out += mark(marked.parse(raw.slice(last)));
  document.getElementById("report").innerHTML = out;
  document.querySelectorAll(".ch").forEach((el) => {{
    const c = echarts.init(el);
    c.setOption(toOption(CHARTS[el.dataset.id]));
    window.addEventListener("resize", () => c.resize());
  }});
}})();
</script></body></html>""".replace("{KPI}", "")


def write(path, title, markdown, charts, unmatched=None, numbers=None) -> str:
    doc = build(title, markdown, charts, unmatched, numbers)
    doc = doc.replace("</style>", _kpi_css() + "\n</style>", 1)
    Path(path).write_text(doc, encoding="utf-8")
    return str(path)


def _kpi_css() -> str:
    js = _chart_js()
    m = re.search(r"KPI_CSS = `([^`]*)`", js)
    return m.group(1) if m else ""
