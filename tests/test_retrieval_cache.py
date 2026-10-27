import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.relationship import RelationshipItem
from app.repositories.database import get_engine
from app.services import relationship_rag as rag
from app.services.retrieval_cache import CorpusCache, corpus_cache
from tests.test_resource_control import owner


def cached(cache, key, scope=None, size=60):
    return cache.get_or_build(
        key,
        scope or ("fixture", "owner"),
        lambda: (key, size),
        max_bytes=100,
        max_entries=2,
        ttl=10,
    )


def test_cache_bounds_expiry_and_oversized_values():
    clock = [0.0]
    cache = CorpusCache(clock=lambda: clock[0])
    assert cached(cache, "a") == ("a", False)
    assert cached(cache, "a") == ("a", True)
    cached(cache, "b")
    assert cache.stats()["entries"] == 1 and cache.stats()["estimated_bytes"] == 60
    assert cached(cache, "huge", size=101) == ("huge", False)
    assert cache.stats()["oversized"] == 1 and cache.stats()["estimated_bytes"] == 60
    clock[0] = 11
    cache.expire()
    assert cache.stats()["entries"] == 0 and cache.stats()["estimated_bytes"] == 0


def test_concurrent_queries_share_build_and_invalidation_fences_late_publish():
    cache = CorpusCache()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def build():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return "fixture", 20

    def read():
        return cache.get_or_build(
            "same", ("db", "owner"), build, max_bytes=100, max_entries=2, ttl=10
        )[0]

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(read) for _ in range(4)]
        assert entered.wait(5)
        cache.invalidate(("db", "owner"))
        release.set()
        assert [f.result(timeout=5) for f in futures] == ["fixture"] * 4
    assert len(calls) == 1 and cache.stats()["entries"] == 0
    cached(cache, "new", scope=("db", "owner"))
    cached(cache, "other", scope=("db", "other"), size=20)
    cache.invalidate(("db", "owner"))
    assert cache.stats()["entries"] == 1


def seed(value, text, kind="memory"):
    with Session(get_engine()) as db:
        item = RelationshipItem(
            id=str(uuid4()),
            owner=value,
            kind=kind,
            title=text if kind == "memory" else "synthetic import",
            quote="",
            status="active",
            created_at=datetime.now(UTC),
            revision=1,
            details={"text": text} if kind == "chat_log" else {},
        )
        db.add(item)
        db.commit()
        return item.id


def test_warm_retrieval_reuses_chunks_and_observes_other_process_edits(
    backend, monkeypatch
):
    backend.rag_dense_enabled = False
    value = owner()
    item_id = seed(value, "她喜欢安静的咖啡馆。")
    calls = []
    original = rag.chunks_for

    def counted(item):
        calls.append(item.id)
        return original(item)

    monkeypatch.setattr(rag, "chunks_for", counted)
    with Session(get_engine()) as db:
        first = rag.retrieve(db, value, "咖啡馆", rerank=False)
        second = rag.retrieve(db, value, "咖啡馆", rerank=False)
    assert (
        first["sources"] == second["sources"]
        and not first["cache_hit"]
        and second["cache_hit"]
    )
    assert calls == [item_id]
    # No local invalidation: represents an edit performed by a different API/Worker process.
    with Session(get_engine()) as db:
        item = db.get(RelationshipItem, item_id)
        item.title = "她现在不喝咖啡，只喝热茶。"
        item.revision += 1
        db.commit()
    with Session(get_engine()) as db:
        changed = rag.retrieve(db, value, "热茶", rerank=False)
    assert changed["sources"][0]["revision"] == 2 and not changed["cache_hit"]
    assert calls == [item_id, item_id]
    other = owner()
    with Session(get_engine()) as db:
        assert not rag.retrieve(db, other, "热茶", rerank=False)["sources"]
    assert corpus_cache.stats()["entries"] <= backend.rag_cache_entries


def test_cache_separates_corpus_kind_and_explicit_delete(backend):
    backend.rag_dense_enabled = False
    value = owner()
    memory = seed(value, "偏好甲：喜欢热茶。")
    chat = seed(value, "聊天乙：喜欢热茶。", "chat_log")
    with Session(get_engine()) as db:
        assert {
            s["source_id"]
            for s in rag.retrieve(db, value, "热茶", kind="memory", rerank=False)[
                "sources"
            ]
        } == {memory}
        assert {
            s["source_id"]
            for s in rag.retrieve(db, value, "热茶", kind="chat_log", rerank=False)[
                "sources"
            ]
        } == {chat}
    with Session(get_engine()) as db:
        item = db.get(RelationshipItem, memory)
        rag.schedule_index(db, item, deleted=True)
        db.delete(item)
        db.commit()
    with Session(get_engine()) as db:
        assert not rag.retrieve(db, value, "热茶", kind="memory", rerank=False)[
            "sources"
        ]


def test_full_corpus_remains_searchable_beyond_previous_silent_chunk_cutoff(
    backend, monkeypatch
):
    backend.rag_dense_enabled = False
    value = owner()
    item_id = seed(value, "noise unique")

    def chunks(item):
        base = {
            "owner": value,
            "source_id": item_id,
            "revision": 1,
            "kind": "memory",
            "title": item.title,
        }
        return [
            {
                **base,
                "point_id": str(i),
                "citation_id": str(i),
                "quote": "noise",
                "offset": 0,
                "end_offset": 5,
            }
            for i in range(12000)
        ] + [
            {
                **base,
                "point_id": "target",
                "citation_id": "target",
                "quote": "unique",
                "offset": 6,
                "end_offset": 12,
            }
        ]

    monkeypatch.setattr(rag, "chunks_for", chunks)
    with Session(get_engine()) as db:
        found = rag.retrieve(db, value, "unique", rerank=False)
    assert found["lexical_chunks"] == 12001 and found["sources"][0]["quote"] == "unique"


def test_many_source_spans_are_revalidated_with_one_owner_scoped_read(backend):
    value, other = owner(), owner()
    item_id = seed(value, "合成原文热茶。" * 1000, "chat_log")
    other_id = seed(other, "OTHER_PRIVATE_SENTINEL", "chat_log")
    with Session(get_engine()) as db:
        hits = rag.chunks_for(db.get(RelationshipItem, item_id))[:16]
        foreign = rag.chunks_for(db.get(RelationshipItem, other_id))
        db.rollback()
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            if "SELECT" in statement and "relationship_items" in statement:
                statements.append(statement)

        event.listen(get_engine(), "before_cursor_execute", capture)
        try:
            found = rag.current_sources(db, value, [*hits, *foreign])
        finally:
            event.remove(get_engine(), "before_cursor_execute", capture)
    assert len(found) == len(hits) and len(statements) == 1
    assert "relationship_items.owner =" in statements[0]
    assert "OTHER_PRIVATE_SENTINEL" not in str(found)
