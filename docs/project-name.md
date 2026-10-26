# 项目名称与迁移记录

2026-10-26：项目正式名称为「心语 Xinyu」，Python包为 `xinyu`，前端包为 `xinyu-web`，API文档为「心语 Xinyu API」。

- 当前代码目录：`D:\Study\简历\Xinyu`
- 当前仓库：https://github.com/drbcxe/Xinyu
- origin：https://github.com/drbcxe/Xinyu.git
- Git历史、未提交工作、私有 `.env` 和 `.runtime` 数据均随项目迁移，未重新初始化Git。
- 原目录 `D:\Study\简历\ResearchMate` 因进程占用保留为迁移提示位置，可能含空的隐藏 `.git` 目录；不再作为工作目录。请把终端和编辑器切换到Xinyu。

Python虚拟环境已重新生成启动入口并在新目录同步依赖；前端依赖中的Windows junction已在新目录重建。启动脚本从脚本位置推导工作目录。

旧前端依赖保存在忽略目录 `.runtime/environment/node_modules-before-relocation`，新建空虚拟环境备份为忽略目录 `.venv-bootstrap`，均未公开。可在确认迁移稳定后另行清理。

为继续使用原有数据，下列存储标识保留：

- Docker Compose项目名：`researchmate`（已显式固定）
- PostgreSQL数据库及用户：`researchmate`
- 当前数据卷：`researchmate_postgres_data`

这些标识不是产品名，修改它们需要真实数据库迁移，不能通过只改环境变量完成。退役数据的隔离方式见 [数据库升级与归档](./database-upgrades.md)，应用只保留心语功能。
