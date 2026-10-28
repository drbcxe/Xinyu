import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from queue import Queue
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.relationship import (
    RelationshipJob,
    RelationshipProfile,
    RelationshipStorageTotal,
    RelationshipTurn,
)
from app.repositories.database import get_engine
from app.services import relationship_agent as agent
from app.services.account_closure import purge_content
from app.services.graph_checkpoints import clear_checkpoints
from app.services.relationship_jobs import process_one
from app.services.storage_budget import (
    configure_storage,
    lock_storage,
    pressure_cleanup,
    storage_summary,
)
from tests.test_agent_output_repair import no_retrieval, response
from tests.test_job_reliability import create_job, job_state
from tests.test_resource_control import owner


def snapshot(value):
    with Session(get_engine()) as db:
        return storage_summary(db, value)


def assert_counters_match_rows():
    expected = defaultdict(int)
    with get_engine().connect() as conn:
        tables = conn.execute(
            text(
                "SELECT schemaname,tablename FROM pg_tables WHERE (schemaname='public' AND tablename LIKE 'relationship_%' AND tablename NOT LIKE 'relationship_storage_%') OR (schemaname='xinyu_graph' AND tablename IN ('checkpoints','checkpoint_blobs','checkpoint_writes'))"
            )
        ).all()
        for schema, table in tables:
            assert re.fullmatch("[a-z_]+", schema) and re.fullmatch("[a-z_]+", table)
            category = (
                "graph"
                if schema == "xinyu_graph"
                else "items"
                if table == "relationship_items"
                else "conversations"
                if table in {"relationship_turns", "relationship_feedback"}
                else "jobs"
                if table == "relationship_jobs"
                else "metadata"
            )
            for (value,) in conn.exec_driver_sql(
                f"SELECT to_jsonb(t)::text FROM {schema}.{table} t"
            ):
                row = json.loads(value)
                size = len(value.encode())
                expected["global", category] += size
                owner_key = row.get("owner") or row.get("scope") or ""
                if not re.fullmatch("[a-f0-9]{64}", owner_key):
                    match = re.match(r"xinyu:([a-f0-9]{64}):", row.get("thread_id", ""))
                    owner_key = match.group(1) if match else None
                if owner_key:
                    expected[owner_key, category] += size
        for value, amount in conn.exec_driver_sql(
            "SELECT owner,remaining FROM relationship_storage_reservations"
        ):
            expected["global", "reserved"] += amount
            expected[value, "reserved"] += amount
        actual = {
            (scope, category): amount
            for scope, category, amount in conn.exec_driver_sql(
                "SELECT scope,category,bytes FROM relationship_storage_totals"
            )
        }
    assert actual == {key: value for key, value in expected.items() if value}


def add_turn(value, content="synthetic", result=None):
    return RelationshipTurn(
        id=str(uuid4()),
        owner=value,
        request_id=str(uuid4()),
        content=content,
        result=result or {"analysis": "synthetic"},
        created_at=datetime.now(UTC),
    )


def install_agent(monkeypatch):
    monkeypatch.setattr(agent, "complete", lambda *_, **__: response(1))
    monkeypatch.setattr(agent, "retrieve", no_retrieval)


def test_graph_payload_history_and_metadata_are_counted_and_rebuild_is_exact(
    backend, monkeypatch
):
    value = owner()
    install_agent(monkeypatch)
    with Session(get_engine()) as db:
        result = agent.run_agent(db, value, "synthetic", request_id=str(uuid4()))
        db.add(add_turn(value, result=result))
        db.commit()
    before = snapshot(value)
    assert (
        before["by_category"]["graph"] > 0
        and before["by_category"]["conversations"] > 0
        and before["by_category"]["metadata"] > 0
    )
    assert_counters_match_rows()
    configure_storage(force_rebuild=True)
    assert snapshot(value) == before
    assert_counters_match_rows()
    with Session(get_engine()) as db:
        purge_content(db, value)
        db.commit()
    assert snapshot(value)["used_bytes"] < before["used_bytes"] and not snapshot(value)[
        "by_category"
    ].get("graph", 0)
    assert_counters_match_rows()


