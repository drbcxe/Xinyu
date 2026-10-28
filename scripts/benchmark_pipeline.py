"""Owned local HTTP/queue/graph/RAG workload against a synthetic model protocol peer."""

import argparse
import hashlib
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import psutil
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, get_settings
from app.models.relationship import (
    RelationshipItem,
    RelationshipJob,
    RelationshipModelCall,
    RelationshipProfile,
    RelationshipTurn,
)
from app.repositories.database import get_engine
from app.services.auth_cache import get_redis
from app.services.relationship_auth import COOKIE, token_hash
from app.services.relationship_rag import (
    model_descriptor,
    process_index,
    schedule_index,
)
from app.services.resource_control import (
    SLOT_KEY,
    provider_capacity,
    reservation_grace_seconds,
)
from app.services.storage_budget import global_storage_summary
from app.services.vector_capacity import reconcile_vectors
from quality.environment import isolated_space
from quality.local_provider import local_provider
from quality.run import code_fingerprint
from scripts.benchmark_retrieval import stats


@contextmanager
def configuration(provider_url, recovery=False):
    changes = {
        "MODEL_BASE_URL": provider_url,
        "MODEL_NAME": "local-synthetic-probe",
        "MODEL_API_KEY": "synthetic-local-only",
        "MODEL_TIMEOUT_SECONDS": "10",
        "AGENT_TIMEOUT_SECONDS": "60",
        "WORKER_LEASE_SECONDS": "15",
        "WORKER_RETRY_BASE_SECONDS": "1",
        "GLOBAL_QUEUE_LIMIT": "8",
        "WORKER_ADVICE_CONCURRENCY": "2",
        "GLOBAL_MODEL_CONCURRENCY": "4",
        "RAG_RERANK_ENABLED": "true",
        "RAG_DENSE_ENABLED": "true",
        "RELATIONSHIP_VISION_MODEL": "",
        "RELATIONSHIP_VISION_API_KEY": "",
        "SMTP_HOST": "",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "PYTHONUTF8": "1",
    }
    if recovery:
        changes.update(
            {
                "GLOBAL_MODEL_CONCURRENCY": "1",
                "MODEL_TIMEOUT_SECONDS": "5",
                "AGENT_TIMEOUT_SECONDS": "15",
            }
        )
    previous = {key: os.environ.get(key) for key in changes}
    os.environ.update(changes)
    get_settings.cache_clear()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def stop_owned(child):
    if child.poll() is not None:
        return
    try:
        root = psutil.Process(child.pid)
        processes = [root, *root.children(recursive=True)]
        identities = [(p, p.create_time()) for p in processes]
        for process, created in reversed(identities):
            try:
                if process.create_time() == created:
                    process.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(processes, timeout=5)
        if any(process.status() != psutil.STATUS_ZOMBIE for process in alive):
            raise RuntimeError("Owned benchmark process did not stop")
    except psutil.NoSuchProcess:
        pass
    child.wait(timeout=5)


def launch(command, cleanup, processes):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    child = subprocess.Popen(
        [sys.executable, "-u", "-m", *command],
        cwd=PROJECT_ROOT,
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **options,
    )
    processes.append(child)
    cleanup.callback(stop_owned, child)
    return child


def launch_role(role, cleanup, processes, runtime_root, port=None):
    args = [
        "scripts.benchmark_pipeline",
        "--child-role",
        role,
        "--runtime-root",
        str(runtime_root),
    ]
    if port is not None:
        args.extend(["--port", str(port)])
    return launch(args, cleanup, processes)


