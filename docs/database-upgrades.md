# 数据库升级与归档

当前迁移版本为 `b15e60d7f945`。执行 `uv run alembic upgrade head` 后，默认 `public` schema 仅保留心语业务表与 Alembic 版本记录。账号升级增加会话公开 ID、设备信息及账号安全事件，RAG 升级增加索引 outbox，保留原账号和业务记录。

新增 `xinyu_graph` schema 保存心语的 LangGraph 检查点；运行 `uv run python -m scripts.prepare_ai` 初始化框架自身表结构。该 schema 与退役的科研归档完全隔离，需纳入当前应用备份。Qdrant 使用独立 `xinyu_vectors` 卷，向量可以从原文重建，详见 [RAG与LangGraph](./rag-langgraph.md)。

退役模块已经从应用代码、页面、工具和依赖中移除。原有九张业务表移动到独立的 `research_archive` schema，原状态库移动到 `research_checkpoints_archive`。

按照用户的数据存放要求，原私有附件现已放回 `D:\Study\简历\ResearchMate\.runtime\artifacts`，数据库备份及可查看的CSV记录位于该旧目录的 `.runtime/database/20261026-220749/`。附件移动前后SHA-256一致；PostgreSQL备份包含上述两个schema的13张表，并通过 `pg_restore --list` 检查。心语不会读取该目录。

归档不删除原始记录，应用没有归档查询接口，也不把它们加入恋爱助手的检索范围。升级时已核对九张表的行数，迁移前后保持一致。归档不进入Git。

保留已应用的历史迁移文件，以便现有数据库能够沿版本链升级。这些文件属于升级兼容记录，不是可用的业务模块。不要直接删除旧迁移、手工改版本号或清空数据卷。

如需恢复归档结构，可在停用应用并备份数据库后，按迁移脚本的 `downgrade` 反向移动表。已经对外发送的通知不会因数据库回退而撤销。

PostgreSQL连接身份及既有数据卷保留现有环境中的兼容标识。Redis现在用于心语账号验证码、限流、失败锁定与会话缓存/撤销，Compose使用新的 `auth_redis_data` 数据卷；原科研模块的 Redis 数据卷仍未物理删除，也不会被账号功能读取。
