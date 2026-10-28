import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.main import app
from app.models.relationship import (
    RelationshipAuthEvent,
    RelationshipFeedback,
    RelationshipItem,
    RelationshipJob,
    RelationshipPrivacy,
    RelationshipRagIndex,
    RelationshipTurn,
)
from app.providers import model_provider
from app.repositories.database import get_engine
from app.services.data_retention import prune_graphs, prune_online_data
from app.services.graph_checkpoints import checkpoint_store
from app.services.privacy_policy import require_processing
from app.services.relationship_jobs import process_one
from app.services.resource_control import operation_scope
from tests.test_job_reliability import create_job, install_blocking_agent
from tests.test_read_models import fixture_owner
from tests.test_resource_control import owner


def choices(client, allow=True, days=90):
    value = client.get("/api/relationship/privacy").json()
    return client.put(
        "/api/relationship/privacy",
        json={
            "version": value["version"],
            "provider_fingerprint": value["provider_fingerprint"],
            "allow_model_processing": allow,
            "history_days": days,
        },
    )


def test_missing_consent_blocks_generation_and_ocr_before_admission(backend):
    with TestClient(app) as client:
        value = fixture_owner(client)
        assert not client.get("/api/relationship/privacy").json()["accepted"]
        for path, payload in [
            ("/runs", {"request_id": str(uuid4()), "content": "synthetic"}),
            ("/transcribe", {"images": ["not-an-image"]}),
        ]:
            response = client.post("/api/relationship" + path, json=payload)
            assert (
                response.status_code == 403
                and response.json()["error"]["code"] == "MODEL_CONSENT_REQUIRED"
            )
        assert client.get("/api/relationship/export").status_code == 200
        with Session(get_engine()) as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RelationshipJob)
                    .where(RelationshipJob.owner == value)
                )
                == 0
            )


def test_consent_is_scoped_versioned_and_revocation_cancels_queued_job(backend):
    with TestClient(app) as client, TestClient(app) as other:
        value = fixture_owner(client)
        assert choices(client).json()["accepted"]
        assert not other.get("/api/relationship/privacy").json()["accepted"]
        queued = client.post(
            "/api/relationship/runs",
            json={"request_id": str(uuid4()), "content": "synthetic"},
        )
        assert queued.status_code == 202
        assert not choices(client, False, 30).json()["accepted"]
        status = client.get("/api/relationship/runs/" + queued.json()["id"]).json()
        assert status["status"] == "cancelled" and status["cancel_requested"]
        with pytest.raises(AppError), Session(get_engine()) as db:
            require_processing(value, db)
        assert choices(client).json()["accepted"]
        previous = client.get("/api/relationship/privacy").json()
        backend.model_name = "changed-provider-model"
        assert not client.get("/api/relationship/privacy").json()["accepted"]
        previous["allow_model_processing"] = True
        assert client.put("/api/relationship/privacy", json=previous).status_code == 409


def test_provider_guard_blocks_calls_after_revoke_without_charging(
    backend, monkeypatch
):
    value = owner()
    with Session(get_engine()) as db:
        db.get(RelationshipPrivacy, value).revoked_at = datetime.now(UTC)
        db.commit()
    sent = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: sent.append(request))
    ) as client:
        monkeypatch.setattr(model_provider, "model_http_client", lambda: client)
        with (
            operation_scope(value, "synthetic", "ocr"),
            pytest.raises(AppError) as failure,
        ):
            model_provider.complete(
                [{"role": "user", "content": "synthetic"}],
                [],
                999999999,
                0,
                lambda _: None,
            )
        assert failure.value.code == "MODEL_CONSENT_REQUIRED" and not sent


def test_revocation_while_model_inflight_never_persists_result(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    install_blocking_agent(monkeypatch, entered, release)
    with TestClient(app) as client:
        value = fixture_owner(client)
        assert choices(client).status_code == 200
        _, job_id, _ = create_job(value)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(process_one)
            try:
                assert entered.wait(5)
                assert choices(client, False).status_code == 200
            finally:
                release.set()
            assert future.result(timeout=10)
        with Session(get_engine()) as db:
            assert db.get(RelationshipJob, job_id).status == "cancelled"
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RelationshipTurn)
                    .where(RelationshipTurn.owner == value)
                )
                == 0
            )
        with checkpoint_store() as saver:
            assert not list(
                saver.list({"configurable": {"thread_id": f"xinyu:{value}:{job_id}"}})
            )


