# 聊天输入框 `@` 工作区文件快捷提示技术方案

## 1. 背景与目标

### 问题
工作区文件预览（参见 [tech-spec-workspace-file-preview.md](./tech-spec-workspace-file-preview.md)）已经把右侧文件树+预览区接入主聊天页，但用户在写消息时仍需要：
1. **手动从文件树定位**：消息里想引用一个文件（"看一下 docs/report.md"），用户得分心去右侧文件树点开父目录翻找。
2. **不知道有哪些文件**：刚被 AI 写出来的文件、几层目录下的文件，用户记不住完整路径。
3. **预览切换不连贯**：在输入框写"修改一下 plan.md"之前，得先切到右侧手动点 plan.md 才能边对照边写。

### 目标
- 在主聊天输入框（`frontend/app/page.tsx` 的 `<textarea>`）按 `@` 时弹出**文件快捷提示菜单**：
  - 候选 = 工作区里**可预览的文件**（排除目录、排除二进制如 `.zip`/`.exe`/老 Office）
  - 实时搜索：随用户输入字符过滤（debounce 150ms），按匹配质量+修改时间排序
  - 键盘交互完全对齐已有的 slash command picker（`page.tsx:614-657`）：↑↓ 选择、Tab/Enter 确认、Esc 关闭
- 选中候选后：
  - 输入框中 `@<query>` 被替换为 `@<相对路径> `（带尾部空格便于继续输入）
  - **右侧预览区自动切到该文件**（复用现有 `selectedFile` 状态机）
  - **左侧文件树自动展开父目录、滚动到节点并高亮**
- **`@` 仅是 UI 输入辅助**：替换后的 `@docs/foo.md` 在消息文本中就是普通字符串，**消息流不变更**——AI 不会自动获得文件内容（如有需要它可用 `read_file` 工具读取）。后端无需解析消息中的 `@-mention`。

### 非目标（明确剔除）
- ❌ 把 @ 文件作为附件/上下文塞进 LLM 请求（与本期"仅 UI 提示"决策冲突）
- ❌ @ 引用目录（与本期"排除目录"决策冲突）
- ❌ 富文本 chip：仍然是普通文本，没有"删除整段引用"的特殊键位
- ❌ 历史消息里的 @path 在渲染时变成可点击链接（第二期可加）
- ❌ 多文件 @ 同时切 tab（与本期"切换式预览"决策冲突，最后一个 @ 选中胜出）
- ❌ Web 通道以外的输入（cron 编辑、其它表单）

---

## 2. 术语定义

| 术语 | 含义 |
|------|------|
| **mention picker** | 触发 `@` 后浮在输入框上方的候选下拉菜单，行为对齐已有的 slash command picker |
| **mention token** | 输入框中以 `@` 开头、由空白/换行/字符串边界终止的当前词 |
| **query** | mention token 中 `@` 之后到光标位置的字符（用于发送到后端搜索） |
| **mention start** | 输入框中触发 `@` 的字符索引，选中候选后用于精确替换 |
| **可预览文件** | `nanobot/web/preview.py:preview_kind_for` 返回的 `kind` ≠ `binary` 的文件 |

---

## 3. 整体架构

```
┌──────────────────────────────────────────────────────┐
│  Browser (/)  textarea                               │
│  「修改 @docs/rep|」  ← 光标在 query 末尾           │
│        ┌──────────────┐                              │
│        │ docs/report.md ← 当前选中(高亮)             │
│        │ docs/replan.md                              │
│        │ src/api/replicate.ts                        │
│        └──────────────┘  popup                       │
└──────────────┬───────────────────────────────────────┘
               │ HTTP GET (debounced 150ms)
               ▼
┌──────────────────────────────────────────────────────┐
│  Platform Gateway → User's nanobot 容器              │
│  GET /api/workspace/files?q=rep&limit=20  ← NEW      │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
   walk workspace, skip 隐藏 + 噪音目录
   过滤 preview_kind=binary
   substring/word-boundary 评分排序
   返回 top-N
```

选中候选后纯前端动作：

