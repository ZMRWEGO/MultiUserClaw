# 工作区文件实时预览技术方案

## 1. 背景与目标

### 问题
当前 `/files` 页面只能浏览/上传/下载工作区文件，存在两个体验缺口：

1. **不能预览**：用户必须下载文件到本地才能查看内容，效率低，尤其是 AI 生成的报告、Markdown、代码、图片等。
2. **不能感知改动**：当 nanobot agent 通过 `write_file`/`edit_file` 修改了工作区文件，前端列表/预览不会更新，必须手动刷新页面。

### 目标
- 在 `/files` 页面提供**双栏布局**：左侧文件树 + 右侧内嵌预览。
- 支持的预览格式（**混合渲染策略**）：
  - **文本类**（前端）：代码（py/ts/js/go/rs/java/c/cpp/...）、txt、yaml、toml、log、env
  - **结构化文本**（前端）：JSON、Markdown、HTML
  - **图片**（前端）：png、jpg、jpeg、gif、webp、svg、bmp、ico
  - **PDF**（浏览器原生）
  - **Word `.docx`**（前端 `docx-preview` JS 库）
  - **Excel `.xlsx`**（前端 `xlsx` 库 + `react-window` 虚拟滚动表格）
  - **PowerPoint `.pptx`**（**后端 LibreOffice → HTML**，缓存到 workspace 之外）
- **不预览，仅下载**：
  - 老二进制 Office 格式（.doc/.xls/.ppt） — 前端无法解析
  - 其他 OpenDocument（.odt/.ods/.odp）、RTF — 暂不支持
  - 压缩包、二进制可执行
- **实时同步**：基于 watchdog + WebSocket
  - AI 写文件 / 用户上传 → 文件树自动刷新
  - 当前预览的文件被改动 → 内容自动重载（包括 .pptx 重新转换）
  - 文件被删除 → 预览区显示提示

---

## 2. 术语定义

| 术语 | 含义 |
|------|------|
| **WorkspaceWatcher** | 监听工作区目录变化的服务（基于 watchdog） |
| **OfficeConverter** | 用 LibreOffice/unoserver 把 .pptx 转 HTML 的后端服务 |
| **预览缓存目录** | `/var/cache/nanobot/preview/`，**位于 workspace 外**，用户与 AI 都不可感知 |
| **路径映射** | 前端始终用 workspace 相对路径（`docs/slide.pptx`）；后端按 hash 解析为缓存文件，前端永远不见缓存路径 |
| **预览类型（preview_kind）** | 后端识别出的文件呈现方式：`text`/`markdown`/`json`/`image`/`pdf`/`html`/`docx`/`xlsx`/`pptx`/`binary` |
| **WS /ws/files** | 文件事件推送通道，与 chat 的 `/ws/{session_id}` 独立 |
| **防抖（debounce）** | 同一文件 100ms 内多次修改合并为一个事件 |

---

## 3. 整体架构

```
                    ┌─────────────────────┐
                    │   Browser (/files)  │
                    │ ┌─────┬───────────┐ │
                    │ │Tree │ Preview   │ │
                    │ └─────┴───────────┘ │
                    └──┬───────┬──────────┘
                       │       │
                  HTTP │       │ WebSocket
        (browse/preview/render) (file_event)
                       ▼       ▼
              ┌──────────────────────────┐
              │  Platform Gateway        │
              │  /api/nanobot/...        │
              │  /api/nanobot/ws/files   │
              └──────────┬───────────────┘
                         │ reverse proxy
                         ▼
              ┌─────────────────────────────────────────────┐
              │   User's nanobot 容器                        │
              │                                              │
              │  FastAPI (nanobot/web)                       │
              │  ├ /api/workspace/browse                     │
              │  ├ /api/workspace/preview      ← NEW         │
              │  ├ /api/workspace/download                   │
              │  ├ /api/workspace/render       ← NEW (.pptx)│
              │  └ /ws/files                    ← NEW         │
              │           │                                  │
              │           ▼                                  │
              │  WorkspaceWatcher (inotify, debounce)        │
              │           │                                  │
              │  OfficeConverter (.pptx → HTML)  ← NEW       │
              │   └─ unoserver (常驻 LibreOffice 进程)       │
              │                                              │
              │  ┌──────── 用户视角(可见) ────────┐           │
              │  │ /workspace/                   │           │
              │  │   ├ docs/                     │           │
              │  │   │  └ slide.pptx             │           │
              │  │   └ ...                       │           │
              │  └────────────────────────────────┘           │
              │                                              │
              │  ┌─── 用户与 AI 不可见(隐藏) ────┐            │
              │  │ /var/cache/nanobot/preview/   │           │
              │  │   ├ <hash1>.html              │           │
              │  │   └ <hash2>.html              │           │
              │  └────────────────────────────────┘           │
              └─────────────────────────────────────────────┘
```

**关键约束**：

- 用户在文件树中只看到 `/workspace/` 下的文件，转换产物对用户和 AI（`list_dir`/`read_file` 等工具）完全隐藏。
- 前端访问 `.pptx` 走 `/api/workspace/render?path=docs/slide.pptx` —— **路径仍是用户视角**，由后端做映射到缓存文件。

---

## 4. 后端设计

### 4.1 预览类型识别（`nanobot/web/preview.py`，新增）

入口函数：

```python
def preview_kind_for(path: Path) -> tuple[str, str | None]:
    """
    返回 (preview_kind, language)。
    language 仅当 preview_kind=text 时有意义，用于前端语法高亮提示。
    """
```

识别规则（按扩展名优先，然后 mimetype 兜底）：

