"""Vector admission, exclusive source writers and bounded metadata-only reconciliation."""

import re
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from psycopg import Connection
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models.relationship import (
    RelationshipClosedSpace,
    RelationshipItem,
    RelationshipRagIndex,
    RelationshipStoragePolicy,
)
from app.repositories.database import get_engine
from app.services.storage_budget import lock_storage


def audit_scope():
    return get_settings().rag_collection_override or "application-collections"


@contextmanager
def vector_guard(source_id=None, *, audit=False):
    """Dedicated autocommit session: no pooled session-lock leak and no open write transaction."""
    settings = get_settings()
    url = make_url(settings.database_url).set(drivername="postgresql")
    with Connection.connect(
        url.render_as_string(hide_password=False),
        autocommit=True,
        connect_timeout=settings.database_connect_timeout,
        application_name="xinyu-vector-guard",
        options=f"-c statement_timeout={settings.database_statement_timeout_ms}",
    ) as conn:
        mode = "" if audit else "_shared"
        if not conn.execute(
            f"SELECT pg_try_advisory_lock{mode}(718302640,0)"
        ).fetchone()[0]:
            yield None
            return
        if (
            source_id is not None
            and not conn.execute(
                "SELECT pg_try_advisory_lock(718302641,hashtext(%s))", (source_id,)
            ).fetchone()[0]
        ):
            yield None
            return
        yield conn
    # Physical connection close releases all session locks, even on exceptions.


def vector_summary(db, owner=None):
    statement = select(func.coalesce(func.sum(RelationshipRagIndex.reserved_points), 0))
    if owner is not None:
        statement = statement.where(RelationshipRagIndex.owner == owner)
    policy = db.get(RelationshipStoragePolicy, 1)
    return {
        "allocated_points": db.scalar(statement),
        "limit_points": get_settings().owner_vector_points
        if owner
        else get_settings().global_vector_points,
        "untracked_points": policy.vector_untracked_points
        if policy and owner is None
        else 0,
        "audited_points": policy.vector_observed_points
        if policy and owner is None
        else None,
        "audited_at": policy.vector_audited_at.isoformat()
        if policy and policy.vector_audited_at
        else None,
        "audit_age_seconds": max(
            0, int((datetime.now(UTC) - policy.vector_audited_at).total_seconds())
        )
        if policy and policy.vector_audited_at
        else None,
        "note": "分配量包括可检索片段、执行预留和待确认清理的占用；不是磁盘字节或实时向量计数。",
    }


def ensure_vector_audit():
    with Session(get_engine()) as db:
        policy = db.get(RelationshipStoragePolicy, 1)
        if policy and policy.vector_scope == audit_scope() and policy.vector_audited_at:
            return True
    return reconcile_vectors()


def reconcile_vectors():
    """Publish a complete scan only; never delete an unattributed vector automatically."""
    from app.services.relationship_rag import managed_collections, vector_client

    with vector_guard(audit=True) as guard:
        if guard is None:
            return False
        observed = Counter()
        untracked = 0
        observed_total = 0
        deadline = time.monotonic() + 60
        client = vector_client()
        for collection in managed_collections():
            offset = None
            collection_total = 0
            while True:
                if time.monotonic() > deadline:
                    raise AppError(
                        503,
                        "VECTOR_AUDIT_TIMEOUT",
                        "索引容量核对超时，未发布不完整结果",
                    )
                guard.execute("SELECT 1")
                rows, offset = client.scroll(
                    collection,
                    limit=1000,
                    offset=offset,
                    with_payload=["owner", "source_id"],
                    with_vectors=False,
                )
                collection_total += len(rows)
                for row in rows:
                    payload = row.payload or {}
                    owner, source = payload.get("owner"), payload.get("source_id")
                    try:
                        valid = (
                            isinstance(owner, str)
                            and re.fullmatch(r"[a-f0-9]{64}", owner)
                            and isinstance(source, str)
                            and str(UUID(source)) == source
                        )
                    except ValueError:
                        valid = False
                    if valid:
                        observed[owner, source] += 1
                    else:
                        untracked += 1
                if offset is None:
                    break
            if client.count(collection, exact=True).count != collection_total:
                raise AppError(
                    503,
                    "VECTOR_AUDIT_UNSTABLE",
                    "向量数量在核对期间变化，未发布不完整结果",
                )
            observed_total += collection_total
        guard.execute("SELECT 1")
        with Session(get_engine()) as db:
            lock_storage(db)
            # Only counters/tombstones are reconciled. Preserve over-limit legacy content.
            db.execute(text("SELECT set_config('xinyu.vector_bypass','on',true)"))
            db.execute(text("SELECT set_config('xinyu.storage_bypass','on',true)"))
            existing = {
                row.item_id: row for row in db.scalars(select(RelationshipRagIndex))
            }
            items = {
                row.id: row
                for row in db.execute(
                    select(
                        RelationshipItem.id,
                        RelationshipItem.owner,
                        RelationshipItem.status,
                        RelationshipItem.revision,
                    )
                )
            }
            closed = {
                row.owner: row
                for row in db.scalars(
                    select(RelationshipClosedSpace).where(
                        RelationshipClosedSpace.cleanup_at.is_not(None)
                    )
                )
            }
            for source_id, row in existing.items():
                row.reserved_points = observed.pop((row.owner, source_id), 0)
                if row.reserved_points and row.owner in closed:
                    closed[row.owner].cleanup_at = None
                if row.status == "indexing" or (
                    row.status == "ready" and row.reserved_points < row.chunk_count
                ):
                    item = items.get(source_id)
                    row.status = (
                        "pending" if item and item.status == "active" else "deleting"
                    )
                    row.error, row.attempts = "", 0
                row.chunk_count = min(row.chunk_count, row.reserved_points)
            for (owner, source_id), count in observed.items():
                item = items.get(source_id)
                if source_id in existing or (item and item.owner != owner):
                    untracked += count
                    continue
                db.add(
                    RelationshipRagIndex(
                        item_id=source_id,
                        owner=owner,
                        revision=item.revision if item else 0,
                        status="pending"
                        if item and item.status == "active"
                        else "deleting",
                        signature="",
                        chunk_count=0,
                        reserved_points=count,
                        attempts=0,
                        error="",
                        updated_at=datetime.now(UTC),
                    )
                )
                if owner in closed:
                    closed[owner].cleanup_at = None
            policy = db.get(RelationshipStoragePolicy, 1)
            if not policy:
                raise AppError(503, "STORAGE_POLICY_MISSING", "存储预算尚未初始化")
            policy.vector_untracked_points = untracked
            policy.vector_observed_points = observed_total
            policy.vector_scope, policy.vector_audited_at = (
                audit_scope(),
                datetime.now(UTC),
            )
            db.commit()
        return True


def audit_vectors_if_due():
    with Session(get_engine()) as db:
        policy = db.get(RelationshipStoragePolicy, 1)
        if (
            policy
            and policy.vector_scope == audit_scope()
            and policy.vector_audited_at
            and (datetime.now(UTC) - policy.vector_audited_at).total_seconds() < 300
        ):
            return False
    return reconcile_vectors()
