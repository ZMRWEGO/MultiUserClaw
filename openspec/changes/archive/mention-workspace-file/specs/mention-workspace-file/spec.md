## ADDED Requirements

### Requirement: 工作区文件搜索端点

后端 SHALL 提供 `GET /api/workspace/files?q=<string>&limit=<int>` 端点，返回工作区内**可预览（非 binary）**文件按匹配质量+修改时间排序的 top-N 候选，用于聊天输入框 `@` 提示。

响应载荷：
```typescript
{
  items: Array<{
    name: string;
    path: string;            // 相对工作区根的 POSIX 路径
    size: number;
    content_type: string;
    modified: string;        // ISO8601
    preview_kind: 'text' | 'markdown' | 'json' | 'image' | 'pdf' | 'html' | 'docx' | 'xlsx';
  }>;
  total: number;
  truncated: boolean;        // 命中数 > limit 时为 true
}
```

约束：
- `q` 长度 > 100 字符按 100 截断。
- `limit` 默认 20、最大 50、最小 1。
- 始终排除 `preview_kind == 'binary'` 的文件。
- 始终排除目录与隐藏文件/目录（以 `.` 开头）。
- 始终跳过噪音目录：`.git`、`node_modules`、`__pycache__`、`.venv`、`venv`、`dist`、`build`、`.next`。

#### Scenario: 名字前缀命中排在子串命中之前
- **WHEN** 工作区有 `docs/report.md` 与 `src/api/replicate.ts`，请求 `GET /api/workspace/files?q=rep`
- **THEN** `items[0].name == 'report.md'`（rank=0），`items[1].name == 'replicate.ts'`（rank=0），按 mtime desc 排

#### Scenario: 路径前缀命中
- **WHEN** 工作区有 `docs/foo.md` 与 `src/foo.md`，请求 `q=docs/`
- **THEN** 返回的 `items` 全部 `path` 以 `docs/` 开头

#### Scenario: fuzzy 匹配兜底
- **WHEN** 工作区有 `report.md`,请求 `q=rpt`
- **THEN** 仍返回 `report.md`（fuzzy rank=4），但低于任何前缀/子串命中

#### Scenario: 排除二进制文件
- **WHEN** 工作区有 `archive.zip` 与 `legacy.doc`,请求 `q=`(空)
- **THEN** `items` 不包含这两个文件

#### Scenario: 排除噪音目录
- **WHEN** 工作区有 `node_modules/lodash/index.js`,请求 `q=index`
- **THEN** `items` 不包含 `node_modules` 内的任何文件

#### Scenario: 排除隐藏文件
- **WHEN** 工作区有 `.env`、`.git/HEAD`,请求 `q=`(空)
- **THEN** `items` 不包含这两个文件

#### Scenario: 空 query 按 mtime desc 排序
- **WHEN** `q=`,工作区有 5 个文件,最近修改 `recent.md` 在 1 分钟前,其余在 1 小时前
- **THEN** `items[0].name == 'recent.md'`

#### Scenario: limit 截断与 truncated 标记
- **WHEN** 工作区有 25 个 `.md` 文件,请求 `limit=10`
- **THEN** `len(items) == 10`,`total == 25`,`truncated == true`

#### Scenario: limit 上限
- **WHEN** 请求 `limit=999`
- **THEN** 服务端按 50 处理(不报错)

#### Scenario: 路径穿越
- **WHEN** workspace 之外存在 `/etc/passwd`,无论 `q` 如何,工作区内不包含相关条目
- **THEN** `items` 不包含 workspace 之外的路径(POSIX 相对路径,无 `..`)

### Requirement: 聊天输入框 @ 提示菜单

前端 SHALL 在主聊天页面 `frontend/app/page.tsx` 的 textarea 输入 `@` 后,在输入框上方弹出文件候选菜单,候选来自后端 `GET /api/workspace/files`,行为对齐既有的 slash command picker。

约束:
- `@` 仅在 token 起始触发——`@` 前必须是空白(空格/换行/制表符)或字符串起始;邮箱场景 `me@example.com` 不触发。
- query = `@` 之后到光标位置的字符;query 中遇到换行/制表符立即关闭 picker;长度 > 100 关闭。
- 候选拉取 debounce 150ms。
- 键盘交互: ↑↓ 移动 pickIndex(wrap-around)、Tab/Enter 选中(非 composing 时)、Esc 关闭。
- 输入法 composing 期间不响应 Tab/Enter(`e.nativeEvent.isComposing` 检查)。

#### Scenario: @ 触发 popup
- **WHEN** 用户在 textarea 输入 `@rep`(光标在末尾),工作区有 `docs/report.md`
- **THEN** popup 出现并展示 `report.md` 在第一项,pickIndex=0

#### Scenario: 邮箱不触发
- **WHEN** 用户输入 `me@example.com`
- **THEN** popup 不出现

#### Scenario: ↑↓ 导航
- **WHEN** popup 显示 3 条,pickIndex=0,用户按 ↓
- **THEN** pickIndex=1

#### Scenario: ↑ wrap-around
- **WHEN** popup 显示 3 条,pickIndex=0,用户按 ↑
- **THEN** pickIndex=2

#### Scenario: Tab/Enter 提交选中项
- **WHEN** popup 显示 `report.md`(pickIndex=0),用户输入 `@rep` 后按 Enter
- **THEN** textarea 内容变成 `@docs/report.md `(尾部含空格),光标在空格后,popup 关闭

#### Scenario: Esc 关闭 popup
- **WHEN** popup 显示中,用户按 Esc
- **THEN** popup 关闭,textarea 内容不变

#### Scenario: 中文 composing 不吞 Enter
- **WHEN** popup 显示中,用户用中文输入法在 query 中编辑(`isComposing=true`),按 Enter 确认输入候选词
- **THEN** picker 不消费此 Enter,输入法正常落字

### Requirement: 选中候选后联动预览与文件树

选中文件后,前端 SHALL 同时:
1. textarea 内容中 `@<query>` 替换为 `@<相对路径> `(带尾部空格,光标停在空格后)。
2. 右侧预览区切到该文件(复用既有 `selectedFile` 状态)。
3. 左侧 FileTree 展开父目录链 + 滚动到节点 + 既有选中高亮(`isSelected = !isDir && item.path === selectedPath`)生效。

#### Scenario: 选中后预览切换
- **WHEN** 用户从 popup 选中 `docs/replan.md`
- **THEN** 右侧预览区开始加载 `docs/replan.md` 的预览内容

#### Scenario: 选中后文件树展开父目录
- **WHEN** 用户从 popup 选中 `src/components/FileTree.tsx`(`src/`、`src/components/` 此前未展开)
- **THEN** FileTree 中 `src/`、`src/components/` 自动展开,`FileTree.tsx` 节点滚动到可视区并高亮

#### Scenario: 选中后插入路径与光标位置
- **WHEN** 用户输入 `请看 @rep`(光标在末尾,position=8)从 popup 选中 `docs/report.md`
- **THEN** textarea.value = `请看 @docs/report.md `(注意 `report.md` 后含一个空格),光标位置 = 21(末尾)

### Requirement: 不修改消息内容

`@` 选中后插入到 textarea 的 `@<相对路径>` 文本 SHALL 仅作为普通字符串发送给后端,无任何特殊解析。AI 不会自动接收文件内容,如需读取应使用 `read_file` 工具。

#### Scenario: 发送消息不附带文件内容
- **WHEN** 用户发送 `修改 @docs/report.md 中的标题`
- **THEN** WebSocket 推送的 message payload 中 `content == "修改 @docs/report.md 中的标题"`,`attachments` 为空(无 file_id 注入)
