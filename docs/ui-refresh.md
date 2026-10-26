# 前端视觉更新

日期：2026-10-26。

## 已实现

- 深色固定侧栏、银灰画布、磨砂顶栏，以及统一的蓝色操作按钮。
- 项目首页使用独立的研究主题主视觉、SVG 轨道与玻璃质感核心图形；装饰图不表示实际执行记录。
- 项目列表保留真实数据库来源，增加按名称/编号/描述搜索、最近创建/名称排序、无结果状态与加载占位。
- 项目工作台突出 Agent 入口，数据、比较与偏好使用独立卡片导航。
- 对话页增加助手标题栏、引导问题和统一消息、工具记录、输入框样式。
- 文件表格、比较结果/图表、偏好和系统状态页面统一配色、字重、间距、边框与圆角。
- 适配桌面及 390px 窄屏；尊重 prefers-reduced-motion，保留键盘焦点与跳至主要内容入口。

## 主要文件

- `admin-ui/src/index.css`：字体、颜色变量、全局基础样式。
- `admin-ui/src/App.css`：布局、组件和响应式样式。
- `admin-ui/src/components/AppShell.tsx`：全局导航、项目快捷导航、顶栏。
- `admin-ui/src/components/Icon.tsx`：统一 SVG 图标。
- `admin-ui/src/components/ResearchVisual.tsx`：首页装饰图形。
- `admin-ui/src/pages/ProjectsPage.tsx`：首页、搜索与排序。
- `admin-ui/src/pages/ProjectOverviewPage.tsx`：项目工作台入口。

本批没有增加第三方前端依赖，没有更改模型调用、比较计算或偏好保存接口。ESLint、TypeScript 与 Vite 构建通过；没有运行 pytest 套件。

## 实际预览

- 浏览器确认首页、创建项目页、P001 工作台和已有 Agent 会话可以正常显示。
- 搜索 P001 显示 1 个匹配项目，清空恢复 2 个项目；名称排序可以使用。
- 390px 预览中修正了首页标题断行与导航拥挤；尊重减少动态效果设置。
- 聊天页压缩顶部间距，并按窗口高度调整消息区，让输入框更容易出现在首屏。
- 当前视图截图保存在本机 `.runtime/ui-preview/home-desktop.jpg` 与 `chat-desktop.jpg`，该目录不入 Git。

预览期间将 Docker 存储重新指向原有 `D:\DevTools\DockerData\DockerDesktopWSL` 磁盘。P001、MEMORY_DEMO、原实验文件、会话和偏好已通过接口读取确认；没有替换或删除虚拟磁盘。原有嵌套目录仍保留。
