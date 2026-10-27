import secrets
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.errors import AppError
from app.models.relationship import (
    RelationshipFeedback,
    RelationshipJob,
    RelationshipPrivacy,
    RelationshipProfile,
    RelationshipTurn,
)
from app.services.account_closure import close_account, deletion_status
from app.services.auth_cache import (
    check_failures,
    clear_failures,
    digest,
    record_failure,
    verify_captcha,
)
from app.services.privacy_policy import (
    POLICY_VERSION,
    privacy_status,
    provider_fingerprint,
)
from app.services.recovery_journal import record_deletion
from app.services.relationship_auth import (
    DB,
    CaptchaFields,
    Owner,
    address,
    auth_rate,
    captcha_binding,
    check_origin,
    log_event,
    password_matches,
    require_account,
    set_cookie,
)
from app.services.relationship_jobs import get_profile
from app.services.resource_control import local_operation_rate
from app.services.storage_budget import lock_storage

router = APIRouter(
    prefix="/relationship", tags=["privacy"], dependencies=[Depends(check_origin)]
)


class AccountDeletion(CaptchaFields):
    current_password: str = Field(min_length=1, max_length=128)
    confirmation: Literal["注销账号"]


class ReceiptInput(BaseModel):
    receipt: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class FeedbackInput(BaseModel):
    rating: Literal["helpful", "unhelpful"]
    category: Literal["general", "inaccurate", "tone", "unsafe", "bug"] = "general"
    comment: str = Field(default="", max_length=1000)


class PrivacyInput(BaseModel):
    version: str = Field(max_length=40)
    provider_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    allow_model_processing: bool
    history_days: Literal[30, 90, 180] = 90


@router.get("/privacy")
def get_privacy(db: DB, owner: Owner):
    get_profile(db, owner)
    return privacy_status(db, owner)


@router.put("/privacy")
def set_privacy(payload: PrivacyInput, request: Request, db: DB, owner: Owner):
    local_operation_rate(owner, address(request), "privacy", 20)
    get_profile(db, owner)
    lock_storage(db)
    profile = db.scalar(
        select(RelationshipProfile)
        .where(RelationshipProfile.owner == owner)
        .with_for_update()
    )
    if not profile:
        raise AppError(401, "SPACE_CLOSED", "空间已关闭，请刷新")
    if (
        payload.version != POLICY_VERSION
        or payload.provider_fingerprint != provider_fingerprint()
    ):
        raise AppError(
            409,
            "PRIVACY_POLICY_CHANGED",
            "数据处理说明或模型配置已更新，请刷新后重新阅读",
        )
    row = db.get(RelationshipPrivacy, owner)
    if not row:
        row = RelationshipPrivacy(owner=owner)
        db.add(row)
    now = datetime.now(UTC)
    record_deletion(db, owner, "retention", str(payload.history_days))
    row.version, row.provider_fingerprint, row.history_days = (
        POLICY_VERSION,
        provider_fingerprint(),
        payload.history_days,
    )
    if payload.allow_model_processing:
        row.accepted_at, row.revoked_at = now, None
    else:
        row.revoked_at = now
        db.execute(
            update(RelationshipJob)
            .where(
                RelationshipJob.owner == owner,
                RelationshipJob.status.in_(["queued", "running"]),
            )
            .values(
                status="cancelled",
                cancel_requested=True,
                lease_token=None,
                lease_expires_at=None,
                updated_at=now,
                error="AI 数据处理授权已撤回",
            )
        )
    db.commit()
    return privacy_status(db, owner)


@router.delete("/auth/account", status_code=202)
def delete_account(
    payload: AccountDeletion, request: Request, response: Response, db: DB, owner: Owner
):
    auth_rate(request)
    verify_captcha(captcha_binding(request), payload.captcha_id, payload.captcha_code)
    get_profile(db, owner)
    account = require_account(db, owner, lock=True)
    check_failures(account.username, address(request))
    if not password_matches(payload.current_password, account.password_hash):
        record_failure(account.username, address(request))
        log_event(db, request, owner, "account_delete_failed")
        db.commit()
        raise AppError(401, "CURRENT_PASSWORD_INVALID", "当前密码不正确")
    clear_failures(account.username, address(request))
    receipt = close_account(db, owner)
    set_cookie(request, response, "g_" + secrets.token_hex(32))
    return {
        "ok": True,
        "receipt": receipt,
        "status": "pending",
        "message": "账号已注销，在线内容已移除，检索及临时副本继续在后台清理。请保存注销回执。",
    }


@router.post("/privacy/deletion/status")
def get_deletion_status(payload: ReceiptInput, request: Request, db: DB):
    local_operation_rate(
        digest(payload.receipt), address(request), "deletion-status", 30
    )
    return deletion_status(db, payload.receipt)


def own_turn(db, owner, turn_id, lock=False):
    if lock:
        lock_storage(db)
    profile = (
        db.scalar(
            select(RelationshipProfile)
            .where(RelationshipProfile.owner == owner)
            .with_for_update()
        )
        if lock
        else True
    )
    if not profile:
        raise AppError(404, "TURN_NOT_FOUND", "咨询记录不存在")
    turn = db.scalar(
        select(RelationshipTurn).where(
            RelationshipTurn.owner == owner, RelationshipTurn.id == str(turn_id)
        )
    )
    if not turn:
        raise AppError(404, "TURN_NOT_FOUND", "咨询记录不存在")
    return turn


@router.get("/feedback/{turn_id}")
def get_feedback(turn_id: UUID, db: DB, owner: Owner):
    own_turn(db, owner, turn_id)
    row = db.scalar(
        select(RelationshipFeedback).where(
            RelationshipFeedback.owner == owner,
            RelationshipFeedback.turn_id == str(turn_id),
        )
    )
    return {
        "feedback": {
            key: getattr(row, key)
            for key in ("rating", "category", "comment", "updated_at")
        }
        if row
        else None
    }


@router.put("/feedback/{turn_id}")
def save_feedback(
    turn_id: UUID, payload: FeedbackInput, request: Request, db: DB, owner: Owner
):
    local_operation_rate(owner, address(request), "feedback", 20)
    own_turn(db, owner, turn_id, lock=True)
    now = datetime.now(UTC)
    values = {
        "id": str(uuid4()),
        "owner": owner,
        "turn_id": str(turn_id),
        "rating": payload.rating,
        "category": payload.category,
        "comment": payload.comment.strip(),
        "created_at": now,
        "updated_at": now,
    }
    db.execute(
        insert(RelationshipFeedback)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["owner", "turn_id"],
            set_={
                key: values[key]
                for key in ("rating", "category", "comment", "updated_at")
            },
        )
    )
    db.commit()
    return {"ok": True}


@router.delete("/feedback/{turn_id}")
def delete_feedback(turn_id: UUID, request: Request, db: DB, owner: Owner):
    local_operation_rate(owner, address(request), "feedback", 20)
    own_turn(db, owner, turn_id, lock=True)
    record_deletion(db, owner, "feedback", str(turn_id))
    db.execute(
        RelationshipFeedback.__table__.delete().where(
            RelationshipFeedback.owner == owner,
            RelationshipFeedback.turn_id == str(turn_id),
        )
    )
    db.commit()
    return {"ok": True}