def test_concurrent_raw_writes_cannot_overshoot_owner_budget(backend):
    value = owner()
    base = snapshot(value)["used_bytes"]
    backend.owner_storage_bytes = base + 7100
    backend.storage_emergency_bytes = 1
    configure_storage()

    def write(_):
        try:
            with Session(get_engine()) as db:
                db.add(add_turn(value, "x" * 2048))
                db.commit()
            return True
        except AppError as exc:
            assert exc.code == "SPACE_QUOTA_EXCEEDED"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        success = list(pool.map(write, range(8)))
    assert sum(success) == 3
    assert snapshot(value)["used_bytes"] <= backend.owner_storage_bytes
    assert_counters_match_rows()


def test_global_budget_blocks_growth_across_owners_and_rolls_back(backend):
    first, second = owner(), owner()
    with Session(get_engine()) as db:
        used = db.scalar(
            select(text("COALESCE(sum(bytes),0)"))
            .select_from(RelationshipStorageTotal)
            .where(RelationshipStorageTotal.scope == "global")
        )
    backend.global_storage_bytes = used + 3000
    backend.storage_emergency_bytes = 1
    configure_storage()
    with Session(get_engine()) as db:
        db.add(add_turn(first, "x" * 1500))
        db.commit()
    before = snapshot(second)
    with Session(get_engine()) as db, pytest.raises(AppError) as failed:
        db.add(add_turn(second, "x" * 1500))
        db.commit()
    assert (
        failed.value.code == "GLOBAL_STORAGE_QUOTA_EXCEEDED"
        and snapshot(second) == before
    )
    assert_counters_match_rows()


def test_reservations_cover_graph_writes_and_release_on_completion_and_cancel(
    backend, monkeypatch
):
    install_agent(monkeypatch)
    value, job_id, _ = create_job()
    before = snapshot(value)
    assert 0 < before["reserved_bytes"] <= backend.job_storage_reserve_bytes
    configure_storage(force_rebuild=True)
    assert snapshot(value) == before
    assert process_one() and job_state(job_id)["status"] == "succeeded"
    finished = snapshot(value)
    assert (
        finished["reserved_bytes"] == 0
        and finished["by_category"]["graph"] > 0
        and finished["by_category"]["conversations"] > 0
    )
    _, queued_id, _ = create_job(value)
    with Session(get_engine()) as db:
        job = db.get(RelationshipJob, queued_id)
        job.status = "cancelled"
        job.cancel_requested = True
        db.commit()
    assert snapshot(value)["reserved_bytes"] == 0
    assert_counters_match_rows()


def test_completed_notice_batches_do_not_starve_later_reminders(backend):
    from app.models.relationship import RelationshipItem
    from app.services.relationship_jobs import dispatch_reminders

    value = owner()
    with Session(get_engine()) as db:
        for index in range(103):
            db.add(
                RelationshipItem(
                    id=str(uuid4()),
                    owner=value,
                    kind="reminder",
                    title=f"synthetic {index}",
                    due_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                )
            )
        db.commit()
    assert dispatch_reminders() == 100
    assert dispatch_reminders() == 3
    assert dispatch_reminders() == 0
    assert_counters_match_rows()


def test_full_owner_does_not_roll_back_other_owners_reminder(backend):
    from app.models.relationship import RelationshipItem
    from app.services.relationship_jobs import dispatch_reminders

    first, second = owner(), owner()
    with Session(get_engine()) as db:
        for value in (first, second):
            db.add(
                RelationshipItem(
                    id=str(uuid4()),
                    owner=value,
                    kind="reminder",
                    title="synthetic",
                    due_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                )
            )
        db.add(add_turn(first, "x" * 4000))
        db.commit()
    backend.owner_storage_bytes = snapshot(first)["used_bytes"] + 10
    backend.storage_emergency_bytes = 1
    configure_storage()
    assert dispatch_reminders() == 1
    assert dispatch_reminders() == 0
    with Session(get_engine()) as db:
        purge_content(db, first)
        db.commit()
    assert_counters_match_rows()


