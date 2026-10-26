import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.graph.checkpoint import get_graph
from app.graph.state import AgentContext
from app.graph.subgraphs.context_loading import PROMPT_VERSION
from app.models.chat import AgentRun, ChatMessage, ConversationThread, RunEvent
from app.providers.model_provider import require_model_config
from app.repositories.database import get_engine
from app.schemas.chat import (
    EventRead,
    MessageList,
    MessageRead,
    RunCreate,
    RunRead,
    ThreadCreate,
)
from app.services.artifact_service import get_artifact, require_project

logger = logging.getLogger(__name__)


def get_thread(
    db: Session, project_id: str, thread_id: UUID, *, lock: bool = False
) -> ConversationThread:
    require_project(db, project_id)
    query = select(ConversationThread).where(
        ConversationThread.id == thread_id, ConversationThread.project_id == project_id
    )
    if lock:
        query = query.with_for_update()
    thread = db.scalar(query)
    if thread is None:
        raise AppError(404, "THREAD_NOT_FOUND", "当前项目中找不到这个会话")
    return thread


def create_thread(
    db: Session, project_id: str, data: ThreadCreate
) -> ConversationThread:
    require_project(db, project_id)
    if data.artifact_id:
        get_artifact(db, project_id, data.artifact_id)
    thread = ConversationThread(
        project_id=project_id, title=data.title, artifact_id=data.artifact_id
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


def list_threads(db: Session, project_id: str) -> list[ConversationThread]:
    require_project(db, project_id)
    return list(
        db.scalars(
            select(ConversationThread)
            .where(ConversationThread.project_id == project_id)
            .order_by(ConversationThread.updated_at.desc(), ConversationThread.id.asc())
            .limit(100)
        ).all()
    )


def _event_in_transaction(
    db: Session, run: AgentRun, event_type: str, payload: dict
) -> None:
    run.next_event_seq += 1
    db.add(
        RunEvent(
            run_id=run.id, sequence=run.next_event_seq, type=event_type, payload=payload
        )
    )


def _expire_active(db: Session, thread: ConversationThread) -> None:
    if not thread.active_run_id:
        return
    run = db.get(AgentRun, thread.active_run_id)
    if run is None or run.status != "running":
        thread.active_run_id = None
    elif run.lease_until < datetime.now(UTC):
        run.status = "failed"
        run.error = {
            "code": "RUN_EXPIRED",
            "message": "上次执行中断或超过时间预算，已结束；已保存的工具结果仍可查看",
        }
        run.finished_at = datetime.now(UTC)
        _event_in_transaction(db, run, "run.failed", run.error)
        thread.active_run_id = None


def _compatible_request(run: AgentRun, data: RunCreate) -> None:
    if run.message != data.message or (
        "artifact_id" in data.model_fields_set and run.artifact_id != data.artifact_id
    ):
        raise AppError(
            409, "REQUEST_ID_CONFLICT", "同一个 request_id 不能用于不同消息或文件"
        )


def submit_run(project_id: str, thread_id: UUID, data: RunCreate) -> AgentRun:
    settings = get_settings()
    with Session(get_engine(), expire_on_commit=False) as db:
        thread = get_thread(db, project_id, thread_id, lock=True)
        existing = db.scalar(
            select(AgentRun).where(
                AgentRun.thread_id == thread.id, AgentRun.request_id == data.request_id
            )
        )
        if existing:
            _compatible_request(existing, data)
            db.commit()
            return existing
        _expire_active(db, thread)
        if thread.active_run_id:
            raise AppError(
                409,
                "THREAD_BUSY",
                "当前会话还有一轮正在执行，请等待完成",
                {"run_id": str(thread.active_run_id)},
            )
        require_model_config()
        artifact_id = (
            data.artifact_id
            if "artifact_id" in data.model_fields_set
            else thread.artifact_id
        )
        if artifact_id:
            get_artifact(db, project_id, artifact_id)
        run = AgentRun(
            id=uuid4(),
            project_id=project_id,
            thread_id=thread.id,
            request_id=data.request_id,
            artifact_id=artifact_id,
            message=data.message,
            status="running",
            evidence=[],
            model_name=settings.model_name.strip(),
            prompt_version=PROMPT_VERSION,
            next_event_seq=0,
            lease_until=datetime.now(UTC)
            + timedelta(seconds=settings.agent_timeout_seconds + 90),
        )
        db.add(run)
        db.flush()
        db.add(
            ChatMessage(
                thread_id=thread.id, run_id=run.id, role="user", content=data.message
            )
        )
        thread.active_run_id = run.id
        thread.artifact_id = artifact_id
        thread.updated_at = datetime.now(UTC)
        if thread.title == "新会话":
            thread.title = data.message.replace("\n", " ")[:40]
        _event_in_transaction(
            db,
            run,
            "run.started",
            {"model_name": run.model_name, "prompt_version": PROMPT_VERSION},
        )
        db.commit()
        run_id = run.id

    started = time.monotonic()
    result = None
    error = None
    try:
        context = AgentContext(
            project_id,
            thread_id,
            run_id,
            artifact_id,
            started + settings.agent_timeout_seconds,
        )
        result = get_graph().invoke(
            {"input": data.message},
            {"configurable": {"thread_id": str(thread_id)}, "recursion_limit": 32},
            context=context,
        )
        if not result.get("reply", "").strip():
            raise AppError(
                502, "MODEL_EMPTY_REPLY", "未获得完整答复，请查看工具执行记录"
            )
    except AppError as exc:
        error = {"code": exc.code, "message": exc.message}
    except Exception:  # noqa: BLE001 - finalize every failed run without exposing provider exceptions
        logger.error("Agent execution failed for run %s", run_id)
        error = {
            "code": "AGENT_EXECUTION_ERROR",
            "message": "Agent 执行失败；请确认数据库、图状态初始化和模型服务正常，已完成的工具记录仍会保留",
        }

    with Session(get_engine(), expire_on_commit=False) as db:
        thread = get_thread(db, project_id, thread_id, lock=True)
        saved = db.get(AgentRun, run_id)
        if saved.status != "running" or thread.active_run_id != run_id:
            return saved
        saved.duration_ms = round((time.monotonic() - started) * 1000)
        saved.finished_at = datetime.now(UTC)
        if error:
            saved.status = "failed"
            saved.error = error
            _event_in_transaction(db, saved, "run.failed", error)
        else:
            saved.status = "succeeded"
            saved.reply = result["reply"]
            saved.evidence = result["evidence"]
            reported = result.get("usage", [])
            totals = {
                key: sum(item.get(key, 0) for item in reported)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if any(key in item for item in reported)
            }
            saved.usage = {
                "model_calls": result["model_steps"],
                "retries": result["retry_count"],
                "reported_tokens": totals or None,
                "usage_complete": len(reported) == result["model_steps"]
                and result["retry_count"] == 0,
            }
            db.add(
                ChatMessage(
                    thread_id=thread.id,
                    run_id=run_id,
                    role="assistant",
                    content=saved.reply,
                )
            )
            _event_in_transaction(
                db,
                saved,
                "reply.completed",
                {"comparison_ids": [item["id"] for item in saved.evidence]},
            )
        thread.active_run_id = None
        thread.updated_at = datetime.now(UTC)
        db.commit()
        return saved


def _read_run(run: AgentRun, events: list[RunEvent]) -> RunRead:
    return RunRead(
        id=run.id,
        request_id=run.request_id,
        thread_id=run.thread_id,
        project_id=run.project_id,
        artifact_id=run.artifact_id,
        status=run.status,
        reply=run.reply,
        error=run.error,
        evidence=run.evidence,
        events=[EventRead.model_validate(event) for event in events],
        model_name=run.model_name,
        usage=run.usage,
        duration_ms=run.duration_ms,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def read_run(db: Session, project_id: str, run_id: UUID) -> RunRead:
    require_project(db, project_id)
    run = db.scalar(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.project_id == project_id)
    )
    if run is None:
        raise AppError(404, "RUN_NOT_FOUND", "当前项目中找不到这次执行")
    if run.status == "running" and run.lease_until < datetime.now(UTC):
        thread = get_thread(db, project_id, run.thread_id, lock=True)
        _expire_active(db, thread)
        db.commit()
    events = list(
        db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id)
            .order_by(RunEvent.sequence)
        ).all()
    )
    return _read_run(run, events)


def list_messages(db: Session, project_id: str, thread_id: UUID) -> MessageList:
    thread = get_thread(db, project_id, thread_id, lock=True)
    _expire_active(db, thread)
    db.commit()
    messages = list(
        reversed(
            db.scalars(
                select(ChatMessage)
                .where(ChatMessage.thread_id == thread.id)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(60)
            ).all()
        )
    )
    run_ids = {message.run_id for message in messages}
    if thread.active_run_id:
        run_ids.add(thread.active_run_id)
    runs = (
        db.scalars(
            select(AgentRun).where(
                AgentRun.id.in_(run_ids),
                AgentRun.project_id == project_id,
                AgentRun.thread_id == thread_id,
            )
        ).all()
        if run_ids
        else []
    )
    events = (
        db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id.in_(run_ids))
            .order_by(RunEvent.sequence)
        ).all()
        if run_ids
        else []
    )
    grouped: dict[UUID, list[RunEvent]] = {}
    for event in events:
        grouped.setdefault(event.run_id, []).append(event)
    return MessageList(
        items=[MessageRead.model_validate(message) for message in messages],
        active_run_id=thread.active_run_id,
        runs=[_read_run(run, grouped.get(run.id, [])) for run in runs],
    )
