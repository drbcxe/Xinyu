import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest
from qdrant_client import models
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.relationship import (
    RelationshipItem,
    RelationshipRagIndex,
    RelationshipStoragePolicy,
)
from app.repositories.database import get_engine
from app.services import relationship_rag as rag
from app.services.storage_budget import configure_storage
from app.services.vector_capacity import reconcile_vectors, vector_guard
from tests.test_rag_isolation import vector_space as vector_space  # noqa: PLC0414 - pytest fixture re-export


def pending(owner=None, text_value="synthetic preference", *, chat=False):
    owner = owner or secrets.token_hex(32)
    with Session(get_engine()) as db:
        item = RelationshipItem(
            id=str(uuid4()),
            owner=owner,
            kind="chat_log" if chat else "memory",
            title="synthetic chat" if chat else text_value,
            quote="",
            status="active",
            revision=1,
            created_at=datetime.now(UTC),
            details={"text": text_value} if chat else {},
        )
        db.add(item)
        rag.schedule_index(db, item)
        db.commit()
        return item.id, owner


def allocated(item_id):
    with Session(get_engine()) as db:
        row = db.get(RelationshipRagIndex, item_id)
        return row.reserved_points if row else 0


def test_raw_growth_requires_a_complete_vector_audit(backend):
    item_id, _ = pending()
    with Session(get_engine()) as db, pytest.raises(AppError) as caught:
        db.get(RelationshipRagIndex, item_id).reserved_points = 1
        db.commit()
    assert caught.value.code == "VECTOR_AUDIT_REQUIRED" and allocated(item_id) == 0


def test_atomic_owner_and_global_point_admission(backend, vector_space):
    assert reconcile_vectors()
    backend.owner_vector_points, backend.global_vector_points = 3, 20
    configure_storage()
    owner = secrets.token_hex(32)
    ids = [pending(owner)[0] for _ in range(8)]

    def reserve(item_id):
        try:
            with Session(get_engine()) as db:
                db.get(RelationshipRagIndex, item_id).reserved_points = 1
                db.commit()
            return True
        except AppError as exc:
            assert exc.code == "VECTOR_QUOTA_EXCEEDED"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, ids)) == 3
    backend.global_vector_points = 4
    configure_storage()
    second_id, _ = pending()
    third_id, _ = pending()
    with Session(get_engine()) as db:
        db.get(RelationshipRagIndex, second_id).reserved_points = 1
        db.commit()
    with Session(get_engine()) as db, pytest.raises(AppError) as caught:
        db.get(RelationshipRagIndex, third_id).reserved_points = 1
        db.commit()
    assert (
        caught.value.code == "GLOBAL_VECTOR_QUOTA_EXCEEDED" and allocated(third_id) == 0
    )
    with Session(get_engine()) as db:
        assert db.scalar(select(func.sum(RelationshipRagIndex.reserved_points))) == 4


def test_quota_blocks_before_embedding_and_keeps_full_lexical_search(
    backend, vector_space, monkeypatch
):
    backend.owner_vector_points = 1
    configure_storage()
    text_value = "SYNTHETIC_UNIQUE_END " + "some repeated synthetic context. " * 100
    item_id, owner = pending(text_value=text_value, chat=True)
    with Session(get_engine()) as db:
        assert len(rag.chunks_for(db.get(RelationshipItem, item_id))) > 1
    calls = []
    monkeypatch.setattr(rag, "embedder", lambda: calls.append(1))
    assert rag.process_index() and not calls
    assert (
        not rag.process_index()
    )  # Permanent capacity errors are not automatically retried.
    with Session(get_engine()) as db:
        row = db.get(RelationshipRagIndex, item_id)
        assert row.status == "quota_blocked" and "关键词" in row.error
        assert (
            row.reserved_points == 0
            and db.get(RelationshipItem, item_id).details["text"] == text_value
        )
        backend.rag_dense_enabled = False
        result = rag.retrieve(db, owner, "SYNTHETIC_UNIQUE_END", rerank=False)
        assert result["sources"] and result["sources"][0]["source_id"] == item_id


