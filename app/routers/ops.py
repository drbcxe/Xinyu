import hmac

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.errors import AppError
from app.services.observability import BUCKETS, http_metrics, ops_token
from app.services.ops_status import operational_status, probe_readiness

router = APIRouter()


@router.get("/ready")
def ready():
    status = probe_readiness()
    return JSONResponse(status, status_code=200 if status["ready"] else 503)


def authorize_ops(request: Request):
    expected = ops_token()
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode(), ("Bearer " + expected).encode()):
        raise AppError(403, "OPS_FORBIDDEN", "运维接口需要有效凭据")


@router.get("/ops/status", dependencies=[Depends(authorize_ops)])
def status():
    return operational_status()


@router.get("/ops/metrics", dependencies=[Depends(authorize_ops)])
def metrics():
    import json

    snapshot = http_metrics()
    lines = ["# TYPE xinyu_http_requests_total counter"]
    for key, count in sorted(snapshot["by_route"].items()):
        method, route, status_group = key.split(" ", 2)
        labels = ",".join(
            name + "=" + json.dumps(value)
            for name, value in (
                ("method", method),
                ("route", route),
                ("status_group", status_group),
            )
        )
        lines.append(f"xinyu_http_requests_total{{{labels}}} {count}")
    lines.append("# TYPE xinyu_http_request_duration_seconds histogram")
    cumulative = 0
    for bucket in [*(str(value) for value in BUCKETS), "+Inf"]:
        cumulative += snapshot["duration_bucket_counts"].get(bucket, 0)
        lines.append(
            f'xinyu_http_request_duration_seconds_bucket{{le="{bucket}"}} {cumulative}'
        )
    lines += [
        f"xinyu_http_request_duration_seconds_count {snapshot['requests']}",
        f"xinyu_http_request_duration_seconds_sum {snapshot['duration_seconds_sum']}",
    ]
    return PlainTextResponse(
        "\n".join(lines) + "\n", media_type="text/plain; version=0.0.4"
    )
