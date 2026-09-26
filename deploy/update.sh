#!/usr/bin/env bash
# 更新到最新版本（或指定版本），保留 .env、运行状态和 AI 缓存
# 用法：
#   bash deploy/update.sh          更新到最新版本
#   bash deploy/update.sh list     查看所有版本
#   bash deploy/update.sh v3       切换到 v3（用于回退）
set -euo pipefail

# 当前版本号：指向当前代码的版本分支名（如 v3），没有则显示提交号
current() {
  local v
  v="$(git for-each-ref --points-at HEAD --sort=-version:refname refs/remotes/origin/versions --format='%(refname:lstrip=4)' 2>/dev/null | head -1)"
  [ -n "$v" ] && echo "$v" || git rev-parse --short HEAD 2>/dev/null || echo 无
}

main() {
  APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
  cd "$APP_DIR"
  git fetch -q --prune origin

  if [ "${1:-}" = "list" ]; then
    echo "可用版本（新 → 旧）："
    git for-each-ref --sort=-version:refname refs/remotes/origin/versions \
      --format='  %(refname:lstrip=4)  %(committerdate:format:%m-%d %H:%M)  %(contents:subject)'
    echo "当前版本：$(current)"
    return
  fi

  TARGET="origin/main"
  if [ -n "${1:-}" ]; then
    TARGET="origin/versions/$1"
    git rev-parse -q --verify "$TARGET" >/dev/null || { echo "!! 没有版本 $1，运行 bash deploy/update.sh list 查看"; exit 1; }
  fi
  BEFORE="$(current)"
  git reset -q --hard "$TARGET"
  AFTER="$(current)"
  echo "==> 代码：$BEFORE → $AFTER"

  echo "==> 检查依赖"
  .venv/bin/python -m pip install -q -r requirements.txt

  echo "==> 重启服务"
  systemctl restart ecom-ops
  if systemctl list-unit-files ecom-ops-feishu.service >/dev/null 2>&1 && [ -f /etc/systemd/system/ecom-ops-feishu.service ]; then
    systemctl restart ecom-ops-feishu
  fi
  sleep 6
  PORT=$(grep '^PORT=' .env | cut -d= -f2)
  if curl -sf "http://127.0.0.1:${PORT:-80}/api/health" >/dev/null; then
    echo "更新完成，当前版本 $AFTER。刷新浏览器即可看到。"
  else
    echo "!! 服务没有正常启动，请把下面的日志发给开发者："
    journalctl -u ecom-ops -n 40 --no-pager
    exit 1
  fi
}

# 整个脚本先读入再执行，避免更新过程中脚本自身被替换导致出错
main "$@"
exit