def test_acknowledged_but_incomplete_delete_keeps_budget(
    backend, vector_space, monkeypatch
):
    item_id, owner = pending()
    assert rag.process_index() and allocated(item_id) == 1
    with Session(get_engine()) as db:
        item = db.get(RelationshipItem, item_id)
        rag.schedule_index(db, item, deleted=True)
        db.delete(item)
        db.commit()
    delete = vector_space.delete
    monkeypatch.setattr(vector_space, "delete", lambda *_, **__: None)
    assert rag.process_index()
    with Session(get_engine()) as db:
        row = db.get(RelationshipRagIndex, item_id)
        assert row.status == "delete_failed" and row.reserved_points == 1
        row.updated_at = datetime.now(UTC) - timedelta(seconds=31)
        db.commit()
    assert (
        vector_space.count(
            rag.collection_name(), count_filter=rag.source_filter(owner), exact=True
        ).count
        == 1
    )
    monkeypatch.setattr(vector_space, "delete", delete)
    assert rag.process_index() and allocated(item_id) == 0
    assert (
        vector_space.count(
            rag.collection_name(), count_filter=rag.source_filter(owner), exact=True
        ).count
        == 0
    )


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_two_workers_and_source_change_during_embedding(
    backend, vector_space, monkeypatch, change
):
    item_id, owner = pending(text_value="synthetic old preference")
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Encoder:
        def embed(self, texts, **_):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                assert release.wait(10)
            return iter(
                np.asarray([1.0, *([0.0] * 511)], dtype=np.float32) for _ in texts
            )

    monkeypatch.setattr(rag, "embedder", Encoder)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rag.process_index)
        try:
            assert entered.wait(6)
            assert (
                not rag.process_index()
            )  # Same source is guarded; no second encoder or purge.
            with vector_guard(item_id) as guard:
                assert guard is None
            with Session(get_engine()) as db:
                assert (
                    db.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND application_name='xinyu-vector-guard' AND state='idle' AND xact_start IS NULL"
                        )
                    )
                    >= 1
                )
                item = db.get(RelationshipItem, item_id)
                if change == "edit":
                    item.title, item.revision = (
                        "synthetic new preference",
                        item.revision + 1,
                    )
                    rag.schedule_index(db, item)
                else:
                    rag.schedule_index(db, item, deleted=True)
                    db.delete(item)
                db.commit()  # Source edits do not wait for embedding/network work.
            assert allocated(item_id) == 1
        finally:
            release.set()
        assert future.result(timeout=10)
    with Session(get_engine()) as db:
        assert db.get(RelationshipRagIndex, item_id).status == (
            "pending" if change == "edit" else "deleting"
        )
    assert allocated(item_id) == 0 and rag.process_index()
    rows, _ = vector_space.scroll(
        rag.collection_name(), scroll_filter=rag.source_filter(owner), with_payload=True
    )
    assert len(rows) == (1 if change == "edit" else 0)
    if rows:
        assert (
            rows[0].payload["quote"] == "synthetic new preference"
            and rows[0].payload["revision"] == 2
        )


