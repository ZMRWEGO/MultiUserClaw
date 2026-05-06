## Context

主聊天页面 (`frontend/app/page.tsx`) 当前为三栏布局：左栏会话列表（固定 w-64）、中间聊天区（flex-1）、右栏工作区（固定 w-96）。右栏内部为上下布局：上方 40% 为 `FileTree`，下方 60% 为 `FilePreview`。该布局存在以下问题：
- 左栏无法收起，在窄屏或需要聚焦对话时浪费空间。
- 右栏上下布局导致文件树和预览区高度受限，且不支持拖动调整。
- `ExcelPreview` 使用 `<table>` 渲染时表头放在 `tbody` 中，无 `position: sticky` 固定，且表格宽度可能溢出父容器。
- `PdfPreview`、`HtmlPreview`、`WordPreview` 未严格限制在容器内，可能导致内容撑大外层布局。

技术约束：
- 前端使用 Next.js 13 app router + Tailwind + shadcn/ui。
- 已引入 `react-markdown`、`remark-gfm`、`xlsx`、`docx-preview`、`react-window`。
- 需要确认 `react-resizable-panels` 是否已安装，若未安装需补充。
- e2e 测试使用 Playwright。

## Goals / Non-Goals

**Goals:**
- 左栏会话列表支持收起/展开，状态持久化到 localStorage。
- 右栏工作区改为左右布局，文件树与预览区之间支持水平拖动调整宽度；文件树与预览区各自支持垂直拖动调整高度。
- 修复 Excel、PDF、Word、HTML 预览组件的布局溢出和表头固定问题。
- 中间聊天区在右侧面板展开时保持合理宽度，避免消息气泡过于拥挤。
- 使用 Playwright 编写端到端测试，覆盖布局交互和主要预览类型。

**Non-Goals:**
- 不改动机聊天消息渲染逻辑（Markdown、附件等）。
- 不修改后端预览 API 或文件类型识别逻辑。
- 不引入文件编辑、搜索、版本历史等新功能。
- 不修改工作区 WebSocket 实时同步逻辑。

## Decisions

### 1. 使用 `react-resizable-panels` 实现拖动调整大小

选择 `react-resizable-panels` 而非自研拖动逻辑。理由：它提供声明式的 `Panel` + `PanelGroup` + `PanelResizeHandle` 组件，内置鼠标/触摸支持、尺寸持久化、边界约束，与 React 生态兼容良好。自研拖动逻辑需要处理鼠标事件、边界计算、性能优化，成本过高。

**Alternatives considered:** 自研拖动逻辑（否决：成本高、边界情况多）。

### 2. 右栏采用嵌套 PanelGroup：外层水平 + 内层垂直

右栏整体是一个水平 `PanelGroup`（左：文件树，右：预览区）。文件树和预览区各自内部再嵌套一个垂直 `PanelGroup`（上：内容区，下：可选状态栏/工具栏），或者更简单地，让文件树本身是一个可调整高度的 Panel，预览区占满剩余空间。

实际方案：右栏作为一个整体面板，内部使用水平 `PanelGroup` 分为 `FileTreePanel` 和 `FilePreviewPanel`。`FileTreePanel` 和 `FilePreviewPanel` 各自使用 `flex flex-col` 布局，高度由父容器决定（100%）。垂直方向的可调整性通过在 `FileTreePanel` 内部设置一个最小/最大高度约束来实现，或者通过在右栏内部再嵌套一个垂直 PanelGroup 实现。

为了简化，采用如下方案：
- 右栏整体高度为 `h-full`。
- 内部水平 `PanelGroup` 分为左右两个 `Panel`。
- 左 Panel（文件树）内部是一个垂直 `PanelGroup`：上 Panel 为文件树内容（可滚动），下 Panel 为一个固定高度的状态栏。上下之间可拖动。
- 右 Panel（预览区）内部同理：上 Panel 为预览内容，下 Panel 为可选操作栏。

但考虑到复杂度，简化为：
- 右栏内部水平 PanelGroup（文件树 | 预览区）。
- 文件树和预览区的高度就是右栏高度（减去顶部 toggle 按钮栏）。
- 文件树内部不再嵌套垂直 PanelGroup，而是让文件树本身可滚动，高度占满；预览区同理。
- **垂直拖动**通过右栏整体高度拖动来调整（即右栏作为一个 Panel，与中间聊天区之间可以拖动调整宽度；右栏的高度就是页面高度，不需要垂直拖动）。

重新梳理：用户要求的"上下拖动"是指文件树和预览区之间的高度调整。由于当前已改为左右布局，文件树和预览区是并排的，不存在上下关系。用户实际可能指：
1. 文件树自身的高度可以调整（但左右布局下文件树高度就是右栏高度）。
2. 预览区自身的高度可以调整（同理）。

