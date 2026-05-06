## 1. Phase 1 — 后端基础预览

- [x] 1.1 在 `nanobot/web/preview.py` 新增 `preview_kind_for(path) -> tuple[kind, language|None]` 函数（按扩展名映射；无扩展名做 UTF-8 4KB 嗅探）
- [x] 1.2 在 `nanobot/web/preview.py` 新增 `read_text_safely(path, max_bytes=5_242_880) -> tuple[content, truncated]`
- [x] 1.3 在 `nanobot/web/server.py` 新增 `GET /api/workspace/preview` 端点：调用 `_resolve_workspace_path` 防穿越；按 kind 组装响应；text/markdown/json 内嵌 content；image/pdf/html/docx/xlsx/binary 返回 `download_url`
- [x] 1.4 编写后端单元测试（路径穿越、5MB 截断、各类型识别）

## 2. Phase 1 — 前端三栏布局与组件

- [x] 2.1 重构 `frontend/app/page.tsx` 为三栏布局（左 w-64 / 中自适应 / 右 w-80）
- [x] 2.2 新增 `frontend/components/FileTree.tsx`（懒加载子目录、保留展开状态、点击文件触发 onSelect）
- [x] 2.3 新增 `frontend/components/FilePreview/index.tsx`（按 kind 路由分发）
- [x] 2.4 新增 `frontend/components/FilePreview/TextPreview.tsx`（`<pre><code>` monospace）
- [x] 2.5 新增 `frontend/components/FilePreview/MarkdownPreview.tsx`（react-markdown + remark-gfm）
- [x] 2.6 新增 `frontend/components/FilePreview/JsonPreview.tsx`（pretty-print `<pre>`）
- [x] 2.7 新增 `frontend/components/FilePreview/ImagePreview.tsx`（带 token blob URL → `<img>`）
- [x] 2.8 新增 `frontend/components/FilePreview/PdfPreview.tsx`（blob URL → `<iframe>`）
- [x] 2.9 新增 `frontend/components/FilePreview/HtmlPreview.tsx`（`<iframe sandbox="">`）
- [x] 2.10 新增 `frontend/components/FilePreview/BinaryPreview.tsx`（仅显示元数据 + 下载按钮）
- [x] 2.11 在 `frontend/lib/api.ts` 新增 `getWorkspacePreview(path)` 与 `fetchWorkspaceBlobUrl(path)` 工具
- [x] 2.12 右侧栏顶部展示工作区路径 + 刷新按钮，底部展示 WS 连接占位（Phase 2 接入）

## 3. Phase 2 — 后端 watcher + WebSocket

- [x] 3.1 在 `pyproject.toml` 增加 `watchdog>=4.0.0` 依赖
- [x] 3.2 新增 `nanobot/web/watcher.py`：实现 `WorkspaceWatcher`（lazy 启动、100ms 防抖、目录黑名单、跨线程通过 `run_coroutine_threadsafe` 投递、`add_listener/remove_listener`）
- [x] 3.3 在 `nanobot/web/server.py` 新增 `WebSocket /ws/files` 端点：accept → add_listener → 循环 `queue.get` 推事件；处理 `ping/pong`；连接时立即推 `{"type":"snapshot"}`；断开时 `remove_listener`
- [x] 3.4 在 `nanobot/channels/web.py` 启动时构造 `WorkspaceWatcher`，挂入 `app.state.workspace_watcher`；停止时调用 `stop()`
- [x] 3.5 单元测试 watcher（防抖、黑名单、fanout 多 listener）

## 4. Phase 2 — 平台 WS 代理通配

- [x] 4.1 修改 `platform/app/routes/proxy.py`：把 `@router.websocket("/ws/{session_id}")` 改为 `@router.websocket("/ws/{ws_path:path}")`，把上游 URL 从 `f"ws://...:.../ws/{session_id}"` 改为 `f"ws://...:.../ws/{ws_path}"`
- [x] 4.2 验证现有 chat WS 连接（`/ws/web:default`）仍能正常工作（platform 测试）

## 5. Phase 2 — 前端实时同步

- [x] 5.1 新增 `frontend/hooks/useFileEvents.ts`：复用 wsManager 模式（指数退避 1s→30s）；连接 `/api/nanobot/ws/files?token=...`；保留最近 200 条事件；提供 `lastEventForPath`
- [x] 5.2 在 `FileTree` 中接收 `events: FileEvent[]` 做增量更新（created/deleted/modified/moved）
- [x] 5.3 在 `FilePreview` 中通过 `lastEventForPath(currentPath)` 计算 `reloadKey`，触发重新拉取
- [x] 5.4 处理删除提示：当前预览的文件 `deleted` → 显示"文件已被删除"
- [x] 5.5 重连后触发一次根目录 browse，强制对齐状态
- [x] 5.6 主页面接入 useFileEvents，传给 FileTree 和 FilePreview

## 6. Phase 3 — Word / Excel 前端预览

- [ ] 6.1 在 `frontend/package.json` 增加 `docx-preview` 与 `react-window` 依赖（运行 `npm install`）
- [ ] 6.2 在后端 `preview_kind_for` 增加 `.docx`/`.xlsx` → 对应 kind 的映射
- [ ] 6.3 新增 `frontend/components/FilePreview/WordPreview.tsx`：拉 blob → `docx-preview.renderAsync` 注入 `<div>`；`reloadKey` 变化时清空重渲染
- [ ] 6.4 新增 `frontend/components/FilePreview/ExcelPreview.tsx`：xlsx 解析 → 多 sheet tab → react-window FixedSizeGrid 虚拟滚动；超过 100w cells 显示"建议下载"
- [ ] 6.5 在 `FilePreview/index.tsx` 路由中加入 docx/xlsx 分支
- [ ] 6.6 lazy import 这两个组件，避免主页面首屏体积膨胀

## 7. 测试与验收

- [x] 7.1 后端 pytest（preview API + watcher + 路径穿越）
- [x] 7.2 前端 `npm run typecheck` + `npm run lint` 通过
- [x] 7.3 本地 `python start_local.py` 启动全栈
- [x] 7.4 注册测试账号，记录到 `docs/e2e-test-account.md`（或同目录 README）
- [x] 7.5 用 agent-browser 打开主页面，验证三栏布局可见
- [x] 7.6 e2e 验证 Phase 1：预览 .md / .png 成功
- [ ] 7.7 e2e 验证 Phase 2：在工作区放文件 → 1 秒内文件树更新；编辑 → 预览自动刷新；删除 → 提示
- [ ] 7.8 e2e 验证 Phase 3：预览 .docx 与小 .xlsx 文件
- [x] 7.9 截图存档 e2e 测试关键步骤