def test_partial_upsert_reserves_until_verified_cleanup(
    backend, vector_space, monkeypatch
):
    item_id, owner = pending(text_value="synthetic context. " * 2400, chat=True)
    original, batches = vector_space.upsert, []

    def interrupted(*args, **kwargs):
        batches.append(1)
        if len(batches) == 2:
            raise ConnectionError("synthetic interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(vector_space, "upsert", interrupted)
    assert rag.process_index() and len(batches) == 2
    assert allocated(item_id) > 32
    assert (
        vector_space.count(
            rag.collection_name(), count_filter=rag.source_filter(owner), exact=True
        ).count
        == 32
    )
    with Session(get_engine()) as db:
        item = db.get(RelationshipItem, item_id)
        rag.schedule_index(db, item, deleted=True)
        db.delete(item)
        db.commit()
    assert allocated(item_id) > 32 and rag.process_index() and allocated(item_id) == 0
    assert (
        vector_space.count(
            rag.collection_name(), count_filter=rag.source_filter(owner), exact=True
        ).count
        == 0
    )


def test_audit_repairs_orphans_and_quarantines_unattributed_points(
    backend, vector_space
):
    source_id, owner = str(uuid4()), secrets.token_hex(32)
    vector = [1.0, *([0.0] * 511)]
    unknown_id = str(uuid4())
    vector_space.upsert(
        rag.collection_name(),
        [
            models.PointStruct(
                id=str(uuid4()),
                vector=vector,
                payload={"source_id": source_id, "owner": owner},
            ),
            models.PointStruct(
                id=unknown_id, vector=vector, payload={"unattributed": True}
            ),
        ],
        wait=True,
    )
    assert reconcile_vectors() and allocated(source_id) == 1
    with Session(get_engine()) as db:
        assert db.get(RelationshipStoragePolicy, 1).vector_untracked_points == 1
        assert db.get(RelationshipRagIndex, source_id).status == "deleting"
    assert rag.process_index() and allocated(source_id) == 0
    item_id, _ = pending()
    assert rag.process_index()
    with Session(get_engine()) as db:
        assert db.get(RelationshipRagIndex, item_id).status == "quota_blocked"
    assert vector_space.retrieve(
        rag.collection_name(), [unknown_id]
    )  # Never auto-delete unattributed data.


def test_incomplete_audit_never_publishes_partial_counts(
    backend, vector_space, monkeypatch
):
    item_id, _ = pending()
    assert rag.process_index()
    with Session(get_engine()) as db:
        previous = db.get(RelationshipStoragePolicy, 1).vector_audited_at
    original = vector_space.scroll
    calls = []

    def incomplete(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ConnectionError("synthetic audit interruption")
        rows, _ = original(*args, **kwargs)
        return rows, str(uuid4())

    monkeypatch.setattr(vector_space, "scroll", incomplete)
    with pytest.raises(ConnectionError):
        reconcile_vectors()
    with Session(get_engine()) as db:
        assert db.get(RelationshipStoragePolicy, 1).vector_audited_at == previous
    assert allocated(item_id) == 1


def test_late_orphan_reopens_closed_space_cleanup_receipt(backend, vector_space):
    from app.models.relationship import RelationshipClosedSpace
    from app.services.account_closure import reconcile_closed_spaces

    owner, source_id = secrets.token_hex(32), str(uuid4())
    with Session(get_engine()) as db:
        db.add(
            RelationshipClosedSpace(
                owner=owner,
                receipt_hash=secrets.token_hex(32),
                closed_at=datetime.now(UTC) - timedelta(days=1),
                cleanup_at=datetime.now(UTC),
            )
        )
        db.commit()
    vector_space.upsert(
        rag.collection_name(),
        [
            models.PointStruct(
                id=str(uuid4()),
                vector=[1.0, *([0.0] * 511)],
                payload={"owner": owner, "source_id": source_id},
            )
        ],
        wait=True,
    )
    assert reconcile_vectors()
    with Session(get_engine()) as db:
        assert db.get(RelationshipClosedSpace, owner).cleanup_at is None
    assert rag.process_index() and allocated(source_id) == 0
    assert reconcile_closed_spaces() == 1


def test_lowered_point_limit_preserves_old_vectors_and_allows_cleanup(
    backend, vector_space
):
    item_id, owner = pending(text_value="synthetic context. " * 80, chat=True)
    assert rag.process_index()
    old = allocated(item_id)
    assert old > 1
    backend.owner_vector_points = 1
    configure_storage()
    assert reconcile_vectors() and allocated(item_id) == old
    assert (
        vector_space.count(
            rag.collection_name(), count_filter=rag.source_filter(owner), exact=True
        ).count
        == old
    )
    second_id, _ = pending(owner)
    assert rag.process_index() and allocated(second_id) == 0
    with Session(get_engine()) as db:
        item = db.get(RelationshipItem, item_id)
        rag.schedule_index(db, item, deleted=True)
        db.delete(item)
        db.commit()
    assert rag.process_index() and allocated(item_id) == 0
    with Session(get_engine()) as db:
        rag.schedule_index(db, db.get(RelationshipItem, second_id))
        db.commit()
    assert rag.process_index() and allocated(second_id) == 1


def test_index_quota_status_can_use_system_headroom(backend, vector_space):
    from app.services.storage_budget import storage_summary

    item_id, owner = pending(text_value="synthetic context. " * 120, chat=True)
    with Session(get_engine()) as db:
        current = storage_summary(db, owner)["used_bytes"]
    backend.owner_storage_bytes = current + 150
    backend.owner_vector_points = 1
    configure_storage()
    assert rag.process_index()
    with Session(get_engine()) as db:
        row = db.get(RelationshipRagIndex, item_id)
        assert row.status == "quota_blocked" and "关键词" in row.error
        assert storage_summary(db, owner)["used_bytes"] <= backend.owner_storage_bytes