```
选中 docs/report.md
   ├─► textarea 内容: "修改 @docs/report.md "
   ├─► setSelectedFile({path:'docs/report.md',...})  → FilePreview 加载
   └─► fileTree.scrollToPath('docs/report.md')       → 展开父目录 + 滚动
```

**关键约束**：
- 路径展示**必须**是相对工作区根的路径，不暴露任何绝对路径。
- 候选项是后端实时计算的，不在前端做全量缓存（避免大工作区首拉慢）。
- `@` 触发不影响 slash command picker（`/` 仅在输入框首字符触发；`@` 在任意位置 token 起始触发，互不重叠）。

---

## 4. 后端设计

### 4.1 新增 API：`GET /api/workspace/files`

**入参**（query string）：

| 名称 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `q` | string | 否 | 模糊匹配字符串。空字符串=只看修改时间排序的近期文件。trim 后用于匹配，长度上限 100。 |
| `limit` | int | 否 | 返回条数，默认 20，最大 50。 |

**出参**：

```typescript
type FilesSearchResponse = {
  items: Array<{
    name: string;              // 文件名
    path: string;              // 相对 workspace 的路径
    size: number;
    content_type: string;
    modified: string;          // ISO8601
    preview_kind: 'text' | 'markdown' | 'json' | 'image' | 'pdf' | 'html' | 'docx' | 'xlsx';
  }>;
  total: number;               // 命中总数（用于"还有 N 条..."提示，可省略 UI）
  truncated: boolean;          // 命中数 > limit 时为 true
};
```

**关键约束**：
- 始终排除 `preview_kind == 'binary'` 的文件，无开关。
- 始终排除目录（`@` 候选只允许文件）。
- 始终排除以 `.` 开头的隐藏文件/目录（与 `browse_workspace` 一致）。
- 始终跳过噪音目录：`.git`、`node_modules`、`__pycache__`、`.venv`、`venv`、`dist`、`build`、`.next`（与 watcher 黑名单一致，避免一次扫描成千上万个文件）。
- `q` 长度截断到 100 字符；超过 100 直接当 100 处理（防误用）。
- 返回的 `path` 都是 POSIX 风格的相对路径（`docs/foo.md`），与 browse/preview API 一致。

### 4.2 实现：`nanobot/web/files.py` 新增 `search_workspace_files`

```python
# 噪音目录黑名单（不递归进入）
_NOISE_DIRS = frozenset({
    ".git", "node_modules", "__pycache__",
    ".venv", "venv", "dist", "build", ".next",
})

def search_workspace_files(
    workspace: Path,
    query: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Return the top-N preview-able files matching query."""
    workspace = workspace.resolve()
    q = (query or "").strip().lower()[:100]
    limit = max(1, min(50, limit))

    matches: list[tuple[int, float, dict]] = []  # (rank, mtime_neg, item)
    for item in _iter_preview_files(workspace):
        score = _match_score(item["name"], item["path"], q)
        if score is None:
            continue
        matches.append((score, -item["_mtime_ts"], item))

    matches.sort(key=lambda t: (t[0], t[1]))
    truncated = len(matches) > limit
    items = [m[2] for m in matches[:limit]]
    # 删除内部字段后返回
    for it in items:
        it.pop("_mtime_ts", None)

    return {
        "items": items,
        "total": len(matches),
        "truncated": truncated,
    }


def _iter_preview_files(workspace: Path) -> Iterator[dict]:
    """Walk workspace, skipping noise dirs and binary files."""
    from nanobot.web.preview import preview_kind_for

    for root, dirs, files in os.walk(workspace, followlinks=False):
        # 原地剪枝
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in _NOISE_DIRS
        ]
        root_path = Path(root)
        for fname in files:
            if fname.startswith("."):
                continue
            fpath = root_path / fname
            try:
                kind, _ = preview_kind_for(fpath)
            except Exception:
                continue
            if kind == "binary":
                continue
            try:
                stat = fpath.stat()
            except OSError:
                continue
            rel = str(fpath.relative_to(workspace).as_posix())
            ct, _ = mimetypes.guess_type(fname)
            yield {
                "name": fname,
                "path": rel,
                "size": stat.st_size,
                "content_type": ct or "application/octet-stream",
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "preview_kind": kind,
                "_mtime_ts": stat.st_mtime,
            }
```

