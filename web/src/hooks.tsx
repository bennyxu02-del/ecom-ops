import { useCallback, useEffect, useState } from "react";
import { Result, Spin } from "antd";
import { api } from "./api";

/** 读取接口数据：返回 data / loading / error / reload */
export function useLoad<T = any>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    if (!path) return;
    try { setData(await api<T>(path)); setError(null); }
    catch (e: any) { setError(e.message || "加载失败"); }
    finally { setLoading(false); }
  }, [path]);
  useEffect(() => { setLoading(true); reload(); }, [reload]);
  return { data, error, loading, reload, setData };
}

export function Loading({ error }: { error?: string | null }) {
  if (error) return <Result status="warning" title="加载失败" subTitle={error} />;
  return <div className="empty"><Spin /> <span style={{ marginLeft: 8 }}>加载中</span></div>;
}
