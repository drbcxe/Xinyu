"""Exercise real file locks and writes inside an isolated D-drive runtime tree."""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import PROJECT_ROOT, get_settings
from app.services import physical_storage as files
from app.services.observability import SafeJsonFormatter
from scripts import backup, supervise
from scripts.backup_crypto import decrypt_to_file, encrypt_chunks


@pytest.fixture
def runtime_files(monkeypatch):
    parent = PROJECT_ROOT / ".runtime" / "test-physical"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="test-", dir=parent) as name:
        root = Path(name).resolve()
        assert root.is_relative_to(parent.resolve())
        settings = get_settings().model_copy(update={"runtime_free_bytes": 1})
        monkeypatch.setattr(files, "PROJECT_ROOT", root)
        monkeypatch.setattr(backup, "PROJECT_ROOT", root)
        monkeypatch.setattr(files, "get_settings", lambda: settings)
        monkeypatch.setattr(backup, "get_settings", lambda: settings)
        dropped = files.BudgetedJsonHandler.dropped
        try:
            yield root, settings
        finally:
            files.BudgetedJsonHandler.dropped = dropped


def log_record():
    return logging.LogRecord(
        "scripts.fixture", logging.INFO, "", 0, "synthetic-private-secret", (), None
    )


def test_process_probe_preserves_a_real_running_child_and_detects_exit():
    # On Windows os.kill(pid, 0) can terminate the target. Exercise the actual OS probe.
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as child:
        assert files.process_alive(child.pid) and child.poll() is None
        child.communicate(timeout=10)
        assert child.returncode == 0 and not files.process_alive(child.pid)
    assert files.process_alive(0) and files.process_alive(2**32)


def test_log_rotation_and_concurrent_writers_obey_the_shared_budget(runtime_files):
    _, settings = runtime_files
    settings.log_file_bytes = 300
    settings.log_total_bytes = 1400
    folder = files.runtime_folder("logs")
    handlers = [
        files.BudgetedJsonHandler(folder / f"app-fixture{i}-{os.getpid()}.jsonl")
        for i in range(6)
    ]
    for handler in handlers:
        handler.setFormatter(SafeJsonFormatter())
    gate = threading.Barrier(len(handlers))
    dropped_before = files.BudgetedJsonHandler.dropped

    def write(handler):
        gate.wait(timeout=10)
        for _ in range(60):
            handler.handle(log_record())
            with files.storage_lock(folder, ".log-budget.lock"):
                assert sum(row[1] for row in files.log_files(folder)) <= 1400

    try:
        with ThreadPoolExecutor(max_workers=len(handlers)) as pool:
            list(pool.map(write, handlers))
    finally:
        for handler in handlers:
            handler.close()
    rows = files.log_files(folder)
    assert rows and all(size <= 300 for _, size, _, _ in rows)
    assert files.BudgetedJsonHandler.dropped > dropped_before
    for path, *_ in rows:
        assert "synthetic-private-secret" not in path.read_text(encoding="utf8")
        assert all(
            json.loads(line)["event"] == "application_log"
            for line in path.read_text(encoding="utf8").splitlines()
        )


def test_log_cleanup_preserves_active_uncertain_and_unowned_files(
    runtime_files, monkeypatch
):
    _, settings = runtime_files
    settings.log_total_bytes = 10
    folder = files.runtime_folder("logs")
    active = folder / f"app-fixture-{os.getpid()}.jsonl"
    dead = folder / "app-fixture-123.jsonl.1"
    uncertain = folder / "app-fixture-0.jsonl"
    unrelated = folder / "notes.txt"
    for path in (active, dead, uncertain, unrelated):
        path.write_bytes(b"private" * 10)
        old = time.time() - 8 * 86400
        os.utime(path, (old, old))
    monkeypatch.setattr(files, "process_alive", lambda pid: pid != 123)
    result = files.maintain_logs(folder)
    assert result["deleted_files"] == 1 and not dead.exists()
    assert active.exists() and uncertain.exists() and unrelated.exists()


