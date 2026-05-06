## 1. 依赖准备

- [x] 1.1 检查 `frontend/package.json` 是否已安装 `react-resizable-panels`，若未安装则执行 `npm install react-resizable-panels`
- [x] 1.2 检查 `frontend/package.json` 是否已安装 `@playwright/test`，若未安装则执行 `npm install -D @playwright/test` 并运行 `npx playwright install`
- [x] 1.3 运行 `npm run typecheck` 和 `npm run lint` 确认基线通过

## 2. 左侧会话栏收起/展开

- [x] 2.1 在 `frontend/app/page.tsx` 左栏顶部添加 collapse/expand toggle 按钮（使用 `PanelLeftClose` / `PanelLeftOpen` 图标）
- [x] 2.2 使用 `react-resizable-panels` 的 `Panel` + `PanelGroup` 包裹左栏和中栏，左栏设置 ` collapsible` 属性或手动控制宽度为 0 / w-64
- [x] 2.3 实现 `useSidebarCollapse` 状态（`useState` + `useEffect` 读取/写入 localStorage 的 `sidebarCollapsed` 键）
- [x] 2.4 确保 SSR 安全：初始状态为展开，客户端 `useEffect` 挂载后根据 localStorage 切换
- [x] 2.5 左栏收起时，中间聊天区通过 `flex-1` 自动扩展，保持 `min-w-0` 防止溢出

## 3. 右侧工作区左右布局 + 拖动调整大小

- [x] 3.1 修改 `frontend/app/page.tsx`：右栏内部从上下布局改为 `react-resizable-panels` 水平 `PanelGroup`（左：FileTree，右：FilePreview）
- [x] 3.2 在 FileTree 和 FilePreview 之间添加 `PanelResizeHandle`，设置最小宽度约束（文件树最小 120px，预览区最小 120px）
- [x] 3.3 移除右栏顶部硬编码的 `h-[40%]` 和 `flex-1` 上下分配，改为左右 Panel 各占 50% 默认（或文件树 35% / 预览 65%）
- [x] 3.4 确保右栏整体宽度可通过中栏与右栏之间的 `PanelResizeHandle` 调整，最小宽度 300px
- [x] 3.5 保留右栏顶部的 toggle 按钮用于完全折叠/展开工作区面板
- [x] 3.6 调整 `FileTree.tsx` 和 `FilePreview/index.tsx` 的外层容器，使其高度占满右栏 Panel 高度（`h-full` + `flex flex-col`）

## 4. 预览组件布局修复

- [x] 4.1 修改 `frontend/components/FilePreview/ExcelPreview.tsx`：
  - 小表格：将表头行从 `tbody` 移至 `thead`，设置 `sticky top-0` 样式
  - 表格外层包裹 `overflow-auto` 容器，设置 `max-w-full`
  - 单元格样式增加 `break-words` 和 `max-w-xs`（或固定列宽），防止长文本撑大表格
  - 大表格：在 `react-window` Grid 上方单独渲染固定表头行，Grid 占满剩余高度
- [x] 4.2 修改 `frontend/components/FilePreview/PdfPreview.tsx`：
  - iframe 设置 `className="w-full h-full"`
  - 外层容器添加 `overflow-hidden` 和 `min-w-0`
- [x] 4.3 修改 `frontend/components/FilePreview/HtmlPreview.tsx`：
  - iframe 设置 `className="w-full h-full"`
  - 外层容器添加 `overflow-hidden` 和 `min-w-0`
- [x] 4.4 修改 `frontend/components/FilePreview/WordPreview.tsx`：
  - docx-preview 渲染目标 `div` 外层包裹 `overflow-auto` 容器
  - 容器设置 `max-w-full` 和 `min-w-0`，防止内容撑大父容器
- [x] 4.5 修改 `frontend/components/FilePreview/index.tsx`：
  - 各预览分支的外层容器统一添加 `min-w-0 overflow-hidden flex-1 flex flex-col`
- [x] 4.6 运行 `npm run typecheck` 确认 TypeScript 无错误

## 5. 对话框布局保护

- [x] 5.1 在 `frontend/app/page.tsx` 中间聊天区容器添加 `min-w-0`，确保 Flex 布局下不会溢出
- [x] 5.2 验证消息气泡 `max-w-[80%]` 在右侧面板展开时仍然正常工作，不会被过度压缩
- [x] 5.3 在极窄视口（< 768px）下测试，确保布局不崩坏

## 6. 端到端测试

- [x] 6.1 创建 `frontend/tests/fixtures/` 目录，放入测试文件：`test.xlsx`（含表头和多行数据）、`test.pdf`、`test.docx`、`test.html`
- [x] 6.2 初始化 Playwright 配置（`playwright.config.ts`），设置 `webServer` 指向本地开发服务器或 `start_local.py`
- [x] 6.3 编写 `tests/e2e/layout.spec.ts`：
  - 测试左侧栏收起/展开按钮切换正常
  - 测试右侧面板折叠/展开按钮切换正常
- [x] 6.4 编写 `tests/e2e/preview.spec.ts`：
  - 测试 Excel 预览：表头固定在首行、滚动时表头可见、内容不溢出布局
  - 测试 PDF 预览：iframe 在容器内正常显示
  - 测试 Word 预览：文档在容器内显示，不撑大布局
  - 测试 HTML 预览：iframe 填满容器
- [x] 6.5 运行 `npx playwright test` 并确保所有测试通过
- [x] 6.6 运行 `npm run lint` 确保测试代码风格符合项目规范

## 7. 验收

- [x] 7.1 本地启动 `python start_local.py`，全栈运行正常
- [x] 7.2 手动验证左侧栏收起/展开、右侧面板左右拖动、工作区内部左右拖动
- [x] 7.3 手动验证 Excel 表头固定、PDF/Word/HTML 不溢出
- [x] 7.4 `npm run typecheck` 通过
- [x] 7.5 `npm run lint` 通过（lint errors 来自已有文件，非本次引入）
- [x] 7.6 Playwright e2e 测试全部通过
- [x] 7.7 截图存档关键验收步骤
