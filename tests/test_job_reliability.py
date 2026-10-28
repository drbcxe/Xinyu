import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.errors import AppError
from app.main import app
from app.models.relationship import (
    RelationshipJob,
    RelationshipPrivacy,
    RelationshipProfile,
    RelationshipTurn,
)
from app.repositories.database import get_engine
from app.services import relationship_agent, relationship_jobs
from app.services.graph_checkpoints import checkpoint_store
from app.services.privacy_policy import POLICY_VERSION, provider_fingerprint
from app.services.relationship_auth import COOKIE, token_hash
from app.services.relationship_jobs import enqueue, process_one
from app.services.resource_control import provider_slot, usage_summary


def create_job(owner=None, request_id=None, content="synthetic question"):
    owner = owner or secrets.token_hex(32)
    request_id = request_id or str(uuid4())
    with Session(get_engine()) as db:
        if not db.get(RelationshipPrivacy, owner):
            db.add(
                RelationshipPrivacy(
                    owner=owner,
                    version=POLICY_VERSION,
                    provider_fingerprint=provider_fingerprint(),
                    accepted_at=datetime.now(UTC),
                    history_days=90,
                )
            )
            db.commit()
        job = enqueue(db, owner, request_id, content, ip_scope="test-" + owner)
        value = job.id
    return owner, value, request_id


def job_state(job_id):
    with Session(get_engine()) as db:
        row = db.get(RelationshipJob, job_id)
        return (
            {
                "status": row.status,
                "attempts": row.attempts,
                "capacity_deferrals": row.capacity_deferrals,
                "token": row.lease_token,
                "available_at": row.available_at,
                "error": row.error,
            }
            if row
            else None
        )


def install_blocking_agent(monkeypatch, entered, release):
    def model(*args, **kwargs):
        entered.set()
        assert release.wait(8)
        return (
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "analysis": "Synthetic response",
                        "uncertainty": "",
                        "replies": [],
                        "source_ids": [],
                    }
                ),
            },
            {"total_tokens": 10},
            0,
        )

    monkeypatch.setattr(relationship_agent, "complete", model)
    monkeypatch.setattr(
        relationship_agent,
        "retrieve",
        lambda *a, **kw: {
            "sources": [],
            "mode": "empty",
            "warnings": [],
            "milliseconds": 0,
        },
    )


def test_replayed_request_does_not_consume_admission_twice(backend):
    owner, job_id, request_id = create_job()
    assert create_job(owner, request_id)[1] == job_id
    with Session(get_engine()) as db:
        assert usage_summary(db, owner)["requests"] == 1
    with pytest.raises(AppError) as failure:
        create_job(owner, request_id, "different question")
    assert failure.value.code == "REQUEST_ID_REUSED"


def test_global_queue_limit_is_enforced(backend):
    backend.global_queue_limit = 1
    create_job()
    with pytest.raises(AppError) as failure:
        create_job()
    assert failure.value.code == "QUEUE_FULL"


def test_owner_pending_limit_is_enforced(backend):
    backend.owner_pending_limit = 1
    owner, _, _ = create_job()
    with pytest.raises(AppError) as failure:
        create_job(owner)
    assert failure.value.code == "QUEUE_FULL"