| 扩展名 | preview_kind | language |
|--------|--------------|----------|
| `.md`, `.markdown` | `markdown` | — |
| `.json` | `json` | — |
| `.html`, `.htm` | `html` | — |
| `.pdf` | `pdf` | — |
| `.png`/`.jpg`/`.jpeg`/`.gif`/`.webp`/`.svg`/`.bmp`/`.ico` | `image` | — |
| `.py`/`.ts`/`.tsx`/`.js`/`.jsx`/`.go`/`.rs`/`.java`/`.c`/`.cpp`/`.h`/`.css`/`.scss`/`.yaml`/`.yml`/`.toml`/`.sh`/`.sql`/`.xml`/`.dockerfile`/`.env` | `text` | 对应语言 |
| `.txt`/`.log`/`.csv`/无扩展 | `text` | `plain` |
| `.docx` | `docx` | — |
| `.xlsx` | `xlsx` | — |
| `.pptx` | `pptx` | — |
| `.doc`/`.xls`/`.ppt`/`.odt`/`.ods`/`.odp`/`.rtf` | `binary` | — |
| `.zip`/`.tar`/`.gz`/`.7z`/`.rar` | `binary` | — |
| 其他（无法识别） | `binary` | — |

无扩展名文件：尝试以 UTF-8 读取前 4KB；若可解码且无大量不可打印字符则视为 `text`，否则 `binary`。

### 4.2 预览 API：`GET /api/workspace/preview`

**入参**：`path` (string，相对工作区)

**出参**：

```typescript
type PreviewResponse =
  | { kind: 'text'; language: string; content: string; size: number; truncated: boolean; modified: string }
  | { kind: 'markdown'; content: string; size: number; modified: string }
  | { kind: 'json'; content: string; size: number; modified: string }    // 已 pretty-print
  | { kind: 'html'; download_url: string; size: number; modified: string }   // 前端用 iframe 加载 download_url
  | { kind: 'image'; download_url: string; content_type: string; size: number; modified: string }
  | { kind: 'pdf'; download_url: string; size: number; modified: string }
  | { kind: 'docx'; download_url: string; size: number; modified: string }   // 前端用 docx-preview 拉 blob 渲染
  | { kind: 'xlsx'; download_url: string; size: number; modified: string }   // 前端用 xlsx + react-window 渲染
  | { kind: 'pptx'; render_url: string; size: number; modified: string; converted: boolean }  // 后端 LibreOffice 转 HTML,前端 iframe
  | { kind: 'binary'; reason: 'office_legacy' | 'archive' | 'unknown'; size: number; modified: string; download_url: string }
```

> **路径映射**：`render_url` 对 .pptx 形如 `/api/workspace/render?path=docs/slide.pptx` —— **路径仍是 workspace 相对路径**。后端在 render 端点内部做 `path → cache_file` 的映射，前端永远拿不到缓存路径。

**关键约束**：

- `path` 必须落在 workspace 内（复用 `_resolve_workspace_path`，防穿越）
- 文本类（`text`/`markdown`/`json`）硬限制 **5MB**：超过则
  - `text`/`markdown`：返回前 5MB + `truncated: true`
  - `json`：直接返回 `binary`，提示用户下载
- 图片不在后端读字节：直接返回 `download_url`，由前端 `<img>` 拉
- PDF/HTML 同理：返回 `download_url` 让浏览器原生处理

### 4.3 OfficeConverter（`nanobot/web/converter.py`，新增）

负责把 `.pptx` 转换成 HTML 并缓存。Word/Excel 都走前端，**只有 .pptx 走这条路径**。

```python
class OfficeConverter:
    """LibreOffice headless 转 HTML,带缓存,缓存目录在 workspace 之外"""

    SUPPORTED_EXTS = {".pptx"}                 # 第一阶段只做 pptx
    CACHE_ROOT = Path("/var/cache/nanobot/preview")  # 容器内,workspace 外
    UNOSERVER_PORT = 2003
    READY_TIMEOUT = 30                          # 首次等待 unoserver 就绪的最长秒数

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()            # 串行化,避免 LO 并发崩
        self._ready_event = asyncio.Event()    # unoserver 就绪信号

    @classmethod
    def can_convert(cls, path: Path) -> bool:
        return path.suffix.lower() in cls.SUPPORTED_EXTS

    def cache_key(self, src: Path) -> str:
        """根据 (workspace 相对路径 + mtime + size) 计算 cache key"""
        rel = src.resolve().relative_to(self.workspace)
        stat = src.stat()
        raw = f"{rel}|{stat.st_mtime_ns}|{stat.st_size}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def cache_path(self, src: Path) -> Path:
        return self.CACHE_ROOT / f"{self.cache_key(src)}.html"

    # ---- 启动期就绪探测(WebChannel 启动后 fire-and-forget 调用) ----
    async def start_readiness_probe(self) -> None:
        """容器启动后异步监听 unoserver 端口;就绪时 set event"""
        async def _probe():
            deadline = time.monotonic() + self.READY_TIMEOUT
            while time.monotonic() < deadline:
                if await self._port_open(self.UNOSERVER_PORT):
                    self._ready_event.set()
                    logger.info("unoserver ready on port %d", self.UNOSERVER_PORT)
                    return
                await asyncio.sleep(0.5)
            logger.warning("unoserver not ready within %ds — first .pptx render may fail",
                           self.READY_TIMEOUT)
        asyncio.create_task(_probe())   # fire-and-forget,不阻塞

    async def _ensure_ready(self) -> None:
        """转换前确保 unoserver 已就绪;通常立即返回(已 set);冷启动期等待"""
        if self._ready_event.is_set():
            return
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=self.READY_TIMEOUT)
        except asyncio.TimeoutError:
            raise RuntimeError("unoserver not ready")

    @staticmethod
    async def _port_open(port: int) -> bool:
        try:
            _, w = await asyncio.open_connection("127.0.0.1", port)
            w.close(); await w.wait_closed()
            return True
        except OSError:
            return False

    # ---- 主流程 ----
    async def to_html(self, src: Path) -> Path:
        """返回缓存好的 HTML 路径;按需触发转换"""
        target = self.cache_path(src)
        if target.exists():
            return target
        async with self._lock:
            if target.exists():           # 双检
                return target
            await self._ensure_ready()    # 等 unoserver 就绪(通常 0ms)
            await self._convert(src, target)
            return target

    async def _convert(self, src: Path, dst: Path) -> None:
        # unoconvert + unoserver(常驻 LibreOffice)
        proc = await asyncio.create_subprocess_exec(
            "unoconvert", "--port", str(self.UNOSERVER_PORT),
            "--convert-to", "html", str(src), str(dst),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("convert timeout")
        if proc.returncode != 0 or not dst.exists():
            raise RuntimeError(f"convert failed: rc={proc.returncode}")
```

