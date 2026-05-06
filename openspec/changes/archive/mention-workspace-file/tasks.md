## 1. Phase 1 — 后端搜索接口

- [x] 1.1 在 `nanobot/web/files.py` 新增 `_NOISE_DIRS` 常量(`.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `dist`, `build`, `.next`)
- [x] 1.2 在 `nanobot/web/files.py` 新增 `_iter_preview_files(workspace) -> Iterator[dict]`:os.walk + 原地剪枝噪音目录 + 跳过隐藏 + 调用 `preview_kind_for` 排除 binary
- [x] 1.3 在 `nanobot/web/files.py` 新增 `_match_score(name, path, q) -> int | None` + `_fuzzy_match(needle, haystack)`
- [x] 1.4 在 `nanobot/web/files.py` 新增 `search_workspace_files(workspace, query="", limit=20) -> dict`:组合 walker + scorer + 排序 `(rank asc, mtime desc)` + truncate
- [x] 1.5 在 `nanobot/web/server.py` 注册 `@app.get("/api/workspace/files")` 端点
- [x] 1.6 编写 `tests/test_files_search.py` 单元测试:前缀/子串/路径/fuzzy 命中、排序、limit、binary 过滤、噪音目录过滤、隐藏文件过滤、路径穿越、空 query

## 2. Phase 2 — 前端 mention picker

- [x] 2.1 在 `frontend/lib/api.ts` 新增 `WorkspaceFile`、`FilesSearchResult` 类型 + `searchWorkspaceFiles(q, limit)` 函数
- [x] 2.2 新增 `frontend/hooks/useMentionPicker.ts`:导出 `useMentionPicker` hook(detectMention / debounce 150ms / pickIndex / handleKeyDown / pick / close)
- [x] 2.3 修改 `frontend/app/page.tsx`:引入 hook、新增 `scrollToPath` state、渲染 popup(复用 slash picker 样式)、onCommit 联动 `selectedFile` + `setScrollToPath`
- [x] 2.4 textarea `onChange/onSelect/onKeyDown` 接线 mention 优先级(在 slash picker 之前判断)
- [x] 2.5 邮箱场景 `me@example.com` 不弹 popup 的边界处理(detectMention 严格要求 `@` 前为空白/换行/字符串起始)

## 3. Phase 3 — FileTree 滚动定位

- [x] 3.1 修改 `frontend/components/FileTree.tsx`:新增 `scrollToPath?: string | null` prop
- [x] 3.2 在 FileTree 内 `useEffect([scrollToPath])` 沿路径自顶向下展开父链 + `loadDir` 未加载的目录
- [x] 3.3 `requestAnimationFrame` 后 `document.querySelector('[data-path=...]').scrollIntoView({block:'nearest', behavior:'smooth'})`
- [x] 3.4 `page.tsx` 在 `mention.onCommit` 回调中 `setScrollToPath(file.path)`

## 4. 测试与验收

- [x] 4.1 后端 `uv run pytest tests/test_files_search.py -v` 全绿(33 passed)
- [x] 4.2 前端 `cd frontend && npm run typecheck` 通过(`lint` 仅有不相关的预先存在错误)
- [x] 4.3 本地启动全栈 `python start_local.py`(已在跑;手动重启 nanobot web 加载新端点)
- [x] 4.4 在测试工作区准备文件(本地真实 workspace 已含 100+ 文件)
- [x] 4.5 `curl 'http://localhost:18080/api/workspace/files?q=md&limit=3'` 返回真实命中,`q=zip` 返回 0(zip 已被排除)
- [x] 4.6 编写 `frontend/tests/e2e/mention.spec.ts`(8 个测试)+ `mention_screenshots.spec.ts`(1 个截图归档测试)
- [x] 4.7 e2e: `@` 触发后弹出 popup
- [x] 4.8 e2e: ↑↓ Tab Enter Esc 行为对齐 slash picker
- [x] 4.9 e2e: 选中后 textarea 显示 `@<相对路径> ` 并紧跟一个空格
- [x] 4.10 e2e: 选中后右侧预览区切到该文件
- [x] 4.11 e2e: 选中后左侧 FileTree 自动展开父目录、滚动到节点、节点高亮
- [x] 4.12 e2e: 邮箱 `me@example.com` 不触发 popup
- [x] 4.13 用 Playwright **有头模式**(`--headed`)运行 e2e 截图存档至 `/tmp/nanobot_e2e_mention/01..07_*.png`

## 5. 归档

- [x] 5.1 全部 tasks 完成后 `mv openspec/changes/mention-workspace-file openspec/changes/archive/`
