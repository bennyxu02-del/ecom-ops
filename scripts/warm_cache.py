"""预热 AI 缓存：对所有演示案例实际调用一次大模型，结果写入 server/cache/，演示时模型不可用也能回放。

用法（在服务器上、配置好模型环境变量后）：
    python scripts/warm_cache.py            诊断全部今日问题卡商品 + 新品案例 + 两份周报
    python scripts/warm_cache.py --check    同一案例连续跑 3 次，检查首要根因是否一致
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import data, llm_client, state  # noqa: E402
from server.agent import diagnose, report  # noqa: E402

EXTRA = {"3c": ["P04"], "snacks": []}


def run_diag(name, pid):
    steps, result, meta = [], None, {}
    for ev in diagnose.run(name, pid, refresh=True):
        if ev["type"] == "step":
            steps.append(ev)
        elif ev["type"] == "result":
            result = ev["result"]
        elif ev["type"] == "meta":
            meta.update(ev)
    return steps, result, meta


def main():
    check = "--check" in sys.argv
    print("模型状态：", json.dumps(llm_client.health(force=True), ensure_ascii=False))
    if llm_client.mode() not in ("live", "mock"):
        print("未连接模型，无法预热。请先配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL。")
        return 1
    bad = 0
    for name in data.DATASETS:
        pids = [c["product_id"] for c in data.product_cards(name) if c["is_today"]] + EXTRA.get(name, [])
        for pid in pids:
            runs = 3 if check else 1
            causes = []
            for _ in range(runs):
                steps, res, meta = run_diag(name, pid)
                causes.append(res["root_causes"][0]["cause"] if res and res["root_causes"] else None)
            ok = res and res.get("source", "").startswith("llm")
            v = (res or {}).get("verify", {})
            print(f"[{name}] {pid} 来源={res.get('source') if res else None} 根因={causes} "
                  f"未核对数字={v.get('unmatched_numbers')} 拦截方案={len(v.get('dropped_plans') or [])}"
                  + (f" 降级原因={meta.get('fallback_reason')}" if meta.get("fallback_reason") else ""))
            if not ok or len(set(causes)) > 1 or v.get("unmatched_numbers"):
                bad += 1
            if ok:
                state.cache_put(diagnose.cache_key(name, pid), dict(steps=steps, result=res), to_file=True)
        text = None
        for ev in report.run(name):
            if ev["type"] == "result":
                rid = ev["id"]
                text = state.get_report(rid)["content"]
                print(f"[{name}] 周报 来源={ev['source']} 未核对数字={ev['unmatched_numbers']}")
                if ev["source"] == "llm":
                    ds = data.ds_of(name)
                    state.cache_put(f"weekly__{name}__{ds.as_of:%Y%m%d}", dict(content=text, source="llm"), to_file=True)
    state.reset(data.seed)
    print("完成。需要人工复核的条目数：", bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
