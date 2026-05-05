## Context

工作区 `FileTree` 现有目录下载按钮，调用链路是 `frontend → /api/nanobot/workspace/download → gateway proxy → nanobot web → zipfile.write(BytesIO)`。三个层级有问题：

1. **nanobot 层** (`nanobot/web/server.py:909-925`)：在 FastAPI 协程内同步遍历目录、`zipfile.ZIP_DEFLATED` 压缩进 `io.BytesIO`，再 `Response(content=buf.getvalue())`。同步 I/O 在 event loop 线程上执行 → 单核阻塞，大目录直接卡死整个 nanobot web。
2. **gateway 层** (`platform/app/routes/proxy.py:64-93`)：`httpx.AsyncClient(timeout=120.0)` + `client.request(..., content=body)` 把上游 zip 完整读入 `resp.content`，再用 `Response(content=resp.content, ...)` 转出；只透传 `Content-Disposition`，丢弃 `Content-Length`/`Content-Type`。结果：(a) 大目录命中 120s 超时；(b) 浏览器没有 `Content-Length`，进度条/blob 解析行为不稳。
3. **前端层** (`frontend/components/FileTree.tsx:328`)：`downloadWorkspacePath(path).catch(() => {})` 没有 loading、没有错误提示。

约束：
- 多租户部署，gateway 的代理路径是必经之路，不能绕过；用户容器在 `nanobot-internal` Docker 网络内，gateway 是唯一出口。
- nanobot 是 vendored 上游分叉，需要保持上游补丁清单（CLAUDE.md "Vendored nanobot 补丁"）尽量小 —— 优先在已有"web 通道补丁"目录里追加。
- 没有共享存储，每个用户容器有自己的 `workspace_path`。job 状态可以在进程内内存里。
- 前端用 Next.js 13 + Tailwind + shadcn/ui，已经有 `Loader2`、`lucide-react`、`Toast`/`Sonner` 之一可用（实现时先 grep 确认现成的 toast 库再决定）。

## Goals / Non-Goals

**Goals:**
- 目录下载在大小为 200MB / 5000 文件量级时能稳定完成，不卡死 event loop。
- 前端在压缩期间有可见的 loading 反馈（按钮 spinner + 全局 toast）。
- 网关代理不再因大响应体超时；保留正确的 `Content-Length` / `Content-Type` / `Content-Disposition`。
- 单文件下载链路保持向后兼容、行为不变。
- e2e（有头 Playwright）覆盖目录下载完整路径，作为本变更的合并门。

**Non-Goals:**
- 真正的压缩进度百分比（用 `bytes_written / total_bytes_estimate` 已够；不要求字节精确）。
- 跨进程持久化任务（重启即丢，对短生命周期容器可接受）。
- 分卷压缩、断点续传、tar.gz/7z 等其他格式。
- 将 archive 任务调度成独立 worker 进程或队列服务。
- 单文件下载链路的任何重构。

## Decisions

### D1: 异步任务模型 vs 流式 zip

选 **"异步任务（job）+ 临时文件 + 完成后流式下载"**，不选"流式生成 zip 边压边吐"。

理由：
- FastAPI `StreamingResponse` 配合 `zipfile` 边压边写 chunk 在网关代理路径上很难实现 —— `httpx.stream()` 客户端 + `StreamingResponse` 双向可以做，但中间任何一段缓冲（gateway、nginx、CDN）都会让"前端实时看到进度"的承诺破灭，而且无法回退（一旦开始流就不能改 status code）。
- 用 job + 轮询的状态机模型显式：`pending → running → ready/failed`，前端能清晰展示 loading，错误能用普通 4xx/5xx 状态码返回。
- 临时文件落在 `workspace/.cache/archive/<job_id>/` 之下，已经在 workspace 卷里，不需要额外 mount。

