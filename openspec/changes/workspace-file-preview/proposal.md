## Why

主聊天页面（`/`）当前缺少工作区文件感知能力，存在两个体验缺口：用户必须切换到 `/files` 页面或下载到本地才能查看 AI 生成的报告/Markdown/代码/图片，打断对话流；当 nanobot agent 通过 `write_file`/`edit_file` 修改了工作区文件，聊天页面没有任何反馈。本变更通过在主页面引入工作区文件树 + 实时预览栏来弥补缺口。

## What Changes

- 主页面 `/` 重构为**三栏布局**：左侧会话列表 + 中间对话框 + 右侧工作区文件预览。
- 后端新增 `GET /api/workspace/preview` 端点，按文件类型返回适合的预览载荷（text/markdown/json/image/pdf/html/docx/xlsx/binary）。
- 后端新增 `WebSocket /ws/files` 端点，基于 watchdog 推送文件 created/modified/deleted/moved 事件，带 100ms 防抖。
- 平台 gateway 的 WebSocket 代理路由从 `/ws/{session_id}` 改为通配 `/ws/{path:path}`，**BREAKING** 仅对路由模式而言（旧路径仍然兼容匹配）。
- 前端新增 `FileTree` 组件 + `FilePreview` 组件族（文本/Markdown/JSON/图片/PDF/HTML/Word/Excel/Binary 各自子组件）+ `useFileEvents` Hook。
- 前端新增依赖 `docx-preview` 和 `react-window`；后端新增依赖 `watchdog>=4.0.0`。

## Capabilities

### New Capabilities
- `workspace-file-preview`: 工作区文件树浏览、按文件类型预览、基于 WebSocket 的实时同步推送。

### Modified Capabilities
<!-- 当前 openspec/specs/ 为空，无既有 capability 需要修改 -->

## Impact

- **后端**：新增 `nanobot/web/preview.py`、`nanobot/web/watcher.py`；修改 `nanobot/web/server.py`、`nanobot/channels/web.py`；`pyproject.toml` 增加 `watchdog>=4.0.0`。
- **平台**：修改 `platform/app/routes/proxy.py`，WebSocket 代理路径改通配。
- **前端**：新增 `frontend/components/FileTree.tsx`、`frontend/components/FilePreview/*`、`frontend/hooks/useFileEvents.ts`；修改 `frontend/app/page.tsx`、`frontend/lib/api.ts`、`frontend/package.json`（+ `docx-preview` + `react-window`）。
- **API**：新增 `/api/workspace/preview` 与 `/ws/files` 两个端点。
- **不在范围**：PowerPoint/老二进制 Office/.odt/.rtf 预览；文件编辑、搜索、版本历史、视频音频预览。
