# 多 AI 协同工作平台

让多个大模型围绕同一个问题**轮流发言、互相评分、被淘汰、达成共识**，全程实时可见。
后端 FastAPI + SQLite，前端 Vue 3 + Element Plus，单进程部署，无需 Redis / MQ / 容器。

---

## 它解决什么问题

单个模型的回答没有对抗性，容易一本正经地跑偏。这个平台把一次提问变成一场有规则的讨论：

- **轮流发言**：按席位顺序发言，每个模型能看到前面所有人的观点
- **裁判打分**：独立的裁判模型对每轮发言评分，末位累计会被**禁言**淘汰
- **弹劾机制**：当多数席位认为某个发言者有严重问题，可发起弹劾，通过后由替补补位
- **共识检测**：裁判判定观点已收敛时提前结束，不再空转烧 token
- **全程可回溯**：每一次 AI 调用的模型、token、耗时、失败原因都落库，可在 Token 面板查看

## 功能面板

| 面板 | 说明 |
| --- | --- |
| 多 AI 讨论 | 建讨论、实时看发言流（SSE）、评分榜、共识结论、暂停/终止/延长 |
| 文档处理 | 上传 PDF / Word / 文本，交给多个模型并行处理并汇总 |
| AI 成文 | 大纲 → 分节撰写 → 修订的多阶段流水线 |
| 记忆模块 | 跨会话的上下文记忆 |
| 系统监控 | 模型可用性、并发与超时配置、服务状态 |
| Token 消耗统计 | 各模型累积/单次消耗、按日趋势、失败率、调用明细 |

## 技术栈

**后端**：FastAPI 0.109 · Uvicorn / Gunicorn · SQLite(aiosqlite) · httpx(异步调用模型) · SSE 推送
**前端**：Vue 3.4 · Vite 5 · Element Plus 2.8 · Pinia · vue-router · axios · markdown-it

**接入的模型**（10 个，官方与中转双渠道）：

- 官方：DeepSeek · 智谱 GLM · 文心一言 · Kimi · 讯飞星火 · 腾讯混元
- 中转：OpenAI · Claude · Gemini · Grok

主模型失败会自动降级到备用模型；并发数、超时、重试退避、失败是否跳过均可在 `.env` 调。

## 目录结构

```
.
├── backend/               FastAPI 后端
│   ├── app/
│   │   ├── main.py          应用入口（装配 + 生命周期）
│   │   ├── config.py        配置与环境变量
│   │   ├── routers/         /api/discussions · articles · docs · scores · memory · system
│   │   ├── services/
│   │   │   ├── ai_scheduler.py      并发调度、重试、降级、调用日志
│   │   │   ├── discussion_engine.py 讨论主流程、评分、弹劾、共识
│   │   │   └── official_clients/    各模型官方协议适配
│   │   └── database/        连接与建表
│   ├── deploy/            systemd unit + nginx 配置
│   └── .env.example       环境变量模板（复制为 .env 后填写）
└── frontend/              Vue 3 前端
    ├── src/views/         六个面板
    ├── src/api/           后端接口封装
    └── deploy/            nginx 配置与部署说明
```

## 快速开始

### 后端

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # 填写你的模型密钥
python -m uvicorn app.main:app --reload --port 8000
```

启动时会依次做：建目录 → 校验配置 → 初始化数据库 → 同步模型注册表。
接口文档：<http://localhost:8000/docs>

### 前端

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173，已配好 /api 代理
npm run build:prod              # 产物在 frontend/dist
```

### 配置

`.env` 分五组，除模型密钥外都有默认值，一般不用改：

| 分组 | 关键项 |
| --- | --- |
| 模型密钥 | `RELAY_API_KEY`、`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`WENXIN_API_KEY/SECRET_KEY`、`KIMI_API_KEY` |
| 调用参数 | `AI_MAX_CONCURRENT`、`AI_CALL_TIMEOUT`、`AI_RETRY_DELAY_BASE`、`AI_FALLBACK_ENABLED` |
| SSE 与讨论 | `SSE_HEARTBEAT_INTERVAL`、`PAUSE_TIMEOUT_SECONDS` |
| 服务 | `SERVER_HOST`、`SERVER_PORT`、`SERVER_WORKERS` |
| 其他 | `MAX_FILE_SIZE_MB`、`LOG_LEVEL`、`CORS_ORIGINS` |

## 部署

服务器上是 systemd + nginx，配置都在仓库里：

```bash
# 后端：backend/deploy/multi-ai.service
systemctl restart multi-ai

# 前端构建产物由 nginx 托管：frontend/deploy/nginx.conf
# 接口反向代理：backend/deploy/nginx.conf
```

## 说明

- 密钥、数据库、虚拟环境、构建产物均不在版本控制内（`.gitignore` 已排除）；`.env` 只提交 `.env.example` 模板。
- 每次 AI 调用都会写入 `ai_call_logs`（模型、prompt/completion token、耗时、状态、错误信息），Token 统计面板读的就是这张表。
