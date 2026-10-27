import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.main import app
from app.models.relationship import (
    RelationshipAccount,
    RelationshipAuthEvent,
    RelationshipClosedSpace,
    RelationshipFeedback,
    RelationshipItem,
    RelationshipJob,
    RelationshipNotice,
    RelationshipProfile,
    RelationshipRagIndex,
    RelationshipSession,
    RelationshipTurn,
)
from app.repositories.database import get_engine
from app.services import auth_cache, relationship_rag
from app.services.account_closure import checkpoint_count, reconcile_closed_spaces
from app.services.graph_checkpoints import checkpoint_store, clear_checkpoints
from app.services.relationship_jobs import get_profile, process_one
from tests.test_email_recovery import PASSWORD, captcha, register
from tests.test_job_reliability import create_job, install_blocking_agent


@pytest.fixture
def identity(backend, monkeypatch):
    monkeypatch.setattr(
        auth_cache, "captcha_secret", lambda: b"synthetic-closure-fixture-secret!!"
    )
    monkeypatch.setattr(auth_cache.secrets, "choice", lambda _: "A")
    monkeypatch.setattr(
        auth_cache, "render_captcha", lambda _: "data:image/png;base64,synthetic"
    )
    with TestClient(app) as client:
        client.get("/api/relationship/state")
        original_guest = client.cookies.get("relationship_session")
        register(client)
        with Session(get_engine()) as db:
            owner = db.get(RelationshipAccount, "email_fixture").owner
        yield client, owner, original_guest


def delete_account(client, password=PASSWORD):
    return client.request(
        "DELETE",
        "/api/relationship/auth/account",
        json={
            **captcha(client),
            "current_password": password,
            "confirmation": "注销账号",
        },
    )


def add_turn(owner):
    turn_id = str(uuid4())
    with Session(get_engine()) as db:
        db.add(
            RelationshipTurn(
                id=turn_id,
                owner=owner,
                request_id=str(uuid4()),
                content="PRIVATE_SYNTHETIC_FIXTURE",
                result={"analysis": "fixture", "replies": [], "candidates": []},
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
    return turn_id


def test_feedback_update_export_delete_and_owner_isolation(identity):
    client, owner, _ = identity
    turn_id = add_turn(owner)
    path = f"/api/relationship/feedback/{turn_id}"
    payload = {
        "rating": "unhelpful",
        "category": "tone",
        "comment": "synthetic feedback",
    }
    assert client.put(path, json=payload).status_code == 200
    assert client.get(path).json()["feedback"]["comment"] == payload["comment"]
    assert client.put(path, json={**payload, "rating": "helpful"}).status_code == 200
    with Session(get_engine()) as db:
        assert db.scalar(select(func.count()).select_from(RelationshipFeedback)) == 1
    with TestClient(app) as other:
        assert other.get(path).status_code == 404
        assert other.put(path, json=payload).status_code == 404
        assert other.delete(path).status_code == 404
    exported = client.get("/api/relationship/export")
    assert exported.json()["feedback"][0]["rating"] == "helpful"
    assert exported.headers["Cache-Control"] == "no-store"
    assert client.delete(path).status_code == 200
    assert client.get(path).json()["feedback"] is None


def test_history_clear_removes_feedback_with_content(identity):
    client, owner, _ = identity
    turn_id = add_turn(owner)
    client.put(f"/api/relationship/feedback/{turn_id}", json={"rating": "helpful"})
    assert client.delete("/api/relationship/history").status_code == 200
    with Session(get_engine()) as db:
        assert db.scalar(select(func.count()).select_from(RelationshipFeedback)) == 0
        assert db.get(RelationshipAccount, "email_fixture") is not None


def test_account_closure_revokes_sessions_blocks_original_guest_and_queues_index_cleanup(
    identity,
):
    client, owner, original_guest = identity
    original_session = client.cookies.get("relationship_session")
    assert client.get("/api/relationship/auth/me").json()["username"] == "email_fixture"
    turn_id = add_turn(owner)
    client.put(f"/api/relationship/feedback/{turn_id}", json={"rating": "helpful"})
    assert (
        client.post(
            "/api/relationship/items",
            json={"kind": "memory", "title": "synthetic preference"},
        ).status_code
        == 200
    )
    result = delete_account(client)
    assert result.status_code == 202, result.text
    receipt = result.json()["receipt"]
    with Session(get_engine()) as db:
        for model in (
            RelationshipAccount,
            RelationshipProfile,
            RelationshipTurn,
            RelationshipItem,
            RelationshipJob,
            RelationshipFeedback,
            RelationshipNotice,
            RelationshipAuthEvent,
            RelationshipSession,
        ):
            assert (
                db.scalar(
                    select(func.count()).select_from(model).where(model.owner == owner)
                )
                == 0
            )
        assert db.get(RelationshipClosedSpace, owner)
        assert (
            db.scalar(
                select(RelationshipRagIndex.status).where(
                    RelationshipRagIndex.owner == owner
                )
            )
            == "deleting"
        )
    for old_cookie in (original_guest, original_session):
        with TestClient(app) as stale:
            stale.cookies.set(
                "relationship_session",
                old_cookie,
                domain="testserver.local",
                path="/api/relationship",
            )
            assert stale.get("/api/relationship/auth/me").json()["username"] is None
            state = stale.get("/api/relationship/state").json()
            assert not state["turns"] and not state["item_counts"].get("memory")
    status = client.post(
        "/api/relationship/privacy/deletion/status", json={"receipt": receipt}
    )
    assert status.status_code == 200 and status.json()["status"] == "pending"
    assert status.json()["pending_indexes"] == 1
    assert (
        client.post(
            "/api/relationship/privacy/deletion/status", json={"receipt": "Z" * 43}
        ).status_code
        == 404
    )
    with Session(get_engine()) as db, pytest.raises(AppError):
        get_profile(db, owner)


def test_account_closure_requires_current_password(identity):
    client, owner, _ = identity
    assert delete_account(client, "incorrect").status_code == 401
    with Session(get_engine()) as db:
        assert db.get(RelationshipAccount, "email_fixture") and db.get(
            RelationshipProfile, owner
        )
        assert not db.get(RelationshipClosedSpace, owner)


def test_account_closure_during_model_call_cannot_resurrect(identity, monkeypatch):
    client, owner, _ = identity
    entered, release = threading.Event(), threading.Event()
    install_blocking_agent(monkeypatch, entered, release)
    create_job(owner)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(process_one)
        try:
            assert entered.wait(5)
            assert delete_account(client).status_code == 202
        finally:
            release.set()
        assert future.result(timeout=10)
    with Session(get_engine()) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RelationshipTurn)
                .where(RelationshipTurn.owner == owner)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(RelationshipJob)
                .where(RelationshipJob.owner == owner)
            )
            == 0
        )
    assert checkpoint_count(owner) == 0