def child_runtime(role, runtime_root, port):
    from app.services import observability, physical_storage, recovery_journal

    root = Path(runtime_root).resolve()
    if (
        not root.is_relative_to((PROJECT_ROOT / ".runtime" / "benchmarks").resolve())
        or not root.name.startswith("probe-")
        or not root.is_dir()
    ):
        raise RuntimeError("Probe child requires an owned private runtime directory")
    if (
        get_settings().app_env != "test"
        or not get_settings().rag_collection_override.startswith("xinyu_eval_")
        or make_url(get_settings().database_url).database
        != get_settings().rag_collection_override
        or get_settings().model_name != "local-synthetic-probe"
        or urlsplit(get_settings().model_base_url).hostname != "127.0.0.1"
    ):
        raise RuntimeError("Probe child requires isolated test configuration")
    os.environ["BACKUP_KEY_FILE"] = str(root / "master.key")
    get_settings.cache_clear()
    recovery_journal.PROJECT_ROOT = root
    physical_storage.PROJECT_ROOT = root
    observability.PROJECT_ROOT = root
    if role == "api":
        import uvicorn

        from app import main as application

        application.PROJECT_ROOT = root
        uvicorn.run(application.app, host="127.0.0.1", port=port)
    else:
        from scripts.worker import main as worker_main

        worker_main()


class Sampler:
    def __init__(self, processes):
        self.processes = processes
        self.stop = threading.Event()
        self.peak = 0
        self.queued = 0
        self.running = 0
        self.slots = 0
        self.samples = 0
        self.error = None

    def __enter__(self):
        def sample():
            while not self.stop.is_set():
                try:
                    rss = 0
                    for child in self.processes[:]:
                        if child.poll() is None:
                            try:
                                root = psutil.Process(child.pid)
                                for process in [root, *root.children(recursive=True)]:
                                    try:
                                        rss += process.memory_info().rss
                                    except psutil.NoSuchProcess:
                                        pass
                            except psutil.NoSuchProcess:
                                pass
                    self.peak = max(self.peak, rss)
                    with Session(get_engine()) as db:
                        counts = dict(
                            db.execute(
                                select(RelationshipJob.status, func.count()).group_by(
                                    RelationshipJob.status
                                )
                            ).all()
                        )
                    self.queued = max(self.queued, counts.get("queued", 0))
                    self.running = max(self.running, counts.get("running", 0))
                    self.slots = max(self.slots, get_redis().zcard(SLOT_KEY))
                    self.samples += 1
                except Exception as exc:  # noqa: BLE001 - record sampling failure, never claim missing evidence
                    self.error = type(exc).__name__
                    break
                self.stop.wait(0.1)

        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive() or self.error:
            raise RuntimeError("Pipeline sampler failed: " + str(self.error))

    def result(self):
        return {
            "samples": self.samples,
            "sampled_api_worker_peak_rss_mib": round(self.peak / 1024**2, 2),
            "max_queued": self.queued,
            "max_running": self.running,
            "max_provider_slots": self.slots,
            "sampling_interval_ms": 100,
        }