def test_upgrade_backfills_existing_oversized_history_without_deleting_it(backend):
    from alembic import command
    from alembic.config import Config

    # Only the disposable fixture DB may be downgraded. The business DB is never touched.
    assert get_engine().url.database.startswith("xinyu_test_")
    config = Config("alembic.ini")
    command.downgrade(config, "ab3e94c520f1")
    try:
        value = owner()
        with Session(get_engine()) as db:
            row = add_turn(value, "synthetic-" * 3000)
            row_id = row.id
            db.add(row)
            db.commit()
    finally:
        command.upgrade(config, "head")
    backend.owner_storage_bytes = 2000
    configure_storage(force_rebuild=True)
    with Session(get_engine()) as db:
        assert db.get(RelationshipTurn, row_id).content == "synthetic-" * 3000
    assert snapshot(value)["used_bytes"] > backend.owner_storage_bytes
    with Session(get_engine()) as db:
        purge_content(db, value)
        db.commit()
    assert_counters_match_rows()


def test_admission_without_storage_headroom_never_queues_or_charges(backend):
    value = owner()
    backend.owner_storage_bytes = (
        snapshot(value)["used_bytes"] + backend.job_storage_reserve_bytes - 1000
    )
    configure_storage()
    with pytest.raises(AppError) as failed:
        create_job(value)
    assert failed.value.code == "SPACE_QUOTA_EXCEEDED"
    with Session(get_engine()) as db:
        assert not db.scalars(
            select(RelationshipJob).where(RelationshipJob.owner == value)
        ).all()
    assert snapshot(value)["reserved_bytes"] == 0
    assert_counters_match_rows()


def test_graph_limit_fails_once_and_cleans_partial_state(backend, monkeypatch):
    backend.graph_thread_storage_bytes = 100000
    configure_storage()
    calls = []

    def large(*_, **__):
        calls.append(1)
        message, usage, retries = response(1)
        message["content"] = json.dumps(
            {"analysis": "x" * 70000, "replies": [], "source_ids": []}
        )
        return message, usage, retries

    monkeypatch.setattr(agent, "complete", large)
    monkeypatch.setattr(agent, "retrieve", no_retrieval)
    value, job_id, _ = create_job()
    assert process_one()
    state = job_state(job_id)
    assert state["status"] == "failed" and state["attempts"] == 1 and len(calls) == 1, (
        state,
        snapshot(value),
    )
    assert (
        snapshot(value)["reserved_bytes"] == 0
        and snapshot(value)["by_category"].get("graph", 0) == 0
    )
    assert_counters_match_rows()


def test_active_graph_reservation_is_not_recreated_after_restart(backend, monkeypatch):
    install_agent(monkeypatch)
    value, job_id, _ = create_job()
    queued = snapshot(value)["reserved_bytes"]
    with Session(get_engine()) as db:
        agent.run_agent(db, value, "synthetic", request_id=job_id)
    before = snapshot(value)
    assert 0 < before["reserved_bytes"] < queued
    configure_storage(force_rebuild=True)
    assert snapshot(value) == before
    assert_counters_match_rows()


