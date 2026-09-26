# 重点商品经营作战台

面向品牌电商商品运营的 AI 经营平台：AI 每天盯盘，异常自动预警并归因到具体原因，从动作库给出可执行的方案，每周自动生成经营周报。同一套方法打包为标准 Skill，可在通用 Agent 上复用。

## 目录

```
methods/        方法内核（平台与 Skill 共用的唯一来源）：指标字典、拆解树、分层、品类配置、预警规则、归因 SOP、动作库、报告模板
core/           计算层（平台与 Skill 共用）：加载校验、分层、贡献度拆解、预警扫描、健康度、动作方案、诊断工具、数字校验、周报数据包
server/         后端（FastAPI）与 AI 编排：诊断 Agent、周报、追问、缓存与降级；接口文档在 /docs
web/            前端（React + TypeScript + Ant Design + ECharts）；构建产物 web/dist 由后端直接托管
data/           模拟数据生成器与两个数据集（3C 数码配件、休闲零食）
skill/          Skill 源文件与构建脚本；构建产物在 dist/product-ops-analysis/
scripts/        check_cases.py 案例校验 · warm_cache.py 预热 AI 缓存 · reset_demo.py 恢复演示起点
tests/          计算层单元测试 test_core.py · 界面走查 e2e_ui.py（Playwright）
deploy/         一键部署脚本
```

## 本地运行

```bash
pip install -r requirements.txt
python data/generator/generate.py     # 生成模拟数据（已附带，可跳过）
python scripts/check_cases.py         # 校验演示案例
python -m unittest discover -s tests  # 单元测试
LLM_MODE=mock PORT=8000 python -m server.app   # 用模拟模型启动；配置 .env 后去掉 LLM_MODE 即接入真实模型
```

浏览器打开 http://127.0.0.1:8000 。

### 修改前端

需要 Node.js 18+：

```bash
cd web
npm ci
npm run dev      # 开发模式 http://127.0.0.1:5173 ，/api 自动转发到 8000 端口的后端
npm run build    # 类型检查 + 构建到 web/dist；服务器部署只用这份产物，不需要 Node
```

### 技术栈

| 层 | 选型 |
| --- | --- |
| 前端 | React 18 · TypeScript · Vite · Ant Design 5 · ECharts 5 · react-markdown |
| 后端 | Python 3.10+ · FastAPI · uvicorn · pandas |
| 存储 | 数据集为 CSV；运行状态（问题卡处理、动作、报告）为 SQLite 单文件 state/state.db |
| 模型 | OpenAI 兼容接口，流式输出（SSE），工具调用 / 证据包两种编排模式 |

## 模型

OpenAI 兼容接口，配置见 `.env.example`。模型不可用时依次降级为：缓存结果 → 规则生成，演示不中断。

## Skill

```bash
python skill/build_skill.py    # 输出 dist/product-ops-analysis/，平台「方法库」页也可下载 zip
```

部署见 [DEPLOY.md](DEPLOY.md)。
