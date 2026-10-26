# 项目偏好开发记录

日期：2026-10-25。此批次完成 Day 4 的可修订偏好模块；不代表 Day 4 的登录和图中断恢复已完成。

## 使用入口

- 项目内“项目偏好”导航：`/projects/:projectId/memories`，可设置、修改、停用、刷新和查看最近 50 个版本。
- Agent 对话中明确要求记住偏好，例如“记住，这个项目默认基线是 R001，回复请简短”。
- 新会话和后续模型调用从数据库读取最新值，项目之间隔离；不依赖浏览器 localStorage。

## 代码对应

| 层 | 文件 | 职责 |
|---|---|---|
| 数据 | `app/models/memory.py` | 修订、是否生效、被替代版本、真实来源消息和工具动作去重 |
| 迁移 | `migrations/versions/c60bdf0f37ba_add_revisioned_project_preferences.py` | 创建 project_memories |
| 业务 | `app/services/memory_service.py` | 项目锁、版本冲突、保存/停用、有限键值验证、召回时隐藏停用值 |
| 接口 | `app/routers/memories.py` | 列表、保存、停用、历史 |
| 工具 | `app/graph/tools/experiments.py` | get_project_preferences、revise_memory |
| 模型输入 | `app/graph/subgraphs/generation.py` | 每次模型调用前重新加载生效偏好，工具保存后立即可读取新值 |
| 页面 | `admin-ui/src/pages/MemoryPage.tsx` | 四项偏好编辑、冲突刷新、来源与历史 |

支持 default_baseline、preferred_metric、response_length、report_language。回复长度只支持简短/适中/详细，语言只支持中文/英文；第一版不自动抽取任意研究结论，也不保存模型密钥或文件路径。

## 修订语义

- 首次设置 expected_revision=0；后续修改/停用提交当前最新 revision。
- 对项目行加锁，避免两个同时创建的请求相互覆盖；同项目同键同 revision 有数据库唯一约束。
- 修订创建新记录，通过 supersedes_id 关联旧记录，旧记录停止生效；停用创建不生效的新版本。
- 停用是停止偏好召回，原始聊天、旧工具消息和修订审计仍保留，不等于删除所有历史文本。
- 同 AgentRun/tool_call_id 去重，工具重放不重复增加偏好版本。
- Agent 来源必须为当前执行对应的真实用户消息；偏好值须出现在逐字来源引用中，并包含明确偏好表达。后端拒绝常见否定保存表达；复杂自然语言意图仍依赖模型理解，用户可在页面纠正。
- 当前用户明确要求优先于默认偏好；保存基线不代表该基线存在于所有文件，比较时仍要读取并验证当前文件。

## 完成范围与后续

已执行数据库迁移和前端构建、代码静态检查。另修正了 ECharts 的 use 导入名称与 React Hooks 检查规则的冲突，注册图表时改用 registerCharts 别名。遵照用户要求不新增或运行 pytest 套件。

## 实际功能记录

演示项目 MEMORY_DEMO，不包含真实研究数据。

- 保存会话：eff440db-1ac3-4321-806f-3757c13943c0。
- 保存执行：d9459075-b707-4603-8f61-02b9a8275ed4，glm-5.3-flash，succeeded，16421 ms。
- 当前用户明确要求保存默认基线 R001、回复简短；模型真实调用 get_project_preferences 和两次 revise_memory。
- 两项偏好均为 revision=1、source=agent，关联来源消息 25d22579-5480-44f6-ab00-72736a1c228d。
- 新会话：57016121-cdb5-426c-9564-1752f7f6b625。
- 读取执行：6eb07ca6-3833-4646-b1d3-c163d5fa88c0，succeeded，9157 ms；模型真实调用 get_project_preferences，答复默认基线 R001、回复长度简短。
- 演示项目额外走通关注指标 RMSE（v1）→ MAE（v2）→ 停用（v3）；历史保留 3 条，携带旧版本的修改返回 409 REVISION_CONFLICT。
- P001 的偏好仍为空，演示偏好没有跨项目带入。
- 前端偏好页地址及经 Vite 代理的偏好接口均返回 200。

本地入口：`http://localhost:5173/projects/MEMORY_DEMO/memories`；记录依赖当前本地数据库，不会随空数据库自动创建。

单用户登录、图 interrupt/resume、后台任务、提醒与 SSE 不在本批次完成范围。没有进行浏览器截图验收或 API 重启后的恢复演示。
