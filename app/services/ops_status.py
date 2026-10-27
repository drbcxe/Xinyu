"""Health evidence, aggregate queue state and local alert transitions."""

import json
import logging
import threading
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models.relationship import (
    RelationshipEmailOutbox,
    RelationshipJob,
    RelationshipModelCall,
    RelationshipRagIndex,
)
from app.repositories.database import get_engine
from app.services.auth_cache import auth_cache
from app.services.observability import http_metrics
from app.services.runtime_state import worker_count

_probe_lock = threading.Lock()
_probe_cache = {}


def probe_readiness(cached=True):
    settings = get_settings()
    cache_key = (
        settings.database_url,
        settings.redis_url,
        settings.qdrant_url,
        settings.model_name,
        bool(settings.model_api_key.get_secret_value()),
    )
    with _probe_lock:
        if (
            cached
            and _probe_cache.get("key") == cache_key
            and time.monotonic() - _probe_cache.get("at", 0) < 3
        ):
            return _probe_cache["value"]
        checks = {}
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["postgres"] = True
        except Exception:  # noqa: BLE001 - dependency errors can contain credentials
            checks["postgres"] = False
        try:
            with auth_cache() as cache:
                checks["redis"] = bool(cache.ping())
            checks["worker"] = worker_count() > 0
        except AppError:
            checks["redis"] = checks["worker"] = False
        try:
            with httpx.Client(trust_env=False, timeout=2) as client:
                checks["qdrant"] = client.get(
                    settings.qdrant_url.rstrip("/") + "/readyz"
                ).is_success
        except (httpx.HTTPError, ValueError):
            checks["qdrant"] = False
        from app.providers.model_provider import require_model_config

        try:
            require_model_config()
            checks["model_config"] = True
        except AppError:
            checks["model_config"] = False
        result = {
            "ready": all(checks.values()),
            "checks": checks,
            "checked_at": datetime.now(UTC).isoformat(),
            "model_note": "仅检查本地配置，不代表模型平台实时可用或有额度",
        }
        _probe_cache.update(key=cache_key, at=time.monotonic(), value=result)
        return result


def queue_metrics():
    with Session(get_engine()) as db:
        result = {
            name: dict(
                db.execute(
                    select(model.status, func.count()).group_by(model.status)
                ).all()
            )
            for name, model in (
                ("jobs", RelationshipJob),
                ("indexes", RelationshipRagIndex),
                ("mail", RelationshipEmailOutbox),
            )
        }
        oldest = db.scalar(
            select(func.min(RelationshipJob.created_at)).where(
                RelationshipJob.status == "queued"
            )
        )
        result["oldest_queue_seconds"] = (
            max(0, round((datetime.now(UTC) - oldest).total_seconds())) if oldest else 0
        )
        result["model_calls_today"] = dict(
            db.execute(
                select(RelationshipModelCall.status, func.count())
                .where(
                    RelationshipModelCall.day == datetime.now(UTC).date().isoformat()
                )
                .group_by(RelationshipModelCall.status)
            ).all()
        )
        return result


def alert_findings(readiness, queues, http, storage=None):
    findings = []
    if storage:
        if storage.get("unavailable"):
            findings.append("STORAGE_METRICS_UNAVAILABLE")
        elif (
            storage["used_bytes"] + storage["reserved_bytes"]
            >= storage["limit_bytes"] * 0.9
        ):
            findings.append("LOGICAL_STORAGE_HIGH")
        disk = storage.get("workspace_disk", {})
        if disk and disk["free_bytes"] < max(2 * 1024**3, disk["total_bytes"] // 20):
            findings.append("WORKSPACE_DISK_LOW")
    if not readiness["ready"]:
        findings.append("DEPENDENCY_NOT_READY")
    if not readiness["checks"].get("worker"):
        findings.append("WORKER_OFFLINE")
    if queues.get("oldest_queue_seconds", 0) > 60:
        findings.append("QUEUE_STALLED")
    if queues.get("indexes", {}).get("delete_failed", 0):
        findings.append("INDEX_DELETE_FAILED")
    if queues.get("indexes", {}).get("quota_blocked", 0):
        findings.append("VECTOR_INDEX_BLOCKED")
    vectors = (storage or {}).get("vectors", {})
    if vectors.get("untracked_points", 0):
        findings.append("UNATTRIBUTED_VECTORS")
    if vectors and not vectors.get("audited_at"):
        findings.append("VECTOR_AUDIT_REQUIRED")
    if (vectors.get("audit_age_seconds") or 0) > 900:
        findings.append("VECTOR_AUDIT_STALE")
    if queues.get("mail", {}).get("failed", 0):
        findings.append("MAIL_DELIVERY_FAILED")
    if queues.get("jobs", {}).get("failed", 0):
        findings.append("FAILED_JOBS")
    if (
        http.get("requests", 0) >= 20
        and http.get("errors_5xx", 0) / http["requests"] >= 0.1
    ):
        findings.append("DAILY_HTTP_ERRORS_HIGH")
    return sorted(findings)


def update_alerts(findings):
    with auth_cache() as cache:
        # Atomic exchange makes concurrent API/worker observations idempotent.
        previous = cache.getset("xinyu:ops:alerts", json.dumps(findings))
    old = set(json.loads(previous or "[]"))
    new = set(findings)
    for code in sorted(old ^ new):
        logging.getLogger(__name__).warning(
            "alert_changed", extra={"alert_code": code, "active": code in new}
        )


def operational_status(update=False):
    from app.services.retrieval_cache import corpus_cache
    from app.services.storage_budget import capacity_status

    readiness = probe_readiness()
    try:
        queues = queue_metrics()
    except (SQLAlchemyError, RuntimeError):
        queues = {"unavailable": True}
    try:
        http = http_metrics()
    except AppError:
        http = {"unavailable": True}
    try:
        storage = capacity_status()
    except (SQLAlchemyError, OSError, RuntimeError):
        storage = {"unavailable": True}
    alerts = alert_findings(readiness, queues, http, storage)
    if update:
        update_alerts(alerts)
    return {
        "readiness": readiness,
        "queues": queues,
        "http": http,
        "alerts": alerts,
        "storage": storage,
        "retrieval_cache": {"scope": "calling_process_only", **corpus_cache.stats()},
    }