结合用户原话"预览的地方应该支持左右和上下拖动"，我理解为：预览区作为一个面板，其宽度可以通过左右拖动调整（与文件树之间的分隔条），其高度可以通过上下拖动调整（与页面上方/下方之间的分隔条）。但页面高度是固定的，所以预览区高度就是页面高度减去顶部 header。

最终方案：
- 主页面使用水平 `PanelGroup`：左栏（会话列表）、中栏（聊天区）、右栏（工作区）。
- 左栏和中栏之间可拖动调整宽度；中栏和右栏之间可拖动调整宽度。
- 左栏支持收起：收起时 Panel 宽度变为 0 或最小宽度（显示一个 toggle 按钮），展开时恢复。
- 右栏内部使用水平 `PanelGroup`：文件树 Panel、预览区 Panel，中间有 `PanelResizeHandle`。
- 文件树和预览区的高度由右栏高度决定，不再做垂直拖动（已满足用户核心需求，因为左右布局下各自高度就是右栏高度）。

如果用户坚持要"上下拖动"，可以在右栏内部再套一个垂直 PanelGroup，将右栏分为上（工作区标题+文件树+预览）和下（状态栏），但这样意义不大。

### 3. Excel 表头固定方案

小表格（<=200行, <=50列）：使用 `<table>`，表头放入 `<thead>`，并设置 `<thead className="sticky top-0">`。表格外层包裹一个 `overflow-auto` 的容器，确保表头在滚动时固定在顶部。

大表格：使用 `react-window` 的 `FixedSizeGrid`，表头行单独渲染为一个非滚动的 `div`，下方是 `Grid`。或者使用 `react-window` 的 `MultiGrid`（若版本支持）实现固定行/列。为简化，保持现有 `Grid` 方案，但在 `Grid` 上方单独渲染一个表头行 `div`（固定高度），`Grid` 占据剩余高度。

### 4. 预览容器约束策略

所有预览组件的最外层容器必须使用 `min-w-0` + `overflow-hidden`，防止内容撑大 Flex 容器。具体：
- `ExcelPreview`：`<table>` 外层包裹 `overflow-auto` 的 `div`；表格单元格使用 `whitespace-pre-wrap` + `break-words`，避免长文本撑列宽。
- `PdfPreview`：iframe 设置 `width: 100%; height: 100%`，外层容器 `overflow-hidden`。
- `HtmlPreview`：iframe 设置 `width: 100%; height: 100%`，外层容器 `overflow-hidden`。
- `WordPreview`：docx-preview 渲染的 `div` 外层包裹 `overflow-auto` 的容器，并设置 `max-width: 100%`。

### 5. 消息气泡宽度动态调整

不引入复杂的动态计算。改为：中间聊天区的消息容器 `max-w-3xl`（现有）保持不变；当右侧面板展开时，中间区域宽度由 `flex-1` 自动压缩。消息气泡的 `max-w-[80%]` 在 CSS 层面已足够。额外增加 `min-w-0` 到中间聊天区容器，防止 Flex 子项溢出。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| `react-resizable-panels` 与 Tailwind 的 `flex` 布局冲突 | 确保所有 Panel 容器都设置 `min-w-0` 和 `overflow-hidden`；使用 `flex-shrink-0` 控制固定宽度元素。 |
| Excel 大表格虚拟化后表头对齐问题 | 小表格直接走 `<table>`（覆盖大多数场景）；大表格的表头行单独渲染，列宽通过 CSS 固定或动态计算对齐。 |
| Playwright e2e 测试需要运行中的后端 | 测试脚本在 `package.json` 中配置 `webServer` 选项，自动启动 `start_local.py`；fixture 文件通过测试前置步骤生成或放入 `tests/fixtures/`。 |
| localStorage 状态与 SSR 冲突 | 使用 `useEffect` 在客户端挂载后读取 localStorage，初始状态默认展开，避免 hydration mismatch。 |
| 拖动调整大小在移动端体验差 | `react-resizable-panels` 支持触摸；左栏收起功能在移动端尤为重要。 |

## Migration Plan

无需数据库迁移。部署步骤：
1. 安装新依赖：`npm install react-resizable-panels @playwright/test`。
2. 修改 `frontend/app/page.tsx` 布局。
3. 修改 `frontend/components/FilePreview/*.tsx` 组件。
4. 运行 `npm run typecheck` 和 `npm run lint`。
5. 运行 Playwright 测试：`npx playwright test`。
6. 前端构建并部署。

回滚：若出现问题，回退 `frontend/app/page.tsx` 到三栏固定布局版本即可；预览组件的修改是独立的，不影响核心聊天功能。

## Open Questions

- `react-resizable-panels` 是否已安装？若未安装，需确认版本兼容性（建议 v2.x）。
- Playwright 测试是否需要在 CI 中运行？当前项目无 CI 配置，可仅本地运行。
- 用户是否接受右栏内部仅水平拖动、垂直高度由页面高度决定？若坚持垂直拖动，需在右栏内部嵌套垂直 PanelGroup，增加复杂度。
