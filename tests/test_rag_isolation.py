import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest
from qdrant_client import models
from sqlalchemy.orm import Session

from app.models.relationship import RelationshipItem, RelationshipRagIndex
from app.repositories.database import get_engine
from app.services import relationship_rag as rag


@pytest.fixture
def vector_space(backend, monkeypatch):
    backend.rag_collection_override = "xinyu_eval_" + uuid4().hex[:16]
    name = backend.rag_collection_override
    rag.vector_client.cache_clear()
    client = rag.vector_client()
    deadline = time.monotonic() + 30
    while True:
        try:
            client.get_collections()
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.3)
    assert not client.collection_exists(name)

    class Encoder:
        def embed(self, texts, **_):
            for _ in texts:
                yield np.asarray([1.0, *([0.0] * 511)], dtype=np.float32)

        def query_embed(self, _):
            yield np.asarray([1.0, *([0.0] * 511)], dtype=np.float32)

    monkeypatch.setattr(rag, "embedder", lambda: Encoder())
    rag.ensure_collection()
    try:
        yield client
    finally:
        assert name.startswith("xinyu_eval_") and len(name) == 27
        client.delete_collection(name)
        client.close()
        rag.vector_client.cache_clear()


def seed(owner, title):
    with Session(get_engine()) as db:
        item = RelationshipItem(
            id=str(uuid4()),
            owner=owner,
            kind="memory",
            title=title,
            quote=title,
            status="active",
            created_at=datetime.now(UTC),
            revision=1,
            details={},
        )
        db.add(item)
        rag.schedule_index(db, item)
        db.commit()
        item_id = item.id
    assert rag.process_index()
    return item_id


def test_actual_vector_query_and_sql_fallback_are_owner_scoped(backend, vector_space):
    own, other = uuid4().hex + uuid4().hex, uuid4().hex + uuid4().hex
    item_id = seed(own, "她不喝咖啡。")
    seed(other, "DECOY_ONLY_7Q4 她喜欢咖啡。")
    with Session(get_engine()) as db:
        result = rag.retrieve(db, own, "咖啡", rerank=False)
    assert result["sources"] and {row["source_id"] for row in result["sources"]} == {
        item_id
    }
    assert "DECOY_ONLY_7Q4" not in str(result)
    count = vector_space.count(
        rag.collection_name(), count_filter=rag.source_filter(own), exact=True
    )
    assert count.count == 1


@pytest.mark.parametrize("change", ["edit", "delete", "cancel"])
def test_concurrent_change_during_embedding_invalidates_lexical_and_dense_hits(
    backend, vector_space, monkeypatch, change
):
    owner = uuid4().hex + uuid4().hex
    item_id = seed(owner, "旧偏好：喜欢全糖奶茶。")

    class ChangingEncoder:
        def query_embed(self, _):
            with Session(get_engine()) as db:
                item = db.get(RelationshipItem, item_id)
                if change == "delete":
                    rag.schedule_index(db, item, deleted=True)
                    db.delete(item)
                else:
                    item.revision += 1
                    if change == "edit":
                        item.title = item.quote = "新偏好：只喝无糖奶茶。"
                    else:
                        item.status = "cancelled"
                    rag.schedule_index(db, item)
                db.commit()
            yield np.asarray([1.0, *([0.0] * 511)], dtype=np.float32)

    monkeypatch.setattr(rag, "embedder", ChangingEncoder)
    with Session(get_engine()) as db:
        result = rag.retrieve(db, owner, "全糖奶茶", rerank=False)
    assert not result["sources"]


def test_real_vector_delete_failure_is_durable_and_retried(
    backend, vector_space, monkeypatch
):
    owner = uuid4().hex + uuid4().hex
    item_id = seed(owner, "合成的待删除偏好。")
    with Session(get_engine()) as db:
        item = db.get(RelationshipItem, item_id)
        rag.schedule_index(db, item, deleted=True)
        db.delete(item)
        db.commit()
    original = vector_space.delete

    def fail(*_, **__):
        raise ConnectionError("synthetic transport outage")

    monkeypatch.setattr(vector_space, "delete", fail)
    assert rag.process_index()
    with Session(get_engine()) as db:
        row = db.get(RelationshipRagIndex, item_id)
        assert row.status == "delete_failed"
        row.updated_at = datetime.now(UTC) - timedelta(seconds=31)
        db.commit()
    monkeypatch.setattr(vector_space, "delete", original)
    assert rag.process_index()
    with Session(get_engine()) as db:
        assert db.get(RelationshipRagIndex, item_id) is None
    assert (
        vector_space.count(
            rag.collection_name(),
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="owner", match=models.MatchValue(value=owner)
                    )
                ]
            ),
            exact=True,
        ).count
        == 0
    )