def test_low_free_space_refuses_writes_without_dropping_existing_files(
    runtime_files, monkeypatch
):
    folder = files.runtime_folder("logs")
    target = folder / f"app-fixture-{os.getpid()}.jsonl"
    target.write_bytes(b"existing\n")
    handler = files.BudgetedJsonHandler(target)
    handler.setFormatter(SafeJsonFormatter())
    monkeypatch.setattr(files.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    try:
        handler.handle(log_record())
    finally:
        handler.close()
    assert target.read_bytes() == b"existing\n"
    with pytest.raises(files.FileBudgetError, match="free space"):
        backup.create_backup()
    assert not list((runtime_files[0] / ".runtime" / "backups").glob("xinyu_backup_*"))


def complete_copy(root, age=0):
    folder = root / (
        "xinyu_backup_"
        + (datetime.now(UTC) - timedelta(days=age)).strftime("%Y%m%dT%H%M%SZ_")
        + uuid4().hex[:16]
    )
    folder.mkdir(parents=True)
    (folder / "COMPLETE").write_text(folder.name, encoding="ascii")
    return folder


def test_failed_backup_cleans_only_its_new_partial_copy(runtime_files, monkeypatch):
    _, settings = runtime_files
    root = files.runtime_folder("backups")
    protected = [complete_copy(root, age=i + 9) for i in range(3)]
    original = {p: (p / "COMPLETE").read_bytes() for p in protected}
    settings.backup_total_bytes = files.directory_bytes(root) + 100

    def overflow(folder, collections, budget):
        encrypt_chunks(
            [b"x" * 64, b"x" * 64],
            folder / "database.enc",
            b"k" * 32,
            b"test",
            before_write=budget.reserve,
        )

    monkeypatch.setattr(backup, "_create_backup", overflow)
    with pytest.raises(files.FileBudgetError, match="budget exhausted"):
        backup.create_backup(root)
    assert set(root.glob("xinyu_backup_*")) == set(protected)
    assert all((p / "COMPLETE").read_bytes() == value for p, value in original.items())


def test_backup_count_limit_preserves_the_last_copies(runtime_files, monkeypatch):
    _, settings = runtime_files
    settings.backup_max_count = 3
    root = files.runtime_folder("backups")
    protected = [complete_copy(root, age=i + 20) for i in range(3)]
    monkeypatch.setattr(
        backup, "_create_backup", lambda *_: pytest.fail("must reject before dumping")
    )
    with pytest.raises(files.FileBudgetError, match="count limit"):
        backup.create_backup(root)
    assert all(path.exists() for path in protected)


def test_concurrent_backups_cannot_both_spend_the_same_remaining_bytes(
    runtime_files, monkeypatch
):
    _, settings = runtime_files
    settings.backup_total_bytes = 350
    root = files.runtime_folder("backups")

    def write_copy(folder, collections, budget):
        budget.reserve(200)
        (folder / "database.enc").write_bytes(b"x" * 200)
        return folder

    monkeypatch.setattr(backup, "_create_backup", write_copy)

    def attempt(_):
        try:
            return backup.create_backup(root)
        except files.FileBudgetError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(value is not None for value in results) == 1
    assert files.directory_bytes(root) == 200
    assert len(list(root.glob("xinyu_backup_*"))) == 1


def test_distinct_processes_share_log_admission_and_lock_failure_is_bounded(
    runtime_files,
):
    folder = files.runtime_folder("logs")
    program = """
import logging, os, sys
from pathlib import Path
from app.services import physical_storage as files
from app.services.observability import SafeJsonFormatter
root = Path(sys.argv[1]).resolve()
handler = files.BudgetedJsonHandler(root / f'app-child-{os.getpid()}.jsonl')
handler.setFormatter(SafeJsonFormatter())
record = logging.LogRecord('fixture', logging.INFO, '', 0, 'private-fixture', (), None)
try:
    for _ in range(100):
        handler.handle(record)
        with files.storage_lock(root, '.log-budget.lock'):
            assert sum(row[1] for row in files.log_files(root)) <= 4000
finally:
    handler.close()
"""
    environment = {
        **os.environ,
        "PYTHONUTF8": "1",
        "LOG_FILE_BYTES": "600",
        "LOG_TOTAL_BYTES": "4000",
        "RUNTIME_FREE_BYTES": "1",
    }
    children = []
    try:
        for _ in range(2):
            children.append(
                subprocess.Popen(
                    [sys.executable, "-c", program, str(folder)],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
        for child in children:
            output, error = child.communicate(timeout=25)
            assert child.returncode == 0, error.decode("utf8")
            assert not output and not error
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
    assert files.directory_bytes(folder) <= 4000

    lock_program = """
import sys
from pathlib import Path
from app.services.physical_storage import storage_lock
with storage_lock(Path(sys.argv[1]), '.log-budget.lock'):
    print('locked', flush=True)
    sys.stdin.buffer.read()
"""
    with subprocess.Popen(
        [sys.executable, "-c", lock_program, str(folder)],
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as locker:
        try:
            assert locker.stdout.readline().strip() == b"locked"
            before = files.directory_bytes(folder)
            handler = files.BudgetedJsonHandler(
                folder / f"app-locked-{os.getpid()}.jsonl"
            )
            handler.setFormatter(SafeJsonFormatter())
            try:
                started = time.monotonic()
                dropped = files.BudgetedJsonHandler.dropped
                handler.handle(log_record())
                assert time.monotonic() - started < 2
                assert files.BudgetedJsonHandler.dropped == dropped + 1
                assert files.directory_bytes(folder) == before
            finally:
                handler.close()
        finally:
            _, errors = locker.communicate(timeout=10)
        assert locker.returncode == 0 and not errors


def test_restore_budget_counts_all_decrypted_components_and_cleans_failure(
    runtime_files,
):
    root, settings = runtime_files
    settings.restore_temp_bytes = 1000
    key = os.urandom(32)
    first, second = root / "first.enc", root / "second.enc"
    encrypt_chunks([b"a" * 400], first, key, b"first")
    encrypt_chunks([b"b" * 700], second, key, b"second")
    with (
        pytest.raises(files.FileBudgetError, match="budget exhausted"),
        backup.private_temp() as temporary,
    ):
        decrypt_to_file(
            first,
            temporary / "first",
            key,
            b"first",
            before_write=files.FileBudget(temporary.parent, 1000).reserve,
        )
        assert (temporary / "first").stat().st_size == 400
        decrypt_to_file(
            second,
            temporary / "second",
            key,
            b"second",
            before_write=files.FileBudget(temporary.parent, 1000).reserve,
        )
    remaining = list((root / ".runtime" / "restore-tmp").iterdir())
    assert [path.name for path in remaining] == [".restore.lock"]
    assert files.directory_bytes(remaining[0].parent) == 0


def test_supervisor_drains_large_unterminated_private_output_as_metadata(
    runtime_files, monkeypatch
):
    folder = files.runtime_folder("logs")
    handler = files.BudgetedJsonHandler(
        folder / f"app-supervisor-fixture-{os.getpid()}.jsonl"
    )
    handler.setFormatter(SafeJsonFormatter())
    logger = logging.getLogger("scripts.supervise")
    previous = logger.handlers[:], logger.propagate, logger.level
    monkeypatch.setattr(supervise, "configure_logging", lambda _: None)
    logger.handlers, logger.propagate = [handler], False
    logger.setLevel(logging.INFO)
    try:
        code = supervise.supervise(
            "fixture",
            [
                sys.executable,
                "-c",
                "import sys; payload=b'synthetic-private-secret'*50000; sys.stdout.buffer.write(payload); sys.stderr.buffer.write(payload); sys.exit(7)",
            ],
            runtime_files[0],
        )
    finally:
        logger.handlers, logger.propagate, logger.level = previous
        handler.close()
    records = [
        json.loads(line)
        for path, *_ in files.log_files(folder)
        for line in path.read_text(encoding="utf8").splitlines()
    ]
    assert code == 7 and records[-1]["exit_code"] == 7
    assert {
        row.get("stream") for row in records if row["event"] == "process_output"
    } == {"stdout", "stderr"}
    assert (
        sum(row.get("bytes", 0) for row in records)
        == len(b"synthetic-private-secret") * 100000
    )
    assert "synthetic-private-secret" not in json.dumps(records)
    assert files.directory_bytes(folder) < 4096


def test_private_status_reports_incomplete_backups_and_file_usage(runtime_files):
    root, settings = runtime_files
    copies = files.runtime_folder("backups")
    complete_copy(copies)
    incomplete = complete_copy(copies)
    (incomplete / "COMPLETE").unlink()
    (incomplete / "database.enc").write_bytes(b"x" * 30)
    status = files.runtime_file_status()
    assert status["backups"]["count"] == 2
    assert status["backups"]["incomplete"] == 1
    assert status["backups"]["file_bytes"] == files.directory_bytes(copies)
    assert status["minimum_free_bytes"] == settings.runtime_free_bytes
    assert str(root) not in json.dumps(status)