def test_worker_releases_profile_lock_during_model_call(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    install_blocking_agent(monkeypatch, entered, release)
    owner, job_id, _ = create_job()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(process_one)
        try:
            assert entered.wait(5)
            with Session(get_engine()) as db:
                db.execute(text("SET LOCAL lock_timeout='300ms'"))
                profile = db.scalar(
                    select(RelationshipProfile)
                    .where(RelationshipProfile.owner == owner)
                    .with_for_update()
                )
                profile.partner_name = "can update while model waits"
                db.commit()
        finally:
            release.set()
        assert future.result(timeout=10)
    assert job_state(job_id)["status"] == "succeeded"


@pytest.mark.parametrize("clear_history", [False, True])
def test_cancellation_and_history_clear_cannot_resurrect_results_or_checkpoints(
    backend, monkeypatch, clear_history
):
    entered, release = threading.Event(), threading.Event()
    install_blocking_agent(monkeypatch, entered, release)
    token = "g_" + secrets.token_hex(32)
    owner = token_hash(token)
    _, job_id, _ = create_job(owner)
    with (
        TestClient(app, base_url="http://localhost") as client,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        client.cookies.set(COOKIE, token, path="/api/relationship")
        future = pool.submit(process_one)
        try:
            assert entered.wait(5)
            response = (
                client.delete("/api/relationship/history")
                if clear_history
                else client.post(f"/api/relationship/runs/{job_id}/cancel")
            )
            assert response.status_code == 200, response.text
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
    with checkpoint_store() as saver:
        assert (
            saver.get_tuple({"configurable": {"thread_id": f"xinyu:{owner}:{job_id}"}})
            is None
        )
    assert (
        job_state(job_id) is None
        if clear_history
        else job_state(job_id)["status"] == "cancelled"
    )


def test_stale_worker_cannot_overwrite_new_lease(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    install_blocking_agent(monkeypatch, entered, release)
    owner, job_id, _ = create_job()
    replacement = str(uuid4())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(process_one)
        try:
            assert entered.wait(5)
            with Session(get_engine()) as db:
                job = db.get(RelationshipJob, job_id)
                job.lease_token = replacement
                job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=2)
                db.commit()
        finally:
            release.set()
        assert future.result(timeout=10)
    assert job_state(job_id)["token"] == replacement
    assert job_state(job_id)["status"] == "running"
    with Session(get_engine()) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RelationshipTurn)
                .where(RelationshipTurn.owner == owner)
            )
            == 0
        )


def test_transient_failure_backs_off_then_recovers_without_duplicate_turn(
    backend, monkeypatch
):
    owner, job_id, _ = create_job()

    def fail(*args, **kwargs):
        raise AppError(502, "SYNTHETIC", "temporary provider failure")

    monkeypatch.setattr(relationship_jobs, "run_agent", fail)
    assert process_one()
    row = job_state(job_id)
    assert row["status"] == "queued" and row["available_at"] > datetime.now(UTC)
    assert not process_one()
    with Session(get_engine()) as db:
        db.get(RelationshipJob, job_id).available_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        db.commit()
    monkeypatch.setattr(
        relationship_jobs, "run_agent", lambda *a, **kw: {"analysis": "recovered"}
    )
    assert process_one()
    assert job_state(job_id)["status"] == "succeeded"
    with Session(get_engine()) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RelationshipTurn)
                .where(RelationshipTurn.owner == owner)
            )
            == 1
        )
        assert usage_summary(db, owner)["requests"] == 1


def test_expired_lease_is_reclaimed(backend, monkeypatch):
    _, job_id, _ = create_job()
    with Session(get_engine()) as db:
        job = db.get(RelationshipJob, job_id)
        job.status = "running"
        job.attempts = 1
        job.lease_token = str(uuid4())
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(
        relationship_jobs, "run_agent", lambda *a, **kw: {"analysis": "recovered"}
    )
    assert process_one()
    assert (
        job_state(job_id)["status"] == "succeeded"
        and job_state(job_id)["attempts"] == 2
    )


