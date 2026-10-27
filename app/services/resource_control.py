"""Durable admission/token budgets and expiring global provider concurrency slots."""

import json
import logging
import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Text, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models.relationship import (
    RelationshipAccount,
    RelationshipDailyUsage,
    RelationshipItem,
    RelationshipModelCall,
)
from app.repositories.database import get_engine
from app.services.auth_cache import auth_cache, consume_rate, digest

SLOT_KEY = "xinyu:resources:model-slots"
TAKE_SLOT = """
local clock=redis.call('TIME')
local now=tonumber(clock[1])*1000+math.floor(tonumber(clock[2])/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
if redis.call('ZCARD',KEYS[1]) >= tonumber(ARGV[1]) then return 0 end
redis.call('ZADD',KEYS[1],now+tonumber(ARGV[3]),ARGV[2])
redis.call('PEXPIRE',KEYS[1],tonumber(ARGV[3])+60000)
return 1
"""


@dataclass(frozen=True)
class Operation:
    owner: str
    id: str
    purpose: str
    check_active: Callable[[], None] | None = None
    progress: Callable[[str, int], None] | None = None
    job_id: str | None = None


operation_context: ContextVar[Operation | None] = ContextVar(
    "model_operation", default=None
)


@contextmanager
def operation_scope(
    owner: str,
    operation_id: str,
    purpose: str,
    check_active=None,
    progress=None,
    job_id=None,
) -> Iterator[Operation]:
    operation = Operation(owner, operation_id, purpose, check_active, progress, job_id)
    token = operation_context.set(operation)
    try:
        yield operation
    finally:
        operation_context.reset(token)


@contextmanager
def purpose_scope(purpose: str):
    current = operation_context.get()
    token = operation_context.set(
        replace(current, purpose=purpose) if current else None
    )
    try:
        yield
    finally:
        operation_context.reset(token)


def ensure_active():
    current = operation_context.get()
    if current and current.check_active:
        current.check_active()


def report_progress(node: str, step: int = 0):
    ensure_active()
    current = operation_context.get()
    if current and current.progress:
        try:
            current.progress(node, step)
        except AppError:
            logging.getLogger(__name__).warning("job_progress_unavailable")


def utc_window() -> tuple[str, datetime]:
    now = datetime.now(UTC)
    reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.date().isoformat(), reset


def locked_usage(db: Session, scope: str, day: str) -> RelationshipDailyUsage:
    db.execute(
        insert(RelationshipDailyUsage)
        .values(scope=scope, day=day, requests=0, reserved_tokens=0, charged_tokens=0)
        .on_conflict_do_nothing()
    )
    return db.scalar(
        select(RelationshipDailyUsage)
        .where(RelationshipDailyUsage.scope == scope, RelationshipDailyUsage.day == day)
        .with_for_update()
    )


def account_limits(db: Session, owner: str) -> tuple[int, int]:
    settings = get_settings()
    registered = (
        db.scalar(
            select(RelationshipAccount.username).where(
                RelationshipAccount.owner == owner
            )
        )
        is not None
    )
    return (
        (settings.account_daily_requests, settings.account_daily_tokens)
        if registered
        else (settings.guest_daily_requests, settings.guest_daily_tokens)
    )


def daily_error(message: str, reset: datetime):
    raise AppError(
        429,
        "DAILY_QUOTA_EXCEEDED",
        message,
        {
            "retry_after_seconds": max(
                1, math.ceil((reset - datetime.now(UTC)).total_seconds())
            ),
            "resets_at": reset.isoformat(),
        },
    )


def admit_request(db: Session, owner: str, ip_scope: str, purpose: str):
    settings = get_settings()
    consume_rate(
        "business:" + digest(ip_scope) + ":" + purpose,
        20,
        60,
        "操作过于频繁，请稍后再试",
    )
    day, reset = utc_window()
    request_limit, _ = account_limits(db, owner)
    # Global -> IP -> owner is the universal lock order, shared with model reservations.
    limits = [
        ("global", settings.global_daily_requests),
        ("ip:" + digest(ip_scope), settings.ip_daily_requests),
        (owner, request_limit),
    ]
    rows = []
    for scope, maximum in limits:
        row = locked_usage(db, scope, day)
        if row.requests >= maximum:
            daily_error("今日使用次数已达上限，请在额度重置后重试", reset)
        rows.append(row)
    for row in rows:
        row.requests += 1


