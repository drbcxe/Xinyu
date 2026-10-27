"""Bounded online retention. Confirmed items stay until explicitly removed."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.relationship import (
    RelationshipAuthEvent,
    RelationshipDailyUsage,
    RelationshipEmailChallenge,
    RelationshipEmailOutbox,
    RelationshipFeedback,
    RelationshipItem,
    RelationshipJob,
    RelationshipLoginLimit,
    RelationshipModelCall,
    RelationshipNotice,
    RelationshipPrivacy,
    RelationshipProfile,
    RelationshipSession,
    RelationshipTurn,
)
from app.repositories.database import get_engine
from app.services.graph_checkpoints import checkpoint_store
from app.services.recovery_journal import record_deletion
from app.services.relationship_rag import schedule_index


def cutoff(stamp):
    return stamp - func.coalesce(RelationshipPrivacy.history_days, 90) * text(
        "INTERVAL '1 day'"
    )


def prune_graphs(stamp):
    deleted = 0
    with checkpoint_store() as saver, saver.conn.cursor() as cursor:
        cursor.execute(
            """SELECT c.thread_id FROM checkpoints c
            LEFT JOIN public.relationship_jobs j ON c.thread_id = 'xinyu:' || j.owner || ':' || j.id
            WHERE COALESCE(j.status, 'orphan') NOT IN ('queued', 'running')
            GROUP BY c.thread_id, j.status
            HAVING MAX((c.checkpoint->>'ts')::timestamptz) <
                CASE WHEN j.status='succeeded' THEN %s::timestamptz ELSE %s::timestamptz END
            ORDER BY MAX((c.checkpoint->>'ts')::timestamptz) LIMIT 100""",
            (stamp - timedelta(days=1), stamp - timedelta(days=7)),
        )
        threads = [row["thread_id"] for row in cursor.fetchall()]
    for thread in threads:
        parts = thread.split(":")
        if len(parts) != 3 or parts[0] != "xinyu" or len(parts[1]) != 64:
            continue
        with Session(get_engine()) as db:
            profile = db.scalar(
                select(RelationshipProfile)
                .where(RelationshipProfile.owner == parts[1])
                .with_for_update(skip_locked=True)
            )
            if not profile:
                continue
            job = db.get(RelationshipJob, parts[2])
            if job and job.status in {"queued", "running"}:
                continue
            if not job or job.status != "succeeded":
                with checkpoint_store() as saver, saver.conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT MAX((checkpoint->>'ts')::timestamptz) AS latest FROM checkpoints WHERE thread_id=%s",
                        (thread,),
                    )
                    latest = cursor.fetchone()["latest"]
                if latest and latest > stamp - timedelta(days=7):
                    continue
            with checkpoint_store() as saver:
                saver.delete_thread(thread)
            deleted += 1
    return deleted


def prune_online_data(stamp=None):
    stamp = stamp or datetime.now(UTC)
    deleted = {"turns": 0, "imports": 0, "jobs": 0, "metadata": 0, "graphs": 0}
    with Session(get_engine()) as db:
        candidates = []
        for model in (RelationshipTurn, RelationshipJob, RelationshipItem):
            statement = (
                select(model.owner)
                .outerjoin(
                    RelationshipPrivacy, RelationshipPrivacy.owner == model.owner
                )
                .where(model.created_at < cutoff(stamp))
            )
            if model is RelationshipItem:
                statement = statement.where(model.kind == "chat_log")
            if model is RelationshipJob:
                statement = statement.where(model.status.not_in(["queued", "running"]))
            candidates.extend(
                db.scalars(statement.order_by(model.created_at).limit(100))
            )
    for owner in dict.fromkeys(candidates):
        with Session(get_engine()) as db:
            profile = db.scalar(
                select(RelationshipProfile)
                .where(RelationshipProfile.owner == owner)
                .with_for_update(skip_locked=True)
            )
            if not profile:
                continue
            policy = db.get(RelationshipPrivacy, owner)
            threshold = stamp - timedelta(days=policy.history_days if policy else 90)
            turns = list(
                db.scalars(
                    select(RelationshipTurn.id)
                    .where(
                        RelationshipTurn.owner == owner,
                        RelationshipTurn.created_at < threshold,
                    )
                    .order_by(RelationshipTurn.created_at)
                    .limit(100)
                )
            )
            if turns:
                db.execute(
                    RelationshipFeedback.__table__.delete().where(
                        RelationshipFeedback.owner == owner,
                        RelationshipFeedback.turn_id.in_(turns),
                    )
                )
                db.execute(
                    RelationshipItem.__table__.update()
                    .where(
                        RelationshipItem.owner == owner,
                        RelationshipItem.source_turn_id.in_(turns),
                    )
                    .values(source_turn_id=None)
                )
                db.execute(
                    RelationshipJob.__table__.update()
                    .where(
                        RelationshipJob.owner == owner,
                        RelationshipJob.turn_id.in_(turns),
                    )
                    .values(turn_id=None)
                )
                db.execute(
                    RelationshipTurn.__table__.delete().where(
                        RelationshipTurn.owner == owner, RelationshipTurn.id.in_(turns)
                    )
                )
                deleted["turns"] += len(turns)
            imports = list(
                db.scalars(
                    select(RelationshipItem)
                    .where(
                        RelationshipItem.owner == owner,
                        RelationshipItem.kind == "chat_log",
                        RelationshipItem.created_at < threshold,
                    )
                    .order_by(RelationshipItem.created_at)
                    .limit(100)
                )
            )
            for item in imports:
                schedule_index(db, item, deleted=True)
                db.delete(item)
            deleted["imports"] += len(imports)
            jobs = list(
                db.scalars(
                    select(RelationshipJob)
                    .where(
                        RelationshipJob.owner == owner,
                        RelationshipJob.created_at < threshold,
                        RelationshipJob.status.not_in(["queued", "running"]),
                    )
                    .order_by(RelationshipJob.created_at)
                    .limit(100)
                )
            )
            for job in jobs:
                with checkpoint_store() as saver:
                    saver.delete_thread(f"xinyu:{owner}:{job.id}")
                db.delete(job)
            deleted["jobs"] += len(jobs)
            if turns or imports or jobs:
                record_deletion(db, owner, "expiry", threshold.isoformat())
            db.commit()
    rules = [
        (RelationshipSession, RelationshipSession.expires_at < stamp),
        (
            RelationshipAuthEvent,
            RelationshipAuthEvent.created_at < stamp - timedelta(days=30),
        ),
        (
            RelationshipLoginLimit,
            RelationshipLoginLimit.reset_at < stamp - timedelta(days=1),
        ),
        (
            RelationshipEmailChallenge,
            RelationshipEmailChallenge.expires_at < stamp - timedelta(days=1),
        ),
        (
            RelationshipEmailOutbox,
            (RelationshipEmailOutbox.created_at < stamp - timedelta(days=7))
            & RelationshipEmailOutbox.status.in_(["sent", "failed", "expired"]),
        ),
        (
            RelationshipModelCall,
            (RelationshipModelCall.created_at < stamp - timedelta(days=14))
            & (RelationshipModelCall.status != "reserved"),
        ),
        (
            RelationshipDailyUsage,
            (
                RelationshipDailyUsage.day
                < (stamp - timedelta(days=14)).strftime("%Y-%m-%d")
            )
            & (RelationshipDailyUsage.reserved_tokens == 0),
        ),
        (
            RelationshipNotice,
            (RelationshipNotice.created_at < stamp - timedelta(days=30))
            & RelationshipNotice.status.in_(["read", "cancelled"]),
        ),
    ]
    for model, condition in rules:
        with Session(get_engine()) as db:
            keys = list(model.__table__.primary_key.columns)
            rows = db.execute(
                select(*keys)
                .where(condition)
                .limit(200)
                .with_for_update(skip_locked=True)
            ).all()
            for values in rows:
                result = db.execute(
                    model.__table__.delete().where(
                        *(key == value for key, value in zip(keys, values, strict=True))
                    )
                )
                deleted["metadata"] += result.rowcount
            db.commit()
    deleted["graphs"] = prune_graphs(stamp)
    return deleted
