#!/usr/bin/env bash
# 一次性设置：把服务器上的平台接到 GitHub 仓库，之后用 deploy/update.sh 一条命令更新
# 用法：cd /opt/ecom-ops && bash deploy/setup_git.sh
set -euo pipefail
REPO="bennyxu02-del/ecom-ops"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
KEY="$HOME/.ssh/ecom_ops_deploy"

echo "==> 1/4 安装 git"
command -v git >/dev/null 2>&1 || { apt-get update -y >/dev/null || true; apt-get install -y git openssh-client >/dev/null; }

echo "==> 2/4 生成服务器的只读钥匙"
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "ecom-ops-server" -f "$KEY"
# 走 443 端口连接 GitHub，避免部分云服务器封 22 端口
if ! grep -q "Host github-ecom" "$HOME/.ssh/config" 2>/dev/null; then
  cat >> "$HOME/.ssh/config" <<CFG

Host github-ecom
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile $KEY
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
CFG
  chmod 600 "$HOME/.ssh/config"
fi

echo
echo "请把下面这一整行（ssh-ed25519 开头）复制下来："
echo "------------------------------------------------------------"
cat "$KEY.pub"
echo "------------------------------------------------------------"
echo "然后在浏览器打开：https://github.com/$REPO/settings/keys/new"
echo "  Title 随便填（如 server），Key 粘贴上面那一行，不要勾选 Allow write access，点 Add key。"
echo
# 注意：GitHub 认证成功时 ssh 也返回非 0，所以只看输出文字
while true; do
  read -rp "添加好后按回车继续检查……" _
  OUT="$(ssh -T github-ecom 2>&1 || true)"
  if echo "$OUT" | grep -q "successfully authenticated"; then break; fi
  echo "    还没连通，GitHub 返回：$OUT"
done
echo "    已连通 GitHub"

echo "==> 3/4 接入仓库"
if [ ! -d .git ]; then
  git init -q
  git remote add origin "git@github-ecom:$REPO.git"
fi
git fetch -q origin
# 用仓库里的代码覆盖同名文件；.env、state/、server/cache/、.venv 不在仓库中，保持不动
git reset -q --hard origin/main
git branch -q --set-upstream-to=origin/main 2>/dev/null || true

echo "==> 4/4 更新到最新版本"
bash deploy/update.sh