### 4.3 匹配评分：`_match_score`

```python
def _match_score(name: str, path: str, q: str) -> int | None:
    """
    返回 None 表示不命中,否则返回 rank 数字(越小越靠前)。
    评分维度(rank 越小越好):
      0  文件名前缀命中            (eg. q='rep' → 'report.md')
      1  文件名子串命中            (eg. q='ort' → 'report.md')
      2  路径前缀命中              (eg. q='docs/' → 'docs/foo.md')
      3  路径子串命中              (eg. q='ort' → 'docs/report.md',名字未命中部分)
      4  fuzzy(字符按序出现)命中   (eg. q='rpm' → 'report.md')
    空 q 一律 rank=99(纯按时间排序)
    """
    if not q:
        return 99
    n_lower, p_lower = name.lower(), path.lower()
    if n_lower.startswith(q):
        return 0
    if q in n_lower:
        return 1
    if p_lower.startswith(q):
        return 2
    if q in p_lower:
        return 3
    if _fuzzy_match(q, n_lower) or _fuzzy_match(q, p_lower):
        return 4
    return None


def _fuzzy_match(needle: str, haystack: str) -> bool:
    i = 0
    for c in haystack:
        if i < len(needle) and c == needle[i]:
            i += 1
            if i == len(needle):
                return True
    return False
```

排序：`(rank asc, mtime desc)`——同档命中按修改时间倒序，让最近改过的文件排前。

### 4.4 端点注册：`nanobot/web/server.py`（修改）

```python
@app.get("/api/workspace/files")
async def search_workspace_files_endpoint(q: str = "", limit: int = 20):
    """Search workspace for preview-able files (used by chat input @-mention)."""
    from nanobot.web.files import search_workspace_files
    config: Config = app.state.config
    return search_workspace_files(config.workspace_path, q, limit)
```

### 4.5 平台网关代理（无改动）

`/api/nanobot/*` 已经透传所有 `/api/*`，新端点自动被代理，无需改 `platform/app/routes/proxy.py`。

### 4.6 性能预算

| 工作区规模（含噪音目录前） | 实际 walk 文件数 | 单次 search 耗时 |
|---|---|---|
| 100 文件 | < 100 | < 5ms |
| 1k 文件 | ~ 1k | < 30ms |
| 10k 文件（如 mono-repo） | 噪音剪枝后通常 < 2k | < 80ms |
| 极端：用户工作区里有 100k 个 `.md` | 100k | < 500ms |

每次按键触发一次 search 不可接受（需 debounce 150ms），同时这是相对工作区目录的 stat-only 遍历，CPU 开销远低于 `read_file`。无需 Phase 1 即引入索引；如线上发现 P99 > 200ms 再引入持久化 index（见 §10）。

### 4.7 缓存策略（不做）

不引入 in-memory index 或 lru_cache：
- 工作区文件可由 AI/用户随时改动，缓存一致性是新麻烦。
- `WorkspaceWatcher` 只发事件，不维护索引——本期不扩展它的职责。
- 如未来要做：基于 watcher 维护一个 `dict[str, FileStat]`，事件来时 in-place 增删改，search 时仅过滤+排序内存数据。这是独立的优化项，不在本方案范围。

---

## 5. 前端设计

### 5.1 输入框触发器：`useMentionPicker` Hook（新增）

新增 `frontend/hooks/useMentionPicker.ts`，负责所有"识别 query / 弹出候选 / 替换文本"的状态机。

```typescript
interface MentionState {
  active: boolean;          // 当前是否在 @ 触发态
  mentionStart: number;     // @ 字符的 textarea 索引(active 时有效)
  query: string;            // @ 之后到光标的字符串
}

export function useMentionPicker(opts: {
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  value: string;
  onCommit: (item: WorkspaceFile) => void;   // 选中候选时调用
}) {
  const [state, setState] = useState<MentionState>({ active: false, mentionStart: -1, query: '' });
  const [items, setItems] = useState<WorkspaceFile[]>([]);
  const [pickIndex, setPickIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  // ...
  return {
    state, items, pickIndex, loading,
    onValueChange,           // 输入框 onChange 时调
    onSelectionChange,       // 输入框 onSelect/onClick/onKeyUp 时调
    onKeyDown,               // 键盘交互(↑↓ Tab Enter Esc)
    pick,                    // 程序化选中第 i 项
    close,                   // 强制关闭
  };
}
```