def seed_retention(value, age, stamp):
    turn_id, memory_id, import_id = (str(uuid4()) for _ in range(3))
    with Session(get_engine()) as db:
        db.add(
            RelationshipTurn(
                id=turn_id,
                owner=value,
                request_id=str(uuid4()),
                content="synthetic old chat",
                result={},
                created_at=stamp - timedelta(days=age),
            )
        )
        db.add(
            RelationshipFeedback(
                id=str(uuid4()),
                owner=value,
                turn_id=turn_id,
                rating="helpful",
                category="general",
                comment="synthetic",
                created_at=stamp - timedelta(days=age),
                updated_at=stamp,
            )
        )
        for item_id, kind in [(memory_id, "memory"), (import_id, "chat_log")]:
            db.add(
                RelationshipItem(
                    id=item_id,
                    owner=value,
                    kind=kind,
                    title="synthetic",
                    quote="confirmed synthetic quote",
                    source_turn_id=turn_id,
                    status="active",
                    created_at=stamp - timedelta(days=age),
                    revision=1,
                    details={"text": "synthetic"} if kind == "chat_log" else {},
                )
            )
        db.commit()
    return turn_id, memory_id, import_id


def test_retention_respects_each_owner_and_keeps_confirmed_memory(backend):
    stamp = datetime.now(UTC)
    short, long, default = owner(), owner(), owner()
    with Session(get_engine()) as db:
        db.get(RelationshipPrivacy, short).history_days = 30
        db.get(RelationshipPrivacy, long).history_days = 180
        db.delete(db.get(RelationshipPrivacy, default))
        db.add(
            RelationshipAuthEvent(
                id=str(uuid4()),
                owner=short,
                event="synthetic",
                device_label="fixture",
                ip_tag="",
                created_at=stamp - timedelta(days=31),
            )
        )
        db.commit()
    expired = seed_retention(short, 60, stamp)
    preserved = seed_retention(long, 100, stamp)
    legacy = seed_retention(default, 100, stamp)
    result = prune_online_data(stamp)
    assert result["turns"] == 2 and result["imports"] == 2
    with Session(get_engine()) as db:
        for value, ids in [(short, expired), (default, legacy)]:
            assert db.get(RelationshipTurn, ids[0]) is None
            assert db.get(RelationshipItem, ids[2]) is None
            memory = db.get(RelationshipItem, ids[1])
            assert (
                memory.quote == "confirmed synthetic quote"
                and memory.source_turn_id is None
            )
            assert db.get(RelationshipRagIndex, ids[2]).status == "deleting"
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RelationshipFeedback)
                    .where(RelationshipFeedback.owner == value)
                )
                == 0
            )
        assert db.get(RelationshipTurn, preserved[0]) and db.get(
            RelationshipItem, preserved[2]
        )
        assert db.scalar(select(func.count()).select_from(RelationshipAuthEvent)) == 0


def test_graph_retention_does_not_remove_active_or_recent_failed_checkpoints(
    backend, monkeypatch
):
    from app.services import relationship_agent as agent
    from tests.test_agent_output_repair import no_retrieval, response

    monkeypatch.setattr(agent, "complete", lambda *_, **__: response(1))
    monkeypatch.setattr(agent, "retrieve", no_retrieval)
    stamp = datetime.now(UTC)
    threads = {}
    for status, age in [("succeeded", 2), ("failed", 2), ("running", 8), ("orphan", 8)]:
        value, job_id, _ = create_job()
        with Session(get_engine()) as db:
            agent.run_agent(db, value, "synthetic", request_id=job_id)
            job = db.get(RelationshipJob, job_id)
            if status == "orphan":
                db.delete(job)
            else:
                job.status = status
            db.commit()
        thread = f"xinyu:{value}:{job_id}"
        threads[status] = thread
        with checkpoint_store() as saver:
            saver.conn.execute(
                "UPDATE checkpoints SET checkpoint=jsonb_set(checkpoint,'{ts}',to_jsonb(%s::text)) WHERE thread_id=%s",
                ((stamp - timedelta(days=age)).isoformat(), thread),
            )
    assert prune_graphs(stamp) == 2
    with checkpoint_store() as saver:
        for status, thread in threads.items():
            assert bool(list(saver.list({"configurable": {"thread_id": thread}}))) == (
                status in {"failed", "running"}
            )
