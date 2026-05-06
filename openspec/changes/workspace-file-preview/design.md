## Context

主聊天页面（`frontend/app/page.tsx`）目前是两栏布局（左会话列表 + 中间聊天），缺少工作区文件可视化。`/files` 是独立页面，切换会打断对话。后端 `nanobot/web/server.py` 已有 `/api/workspace/browse`、`/api/workspace/download`，但没有按文件类型返回前端友好的预览载荷的端点；也没有文件变化通知。

技术约束：
- 已有 WebSocket 基础设施（chat 通道 `/ws/{session_id}`），前端通过 platform gateway 代理。
- 单用户模式直连 nanobot/web；多用户模式由 `platform/app/routes/proxy.py` 反向代理 `/api/nanobot/*` → 用户容器。
- 用户容器在隔离的 Docker 网络上运行，没有公网出口；watchdog 在 Linux 容器内可用 inotify。
- 前端已使用 Next.js 13 app router + Zustand + Tailwind + shadcn/ui，已引入 `react-markdown` + `remark-gfm`，已引入 `xlsx`。

## Goals / Non-Goals

**Goals:**
- 主页面无需切换即可浏览/预览工作区文件。
- AI 通过 `write_file`/`edit_file` 修改文件后 1 秒内前端可见，预览自动刷新。
- 支持的预览类型：text/markdown/json/image/pdf/html/docx/xlsx；其他二进制走"仅下载"。
- 单用户/多用户模式共用一套前端代码。

**Non-Goals:**
- 不支持 .pptx、.doc/.xls/.ppt 老二进制 Office、OpenDocument、RTF 预览。
- 不提供在线编辑、版本历史、全文搜索、视频音频预览。
- 不做文件锁、协作多用户预览。

## Decisions

### 1. 实时推送协议：WebSocket（不用 SSE）

复用现有 chat WS 基础设施（重连/心跳/平台代理），降低前后端心智成本。SSE 单向推送语义更纯，但需要再写一套连接管理；本项目 WS 比 SSE 收益大。

**Alternatives considered:** SSE（被否决：要再写心跳和重连）、HTTP 长轮询（被否决：延迟和资源开销大）。

### 2. 预览载荷分类：后端识别 → 前端按 kind 路由

预览类型由后端 `preview_kind_for(path)` 在文件级别决定（按扩展名优先 + UTF-8 4KB 嗅探兜底）。前端在 `FilePreview` 中按 `data.kind` 分发到具体子组件。前端**不重复识别类型**。

文本/Markdown/JSON 直接把内容塞进响应 JSON；图片/PDF/HTML/docx/xlsx 只返回 `download_url`，前端二次拉取。理由：图片二进制大，不应通过 JSON base64 转码；docx/xlsx 必须给前端库 ArrayBuffer。

### 3. WorkspaceWatcher 单例 + Lazy 启动

每个 `nanobot/web` server 进程持有一个 watcher（用户容器内每个用户独立进程，等价于 per-user 单例）。第一个 WS 连接时 `start()`，最后一个断开时 `stop()`，节省后台 IO。事件通过 100ms 防抖合并（watchdog 在 save 时常发多次 modified）。

跨线程通知：watchdog Observer 在自己线程触发回调，用 `asyncio.run_coroutine_threadsafe` 投递到主事件循环。每个 WS 连接独立 `asyncio.Queue`，事件 fanout。

### 4. 平台 WS 代理改通配 `/ws/{path:path}`

现有 `/ws/{session_id}` 只匹配单段路径，无法同时承载 `/ws/files`。改为 `path:path` 通配。原 chat 路径（`/ws/<session_id>`）仍然落入新路由，无需前端修改。

**Alternatives considered:** 给 `/ws/files` 单独写一条代理路由（被否决：以后再加 WS 端点又要改一次）。

### 5. 大文件保护策略

| 类型 | 上限 | 超限处理 |
|---|---|---|
| text/markdown | 5MB | 返回前 5MB + `truncated: true` |
| json | 5MB | 直接降级为 `binary` 给下载 |
| 图片/PDF/HTML | 无后端硬限 | 浏览器原生处理 |
| docx | 后端不解析 | 前端 docx-preview 自己处理 |
| xlsx | 100w cells | 前端 `react-window` 虚拟化 + 超过显示"建议下载" |

### 6. HTML 预览 sandbox 严格策略

HTML 用 `<iframe sandbox="">`（最严格，禁用 JS、表单、popup、跨源），阻止恶意页面盗 token / 跨域请求。SVG 用 `<img>`（不执行内嵌脚本），不走 iframe。

### 7. 鉴权：blob URL 模式

下载 API（`/api/nanobot/workspace/download`）需要 `Authorization: Bearer ...`。前端通过 `fetch + token → blob → URL.createObjectURL` 拿到本地 blob URL，再喂给 `<img>` / `<iframe>` / docx-preview / xlsx parser。组件 unmount 时 `URL.revokeObjectURL` 释放。

WS 鉴权：单用户模式直连 nanobot/web 无需鉴权；多用户模式由平台 gateway 在代理时验证 token，未验证不打开上游连接。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| watchdog 在某些 fs 上不工作（如某些 Docker volume 驱动） | 启动时捕获异常 → 降级为不推送（前端轮询兜底，未来可加） |
| 用户工作区有 node_modules 几万文件 | 默认目录黑名单跳过：`.git`、`.next`、`node_modules`、`__pycache__`、`.venv`、`dist`、`build` |
| 100ms 防抖窗口太小，git pull 几千文件仍可能压垮前端 | 前端事件流上限 200 条；超出滚动丢弃；FileTree 的 `created` 增量插入只对已展开目录生效 |
| docx-preview 对复杂域/活动控件 fallback 为占位符 | 文档说明：仅基本格式还原；用户可点下载查看原文件 |
| HTML 预览的 XSS 攻击面 | `sandbox=""` + 通过 blob URL 隔离 origin |
| WS 重连漏事件 | 重连时前端触发一次根目录 browse，强制对齐状态；服务端连接建立后立即推 `{"type":"snapshot"}` |
| 多 WS 标签页 | 每个 WS 独立 queue，watcher 仍单例；fanout 复杂度 O(N) 可接受 |

## Migration Plan

无需数据库迁移。分三阶段灰度：

1. **Phase 1**：基础预览（无实时同步）。后端 preview 端点 + 前端三栏 + 各预览组件。回滚：前端开关回退到旧两栏布局。
2. **Phase 2**：实时同步。增加 watchdog + `/ws/files` + 平台代理通配。回滚：前端关闭 useFileEvents Hook，退化为只在 user action 时刷新。
3. **Phase 3**：Word/Excel 高级预览。增加 `docx-preview` + `react-window` 前端依赖。回滚：让 docx/xlsx fallback 到 binary 卡片。

## Open Questions

- 文件树搜索框、展开状态持久化等体验优化：列入 Phase 4 可选项。
- 是否需要 WS 鉴权 token rotate 机制：当前通过 query 传 access token，后续可接入 platform 的刷新机制。