def wait_until(probe, seconds=120, interval=0.2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(interval)
    raise RuntimeError("Pipeline probe exceeded its bounded wait")


def wait_api(base, process):
    def ready():
        if process.poll() is not None:
            raise RuntimeError("Owned HTTP process exited")
        try:
            return (
                httpx.get(base + "/api/health", timeout=1, trust_env=False).status_code
                == 200
            )
        except httpx.TransportError:
            return False

    wait_until(ready, 45)


def submit(client, mode="normal", request_id=None):
    request_id = request_id or uuid4().hex
    content = (
        f"[probe:{request_id}:{mode}] 请参考我导入的聊天，蓝色杯子和散步是怎样的偏好？"
    )
    started = time.perf_counter()
    response = client.post(
        "/api/relationship/runs", json={"request_id": request_id, "content": content}
    )
    return {
        "request_key": request_id,
        "status": response.status_code,
        "body": response.json(),
        "retry_after_seconds": int(response.headers.get("Retry-After", "0")),
        "admission_ms": (time.perf_counter() - started) * 1000,
        "started": started,
    }


def finish(client, row):
    if row["status"] != 202:
        return row
    polls = []

    def terminal():
        started = time.perf_counter()
        response = client.get("/api/relationship/runs/" + row["body"]["id"])
        polls.append((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        state = response.json()
        return (
            state if state["status"] in {"succeeded", "failed", "cancelled"} else None
        )

    result = wait_until(terminal)
    row.update(
        terminal=result,
        elapsed_ms=(time.perf_counter() - row["started"]) * 1000,
        poll_ms=polls,
    )
    return row


def summarize(rows):
    completed = [
        row for row in rows if row.get("terminal", {}).get("status") == "succeeded"
    ]
    return {
        "requests": len(rows),
        "admission": stats([row["admission_ms"] for row in rows]),
        "polling": stats(
            [latency for row in rows for latency in row.get("poll_ms", [])]
        )
        if any(row.get("poll_ms") for row in rows)
        else None,
        "http_status": dict(Counter(str(row["status"]) for row in rows)),
        "rejections": dict(
            Counter(
                row["body"].get("error", {}).get("code", "unknown")
                for row in rows
                if row["status"] != 202
            )
        ),
        "terminal": dict(
            Counter(
                row.get("terminal", {}).get("status", "not_admitted") for row in rows
            )
        ),
        "successful_end_to_end": stats([row["elapsed_ms"] for row in completed])
        if completed
        else None,
    }


def verify_success(row):
    if row["status"] != 202:
        raise RuntimeError(
            "Expected admission; got "
            + str(row["status"])
            + " "
            + row["body"].get("error", {}).get("code", "unknown")
        )
    result = row["terminal"]
    if result["status"] != "succeeded":
        raise RuntimeError(
            "Expected successful synthetic task: " + result.get("error", "")
        )
    advice = result["turn"]["result"]
    if advice.get("execution", {}).get("engine") != "LangGraph" or not advice[
        "execution"
    ].get("checkpointed"):
        raise RuntimeError("Expected persistent LangGraph execution")
    if not advice.get("sources") or not any(
        trace.get("tool") == "search_chat_history"
        and trace.get("status") == "succeeded"
        for trace in advice["tool_trace"]
    ):
        raise RuntimeError("Synthetic success skipped a required tool or source")
    if len(advice["retrieval_trace"]) != 2:
        raise RuntimeError("Expected initial and tool retrieval phases")
    with Session(get_engine()) as db:
        job = db.get(RelationshipJob, row["body"]["id"])
        expected = {source["source_id"] for source in advice["sources"]}
        found = set(
            db.scalars(
                select(RelationshipItem.id).where(
                    RelationshipItem.owner == job.owner,
                    RelationshipItem.id.in_(expected),
                )
            )
        )
        if expected != found:
            raise RuntimeError("A returned citation escaped its owner")
    if not all(
        trace["mode"] == "hybrid" and trace["reranked"]
        for trace in advice["retrieval_trace"]
    ):
        raise RuntimeError(
            "Dense/hybrid/rerank path was degraded during the measurement"
        )


def seed(tokens):
    if not reconcile_vectors():
        raise RuntimeError("Isolated vector audit could not complete")
    topics = [
        "她喜欢蓝色杯子，先问她想用哪一个。",
        "她周末喜欢散步，希望提前商量时间。",
        "今天聊了咖啡和天气。",
        "我们在图书馆找了一本电影史。",
        "今天工作开会，明天早点休息。",
        "买零食前要留意花生成分。",
        "周五是家人的生日。",
        "音乐会结束以后坐公交回家。",
    ]
    with Session(get_engine()) as db:
        for token in tokens:
            owner = token_hash(token)
            db.add(
                RelationshipProfile(
                    owner=owner, partner_name="合成伴侣", style="自然简短"
                )
            )
        db.commit()
        for token in tokens:
            owner = token_hash(token)
            for i in range(8):
                item = RelationshipItem(
                    id=str(uuid4()),
                    owner=owner,
                    kind="chat_log",
                    title=f"合成聊天{i}",
                    quote="",
                    status="active",
                    created_at=datetime.now(UTC),
                    revision=1,
                    details={"text": f"第{i}份合成聊天：{topics[i]}" * 8},
                )
                db.add(item)
                db.flush()
                schedule_index(db, item)
        db.commit()
    for _ in range(len(tokens) * 8 + 1):
        if not process_index():
            break
    with Session(get_engine()) as db:
        count = db.scalar(
            text(
                "SELECT count(*) FROM relationship_rag_indexes WHERE status <> 'ready'"
            )
        )
        if count:
            raise RuntimeError("Synthetic source indexing did not finish")
    return {
        "owners": len(tokens),
        "documents": len(tokens) * 8,
        "embedding_dimensions": model_descriptor()["dim"],
        "embedding_model": get_settings().rag_embedding_model,
    }


def workload(base, cleanup, processes, state, steady, runtime_root):
    tokens = ["g_" + secrets.token_hex(32) for _ in range(12)]
    seeded = seed(tokens)
    clients = []
    for token in tokens:
        client = httpx.Client(
            base_url=base,
            trust_env=False,
            timeout=15,
            headers={"Origin": "http://localhost:5173"},
        )
        client.cookies.set(COOKIE, token, path="/api/relationship")
        clients.append(cleanup.enter_context(client))
    denied = submit(clients[0])
    assert (
        denied["status"] == 403
        and denied["body"].get("error", {}).get("code") == "MODEL_CONSENT_REQUIRED"
    )
    assert not state.snapshot()["calls"]
    for client in clients:
        choice = client.get("/api/relationship/privacy").json()
        response = client.put(
            "/api/relationship/privacy",
            json={
                "version": choice["version"],
                "provider_fingerprint": choice["provider_fingerprint"],
                "allow_model_processing": True,
                "history_days": 90,
            },
        )
        response.raise_for_status()
    report = {
        "synthetic_corpus": seeded,
        "consent_gate": {"status": 403, "model_calls": 0},
    }
    report["rate_window_waits"] = []

    def fault_submit(client, mode):
        row = submit(client, mode)
        if (
            row["status"] == 429
            and row["body"].get("error", {}).get("code") == "AUTH_RATE_LIMIT"
        ):
            seconds = row["retry_after_seconds"]
            if not 0 < seconds <= 60:
                raise RuntimeError("Invalid rate-limit retry window")
            report["rate_window_waits"].append(
                {"mode": mode, "code": "AUTH_RATE_LIMIT", "wait_seconds": seconds}
            )
            print(
                f"observed IP rate limit; waiting {seconds}s for its real window",
                flush=True,
            )
            time.sleep(seconds + 0.1)
            row = submit(client, mode)
        if row["status"] != 202:
            verify_success(row)
        return row

    with Sampler(processes) as memory, ThreadPoolExecutor(max_workers=8) as pool:
        burst = list(pool.map(lambda i: submit(clients[i % len(clients)]), range(16)))
        assert sum(row["status"] == 202 for row in burst) == 8
        assert all(row["status"] in {202, 429} for row in burst)
        worker = launch_role("worker", cleanup, processes, runtime_root)
        burst = list(
            pool.map(
                lambda pair: finish(clients[pair[0] % len(clients)], pair[1]),
                enumerate(burst),
            )
        )
        for row in burst:
            if row["status"] == 202:
                verify_success(row)
        report["burst_worker_initially_stopped"] = summarize(burst)
        print("burst and queue-cap checks complete", flush=True)

        def normal(i):
            client = clients[i % len(clients)]
            row = finish(client, submit(client))
            if row["status"] == 202:
                verify_success(row)
            elif (
                row["status"] != 429
                or row["body"].get("error", {}).get("code") != "AUTH_RATE_LIMIT"
            ):
                raise RuntimeError("Unexpected steady admission failure")
            return row

        with ThreadPoolExecutor(max_workers=4) as steady_pool:
            normal_rows = list(steady_pool.map(normal, range(steady)))
        report["steady_concurrent_4"] = summarize(normal_rows)
        first_owner, first = next(
            (i, row) for i, row in enumerate(burst) if row["status"] == 202
        )
        other = clients[(first_owner + 1) % len(clients)]
        denied_job = other.get("/api/relationship/runs/" + first["body"]["id"])
        source_id = first["terminal"]["turn"]["result"]["sources"][0]["source_id"]
        denied_source = other.get("/api/relationship/items/" + source_id + "/content")
        assert denied_job.status_code == 404 and denied_source.status_code == 404
        report["cross_owner_access"] = {"job_http": 404, "source_http": 404}
        before = state.snapshot()
        replay = submit(clients[0], request_id=normal_rows[0]["request_key"])
        assert (
            replay["status"] == 202
            and replay["body"]["id"] == normal_rows[0]["body"]["id"]
        )
        assert state.snapshot() == before
        report["idempotent_replay"] = {"same_job": True, "extra_model_calls": 0}

        faults = []
        for i, mode in enumerate(["retry503", "deny401", "malformed"]):
            row = finish(clients[8 + i], fault_submit(clients[8 + i], mode))
            if mode == "retry503":
                verify_success(row)
            else:
                assert (
                    row["terminal"]["status"] == "failed"
                    and row["terminal"]["attempts"] == 1
                )
            faults.append(
                {
                    "mode": mode,
                    "summary": summarize([row]),
                    "worker_attempts": row["terminal"]["attempts"],
                }
            )
        report["protocol_faults"] = faults
        print("steady, replay and protocol-fault checks complete", flush=True)

        cancelled = fault_submit(clients[5], "slow")
        wait_until(lambda: state.has_entered(cancelled["request_key"]))
        response = clients[5].post(
            "/api/relationship/runs/" + cancelled["body"]["id"] + "/cancel"
        )
        response.raise_for_status()
        finish(clients[5], cancelled)
        assert cancelled["terminal"]["status"] == "cancelled" and not cancelled[
            "terminal"
        ].get("turn")
        time.sleep(
            1.5
        )  # Let the known bounded in-flight peer finish before checking late writes.
        with Session(get_engine()) as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RelationshipTurn)
                    .where(
                        RelationshipTurn.request_id
                        == str(UUID(cancelled["request_key"]))
                    )
                )
                == 0
            )
        report["cancel_in_flight"] = {"cancelled": True, "late_turns": 0}

        interrupted = fault_submit(clients[6], "slow")
        wait_until(lambda: state.has_entered(interrupted["request_key"]))
        stop_owned(worker)
        launch_role("worker", cleanup, processes, runtime_root)
        interrupted = finish(clients[6], interrupted)
        verify_success(interrupted)
        assert interrupted["terminal"]["attempts"] >= 2
        report["worker_interrupted"] = {
            "status": "succeeded",
            "attempts": interrupted["terminal"]["attempts"],
            "elapsed_ms": round(interrupted["elapsed_ms"], 3),
        }
        with Session(get_engine()) as db:
            report["model_ledger"] = dict(
                db.execute(
                    select(RelationshipModelCall.status, func.count()).group_by(
                        RelationshipModelCall.status
                    )
                ).all()
            )
            report["turns"] = db.scalar(
                select(func.count()).select_from(RelationshipTurn)
            )
            report["postgres_version"] = db.scalar(text("SHOW server_version"))
        report["remaining_provider_slots"] = get_redis().zcard(SLOT_KEY)
        report["storage"] = global_storage_summary()
    report["resource_observations"] = memory.result()
    report["synthetic_model"] = state.snapshot()
    return report


