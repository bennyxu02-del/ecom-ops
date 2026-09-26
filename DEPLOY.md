# 部署说明

预计 20–30 分钟。全程在 Mac 的「终端」里操作，大部分是复制粘贴。

## 准备

- 一台 Linux 服务器（Debian / Ubuntu 系，2 核 4 GB），已知 **公网 IP** 和 **root 密码**。系统自带 Python 低于 3.10 时，部署脚本会自动安装 3.11
- 云控制台的防火墙 / 安全组已开放 **22** 和 **80** 端口
- 项目压缩包 `ecom-ops.zip`（前端已构建好，服务器不需要装 Node.js）
- 模型接口地址、密钥、模型名

下文用 `1.2.3.4` 代表你的公网 IP，请替换。

## 第 1 步：上传项目

在 Mac 终端中，进入压缩包所在文件夹（例如「下载」），执行：

```bash
cd ~/Downloads
scp ecom-ops.zip root@1.2.3.4:/opt/
```

第一次连接会问是否信任，输入 `yes`，再输入 root 密码。

## 第 2 步：登录服务器并解压

```bash
ssh root@1.2.3.4
apt-get install -y unzip
cd /opt && unzip -o ecom-ops.zip && cd ecom-ops
```

## 第 3 步：确认模型能连通，并查看可用的模型名

把 `你的密钥` 换成真实密钥：

```bash
curl -s https://it-ai.fineres.com/v1/models -H "Authorization: Bearer 你的密钥" | head -c 1500
```

- 能看到 `"id": "claude-opus-4-8"`：连通正常（目前该接口只提供这一个模型，已在开发环境实测可用，支持工具调用）。
- 连不上：说明这台服务器访问不了接口，请把输出发给我。

## 第 4 步：一键部署

```bash
bash deploy/deploy.sh
```

脚本会依次询问接口地址、模型名（都直接回车用默认）、密钥。完成后显示访问地址，例如 `http://1.2.3.4`。

> 如果安装依赖很慢，可以改用国内镜像：`PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/ bash deploy/deploy.sh`

## 第 5 步：打开页面检查

浏览器打开 `http://1.2.3.4`，右上角应显示「在线模型 · 模型名」（绿点）。

## 第 6 步：预热 AI 缓存（重要）

对所有演示案例实际调用一次大模型，把结果存下来。演示时网络或模型出问题，平台会自动回放这些结果。

```bash
cd /opt/ecom-ops
.venv/bin/python scripts/warm_cache.py --check
```

每个案例会连续跑 3 次检查结论是否稳定。在线模型一次诊断约 1 分钟，全部跑完约 30–40 分钟，可以先去做别的；预热后演示时诊断秒出。最后一行「需要人工复核的条目数」为 0 最好；不为 0 时把输出发给我，我来调整提示词。

## 常用操作

| 操作 | 命令 |
| --- | --- |
| 查看运行状态 | `systemctl status ecom-ops` |
| 查看日志 | `journalctl -u ecom-ops -n 100` |
| 重启服务 | `systemctl restart ecom-ops` |
| 修改模型配置 | `nano /opt/ecom-ops/.env`，改完 `systemctl restart ecom-ops` |
| 演示前恢复初始状态 | `cd /opt/ecom-ops && .venv/bin/python scripts/reset_demo.py && systemctl restart ecom-ops` |
| 现场网络不稳时强制用缓存 | `.env` 中设 `DEMO_MODE=true`，然后重启 |
| 模型不支持工具调用时 | `.env` 中设 `AGENT_MODE=evidence_pack`，然后重启 |

## 更新代码（接入 GitHub 后）

**一次性设置**：把 `deploy/setup_git.sh` 传到服务器 `/opt/ecom-ops/deploy/`，然后执行：

```bash
cd /opt/ecom-ops && bash deploy/setup_git.sh
```

脚本会显示一行以 `ssh-ed25519` 开头的「只读钥匙」，按提示把它添加到 GitHub 仓库的 Deploy keys 页面，回到终端按回车即可。

**之后每次更新**：

| 操作 | 命令 |
| --- | --- |
| 更新到最新版本 | `bash /opt/ecom-ops/deploy/update.sh` |
| 查看所有版本 | `bash /opt/ecom-ops/deploy/update.sh list` |
| 回退到某个版本（如 v2） | `bash /opt/ecom-ops/deploy/update.sh v2` |

`.env`（模型密钥）、`state/`（运行状态和 AI 缓存）、`.venv`（Python 环境）不在仓库里，更新时不会被改动。

## 飞书集成（可选）

平台可以把转交单、审批申请和进展通知直接发到同事的飞书，对方在卡片上点按钮，状态同步回平台。

1. 在飞书开放平台建一个企业自建应用：开通「机器人」能力，开通权限 `im:message:send_as_bot`、`contact:user.id:readonly`，发布版本。
2. 在服务器执行 `cd /opt/ecom-ops && bash deploy/setup_feishu.sh`，输入 App ID 和 App Secret。脚本会启动卡片回调服务 `ecom-ops-feishu`。
3. 回到飞书开放平台：「事件与回调」→「回调配置」→ 订阅方式选「使用长连接接收回调」并保存 → 添加回调「卡片回传交互」（card.action.trigger）→ 创建新版本并发布。
4. 打开平台左侧「集成」页，给各角色设置对应的飞书成员（手机号或邮箱），可以点「发测试消息」确认。

| 操作 | 命令 |
| --- | --- |
| 查看回调服务状态 | `systemctl status ecom-ops-feishu` |
| 查看回调服务日志 | `journalctl -u ecom-ops-feishu -n 100` |
