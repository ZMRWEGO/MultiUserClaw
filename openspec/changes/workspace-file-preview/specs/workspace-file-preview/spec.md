## ADDED Requirements

### Requirement: 工作区文件预览端点

后端 SHALL 提供 `GET /api/workspace/preview?path=<relative_path>` 端点，根据文件扩展名/内容返回结构化预览载荷。响应字段必须包含 `kind` (text/markdown/json/image/pdf/html/docx/xlsx/binary)、`size`、`modified`，且按 kind 携带相应字段（`content`/`download_url`/`language`/`truncated` 等）。

#### Scenario: 预览 Markdown 文件
- **WHEN** 客户端请求 `GET /api/workspace/preview?path=docs/report.md`，文件存在且 < 5MB
- **THEN** 服务端返回 `200 OK`，响应体满足 `{ "kind": "markdown", "content": "...", "size": <int>, "modified": "<iso8601>" }`

#### Scenario: 预览 5MB 以上文本文件
- **WHEN** 客户端请求一个 6MB 的 .log 文件
- **THEN** 服务端返回 `kind: "text"`、`truncated: true`，且 `content` 长度等于前 5MB

#### Scenario: 预览图片文件
- **WHEN** 客户端请求 `path=assets/logo.png`
- **THEN** 服务端返回 `{ "kind": "image", "download_url": "...", "content_type": "image/png" }`，**不**包含 base64 内容

#### Scenario: 预览 docx 文件
- **WHEN** 客户端请求 `path=plan.docx`
- **THEN** 服务端返回 `{ "kind": "docx", "download_url": "...", "size": <int> }`，让前端自行用 docx-preview 库渲染

#### Scenario: 预览二进制（zip / 老 office）
- **WHEN** 客户端请求 `path=archive.zip` 或 `path=legacy.doc`
- **THEN** 服务端返回 `{ "kind": "binary", "reason": "archive" | "office_legacy", "download_url": "..." }`

#### Scenario: 路径穿越攻击
- **WHEN** 客户端请求 `path=../../etc/passwd`
- **THEN** 服务端返回 `400` 或 `403`，且不返回任何文件内容

#### Scenario: 文件不存在
- **WHEN** 客户端请求一个不存在的路径
- **THEN** 服务端返回 `404`

### Requirement: 文件事件 WebSocket 推送

后端 SHALL 提供 `WebSocket /ws/files` 端点，连接建立后实时推送工作区内的文件 created/modified/deleted/moved 事件，事件载荷 100ms 内合并防抖，跳过隐藏目录、`.git`、`node_modules`、`__pycache__`、`.venv`、`.next`、`dist`、`build`。

#### Scenario: 收到新建文件事件
- **WHEN** WebSocket 已连接，agent 调用 `write_file("docs/new.md", ...)` 创建新文件
- **THEN** 客户端在 1 秒内收到一条 `{ "type": "file_event", "event": "created", "path": "docs/new.md", "is_directory": false, ... }`

#### Scenario: 收到修改文件事件
- **WHEN** 已存在文件 `docs/TODO` 被 `edit_file` 修改
- **THEN** 客户端收到一条 `event: "modified"` 事件，包含新的 `size` 和 `modified` 时间戳

#### Scenario: 收到删除文件事件
- **WHEN** 文件被删除
- **THEN** 客户端收到一条 `event: "deleted"` 事件

#### Scenario: 防抖合并
- **WHEN** 同一文件在 50ms 内被连续 modify 5 次（如保存大文件）
- **THEN** 客户端只收到 1 条 `modified` 事件（最后一次的状态）

#### Scenario: 跳过黑名单目录
- **WHEN** `node_modules/some-pkg/index.js` 被修改
- **THEN** 客户端**不**收到事件

#### Scenario: WebSocket 心跳
- **WHEN** 客户端发送 `{"type":"ping"}`
- **THEN** 服务端在 1 秒内回复 `{"type":"pong"}`

#### Scenario: 连接快照通知
- **WHEN** WebSocket 连接刚建立
- **THEN** 服务端立即推送一次 `{"type":"snapshot"}` 消息，提示客户端做一次全量刷新

### Requirement: 主页面三栏布局

前端 SHALL 把主页面 `/` 重构为三栏布局：左侧会话列表（宽度 `w-64`）、中间聊天区（自适应）、右侧工作区文件树 + 预览（默认宽度 `w-80`）。整体高度 `h-[calc(100vh-3.5rem)]`。

#### Scenario: 三栏正确显示
- **WHEN** 用户登录后访问 `/`
- **THEN** 页面同时显示会话列表、聊天区、文件树/预览三个区域

#### Scenario: 右侧栏可折叠
- **WHEN** 用户点击右侧栏折叠按钮
- **THEN** 文件树/预览栏隐藏，聊天区填充剩余空间

