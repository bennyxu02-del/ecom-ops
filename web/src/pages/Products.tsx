import { useMemo, useState } from "react";
import { App, Button, Card, Input, Segmented, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Sparkline } from "../components/TrendChart";
import { Delta, Health, Sev, TierTag, money } from "../format";
import { Loading, useLoad } from "../hooks";

const uniq = (xs: any[]) => [...new Set(xs)].filter(Boolean).map(v => ({ text: v, value: v }));
const SEV_ORDER: Record<string, number> = { red: 3, yellow: 2, blue: 1 };

export default function Products() {
  const [focusOnly, setFocusOnly] = useState(true);
  const [q, setQ] = useState("");
  const { data, error, reload } = useLoad<any[]>("/api/products" + (focusOnly ? "?focus=true" : ""));
  const nav = useNavigate();
  const { message } = App.useApp();

  const rows = useMemo(() => (data || []).filter(p => !q || p.product_name.includes(q) || p.product_id.toLowerCase().includes(q.toLowerCase())), [data, q]);

  const toggleFocus = async (p: any) => {
    await api(`/api/products/${p.product_id}/focus`, { method: "POST", body: { focus: !p.focus } });
    message.success(p.focus ? "已移出重点商品池" : "已加入重点商品池");
    reload();
  };

  const all = data || [];
  const columns: ColumnsType<any> = [
    { title: "商品", dataIndex: "product_name", fixed: "left", width: 190,
      render: (_, p) => <><b>{p.product_name}</b><div className="muted small">{p.product_id} · {p.sub_category || ""}</div></> },
    { title: "分层", dataIndex: "tier", width: 80, filters: uniq(all.map(p => p.tier)), onFilter: (v, p) => p.tier === v,
      render: (_, p) => <TierTag tier={p.tier} id={p.tier_id} /> },
    { title: "生命周期", dataIndex: "lifecycle", width: 90, filters: uniq(all.map(p => p.lifecycle)), onFilter: (v, p) => p.lifecycle === v },
    { title: "近 7 日 GMV", dataIndex: "gmv", align: "right", width: 110, sorter: (a, b) => a.gmv - b.gmv, defaultSortOrder: "descend",
      render: v => <span className="num">{money(v)}</span> },
    { title: "GMV 变化", dataIndex: "gmv_change", align: "right", width: 95, sorter: (a, b) => (a.gmv_change ?? 0) - (b.gmv_change ?? 0),
      render: v => <Delta v={v} /> },
    { title: "近 28 日趋势", dataIndex: "spark", width: 110, render: v => <Sparkline values={v} /> },
    { title: "访客", dataIndex: "uv", align: "right", width: 95, sorter: (a, b) => a.uv - b.uv,
      render: (_, p) => <span className="num">{p.uv.toLocaleString("zh-CN")}<div><Delta v={p.uv_change} /></div></span> },
    { title: "转化率", dataIndex: "cvr", align: "right", width: 90, sorter: (a, b) => a.cvr - b.cvr,
      render: (_, p) => <span className="num">{(p.cvr * 100).toFixed(2)}%<div><Delta v={p.cvr_change} /></div></span> },
    { title: "客单价", dataIndex: "aov", align: "right", width: 80, sorter: (a, b) => a.aov - b.aov, render: v => <span className="num">¥{v.toFixed(1)}</span> },
    { title: "库存可售", dataIndex: "days_of_supply", align: "right", width: 95,
      sorter: (a, b) => (a.days_of_supply ?? 9999) - (b.days_of_supply ?? 9999),
      render: v => v == null ? "—" : v === 0 ? <Tag color="error" bordered={false}>有断货</Tag> : <span className="num">{v} 天</span> },
    { title: "健康度", dataIndex: "health", width: 90, sorter: (a, b) => a.health - b.health,
      filters: uniq(["健康", "关注", "风险"]), onFilter: (v, p) => p.health_level === v,
      render: (_, p) => <Health score={p.health} level={p.health_level} /> },
    { title: "预警", dataIndex: "alert", width: 85,
      sorter: (a, b) => (SEV_ORDER[a.alert?.severity] || 0) - (SEV_ORDER[b.alert?.severity] || 0),
      filters: [{ text: "红色", value: "red" }, { text: "黄色", value: "yellow" }, { text: "蓝色", value: "blue" }, { text: "无预警", value: "none" }],
      onFilter: (v, p) => (p.alert?.severity || "none") === v,
      render: a => a ? <Sev s={a.severity} /> : <span className="muted">—</span> },
    { title: "", key: "op", width: 100, fixed: "right",
      render: (_, p) => <Button size="small" type="text" onClick={e => { e.stopPropagation(); toggleFocus(p); }}>{p.focus ? "移出重点池" : "加入重点池"}</Button> },
  ];

  return (
    <>
      <div className="page-head">
        <div><h1>商品</h1><div className="sub">重点商品池 = 爆品 + 潜力品 + 利润品 + 人工加入；长尾品默认不在池中，但 AI 同样会扫描</div></div>
        <div className="right">
          <Input.Search placeholder="搜索商品名称或编号" allowClear style={{ width: 220 }} onChange={e => setQ(e.target.value)} />
          <Segmented value={focusOnly ? "focus" : "all"} onChange={v => setFocusOnly(v === "focus")}
            options={[{ label: "只看重点商品", value: "focus" }, { label: "全部商品", value: "all" }]} />
        </div>
      </div>
      {!data ? <Loading error={error} /> : (
        <Card styles={{ body: { padding: 0 } }}>
          <Table rowKey="product_id" dataSource={rows} columns={columns} size="middle" pagination={false} scroll={{ x: 1200 }}
            rowClassName={() => "clickable-row"} onRow={p => ({ onClick: () => nav("/product/" + p.product_id) })}
            showSorterTooltip={{ title: "点击排序" }} />
        </Card>
      )}
    </>
  );
}
