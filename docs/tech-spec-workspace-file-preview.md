# 工作区文件实时预览技术方案

## 1. 背景与目标

### 问题
当前主聊天页面缺少工作区文件感知能力，存在两个体验缺口：

1. **不能预览**：用户必须切换到 `/files` 页面或下载到本地才能查看 AI 生成的报告、Markdown、代码、图片等，打断对话流。
2. **不能感知改动**：当 nanobot agent 通过 `write_file`/`edit_file` 修改了工作区文件，聊天页面没有任何反馈，用户不知道文件已变更。

### 目标
- 在主页面（`/`）提供**三栏布局**：左侧会话列表 + 中间对话框 + 右侧工作区文件预览。
- 支持的预览格式（**混合渲染策略**）：
  - **文本类**（前端）：代码（py/ts/js/go/rs/java/c/cpp/...）、txt、yaml、toml、log、env
  - **结构化文本**（前端）：JSON、Markdown、HTML
  - **图片**（前端）：png、jpg、jpeg、gif、webp、svg、bmp、ico
  - **PDF**（浏览器原生）
  - **Word `.docx`**（前端 `docx-preview` JS 库）
  - **Excel `.xlsx`**（前端 `xlsx` 库 + `react-window` 虚拟滚动表格）
- **不预览，仅下载**：
  - 老二进制 Office 格式（.doc/.xls/.ppt） — 前端无法解析
  - 其他 OpenDocument（.odt/.ods/.odp）、RTF — 暂不支持
  - 压缩包、二进制可执行
- **实时同步**：基于 watchdog + WebSocket
  - AI 写文件 / 用户上传 → 右侧文件树自动刷新
  - 当前预览的文件被改动 → 内容自动重载
  - 文件被删除 → 预览区显示提示

---

## 2. 术语定义

| 术语 | 含义 |
|------|------|
| **WorkspaceWatcher** | 监听工作区目录变化的服务（基于 watchdog） |
| **预览类型（preview_kind）** | 后端识别出的文件呈现方式：`text`/`markdown`/`json`/`image`/`pdf`/`html`/`docx`/`xlsx`/`binary` |
| **WS /ws/files** | 文件事件推送通道，与 chat 的 `/ws/{session_id}` 独立 |
| **防抖（debounce）** | 同一文件 100ms 内多次修改合并为一个事件 |

---

## 3. 整体架构

```
                    ┌──────────────────────────────────────┐
                    │   Browser (/)                        │
                    │ ┌────────┬────────────────┬────────┐ │
                    │ │Session │ Chat           │File    │ │
                    │ │List    │                │Preview │ │
                    │ └────────┴────────────────┴────────┘ │
                    └──┬───────────────┬───────────────────┘
                       │               │
                  HTTP │               │ WebSocket
        (browse/preview/render)        (file_event)
                       ▼               ▼
              ┌─────────────────────────────────┐
              │  Platform Gateway               │
              │  /api/nanobot/...               │
              │  /api/nanobot/ws/files          │
              └──────────┬──────────────────────┘
                         │ reverse proxy
                         ▼
              ┌─────────────────────────────────────────────┐
              │   User's nanobot 容器                        │
              │                                              │
              │  FastAPI (nanobot/web)                       │
              │  ├ /api/workspace/browse                     │
              │  ├ /api/workspace/preview      ← NEW         │
              │  ├ /api/workspace/download                   │
              │  └ /ws/files                    ← NEW         │
              │           │                                  │
              │           ▼                                  │
              │  WorkspaceWatcher (inotify, debounce)        │
              │                                              │
              │  ┌──────── 用户视角(可见) ────────┐           │
              │  │ /workspace/                   │           │
              │  │   ├ docs/                     │           │
              │  │   │  └ report.md              │           │
              │  │   └ ...                       │           │
              │  └────────────────────────────────┘           │
              └─────────────────────────────────────────────┘
```

**关键约束**：

- 用户在文件树中只看到 `/workspace/` 下的文件。

---

## 4. 后端设计

### 4.0 实时推送协议选型

文件事件采用 **WebSocket**（`/ws/files`），不引入 SSE。