**缓存目录设计（核心）**：

```
容器目录布局
─────────────
/workspace/                  ← 用户工作区(挂 docker volume,用户/AI 可见)
  └ docs/slide.pptx

/var/cache/nanobot/preview/  ← 缓存目录(容器临时层,用户/AI 不可见)
  ├ a3f9c2e8d1b04567.html    ← 由 (workspace 相对路径 + mtime + size) 哈希得到
  └ b7e1d3a9f8c12340.html
```

| 属性 | 设计 |
|------|------|
| 位置 | **容器内 `/var/cache/nanobot/preview/`** —— 不在 workspace volume 内 |
| 用户可见性 | `browse` API 只列 workspace 内容；`list_dir` 工具被 `restrict_to_workspace` 限制；缓存对用户和 AI 完全透明 |
| watcher 监听 | 不监听缓存目录（它本来就不在 workspace 内，不会触发事件） |
| 跨容器 | 容器销毁则缓存丢失，重建时重新转换。**不持久化**，简单优先 |
| 命中条件 | `(workspace 相对路径, mtime, size)` 完全一致才命中；文件被改动 mtime 变化，自然 miss → 重转 |

**清理策略**：

- **被动清理**：watcher 发现 .pptx 被删/改名时，调用 `converter.cleanup_for(path)` 删掉对应 hash 的缓存
- **定时清理**：容器启动时 + 每 24h 扫一次，删除：
  - 超过 7 天未访问（按 atime）的文件
  - 总大小 > 500MB 时按 atime 升序删到 < 250MB

### 4.4 路径映射端点：`GET /api/workspace/render`

专门服务 .pptx 转换后的 HTML，**前端始终用 workspace 路径，不暴露缓存细节**。

```python
@app.get("/api/workspace/render")
async def render_converted(path: str):
    """对 .pptx 等需要转换的文件,返回缓存的 HTML 流"""
    config: Config = app.state.config
    src = workspace_file_path(config.workspace_path, path)
    if src is None:
        raise HTTPException(404, "File not found")

    converter: OfficeConverter = app.state.office_converter
    if not converter.can_convert(src):
        raise HTTPException(400, "File type not supported for rendering")

    html_path = await converter.to_html(src)        # cache hit or convert
    return FileResponse(
        html_path,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
```

**前端调用**：

```typescript
// preview API 返回
{ kind: 'pptx', render_url: '/api/workspace/render?path=docs/slide.pptx', ... }

// 前端 iframe 直接嵌入(经 platform gateway 代理)
<iframe src={`${API_URL}/api/nanobot/workspace/render?path=docs/slide.pptx`} sandbox=""/>
```

> 用户和 AI 任何时候**只见 `docs/slide.pptx`**，看不到 `/var/cache/nanobot/preview/<hash>.html`。

### 4.5 LibreOffice 在镜像中的集成

#### Dockerfile 修改（nanobot 基础镜像）

```dockerfile
# 在现有镜像基础上加
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-core \
        libreoffice-impress \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir unoserver

# 创建缓存目录
RUN mkdir -p /var/cache/nanobot/preview && chmod 755 /var/cache/nanobot/preview
```

> **包选择说明**:
> - 只装 `libreoffice-impress`(PowerPoint 模块) + 共用核心,对比全量 LibreOffice (600MB) 增量约 **280MB**
> - **不装 `libreoffice-java-common`**(节省 ~100MB):unoserver 用 Python UNO 桥(`python3-uno` 由 libreoffice-core 自带),不需要 Java 运行时;仅当用户文档触发 Basic 宏才需要,预览场景不会触发
> - **不装 `netcat-openbsd`**:entrypoint 不再做端口探测(改为 web server 内 `_port_open` 用 asyncio 探测)

#### 容器启动:非阻塞拉起 unoserver

```bash
#!/bin/bash
# entrypoint.sh
unoserver --port 2003 &     # fire-and-forget,后台启动
exec nanobot web "$@"        # 立即拉起 web 服务,不等待 unoserver
```

**核心要点**:
- entrypoint **不等待** unoserver 就绪,容器启动时间 = 现在的启动时间(无回归)
- unoserver 在后台并行启动(典型 8-12s),与 web server 同时进行
- web server 启动后立即接收所有 HTTP/WS 请求(`/preview`、`/browse`、`/ws/files` 等)
- 只有第一次 `.pptx` 转换请求落到 OfficeConverter 时才检查 unoserver 是否就绪(`_ensure_ready` 内做短超时等待,详见 §4.3)
- 普通用户(不打开 .pptx)从未感知 LibreOffice 存在 — 它在后台静默就绪,资源消耗也仅是 LO 守护进程内存(~150MB)

时序示意:

```
T+0ms     entrypoint 启动
T+0ms     unoserver --port 2003 &  (fork,立即返回)
T+0ms     exec nanobot web         (web server 起)
T+200ms   web server listen 0.0.0.0:8080,容器就绪 ← 容器启动时间不变
T+~10s    unoserver 完全就绪(后台,不阻塞任何请求)
T+...     用户请求 /api/preview, /ws/files... 全部正常服务

(若用户在 unoserver 就绪前就点击 .pptx)
T+3s      用户点 docs/slide.pptx → /render → converter._ensure_ready 等待
T+~10s    unoserver 就绪,触发转换
T+~12s    返回首张 HTML(冷启动一次性代价)
```

### 4.6 WorkspaceWatcher（`nanobot/web/watcher.py`，新增）

核心类：