def check_storage(db: Session, owner: str, added_bytes: int = 0, added_items: int = 0):
    settings = get_settings()
    db.execute(text("SELECT pg_advisory_xact_lock(718302612)"))
    aggregate = select(
        func.count(),
        func.coalesce(
            func.sum(
                func.octet_length(RelationshipItem.title)
                + func.octet_length(RelationshipItem.quote)
                + func.octet_length(RelationshipItem.details.cast(Text))
            ),
            0,
        ),
    )
    count, used = db.execute(aggregate.where(RelationshipItem.owner == owner)).one()
    if (
        count + added_items > settings.owner_item_limit
        or used + added_bytes > settings.owner_storage_bytes
    ):
        raise AppError(
            413,
            "SPACE_QUOTA_EXCEEDED",
            "当前空间资料数量或容量已达上限，请先删除不需要的资料",
        )
    total_count, total_used = db.execute(aggregate).one()
    if (
        total_count + added_items > settings.global_item_limit
        or total_used + added_bytes > settings.global_storage_bytes
    ):
        raise AppError(503, "STORAGE_BUSY", "当前资料存储已达容量上限，请稍后再试")


def local_operation_rate(
    owner: str, ip: str, purpose: str, maximum: int, seconds: int = 60
):
    message = "操作过于频繁，请稍后再试"
    consume_rate("local:global:" + purpose, maximum * 10, seconds, message)
    consume_rate(
        "local:ip:" + purpose + ":" + digest(ip), maximum * 2, seconds, message
    )
    consume_rate("local:owner:" + purpose + ":" + owner, maximum, seconds, message)