原因：
- 项目前端已有一套成熟的 WS 基础设施（chat 通道 `/ws/{session_id}`），文件事件复用同一套连接管理、心跳、重连逻辑，前端成本最低。
- Gateway 的 WS 代理已打通（`platform/app/routes/proxy.py`），无需再维护一套 HTTP 流转发逻辑。
- SSE 虽语义上更适合单向推送，但引入第二种实时协议会增加前后端心智负担；在本项目中收益有限。

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
  | { kind: 'binary'; reason: 'office_legacy' | 'archive' | 'unknown'; size: number; modified: string; download_url: string }
```

**关键约束**：

- `path` 必须落在 workspace 内（复用 `_resolve_workspace_path`，防穿越）
- 文本类（`text`/`markdown`/`json`）硬限制 **5MB**：超过则
  - `text`/`markdown`：返回前 5MB + `truncated: true`
  - `json`：直接返回 `binary`，提示用户下载
- 图片不在后端读字节：直接返回 `download_url`，由前端 `<img>` 拉
- PDF/HTML 同理：返回 `download_url` 让浏览器原生处理


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

文件事件的唯一实时推送通道，前端复用现有 WS 基础设施。挂在 `nanobot/web/server.py`：

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

WebChannel 启动时初始化 watcher 并塞进 app.state：

```python
async def start(self) -> None:
    ...
    workspace = self.full_config.workspace_path
    self._watcher = WorkspaceWatcher(workspace)

    app = create_app(
        ...,
        workspace_watcher=self._watcher,
    )

async def stop(self) -> None:
    ...
    if self._watcher:
        self._watcher.stop()