```python
class WorkspaceWatcher:
    def __init__(self, workspace: Path):
        self._workspace = workspace.resolve()
        self._observer: Observer | None = None
        self._listeners: set[asyncio.Queue] = set()  # 每个 WS 一个队列
        self._debounce: dict[str, asyncio.TimerHandle] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def add_listener(self) -> asyncio.Queue: ...
    def remove_listener(self, q: asyncio.Queue) -> None: ...
    def _on_event(self, event: FileSystemEvent) -> None: ...
```

**设计要点**：

- 单例：每个 `nanobot/web` server 进程持有一个 watcher（用户容器内每个用户独立进程）。
- **Lazy 启动**：第一个 WS 连接时启动 Observer，最后一个断开时停止（节省后台 IO）。
- **防抖**：`watchdog` 在文件保存时常常连发 `modified` 多次，对每条事件用 `loop.call_later(0.1, _flush_event, path)` 收敛。
- **过滤目录**：跳过隐藏目录、`.git`/`.next`/`node_modules`/`__pycache__`/`.venv`/`dist`/`build` 等。
- **跨线程通知**：Observer 在自己的线程触发回调；用 `asyncio.run_coroutine_threadsafe` 把事件投递到主事件循环。
- **事件 fanout**：每个连接的 WS 各持有一个 `asyncio.Queue`，事件并发推到所有队列。

事件载荷：

```json
{
  "type": "file_event",
  "event": "created" | "modified" | "deleted" | "moved",
  "path": "docs/TODO",
  "old_path": "docs/OLD",        // 仅 moved 事件
  "is_directory": false,
  "size": 123,
  "modified": "2026-05-02T01:23:45Z"
}
```

### 4.7 WebSocket 端点：`/ws/files`

挂在 `nanobot/web/server.py`：

```python
@app.websocket("/ws/files")
async def workspace_files_ws(websocket: WebSocket):
    await websocket.accept()
    watcher: WorkspaceWatcher = app.state.workspace_watcher
    queue = watcher.add_listener()
    try:
        # 主循环：从 queue 拉事件 → 推给客户端;同时收 ping
        while True:
            done, _ = await asyncio.wait(
                [asyncio.create_task(queue.get()),
                 asyncio.create_task(websocket.receive_text())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            ... # 处理事件 / 处理 ping
    except WebSocketDisconnect:
        pass
    finally:
        watcher.remove_listener(queue)
```

实现细节：

- **鉴权**：单用户模式（直连 nanobot/web）无需鉴权；多用户模式由 platform gateway 在代理时鉴权（见 4.8）。
- **心跳**：客户端每 30s 发 `{"type":"ping"}`，服务端回 `{"type":"pong"}`，与 chat WS 一致。
- **初始快照**：连接建立后服务端立即推一次 `{"type":"snapshot"}`，前端据此决定是否做一次全量刷新。

### 4.8 Platform Gateway 代理（`platform/app/routes/proxy.py`，修改）

现有 WebSocket 代理路由是 `/ws/{session_id}` —— 路径只支持单段。需要扩展：

```python
@router.websocket("/ws/{path:path}")        # path 改为通配
async def proxy_websocket(websocket, path: str, token: str = ""):
    # ...鉴权...
    target_ws_url = f"ws://{container_host}:{container_port}/ws/{path}"
    # ...relay...
```

这样 `/api/nanobot/ws/files` → 容器内 `/ws/files`。

> **兼容性**：原 `chat WS` 路径 `/ws/{session_id}` 自然落入新通配路径，无需前端修改。

### 4.9 集成入口（`nanobot/channels/web.py`，修改）

WebChannel 启动时初始化 watcher、converter 并塞进 app.state：

```python
async def start(self) -> None:
    ...
    workspace = self.full_config.workspace_path
    self._watcher = WorkspaceWatcher(workspace)
    self._converter = OfficeConverter(workspace)

    # 启动后异步监听 unoserver 端口就绪 — 不阻塞 web server
    await self._converter.start_readiness_probe()

    # 删除 .pptx 时自动清理对应缓存
    self._watcher.on_delete(lambda path: self._converter.cleanup_for(path))

    app = create_app(
        ...,
        workspace_watcher=self._watcher,
        office_converter=self._converter,
    )

async def stop(self) -> None:
    ...
    if self._watcher:
        self._watcher.stop()
    # converter 没有需要主动 stop 的资源(unoserver 由容器进程组随容器关闭)
```

---

## 5. 前端设计

### 5.1 页面布局重构（`frontend/app/files/page.tsx`）

使用已存在的 `react-resizable-panels` 实现可拖拽分栏：

```
┌─────────────────────────────────────────────────────────┐
│  文件管理      [刷新] [上传] [新建文件夹]   ● 已连接     │
├──────────────────┬──────────────────────────────────────┤
│ workspace        │ docs/TODO                  [↓ 下载]  │
│ ▼ 📁 docs       │ ┌──────────────────────────────────┐ │
│   📄 TODO       │ │ - [ ] feature 1                  │ │
│   📄 plan.pdf   │ │ - [x] feature 2                  │ │
│ ▶ 📁 src        │ │ ...                              │ │
│   📁 images     │ └──────────────────────────────────┘ │
│   🖼 logo.png   │                                      │
└──────────────────┴──────────────────────────────────────┘
```

- 默认分栏比例 30 : 70，可拖拽。
- 顶部头部保留现有上传/新建/刷新按钮，移除面包屑（已被树替代）。
- 右下角连接状态指示（`useFileEvents` Hook 暴露 ws 状态）。

### 5.2 FileTree 组件（`frontend/components/FileTree.tsx`，新增）

```typescript
interface FileTreeProps {
  rootPath: string;            // 起始目录，默认 ""
  selectedPath: string | null;
  onSelect: (item: WorkspaceItem) => void;
  externalEvents?: FileEvent[]; // 由 useFileEvents 注入
}
```

