"""Bounded read models. Polling never loads chat transcripts or model answers."""

import base64
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.relationship import (
    RelationshipAccount,
    RelationshipItem,
    RelationshipJob,
    RelationshipNotice,
    RelationshipProfile,
    RelationshipTurn,
    RelationshipWeixinBinding,
)
from app.services.auth_cache import digest
from app.services.relationship_jobs import job_data
from app.services.runtime_state import job_progress, worker_count


def snapshot_version(db: Session, owner: str) -> str:
    parts = [owner]
    for model, fields in [
        (RelationshipTurn, [func.count(), func.max(RelationshipTurn.created_at)]),
        (
            RelationshipItem,
            [
                func.count(),
                func.max(RelationshipItem.created_at),
                func.sum(RelationshipItem.revision),
            ],
        ),
        (RelationshipJob, [func.count(), func.max(RelationshipJob.updated_at)]),
        (
            RelationshipNotice,
            [
                func.count(),
                func.max(RelationshipNotice.created_at),
                func.md5(
                    func.string_agg(
                        RelationshipNotice.id
                        + RelationshipNotice.status
                        + RelationshipNotice.weixin_status
                        + RelationshipNotice.error,
                        aggregate_order_by(literal(""), RelationshipNotice.id),
                    )
                ),
            ],
        ),
    ]:
        parts.append(
            tuple(db.execute(select(*fields).where(model.owner == owner)).one())
        )
    profile = db.get(RelationshipProfile, owner)
    account = db.scalar(
        select(RelationshipAccount.username).where(RelationshipAccount.owner == owner)
    )
    binding = db.get(RelationshipWeixinBinding, owner)
    parts += [
        (profile.partner_name, profile.style) if profile else None,
        account,
        (binding.status, binding.updated_at) if binding else None,
    ]
    return digest(repr(parts))


def jobs_summary(db: Session, owner: str) -> list[dict]:
    rows = list(
        db.scalars(
            select(RelationshipJob)
            .where(RelationshipJob.owner == owner)
            .order_by(RelationshipJob.created_at.desc(), RelationshipJob.id.desc())
            .limit(12)
        )
    )
    try:
        progress = job_progress(rows)
    except AppError:
        progress = {}
    return [{**job_data(row), "progress": progress.get(row.id)} for row in rows]


def presence() -> bool:
    try:
        return worker_count() > 0
    except AppError:
        return False


def item_counts(db: Session, owner: str) -> dict:
    counts = dict(
        db.execute(
            select(RelationshipItem.kind, func.count())
            .where(RelationshipItem.owner == owner)
            .group_by(RelationshipItem.kind)
        ).all()
    )
    counts["memories_from_chat"] = db.scalar(
        select(func.count())
        .select_from(RelationshipItem)
        .where(
            RelationshipItem.owner == owner,
            RelationshipItem.kind == "memory",
            RelationshipItem.source_turn_id.is_not(None),
        )
    )
    return counts


def encode_cursor(row) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps([row.created_at.isoformat(), row.id]).encode()
        )
        .decode()
        .rstrip("=")
    )


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        stamp, item_id = json.loads(
            base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        )
        moment = datetime.fromisoformat(stamp)
        if moment.tzinfo is None:
            raise ValueError("Missing timezone")
        return moment, str(UUID(item_id))
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise AppError(422, "INVALID_CURSOR", "分页位置无效，请刷新列表") from None


def item_page(
    db: Session,
    owner: str,
    kind: str,
    cursor: str | None,
    limit: int,
    query: str = "",
    status: str = "all",
) -> dict:
    fields = [
        getattr(RelationshipItem, key)
        for key in (
            "id",
            "kind",
            "title",
            "quote",
            "source_turn_id",
            "due_at",
            "status",
            "created_at",
            "revision",
        )
    ]
    # PostgreSQL builds chat-log metadata without returning the raw text to the Python process.
    details = case(
        (
            RelationshipItem.kind == "chat_log",
            func.json_build_object(
                "characters", func.length(RelationshipItem.details["text"].as_string())
            ),
        ),
        else_=RelationshipItem.details,
    ).label("details")
    statement = select(*fields, details).where(
        RelationshipItem.owner == owner, RelationshipItem.kind == kind
    )
    if query.strip():
        statement = statement.where(
            RelationshipItem.title.contains(query.strip(), autoescape=True)
        )
    if status != "all":
        statement = statement.where(RelationshipItem.status == status)
    if cursor:
        statement = statement.where(
            tuple_(RelationshipItem.created_at, RelationshipItem.id)
            < decode_cursor(cursor)
        )
    rows = db.execute(
        statement.order_by(
            RelationshipItem.created_at.desc(), RelationshipItem.id.desc()
        ).limit(limit + 1)
    ).all()
    selected = rows[:limit]
    return {
        "items": [dict(row._mapping) for row in selected],
        "next_cursor": encode_cursor(selected[-1]) if len(rows) > limit else None,
    }
