import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { HashRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Badge, Menu, Select, Spin, Tooltip } from "antd";
import { AlertOutlined, AppstoreOutlined, CheckSquareOutlined, DashboardOutlined, FileTextOutlined, ReadOutlined, TeamOutlined } from "@ant-design/icons";
import { api, getDs, setDs } from "./api";
import Overview from "./pages/Overview";
import Products from "./pages/Products";
import ProductDetail from "./pages/ProductDetail";
import Alerts from "./pages/Alerts";
import Actions from "./pages/Actions";
import Reports from "./pages/Reports";
import ReportView from "./pages/ReportView";
import Methods from "./pages/Methods";
import Collab from "./pages/Collab";
import HandoffView from "./pages/HandoffView";

type Dataset = { id: string; name: string; as_of: string; products: number };
type Ctx = { ds: string; refreshMeta: () => void };
const AppCtx = createContext<Ctx>({ ds: "3c", refreshMeta: () => {} });
export const useApp = () => useContext(AppCtx);

const NAV = [
  { key: "overview", label: "经营总览", icon: <DashboardOutlined /> },
  { key: "products", label: "商品", icon: <AppstoreOutlined /> },
  { key: "alerts", label: "预警中心", icon: <AlertOutlined /> },
  { key: "actions", label: "行动跟踪", icon: <CheckSquareOutlined /> },
  { key: "collab", label: "协同中心", icon: <TeamOutlined /> },
  { key: "reports", label: "报告中心", icon: <FileTextOutlined /> },
  { key: "methods", label: "方法库", icon: <ReadOutlined /> },
];

function ModelPill() {
  const [h, setH] = useState<any>(null);
  useEffect(() => {
    const load = () => api("/api/health").then(setH).catch(() => setH({ llm: { mode: "error" } }));
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);
  if (!h) return <span className="pill"><span className="dot" />模型检测中</span>;
  const l = h.llm || {};
  const txt: Record<string, string> = {
    live: l.online ? `AI 在线 · ${l.model}` : "模型连接失败 · 使用缓存/规则",
    mock: "模拟模型（开发）", demo: "AI 已就绪", unconfigured: "未配置模型 · 使用缓存/规则", off: "模型已关闭", error: "服务未连接",
  };
  const cls = (l.mode === "live" && l.online) || l.mode === "demo" ? "ok" : l.mode === "mock" ? "warn" : "bad";
  return <Tooltip title={l.error || ""}><span className={"pill " + cls}><span className="dot" />{txt[l.mode] || l.mode}</span></Tooltip>;
}

function Shell() {
  const [ds, setDsState] = useState(getDs());
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [alertCount, setAlertCount] = useState(0);
  const [collabCount, setCollabCount] = useState(0);
  const nav = useNavigate();
  const loc = useLocation();

  const refreshMeta = useCallback(() => {
    api<any[]>("/api/alerts?today=true&status=pending,processing").then(a => setAlertCount(a.length)).catch(() => {});
    api<any[]>("/api/handoffs").then(hs => setCollabCount(hs.filter(h => ["draft", "question"].includes(h.status)).length)).catch(() => {});
  }, []);

  useEffect(() => { api<Dataset[]>("/api/datasets").then(setDatasets); }, []);
  useEffect(() => { refreshMeta(); }, [ds, refreshMeta]);
  useEffect(() => { window.scrollTo(0, 0); }, [loc.pathname]);

  const seg = loc.pathname.split("/")[1] || "overview";
  const active = seg === "product" ? "products" : seg === "report" ? "reports" : seg;
  const cur = datasets.find(d => d.id === ds);

  return (
    <AppCtx.Provider value={{ ds, refreshMeta }}>
      <header className="topbar">
        <div className="brand"><div className="logo">作</div><span className="name">重点商品经营作战台</span> <small>AI 盯盘 · 诊断 · 周报</small></div>
        <Select value={ds} style={{ width: 200 }} aria-label="切换数据集"
          options={datasets.map(d => ({ value: d.id, label: "数据集：" + d.name }))}
          onChange={v => { setDs(v); setDsState(v); }} />
        {cur && <span className="meta">数据截至 {cur.as_of}（T+1）</span>}
        <div className="spacer" />
        <ModelPill />
      </header>
      <nav className="side">
        <Menu mode="inline" selectedKeys={[active]} onClick={e => nav("/" + e.key)}
          items={NAV.map(n => ({
            key: n.key, icon: n.icon,
            label: (n.key === "alerts" && alertCount) || (n.key === "collab" && collabCount)
              ? <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>{n.label}
                  <Badge count={n.key === "alerts" ? alertCount : collabCount} size="small" color={n.key === "collab" ? "#fab219" : undefined} /></span>
              : n.label,
          }))} />
      </nav>
      <main className="main" key={ds}>
        {datasets.length === 0 ? <div className="empty"><Spin /></div> : (
          <Routes>
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/overview" element={<Overview />} />
            <Route path="/products" element={<Products />} />
            <Route path="/product/:pid" element={<ProductDetail />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/actions" element={<Actions />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/report/:rid" element={<ReportView />} />
            <Route path="/methods" element={<Methods />} />
            <Route path="/collab" element={<Collab />} />
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        )}
      </main>
    </AppCtx.Provider>
  );
}

function Root() {
  const loc = useLocation();
  // 协同方处理页（从飞书卡片或转交单链接打开）不带导航框架
  if (loc.pathname.startsWith("/h/")) return <Routes><Route path="/h/:hid" element={<HandoffView />} /></Routes>;
  return <Shell />;
}

export default function App() {
  return <HashRouter><Root /></HashRouter>;
}
