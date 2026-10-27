"""Deletion fencing and receipts. Keep only non-content tombstones and usage accounting."""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models.relationship import (
    RelationshipAccount,
    RelationshipAuthEvent,
    RelationshipClosedSpace,
    RelationshipEmailChallenge,
    RelationshipEmailOutbox,
    RelationshipFeedback,
    RelationshipItem,
    RelationshipJob,
    RelationshipNotice,
    RelationshipPrivacy,
    RelationshipProfile,
    RelationshipRagIndex,
    RelationshipSession,
    RelationshipTurn,
    RelationshipWeixinBinding,
)
from app.repositories.database import get_engine
from app.services.auth_cache import digest
from app.services.graph_checkpoints import checkpoint_store, clear_checkpoints
from app.services.recovery_journal import record_deletion
from app.services.relationship_auth import revoke_sessions
from app.services.relationship_rag import schedule_index


def purge_content(db: Session, owner: str):
    record_deletion(db, owner, "content")
    for item in db.scalars(
        select(RelationshipItem).where(RelationshipItem.owner == owner)
    ):
        schedule_index(db, item, deleted=True)
    for index in db.scalars(
        select(RelationshipRagIndex).where(RelationshipRagIndex.owner == owner)
    ):
        index.status, index.error, index.attempts, index.updated_at = (
            "deleting",
            "",
            0,
            datetime.now(UTC),
        )
    clear_checkpoints(owner)
    for model in (
        RelationshipJob,
        RelationshipNotice,
        RelationshipFeedback,
        RelationshipItem,
        RelationshipTurn,
    ):
        db.execute(model.__table__.delete().where(model.owner == owner))


def close_account(db: Session, owner: str) -> str:
    # Insertion of a missing profile and account deletion share the same advisory lock.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:owner,718302620))"),
        {"owner": owner},
    )
    profile = db.scalar(
        select(RelationshipProfile)
        .where(RelationshipProfile.owner == owner)
        .with_for_update()
    )
    if not profile:
        raise AppError(409, "SPACE_CHANGED", "空间状态发生变化，请刷新后重试")
    record_deletion(db, owner)
    revoke_sessions(
        db,
        list(
            db.scalars(
                select(RelationshipSession).where(RelationshipSession.owner == owner)
            )
        ),
    )
    purge_content(db, owner)
    for model in (
        RelationshipEmailOutbox,
        RelationshipEmailChallenge,
        RelationshipAuthEvent,
        RelationshipWeixinBinding,
        RelationshipPrivacy,
    ):
        db.execute(model.__table__.delete().where(model.owner == owner))
    db.execute(
        RelationshipAccount.__table__.delete().where(RelationshipAccount.owner == owner)
    )
    db.delete(profile)
    receipt = secrets.token_urlsafe(32)
    db.add(
        RelationshipClosedSpace(
            owner=owner, receipt_hash=digest(receipt), closed_at=datetime.now(UTC)
        )
    )
    db.commit()
    return receipt


def checkpoint_count(owner: str) -> int:
    with checkpoint_store() as saver, saver.conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS count FROM checkpoints WHERE thread_id LIKE %s",
            ("xinyu:" + owner + ":%",),
        )
        return cursor.fetchone()["count"]


def deletion_status(db: Session, receipt: str) -> dict:
    closed = db.scalar(
        select(RelationshipClosedSpace).where(
            RelationshipClosedSpace.receipt_hash == digest(receipt)
        )
    )
    if not closed:
        raise AppError(404, "DELETION_RECEIPT_NOT_FOUND", "注销回执不存在")
    pending = db.scalar(
        select(func.count())
        .select_from(RelationshipRagIndex)
        .where(RelationshipRagIndex.owner == closed.owner)
    )
    return {
        "status": "completed" if closed.cleanup_at else "pending",
        "pending_indexes": pending,
        "closed_at": closed.closed_at,
        "cleanup_at": closed.cleanup_at,
        "note": "清理范围为在线业务数据与索引；已有备份需按实际保留策略另行处理，模型平台留存遵循供应商政策。",
    }


def reconcile_closed_spaces() -> int:
    window = (
        max(get_settings().agent_timeout_seconds, get_settings().model_timeout_seconds)
        * 2
        + 120
    )
    with Session(get_engine()) as db:
        owners = list(
            db.scalars(
                select(RelationshipClosedSpace.owner)
                .where(RelationshipClosedSpace.cleanup_at.is_(None))
                .order_by(RelationshipClosedSpace.closed_at)
                .limit(20)
            )
        )
    completed = 0
    for owner in owners:
        clear_checkpoints(owner)
        with Session(get_engine()) as db:
            closed = db.get(RelationshipClosedSpace, owner)
            pending = db.scalar(
                select(func.count())
                .select_from(RelationshipRagIndex)
                .where(RelationshipRagIndex.owner == owner)
            )
            if (
                closed
                and not pending
                and closed.closed_at < datetime.now(UTC) - timedelta(seconds=window)
                and checkpoint_count(owner) == 0
            ):
                closed.cleanup_at = datetime.now(UTC)
                db.commit()
                completed += 1
    return completed
