import gzip
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.relationship import RelationshipModelCall
from app.providers import model_provider
from app.repositories.database import get_engine
from app.services.auth_cache import get_redis
from app.services.resource_control import SLOT_KEY, operation_scope, usage_summary
from tests.test_resource_control import owner


def run_provider():
    return model_provider.complete(
        [{"role": "user", "content": "Synthetic fixture"}],
        [],
        time.monotonic() + 10,
        0,
        lambda _: None,
    )


@pytest.mark.parametrize("purpose", ["advice", "ocr", "rerank"])
def test_provider_usage_is_recorded_for_every_purpose(backend, monkeypatch, purpose):
    value = owner()

    def handler(request):
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "{}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(model_provider, "model_http_client", lambda: client)
        with operation_scope(value, "operation", purpose):
            run_provider()
    with Session(get_engine()) as db:
        summary = usage_summary(db, value)
        assert summary["charged_tokens"] == 15 and summary["reserved_tokens"] == 0
        assert summary["usage_by_purpose"] == [
            {"purpose": purpose, "calls": 1, "charged_tokens": 15}
        ]


def test_malformed_response_without_usage_keeps_conservative_charge(
    backend, monkeypatch
):
    value = owner()
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="not-json"))
    ) as client:
        monkeypatch.setattr(model_provider, "model_http_client", lambda: client)
        with operation_scope(value, "operation", "advice"), pytest.raises(AppError):
            run_provider()
    with Session(get_engine()) as db:
        row = db.scalar(
            select(RelationshipModelCall).where(RelationshipModelCall.owner == value)
        )
        assert row.status == "uncertain" and row.charged_tokens == row.reserved_tokens


def test_rejected_provider_request_does_not_leave_reserved_tokens(backend, monkeypatch):
    value = owner()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(429, json={"error": "synthetic"})
        )
    ) as client:
        monkeypatch.setattr(model_provider, "model_http_client", lambda: client)
        with operation_scope(value, "operation", "advice"), pytest.raises(AppError):
            run_provider()
    with Session(get_engine()) as db:
        summary = usage_summary(db, value)
        assert summary["reserved_tokens"] == 0 and summary["charged_tokens"] == 0


def test_provider_cannot_run_without_controlled_context(backend, monkeypatch):
    sent = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: sent.append(request))
    ) as client:
        monkeypatch.setattr(model_provider, "model_http_client", lambda: client)
        with pytest.raises(AppError) as failure:
            run_provider()
    assert failure.value.code == "MODEL_CONTEXT_REQUIRED" and not sent


def test_gzip_provider_usage_survives_bounded_response_rebuild(backend, monkeypatch):
    value = owner()
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "{}"}}],
        "usage": {"total_tokens": 15},
    }
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                content=gzip.compress(json.dumps(payload).encode()),
            )
        )
    ) as client:
        monkeypatch.setattr(model_provider, "model_http_client", lambda: client)
        with operation_scope(value, "gzip", "advice"):
            run_provider()
    with Session(get_engine()) as db:
        assert usage_summary(db, value)["charged_tokens"] == 15


def test_oversized_provider_response_is_bounded_and_slot_released(backend, monkeypatch):
    value = owner()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
        )
    ) as client:
        monkeypatch.setattr(model_provider, "model_http_client", lambda: client)
        with (
            operation_scope(value, "large", "advice"),
            pytest.raises(AppError) as failure,
        ):
            run_provider()
    assert failure.value.code == "MODEL_RESPONSE_TOO_LARGE"
    assert not get_redis().zcard(SLOT_KEY)


def test_streamed_body_enforces_wall_deadline(backend, monkeypatch):
    value = owner()
    now = time.monotonic()
    monkeypatch.setattr(
        model_provider,
        "time",
        SimpleNamespace(monotonic=lambda: now + 20, sleep=lambda _: None),
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"{}"))
    ) as client:
        monkeypatch.setattr(model_provider, "model_http_client", lambda: client)
        with (
            operation_scope(value, "deadline", "advice"),
            pytest.raises(httpx.ReadTimeout),
        ):
            model_provider.controlled_post(
                "https://fixture.invalid",
                {},
                {"model": "fixture", "messages": [], "max_tokens": 100},
                1,
                now + 10,
            )
    assert not get_redis().zcard(SLOT_KEY)
