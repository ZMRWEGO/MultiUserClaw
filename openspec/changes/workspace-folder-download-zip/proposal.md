## Why

工作区 FileTree 已经有"下载文件夹"按钮，但当前实现下载目录时经常报错或卡死，无法可靠交付 zip 包。根因有三：(1) 后端 `/api/workspace/download` 在请求线程内同步遍历目录、把整个 zip 写入 `BytesIO` 再一次性返回，目录稍大就阻塞 event loop；(2) 平台 gateway 代理 `platform/app/routes/proxy.py` 用 `httpx.AsyncClient(timeout=120.0)` 把上游响应整个读入内存才转发，并丢弃了除 `Content-Disposition` 外的所有头（含 `Content-Length`/`Content-Type`），大目录会触发 120s 超时，下游浏览器拿不到正确的下载提示；(3) 前端 `downloadWorkspacePath` 没有任何 loading 或错误反馈，`.catch(() => {})` 静默失败，用户只看到"按了没反应"。

本变更把目录下载改成"异步压缩任务 + 流式下载 + 前端进度提示"的稳定形态。

## What Changes

- **后端**新增异步压缩任务：
  - `POST /api/workspace/archive` —— 按相对路径创建一个压缩任务，返回 `{ job_id, status: "pending" }`。后端在线程池中把目标目录写入临时 zip 文件，期间记录字节进度。
  - `GET  /api/workspace/archive/{job_id}` —— 查询任务状态，返回 `{ status: "pending"|"running"|"ready"|"failed", bytes_written, total_bytes_estimate, error? }`。
  - `GET  /api/workspace/archive/{job_id}/download` —— `status=ready` 时以 `FileResponse` 流式返回 zip，并在响应完成后异步清理临时文件与 job 记录。
  - 单文件下载继续使用原 `/api/workspace/download`，**不变**。
- **后端**新增 `nanobot/web/archive.py` 模块：内部 job 注册表（仅内存，进程内），临时目录在 `workspace/.cache/archive/<job_id>/`，进程退出 / 任务超过 `ARCHIVE_TTL_SECONDS`（默认 600s）后清理。
- **平台 gateway** 改造 `platform/app/routes/proxy.py` 的 HTTP 代理为流式转发：用 `httpx.AsyncClient.stream()` + `fastapi.responses.StreamingResponse`，保留 `Content-Type`、`Content-Length`、`Content-Disposition` 三个关键头；JSON 响应继续走旧的反序列化路径。WebSocket 代理不变。
- **前端**改造 `FileTree.tsx`：目录下载按钮改为调用新 `archiveWorkspacePath(path)` 流程（创建 job → 轮询 status → 完成后触发 blob 下载），按钮在压缩期间显示行内 spinner + 全局 toast"正在压缩 …"，失败时显示 toast 错误信息；单文件下载路径保持调用 `downloadWorkspacePath`。
- **前端** `frontend/lib/api.ts` 新增 `createWorkspaceArchive` / `getWorkspaceArchiveStatus` / `downloadWorkspaceArchive` 以及高层封装 `archiveWorkspacePath`。
- **新增 e2e**（`frontend/tests/folder-download.spec.ts`）：以**有头 Playwright**驱动完整链路 —— 准备含若干文件的目录、点击 FileTree 上目录的下载按钮、断言出现 loading、监听 `page.waitForEvent('download')` 检查文件名为 `<dir>.zip` 且体积大于 0、再断言再次以普通文件下载仍然工作；e2e 是 SDD 主要质量门，须通过才能合并。

## Capabilities

### New Capabilities
- `workspace-folder-download`: 工作区目录的异步压缩、状态查询、流式下载与对应前端 loading 体验。

### Modified Capabilities
<!-- openspec/specs/ 当前为空，无既有 capability 需要修改 -->

## Impact

- **后端**：新增 `nanobot/web/archive.py`；修改 `nanobot/web/server.py`（替换/拆分 `/api/workspace/download` 路由，新增三个 archive 路由）。
- **平台**：修改 `platform/app/routes/proxy.py`，HTTP 代理改流式 + 透传关键响应头；`httpx` 客户端超时由 120s 调整为可配置（archive 下载路由不超时）。
- **前端**：修改 `frontend/lib/api.ts`、`frontend/components/FileTree.tsx`；新增轻量 toast 组件（如 `frontend/components/ui/toast.tsx`，若仓库已有则复用 shadcn 现成组件）；新增 `frontend/tests/folder-download.spec.ts`。
- **API**：新增 `POST /api/workspace/archive`、`GET /api/workspace/archive/{job_id}`、`GET /api/workspace/archive/{job_id}/download`；`/api/workspace/download` 行为收窄为"仅文件下载"（请求目录时返回 400 + 引导信息）。
- **依赖**：无新增运行时依赖；测试侧确保 `frontend/playwright.config.ts` 的 `headed: true` 配置在该 spec 上启用。
- **不在范围**：分卷压缩、断点续传、压缩进度的 WebSocket 推送（用 polling 即可，1s 间隔）、跨用户共享 archive、archive 持久化（重启即丢失任务记录是预期行为）。
