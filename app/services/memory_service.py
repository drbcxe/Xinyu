import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.chat import AgentRun, ChatMessage
from app.models.memory import ProjectMemory
from app.models.project import Project
from app.schemas.memories import MemoryKey
from app.services import project_service


def list_memories(db: Session, project_id: str) -> list[ProjectMemory]:
    project_service.get_project(db, project_id)
    heads = (
        select(ProjectMemory.key, func.max(ProjectMemory.revision).label("revision"))
        .where(ProjectMemory.project_id == project_id)
        .group_by(ProjectMemory.key)
        .subquery()
    )
    return list(
        db.scalars(
            select(ProjectMemory)
            .join(
                heads,
                (ProjectMemory.key == heads.c.key)
                & (ProjectMemory.revision == heads.c.revision),
            )
            .where(ProjectMemory.project_id == project_id)
            .order_by(ProjectMemory.key)
        )
    )


def history(db: Session, project_id: str, key: MemoryKey) -> list[ProjectMemory]:
    project_service.get_project(db, project_id)
    return list(
        db.scalars(
            select(ProjectMemory)
            .where(ProjectMemory.project_id == project_id, ProjectMemory.key == key)
            .order_by(ProjectMemory.revision.desc())
            .limit(50)
        )
    )


def prompt_memories(db: Session, project_id: str) -> list[dict]:
    # Inactive values and their original quotes stay out of model recall.
    return [
        {
            "key": row.key,
            "value": row.value if row.is_active else None,
            "revision": row.revision,
            "is_active": row.is_active,
        }
        for row in list_memories(db, project_id)
    ]


def validate_value(key: MemoryKey, value: str) -> None:
    allowed = {
        "response_length": {"简短", "适中", "详细"},
        "report_language": {"中文", "英文"},
    }
    if key in allowed and value not in allowed[key]:
        raise AppError(
            422,
            "INVALID_MEMORY_VALUE",
            "该偏好值不受支持",
            {"allowed": sorted(allowed[key])},
        )
    if key in {"preferred_metric", "default_baseline"} and not re.fullmatch(
        r"[\w.()+% /:-]{1,100}", value
    ):
        raise AppError(
            422,
            "INVALID_MEMORY_VALUE",
            "指标或基线编号只能包含文字、数字和常见编号符号，最多 100 字符",
        )


def revise_memory(
    db: Session,
    project_id: str,
    key: MemoryKey,
    value: str | None,
    expected_revision: int,
    *,
    run_id: UUID | None = None,
    call_id: str | None = None,
    source_quote: str = "",
) -> ProjectMemory:
    # Lock a stable parent row even when this key has never existed.
    project = db.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    if project is None:
        raise AppError(404, "PROJECT_NOT_FOUND", "项目不存在")
    if run_id and call_id:
        existing = db.scalar(
            select(ProjectMemory).where(
                ProjectMemory.project_id == project_id,
                ProjectMemory.agent_run_id == run_id,
                ProjectMemory.tool_call_id == call_id,
            )
        )
        if existing:
            return existing
    current = db.scalar(
        select(ProjectMemory)
        .where(
            ProjectMemory.project_id == project_id,
            ProjectMemory.key == key,
        )
        .order_by(ProjectMemory.revision.desc())
        .limit(1)
    )
    actual_revision = current.revision if current else 0
    if actual_revision != expected_revision:
        raise AppError(
            409,
            "REVISION_CONFLICT",
            "偏好已被其他操作修改，请刷新后再保存",
            {"current_revision": actual_revision},
        )
    if value is None and (current is None or not current.is_active):
        raise AppError(409, "MEMORY_INACTIVE", "该偏好当前未生效，请刷新页面")
    if value is not None:
        validate_value(key, value)
    source_message = None
    if run_id:
        run = db.get(AgentRun, run_id)
        source_message = db.scalar(
            select(ChatMessage).where(
                ChatMessage.run_id == run_id, ChatMessage.role == "user"
            )
        )
        # Only the current real user message may authorize a memory write.
        if (
            run is None
            or run.project_id != project_id
            or source_message is None
            or not source_quote
            or source_quote not in source_message.content
            or re.search(
                r"(?:不要|不用|别|无需|不必|不需要).{0,8}(?:记住|记下|保存|记录|默认)|(?:do not|don't).{0,12}(?:remember|save)",
                source_message.content,
                re.IGNORECASE,
            )
            or not re.search(
                r"记住|记下|保存.{0,6}偏好|默认|以后|今后|偏好|改为|改成|remember|prefer|from now",
                source_quote,
                re.IGNORECASE,
            )
        ):
            raise AppError(
                422,
                "EXPLICIT_PREFERENCE_REQUIRED",
                "只能记录当前用户明确要求记住或修改的偏好，请提供包含偏好值的原话",
            )
    if current:
        current.is_active = False
    record = ProjectMemory(
        project_id=project_id,
        key=key,
        value=value if value is not None else current.value,
        revision=actual_revision + 1,
        is_active=value is not None,
        supersedes_id=current.id if current else None,
        source="agent" if run_id else "manual",
        source_quote=source_quote,
        source_message_id=source_message.id if source_message else None,
        agent_run_id=run_id,
        tool_call_id=call_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
