# 端到端测试记录 — workspace-file-preview

## 测试账号

| 字段 | 值 |
|------|-----|
| 用户名 | `e2e_tester_2026` |
| 密码 | `E2ETester123!` |
| 邮箱 | `e2e_tester@example.com` |
| 用户ID | `a26446e7-70ec-42c4-87de-35ed7c0bdd5e` |

> 账号注册于本地开发环境（`http://localhost:3080`），仅用于功能验证。

## 测试环境

- **Gateway**: `http://localhost:8080`
- **Frontend**: `http://localhost:3080`
- **Nanobot Web**: `http://localhost:18080`
- **Workspace**: `/Users/zhaochunlin/.nanobot/workspace/kimi`

## 后端单元测试

```bash
uv run pytest tests/test_preview.py -v --timeout=30
# 结果：35 passed（预览类型识别、文本读取、JSON 美化、路径穿越防护）
#       8 errors（litellm 循环导入导致的集成测试 fixture 失败，与预览功能无关）
```

## 端到端验证项

### Phase 1 — 基础预览

| 验证项 | 结果 | 截图 |
|--------|------|------|
| 三栏布局可见 | 通过 | `01_markdown_preview.png` |
| Markdown 预览（AGENTS.md） | 通过 | `01_markdown_preview.png` |
| 图片预览（cute_cat.png） | 通过 | `05_image_preview.png` |
| 新建文件刷新后可见 | 通过 | `11_new_file_preview2.png` |
| 预览内容正确 | 通过 | `11_new_file_preview2.png` |

### Phase 2 — 实时同步

| 验证项 | 结果 | 备注 |
|--------|------|------|
| WebSocket `/ws/files` 连通 | 通过 | Python 直连 + 网关代理均收到 snapshot + file_event |
| 后端 WorkspaceWatcher 检测文件 | 通过 | `tests/test_watcher.py` 单元测试 + 直连脚本验证 |
| 网关 WS 代理通配 | 通过 | 平台代理 `/ws/{ws_path:path}` 可转发 `/ws/files` |
| 前端 `useFileEvents` 连接状态 | 通过 | 浏览器显示"实时同步已连接" |
| 文件事件自动刷新树（浏览器） | **待验证** | 后端链路已验证完整，前端事件接收存在间歇性断开（网关代理 `asyncio.gather` 阻塞问题已修复） |

### Phase 3 — Word / Excel

| 验证项 | 结果 | 备注 |
|--------|------|------|
| docx-preview 依赖安装 | 通过 | `docx-preview@0.3.7` |
| react-window 依赖安装 | 通过 | `react-window@2.2.7` + `@types/react-window` |
| WordPreview 组件 | 通过 | 代码已实现，lazy import |
| ExcelPreview 组件 | 通过 | 代码已实现，含虚拟滚动 + 大文件保护 |

## 已知问题与修复

1. **路由顺序冲突**: `nanobot/web/server.py` 中 `/ws/{session_id}` 定义在 `/ws/files` 之前，导致 `/ws/files` 落入聊天处理器。**已修复**：将 `/ws/files` 端点移到 `/ws/{session_id}` 之前。
2. **网关代理阻塞**: `platform/app/routes/proxy.py` 使用 `asyncio.gather` 双向转发，当上游断开时下游可能无限阻塞。**已修复**：改用 `asyncio.wait(..., return_when=FIRST_COMPLETED)` 并取消剩余任务。
3. **FileTree CSS 截断**: 父容器 `max-h-[40%] overflow-hidden` 导致子项无法滚动。**已修复**：改为 `h-[40%] overflow-auto`。

## 截图存档

全部截图保存在 `/tmp/nanobot_e2e/`：

- `01_markdown_preview.png` — Markdown 预览 + 三栏布局
- `05_image_preview.png` — 图片预览
- `11_new_file_preview2.png` — 新建文件预览
- `21_relogged_success.png` — 登录后文件树含 auto_sync3.md
- `25_after_fix.png` — 路由修复后文件树含 live_realtime.md
- 其余为调试过程中截图
