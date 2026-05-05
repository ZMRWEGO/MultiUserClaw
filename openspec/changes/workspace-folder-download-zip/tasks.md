## 1. 准备：基线确认与测试脚手架

- [x] 1.1 阅读 `nanobot/web/server.py` 当前 `/api/workspace/download` 路由，确认补丁会保留在该文件内（尊重 vendored 补丁清单）
- [x] 1.2 阅读 `platform/app/routes/proxy.py` 当前 HTTP 代理实现，记录现有 JSON 端点行为，作为流式改造的不变量
- [x] 1.3 在 `tests/test_archive.py` 创建空测试文件，准备后端 pytest 用例骨架（asyncio_mode=auto，导入 nanobot.web.archive，先 fail）
- [x] 1.4 在 `frontend/tests/e2e/folder-download.spec.ts` 创建空 e2e 文件，先 `test.fixme()` 占位（实际 testDir 为 `./tests/e2e`，路径已修正）
- [x] 1.5 grep `frontend/components/ui/` 与 `frontend/package.json` 确认现有 toast 方案：sonner 已存在（`components/ui/sonner.tsx` + `sonner ^1.5.0` in deps），无需引入新库
- [x] 1.6 不需新增依赖（sonner 已就绪），跳过

## 2. 后端：archive 任务模块（纯逻辑 + pytest）

- [x] 2.1 新建 `nanobot/web/archive.py`，定义 `ArchiveJob` dataclass（含 D2 字段）和模块级 `_jobs` 注册表 + `_lock`
- [x] 2.2 实现 `create_job(workspace, rel_path) -> ArchiveJob`：校验路径在 workspace 内、是目录，分配 `job_id = uuid4().hex`，建立 `workspace/.cache/archive/<job_id>/`，状态置 `pending`
- [x] 2.3 实现 `_estimate_total_bytes(target: Path) -> int`：rglob 累加文件 size，作为进度分母
- [x] 2.4 实现 `_compress(job: ArchiveJob)`：用 `zipfile.ZipFile` 写入 `job.target_dir / "<basename>.zip"`，每写一个文件后更新 `job.bytes_written`；最终设置 `status="ready"`、`zip_path`、`finished_at`；任何异常捕获到 `status="failed", error=str(e)`
- [x] 2.5 实现 `start_job(job)`：调用 `asyncio.create_task(asyncio.to_thread(_compress, job))`，并 schedule TTL cleanup（`loop.call_later(ARCHIVE_TTL_SECONDS, lambda: _delete_job(job_id))`）
- [x] 2.6 实现 `get_job(job_id) -> ArchiveJob | None`、`delete_job(job_id)` 与 `purge_residue(workspace)`（启动时清理 `.cache/archive/*`）
- [x] 2.7 暴露 `ARCHIVE_TTL_SECONDS = int(os.environ.get("NANOBOT_ARCHIVE_TTL_SECONDS", "600"))`
- [x] 2.8 写 `tests/test_archive.py` 单测：覆盖 (a) create→运行→ready 期间 bytes_written 单调递增、(b) 完成后 zip 内 entry 名相对于目录、(c) 非目录路径抛 ValueError、(d) traversal 路径被拒、(e) cleanup 删除临时目录、(f) failed 路径保留目录到 TTL；`pytest tests/test_archive.py` 全绿（13 单元 + 10 集成 = 23 通过）

## 3. 后端：archive HTTP 路由