行为：
- 点击目录展开/折叠（lazy-load 子项 via `/api/workspace/browse?path=...`）。
- 已展开的目录列表保存在组件内 state，刷新时尽量保留展开状态。
- 收到外部 `file_event` 时增量更新：
  - `created` → 父目录已展开就插入节点
  - `deleted` → 移除节点
  - `modified` → 仅更新 mtime/size
  - `moved` → 删除旧节点 + 插入新节点
- 选中文件时高亮 + 滚动可见。
- 右键菜单（暂可省略，第二期）：下载、删除、复制路径。

### 5.3 FilePreview 组件（`frontend/components/FilePreview/`，新增）

入口 `index.tsx`：

```typescript
interface FilePreviewProps {
  path: string | null;
  reloadKey?: number;   // 改动事件触发时 +1，导致重新拉取
}

export function FilePreview({ path, reloadKey }: FilePreviewProps) {
  const { data, error } = usePreview(path, reloadKey);
  if (!path) return <EmptyHint />;
  if (error) return <ErrorHint error={error} />;
  if (!data) return <LoadingHint />;

  switch (data.kind) {
    case 'text':     return <TextPreview {...data} />;
    case 'markdown': return <MarkdownPreview content={data.content} />;
    case 'json':     return <JsonPreview content={data.content} />;
    case 'image':    return <ImagePreview url={data.download_url} />;
    case 'pdf':      return <PdfPreview url={data.download_url} />;
    case 'html':     return <HtmlPreview url={data.download_url} />;
    case 'docx':     return <WordPreview url={data.download_url} reloadKey={reloadKey} />;
    case 'xlsx':     return <ExcelPreview url={data.download_url} reloadKey={reloadKey} />;
    case 'pptx':     return <PptxPreview renderUrl={data.render_url} reloadKey={reloadKey} />;
    case 'binary':   return <BinaryPreview {...data} />;
  }
}
```

各子组件：

| 子组件 | 实现 | 依赖 |
|--------|------|------|
| `TextPreview` | `<pre><code>` + 简单 monospace 显示；可选 syntax highlight（轻量方案：用 `react-markdown` 的 code 块 + `remark-gfm` 内联渲染。重量方案：引入 `highlight.js` 或 `prism-react-renderer`，~30KB） | 已有 |
| `MarkdownPreview` | `react-markdown` + `remark-gfm` | 已有 |
| `JsonPreview` | 简单 `<pre>` 显示 pretty-print 内容；后续可加折叠 | — |
| `ImagePreview` | `<img>` + 缩放（点击放大）；带 Authorization header 的 fetch → blob URL（因为 download_url 需要鉴权） | — |
| `PdfPreview` | 用 `<iframe>` 加载 blob URL（同 image，需要带 token 拉再喂给 iframe） | — |
| `HtmlPreview` | `<iframe sandbox>` 加载 blob URL，sandbox 限制为 `""`（最严格，禁用 JS） | — |
| `WordPreview` | 拉取 .docx blob → `docx-preview` 渲染到 `<div>`；首次加载时显示 loading；reloadKey 变化时重渲染 | **新增** `docx-preview` |
| `ExcelPreview` | 拉取 .xlsx blob → `xlsx` 库解析 → `react-window` 虚拟滚动 + sheet tab 切换；超过 100 万 cells 提示"文件过大,请下载查看" | 已有 `xlsx`，**新增** `react-window` |
| `PptxPreview` | `<iframe sandbox>` 加载 `render_url`（后端返回 LibreOffice 转换后的 HTML）；reloadKey 变化时改 src 强制重载 | — |
| `BinaryPreview` | 显示文件名、大小、MIME，只给"下载"按钮 | — |

> **鉴权流程**：所有 `download_url`（`/api/nanobot/workspace/download`）需要 `Authorization: Bearer ...` Header。前端调用 `fetch + blob → URL.createObjectURL` 得到本地 blob URL，再喂给 `<img>`/`<iframe>`/Word/Excel 渲染器。
>
> `render_url`(`/api/nanobot/workspace/render`)同样需要鉴权,但因为是 iframe src 直接加载,需要在 URL 上携带 token —— 由 platform gateway 接受 `?token=` query 参数(与 WS 一致),代理时转换为 Authorization header 发给容器内端点。

#### 5.3.1 WordPreview 实现要点

```typescript
// frontend/components/FilePreview/WordPreview.tsx
import { renderAsync } from 'docx-preview';

export function WordPreview({ url, reloadKey }: { url: string; reloadKey?: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const blob = await fetchAuthBlob(url);                  // Authorization 拉 blob
      if (cancelled || !containerRef.current) return;
      containerRef.current.innerHTML = '';                    // reloadKey 变化时清空
      await renderAsync(blob, containerRef.current, undefined, {
        className: 'docx-viewer',
        inWrapper: true,
        ignoreWidth: false,
        breakPages: true,
      });
    })();
    return () => { cancelled = true; };
  }, [url, reloadKey]);
  return <div ref={containerRef} className="overflow-auto p-4" />;
}
```

#### 5.3.2 ExcelPreview 实现要点

- 用 `xlsx.read(buffer, { type: 'array' })` 解析,首次加载完成前显示 loading。
- 多 sheet 时显示底部 tab,默认选中第一个。
- 单 sheet 用 `react-window` 的 `FixedSizeGrid` 虚拟化:仅渲染可视范围 cells,可处理 10w+ 行。
- 列宽默认 120px,首行加粗作为表头(若用户 Excel 有表头);更精细的样式不还原(避免膨胀)。
- **大文件保护**:`sheet['!ref']` 解析得到行列数 → `rows * cols > 1_000_000` 时直接显示"文件过大,建议下载本地查看",并给下载按钮。

#### 5.3.3 PptxPreview 实现要点

