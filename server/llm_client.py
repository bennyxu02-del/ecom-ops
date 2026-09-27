"""OpenAI 兼容接口客户端（仅用标准库）。

环境变量：
  LLM_BASE_URL  接口地址，如 https://xxx/v1
  LLM_API_KEY   密钥（只存在于服务端）
  LLM_MODEL     模型名
  LLM_TIMEOUT   单次调用超时秒数，默认 60
  LLM_MODE      live（默认）/ mock（开发用模拟模型）/ off（只用缓存与规则）
  AGENT_MODE    tools（默认，工具调用）/ evidence_pack（证据包）
  DEMO_MODE     true 时强制使用缓存
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


def _load_dotenv():
    """项目根目录的 .env 自动加载（已存在的环境变量优先）。"""
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def env(k, d=None):
    v = os.environ.get(k)
    return v if v not in (None, "") else d


def mode() -> str:
    if env("DEMO_MODE", "false").lower() in ("1", "true", "yes"):
        return "demo"
    m = env("LLM_MODE", "live")
    if m == "live" and not (env("LLM_BASE_URL") and env("LLM_API_KEY") and env("LLM_MODEL")):
        return "unconfigured"
    return m


def agent_mode() -> str:
    return env("AGENT_MODE", "tools")


def timeout() -> float:
    return float(env("LLM_TIMEOUT", "60"))


def _req(path: str, payload: dict | None = None, method: str = "POST", stream: bool = False, t: float | None = None):
    url = env("LLM_BASE_URL").rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {env('LLM_API_KEY')}", "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=t or timeout())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        raise LLMError(f"模型接口返回 {e.code}：{body}") from e
    except Exception as e:  # noqa: BLE001
        raise LLMError(f"模型接口连接失败：{e}") from e


def chat(messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2, max_tokens: int = 3000,
         timeout_s: float | None = None) -> dict:
    payload = dict(model=env("LLM_MODEL"), messages=messages, temperature=temperature, max_tokens=max_tokens)
    if tools:
        payload["tools"] = [{"type": "function", "function": t} for t in tools]
        payload["tool_choice"] = "auto"
    with _req("/chat/completions", payload, t=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"模型返回格式异常：{str(data)[:200]}") from e
    return msg


def stream(messages: list[dict], temperature: float = 0.3, max_tokens: int = 3000):
    payload = dict(model=env("LLM_MODEL"), messages=messages, temperature=temperature, max_tokens=max_tokens, stream=True)
    with _req("/chat/completions", payload, stream=True) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            try:
                delta = json.loads(body)["choices"][0].get("delta", {}).get("content")
            except Exception:  # noqa: BLE001
                continue
            if delta:
                yield delta


_health = {"t": 0, "v": None}


def health(force=False) -> dict:
    m = mode()
    if m != "live":
        return dict(mode=m, online=m == "mock", model=env("LLM_MODEL") if m == "mock" else None)
    if not force and time.time() - _health["t"] < 60 and _health["v"]:
        return _health["v"]
    try:
        with _req("/models", method="GET", t=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ids = [m["id"] for m in data.get("data", [])]
        v = dict(mode="live", online=True, model=env("LLM_MODEL"), model_listed=env("LLM_MODEL") in ids if ids else None)
    except LLMError as e:
        v = dict(mode="live", online=False, model=env("LLM_MODEL"), error=str(e)[:200])
    _health.update(t=time.time(), v=v)
    return v
