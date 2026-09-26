#!/usr/bin/env bash
# 一键部署（Debian / Ubuntu 系 Linux，root 用户执行；系统 Python 低于 3.10 时自动安装 3.11）
# 用法：cd /opt/ecom-ops && bash deploy/deploy.sh
# 服务器只需要 Python：前端（React）已预先构建在 web/dist，不需要安装 Node.js
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
echo "==> 部署目录：$APP_DIR"

echo "==> 1/5 安装 Python 运行环境"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null || echo "    （apt 源更新有报错，继续）"
apt-get install -y python3 python3-venv python3-pip curl ca-certificates >/dev/null || true

# 平台需要 Python 3.10+；系统自带版本过低时（如 Debian 10 / CentOS 7 / Ubuntu 18.04），用 uv 自动安装独立的 Python 3.11，不影响系统 Python
py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && py_ok "$(command -v "$c")"; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ]; then
  echo "    系统 Python 版本过低（$(python3 --version 2>&1 || echo 未安装)），自动安装 Python 3.11（约 1–3 分钟）"
  export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
  fi
  uv python install 3.11
  PY="$(uv python find 3.11)"
fi
py_ok "$PY" || { echo "!! 没能准备好 Python 3.10+，请把以上输出发给开发者"; exit 1; }
echo "    使用 $("$PY" --version)（$PY）"

echo "==> 2/5 安装依赖"
if [ -x .venv/bin/python ] && ! py_ok .venv/bin/python; then
  echo "    删除旧的低版本虚拟环境"
  rm -rf .venv
fi
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
if [ -n "${PIP_INDEX:-}" ]; then
  .venv/bin/python -m pip install -q -r requirements.txt -i "$PIP_INDEX"
else
  .venv/bin/python -m pip install -q -r requirements.txt
fi

if [ ! -f web/dist/index.html ]; then
  echo "!! 缺少前端构建产物 web/dist（交付包内已包含；若从源码部署，请先在开发机执行 cd web && npm ci && npm run build）"
  exit 1
fi

echo "==> 3/5 配置模型"
if [ ! -f .env ]; then
  cp .env.example .env
  read -rp "模型接口地址 [https://it-ai.fineres.com/v1]：" URL
  read -rp "模型名 [claude-opus-4-8]：" MODEL
  read -rsp "API 密钥（输入时不显示）：" KEY; echo
  URL=${URL:-https://it-ai.fineres.com/v1}
  MODEL=${MODEL:-claude-opus-4-8}
  TOKEN=$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')
  sed -i "s#^LLM_BASE_URL=.*#LLM_BASE_URL=${URL}#; s#^LLM_MODEL=.*#LLM_MODEL=${MODEL}#; s#^LLM_API_KEY=.*#LLM_API_KEY=${KEY}#; s#^ADMIN_TOKEN=.*#ADMIN_TOKEN=${TOKEN}#" .env
fi
chmod 600 .env
mkdir -p state server/cache

echo "==> 4/5 注册为系统服务（开机自启、异常自动重启）"
cat > /etc/systemd/system/ecom-ops.service <<EOF
[Unit]
Description=重点商品经营作战台
After=network-online.target

[Service]
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
Environment=QUIET=1
ExecStart=${APP_DIR}/.venv/bin/python -m server.app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ecom-ops >/dev/null 2>&1
systemctl restart ecom-ops
sleep 8

echo "==> 5/5 检查"
PORT=$(grep '^PORT=' .env | cut -d= -f2)
curl -s "http://127.0.0.1:${PORT:-80}/api/health" && echo
IP=$(curl -s --max-time 5 ifconfig.me || hostname -I | awk '{print $1}')
echo
echo "部署完成。访问：http://${IP}$( [ "${PORT:-80}" = "80" ] || echo ":${PORT}" )"
echo "下一步：运行 .venv/bin/python scripts/warm_cache.py --check 预热 AI 缓存（约 30–40 分钟，见 DEPLOY.md 第 6 步）"