#### 5.1.1 query 检测算法

每次 `value` 变化或光标位置变化，重新计算：

```typescript
function detectMention(value: string, cursor: number): MentionState {
  // 从光标向前扫描,找最近的 @
  let i = cursor - 1;
  while (i >= 0) {
    const ch = value[i];
    if (ch === '@') break;
    // 遇到空白/换行时停止 → 当前 token 内没 @
    if (ch === ' ' || ch === '\n' || ch === '\t') return inactive;
    i--;
  }
  if (i < 0 || value[i] !== '@') return inactive;

  // 验证 @ 前是 token 边界(空白或字符串起始)
  if (i > 0) {
    const prev = value[i - 1];
    if (prev !== ' ' && prev !== '\n' && prev !== '\t') return inactive;  // 拒绝 me@example.com
  }

  const query = value.slice(i + 1, cursor);
  return { active: true, mentionStart: i, query };
}
```

约束：
- query 中不允许换行（一旦碰到就 inactive，避免 textarea 多行误命中）。
- query 长度上限 100，超过则关闭 picker（罕见情况，用户大概率是粘贴文本）。
- `@` 后立刻接空格也关闭（query 必须连续）。

#### 5.1.2 候选拉取（debounce）

```typescript
const debouncedQuery = useDebouncedValue(state.query, 150);

useEffect(() => {
  if (!state.active) { setItems([]); return; }
  let cancelled = false;
  setLoading(true);
  searchWorkspaceFiles(debouncedQuery, 20)
    .then(res => { if (!cancelled) { setItems(res.items); setPickIndex(0); } })
    .catch(() => { if (!cancelled) setItems([]); })
    .finally(() => { if (!cancelled) setLoading(false); });
  return () => { cancelled = true; };
}, [debouncedQuery, state.active]);
```

#### 5.1.3 选中提交

```typescript
const pick = (idx: number) => {
  const item = items[idx];
  if (!item) return;
  const before = value.slice(0, state.mentionStart);
  const after = value.slice(textareaRef.current!.selectionStart);
  const inserted = `@${item.path} `;
  const newValue = before + inserted + after;
  const newCursor = (before + inserted).length;
  // 通过 onCommit 把 newValue+newCursor 上报给 page,page 调 setInput 并设光标
  onCommit({ value: newValue, cursor: newCursor, file: item });
  setState(inactive);
};
```

> 设光标位置：`textareaRef.current.setSelectionRange(newCursor, newCursor)` 必须在 React commit 后做（用 `useLayoutEffect` 或 setTimeout 0）。

#### 5.1.4 键盘交互

`onKeyDown` 的优先级在 slash picker 之前？**不**——slash picker 只在 `input.startsWith('/')` 才生效，二者天然互斥。逻辑顺序：

```
if mentionPicker.active && items.length > 0:
   ↑↓: 移动 pickIndex(wrap-around)
   Tab/Enter (no shift, not composing): pick(pickIndex), preventDefault
   Esc: close, preventDefault
else if slashPicker(已有逻辑):
   ...
else if Enter && !shift && !composing:
   handleSend()
```

复合键（输入法 composing）必须保持透传，避免中文输入被误吞。

### 5.2 主页面集成：`frontend/app/page.tsx`（修改）

#### 5.2.1 引入 hook 与状态

```tsx
const mention = useMentionPicker({
  textareaRef,
  value: input,
  onCommit: ({ value, cursor, file }) => {
    setInput(value);
    requestAnimationFrame(() => {
      textareaRef.current?.setSelectionRange(cursor, cursor);
      textareaRef.current?.focus();
    });
    // 联动预览:把 file 包成 WorkspaceItem 喂给已有 setSelectedFile
    setSelectedFile({
      name: file.name,
      path: file.path,
      type: 'file',
      size: file.size,
      modified: file.modified,
      content_type: file.content_type,
    });
    setPreviewDeleted(false);
    // 通知 FileTree 滚动定位
    setScrollToPath(file.path);
  },
});
```

