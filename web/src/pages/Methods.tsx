import { Button, Card, Descriptions, Table, Tabs, Tag } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { getDs } from "../api";
import Markdown from "../components/Markdown";
import { Loading, useLoad } from "../hooks";

export default function Methods() {
  const { data: m, error } = useLoad<any>("/api/methods");
  if (!m) return <Loading error={error} />;
  const p = m.profile, c = p.constraints;
  const cn = (x: string) => m.action_library.cause_names[x] || x;

  const items = [
    { key: "metrics", label: "指标字典", children: (
      <Table size="small" rowKey="id" pagination={false} dataSource={m.metrics.metrics} columns={[
        { title: "指标", dataIndex: "name", render: t => <b>{t}</b>, width: 130 },
        { title: "字段", dataIndex: "id", render: t => <code>{t}</code>, width: 140 },
        { title: "公式", dataIndex: "formula" },
        { title: "口径说明", dataIndex: "note", render: t => <span className="sec">{t}</span> },
      ]} />) },
    { key: "tree", label: "拆解树", children: (
      <div className="md">
        <p><b>GMV = 访客数 × 支付转化率 × 客单价</b>（数学拆解，可精确计算每个因子的贡献额）</p>
        <ul>
          <li>访客数 → 按渠道相加：{Object.values(m.metrics.channels).join("、")}</li>
          <li>支付转化率 → 按规格看销量结构；影响因素（需找证据判断）：{Object.values(m.tree.factors).join("；")}</li>
          <li>客单价 → 件单价 × 人均件数；影响因素：促销、优惠券、规格结构</li>
        </ul>
        <p className="sec">影响因素的检查顺序由品类配置决定：当前为 {p.factor_priority.join(" → ")}</p>
        <p className="sec">AI 诊断按这棵树逐层下钻：先算出三个因子各自的贡献，找到主因，再对主因往下拆，直到定位原因。商品诊断里的「分析路径」就是沿这棵树走出来的。</p>
      </div>) },
    { key: "tiering", label: "商品分层", children: (
      <>
        <Table size="small" rowKey="name" pagination={false} dataSource={m.tiering.tiers} columns={[
          { title: "分层", dataIndex: "name", render: t => <b>{t}</b>, width: 110 },
          { title: "规则", dataIndex: "rule" },
          { title: "进入重点池", dataIndex: "focus", render: f => f ? "是" : "否（可人工加入）", width: 150 },
        ]} />
        <Table className="mt" size="small" rowKey="name" pagination={false} dataSource={m.tiering.lifecycles} columns={[
          { title: "生命周期", dataIndex: "name", render: t => <b>{t}</b>, width: 110 },
          { title: "规则", dataIndex: "rule" },
          { title: "预警阈值系数", dataIndex: "coef", render: v => "×" + v, width: 150 },
        ]} />
      </>) },
    { key: "alerts", label: "预警规则", children: (
      <>
        <Table size="small" rowKey="id" pagination={false} dataSource={m.alert_rules.rules} columns={[
          { title: "规则", dataIndex: "id", width: 60 },
          { title: "名称", dataIndex: "name", render: t => <b>{t}</b>, width: 120 },
          { title: "类型", dataIndex: "type", width: 90 },
          { title: "触发条件", dataIndex: "condition" },
          { title: "严重度", dataIndex: "severity", render: t => <span className="sec">{t}</span> },
        ]} />
        <div className="md mt"><h3>附加与合并规则</h3><ul>{[...m.alert_rules.extra, ...m.alert_rules.merge].map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
      </>) },
    { key: "sop", label: "归因 SOP", children: <Markdown text={m.sop} /> },
    { key: "actions", label: "动作库", children: (
      <Table size="small" rowKey="id" pagination={false} dataSource={m.action_library.actions} scroll={{ x: 900 }} columns={[
        { title: "动作", dataIndex: "name", width: 150, render: (t, a: any) => <><b>{t}</b>{a.optional && <div className="muted small">品类启用</div>}</> },
        { title: "适用原因", dataIndex: "causes", width: 160, render: (xs: string[]) => xs.map(cn).join("、") },
        { title: "参数计算", dataIndex: "params", render: t => <span className="sec">{t}</span> },
        { title: "约束检查", dataIndex: "constraints", render: t => <span className="sec">{t}</span> },
        { title: "执行类型", dataIndex: "exec_type", width: 100, render: t => <Tag bordered={false}>{t}</Tag> },
      ]} />) },
    { key: "profile", label: "当前品类配置", children: (
      <>
        <Descriptions size="small" column={1} bordered labelStyle={{ width: 140 }} items={[
          ["品类", p.name], ["新品期", p.new_product_days + " 天"], ["成长期上限", p.growth_days + " 天"],
          ["GMV 下滑阈值", `黄 ${p.gmv_drop_yellow * 100}% / 红 ${p.gmv_drop_red * 100}%`], ["异常 z 值", p.z_threshold],
          ["补货周期", p.replenish_lead_days + " 天"], ["价差阈值", p.price_gap * 100 + "%"], ["影响因素顺序", p.factor_priority.join(" → ")],
          ["价格类动作偏好", p.action_prefs.price_actions.join(" → ")], ["惯用赠品", p.action_prefs.gifts.map((g: any) => g.name).join("、")], ["季节性", p.seasonality_note],
        ].map(([k, v]) => ({ key: String(k), label: k, children: String(v) }))} />
        <h3 style={{ fontSize: 14, margin: "18px 0 8px" }}>经营约束（示例值，落地时按公司规则设置）</h3>
        <Descriptions size="small" column={1} bordered labelStyle={{ width: 140 }} items={[
          ["毛利底线", c.margin_floor * 100 + "%"], ["自主调价权限", `单次降幅 ≤ ${c.price_authority * 100}%`],
          ["最低价保护", `不低于近 ${c.min_price_window} 天最低成交价`], ["安全库存", c.safety_days + " 天"], ["活动报名提前", c.campaign_signup_lead + " 天"],
        ].map(([k, v]) => ({ key: String(k), label: k, children: String(v) }))} />
      </>) },
  ];

  return (
    <>
      <div className="page-head">
        <div><h1>方法库</h1><div className="sub">平台使用的指标口径、预警规则、分析步骤和动作方案</div></div>
        <div className="right"><Button type="primary" icon={<DownloadOutlined />} href={"/api/skill/download?ds=" + getDs()}>下载 Skill 包</Button></div>
      </div>
      <Card><Tabs items={items} /></Card>
    </>
  );
}