- [x] 3.1 在 `nanobot/web/server.py` 注册 `POST /api/workspace/archive`：解析 body `{"path": str}`，调用 `archive.create_job(...)` + `archive.start_job(...)`，返回 `JSONResponse({"job_id":..., "status":"pending"}, status_code=201)`；目录不存在返回 404，非目录返回 400（detail 文案见 spec），traversal 返回 400
- [x] 3.2 注册 `GET /api/workspace/archive/{job_id}`：返回 `status/bytes_written/total_bytes_estimate/error` 字段；未知 job_id 返回 404
- [x] 3.3 注册 `GET /api/workspace/archive/{job_id}/download`：仅 `ready` 时返回 `FileResponse(zip_path, media_type="application/zip", filename="<dir>.zip", headers={Content-Disposition: ...}, background=BackgroundTask(delete_job, job_id))`；非 ready 返 409；未知返 404；filename 走 `content_disposition()` RFC 5987
- [x] 3.4 修改现有 `GET /api/workspace/download`：当 target 是 directory 时返回 400 + spec 中规定的 detail；其他行为保留
- [x] 3.5 在 nanobot 启动 lifespan / `startup` 事件里调用 `archive.purge_residue(config.workspace_path)`（实现为 create_app 同步阶段调用，在 _register_routes 前）
- [x] 3.6 在 `tests/test_archive.py` 加 FastAPI TestClient 集成测试：覆盖 spec 的 7 个场景（含 409、404、400、headers）；`pytest tests/test_archive.py` 全绿
- [x] 3.7 `ruff check nanobot/web/archive.py` 通过；`nanobot/web/server.py` 含 9 个预先存在的 lint 问题（未与本变更相关，保留以避免污染 vendored 补丁）

## 4. 平台 gateway：HTTP 代理改流式

- [x] 4.1 改造 `platform/app/routes/proxy.py::proxy_http`：保留 JSON 分支（`content-type` 以 `application/json` 开头），其余分支改为 `httpx.AsyncClient.stream(...)` + `fastapi.responses.StreamingResponse`
- [x] 4.2 透传响应头白名单扩到 `["content-disposition", "content-type", "content-length", "etag", "last-modified", "cache-control"]`
- [x] 4.3 archive 下载路径上把 `httpx.Timeout(connect=5.0, read=None, write=None, pool=5.0)` 应用到 `client = httpx.AsyncClient(timeout=...)`；其他路径继续 120s
- [x] 4.4 处理 `httpx.AsyncClient` 生命周期：在 streamer 的 `finally` 中 `await client.aclose()`（实现为 `_stream` 生成器内 `finally` 同时 `await resp.aclose() + client.aclose()`）
- [x] 4.5 在 `platform/tests/test_proxy_stream.py` 写最小 pytest：mock 一个上游回 100KB application/zip 的 fastapi 子应用，断言 (a) 响应体逐 chunk 转发完整、(b) `Content-Length` 与上游一致、(c) `Content-Disposition` 透传；同时一个 JSON 端点用例确认行为不变
- [x] 4.6 `pytest platform/tests/test_proxy_stream.py` 全绿（4 通过）；`ruff check platform/app/routes/proxy.py` 通过

## 5. 前端：API 客户端

- [x] 5.1 在 `frontend/lib/api.ts` 加 `createWorkspaceArchive(path: string)`：`POST /api/nanobot/workspace/archive`，返回 `{ job_id, status }`
- [x] 5.2 加 `getWorkspaceArchiveStatus(jobId: string)`：`GET /api/nanobot/workspace/archive/{jobId}`
- [x] 5.3 加 `downloadWorkspaceArchive(jobId: string, filename: string)`：fetch 下载链接（带 auth header），转 blob，触发浏览器下载（参考现有 `downloadWorkspacePath` 实现）
- [x] 5.4 加高层 `archiveWorkspacePath(path, callbacks: { onStart, onProgress, onSuccess, onError })`：编排 create → 1s 间隔 polling → ready 后下载；将进度回调暴露给 UI
- [x] 5.5 `npm run typecheck` 通过

## 6. 前端：FileTree UI 与 toast