替代方案：
- **`StreamingResponse(zipfile.ZipFile(...))`**：前述代理缓冲风险；并且单文件 vs 目录两条路径耦合。
- **同步路由 + `run_in_threadpool`**：能避免阻塞 event loop，但仍需把整个 zip 读入内存，且 gateway 一侧 120s 超时未解决。
- **直接写 zip 到 streaming 响应 + `Transfer-Encoding: chunked`**：浏览器没有 Content-Length，loading 提示变得不准；网关如果中断，前端只能拿到半个 zip。

### D2: Job 注册表存放位置

选 **"进程内 dict + asyncio.Lock + 后台 task"**，不选 Redis/SQLite。

理由：
- 单容器单进程，job 不需要跨进程共享。
- 任务生命周期短（默认 600s 后清理），重启即丢可接受。
- 项目目前无 Redis 依赖，加一个会破坏 `start_local.py` 的零依赖体验。

数据结构：
```python
@dataclass
class ArchiveJob:
    job_id: str
    rel_path: str
    target_dir: Path  # 临时目录
    zip_path: Path | None  # 完成后填充
    status: Literal["pending", "running", "ready", "failed"]
    bytes_written: int
    total_bytes_estimate: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None
```

注册表：模块级 `_jobs: dict[str, ArchiveJob]`，由 `_lock: asyncio.Lock` 保护写入，读不加锁（值都是不可变完成态时单写多读 + GIL 保证）。

### D3: 压缩在哪里跑

选 **`asyncio.to_thread()` + `concurrent.futures.ThreadPoolExecutor`（默认）**。

理由：
- `zipfile` 是 CPython 同步 API，必须放线程池。`asyncio.to_thread` 是 stdlib 标准方式。
- 不需要 ProcessPoolExecutor —— GIL 在压缩这种 zlib 调用里会被释放。
- 不绑定到 FastAPI lifespan：任务在请求返回后继续跑，由后台 task 推进状态。

### D4: 网关代理改流式

把 `proxy.py` 的 `httpx.AsyncClient.request` 改成 `httpx.AsyncClient.stream`，配合 `fastapi.responses.StreamingResponse`。

关键细节：
- 客户端用 `async with client.stream(...)` 进入 context，`StreamingResponse` 的生成器在 yield chunk 同时还要保持 client/响应对象存活 → 要把 client 创建移出 `async with`，改用手动 `client.aclose()` 在 streamer 结束时调用，或者用 `lifespan="manual"` + 单例 client。
- 透传响应头白名单从 `["content-disposition"]` 扩到 `["content-disposition", "content-type", "content-length", "etag", "last-modified", "cache-control"]`。
- JSON 响应继续按现状反序列化（保持现有行为，避免影响其他端点）。判断条件：`content-type` 以 `application/json` 开头时走旧路径；其余流式。
- archive 下载路径上把 httpx timeout 调成 `httpx.Timeout(connect=5.0, read=None, write=None, pool=5.0)`（read 取消上限）；其他路径仍用 120s。

替代方案：
- **保留同步 + 提高 timeout**：只是把问题推迟，仍会爆内存。
- **WebSocket 代替 HTTP 下载**：浏览器无原生支持，要 service worker 把流转回成下载 → 复杂度爆炸。

### D5: 前端 UX

- `FileTree` 下载按钮的 `onClick` 根据 `item.type` 分支：file → `downloadWorkspacePath`（不变）；directory → `archiveWorkspacePath`。
- `archiveWorkspacePath(path)` 内部：(a) 显示 toast「正在压缩 …」、(b) 把按钮 icon 换成 `Loader2 animate-spin`、(c) 创建 job、(d) 1s 间隔轮询 status、(e) ready 后调用下载链接拿 blob、(f) 触发浏览器下载、(g) 关闭 toast。
- 失败：toast 改成「压缩失败：<error>」，spinner 复原。
- toast 组件：先 grep `frontend/components/ui` 看有没有现成的；没有就用 `sonner`（已在 shadcn 生态里），按最小化原则不引入新交互库。

