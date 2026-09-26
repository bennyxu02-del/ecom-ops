// 接口访问：普通 JSON 请求与 SSE 流式请求
let currentDs = localStorage.getItem("ds") || "3c";
export const getDs = () => currentDs;
export const setDs = (v: string) => { currentDs = v; localStorage.setItem("ds", v); };

function withDs(path: string) {
  return path + (path.includes("?") ? "&" : "?") + "ds=" + currentDs;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: any } = {}): Promise<T> {
  const r = await fetch(withDs(path), {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || (typeof j.detail === "string" ? j.detail : "请求失败"));
  return j as T;
}

export async function sse(path: string, body: any, onEvent: (e: any) => void, signal?: AbortSignal) {
  const r = await fetch(withDs(path), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}), signal,
  });
  if (!r.ok || !r.body) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || j.error || "请求失败");
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = chunk.split("\n").find(l => l.startsWith("data:"));
      if (line) {
        try { onEvent(JSON.parse(line.slice(5))); } catch (e) { console.error(e); }
      }
    }
  }
}