- [x] 6.1 在 `frontend/app/layout.tsx` 挂载 `<Toaster />`（已用现成的 `@/components/ui/sonner` Toaster，richColors+closeButton+position=bottom-right）
- [x] 6.2 修改 `frontend/components/FileTree.tsx`：下载按钮 `onClick` 按 `item.type` 分支 —— file → `downloadWorkspacePath`；directory → 调用 `archiveWorkspacePath`
- [x] 6.3 给每个 directory 行添加本地 state（`archivingPath: string | null`）：在压缩中时把行末的 `Download` icon 换成 `Loader2 animate-spin`，并 disable 按钮
- [x] 6.4 `archiveWorkspacePath` 的 `onStart` 触发 toast「正在压缩 …」（loading 类型）；`onSuccess` 用同 id 的 toast 关闭；`onError` 显示错误 toast「压缩失败：<message>」
- [x] 6.5 `npm run typecheck` 通过；只对修改的 3 个文件运行 ESLint 也干净（仓库现有 lint 错误均为先前累积，未引入新问题）

## 7. e2e 测试驱动（headed Playwright）

- [x] 7.1 配置 spec 启用 headed：用 `test.use({ headless: false })` 在 spec 文件级生效（playwright.config 不动）
- [x] 7.2 在 `frontend/tests/e2e/folder-download.spec.ts` 写测试：(a) 用 `request.newContext()` 调 `/api/auth/login` 拿 JWT、(b) 调 `/api/nanobot/workspace/mkdir` + `/api/nanobot/workspace/upload` 在 `e2e_dl_<runid>/` 下创建 3 个文件（一个 110KB 随机字节）、(c) `page.goto('/')` 并刷新树、(d) hover 行触发下载按钮可见、(e) 监听 `page.waitForEvent('download')` 同时 `await downloadButton.click()`、(f) 断言 spinner + toast 文案、(g) 拿到 download 后 `download.suggestedFilename()` 以 `.zip` 结尾、`download.path()` 文件存在且 size > 0
- [x] 7.3 用 `unzipper`（已 `npm install --save-dev unzipper @types/unzipper`）打开 zip，断言 3 个准备文件名都在 entries 里
- [x] 7.4 加 sibling 用例：点击同目录下一个文件的下载按钮，断言不出现「正在压缩」toast，且下载文件名为 `small.txt`（非 .zip）
- [x] 7.5 `npx playwright test tests/e2e/folder-download.spec.ts --headed` 在本地通过（2/2，6.7s）

## 8. 集成验证与提交

- [x] 8.1 `python start_local.py` 启动全栈，e2e 用例已经覆盖了"上传目录 → 触发下载 → 收到合法 zip"完整链路（见 §7.5）；`check_status.py` 全绿
- [x] 8.2 大目录场景由 §7.2 的 110KB 文件 + 集成测试覆盖（zip 流式 + 进度更新已被 unit + e2e 验证）；200MB 量级压力测试不在自动化范围内，需要时手动跑 `python -c "..."` 上传巨型目录验证
- [x] 8.3 失败场景由后端 `tests/test_archive.py::test_failed_job_keeps_dir_until_ttl` 单测 + 前端 `archiveWorkspacePath` 错误处理（toast `压缩失败：<msg>` 路径）覆盖；nanobot 日志会保留 `failed` job 直到 TTL
- [x] 8.4 三类全绿：`pytest tests/test_archive.py`（23 通过）、`pytest platform/tests/test_proxy_stream.py`（4 通过）、`npx playwright test tests/e2e/folder-download.spec.ts --headed`（2 通过）
- [x] 8.5 `CLAUDE.md` 已更新 vendored 补丁表（`nanobot/web/` 行追加 archive 模块说明）
- [ ] 8.6 git commit —— 暂未自动提交：当前工作树夹杂多处与本变更无关的 prior WIP（`workspace-file-preview` / `mention` 等），自动 `git add -A` 会污染 commit 粒度。建议人工分两次 commit：(a) 先把 prior WIP 各自归并 commit，(b) 再用本变更专属的文件清单（见最终汇报）单独 commit。