新增 state：`const [scrollToPath, setScrollToPath] = useState<string | null>(null);`

#### 5.2.2 textarea 接线

```tsx
<textarea
  ref={textareaRef}
  value={input}
  onChange={(e) => {
    setInput(e.target.value);
    mention.onValueChange(e.target.value, e.target.selectionStart);
  }}
  onSelect={(e) => mention.onSelectionChange((e.target as HTMLTextAreaElement).selectionStart)}
  onKeyDown={(e) => {
    if (mention.handleKeyDown(e)) return;
    handleKeyDown(e);   // 已有的 slash + Enter 逻辑
  }}
  ... // 其余样式不变
/>
```

#### 5.2.3 候选 popup（复用 slash picker 样式）

挂在 textarea 上方,绝对定位与 slash picker 共享同一区域：

```tsx
{mention.state.active && mention.items.length > 0 && (
  <div
    className="absolute bottom-full left-0 right-10 mb-2 bg-popover border border-border rounded-lg shadow-lg overflow-y-auto max-h-60 z-50"
    data-testid="mention-picker"
  >
    {mention.items.map((item, i) => (
      <button
        key={item.path}
        className={`w-full text-left px-3 py-2 flex items-center gap-2 text-sm transition-colors ${
          i === mention.pickIndex ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/50'
        }`}
        onMouseDown={(e) => { e.preventDefault(); mention.pick(i); }}
        onMouseEnter={() => mention.setPickIndex(i)}
        data-path={item.path}
      >
        <FileIconForKind kind={item.preview_kind} />
        <span className="font-mono text-foreground truncate">{item.name}</span>
        <span className="text-muted-foreground text-xs truncate ml-auto">{item.path}</span>
      </button>
    ))}
  </div>
)}
```

> **不要**和 slash picker 同时显示。互斥保证：mention.active 与 `input.startsWith('/')` 自然互斥（前者要求 `@` 在 token 起点，后者要求 `/` 在字符串起点；只可能其一为真）。

#### 5.2.4 空 query 与 loading 视觉

- `mention.state.active` 但 `items` 为空 + `loading=true`：popup 内显示一行"搜索中..."。
- 拿到结果且 `items.length === 0`：popup 显示"未找到匹配的文件"。
- 不展示 popup 边框抖动：固定 max-h-60 + overflow-y-auto。

### 5.3 FileTree 滚动定位：`frontend/components/FileTree.tsx`（修改）

新增 prop：

```typescript
interface FileTreeProps {
  // ... 已有字段
  /** 接收一个相对路径,组件内部展开父目录链 + 滚动 + 高亮 */
  scrollToPath?: string | null;
}
```

实现：

```tsx
useEffect(() => {
  if (!scrollToPath) return;
  let cancelled = false;
  (async () => {
    // 1) 沿路径自顶向下展开父链
    const parts = scrollToPath.split('/').slice(0, -1);   // 去掉文件名只留父目录
    let cur = '';
    for (const seg of parts) {
      const next = cur ? `${cur}/${seg}` : seg;
      // 若该目录未加载,await loadDir(next) 后再展开
      if (cancelled) return;
      if (tree[next]?.children === undefined) {
        await loadDir(next);
      }
      setTree(prev => ({ ...prev, [next]: { ...prev[next], expanded: true } }));
      cur = next;
    }
    // 2) 等下一帧让 DOM 渲染好,再 scrollIntoView
    if (cancelled) return;
    requestAnimationFrame(() => {
      const el = document.querySelector(`[data-path="${CSS.escape(scrollToPath)}"]`);
      el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
  })();
  return () => { cancelled = true; };
}, [scrollToPath]);
```

注意：
- `selectedPath` 已有的高亮逻辑天然命中（`isSelected = !isDir && item.path === selectedPath`），无需新增高亮。
- 多次设置同一 `scrollToPath`：用 `useEffect` 依赖比较时不会重复触发；但若希望"再次 @ 同一文件也重新滚"，可改成 `[scrollToPath, scrollKey]`，page 维护一个 nonce。本期暂不做。
- 父链已展开时 `loadDir` 不会重复加载（已加载过则不进入分支）。