```typescript
// frontend/components/FilePreview/PptxPreview.tsx
export function PptxPreview({ renderUrl, reloadKey }: { renderUrl: string; reloadKey?: number }) {
  const token = useAuthToken();
  // render_url 已是相对 /api/nanobot/...,token 走 query
  const src = `${renderUrl}${renderUrl.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}&_=${reloadKey ?? 0}`;
  return (
    <iframe
      src={src}
      sandbox=""
      className="w-full h-full border-0"
      title="PowerPoint preview"
    />
  );
}
```

- LibreOffice 转换的 HTML 是单页静态页(所有幻灯片纵向排列),用户滚动浏览。
- 首次访问会触发后端转换(可能 1-3s),iframe 自带 loading,前端额外显示 loading hint(可选)。
- `reloadKey` 变化(.pptx 被修改) → URL 上 `_=${reloadKey}` 改变 → iframe 重载 → 后端因 mtime 变化命中新 cache key → 自动重转。

### 5.4 useFileEvents Hook（`frontend/hooks/useFileEvents.ts`，新增）

```typescript
export function useFileEvents(): {
  status: WsStatus;
  events: FileEvent[];        // 最近事件流（全量）
  lastEventForPath: (path: string) => number | null; // ts of last event
}
```

实现：
- 使用与 `wsManager` 相同的 reconnect / ping 模式（指数退避 1s→30s）。
- 连接 URL：`${API_URL}/api/nanobot/ws/files?token=...`。
- 事件以滚动窗口（保留最近 200 条）存入 React state。
- 提供 `lastEventForPath(path)` 工具：用于让 `FilePreview` 通过 `reloadKey` 检测当前预览的文件是否被改动。

### 5.5 API Client 扩展（`frontend/lib/api.ts`，修改）

```typescript
export interface PreviewResult { /* 与后端响应同构 */ }

export async function getWorkspacePreview(path: string): Promise<PreviewResult> {
  return fetchJSON(`/api/nanobot/workspace/preview?path=${encodeURIComponent(path)}`);
}

export async function fetchWorkspaceBlobUrl(path: string): Promise<string> {
  // 用于 image/pdf/html 预览：带 token 拉成 blob URL
  const url = getWorkspaceDownloadUrl(path);
  const token = getAccessToken();
  const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}
```

> 调用方需要在 unmount 时 `URL.revokeObjectURL` 释放。

---

## 6. 安全 / 边界

| 风险 | 对策 |
|------|------|
| 路径穿越 | 复用 `_resolve_workspace_path`；preview/render API 在文件不在 workspace 内时返回 400 |
| 大文件预览拖垮服务器 | 文本类硬限制 5MB；超过则截断或拒绝；二进制不读字节;Excel cells > 1M 直接拒绝渲染 |
| 恶意 HTML / .pptx 通过 iframe 盗 token / 跨域请求 | iframe `sandbox=""`（最严格，禁用 JS、表单、popup、跨源） + render 端点设 `CSP: default-src 'self'; script-src 'none'; sandbox` |
| SVG 内嵌脚本 | SVG 走 `<img>` 而不是 `<iframe>`（`<img>` 不执行脚本） |
| WS 鉴权 | 多用户模式由 platform gateway 验证 token，未验证不打开上游连接;render 端点 token 经 query 进入 platform 后转 Bearer |
| Watcher 资源泄漏 | start/stop 严格配对；最后一个 listener 移除时回收 Observer |
| 监听放大效应（如 git pull 一次几千文件） | 100ms 防抖；前端事件流上限 200 条 |
| 用户重连漏事件 | 重连时前端触发一次根目录 browse，强制对齐状态 |
| **LibreOffice 转换被恶意 .pptx 卡死** | `unoconvert` 60s 超时,超时则 kill 子进程并返回 502;render 端点对同一 path 失败 5 次内拒绝重试 |
| **LibreOffice 命令注入** | `unoconvert` 走 `subprocess_exec`(数组传参,非 shell);路径来自 `_resolve_workspace_path` 已校验 |
| **缓存目录撑爆磁盘** | 总大小 500MB 上限;启动时 + 每 24h 清理超 7 天 atime 文件;命中 LRU 删到 250MB |
| **缓存目录被 AI/用户感知** | 缓存路径在 `/var/cache/nanobot/preview/`,**不在** workspace volume 内;`browse` 与 `list_dir` 工具均限制于 workspace,完全屏蔽 |
| **字体缺失导致 .pptx 中文乱码** | 镜像安装 `fonts-noto-cjk`;若用户文档用了 Win 私有字体,按 Noto fallback,接受展示偏差(下载查看为兜底) |
| **unoserver 进程僵死** | converter 内置 `_port_open` + 30s 就绪超时探测;运行期 healthcheck 由容器层 supervisor 守护(后续阶段);`_convert` 失败回收并清缓存 |

---

## 7. 性能

- watchdog 在 Linux 容器内使用 inotify，几千个文件无压力（每文件 ~1KB 内核内存）。
- 单容器单 watcher 单 Observer 线程；多 WS 连接共用。
- 文本预览 5MB 上限 ≈ 千万字符级，前端 `<pre>` 渲染压力可控；超过用截断。
- 大量小文件改动（编译 / 解压）通过防抖合并到 100ms 窗口。
- **容器启动时间(关键)**:
  - entrypoint **不阻塞**等待 unoserver,容器从 `docker run` 到 web 服务可接受请求 = 现状无回归(<1s)
  - unoserver 在后台并行启动,8-12s 后就绪;期间所有非 .pptx 请求(浏览/预览/聊天/WS)正常服务
  - LibreOffice 守护进程内存占用 ~150MB(容器空跑 baseline 之上的增量)
- **LibreOffice 转换性能**:
  - 冷启动:容器启动后首次 .pptx 请求 — 通常 unoserver 已就绪(因为用户从打开 /files 到点击 .pptx 已超过 10s),走热请求路径
  - 罕见冷启动场景(用户启动后 5s 内点 .pptx):converter 内 `_ensure_ready` 等待 unoserver,合计延迟 5-10s
  - 热请求(unoserver 已就绪): 200-800ms / .pptx
  - 缓存命中: <10ms(直接 sendfile)
  - 串行化 `asyncio.Lock`,避免单容器并发调 LibreOffice 引发崩溃
