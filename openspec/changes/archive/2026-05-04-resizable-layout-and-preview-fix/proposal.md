## Why

当前主页面 (`/`) 的三栏布局存在可用性缺陷：左侧会话列表占用固定 256px 宽度且无法收起，在窄屏或需要聚焦对话时浪费空间；右侧工作区采用上下布局（文件树在上、预览在下），文件树高度被硬编码为 40%，预览区高度受限，且两者均不支持拖动调整尺寸；Excel 等预览组件存在表头不固定、内容溢出容器等问题，严重影响文件浏览体验。

## What Changes

- **左侧会话栏可收起**：在左栏添加 toggle 按钮，支持收起为窄条（仅显示新建对话图标），展开时恢复 w-64；收起状态持久化到 localStorage。
- **右侧工作区改为左右布局 + 可拖动调整大小**：将右栏内部从上下布局改为左右布局（左侧文件树、右侧文件预览）；左右之间引入可拖动分隔条（resizer）调整宽度；文件树与预览区支持垂直拖动调整高度。
- **预览组件布局修复**：
  - Excel 预览：小表格使用 `<table>` 并将表头放入 `<thead>`，支持 `position: sticky` 固定表头；表格在容器内滚动，不溢出外层布局；大表格继续使用 `react-window` 虚拟化。
  - PDF 预览：iframe 在容器内自适应缩放，不溢出。
  - Word 预览：docx-preview 渲染内容自适应容器宽度。
  - HTML 预览：iframe sandbox 自适应容器大小。
- **对话框动态缩小**：中间聊天区在右侧面板展开时保持合理宽度，避免消息气泡过于拥挤；设置中间区域最小宽度，窗口过小时优先压缩工作区。
- **端到端测试**：使用 Playwright 编写 e2e 测试，覆盖左侧栏收起/展开、工作区拖动调整大小、Excel/PDF/Word/HTML 预览在容器内正常显示且不溢出。

## Capabilities

### New Capabilities
- `resizable-sidebar`: 左侧会话栏收起/展开能力，支持状态持久化。
- `resizable-workspace-panels`: 右侧工作区左右/上下拖动调整面板大小能力。
- `preview-layout-fix`: 文件预览组件（Excel/PDF/Word/HTML）布局修复，确保内容在容器内自适应、不溢出、表头固定。

### Modified Capabilities
<!-- 无现有 spec 需要修改 -->

## Impact

- **前端**：
  - 修改 `frontend/app/page.tsx`（三栏布局重构、收起状态管理）。
  - 修改 `frontend/components/FileTree.tsx`（适配左右布局）。
  - 修改 `frontend/components/FilePreview/index.tsx` 及各子组件（ExcelPreview、PdfPreview、HtmlPreview、WordPreview）修复布局溢出。
  - 可能新增 `frontend/components/ResizablePanel.tsx` 或直接使用 `react-resizable-panels`。
  - 新增 e2e 测试目录（Playwright）。
- **依赖**：确认并安装 `react-resizable-panels`（若未安装）；安装 `@playwright/test` 用于 e2e 测试。
- **API**：无后端 API 变更。
- **风险**：`react-resizable-panels` 与现有 Tailwind 布局的兼容性需验证；Playwright 测试需准备 fixture 文件（.xlsx、.pdf、.docx、.html）。