### 5.4 API Client 扩展：`frontend/lib/api.ts`（修改）

```typescript
export interface WorkspaceFile {
  name: string;
  path: string;
  size: number;
  content_type: string;
  modified: string;
  preview_kind: 'text' | 'markdown' | 'json' | 'image' | 'pdf' | 'html' | 'docx' | 'xlsx';
}

export interface FilesSearchResult {
  items: WorkspaceFile[];
  total: number;
  truncated: boolean;
}

export async function searchWorkspaceFiles(
  q: string = '',
  limit: number = 20,
): Promise<FilesSearchResult> {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  params.set('limit', String(limit));
  return fetchJSON(`/api/nanobot/workspace/files?${params.toString()}`);
}
```

### 5.5 文件事件联动（无新增）

文件被删/改时 `WorkspaceWatcher` 已经会推 WS 事件，由 `useFileEvents` 接收，`FilePreview` 已经会 reload，`FileTree` 已经会增删节点——这些都不变。`@` 选中后被联动的预览/树都跟着已有事件流走，无需新增订阅。

唯一边界：用户 `@` 选中文件后，AI 立刻把它删了——这时 popup 已关闭，selectedFile 仍指向该路径，`previewDeleted` 状态机会显示"文件已删除"提示（已有逻辑）。无需新增处理。

---

## 6. 数据流（端到端）

```
用户在 textarea 输入「请看 @rep」(光标在末尾)
   │
   ▼
onChange → useMentionPicker.onValueChange("请看 @rep", cursor=7)
   │
   ▼
detectMention 找到 @ 在 index=3, query="rep"
   │
   ▼
setState({ active:true, mentionStart:3, query:"rep" })
   │
   ▼
debounced 150ms → searchWorkspaceFiles("rep", 20)
   │
   ▼
GET /api/nanobot/workspace/files?q=rep&limit=20
   │
   ▼
后端 walk + 评分 → [{path:"docs/report.md",...}, {path:"docs/replan.md",...}]
   │
   ▼
setItems(...) → popup 显示 (pickIndex=0 高亮第一项)
   │
   ▼
用户按 ↓ → pickIndex=1; 按 Tab/Enter → pick(1)
   │
   ▼
textarea 内容变成 "请看 @docs/replan.md "
selectedFile = { path:"docs/replan.md", ... }     → FilePreview 加载
setScrollToPath("docs/replan.md")                 → FileTree 展开 docs/ + 滚动
```

---

## 7. 安全 / 边界

| 风险 | 对策 |
|------|------|
| 路径穿越 | 后端 search 只返回工作区内文件,`Path.relative_to(workspace)` 失败的不入队。前端拿到的 path 必经 `_resolve_workspace_path` 才能在 preview/download 用。 |
| 大工作区 walk 卡顿 | 噪音目录黑名单(`.git` 等);单次 limit ≤ 50;前端 debounce 150ms。 |
| 用户在密码 / token 里出现 `@` 被误识别 | 触发条件要求 `@` 前是空白/换行/字符串起始;邮箱 `me@example.com` 中的 `@` 因前面是 `me` 不会触发。 |
| 中文输入法 composing 期间被误吞 Enter/Tab | `onKeyDown` 检查 `e.nativeEvent.isComposing`(已有 slash picker 同款逻辑)。 |
| 文件名含空格(如 `my doc.md`)输入困难 | 接受现实:用户输入不带空格的关键字命中,选中后路径直接整段插入(已含空格)。后端搜索也会基于 trim 后的 q 命中空格前的部分;若用户硬要带空格,query 会因 detectMention 的空格规则关闭 picker——属于已知限制。 |
| 用户连续多次 `@`(如 `@a@b`) | 检测从光标向前找最近的 `@`;遇到空白前的另一个 `@` 已是上一个 token 的内容,本 token 边界不满足"`@` 前是空白"则失败。即:`@a@b` 中第二个 `@` 触发的 query="b",但前置字符是 'a' 不是空白,因此**不触发**。这是合理保守行为(不希望嵌套)。 |
| 工作区根有同名 doc/foo.md 与 src/foo.md | 两条候选都返回,各自完整路径,用户按上下文选。 |
| 后端 q 注入 / SQL-like 攻击 | q 是纯字符串、只用于子串/fuzzy 比较,无 SQL/shell,无注入面。 |
| popup 太长盖住消息 | `max-h-60 overflow-y-auto`(已有 slash picker 做法)。 |
| FileTree scrollToPath 时该目录因事件被删 | `loadDir` 会失败,捕获异常后静默跳过(不要 alert);用户依旧看到"文件已删除"提示。 |