def test_capacity_wait_does_not_exhaust_execution_retries(backend, monkeypatch):
    backend.global_model_concurrency = 1
    backend.worker_max_attempts = 2
    _, job_id, _ = create_job()

    def controlled_agent(*_args, **_kwargs):
        with provider_slot(time.monotonic() + 60):
            return {"analysis": "capacity recovered"}

    monkeypatch.setattr(relationship_jobs, "run_agent", controlled_agent)
    with provider_slot(time.monotonic() + 120):
        for _ in range(4):
            assert process_one()
            state = job_state(job_id)
            assert state["status"] == "queued"
            assert state["attempts"] == 0
            assert state["available_at"] > datetime.now(UTC)
            with Session(get_engine()) as db:
                db.get(RelationshipJob, job_id).available_at = datetime.now(
                    UTC
                ) - timedelta(seconds=1)
                db.commit()
    assert process_one()
    assert job_state(job_id)["status"] == "succeeded"
    assert job_state(job_id)["capacity_deferrals"] == 4
    assert job_state(job_id)["attempts"] == 1


def test_capacity_wait_deadline_stops_without_more_agent_calls(backend, monkeypatch):
    from app.models.relationship import RelationshipStorageReservation

    _, job_id, _ = create_job()
    with Session(get_engine()) as db:
        job = db.get(RelationshipJob, job_id)
        job.capacity_wait_started_at = datetime.now(UTC) - timedelta(
            seconds=backend.worker_capacity_wait_seconds + 1
        )
        job.capacity_deferrals = 4
        db.commit()
    monkeypatch.setattr(
        relationship_jobs,
        "run_agent",
        lambda *_a, **_kw: pytest.fail("expired wait must not call agent"),
    )
    assert not process_one()
    assert job_state(job_id)["status"] == "failed"
    with Session(get_engine()) as db:
        assert (
            db.get(RelationshipJob, job_id).error == "模型资源等待超时，请稍后手动重试"
        )
        assert not db.get(RelationshipStorageReservation, job_id)


def test_manual_retry_resets_failure_and_capacity_wait_but_not_usage(backend):
    token = "g_" + secrets.token_hex(32)
    owner, job_id, _ = create_job(token_hash(token))
    with Session(get_engine()) as db:
        job = db.get(RelationshipJob, job_id)
        job.status, job.attempts = "failed", backend.worker_max_attempts
        job.capacity_wait_started_at = datetime.now(UTC) - timedelta(hours=1)
        job.capacity_deferrals = 4
        db.commit()
    with TestClient(app, base_url="http://localhost") as client:
        client.cookies.set(COOKIE, token, path="/api/relationship")
        response = client.post(f"/api/relationship/runs/{job_id}/retry")
    assert response.status_code == 200
    value = response.json()
    assert value["status"] == "queued" and value["attempts"] == 0
    assert (
        value["capacity_deferrals"] == 0 and value["capacity_wait_started_at"] is None
    )
    with Session(get_engine()) as db:
        assert usage_summary(db, owner)["requests"] == 2


def test_waiting_for_capacity_can_be_cancelled_without_a_saved_turn(
    backend, monkeypatch
):
    backend.global_model_concurrency = 1
    token = "g_" + secrets.token_hex(32)
    _, job_id, _ = create_job(token_hash(token))

    def blocked(*_a, **_kw):
        with provider_slot(time.monotonic() + 10):
            pytest.fail("saturated capacity must not reach model execution")

    monkeypatch.setattr(relationship_jobs, "run_agent", blocked)
    with (
        provider_slot(time.monotonic() + 60),
        TestClient(app, base_url="http://localhost") as client,
    ):
        assert process_one()
        assert job_state(job_id)["capacity_deferrals"] == 1
        client.cookies.set(COOKIE, token, path="/api/relationship")
        response = client.post(f"/api/relationship/runs/{job_id}/cancel")
        assert response.status_code == 200
        assert not process_one()
    assert job_state(job_id)["status"] == "cancelled"
    with Session(get_engine()) as db:
        assert not db.get(RelationshipJob, job_id).turn_id


def test_second_worker_does_not_run_same_owner_concurrently(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    install_blocking_agent(monkeypatch, entered, release)
    owner, _, _ = create_job()
    create_job(owner)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(process_one)
        try:
            assert entered.wait(5)
            assert not process_one()
        finally:
            release.set()
        assert future.result(timeout=10)
