# Agent 接入记录

日期：2026-10-24。

## 已实现

- GLM-5.3-Flash 真实调用，LangGraph 编排模型与工具循环。
- 三个工具：get_project_context、list_runs、compare_runs。
- PostgreSQL 保存会话、用户/助手消息、执行状态、事件及比较证据。
- 独立 PostgreSQL schema 保存 LangGraph checkpoint，保留工具调用所需的原始消息字段。
- 独立 Agent 对话页；可选择文件、新建/切换会话、读取执行进度、恢复消息并查看结构化比较卡片。
- 每轮工具上限 6 次、暂时性模型重试全轮最多 2 次、会话并发控制、请求和比较动作去重。

## 一条实际执行记录

使用已有合成文件，不将记录当作真实研究结果。

- 项目：P001。
- 文件：runs_demo.csv，artifact_id 为 5da4adbc-efcd-479f-a6f1-cf1c40400ced。
- 会话：21411ad7-0634-45df-8be9-2fd50a79b03c，标题为“Agent 工具调用演示”。
- 执行：3ba83f9d-6f4a-4187-aa3f-c813b8e70196。
- 模型：glm-5.3-flash。
- 用户消息：请调用工具比较当前文件中的 R001 和 R002，以 R001 为基线，说明改善率和来源行号。
- 模型实际产生 compare_runs 工具调用，tool_call_id 为 call_-7142428255587398995。
- 持久化事件：tool.started（sequence 5）→ tool.finished（sequence 6，ok=true）。
- 状态：succeeded；后端记录耗时 13188 ms；保存 1 条比较证据。
- 结果：RMSE 0.50 → 0.45，改善量 0.05，改善率 10.0%；来源为 CSV 第 2、3 行。
- 用户和助手消息各 1 条，可通过会话接口恢复；active_run_id 已清空。

这是实际模型 API 和真实比较服务执行的结果；没有使用硬编码回复或测试模型替代。该轮对话可在当前本地数据库中查看，重建空数据库后这些演示 ID 不会自动存在。

## 本轮验证范围

完成业务表迁移、checkpointer 初始化、真实模型比较调用、消息读取和前端构建。按用户要求没有运行 pytest 测试套件，也没有将所有规划场景标记为通过。

## 后续范围

长期偏好记忆、LangGraph interrupt/resume 的用户流程、登录、后台任务、待办/提醒、SSE、CI 和完整部署尚待开发。当前多轮上下文是会话短期状态，不能称为已经实现长期用户记忆。