---

## 8. 性能

- **后端搜索**：典型工作区 P50 < 30ms,P99 < 100ms;`os.walk` + `stat` 不读文件内容,IO 是 stat-only。
- **前端 popup**:每次按键最多 1 次网络往返,debounce 150ms 内多次按键合并。20 条候选的 list 渲染开销 < 1ms。
- **FileTree scroll**:展开父链是 N 次 `loadDir`(每次返回 100 个以内的目录项),典型 N ≤ 5;最坏 < 200ms,远小于用户感知阈值。

---

## 9. 实施步骤（分阶段提交）

### Phase 1 — 后端搜索接口

| # | 任务 | 文件 |
|---|------|------|
| 1.1 | 新增 `_NOISE_DIRS` 常量与 `_iter_preview_files` walker | `nanobot/web/files.py` |
| 1.2 | 新增 `_match_score`、`_fuzzy_match`、`search_workspace_files` | `nanobot/web/files.py` |
| 1.3 | 在 `server.py` 注册 `/api/workspace/files` 端点 | `nanobot/web/server.py` |
| 1.4 | 单元测试:子串/前缀/fuzzy/排序/limit/binary 过滤/路径穿越 | `tests/test_files.py`(新增或扩展) |

**Phase 1 验收**：
- `pytest tests/test_files.py` 全绿。
- 本地 `nanobot web -p 18080`,`curl 'localhost:18080/api/workspace/files?q=md&limit=5'` 返回合理结果。
- 没有 `.git`、`node_modules` 内的文件出现在结果中。
- `.zip`/`.exe` 不在结果中。

### Phase 2 — 前端 mention picker

| # | 任务 | 文件 |
|---|------|------|
| 2.1 | 新增 `searchWorkspaceFiles` API client + 类型 | `frontend/lib/api.ts` |
| 2.2 | 新增 `useMentionPicker` hook(含 detectMention、debounce、键盘) | `frontend/hooks/useMentionPicker.ts`(新增) |
| 2.3 | `page.tsx` 接线:state、textarea props、popup UI、onCommit 联动 selectedFile | `frontend/app/page.tsx` |
| 2.4 | 复用 slash picker 样式,微调 popup 文案显示(图标 + 文件名 + 路径) | 同上 |

**Phase 2 验收**：
- 输入 `@` 弹出最近 20 个文件;边输边过滤。
- ↑↓ Tab/Enter Esc 行为对齐 slash picker。
- 选中后 textarea 显示 `@<相对路径>` 并紧跟一个空格;光标停在空格后。
- 选中后右侧预览区切到该文件。
- 邮箱场景 `me@example.com` 不弹 popup。

### Phase 3 — FileTree 滚动定位

| # | 任务 | 文件 |
|---|------|------|
| 3.1 | `FileTree` 新增 `scrollToPath` prop | `frontend/components/FileTree.tsx` |
| 3.2 | 实现 `useEffect` 沿路径展开 + scrollIntoView | 同上 |
| 3.3 | `page.tsx` 在 onCommit 时 `setScrollToPath(file.path)` | `frontend/app/page.tsx` |

**Phase 3 验收**：
- @ 选中一个深目录的文件 → 左侧 FileTree 自动展开父目录链,滚动到该节点,节点高亮。
- 该目录已展开时仍能正确滚动。
- 父目录加载失败时不影响其余 UI(只是滚不到)。

### Phase 4 — 体验优化（可选,第二期）