- **镜像体积**: 仅装 `libreoffice-impress` + 核心,增量约 280MB(去掉 java-common 后);加 CJK 字体约 +50MB
- **前端 docx-preview / xlsx**: 都是纯前端 JS,无后端开销;首次加载组件 lazy-import,避免影响 `/files` 首屏

---

## 8. 实施步骤（分阶段提交）

### Phase 1 — 基础预览（无实时同步、无 Office）

| # | 任务 | 文件 |
|---|------|------|
| 1.1 | 增加 `preview_kind_for` 与 `read_text_safely` | `nanobot/web/preview.py`（新增） |
| 1.2 | 增加 `/api/workspace/preview` 端点 | `nanobot/web/server.py` |
| 1.3 | 重构 `/files` 页面为左右分栏 | `frontend/app/files/page.tsx` |
| 1.4 | 新增 `FileTree` 组件 | `frontend/components/FileTree.tsx` |
| 1.5 | 新增 `FilePreview` + 文本/Markdown/JSON/图片/PDF/HTML/Binary 子组件 | `frontend/components/FilePreview/*` |
| 1.6 | 增加 `getWorkspacePreview`/`fetchWorkspaceBlobUrl` API | `frontend/lib/api.ts` |
| 1.7 | 本地 `nanobot web` 模式手测各类型预览 | — |

**Phase 1 验收标准**：
- 在文件树中点击任意 md/py/json/png/pdf/html，右侧能看到内容。
- Office/.zip 显示"仅支持下载"+ 下载按钮。
- >5MB 的文本文件提示截断。

### Phase 2 — 实时同步

| # | 任务 | 文件 |
|---|------|------|
| 2.1 | 增加 `pyproject.toml` 依赖 `watchdog>=4.0.0` | `pyproject.toml` |
| 2.2 | 实现 `WorkspaceWatcher` | `nanobot/web/watcher.py`（新增） |
| 2.3 | 增加 `/ws/files` 端点 | `nanobot/web/server.py` |
| 2.4 | 在 WebChannel 启停时管理 watcher 生命周期 | `nanobot/channels/web.py` |
| 2.5 | 平台 WS 代理改为 `/ws/{path:path}` 通配 | `platform/app/routes/proxy.py` |
| 2.6 | 实现 `useFileEvents` Hook | `frontend/hooks/useFileEvents.ts` |
| 2.7 | `FileTree` 接收事件做增量更新 | `frontend/components/FileTree.tsx` |
| 2.8 | `FilePreview` 监听当前路径事件触发 reload | `frontend/components/FilePreview/index.tsx` |
| 2.9 | 多用户模式联调（docker-compose 启动） | — |

**Phase 2 验收标准**：
- 在聊天中让 AI `write_file` 一个新文件 → 前端 1s 内出现节点。
- 当前预览的 markdown 被 AI `edit_file` → 内容自动刷新。
- 删除文件 → 节点从树中消失，预览区显示提示。
- WS 断开重连时不丢节点。

### Phase 3 — Word / Excel 前端预览

| # | 任务 | 文件 |
|---|------|------|
| 3.1 | 增加 `package.json` 依赖 `docx-preview` + `react-window` | `frontend/package.json` |
| 3.2 | 实现 `WordPreview` 组件 | `frontend/components/FilePreview/WordPreview.tsx` |
| 3.3 | 实现 `ExcelPreview` 组件(含 sheet 切换 + 大文件保护) | `frontend/components/FilePreview/ExcelPreview.tsx` |
| 3.4 | 后端 `preview_kind_for` 新增 `docx`/`xlsx` 分类 | `nanobot/web/preview.py` |
| 3.5 | `FilePreview` 路由分发新增 docx/xlsx 分支 | `frontend/components/FilePreview/index.tsx` |
| 3.6 | 真实 .docx 与超大 .xlsx 测试 | — |

**Phase 3 验收标准**:
- 点击 .docx → 右侧渲染出文档(含基本格式: 标题、段落、表格、图片)
- 点击 .xlsx → 渲染表格,sheet tab 可切换
- 10w 行的 .xlsx 滚动流畅(react-window 虚拟化生效)
- 100w cells+ 的 .xlsx 显示"建议下载查看"

### Phase 4 — PowerPoint 后端转换

| # | 任务 | 文件 |
|---|------|------|
| 4.1 | nanobot 镜像 Dockerfile 加装 LibreOffice impress + unoserver + CJK 字体 | `Dockerfile` |
| 4.2 | entrypoint 启动 unoserver 守护进程 | `entrypoint.sh` (或现有启动脚本) |
| 4.3 | 实现 `OfficeConverter`(转 + 缓存 + 清理) | `nanobot/web/converter.py`(新增) |
| 4.4 | 增加 `/api/workspace/render` 端点 | `nanobot/web/server.py` |
| 4.5 | WebChannel 启停时管理 converter,watcher 删除事件回调 cleanup | `nanobot/channels/web.py` |
| 4.6 | 后端 `preview_kind_for` 新增 `pptx` 分类 | `nanobot/web/preview.py` |
| 4.7 | 实现 `PptxPreview` iframe 组件 | `frontend/components/FilePreview/PptxPreview.tsx` |
| 4.8 | `FilePreview` 路由分发新增 pptx 分支 | `frontend/components/FilePreview/index.tsx` |
| 4.9 | platform gateway 给 render 端点支持 `?token=` query 转 Bearer | `platform/app/routes/proxy.py` |
| 4.10 | 缓存目录定时清理任务(启动时 + 每 24h) | `nanobot/web/converter.py` |
| 4.11 | 真实 .pptx(中文 + 图片)端到端测试 | — |

**Phase 4 验收标准**:
- 点击 .pptx → 右侧 iframe 加载 HTML(首次 1-3s,后续 <1s)
- AI `write_file` 替换 .pptx 内容 → 自动重转 + 重载
- 用户在文件树中**看不到** `/var/cache/...`,工作区下也无新增文件
- 删除 .pptx → 缓存自动清理
- 中文字体显示正常

