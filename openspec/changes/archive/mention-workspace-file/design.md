## Context

当前主聊天页面的工作区文件感知由两部分组成：右侧 FileTree 让用户**点击**定位文件，FilePreview 渲染当前选中。但写消息时用户的诉求是"边写边引用"，必须主动切换到右侧手动找——尤其对深目录、AI 刚生成的文件、记不住完整路径的场景特别低效。

slash command picker（`/` 触发）已经在 `frontend/app/page.tsx:614-657` 提供了"输入即过滤、键盘导航、选中替换"的交互范式。本变更复用该范式，把它扩展到 `@` 触发的工作区文件搜索，并联动既有的 `selectedFile` 状态机和 FileTree。

## Goals / Non-Goals

### Goals
- `@` 在 token 起始（前面是空白/换行/字符串起始）触发，按当前 query 实时搜索工作区可预览文件。
- 选中后输入框中 `@<query>` → `@<相对路径> `（带尾部空格），并联动右侧预览 + 左侧 FileTree。
- 后端搜索 P50 < 30ms（典型工作区 < 1k 文件）、P99 < 100ms。
- 邮箱场景 `me@example.com` 中的 `@` 不触发 picker。
- 中文输入法 composing 期间不误吞 Enter/Tab。

### Non-Goals
- ❌ `@` 文件作为 LLM 上下文/附件（"仅 UI 提示"，消息文本不变）
- ❌ 历史消息中 `@path` 渲染为可点击链接
- ❌ `@` 引用目录、工作区外文件
- ❌ 多文件 `@` 同时切 tab
- ❌ Web 通道以外的输入位置
- ❌ 后端持久化文件名索引（性能足够时不引入）

## Decisions

### Decision 1: 评分维度——名字 > 路径 > fuzzy
**Choice**: `_match_score` 返回 5 档（0=name 前缀、1=name 子串、2=path 前缀、3=path 子串、4=fuzzy）。同档按 mtime desc 二次排序。

**Why**: 用户输入 `rep` 通常想要 `report.md`，而不是 `src/api/replicate.ts`——名字命中应该排前。但若用户输入 `docs/`，路径前缀命中也应该出现。fuzzy（按序出现）兜底拼写错误，但代价是 rank=4 排到最后，避免污染前几条。

**Alternatives considered**:
- TF-IDF / BM25：搜索 100 文件不需要这种工业强度算法；调试难。
- 单纯子串：丢掉"前缀更优"的人因直觉。

### Decision 2: 不引入索引/缓存
**Choice**: 每次 search 都做一次 `os.walk` + `stat`，依赖**噪音目录黑名单**（`.git`, `node_modules`, etc）控制扫描量。

**Why**: 工作区文件可由 AI/用户随时改动，缓存一致性是新麻烦——本期 watcher 已经在维护文件事件流，再叠一个索引层会出现"watcher 漏抓 → 索引和文件树不一致"。性能预算下 10k 文件 < 80ms 可接受。如果未来发现 P99 退化，再基于 watcher 维护内存索引（独立优化项）。

### Decision 3: query 边界——`@` 前必须是空白/换行/字符串起始
**Choice**: detectMention 在找到 `@` 后，检查 `value[i-1]` 是否为 ` `/`\n`/`\t`，否则 inactive。

**Why**: 不希望 `me@example.com` 把 `example.com` 当 query。同样 `@a@b` 中第二个 `@` 因前一个字符是 `a` 不触发——避免嵌套混乱。

### Decision 4: 选中后纯前端联动，不发 RPC
**Choice**: `pick(idx)` 只做：textarea 替换文本 + setSelectedFile + setScrollToPath。**不**发任何后端请求（preview 早已由现有 `selectedFile` effect 触发）。

**Why**: 解耦，且利用既有事件流。AI 删了文件 → preview 显示"已删除"已经由 `useFileEvents` 处理。

### Decision 5: scrollToPath 用 prop 触发，不用 ref/imperative
**Choice**: `<FileTree scrollToPath={path} />`，FileTree 内 `useEffect([scrollToPath])` 处理展开 + 滚动。

**Why**: 避免外部命令式调用（`fileTreeRef.current?.scrollToPath(...)`）破坏纯函数式数据流；React 的 idiomatic 做法。
**Caveat**: 重复 `@` 同一文件不会再触发（依赖比较）；本期可接受。后续如需"再次 `@` 重滚"，加 nonce state。

### Decision 6: e2e 用 mock 路由 + 有头浏览器
**Choice**: 复用 `preview.spec.ts` 的 `page.route('**/api/nanobot/...', ...)` mock 模式；在本地用 Playwright headed 模式跑（`npx playwright test --headed`）。

**Why**: 无需把测试和真实工作区状态耦合；headed 模式下用户能看到测试运行过程，符合"测试驱动开发"的可视化诉求。

## Risks / Trade-offs

| 风险 | 对策 |
|------|------|
| 大工作区 walk 慢 | 噪音目录黑名单 + limit ≤ 50 + 前端 debounce 150ms |
| 文件名带空格命中困难 | 已知限制：query 不允许空格，用户输入关键字命中后再插入完整路径（已含空格） |
| 选中后光标位置错乱 | `requestAnimationFrame` 在 React commit 后 setSelectionRange |
| 中文输入法吞键 | `e.nativeEvent.isComposing` 检查（已有 slash picker 同款） |
| popup 与 slash picker 双触发 | 互斥：mention 要求 `@` 前是空白；slash 要求 `/` 在字符串起始——两者天然不重叠 |

## Migration Plan

无 schema/API 破坏：仅新增 `GET /api/workspace/files`。已有 `/api/workspace/browse` / `/api/workspace/preview` 不变。前端 `FileTree` 新增的 prop 都是可选——其它使用方（暂无）无需改。

## Open Questions

- [ ] 后期是否需要 popup 显示 truncated="还有 N 条"提示？现有 slash picker 没这个机制，本期对齐不做。
- [ ] 是否对 `@` 后空 query 做"显示最近修改的 20 个文件"？后端已支持（rank=99 + mtime 排序）；前端默认拉一次空查询作为初始候选——本期采用。