| # | 任务 |
|---|------|
| 4.1 | 历史消息中 `@docs/foo.md` 渲染为可点击链接(点击切预览) |
| 4.2 | 候选项显示文件大小 + 修改时间 |
| 4.3 | 候选项支持 fuzzy 高亮命中字符 |
| 4.4 | popup `truncated=true` 时显示「还有 N 条结果,请输入更多关键字」提示 |
| 4.5 | 后端基于 watcher 维护内存索引,把 P99 从 100ms 压到 < 5ms(仅当性能成瓶颈时启动) |
| 4.6 | 支持目录 `@`(如把 `@docs/` 选中后插入路径前缀,继续过滤目录内子文件) |

---

## 10. 涉及文件清单

### 新增

```
frontend/hooks/useMentionPicker.ts                   # 状态机 + 键盘
docs/tech-spec-mention-workspace-file.md             # 本文档
```

### 修改

```
nanobot/web/files.py                                 # +search_workspace_files +_iter_preview_files +_match_score +_fuzzy_match +_NOISE_DIRS
nanobot/web/server.py                                # +GET /api/workspace/files
frontend/app/page.tsx                                # +mention 集成 + popup UI + scrollToPath state
frontend/components/FileTree.tsx                     # +scrollToPath prop + 展开父链逻辑
frontend/lib/api.ts                                  # +searchWorkspaceFiles + WorkspaceFile 类型
tests/test_files.py                                  # +search_workspace_files 单元测试
```

### 不动

```
nanobot/web/preview.py                               # preview_kind_for 已就绪,直接复用
nanobot/web/watcher.py                               # 本期不接索引,watcher 不变
platform/app/routes/proxy.py                         # /api/* 透传已覆盖新端点
frontend/components/FilePreview/*                    # 预览组件无改动
frontend/hooks/useFileEvents.ts                      # 事件流复用
```

---

## 11. 风险与对策汇总

| 风险 | 对策 |
|------|------|
| `os.walk` 在工作区有 symlink 循环时陷入死循环 | `followlinks=False`(已采用) |
| 用户工作区有几十万个文件 → walk 慢 | 噪音目录黑名单 + 5 层深度限制(可选项,非本期);若仍慢则进入 Phase 4.5 内存索引 |
| popup 与 slash picker 同时被触发 | 输入"`/@xxx`"时 input 以 / 开头 → slash picker 优先;两者天然互斥(参 §5.2.3) |
| 前端 debounce + 后端慢 → 用户感知卡 | popup 显示 loading 行;按键时不锁住 textarea |
| 选中候选后光标位置错乱 | 用 `requestAnimationFrame` 在 commit 后 setSelectionRange,避免与 React 状态更新竞态 |
| `path` 包含特殊字符(中文、emoji、& % 等) | 前端用 URLSearchParams 编码 q;后端用 FastAPI 自动 decode;path 渲染走 React 文本节点(无 XSS) |
| 用户用相对路径 `../`/`./` 触发 | search 接口不接受 path 参数,只接受 q;后端返回的 path 都是 `relative_to(workspace).as_posix()`,天然无 `..` |
| 后端 walk 时 stat 报错(文件被删) | 捕获 `OSError` 跳过(已实现) |
| 10w+ 文件场景 P99 退化 | 监控:在 Phase 1 验收时跑一个 stress 用例(造 10k 个 .md),记录耗时;若超 200ms 立即转 Phase 4.5 |
| 用户 @ 一个超大文件(如 100MB log) | 不影响——search 只看路径,不读内容;真正点开预览时由现有 5MB 截断逻辑处理 |

---

## 12. 不在本方案范围内（明确剔除）

- ❌ 把 `@` 文件作为 LLM 上下文/附件发送（已对齐为"仅 UI 提示"）
- ❌ 历史消息中 `@path` 的可点击渲染（Phase 4 候选）
- ❌ `@` 引用目录或工作区外文件
- ❌ 多个 `@` 同时切 tab 预览（与"切换式"决策冲突）
- ❌ Web 通道以外的输入位置（cron 编辑等）
- ❌ 候选高亮命中字符（Phase 4 候选）
- ❌ 后端持久化文件名索引（Phase 4 候选,仅当 walk 性能不达标时启动）
- ❌ 用户自定义噪音目录黑名单（用硬编码的常用列表已覆盖 95% 场景）

这些可作为后续独立方案。
