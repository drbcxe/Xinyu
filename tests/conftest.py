"""Integration tests create their own PostgreSQL database and claim an empty Redis DB."""

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy.engine import make_url

from app.config import PROJECT_ROOT, get_settings


@dataclass
class Infrastructure:
    database_url: str
    redis_url: str
    database_name: str


@pytest.fixture(scope="session")
def infrastructure():
    original = make_url(
        os.environ.get("XINYU_TEST_ADMIN_URL", get_settings().database_url)
    )
    name = "xinyu_test_" + uuid4().hex[:16]
    admin_dsn = original.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    redis_url = os.environ.get("XINYU_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
    cache = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry=Retry(NoBackoff(), 0),
    )
    if cache.dbsize() != 0:
        pytest.fail(
            "Test Redis DB must be empty; refusing to touch an existing database. Set XINYU_TEST_REDIS_URL to a dedicated empty DB."
        )
    lock_token = uuid4().hex
    assert cache.set("xinyu:test-lock", lock_token, nx=True, ex=7200)
    created = False
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = True
        url = original.set(database=name).render_as_string(hide_password=False)
        environment = {
            **os.environ,
            "DATABASE_URL": url,
            "APP_ENV": "test",
            "PYTHONUTF8": "1",
        }
        for command in [
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            [
                sys.executable,
                "-c",
                "from app.services.graph_checkpoints import prepare_checkpoints; prepare_checkpoints()",
            ],
        ]:
            result = subprocess.run(
                command,
                check=False,
                env=environment,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
            )
            if result.returncode:
                pytest.fail(
                    "Test database preparation failed: " + result.stderr[-3000:]
                )
        yield Infrastructure(url, redis_url, name)
    finally:
        if created:
            assert name.startswith("xinyu_test_") and len(name) == 27
            with psycopg.connect(admin_dsn, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )
        if cache.get("xinyu:test-lock") == lock_token:
            keys = list(cache.scan_iter("xinyu:*"))
            if keys:
                cache.delete(*keys)
        cache.close()


@pytest.fixture
def backend(infrastructure, monkeypatch):
    from app.models import Base
    from app.repositories.database import get_engine
    from app.services import recovery_journal
    from app.services.auth_cache import get_redis
    from app.services.retrieval_cache import corpus_cache
    from app.services.storage_budget import configure_storage

    get_engine.cache_clear()
    corpus_cache.clear(reset=True)
    get_redis.cache_clear()
    monkeypatch.setenv("DATABASE_URL", infrastructure.database_url)
    monkeypatch.setenv("REDIS_URL", infrastructure.redis_url)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MODEL_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("MODEL_NAME", "fixture-model")
    monkeypatch.setenv("MODEL_API_KEY", "test-only-never-real")
    recovery_root = PROJECT_ROOT / ".runtime" / "test-recovery"
    recovery_root.mkdir(parents=True, exist_ok=True)
    recovery_temp = tempfile.TemporaryDirectory(prefix="test-", dir=recovery_root)
    recovery_path = Path(recovery_temp.name).resolve()
    assert recovery_path.is_relative_to(recovery_root.resolve())
    monkeypatch.setenv("BACKUP_KEY_FILE", str(recovery_path / "master.key"))
    monkeypatch.setattr(
        recovery_journal, "journal_path", lambda: recovery_path / "deletions.log"
    )
    get_settings.cache_clear()
    engine = get_engine()
    assert engine.url.database == infrastructure.database_name
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            conn.exec_driver_sql(f"DELETE FROM xinyu_graph.{table}")
    configure_storage(force_rebuild=True)
    cache = get_redis()
    keys = [key for key in cache.scan_iter("xinyu:*") if key != "xinyu:test-lock"]
    if keys:
        cache.delete(*keys)
    try:
        yield get_settings()
    finally:
        engine.dispose()
        get_engine.cache_clear()
        cache.close()
        get_redis.cache_clear()
        get_settings.cache_clear()
        recovery_temp.cleanup()
