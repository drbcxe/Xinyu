"""PostgreSQL-backed work queue; a separate worker survives browser closure."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.relationship import (
    RelationshipItem,
    RelationshipJob,
    RelationshipNotice,
    RelationshipProfile,
    RelationshipTurn,
)
from app.repositories.database import get_engine
from app.services.relationship_agent import run_agent


def get_profile(db: Session, owner: str):
    db.execute(
        insert(RelationshipProfile)
        .values(owner=owner, partner_name="伴侣", style="自然、简短，不要油腻或说教")
        .on_conflict_do_nothing(index_elements=["owner"])
    )
    db.commit()
    return db.get(RelationshipProfile, owner)


def enqueue(db: Session, owner: str, request_id: str, content: str, mode: str = "chat"):
    get_profile(db, owner)
    now = datetime.now(UTC)
    db.execute(
        insert(RelationshipJob)
        .values(
            id=str(uuid4()),
            owner=owner,
            request_id=request_id,
            status="queued",
            payload={"content": content, "mode": mode},
            error="",
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["owner", "request_id"])
    )
    db.commit()
    job = db.scalar(
        select(RelationshipJob).where(
            RelationshipJob.owner == owner, RelationshipJob.request_id == request_id
        )
    )
    if job.payload.get("content") != content or job.payload.get("mode", "chat") != mode:
        raise AppError(409, "REQUEST_ID_REUSED", "请求编号已用于其他内容，请重新发送")
    return job


def job_data(job: RelationshipJob):
    return {
        key: getattr(job, key)
        for key in ("id", "status", "turn_id", "error", "created_at", "updated_at")
    }


def process_one() -> bool:
    now = datetime.now(UTC)
    with Session(get_engine()) as db:
        # A hard-killed worker releases its row locks; leased jobs may be retried.
        for stale in db.scalars(
            select(RelationshipJob)
            .where(
                RelationshipJob.status == "running",
                RelationshipJob.updated_at < now - timedelta(minutes=6),
            )
            .with_for_update(skip_locked=True)
        ).all():
            stale.status = "queued" if stale.attempts < 2 else "failed"
            stale.error = "执行进程中断，请重试" if stale.status == "failed" else ""
        job = db.scalar(
            select(RelationshipJob)
            .where(RelationshipJob.status == "queued")
            .order_by(RelationshipJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            db.commit()
            return False
        job.status = "running"
        job.attempts += 1
        job.updated_at = now
        job_id, owner, request_id, payload = (
            job.id,
            job.owner,
            job.request_id,
            job.payload,
        )
        db.commit()
    try:
        with Session(get_engine(), expire_on_commit=False) as db:
            # Queue status is committed separately; this transaction makes turn creation atomic.
            profile = db.scalar(
                select(RelationshipProfile)
                .where(RelationshipProfile.owner == owner)
                .with_for_update()
            )
            if not profile:
                raise AppError(409, "SPACE_MISSING", "个人空间不存在")
            job = db.scalar(
                select(RelationshipJob)
                .where(RelationshipJob.id == job_id)
                .with_for_update()
            )
            if not job or job.status != "running":
                return True
            turn = db.scalar(
                select(RelationshipTurn).where(
                    RelationshipTurn.owner == owner,
                    RelationshipTurn.request_id == request_id,
                )
            )
            if not turn:
                result = run_agent(
                    db,
                    owner,
                    payload["content"],
                    payload.get("mode", "chat"),
                    request_id=request_id,
                )
                turn = RelationshipTurn(
                    id=str(uuid4()),
                    owner=owner,
                    request_id=request_id,
                    content=payload["content"],
                    result=result,
                    created_at=datetime.now(UTC),
                )
                db.add(turn)
            job.status, job.turn_id, job.error = "succeeded", turn.id, ""
            job.updated_at = datetime.now(UTC)
            db.commit()
    except Exception as exc:  # noqa: BLE001 - task boundary records failure without killing worker
        with Session(get_engine()) as db:
            job = db.get(RelationshipJob, job_id)
            if job and job.status == "running":
                job.status = "failed"
                job.error = (
                    exc.message
                    if isinstance(exc, AppError)
                    else "任务未完成，请重试；技术细节已留在本地诊断中"
                )
                job.updated_at = datetime.now(UTC)
                db.commit()
        # Never print input, model response, credentials, or raw provider bodies.
        print(f"relationship job failed: {type(exc).__name__}")
    return True


def dispatch_reminders() -> int:
    now = datetime.now(UTC)
    with Session(get_engine()) as db:
        items = db.scalars(
            select(RelationshipItem)
            .where(
                RelationshipItem.kind == "reminder",
                RelationshipItem.status == "active",
                RelationshipItem.due_at <= now,
            )
            .with_for_update(skip_locked=True)
        ).all()
        count = 0
        for item in items:
            result = db.execute(
                insert(RelationshipNotice)
                .values(
                    id=str(uuid4()),
                    owner=item.owner,
                    item_id=item.id,
                    revision=item.revision,
                    title=item.title,
                    status="unread",
                    weixin_status="not_connected",
                    error="",
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=["item_id", "revision"])
            )
            count += result.rowcount
        db.commit()
        return count