### D6: 路由路径

走 `/api/workspace/archive*` 而不是改 `/api/workspace/download`：
- 区分语义（archive 是带状态机的任务，download 是即时返回）。
- 旧客户端缓存或 mention 链接里的 `download?path=...` 不至于失效（对文件仍然 OK；目录会回 400 引导）。

### D7: 临时文件清理

三层兜底：
1. 任务进入 `ready` 后启动一个 `asyncio.call_later(ARCHIVE_TTL_SECONDS, _cleanup)`（默认 600s）。
2. 下载请求结束时，在 `BackgroundTask` 里立即清理（`FileResponse(..., background=BackgroundTask(_cleanup))`）。
3. 进程启动时扫一遍 `workspace/.cache/archive/`，把所有目录删除（防止上次崩溃残留）。

`failed` 状态保留临时目录直到 TTL，便于排查；error 信息记录在 job 里。

## Risks / Trade-offs

- **大目录磁盘占用** → 临时 zip 落到 `workspace/.cache/archive/`，可能让 workspace 卷在一段时间内多占 N×（zip 体积）。Mitigation：TTL 600s + 下载后立即清理；后续可以加 quota 检查。
- **轮询频率** → 1s 轮询在压缩超过 30s 时显得空转。Mitigation：MVP 阶段先 1s；如有体感问题，下个迭代换 SSE 或 WebSocket。
- **内存峰值仍然存在** → `zipfile` 单文件读入仍在内存。Mitigation：默认 `ZIP_DEFLATED` + `chunked write`（zipfile 已经按需流式写），不会全量加载，只是单个最大文件大小为下界。
- **Job ID 泄露/越权** → job 仅与发起容器绑定（容器=用户），ID 用 `uuid.uuid4().hex`，gateway 的 user 维度隔离已经覆盖。Mitigation：路由内校验 `job.rel_path` 仍能解析在 `workspace_path` 下（双重防御）。
- **httpx `client.stream` + StreamingResponse 生命周期** → 实现时容易写错让 client 提前关闭导致 `RemoteProtocolError`。Mitigation：用 `httpx.AsyncClient` per-request + 在 streamer 的 `finally` 里 `aclose`；写一个最小复现的单测验证多 chunk 透传。
- **e2e 在 CI 里 headed 失败** → headed 需要显示器/Xvfb。Mitigation：本仓库定位是个人/小团队部署，按用户要求 e2e 跑 headed 模式，本地通过即可作为合并门；CI 按需开启时可加 Xvfb（不在本变更范围）。
- **代理改流式后影响其他端点** → JSON/小响应行为应保持不变。Mitigation：用 content-type 分支 + 加 ≥1 个 JSON 端点的 e2e 防回归（命中 `/api/sessions` 列表请求即可）。

## Migration Plan

部署顺序：
1. 后端 + 网关同时部署（archive 路由 + 流式 proxy 是绑定的；流式 proxy 单独部署不会破坏旧 download 路径，所以可以先发）。
2. 前端 deploy 后开始走新路径。中间窗口里旧前端继续打 `/api/workspace/download`，对目录会 400 → 用户看到错误（与现状的"卡死"相比是改进）。

回滚：
- 回滚仅前端：用户回到当前 broken 状态（与 main 一致），无新增风险。
- 回滚后端：archive 路由消失（404），前端轮询失败 → toast 报错；不会污染 workspace 数据，临时目录会留存到 TTL 后被新代码清理（或手动 `rm -rf workspace/.cache/archive`）。

## Open Questions

- 是否需要让 `nanobot agent` CLI 用户也能触发 archive？现阶段答案为否（CLI 单用户场景下 `tar -czf` 一行命令更顺手）。如未来要做，把 archive 模块的接口暴露成 nanobot tool。
- TTL 600s 是否合理？现阶段直觉值，等真实使用后调；写成 env var `NANOBOT_ARCHIVE_TTL_SECONDS` 方便调。
