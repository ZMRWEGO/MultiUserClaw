# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 提供关于本仓库代码的指导。

## 项目概述

MultiUserClaw 是 [nanobot](https://github.com/nanobot-ai/nanobot)（轻量级 Python AI 智能体框架）的多租户分支。它通过 FastAPI 网关、Next.js 前端和每用户 Docker 容器隔离来封装 nanobot，使多个用户可以共享同一个部署，而无需向用户代码暴露 LLM API 密钥。

本仓库包含三个一起开发的可部署单元：

- `nanobot/` —— 上游 `nanobot` Python 包（v0.1.4.post3）的 vendored 副本，针对代理模式打过补丁。作为 `nanobot-ai` 安装，并暴露 `nanobot` CLI。
- `platform/` —— FastAPI 网关（`nanobot-platform`）。管理数据库、用户容器和 LLM 密钥注入。
- `frontend/` —— Next.js 13 + Tailwind + shadcn/ui 聊天 UI（端口 3080）。

外加 `bridge/` —— 会被编译进 nanobot 镜像的 Node.js WhatsApp 桥接。

## 常用命令

### 本地开发（单机）

```bash
# 启动所有服务：postgres (docker) + nanobot web + gateway + frontend
python start_local.py

# 子集 / 跳过某些服务
python start_local.py --only db,gateway,frontend
python start_local.py --skip nanobot

# 停止 start_local.py 启动的所有服务
python start_local.py --stop

# 健康检查：postgres、gateway、用户容器、frontend
python check_status.py
python check_status.py --gateway http://localhost:8080
```

`start_local.py` 读取仓库根目录的 `.env`，并将 `*_API_KEY`、`*_API_BASE`、`JWT_SECRET`、`DEFAULT_MODEL` 作为 `PLATFORM_*` 变量转发给网关。它设置 `PLATFORM_DEV_NANOBOT_URL=http://127.0.0.1:18080`，使网关代理到本地 nanobot，而不是为每个用户生成 Docker 容器 —— 开发模式下只有一个共享后端。

默认端口：postgres 5432、nanobot web 18080、gateway 8080、frontend 3080。

### Docker 部署

```bash
python prepare.py                              # uv env + docker 检查 + 镜像拉取
python deploy_docker.py --build-only           # 构建 nanobot:latest 用户容器镜像
python deploy_docker.py --host 192.168.1.10    # 完整部署；将 host 写入 NEXT_PUBLIC_API_URL
python deploy_docker.py --rebuild gateway      # 仅重建 gateway 镜像
python deploy_docker.py --rebuild frontend     # 仅重建 frontend 镜像
python deploy_docker.py --clean                # 完全拆除并从头重建
```

`docker-compose.yml` 启动 `postgres + gateway + frontend`。**用户容器不在 compose 文件中** —— 网关通过挂载在 `/var/run/docker.sock` 的 Docker socket 按需创建它们。它们被命名为 `nanobot-user-<id>`，并在 `nanobot-internal` 网络上运行。

### nanobot CLI（单用户 / 开发）

```bash
nanobot onboard                  # 初始化 ~/.nanobot/config.json + 工作区
nanobot agent                    # 交互式 REPL
nanobot agent -m "hello"         # 单次运行
nanobot web -p 18080             # gateway + Web 通道（平台代理模式下使用）
nanobot gateway -p 18790         # 包含所有通道的完整 gateway（Telegram、Discord、...）
nanobot status                   # 配置 + 提供商 + 通道状态
nanobot channels status
nanobot cron list|add|remove|enable|run
```

会话是 `~/.nanobot/sessions/` 下的 JSONL 文件。使用 `rm ~/.nanobot/sessions/<key>.jsonl` 清除。

### 测试与代码检查

```bash
pytest tests/                                  # nanobot 核心测试（asyncio_mode=auto）
pytest tests/test_tool_validation.py           # 单个文件
pytest platform/tests/                         # 平台测试
ruff check nanobot/                            # 代码检查（行长度 100，目标 py311）

cd frontend
npm run dev                                    # next dev，端口 :3080
npm run build && npm run start
npm run lint                                   # next 代码检查
npm run typecheck                              # tsc --noEmit
```

## 架构

### 多租户请求流

```
browser → frontend:3080 → gateway:8080 ──┬─► /api/nanobot/* → 用户容器 (proxy.py)
                                          ├─► /api/auth/*    → auth.py (JWT + bcrypt)
                                          └─► /llm/v1/*      → llm_proxy/service.py
                                                                  │ 注入真实 API 密钥
                                                                  ▼
                                                              上游 LLM (Anthropic/OpenAI/...)
```

关键安全不变量：**LLM API 密钥仅存在于网关的环境中**。用户容器在 `nanobot-internal` Docker 网络上运行，无法访问上游 LLM API。它们使用一次性的 `X-Container-Token`（在创建时按容器设置，不是真正的 API 密钥）向网关认证。网关根据请求的模型名将该令牌交换为真实的提供商密钥。

这是通过对 nanobot 的补丁实现的 —— 参见下方的"Vendored nanobot 补丁"。

### nanobot 核心（`nanobot/`）

基于消息总线的通道无关智能体循环：

```
通道 (telegram/web/discord/...) ──publish_inbound──► MessageBus (asyncio.Queue)
                                                          │
                                                          ▼
                                                      AgentLoop._process_message
                                                          │ ContextBuilder → LLM → 工具循环
                                                          ▼
                                                      MessageBus.outbound ──► ChannelManager
                                                                              ──► channel.send()
```

关键文件：

- [nanobot/agent/loop.py](nanobot/agent/loop.py) —— `AgentLoop` ReAct 循环。`run()` 用于长期运行的总线消费者，`process_direct()` 用于 CLI/cron 单次运行。
- [nanobot/agent/context.py](nanobot/agent/context.py) —— 系统提示词组装：身份 + 引导文件（`AGENTS.md`、`SOUL.md`、`USER.md`）+ 记忆（`memory/MEMORY.md`、`memory/YYYY-MM-DD.md`）+ 技能（始终在线的内联技能，其他为 XML 索引）。
- [nanobot/agent/tools/](nanobot/agent/tools/) —— `read_file`、`write_file`、`edit_file`、`list_dir`、`exec`、`web_search`、`web_fetch`、`message`、`spawn`、`cron`、`mcp`。`exec` 通过正则表达式阻止 `rm -rf`、`dd`、`format` 等命令。
- [nanobot/providers/litellm_provider.py](nanobot/providers/litellm_provider.py) —— 包装 LiteLLM 的单一提供商类。提供商路由通过 [nanobot/providers/registry.py](nanobot/providers/registry.py) 以数据驱动方式完成 —— 添加提供商意味着追加一个 `ProviderSpec`，而非子类化。
- [nanobot/session/manager.py](nanobot/session/manager.py) —— 每会话 JSONL 文件。会话键格式为 `"{channel}:{chat_id}"`，例如 `web:default`、`telegram:12345`、`cron:abc123`。
- [nanobot/channels/](nanobot/channels/) —— 10 个通道适配器（Telegram、Discord、Feishu、Slack、DingTalk、QQ、WhatsApp、Email、Mochat、Web）。全部继承 `BaseChannel`。`web` 通道是一个 FastAPI 应用，在 `/ws/{session_id}` 提供 WebSocket —— 客户端可以断开并重新连接；JSONL 会话是事实来源，没有离线队列。

### 平台网关（`platform/app/`）

FastAPI 应用，入口在 [platform/app/main.py](platform/app/main.py)。所有设置从以 `PLATFORM_` 为前缀的环境变量加载（参见 [platform/app/config.py](platform/app/config.py)）。

- [platform/app/routes/auth.py](platform/app/routes/auth.py) —— 注册/登录/刷新，返回 JWT。
- [platform/app/routes/proxy.py](platform/app/routes/proxy.py) —— `/api/nanobot/*` 将 HTTP+WebSocket 代理到用户容器。使用 `_container_url(db, user)` → `ensure_running()` 惰性创建/取消暂停/重启容器。
- [platform/app/routes/llm.py](platform/app/routes/llm.py) —— OpenAI 兼容的 `/llm/v1/chat/completions`。解析模型前缀 → 提供商 → 从 `Settings` 注入密钥。
- [platform/app/container/manager.py](platform/app/container/manager.py) —— Docker SDK 生命周期（创建、暂停、归档、重建）。空闲超过 `container_idle_pause_minutes`（默认 30）的容器会被暂停；超过 `container_idle_archive_days`（默认 30）的会被归档。
- [platform/app/db/models.py](platform/app/db/models.py) —— `User`、`Container`、`UsageRecord`、`AuditLog`。SQLAlchemy async + Alembic。
- 按层级（`free`/`basic`/`pro`）在 `Settings.quota_*` 中配置配额，并在 `llm_proxy/service.py` 中强制执行。可在 [platform/app/config.py](platform/app/config.py) 中覆盖。

网关的 `lifespan` 调用 `_ensure_database()`，它连接到 `postgres` admin DB，如果数据库缺失则执行 `CREATE DATABASE` —— 不会自动运行迁移，但启动时会运行 `Base.metadata.create_all`。在 `platform/alembic/` 中使用 Alembic 进行 schema 变更。

### 前端（`frontend/`）

Next.js 13 app router。所有 API 调用都发往 `NEXT_PUBLIC_API_URL`（网关）—— 没有 Next.js API 路由层。状态使用 Zustand 管理。页面：`/`（聊天）、`/login`、`/register`、`/status`、`/cron`、`/files`、`/plugins`、`/skills`、`/marketplace`、`/help`。

聊天页面（`frontend/app/page.tsx`）通过网关代理在 `/api/nanobot/ws/{session_id}` 打开 WebSocket。重连时，它会通过 `/api/nanobot/api/sessions/{key}` 获取完整会话，而不是使用本地缓存，因此智能体在离线期间产生的任何消息都会自动出现。

## Vendored nanobot 补丁

本仓库在特定位置修改了上游 nanobot。**升级 nanobot 时请保留这些补丁**（当你重新 vendor 新版本时，只有列出的文件会分叉 —— 重新应用这些更改，其余保持上游纯净）：

| 文件 | 补丁 |
|---|---|
| `nanobot/providers/litellm_provider.py` | 当设置了 `NANOBOT_LLM_PROXY_URL` 环境变量时，所有 LLM 调用都通过该 URL 路由，并携带 `X-Container-Token` 请求头，而不是使用真实的 `api_key`。 |
| `nanobot/config/schema.py` | 添加 `is_proxy_mode` 标志 |
| `nanobot/cli/commands.py` | 检查 `config.is_proxy_mode` 以跳过提供商密钥验证 |
| `nanobot/channels/manager.py` | 添加 web 通道自动启动 |
| `nanobot/web/` | 新目录：FastAPI 服务器 + 文件上传/下载；含 `archive.py` 异步文件夹压缩任务（`POST/GET /api/workspace/archive*`），目录下载先压缩为 zip 再流式返回，避免阻塞 event loop。 |
| `nanobot/agent/tools/web.py` | 添加 `get_wechat_article` |
| `nanobot/agent/tools/xng_search.py` | 新增（微信搜索后端） |

在不使用 docker 的情况下本地测试更改时，设置 `NANOBOT_LLM_PROXY_URL` 和一个伪造的 `X-Container-Token` 值，以针对 `start_local.py` 的网关测试代理代码路径。

## 配置文件

- 仓库根目录 `.env` —— 被 `start_local.py` 和 `docker-compose.yml` 同时读取。API 密钥、`JWT_SECRET`、`DEFAULT_MODEL` 的唯一事实来源。参见 `.env.example`。
- `~/.nanobot/config.json` —— 单用户配置，在平台外用于 `nanobot agent`/`nanobot web`。提供商密钥存放在这里；多租户模式下不使用。
- `marketplaces.json` —— 技能市场 UI 从中克隆的 git URL。在构建 nanobot 镜像时复制到 `/root/.nanobot/marketplaces.json`。
- `frontend/.env.local` —— 只有 `NEXT_PUBLIC_API_URL` 起作用；默认值为 `http://127.0.0.1:8080`。对于 docker 构建，该值通过 `docker-compose.yml` 的构建参数在构建时写入，而非运行时。

## 分支

- `main` —— 当前 0.1.4 post v3
- `nanobot014` —— 纯净的 nanobot 0.1.4 基线（无 platform/frontend 更改）
- `simple_web` —— 单用户 web 构建（用于在没有平台层的情况下测试聊天 UI）

## 添加测试时

- Python 测试使用 `pytest`，设置 `asyncio_mode = "auto"`（在 `pyproject.toml` 中设置）；异步测试不需要 `@pytest.mark.asyncio`。
- 平台测试位于 `platform/tests/`，从 `platform/` 目录运行，以便 `app` 包可导入。
- 前端没有测试设置 —— `npm run typecheck` 和 `npm run lint` 是唯一的静态检查。
