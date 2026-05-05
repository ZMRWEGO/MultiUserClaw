# 端到端测试记录 — mention-workspace-file

## 测试账号

复用 `docs/e2e-test-account.md` 中的同一账号。

| 字段 | 值 |
|------|-----|
| 用户名 | `e2e_tester_2026` |
| 密码 | `E2ETester123!` |
| 邮箱 | `e2e_tester@example.com` |

## 测试环境

- **Gateway**: `http://localhost:8080`
- **Frontend**: `http://localhost:3080`
- **Nanobot Web**: `http://localhost:18080`
- **Workspace**: `/Users/zhaochunlin/.nanobot/workspace/kimi`

## 后端单元测试

```bash
uv run pytest tests/test_files_search.py -v --timeout=30
# 33 passed in 0.15s
```

涵盖维度:
- `_fuzzy_match`(8 个 parametrize)
- `_match_score`(name 前缀 / name 子串 / path 前缀 / path 子串 / fuzzy / 不命中 / 大小写)
- `search_workspace_files`(可预览/二进制排除/隐藏排除/噪音目录排除/前缀排序/路径前缀/fuzzy/空 query+mtime/limit clamp/truncated/路径相对性/响应 shape/大小写不敏感/缺失工作区/symlink 循环防护)

## 前端 E2E 测试(Playwright 有头模式)

```bash
cd frontend && npx playwright test mention.spec.ts mention_screenshots.spec.ts --headed --workers 1
# 9 passed in ~30s
```

`tests/e2e/mention.spec.ts` 8 个用例:

| 用例 | 验证 |
|------|------|
| `@ triggers popup and lists candidates` | `@` 弹出 popup,5 条 mock 文件 |
| `typing query filters candidates` | `@rep` 过滤为 3 条(report/replan/replicate) |
| `email me@example.com does NOT trigger popup` | 邮箱前置 `me` 抑制 popup |
| `Escape closes popup` | Esc 关闭,textarea 内容不变 |
| `Enter selects highlighted item; textarea gets @<path> with trailing space` | 文本变 `请看 @<path> ` 带尾空格 |
| `ArrowDown changes selection then Tab picks the second item` | ↓ + Tab 选中第 2 条 |
| `selecting a file switches right-side preview` | 选中后预览区切到该文件 |
| `selecting a deep file expands FileTree parent dirs and highlights node` | 深路径文件树展开 + 高亮 |

## 截图存档

`/tmp/nanobot_e2e_mention/` 下:

- `01_initial_layout.png` — 三栏初始布局
- `02_at_triggers_popup.png` — 输入 `@` 后 popup 出现
- `03_filtered_by_rep.png` — `@rep` 过滤后候选
- `04_after_arrowdown.png` — ↓ 切换 pickIndex
- `05_after_enter_committed.png` — Enter 选中后 textarea 含 `@<path> ` + 预览区切换
- `06_email_no_popup.png` — `me@example.com` 不触发 popup
- `07_filetree_expanded.png` — 选中深路径文件后 FileTree 自动展开父链 + 节点高亮

## 现网手动验证

```bash
curl -s 'http://localhost:18080/api/workspace/files?q=&limit=5' | jq .
# 返回最近修改的 5 个可预览文件,total=111,truncated=true

curl -s 'http://localhost:18080/api/workspace/files?q=md&limit=3' | jq .
# 返回最近修改的 3 个 markdown,name 含 'md'

curl -s 'http://localhost:18080/api/workspace/files?q=zip' | jq .
# items=[] —— zip 文件被 binary 过滤排除
```

## 已知限制(不影响验收)

1. 文件名含空格(如 `my doc.md`)用户输入不能带空格命中——detectMention 在空格处终止 query。属于本期已知限制(技术方案 §7 已声明)。
2. 重复 `@` 同一文件不会再次触发 scrollToPath——`useEffect` 依赖比较时不会重复 trigger。后续如需可加 nonce state(本期不做)。
3. 平台 gateway 启动时已经在跑,本次更改未触及 gateway 路由,自动透传 `/api/nanobot/workspace/files` 无需重启 gateway。
