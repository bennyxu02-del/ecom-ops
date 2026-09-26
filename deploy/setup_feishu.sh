#!/usr/bin/env bash
# 一次性设置：配置飞书应用凭证，并启动卡片按钮回调服务（长连接，不需要公网 HTTPS）
# 用法：cd /opt/ecom-ops && bash deploy/setup_feishu.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

echo "==> 1/3 配置飞书应用凭证"
read -rp "飞书 App ID（cli_ 开头）：" FID
read -rsp "飞书 App Secret（输入时不显示）：" FSEC; echo
touch .env && chmod 600 .env
sed -i '/^FEISHU_APP_ID=/d; /^FEISHU_APP_SECRET=/d' .env
printf 'FEISHU_APP_ID=%s\nFEISHU_APP_SECRET=%s\n' "$FID" "$FSEC" >> .env

echo "==> 2/3 安装依赖"
.venv/bin/python -m pip install -q -r requirements.txt

echo "==> 3/3 注册回调服务并重启平台"
cat > /etc/systemd/system/ecom-ops-feishu.service <<UNIT
[Unit]
Description=经营作战台 · 飞书卡片回调
After=network-online.target ecom-ops.service

[Service]
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python -m server.feishu_ws
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable ecom-ops-feishu >/dev/null 2>&1
systemctl restart ecom-ops ecom-ops-feishu
sleep 8
PORT=$(grep '^PORT=' .env | cut -d= -f2)
curl -s "http://127.0.0.1:${PORT:-80}/api/integrations/feishu" | .venv/bin/python -c "
import json,sys; d=json.load(sys.stdin)
print('飞书连接：', '已连接' if d['ready'] else ('失败：' + str(d.get('error'))))
print('回调服务：', '在线' if d['callback_online'] else '启动中（稍等半分钟后在平台「集成」页查看）')"
echo
echo "完成。下一步：打开平台左侧「集成」页，给各角色设置对应的飞书成员。"