def reserve_call(model: str, input_tokens: int, output_tokens: int) -> tuple[str, str]:
    ensure_active()
    current = operation_context.get()
    if current is None:
        raise AppError(503, "MODEL_CONTEXT_REQUIRED", "模型调用缺少受控执行上下文")
    settings = get_settings()
    reserved = input_tokens + output_tokens
    if input_tokens > settings.model_call_input_limit:
        raise AppError(
            413, "MODEL_INPUT_LIMIT", "本轮上下文过长，请缩短消息或减少参考资料"
        )
    day, reset = utc_window()
    with Session(get_engine()) as db:
        _, owner_limit = account_limits(db, current.owner)
        global_row = locked_usage(db, "global", day)
        owner_row = locked_usage(db, current.owner, day)
        for row, maximum in [
            (global_row, settings.global_daily_tokens),
            (owner_row, owner_limit),
        ]:
            if row.charged_tokens + row.reserved_tokens + reserved > maximum:
                daily_error("今日模型 Token 预算不足，请在额度重置后重试", reset)
        calls = list(
            db.scalars(
                select(RelationshipModelCall).where(
                    RelationshipModelCall.owner == current.owner,
                    RelationshipModelCall.operation_id == current.id,
                )
            )
        )
        spent = sum(
            row.reserved_tokens if row.status == "reserved" else row.charged_tokens
            for row in calls
        )
        if (
            len(calls) >= settings.model_calls_per_request
            or spent + reserved > settings.model_budget_per_request
        ):
            raise AppError(
                429,
                "OPERATION_BUDGET_EXCEEDED",
                "本次任务已达到模型预算，请缩小问题范围",
            )
        for row in [global_row, owner_row]:
            row.reserved_tokens += reserved
        call_id = str(uuid4())
        db.add(
            RelationshipModelCall(
                id=call_id,
                owner=current.owner,
                operation_id=current.id,
                purpose=current.purpose,
                model=model[:150],
                day=day,
                reserved_tokens=reserved,
                charged_tokens=0,
                status="reserved",
                usage_reported=False,
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
    return call_id, day


def settle_call(
    call_id: str,
    *,
    usage: dict | None = None,
    sent: bool = True,
    rejected: bool = False,
):
    # Settle idempotently; calls with an uncertain provider outcome retain conservative budget charges.
    with Session(get_engine()) as db:
        entry = db.get(RelationshipModelCall, call_id)
        if not entry:
            return
        global_row = locked_usage(db, "global", entry.day)
        owner_row = locked_usage(db, entry.owner, entry.day)
        entry = db.scalar(
            select(RelationshipModelCall)
            .where(RelationshipModelCall.id == call_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if entry.status != "reserved":
            return
        reported = (
            isinstance((usage or {}).get("total_tokens"), int)
            and usage["total_tokens"] >= 0
        )
        charged = (
            usage["total_tokens"]
            if reported
            else 0
            if rejected or not sent
            else entry.reserved_tokens
        )
        for row in [global_row, owner_row]:
            row.reserved_tokens = max(0, row.reserved_tokens - entry.reserved_tokens)
            row.charged_tokens += charged
        entry.status = (
            "completed"
            if reported
            else "rejected"
            if rejected or not sent
            else "uncertain"
        )
        entry.charged_tokens, entry.usage_reported = charged, reported
        entry.prompt_tokens = (usage or {}).get("prompt_tokens") if reported else None
        entry.completion_tokens = (
            (usage or {}).get("completion_tokens") if reported else None
        )
        entry.completed_at = datetime.now(UTC)
        db.commit()


def estimate_input(messages: list[dict], tools: list[dict]) -> int:
    images = 0

    def scrub(value):
        nonlocal images
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                images += 1
                return {"type": "image_url"}
            return {key: scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    size = len(json.dumps(scrub(messages), ensure_ascii=False).encode()) + len(
        json.dumps(tools, ensure_ascii=False).encode()
    )
    # UTF-8 byte count deliberately overestimates common text tokenizers. Image tokens are configured reserves.
    return size + 256 + images * get_settings().model_image_token_reserve


@contextmanager
def provider_slot(deadline: float):
    token = str(uuid4())
    # HTTPX read timeouts apply to each read. The streamed body checks the wall deadline between chunks.
    lease_ms = max(
        30000,
        math.ceil(
            (deadline - time.monotonic() + get_settings().model_timeout_seconds + 30)
            * 1000
        ),
    )
    with auth_cache() as cache:
        acquired = cache.eval(
            TAKE_SLOT,
            1,
            SLOT_KEY,
            get_settings().global_model_concurrency,
            token,
            lease_ms,
        )
    if not acquired:
        raise AppError(
            503, "MODEL_BUSY", "模型服务正忙，请稍后重试", {"retry_after_seconds": 5}
        )
    try:
        yield
    finally:
        try:
            with auth_cache() as cache:
                cache.zrem(SLOT_KEY, token)
        except AppError:
            logging.getLogger(__name__).warning("model_slot_release_failed")


def usage_summary(db: Session, owner: str) -> dict:
    day, reset = utc_window()
    request_limit, token_limit = account_limits(db, owner)
    row = db.get(RelationshipDailyUsage, (owner, day))
    categories = db.execute(
        select(
            RelationshipModelCall.purpose,
            func.count(),
            func.sum(RelationshipModelCall.charged_tokens),
        )
        .where(RelationshipModelCall.owner == owner, RelationshipModelCall.day == day)
        .group_by(RelationshipModelCall.purpose)
    ).all()
    return {
        "day": day,
        "resets_at": reset,
        "requests": row.requests if row else 0,
        "request_limit": request_limit,
        "charged_tokens": row.charged_tokens if row else 0,
        "reserved_tokens": row.reserved_tokens if row else 0,
        "token_limit": token_limit,
        "usage_by_purpose": [
            {"purpose": purpose, "calls": count, "charged_tokens": charged or 0}
            for purpose, count, charged in categories
        ],
        "currency_cost": None,
        "cost_note": "Token 包含保守预留记账；未配置供应商价格时不展示金额",
    }


def reconcile_abandoned_calls() -> int:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(
        seconds=max(settings.agent_timeout_seconds, settings.model_timeout_seconds) * 2
        + 120
    )
    with Session(get_engine()) as db:
        ids = list(
            db.scalars(
                select(RelationshipModelCall.id)
                .where(
                    RelationshipModelCall.status == "reserved",
                    RelationshipModelCall.created_at < cutoff,
                )
                .limit(100)
            )
        )
    for call_id in ids:
        settle_call(call_id)
    return len(ids)