def test_raw_heartbeat_waits_before_row_lock_so_cancel_cannot_deadlock(backend):
    value, job_id, _ = create_job()
    with Session(get_engine()) as db:
        db.get(RelationshipJob, job_id).status = "running"
        db.commit()
    ready = Queue()

    def heartbeat():
        with Session(get_engine()) as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            ready.put(db.scalar(text("SELECT pg_backend_pid()")))
            changed = db.execute(
                text("""UPDATE relationship_jobs SET updated_at=now()
                WHERE id=:id AND status='running' AND NOT cancel_requested RETURNING id"""),
                {"id": job_id},
            ).scalar_one_or_none()
            db.commit()
            return changed

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(get_engine()) as db:
            lock_storage(db)
            db.execute(text("SET LOCAL lock_timeout='500ms'"))
            future = pool.submit(heartbeat)
            pid = ready.get(timeout=2)
            deadline = time.monotonic() + 2
            while not db.scalar(
                text(
                    "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"
                ),
                {"pid": pid},
            ):
                assert time.monotonic() < deadline
                time.sleep(0.02)
                db.execute(text("SELECT pg_stat_clear_snapshot()"))
            job = db.scalar(
                select(RelationshipJob)
                .where(RelationshipJob.id == job_id)
                .with_for_update()
            )
            job.status, job.cancel_requested = "cancelled", True
            db.commit()
        assert future.result(timeout=3) is None
    assert snapshot(value)["reserved_bytes"] == 0
    assert_counters_match_rows()


def test_oversized_result_is_terminal_and_releases_execution_budget(
    backend, monkeypatch
):
    install_agent(monkeypatch)
    backend.agent_result_bytes = 100
    value, job_id, _ = create_job()
    assert process_one()
    state = job_state(job_id)
    assert state["status"] == "failed" and state["attempts"] == 1
    assert snapshot(value)["reserved_bytes"] == 0
    assert snapshot(value)["by_category"].get("graph", 0) == 0
    assert_counters_match_rows()


def test_lowered_limit_keeps_existing_content_but_allows_clear(backend):
    value = owner()
    with Session(get_engine()) as db:
        db.add(add_turn(value, "x" * 10000))
        db.commit()
    backend.owner_storage_bytes = 1000
    configure_storage(force_rebuild=True)
    assert snapshot(value)["used_bytes"] > 1000
    with Session(get_engine()) as db, pytest.raises(AppError):
        db.add(add_turn(value))
        db.commit()
    with Session(get_engine()) as db:
        purge_content(db, value)
        db.commit()
    assert snapshot(value)["used_bytes"] < 1000
    assert_counters_match_rows()


def test_profile_limit_is_enforced_for_raw_insert_and_existing_spaces_remain(backend):
    first = owner()
    backend.global_profile_limit = 1
    configure_storage()
    with pytest.raises(AppError) as failed:
        owner()
    assert failed.value.code == "SPACE_COUNT_LIMIT"
    with Session(get_engine()) as db:
        assert db.get(RelationshipProfile, first)
    assert_counters_match_rows()


def test_pressure_cleanup_drops_only_completed_buffers_not_user_history(
    backend, monkeypatch
):
    install_agent(monkeypatch)
    value, job_id, _ = create_job()
    assert process_one()
    with Session(get_engine()) as db:
        turn_id = db.get(RelationshipJob, job_id).turn_id
        pressure_cleanup(db, value, needed=backend.owner_storage_bytes)
        db.commit()
        assert (
            db.get(RelationshipTurn, turn_id)
            and db.get(RelationshipJob, job_id).status == "succeeded"
        )
    assert snapshot(value)["by_category"].get("graph", 0) == 0
    clear_checkpoints(value)
    assert_counters_match_rows()


def test_capacity_wait_migration_preserves_jobs_and_atomic_accounting(
    backend, infrastructure
):
    from alembic import command
    from alembic.config import Config

    value, job_id, _ = create_job()
    before = snapshot(value)
    assert get_engine().url.database == infrastructure.database_name
    assert infrastructure.database_name.startswith("xinyu_test_")
    config = Config("alembic.ini")
    command.downgrade(config, "0c58ad62f901")
    try:
        with get_engine().connect() as conn:
            assert (
                conn.scalar(
                    text("SELECT status FROM relationship_jobs WHERE id=:id"),
                    {"id": job_id},
                )
                == "queued"
            )
    finally:
        command.upgrade(config, "head")
    with Session(get_engine()) as db:
        row = db.get(RelationshipJob, job_id)
        assert row.status == "queued" and row.capacity_deferrals == 0
        assert row.capacity_wait_started_at is None
    assert snapshot(value) == before
    assert_counters_match_rows()
