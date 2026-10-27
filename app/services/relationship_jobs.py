"""PostgreSQL-backed work queue; a separate worker survives browser closure."""

import logging
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models.relationship import (
    RelationshipClosedSpace,
    RelationshipItem,
    RelationshipJob,
    RelationshipNotice,
    RelationshipProfile,
    RelationshipTurn,
)
from app.repositories.database import get_engine
from app.services.auth_cache import auth_cache
from app.services.privacy_policy import require_processing
from app.services.relationship_agent import run_agent
from app.services.resource_control import admit_request, check_storage, operation_scope
from app.services.runtime_state import write_progress
from app.services.storage_budget import lock_storage

logger = logging.getLogger(__name__)


def get_profile(db: Session, owner: str):
    if db.get(RelationshipClosedSpace, owner):
        raise AppError(401, "SPACE_CLOSED", "原账号空间已注销，请重新打开页面")
    existing = db.get(RelationshipProfile, owner)
    if existing:
        return existing
    lock_storage(db)
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:owner,718302620))"),
        {"owner": owner},
    )
    if db.get(RelationshipClosedSpace, owner, populate_existing=True):
        raise AppError(401, "SPACE_CLOSED", "原账号空间已注销，请重新打开页面")
    db.execute(
        insert(RelationshipProfile)
        .values(owner=owner, partner_name="伴侣", style="自然、简短，不要油腻或说教")
        .on_conflict_do_nothing(index_elements=["owner"])
    )
    db.commit()
    profile = db.get(RelationshipProfile, owner)
    if not profile:
        raise AppError(401, "SPACE_CLOSED", "原账号空间已注销，请重新打开页面")
    return profile


def enqueue(
    db: Session,
    owner: str,
    request_id: str,
    content: str,
    mode: str = "chat",
    ip_scope: str = "local",
):
    get_profile(db, owner)
    lock_storage(db)
    profile = db.scalar(
        select(RelationshipProfile)
        .where(RelationshipProfile.owner == owner)
        .with_for_update()
    )
    if not profile:
        raise AppError(401, "SPACE_CLOSED", "原账号空间已注销，请重新打开页面")
    require_processing(owner, db)
    existing = db.scalar(
        select(RelationshipJob).where(
            RelationshipJob.owner == owner, RelationshipJob.request_id == request_id
        )
    )
    if existing:
        if (
            existing.payload.get("content") != content
            or existing.payload.get("mode", "chat") != mode
        ):
            raise AppError(
                409, "REQUEST_ID_REUSED", "请求编号已用于其他内容，请重新发送"
            )
        return existing
    settings = get_settings()
    # Serialize only the short global admission decision; model execution never holds this lock.
    db.execute(text("SELECT pg_advisory_xact_lock(718302611)"))
    pending = [RelationshipJob.status.in_(["queued", "running"])]
    total = db.scalar(select(func.count()).select_from(RelationshipJob).where(*pending))
    mine = db.scalar(
        select(func.count())
        .select_from(RelationshipJob)
        .where(*pending, RelationshipJob.owner == owner)
    )
    if total >= settings.global_queue_limit or mine >= settings.owner_pending_limit:
        raise AppError(
            429,
            "QUEUE_FULL",
            "当前任务较多，请等待已有任务完成后重试",
            {"retry_after_seconds": 10},
        )
    check_storage(
        db,
        owner,
        added_bytes=len(content.encode()) + settings.job_storage_reserve_bytes + 1500,
    )
    admit_request(db, owner, ip_scope, "advice")
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
        for key in (
            "id",
            "status",
            "turn_id",
            "error",
            "created_at",
            "updated_at",
            "cancel_requested",
            "attempts",
            "available_at",
        )
    }


