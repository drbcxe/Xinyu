import json
import logging
import re
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.errors import AppError
from app.main import app
from app.services import ops_status
from app.services.auth_cache import get_redis
from app.services.observability import RequestTelemetry, SafeJsonFormatter, http_metrics


def test_log_formatter_drops_raw_content_headers_and_exception_text():
    secret = "synthetic-private-content-and-api-key"
    record = logging.LogRecord(
        "app.fixture", logging.ERROR, "file", 1, secret, (), None
    )
    record.authorization = secret
    record.body = {"password": secret}
    record.request_id = "a" * 32
    record.exc_info = (RuntimeError, RuntimeError(secret), None)
    encoded = SafeJsonFormatter().format(record)
    assert (
        secret not in encoded
        and "authorization" not in encoded
        and "password" not in encoded
    )
    assert json.loads(encoded)["request_id"] == "a" * 32


def test_request_headers_correlate_errors_and_never_cache_private_responses(backend):
    with TestClient(app) as client:
        response = client.post(
            "/api/relationship/runs",
            json={"content": "fixture"},
            headers={"X-Request-ID": "untrusted-header-value"},
        )
        assert response.status_code == 422
        request_id = response.headers["X-Request-ID"]
        assert re.fullmatch(r"[a-f0-9]{32}", request_id)
        assert response.json()["request_id"] == request_id
        assert response.headers["Cache-Control"] == "no-store"


def test_metrics_have_route_templates_not_ids_or_queries(backend):
    with TestClient(app) as client:
        client.get("/api/health?secret=synthetic-private-query")
        client.get("/api/relationship/runs/11111111-1111-1111-1111-111111111111")
        client.get("/unknown/synthetic-private-path")
    metrics = http_metrics()
    encoded = json.dumps(metrics)
    assert metrics["requests"] == 3
    assert "synthetic-private" not in encoded and "11111111" not in encoded
    assert "GET /api/relationship/runs/{run_id} 4xx" in metrics["by_route"]
    assert "GET unmatched 4xx" in metrics["by_route"]


def test_unhandled_failure_is_generic_with_request_id(backend):
    isolated = FastAPI()
    isolated.add_middleware(RequestTelemetry)

    @isolated.get("/boom")
    def boom():
        raise RuntimeError("synthetic-private-password")

    with TestClient(isolated) as client:
        response = client.get("/boom")
    assert (
        response.status_code == 500
        and "synthetic-private-password" not in response.text
    )
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_metrics_outage_does_not_change_successful_response(backend, monkeypatch):
    from app.services import observability

    def unavailable(*_):
        raise AppError(503, "fixture", "synthetic")

    # Exercise the real fail-open metrics writer with an unavailable Redis context manager.
    monkeypatch.setattr(observability, "auth_cache", unavailable)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200


def test_readiness_returns_503_without_leaking_connection_details(backend, monkeypatch):
    class RedisFixture:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def ping(self):
            return False

    class HTTPFixture:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, *_):
            return SimpleNamespace(is_success=False)

    def broken_engine():
        raise RuntimeError("postgresql://synthetic:secret@private-host")

    monkeypatch.setattr(ops_status, "get_engine", broken_engine)
    monkeypatch.setattr(ops_status, "auth_cache", RedisFixture)
    monkeypatch.setattr(ops_status, "worker_count", lambda: 0)
    monkeypatch.setattr(ops_status.httpx, "Client", HTTPFixture)
    ops_status._probe_cache.clear()
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 503 and not response.json()["ready"]
    assert "secret" not in response.text and "private-host" not in response.text


def test_ops_endpoint_requires_private_token(backend, monkeypatch):
    backend.ops_token = SecretStr("synthetic-ops-token-for-isolated-tests-12345")
    monkeypatch.setattr(
        ops_status,
        "probe_readiness",
        lambda: {"ready": True, "checks": {"worker": True}},
    )
    with TestClient(app) as client:
        assert client.get("/api/ops/status").status_code == 403
        response = client.get(
            "/api/ops/status",
            headers={"Authorization": "Bearer " + backend.ops_token.get_secret_value()},
        )
        assert response.status_code == 200 and "queues" in response.json()
        assert backend.ops_token.get_secret_value() not in response.text
        exported = client.get(
            "/api/ops/metrics",
            headers={"Authorization": "Bearer " + backend.ops_token.get_secret_value()},
        )
        assert (
            exported.status_code == 200 and "xinyu_http_requests_total" in exported.text
        )
        assert 'le="+Inf"' in exported.text


def test_alert_transitions_persist_without_private_content(backend):
    healthy = {"ready": True, "checks": {"worker": True}}
    http = {"requests": 20, "errors_5xx": 4}
    alerts = ops_status.alert_findings(
        healthy, {"oldest_queue_seconds": 90, "indexes": {"delete_failed": 1}}, http
    )
    assert alerts == ["DAILY_HTTP_ERRORS_HIGH", "INDEX_DELETE_FAILED", "QUEUE_STALLED"]
    ops_status.update_alerts(alerts)
    assert json.loads(get_redis().get("xinyu:ops:alerts")) == alerts
    ops_status.update_alerts([])
    assert get_redis().get("xinyu:ops:alerts") == "[]"
