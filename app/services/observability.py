"""Content-free request logs and shared, bounded-cardinality operational metrics."""

import json
import logging
import os
import re
import secrets
import time
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.config import PROJECT_ROOT, get_settings
from app.errors import AppError
from app.services.auth_cache import auth_cache
from app.services.physical_storage import BudgetedJsonHandler, runtime_folder

EVENTS = {
    "http_request",
    "http_failed",
    "worker_started",
    "worker_iteration_failed",
    "relationship_job_failed",
    "job_started",
    "job_finished",
    "job_capacity_deferred",
    "model_completed",
    "job_heartbeat_failed",
    "job_progress_unavailable",
    "model_slot_release_failed",
    "email_delivery_failed",
    "alert_changed",
    "maintenance_failed",
    "auth_event_omitted_storage_limit",
    "notice_deferred_storage_limit",
    "process_started",
    "process_output",
    "process_exited",
}
request_context: ContextVar[str | None] = ContextVar("request_id", default=None)
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


class SafeJsonFormatter(logging.Formatter):
    def format(self, record):
        event = (
            record.msg
            if isinstance(record.msg, str) and record.msg in EVENTS
            else "application_log"
        )
        result = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "event": event,
        }
        for key in (
            "request_id",
            "job_id",
            "mail_id",
            "call_id",
            "purpose",
            "route",
            "method",
            "status",
            "elapsed_ms",
            "error_type",
            "alert_code",
            "active",
            "role",
            "stream",
            "bytes",
            "exit_code",
        ):
            value = getattr(record, key, None)
            if type(value) in {int, float, bool} or (
                isinstance(value, str)
                and re.fullmatch(r"[A-Za-z0-9_./{}: -]{1,180}", value)
            ):
                result[key] = value
        # No exception text, stack-local values, URLs with queries, headers, bodies or arbitrary extras.
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def configure_logging(role: str):
    root = logging.getLogger("app")
    if getattr(root, "xinyu_role", None) == role:
        return
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", role):
        raise ValueError("Invalid log role")
    folder = runtime_folder("logs")
    handler = BudgetedJsonHandler(folder / f"app-{role}-{os.getpid()}.jsonl")
    handler.setFormatter(SafeJsonFormatter())
    previous = set(root.handlers)
    for name in ("app", "scripts", "uvicorn", "uvicorn.error"):
        log = logging.getLogger(name)
        log.handlers = [handler]
        log.propagate = False
        log.setLevel(logging.INFO)
    for old in previous:
        old.close()
    root.xinyu_role = role
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("httpx", "httpcore", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(logging.WARNING)


def metrics_key():
    return "xinyu:metrics:http:" + datetime.now(UTC).date().isoformat()


def record_request(route: str, method: str, status: int, elapsed: float):
    label = method + " " + route + " " + str(status // 100) + "xx"
    bucket = next((str(bound) for bound in BUCKETS if elapsed <= bound), "+Inf")
    try:
        with auth_cache() as cache, cache.pipeline(transaction=True) as pipe:
            key = metrics_key()
            pipe.hincrby(key, "count|" + label, 1)
            pipe.hincrby(key, "microseconds|" + label, round(elapsed * 1000000))
            pipe.hincrby(key, "bucket|" + bucket, 1)
            pipe.expire(key, 172800)
            pipe.execute()
    except AppError:
        pass  # Telemetry failure must not change a successful business result.


def http_metrics():
    with auth_cache() as cache:
        raw = cache.hgetall(metrics_key())
    counts = {
        key[6:]: int(value) for key, value in raw.items() if key.startswith("count|")
    }
    return {
        "window": "UTC day",
        "requests": sum(counts.values()),
        "errors_5xx": sum(
            value for key, value in counts.items() if key.endswith("5xx")
        ),
        "by_route": counts,
        "duration_seconds_sum": sum(
            int(value) for key, value in raw.items() if key.startswith("microseconds|")
        )
        / 1000000,
        "duration_bucket_counts": {
            key[7:]: int(value)
            for key, value in raw.items()
            if key.startswith("bucket|")
        },
    }


def ops_token() -> str:
    explicit = get_settings().ops_token.get_secret_value()
    if explicit:
        if len(explicit) < 32:
            raise AppError(503, "OPS_TOKEN_TOO_SHORT", "OPS_TOKEN 至少需要 32 个字符")
        return explicit
    if get_settings().app_env == "production":
        raise AppError(503, "OPS_NOT_CONFIGURED", "运维接口尚未配置")
    path = PROJECT_ROOT / ".runtime" / "secrets" / "ops.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(32))
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32:
        raise AppError(503, "OPS_KEY_UNAVAILABLE", "运维密钥正在准备，请稍后重试")
    return token


class RequestTelemetry:
    def __init__(self, app, route_prefix=""):
        self.app = app
        self.route_prefix = route_prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = secrets.token_hex(16)
        scope.setdefault("state", {})["request_id"] = request_id
        context_token = request_context.set(request_id)
        started = time.monotonic()
        status = 500
        sent = False

        async def observed_send(message):
            nonlocal status, sent
            if message["type"] == "http.response.start":
                sent = True
                status = message["status"]
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in {b"x-request-id", b"cache-control"}
                ]
                message = {
                    **message,
                    "headers": [
                        *headers,
                        (b"x-request-id", request_id.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ],
                }
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        except Exception as exc:
            logging.getLogger(__name__).error(
                "http_failed",
                extra={"request_id": request_id, "error_type": type(exc).__name__},
            )
            if sent:
                raise
            await JSONResponse(
                {
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "服务暂时无法处理请求，请凭请求编号联系维护人员",
                        "details": {},
                    },
                    "request_id": request_id,
                },
                status_code=500,
            )(scope, receive, observed_send)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            # Recent FastAPI included routers keep their original path in scope['route'].
            if (
                self.route_prefix
                and route != "unmatched"
                and scope["path"].startswith(self.route_prefix + "/")
                and not route.startswith(self.route_prefix + "/")
            ):
                route = self.route_prefix + route
            method = (
                scope["method"]
                if scope["method"]
                in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
                else "OTHER"
            )
            elapsed = time.monotonic() - started
            logging.getLogger(__name__).info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "job_id": scope["state"].get("job_id"),
                    "route": route,
                    "method": method,
                    "status": status,
                    "elapsed_ms": round(elapsed * 1000),
                },
            )
            try:
                await run_in_threadpool(record_request, route, method, status, elapsed)
            finally:
                request_context.reset(context_token)
