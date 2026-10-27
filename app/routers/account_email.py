from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.errors import AppError
from app.models.relationship import (
    RelationshipAccount,
    RelationshipEmailChallenge,
    RelationshipEmailOutbox,
    RelationshipSession,
)
from app.services.account_email import (
    TTL,
    invalidate_challenges,
    mail_rate,
    masked_email,
    normalize_email,
    queue_challenge,
    require_smtp,
    smtp_configured,
    verify_challenge,
)
from app.services.auth_cache import (
    check_failures,
    clear_failures,
    record_failure,
    verify_captcha,
)
from app.services.relationship_auth import (
    DB,
    CaptchaFields,
    Owner,
    address,
    auth_rate,
    captcha_binding,
    check_origin,
    hash_password,
    log_event,
    password_matches,
    require_account,
    revoke_sessions,
    validate_password,
)
from app.services.storage_budget import lock_storage

router = APIRouter(
    prefix="/relationship/auth", tags=["account"], dependencies=[Depends(check_origin)]
)


class EmailFields(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        return normalize_email(value)


class EmailSend(EmailFields, CaptchaFields):
    current_password: str = Field(min_length=1, max_length=128)


class EmailVerify(BaseModel):
    challenge_id: UUID
    code: str = Field(pattern=r"^\d{6}$")


class RecoverySend(EmailFields, CaptchaFields):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")


class RecoveryVerify(EmailVerify):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")
    new_password: str = Field(min_length=15, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)


@router.get("/email")
def email_status(db: DB, owner: Owner):
    account = require_account(db, owner)
    return {
        "email": masked_email(account.email),
        "verified": bool(account.email and account.email_verified_at),
        "service_configured": smtp_configured(),
    }


@router.post("/email/send", status_code=202)
def email_send(payload: EmailSend, request: Request, db: DB, owner: Owner):
    auth_rate(request)
    require_smtp()
    verify_captcha(captcha_binding(request), payload.captcha_id, payload.captcha_code)
    account = require_account(db, owner, lock=True)
    check_failures(account.username, address(request))
    if not password_matches(payload.current_password, account.password_hash):
        record_failure(account.username, address(request))
        log_event(db, request, owner, "email_bind_failed")
        db.commit()
        raise AppError(401, "CURRENT_PASSWORD_INVALID", "当前密码不正确")
    clear_failures(account.username, address(request))
    mail_rate(address(request), payload.email)
    challenge_id = queue_challenge(
        db, account, captcha_binding(request), "bind", payload.email
    )
    log_event(db, request, owner, "email_verification_requested")
    db.commit()
    return {
        "challenge_id": challenge_id,
        "expires_in": TTL,
        "message": "验证邮件已加入发送队列，请查看邮箱",
    }


@router.post("/email/verify")
def email_verify(payload: EmailVerify, request: Request, db: DB, owner: Owner):
    auth_rate(request)
    account = require_account(db, owner, lock=True)
    challenge = verify_challenge(
        db,
        account,
        str(payload.challenge_id),
        captcha_binding(request),
        "bind",
        payload.code,
    )
    from datetime import UTC, datetime

    account.email, account.email_verified_at = challenge.email, datetime.now(UTC)
    invalidate_challenges(db, owner)
    log_event(db, request, owner, "email_verified")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(
            409, "EMAIL_UNAVAILABLE", "此邮箱暂时无法绑定，请使用其他邮箱"
        ) from None
    return {"ok": True, "email": masked_email(account.email)}


@router.get("/email/delivery/{challenge_id}")
def email_delivery(challenge_id: UUID, request: Request, db: DB, owner: Owner):
    require_account(db, owner)
    row = db.scalar(
        select(RelationshipEmailOutbox)
        .join(
            RelationshipEmailChallenge,
            RelationshipEmailChallenge.id == RelationshipEmailOutbox.id,
        )
        .where(
            RelationshipEmailOutbox.id == str(challenge_id),
            RelationshipEmailOutbox.owner == owner,
            RelationshipEmailChallenge.binding == captcha_binding(request),
            RelationshipEmailChallenge.purpose == "bind",
        )
    )
    if not row:
        raise AppError(404, "EMAIL_REQUEST_NOT_FOUND", "邮件申请不存在")
    return {"status": row.status, "error": row.error}


@router.post("/recovery/send", status_code=202)
def recovery_send(payload: RecoverySend, request: Request, db: DB, owner: Owner):
    auth_rate(request)
    require_smtp()
    verify_captcha(captcha_binding(request), payload.captcha_id, payload.captcha_code)
    mail_rate(address(request), payload.email)
    lock_storage(db)
    account = db.scalar(
        select(RelationshipAccount)
        .where(RelationshipAccount.username == payload.username.lower())
        .with_for_update()
    )
    challenge_id = str(uuid4())
    if account and account.email == payload.email and account.email_verified_at:
        challenge_id = queue_challenge(
            db, account, captcha_binding(request), "reset", account.email
        )
        log_event(db, request, account.owner, "password_reset_requested")
    db.commit()
    # Same shape and asynchronous response for unknown usernames, unverified emails and valid accounts.
    return {
        "challenge_id": challenge_id,
        "expires_in": TTL,
        "message": "如果账号与已验证邮箱匹配，验证码将发送到该邮箱",
    }


@router.post("/recovery/verify")
def recovery_verify(payload: RecoveryVerify, request: Request, db: DB, owner: Owner):
    auth_rate(request)
    validate_password(payload.new_password, payload.confirm_password)
    lock_storage(db)
    account = db.scalar(
        select(RelationshipAccount)
        .where(RelationshipAccount.username == payload.username.lower())
        .with_for_update()
    )
    challenge = verify_challenge(
        db,
        account,
        str(payload.challenge_id),
        captcha_binding(request),
        "reset",
        payload.code,
    )
    if account.email != challenge.email or not account.email_verified_at:
        raise AppError(422, "EMAIL_CODE_INVALID", "邮箱验证码无效，请重新申请")
    account.password_hash = hash_password(payload.new_password)
    revoke_sessions(
        db,
        list(
            db.scalars(
                select(RelationshipSession).where(
                    RelationshipSession.owner == account.owner
                )
            )
        ),
    )
    invalidate_challenges(db, account.owner)
    clear_failures(account.username, address(request))
    log_event(db, request, account.owner, "password_reset")
    db.commit()
    return {"ok": True, "message": "密码已重置，所有旧会话已退出，请使用新密码登录"}
