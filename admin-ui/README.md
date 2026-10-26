# 心语网页

React + TypeScript + Vite。页面只包含恋爱助手：

| 页面 | 地址 |
| --- | --- |
| 聊一聊 | `/assistant` |
| 关于 TA | `/assistant/memories` |
| 重要事项 | `/assistant/reminders` |
| 约会规划 | `/assistant/dates` |
| 偏好与设置 | `/assistant/settings` |

旧地址或未知地址跳转到助手首页。`/api` 由 Vite 转发到 `http://127.0.0.1:8000`。页面需要 API 与独立 Worker 同时运行。

```text
pnpm install --frozen-lockfile
pnpm dev
pnpm lint
pnpm build
```

共享接口类型和请求在 `src/relationshipApi.ts`，页面在 `src/pages/RelationshipPage.tsx`，样式在同目录 CSS，图标在 `src/components/Icon.tsx`。