def process_one() -> bool:
    now = datetime.now(UTC)
    settings = get_settings()
    lease_token = str(uuid4())
    with Session(get_engine()) as db:
        lock_storage(db)
        for stale in db.scalars(
            select(RelationshipJob)
            .where(
                RelationshipJob.status == "running",
                or_(
                    RelationshipJob.lease_expires_at <= now,
                    RelationshipJob.lease_expires_at.is_(None)
                    & (RelationshipJob.updated_at < now - timedelta(minutes=6)),
                ),
            )
            .with_for_update(skip_locked=True)
        ).all():
            stale.status = (
                "queued"
                if stale.attempts < settings.worker_max_attempts
                and not stale.cancel_requested
                else "cancelled"
                if stale.cancel_requested
                else "failed"
            )
            stale.error = "执行进程中断，请重试" if stale.status == "failed" else ""
            stale.lease_token, stale.lease_expires_at = None, None
            stale.available_at = now
        selected = None
        for candidate in db.scalars(
            select(RelationshipJob)
            .where(
                RelationshipJob.status == "queued",
                RelationshipJob.available_at <= now,
                RelationshipJob.cancel_requested.is_(False),
            )
            .order_by(RelationshipJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(20)
        ).all():
            profile = db.scalar(
                select(RelationshipProfile)
                .where(RelationshipProfile.owner == candidate.owner)
                .with_for_update(skip_locked=True)
            )
            if not profile:
                continue
            running = db.scalar(
                select(RelationshipJob.id)
                .where(
                    RelationshipJob.owner == candidate.owner,
                    RelationshipJob.status == "running",
                    RelationshipJob.lease_expires_at > now,
                )
                .limit(1)
            )
            if not running:
                selected = candidate
                break
        if selected is None:
            db.commit()
            return False
        selected.status, selected.lease_token = "running", lease_token
        selected.lease_expires_at = now + timedelta(
            seconds=settings.worker_lease_seconds
        )
        selected.attempts += 1
        selected.updated_at = now
        job_id, owner, request_id, payload = (
            selected.id,
            selected.owner,
            selected.request_id,
            dict(selected.payload),
        )
        db.commit()

    stop = threading.Event()
    lost = threading.Event()

    def check_current():
        if lost.is_set():
            raise AppError(409, "JOB_LEASE_LOST", "任务执行权已变更，旧执行已停止")
        with Session(get_engine()) as check_db:
            require_processing(owner, check_db)
            job = check_db.get(RelationshipJob, job_id)
            if not job or job.cancel_requested or job.status == "cancelled":
                raise AppError(409, "JOB_CANCELLED", "任务已取消")
            if (
                job.status != "running"
                or job.lease_token != lease_token
                or not job.lease_expires_at
                or job.lease_expires_at <= datetime.now(UTC)
            ):
                raise AppError(409, "JOB_LEASE_LOST", "任务执行权已变更，旧执行已停止")

    def heartbeat():
        while not stop.wait(max(1, settings.worker_lease_seconds / 3)):
            try:
                stamp = datetime.now(UTC)
                with Session(get_engine()) as heartbeat_db:
                    result = heartbeat_db.execute(
                        update(RelationshipJob)
                        .where(
                            RelationshipJob.id == job_id,
                            RelationshipJob.lease_token == lease_token,
                            RelationshipJob.status == "running",
                            RelationshipJob.cancel_requested.is_(False),
                            RelationshipJob.lease_expires_at > stamp,
                        )
                        .values(
                            lease_expires_at=stamp
                            + timedelta(seconds=settings.worker_lease_seconds),
                            updated_at=stamp,
                        )
                    )
                    heartbeat_db.commit()
                    if not result.rowcount:
                        lost.set()
                        return
            except Exception:  # noqa: BLE001 - expiry fences a worker unable to renew
                logger.warning("job_heartbeat_failed", extra={"job_id": job_id})

    thread = threading.Thread(target=heartbeat, name="job-lease", daemon=True)
    thread.start()
    logger.info("job_started", extra={"job_id": job_id})
    quota_failure = False
    try:
        check_current()
        with (
            Session(get_engine(), expire_on_commit=False) as db,
            operation_scope(
                owner,
                request_id,
                "advice",
                check_current,
                lambda node, step: write_progress(job_id, lease_token, node, step),
                job_id=job_id,
            ),
        ):
            result = run_agent(
                db,
                owner,
                payload["content"],
                payload.get("mode", "chat"),
                request_id=job_id,
            )
            db.rollback()
        check_current()
        # Only this short final transaction holds owner/job locks; network work has already finished.
        with Session(get_engine()) as db:
            lock_storage(db)
            profile = db.scalar(
                select(RelationshipProfile)
                .where(RelationshipProfile.owner == owner)
                .with_for_update()
            )
            job = db.scalar(
                select(RelationshipJob)
                .where(RelationshipJob.id == job_id)
                .with_for_update()
            )
            if (
                not profile
                or not job
                or job.status != "running"
                or job.cancel_requested
                or job.lease_token != lease_token
                or job.lease_expires_at <= datetime.now(UTC)
            ):
                return True
            require_processing(owner, db)
            turn = db.scalar(
                select(RelationshipTurn).where(
                    RelationshipTurn.owner == owner,
                    RelationshipTurn.request_id == request_id,
                )
            )
            if not turn:
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
            job.lease_token, job.lease_expires_at = None, None
            job.updated_at = datetime.now(UTC)
            db.commit()
            logger.info("job_finished", extra={"job_id": job_id})
    except Exception as exc:  # noqa: BLE001 - durable task boundary
        quota_failure = isinstance(exc, AppError) and exc.code in {
            "SPACE_QUOTA_EXCEEDED",
            "GLOBAL_STORAGE_QUOTA_EXCEEDED",
            "GRAPH_STORAGE_LIMIT",
            "AGENT_RESULT_LIMIT",
        }
        with Session(get_engine()) as db:
            lock_storage(db)
            job = db.scalar(
                select(RelationshipJob)
                .where(RelationshipJob.id == job_id)
                .with_for_update()
            )
            if job and job.status == "running" and job.lease_token == lease_token:
                retryable = not isinstance(exc, AppError) or exc.status_code in {
                    502,
                    503,
                    504,
                }
                if isinstance(exc, AppError) and exc.code in {
                    "ADVICE_INVALID",
                    "AGENT_BUDGET",
                    "MODEL_AUTH_ERROR",
                    "MODEL_PERMISSION_ERROR",
                    "MODEL_REQUEST_REJECTED",
                    "MODEL_NOT_FOUND",
                    "MODEL_OUTPUT_LIMIT",
                    "SPACE_QUOTA_EXCEEDED",
                    "GLOBAL_STORAGE_QUOTA_EXCEEDED",
                    "GRAPH_STORAGE_LIMIT",
                    "STORAGE_POLICY_MISSING",
                    "SPACE_COUNT_LIMIT",
                    "AGENT_RESULT_LIMIT",
                }:
                    retryable = False
                retry = (
                    retryable
                    and job.attempts < settings.worker_max_attempts
                    and not job.cancel_requested
                )
                job.status = (
                    "queued"
                    if retry
                    else "cancelled"
                    if job.cancel_requested
                    else "failed"
                )
                job.error = (
                    exc.message
                    if isinstance(exc, AppError)
                    else "任务暂未完成，请稍后重试"
                )
                job.lease_token, job.lease_expires_at = None, None
                job.available_at = datetime.now(UTC) + timedelta(
                    seconds=settings.worker_retry_base_seconds
                    * 2 ** max(0, job.attempts - 1)
                )
                job.updated_at = datetime.now(UTC)
                db.commit()
        logger.warning(
            "relationship_job_failed",
            extra={"job_id": job_id, "error_type": type(exc).__name__},
        )
    finally:
        stop.set()
        thread.join(timeout=2)
        with Session(get_engine()) as cleanup_db:
            current = cleanup_db.get(RelationshipJob, job_id)
            cancelled = (
                current is None
                or current.status == "cancelled"
                or current.cancel_requested
            )
        if cancelled or quota_failure:
            from app.services.graph_checkpoints import checkpoint_store

            with checkpoint_store() as saver:
                saver.delete_thread(f"xinyu:{owner}:{job_id}")
    return True


def dispatch_reminders() -> int:
    now = datetime.now(UTC)
    # A full space must not monopolize every bounded batch or roll back other owners' notices.
    with auth_cache() as cache:
        backoff_key = "xinyu:notice:storage-backoff"
        seconds = cache.time()[0]
        cache.zremrangebyscore(backoff_key, "-inf", seconds)
        blocked = cache.zrange(backoff_key, 0, -1)
    with Session(get_engine()) as db:
        lock_storage(db)
        items = db.scalars(
            select(RelationshipItem)
            .where(
                RelationshipItem.kind == "reminder",
                RelationshipItem.status == "active",
                RelationshipItem.due_at <= now,
                RelationshipItem.owner.not_in(blocked),
                ~select(RelationshipNotice.id)
                .where(
                    RelationshipNotice.item_id == RelationshipItem.id,
                    RelationshipNotice.revision == RelationshipItem.revision,
                )
                .exists(),
            )
            .order_by(RelationshipItem.due_at, RelationshipItem.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        ).all()
        count = 0
        for item in items:
            try:
                with db.begin_nested():
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
                        .returning(RelationshipNotice.id)
                    )
                    count += int(result.scalar_one_or_none() is not None)
            except AppError as exc:
                if exc.code not in {
                    "SPACE_QUOTA_EXCEEDED",
                    "GLOBAL_STORAGE_QUOTA_EXCEEDED",
                }:
                    raise
                with auth_cache() as cache:
                    cache.zadd(backoff_key, {item.owner: cache.time()[0] + 60})
                    cache.expire(backoff_key, 120)
                logger.warning("notice_deferred_storage_limit")
        db.commit()
        return count
