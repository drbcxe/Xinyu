"""Own a fresh database, vector collection and empty Redis DB; unwind all on exit."""

import os
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from uuid import uuid4

import psycopg
from psycopg import sql
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy.engine import make_url

from app.config import PROJECT_ROOT, get_settings
from app.providers.model_provider import model_http_client
from app.repositories.database import get_engine
from app.services.auth_cache import get_redis
from app.services.graph_checkpoints import prepare_checkpoints
from app.services.relationship_rag import ensure_collection, vector_client


@dataclass
class EvaluationSpace:
    run_id: str
    database_name: str
    collection: str


def close_connections():
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    if get_redis.cache_info().currsize:
        get_redis().close()
    if model_http_client.cache_info().currsize:
        model_http_client().close()
    if vector_client.cache_info().currsize:
        vector_client().close()
    for factory in (get_engine, get_redis, model_http_client, vector_client):
        factory.cache_clear()


@contextmanager
def isolated_space():
    original = make_url(get_settings().database_url)
    run_id = uuid4().hex
    name = "xinyu_eval_" + run_id[:16]
    dsn = original.set(drivername="postgresql").render_as_string(hide_password=False)
    redis_url = os.environ.get("XINYU_EVAL_REDIS_URL", "redis://127.0.0.1:6379/14")
    changes = {
        "DATABASE_URL": original.set(database=name).render_as_string(
            hide_password=False
        ),
        "REDIS_URL": redis_url,
        "APP_ENV": "evaluation",
        "RAG_COLLECTION_OVERRIDE": name,
    }
    previous = {key: os.environ.get(key) for key in changes}

    def restore_environment():
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

    with ExitStack() as cleanup:
        cache = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry=Retry(NoBackoff(), 0),
        )
        cleanup.callback(cache.close)
        cleanup.callback(restore_environment)
        if cache.dbsize() != 0:
            raise RuntimeError(
                "Evaluation Redis DB must be empty; existing data was not modified"
            )
        if not cache.set("xinyu:eval-lock", run_id, nx=True, ex=14400):
            raise RuntimeError("Evaluation Redis DB is already claimed")

        def clean_cache():
            if cache.get("xinyu:eval-lock") == run_id:
                keys = list(cache.scan_iter("xinyu:*"))
                if keys:
                    cache.delete(*keys)

        cleanup.callback(clean_cache)
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))

        def drop_database():
            assert name == "xinyu_eval_" + run_id[:16]
            with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )

        cleanup.callback(drop_database)
        close_connections()
        cleanup.callback(close_connections)
        os.environ.update(changes)
        get_settings.cache_clear()
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env={**os.environ, "PYTHONUTF8": "1"},
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Evaluation database migration failed")
        prepare_checkpoints()
        if vector_client().collection_exists(name):
            raise RuntimeError(
                "Evaluation vector collection already exists; refusing to reuse it"
            )

        def drop_vectors():
            assert name == "xinyu_eval_" + run_id[:16]
            if vector_client().collection_exists(name):
                vector_client().delete_collection(name)

        cleanup.callback(drop_vectors)
        ensure_collection()
        yield EvaluationSpace(run_id, name, name)
