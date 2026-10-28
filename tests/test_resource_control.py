import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.relationship import (
    RelationshipDailyUsage,
    RelationshipModelCall,
    RelationshipPrivacy,
    RelationshipProfile,
)
from app.repositories.database import get_engine
from app.services.auth_cache import get_redis
from app.services.privacy_policy import POLICY_VERSION, provider_fingerprint
from app.services.resource_control import (
    SLOT_KEY,
    admit_request,
    check_storage,
    estimate_input,
    operation_scope,
    provider_slot,
    reconcile_abandoned_calls,
    reserve_call,
    settle_call,
    usage_summary,
)


def owner():
    value = uuid4().hex + uuid4().hex
    with Session(get_engine()) as db:
        db.add(
            RelationshipProfile(owner=value, partner_name="fixture", style="fixture")
        )
        db.add(
            RelationshipPrivacy(
                owner=value,
                version=POLICY_VERSION,
                provider_fingerprint=provider_fingerprint(),
                accepted_at=datetime.now(UTC),
                history_days=90,
            )
        )
        db.commit()
    return value


def test_daily_admission_is_atomic_under_concurrency(backend):
    backend.guest_daily_requests = 3
    value = owner()

    def attempt(_):
        try:
            with Session(get_engine()) as db:
                admit_request(db, value, "same-test-ip", "advice")
                db.commit()
            return True
        except AppError as exc:
            assert exc.code == "DAILY_QUOTA_EXCEEDED"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(attempt, range(8)))
    assert sum(accepted) == 3
    with Session(get_engine()) as db:
        assert usage_summary(db, value)["requests"] == 3


def test_token_reservations_and_settlement_are_idempotent(backend):
    value = owner()
    with operation_scope(value, "request-1", "advice"):
        call_id, day = reserve_call("fixture", 500, 100)
    with Session(get_engine()) as db:
        assert db.get(RelationshipDailyUsage, (value, day)).reserved_tokens == 600
    settle_call(
        call_id,
        usage={"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
    )
    settle_call(call_id, usage={"total_tokens": 50})
    with Session(get_engine()) as db:
        usage = usage_summary(db, value)
        assert usage["charged_tokens"] == 50 and usage["reserved_tokens"] == 0
        assert usage["usage_by_purpose"][0]["purpose"] == "advice"


def test_concurrent_settlements_refresh_locked_entry(backend, monkeypatch):
    from app.services import resource_control

    value = owner()
    with operation_scope(value, "concurrent-settle", "advice"):
        call_id, _ = reserve_call("fixture", 500, 100)
    barrier = threading.Barrier(8)
    original = resource_control.locked_usage

    def coordinated(db, scope, day):
        if scope == "global":
            barrier.wait(timeout=10)
        return original(db, scope, day)

    monkeypatch.setattr(resource_control, "locked_usage", coordinated)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: settle_call(call_id, usage={"total_tokens": 50}), range(8)
            )
        )
    with Session(get_engine()) as db:
        summary = usage_summary(db, value)
        assert summary["charged_tokens"] == 50 and summary["reserved_tokens"] == 0


def test_abandoned_reservations_are_conservatively_reconciled(backend):
    value = owner()
    with operation_scope(value, "abandoned", "advice"):
        call_id, _ = reserve_call("fixture", 500, 100)
    with Session(get_engine()) as db:
        db.get(RelationshipModelCall, call_id).created_at = datetime.now(
            UTC
        ) - timedelta(hours=1)
        db.commit()
    assert reconcile_abandoned_calls() == 1
    assert reconcile_abandoned_calls() == 0
    with Session(get_engine()) as db:
        assert usage_summary(db, value)["charged_tokens"] == 600


@pytest.mark.parametrize(
    "sent,rejected,charge", [(True, False, 600), (False, False, 0), (True, True, 0)]
)
def test_uncertain_calls_are_not_silently_free(backend, sent, rejected, charge):
    value = owner()
    with operation_scope(value, "uncertain", "ocr"):
        call_id, _ = reserve_call("fixture", 500, 100)
    settle_call(call_id, sent=sent, rejected=rejected)
    with Session(get_engine()) as db:
        assert usage_summary(db, value)["charged_tokens"] == charge


def test_global_token_budget_is_shared_between_owners(backend):
    backend.global_daily_tokens = 1000
    first, second = owner(), owner()
    with operation_scope(first, "first", "advice"):
        reserve_call("fixture", 500, 100)
    with (
        operation_scope(second, "second", "advice"),
        pytest.raises(AppError) as failure,
    ):
        reserve_call("fixture", 500, 100)
    assert failure.value.code == "DAILY_QUOTA_EXCEEDED"


def test_request_budget_includes_rerank_and_retry_calls(backend):
    backend.model_budget_per_request = 1000
    value = owner()
    with operation_scope(value, "same-request", "rerank"):
        call_id, _ = reserve_call("fixture", 500, 100)
    settle_call(call_id)
    with (
        operation_scope(value, "same-request", "advice"),
        pytest.raises(AppError) as failure,
    ):
        reserve_call("fixture", 500, 100)
    assert failure.value.code == "OPERATION_BUDGET_EXCEEDED"


def test_provider_concurrency_and_cleanup(backend):
    backend.global_model_concurrency = 1
    with provider_slot(time.monotonic() + 10):
        with pytest.raises(AppError) as failure, provider_slot(time.monotonic() + 10):
            pass
        assert failure.value.code == "MODEL_BUSY"


def test_shorter_call_cannot_expire_another_live_provider_slot(backend):
    backend.global_model_concurrency = 2
    cache = get_redis()
    with provider_slot(time.monotonic() + 120), provider_slot(time.monotonic() + 10):
        last_expiry = cache.zrange(SLOT_KEY, -1, -1, withscores=True)[0][1]
        seconds, microseconds = cache.time()
        remaining = last_expiry - seconds * 1000 - microseconds // 1000
        assert cache.pttl(SLOT_KEY) >= remaining + 60000 - 500
    assert get_redis().zcard(SLOT_KEY) == 0


def test_expired_slots_do_not_block_future_calls(backend):
    backend.global_model_concurrency = 1
    get_redis().zadd(SLOT_KEY, {"expired": 0})
    with provider_slot(time.monotonic() + 10):
        assert get_redis().zcard(SLOT_KEY) == 1


def test_vision_estimate_does_not_count_base64_as_text(backend):
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + "A" * 100000},
                }
            ],
        }
    ]
    assert (
        backend.model_image_token_reserve
        < estimate_input(messages, [])
        < backend.model_image_token_reserve + 1000
    )


def test_storage_limit_rejects_growth(backend):
    backend.owner_storage_bytes = 100
    with Session(get_engine()) as db, pytest.raises(AppError) as failure:
        check_storage(db, owner(), added_bytes=101, added_items=1)
    assert failure.value.code == "SPACE_QUOTA_EXCEEDED"