def test_deletion_receipt_completes_only_after_index_cleanup_and_settle_window(
    identity, monkeypatch
):
    client, owner, _ = identity
    client.post(
        "/api/relationship/items",
        json={"kind": "memory", "title": "synthetic preference"},
    )
    receipt = delete_account(client).json()["receipt"]
    assert reconcile_closed_spaces() == 0
    with Session(get_engine()) as db:
        db.get(RelationshipClosedSpace, owner).closed_at = datetime.now(
            UTC
        ) - timedelta(hours=1)
        db.commit()
    assert reconcile_closed_spaces() == 0
    deleted = []

    class VectorFixture:
        def get_collections(self):
            return SimpleNamespace(
                collections=[SimpleNamespace(name=relationship_rag.collection_name())]
            )

        def delete(self, *args, **kwargs):
            deleted.append((args, kwargs))

    monkeypatch.setattr(relationship_rag, "vector_client", VectorFixture)
    assert relationship_rag.process_index() and deleted
    assert reconcile_closed_spaces() == 1
    status = client.post(
        "/api/relationship/privacy/deletion/status", json={"receipt": receipt}
    ).json()
    assert status["status"] == "completed" and status["pending_indexes"] == 0


def test_batched_checkpoint_cleanup_removes_orphans_and_preserves_other_spaces(
    backend, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    release.set()
    install_blocking_agent(monkeypatch, entered, release)
    first, _, _ = create_job()
    second, _, _ = create_job()
    assert process_one() and process_one()
    assert checkpoint_count(first) > 0 and checkpoint_count(second) > 0
    with checkpoint_store() as saver, saver.conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM checkpoints WHERE thread_id LIKE %s",
            ("xinyu:" + first + ":%",),
        )
        cursor.execute(
            "SELECT COUNT(*) AS count FROM checkpoint_blobs WHERE thread_id LIKE %s",
            ("xinyu:" + first + ":%",),
        )
        assert cursor.fetchone()["count"] > 0
    clear_checkpoints(first)
    with checkpoint_store() as saver, saver.conn.cursor() as cursor:
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            cursor.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE thread_id LIKE %s",
                ("xinyu:" + first + ":%",),
            )
            assert cursor.fetchone()["count"] == 0
    assert checkpoint_count(second) > 0