### Requirement: 文件树组件

前端 SHALL 提供 FileTree 组件，懒加载子目录、保留展开状态、支持基于 file_event 的增量更新（created→插入、deleted→移除、modified→更新元数据、moved→删除旧+插入新）。

#### Scenario: 点击目录展开
- **WHEN** 用户点击一个未展开的目录节点
- **THEN** 组件调用 `/api/workspace/browse?path=...` 拉取子项并展开

#### Scenario: 选中文件触发预览
- **WHEN** 用户点击一个文件节点
- **THEN** 节点高亮，右侧 FilePreview 加载该文件的预览

#### Scenario: 实时插入新建节点
- **WHEN** 收到 `event: "created", path: "docs/new.md"`，且 `docs/` 目录已展开
- **THEN** `new.md` 节点出现在 `docs/` 子节点中

### Requirement: 文件预览组件

前端 SHALL 提供 FilePreview 组件，根据后端返回的 `kind` 分发到对应子组件渲染：text/markdown/json/image/pdf/html/docx/xlsx/binary 各自一种。

#### Scenario: 预览 Markdown
- **WHEN** 选中 `.md` 文件
- **THEN** 使用 react-markdown + remark-gfm 渲染（含表格、任务列表、代码块）

#### Scenario: 预览图片
- **WHEN** 选中 `.png` 文件
- **THEN** 使用 `<img>` 标签 + 带 token 的 blob URL 显示

#### Scenario: 预览 PDF
- **WHEN** 选中 `.pdf` 文件
- **THEN** 使用 `<iframe>` 加载 blob URL，浏览器原生渲染

#### Scenario: 预览 HTML（XSS 防护）
- **WHEN** 选中 `.html` 文件
- **THEN** 使用 `<iframe sandbox="">` 加载，最严格沙箱（禁用脚本/表单/弹窗/跨源）

#### Scenario: 预览 Word
- **WHEN** 选中 `.docx` 文件
- **THEN** 使用 docx-preview 库渲染基本格式（标题、段落、表格、图片）

#### Scenario: 预览 Excel
- **WHEN** 选中 `.xlsx` 文件
- **THEN** 使用 xlsx 解析 + react-window 虚拟滚动表格，多 sheet 显示底部 tab

#### Scenario: 大 Excel 保护
- **WHEN** 选中的 `.xlsx` 总 cells 超过 100 万
- **THEN** 显示"文件过大，建议下载查看"+ 下载按钮，**不**渲染表格

#### Scenario: 二进制文件
- **WHEN** 选中 `.zip`/`.doc`/`.exe`/未知扩展名
- **THEN** 显示文件名 + 大小 + MIME + "下载"按钮，**不**尝试渲染

#### Scenario: 文件被改动自动重载
- **WHEN** 当前预览的文件收到 `modified` 事件
- **THEN** 预览组件自动重新拉取最新内容并渲染

#### Scenario: 文件被删除提示
- **WHEN** 当前预览的文件收到 `deleted` 事件
- **THEN** 预览区显示"文件已被删除"提示

### Requirement: useFileEvents Hook

前端 SHALL 提供 `useFileEvents()` Hook，封装 WebSocket 连接，复用项目现有 wsManager 重连模式（指数退避 1s→30s），保留最近 200 条事件。

#### Scenario: WebSocket 自动重连
- **WHEN** WebSocket 因网络断开
- **THEN** Hook 在 1s 后第一次重试，失败则按指数退避（最长 30s）继续

#### Scenario: 事件流上限
- **WHEN** 已收到 250 条事件
- **THEN** 内部 state 只保留最近 200 条

#### Scenario: 提供 lastEventForPath
- **WHEN** 调用 `lastEventForPath("docs/TODO")`
- **THEN** 返回该路径最近一次事件的时间戳，或 `null`（如果未收到过）

### Requirement: 平台网关 WebSocket 代理通配

平台 gateway SHALL 把 WebSocket 代理路由从 `/ws/{session_id}` 改为通配 `/ws/{path:path}`，使 `/api/nanobot/ws/files` 等多段路径都能转发到上游容器，同时保持现有 chat WS 路径兼容。

#### Scenario: 代理 chat WebSocket（兼容性）
- **WHEN** 客户端连接 `/api/nanobot/ws/web:default?token=...`
- **THEN** gateway 转发到容器内 `/ws/web:default`，连接成功

#### Scenario: 代理 files WebSocket
- **WHEN** 客户端连接 `/api/nanobot/ws/files?token=...`
- **THEN** gateway 转发到容器内 `/ws/files`，连接成功

#### Scenario: 多用户模式 token 鉴权
- **WHEN** 客户端用无效 token 连接 `/api/nanobot/ws/files`
- **THEN** gateway 拒绝连接（HTTP 401 或关闭握手），不打开上游连接