def recovery_workload(base, cleanup, processes, state, runtime_root):
    token = "g_" + secrets.token_hex(32)
    corpus = seed([token])
    client = cleanup.enter_context(
        httpx.Client(
            base_url=base,
            trust_env=False,
            timeout=15,
            headers={"Origin": "http://localhost:5173"},
        )
    )
    client.cookies.set(COOKIE, token, path="/api/relationship")
    choice = client.get("/api/relationship/privacy").json()
    client.put(
        "/api/relationship/privacy",
        json={
            "version": choice["version"],
            "provider_fingerprint": choice["provider_fingerprint"],
            "allow_model_processing": True,
            "history_days": 90,
        },
    ).raise_for_status()
    with Sampler(processes) as memory:
        row = submit(client, "slow")
        assert row["status"] == 202
        worker = launch_role("worker", cleanup, processes, runtime_root)
        wait_until(lambda: state.has_entered(row["request_key"]))
        with Session(get_engine()) as db:
            call = db.scalar(
                select(RelationshipModelCall).where(
                    RelationshipModelCall.operation_id == str(UUID(row["request_key"])),
                    RelationshipModelCall.status == "reserved",
                )
            )
            assert call
            unknown_id, reserved, created_at = (
                call.id,
                call.reserved_tokens,
                call.created_at,
            )
        stop_owned(worker)
        stranded = provider_capacity()
        assert stranded["active"] == 1
        launch_role("worker", cleanup, processes, runtime_root)
        row = finish(client, row)
        verify_success(row)
        result = row["terminal"]
        assert (
            result["attempts"] == 2
            and result["capacity_deferrals"] > get_settings().worker_max_attempts
        )
        assert result["turn"]["result"]["execution"]["resumed"]
        assert provider_capacity()["active"] == 0
        assert global_storage_summary()["reserved_bytes"] == 0
        print(
            "single-slot task recovered without exhausting retries; waiting for natural accounting reconciliation",
            flush=True,
        )

        def reconciled():
            with Session(get_engine()) as db:
                entry = db.get(RelationshipModelCall, unknown_id)
                if entry.status == "reserved":
                    return None
                assert entry.status == "uncertain" and entry.charged_tokens == reserved
                assert entry.completed_at >= created_at
                return {
                    "status": entry.status,
                    "charged_tokens": entry.charged_tokens,
                    "reserved_tokens": reserved,
                    "seconds_from_reservation": round(
                        (entry.completed_at - created_at).total_seconds(), 3
                    ),
                }

        accounting = wait_until(
            reconciled, seconds=reservation_grace_seconds() + 120, interval=1
        )
        with Session(get_engine()) as db:
            assert db.scalar(select(func.count()).select_from(RelationshipTurn)) == 1
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RelationshipModelCall)
                    .where(RelationshipModelCall.status == "reserved")
                )
                == 0
            )
            ledger = dict(
                db.execute(
                    select(RelationshipModelCall.status, func.count()).group_by(
                        RelationshipModelCall.status
                    )
                ).all()
            )
        report = {
            "scenario": "single-slot Worker crash, bounded capacity wait, natural lease expiry and accounting",
            "synthetic_corpus": corpus,
            "stranded_slot_after_crash": stranded,
            "task": {
                "status": result["status"],
                "attempts": result["attempts"],
                "capacity_deferrals": result["capacity_deferrals"],
                "resumed_from_checkpoint": True,
                "elapsed_ms": round(row["elapsed_ms"], 3),
            },
            "unknown_call_reconciliation": accounting,
            "model_ledger": ledger,
            "final_provider_capacity": provider_capacity(),
            "logical_execution_reserved_bytes": global_storage_summary()[
                "reserved_bytes"
            ],
            "turns": 1,
            "synthetic_model": state.snapshot(),
        }
    report["resource_observations"] = memory.result()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steady", type=int, default=24, choices=range(12, 37, 12))
    parser.add_argument(
        "--recovery-only",
        action="store_true",
        help="Observe natural single-slot crash recovery and unknown-call reconciliation",
    )
    parser.add_argument(
        "--child-role", choices=["api", "worker"], help=argparse.SUPPRESS
    )
    parser.add_argument("--runtime-root", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8000, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not __debug__:
        parser.error("Verification requires Python assertions enabled")
    if args.child_role:
        child_runtime(args.child_role, args.runtime_root, args.port)
        return
    settings = get_settings()
    for hostname in (
        make_url(settings.database_url).host,
        urlsplit(settings.redis_url).hostname,
        urlsplit(settings.qdrant_url).hostname,
    ):
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise SystemExit("This probe only supports local infrastructure")
    with (
        local_provider() as (provider_url, state),
        configuration(provider_url, recovery=args.recovery_only),
        isolated_space() as space,
    ):
        # Exercise explicit consent rather than the evaluation-only bypass.
        os.environ["APP_ENV"] = "test"
        get_settings.cache_clear()
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        processes = []
        with ExitStack() as cleanup:
            temporary_parent = PROJECT_ROOT / ".runtime" / "benchmarks"
            temporary_parent.mkdir(parents=True, exist_ok=True)
            runtime_root = Path(
                cleanup.enter_context(
                    tempfile.TemporaryDirectory(prefix="probe-", dir=temporary_parent)
                )
            ).resolve()
            assert runtime_root.is_relative_to(temporary_parent.resolve())
            api = launch_role("api", cleanup, processes, runtime_root, port)
            base = f"http://127.0.0.1:{port}"
            wait_api(base, api)
            report = (
                recovery_workload(base, cleanup, processes, state, runtime_root)
                if args.recovery_only
                else workload(
                    base, cleanup, processes, state, args.steady, runtime_root
                )
            )
        report.update(
            {
                "data_origin": "synthetic",
                "model_protocol": "local deterministic OpenAI-compatible peer; no paid model calls",
                "python": platform.python_version(),
                "os": platform.system() + " " + platform.release(),
                "logical_cpus": psutil.cpu_count(),
                "memory_gib": round(psutil.virtual_memory().total / 1024**3, 2),
                "source_code_sha256": code_fingerprint(),
                "git_revision": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT
                )
                .decode()
                .strip(),
                "git_worktree_dirty": subprocess.run(
                    ["git", "diff", "--quiet"], cwd=PROJECT_ROOT, check=False
                ).returncode
                != 0
                or bool(
                    subprocess.check_output(
                        ["git", "ls-files", "--others", "--exclude-standard"],
                        cwd=PROJECT_ROOT,
                    )
                ),
                "recorded_at": datetime.now(UTC).isoformat(),
                "benchmark_sha256": hashlib.sha256(
                    Path(__file__).read_bytes().replace(b"\r\n", b"\n")
                ).hexdigest(),
                "note": "Small local workload, not a production SLA. API/Worker RSS excludes DB/Qdrant, preparation process and OS cache; 100 ms sampling may miss peaks. Worker interruption uses a 15-second lease rather than production default 90 seconds; timeout settings are listed explicitly in limits.",
                "limits": {
                    key: getattr(get_settings(), key)
                    for key in [
                        "global_queue_limit",
                        "worker_advice_concurrency",
                        "global_model_concurrency",
                        "worker_lease_seconds",
                        "agent_timeout_seconds",
                        "model_timeout_seconds",
                        "worker_capacity_wait_seconds",
                    ]
                },
            }
        )
    report["resources_cleaned"] = True
    report["private_runtime_cleaned"] = not runtime_root.exists()
    folder = PROJECT_ROOT / ".runtime" / "benchmarks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (
        ("pipeline-recovery-" if args.recovery_only else "pipeline-")
        + space.run_id
        + ".json"
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print("REPORT " + str(path), flush=True)


if __name__ == "__main__":
    main()