### Phase 5 — 体验优化（可选）

| # | 任务 |
|---|------|
| 5.1 | 文件树展开状态持久化到 localStorage |
| 5.2 | 选中路径持久化（刷新页面后还在原位置） |
| 5.3 | 大文件流式预览（>5MB 文本显示前几屏 + 滚动加载） |
| 5.4 | 代码语法高亮（评估 prism-react-renderer 体积成本） |
| 5.5 | 文件树搜索框 |
| 5.6 | 右键菜单（下载/删除/重命名/复制路径） |

---

## 9. 涉及文件清单

### 新增

```
nanobot/web/preview.py                       # 预览类型识别 + 文本读取
nanobot/web/watcher.py                       # WorkspaceWatcher
nanobot/web/converter.py                     # OfficeConverter (.pptx → HTML)
frontend/components/FileTree.tsx
frontend/components/FilePreview/index.tsx
frontend/components/FilePreview/TextPreview.tsx
frontend/components/FilePreview/MarkdownPreview.tsx
frontend/components/FilePreview/JsonPreview.tsx
frontend/components/FilePreview/ImagePreview.tsx
frontend/components/FilePreview/PdfPreview.tsx
frontend/components/FilePreview/HtmlPreview.tsx
frontend/components/FilePreview/WordPreview.tsx          # 用 docx-preview
frontend/components/FilePreview/ExcelPreview.tsx         # 用 xlsx + react-window
frontend/components/FilePreview/PptxPreview.tsx          # iframe → render_url
frontend/components/FilePreview/BinaryPreview.tsx
frontend/hooks/useFileEvents.ts
```

### 修改

```
pyproject.toml                                # +watchdog 依赖
Dockerfile (nanobot 镜像)                     # +libreoffice-impress +unoserver +fonts-noto-cjk;创建 /var/cache/nanobot/preview
entrypoint.sh (或现有启动脚本)                  # 启动 unoserver 后台守护
nanobot/web/server.py                         # +preview API +render API +/ws/files
nanobot/channels/web.py                       # 启停 watcher 与 converter,wire 删除清理回调
platform/app/routes/proxy.py                  # WS 代理改通配;render 端点 ?token= 转 Bearer
frontend/app/files/page.tsx                   # 重构为分栏
frontend/lib/api.ts                           # +preview API +blob URL helper
frontend/package.json                         # +docx-preview +react-window
```

### 不动

```
nanobot/web/files.py                          # 保留所有现有 helper
frontend/types/                               # 现有类型保留
```

---

## 10. 关键风险与对策汇总

| 风险 | 对策 |
|------|------|
| watchdog 在 macOS 本地开发的兼容性 | watchdog 在 macOS 用 FSEvents，已支持 |
| Observer 启动失败（fs 不支持 inotify） | 捕获异常并降级为不推送（前端轮询兜底） |
| 大量并发 WS（一个用户开多个标签页） | 每个 WS 独立 queue，无共享状态；watcher 仍是单例 |
| HTML 预览的 XSS 攻击面 | sandbox="" + Content-Security-Policy header（download API 已通过 Content-Disposition 控制） |
| 文件名含奇怪字符（emoji / 中文 / 空格） | URL encode（前端 `encodeURIComponent`，后端 RFC 5987） |
| 用户工作区有几万文件（如 node_modules） | 默认目录黑名单跳过；同时在 `browse` API 也做同样过滤（已有 `name.startswith(".")` 过滤） |
| 平台 WS 代理改通配后影响现有 chat | chat 走 `/ws/{session_id}` 仍然匹配；只在路径解析处兼容旧逻辑 |
| **镜像体积膨胀(+280MB)影响拉取速度** | 去掉 java-common 已省 ~100MB;LibreOffice 装在独立 Docker 层,warm pool 命中后无重复拉取;后续可评估 multi-stage / squashfs |
| **unoserver 进程僵死/泄漏** | entrypoint fire-and-forget 启动,不阻塞容器就绪;converter 内 30s 就绪超时探测 + 60s 转换超时 + 子进程 kill;运行期 healthcheck 由 supervisor 守护(Phase 4 后续);死了重启容器即可 |
| **容器启动期 unoserver 未就绪而用户已点 .pptx** | converter `_ensure_ready` 默认等待 30s;典型 8-12s 后就绪;若超时返 502,前端展示"预览暂不可用,稍后重试" |
| **LibreOffice 转换失败(损坏 .pptx)** | `_convert` 抛异常 → render 端点返 502 + JSON `{error: "conversion_failed"}`;前端展示"转换失败,请下载查看" |
| **缓存目录磁盘耗尽** | 容器启动 + 24h 定时 + 命中阈值三道清理;超 500MB 触发紧急 LRU |
| **docx-preview 不支持的元素(复杂域、活动控件)** | docx-preview 自动 fallback 为占位符;不支持完美还原(可接受);用户可下载查看 |
| **前端 xlsx 解析超大 .xlsx OOM** | 100w cells 上限做硬切;超过直接拒绝渲染 |
| **多用户并发触发 LibreOffice** | 每用户独立容器 → 每容器一个 unoserver,天然隔离 |

---

## 11. 不在本方案范围内（明确剔除）

- ❌ 老二进制 Office 格式 .doc / .xls / .ppt(无开源前端解析方案,LibreOffice 转 HTML 渲染质量差)
- ❌ OpenDocument(.odt / .ods / .odp)与 RTF
- ❌ 文件编辑能力（在线修改并保存）
- ❌ 文件搜索 / 全文检索
- ❌ 文件版本历史 / 回滚
- ❌ 协作多用户同时预览同一文件
- ❌ 视频 / 音频预览
- ❌ .pptx 之外的 LibreOffice 后端转换(.docx/.xlsx 走前端,避免膨胀镜像)

这些可作为后续独立技术方案。
