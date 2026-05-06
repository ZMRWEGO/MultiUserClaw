## Why

主聊天页面已经有了三栏布局 + 工作区文件预览（`workspace-file-preview` 已归档/进行中），但用户在写消息时仍要手动从右侧文件树定位到目标文件——既无法快速搜索"AI 刚才生成的报告叫什么"，也不能在写消息时让右侧预览同步切到对照文件。这个变更通过聊天输入框的 `@` 快捷提示菜单弥补缺口，对齐 `/` slash command 的交互模式。

`@` 仅是 UI 输入辅助：**消息文本不变，AI 不会自动得到文件内容**——若需要它会通过 `read_file` 工具读取。

## What Changes

- 后端 `nanobot/web/files.py` 新增 `search_workspace_files(workspace, query, limit) -> dict` 函数：walk 工作区，按 `(rank asc, mtime desc)` 排序返回 top-N 可预览文件。
- 后端 `nanobot/web/server.py` 新增 `GET /api/workspace/files?q=&limit=` 端点（透过现有 `/api/nanobot/*` 自动代理）。
- 前端新增 `frontend/hooks/useMentionPicker.ts`：维护 `@` 触发态的状态机（detectMention / debounce / 键盘交互）。
- 前端 `frontend/lib/api.ts` 新增 `searchWorkspaceFiles` + `WorkspaceFile` 类型。
- 前端 `frontend/app/page.tsx` 接入 hook、渲染 popup、`onCommit` 联动 `selectedFile`。
- 前端 `frontend/components/FileTree.tsx` 新增 `scrollToPath` prop：自动展开父链 + scrollIntoView。

## Capabilities

### New Capabilities
- `mention-workspace-file`: 聊天输入框 `@` 触发的工作区文件实时搜索 + 选中后联动右侧预览/左侧文件树。

### Modified Capabilities
- 无（与现有 `workspace-file-preview` capability 解耦——本期只读 `preview_kind_for`，不修改其行为）

## Impact

- **后端**：修改 `nanobot/web/files.py`、`nanobot/web/server.py`；新增 `tests/test_files_search.py`。
- **前端**：新增 `frontend/hooks/useMentionPicker.ts`；修改 `frontend/lib/api.ts`、`frontend/app/page.tsx`、`frontend/components/FileTree.tsx`；新增 `frontend/tests/e2e/mention.spec.ts`。
- **API**：新增 `/api/workspace/files`（gateway 自动透传）。
- **不在范围**：
  - 把 `@` 文件作为 LLM 上下文/附件（明确剔除——只是 UI 输入辅助）
  - `@` 引用目录（只允许文件）
  - 富文本 chip / 历史消息中 `@path` 渲染为可点击链接（第二期可选）
  - 多文件 `@` 同时切 tab（最后一个 `@` 选中胜出）
  - Web 通道以外的输入（cron 编辑等）