```

---

## 5. 前端设计

### 5.1 页面布局重构（`frontend/app/page.tsx`）

在主聊天页面增加右侧文件预览栏，形成三栏布局：

```
┌─────────────┬──────────────────────────────┬──────────────┐
│ Session     │  Chat                        │ Workspace    │
│ List        │                              │ Preview      │
│             │  ┌────────────────────────┐  │              │
│  默认       │  │ User: 生成报告          │  │ 📁 docs      │
│  5月3日    │  │                        │  │  📄 TODO    │
│  5月2日    │  │ Bot: 已生成 report.md   │  │  📄 plan.pdf│
│             │  │ [report.md preview]    │  │ ▶ 📁 src    │
│  + 新对话   │  │                        │  │              │
│             │  ├────────────────────────┤  │ ┌──────────┐ │
│             │  │ 输入消息...              │  │ │ Markdown │ │
│             │  └────────────────────────┘  │ │ Preview  │ │
│             │                              │ └──────────┘ │
└─────────────┴──────────────────────────────┴──────────────┘
```

- **左侧栏**：现有会话列表，宽度 `w-64` 不变。
- **中间栏**：现有聊天区域，自适应剩余宽度。
- **右侧栏**：新增工作区文件树 + 预览，默认宽度 `w-80`（320px），支持折叠/展开。
- 右侧栏顶部显示当前工作区路径 + 刷新按钮，底部显示 WS 连接状态。
- 三栏整体高度为 `h-[calc(100vh-3.5rem)]`（扣除顶部导航栏）。

### 5.2 FileTree 组件（`frontend/components/FileTree.tsx`，新增）

嵌入在主页右侧栏上半部分：

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
- 选中文件时高亮 + 滚动可见，右侧预览区自动加载内容。
- 右键菜单（暂可省略，第二期）：下载、删除、复制路径。

### 5.3 FilePreview 组件（`frontend/components/FilePreview/`，新增）

嵌入在主页右侧栏下半部分，与 FileTree 上下排列：

```
┌──────────────┐
│ 📁 docs      │  ← FileTree
│  📄 TODO    │
│  📄 plan.pdf│
├──────────────┤
│ TODO Content │  ← FilePreview
│ - [ ] ...    │
│ - [x] ...    │
└──────────────┘
```

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
| `BinaryPreview` | 显示文件名、大小、MIME，只给"下载"按钮 | — |

> **鉴权流程**：所有 `download_url`（`/api/nanobot/workspace/download`）需要 `Authorization: Bearer ...` Header。前端调用 `fetch + blob → URL.createObjectURL` 得到本地 blob URL，再喂给 `<img>`/`<iframe>`/Word/Excel 渲染器。

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


### 5.4 useFileEvents Hook（`frontend/hooks/useFileEvents.ts`，新增）

```typescript
export function useFileEvents(): {
  status: WsStatus;
  events: FileEvent[];        // 最近事件流（全量）
  lastEventForPath: (path: string) => number | null; // ts of last event
}
```

实现：
- 复用项目已有的 `wsManager` reconnect / ping 模式（指数退避 1s→30s），不另起 SSE 连接。
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
| 路径穿越 | 复用 `_resolve_workspace_path`；preview API 在文件不在 workspace 内时返回 400 |
| 大文件预览拖垮服务器 | 文本类硬限制 5MB；超过则截断或拒绝；二进制不读字节;Excel cells > 1M 直接拒绝渲染 |
| 恶意 HTML 通过 iframe 盗 token / 跨域请求 | iframe `sandbox=""`（最严格，禁用 JS、表单、popup、跨源） |
| SVG 内嵌脚本 | SVG 走 `<img>` 而不是 `<iframe>`（`<img>` 不执行脚本） |
| WS 鉴权 | 多用户模式由 platform gateway 验证 token，未验证不打开上游连接 |
| Watcher 资源泄漏 | start/stop 严格配对；最后一个 listener 移除时回收 Observer |
| 监听放大效应（如 git pull 一次几千文件） | 100ms 防抖；前端事件流上限 200 条 |
| 用户重连漏事件 | 重连时前端触发一次根目录 browse，强制对齐状态 |

---

## 7. 性能

- watchdog 在 Linux 容器内使用 inotify，几千个文件无压力（每文件 ~1KB 内核内存）。
- 单容器单 watcher 单 Observer 线程；多 WS 连接共用。
- 文本预览 5MB 上限 ≈ 千万字符级，前端 `<pre>` 渲染压力可控；超过用截断。
- 大量小文件改动（编译 / 解压）通过防抖合并到 100ms 窗口。
- **前端 docx-preview / xlsx**: 都是纯前端 JS,无后端开销;首次加载组件 lazy-import,避免影响主页面首屏

---

## 8. 实施步骤（分阶段提交）

### Phase 1 — 基础预览（无实时同步）

| # | 任务 | 文件 |
|---|------|------|
| 1.1 | 增加 `preview_kind_for` 与 `read_text_safely` | `nanobot/web/preview.py`（新增） |
| 1.2 | 增加 `/api/workspace/preview` 端点 | `nanobot/web/server.py` |
| 1.3 | 重构主页面为三栏布局（左会话 / 中聊天 / 右文件预览） | `frontend/app/page.tsx` |
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

### Phase 4 — 体验优化（可选）

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
frontend/components/FilePreview/BinaryPreview.tsx
frontend/hooks/useFileEvents.ts
```

### 修改

```
pyproject.toml                                # +watchdog 依赖
nanobot/web/server.py                         # +preview API +/ws/files
nanobot/channels/web.py                       # 启停 watcher
platform/app/routes/proxy.py                  # WS 代理改通配
frontend/app/page.tsx                         # 重构为三栏布局（左会话 / 中聊天 / 右文件预览）
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
| **docx-preview 不支持的元素(复杂域、活动控件)** | docx-preview 自动 fallback 为占位符;不支持完美还原(可接受);用户可下载查看 |
| **前端 xlsx 解析超大 .xlsx OOM** | 100w cells 上限做硬切;超过直接拒绝渲染 |

---

## 11. 不在本方案范围内（明确剔除）

- ❌ PowerPoint `.pptx`（无开源前端解析方案，暂不提供预览）
- ❌ 老二进制 Office 格式 .doc / .xls / .ppt
- ❌ OpenDocument(.odt / .ods / .odp)与 RTF
- ❌ 文件编辑能力（在线修改并保存）
- ❌ 文件搜索 / 全文检索
- ❌ 文件版本历史 / 回滚
- ❌ 协作多用户同时预览同一文件
- ❌ 视频 / 音频预览

这些可作为后续独立技术方案。
