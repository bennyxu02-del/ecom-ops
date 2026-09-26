"""恢复演示起点：清空问题卡状态、动作、报告、重点池调整，保留 AI 缓存，重新预置案例 E 的历史动作。

用法：python scripts/reset_demo.py
（服务运行中也可以调用 POST /api/admin/reset，需带 ADMIN_TOKEN）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import data, state  # noqa: E402

if __name__ == "__main__":
    state.reset(data.seed)
    print("已恢复演示起点")
