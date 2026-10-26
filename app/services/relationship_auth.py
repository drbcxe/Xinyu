"""PostgreSQL account records with Redis verification, throttling and session cache."""

import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models.relationship import (
    RelationshipAccount,
    RelationshipAuthEvent,
    RelationshipSession,
)
from app.repositories.database import get_db
from app.services.auth_cache import (
    CACHE_SESSION_SCRIPT,
    PREFIX,
    SESSION_SCRIPT,
    auth_cache,
    check_failures,
    clear_failures,
    consume_rate,
    create_captcha,
    digest,
    record_failure,
    verify_captcha,
)

DB = Annotated[Session, Depends(get_db)]
COOKIE = "relationship_session"


def check_origin(request: Request):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        allowed = {
            value.strip().rstrip("/")
            for value in get_settings().auth_allowed_origins.split(",")
        }
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in allowed:
            raise AppError(403, "ORIGIN_FORBIDDEN", "请求来源未获允许")


router = APIRouter(
    prefix="/relationship/auth", tags=["account"], dependencies=[Depends(check_origin)]
)


def token_hash(token: str) -> str:
    return digest(token)


def set_cookie(
    request: Request, response: Response, token: str, max_age: int | None = 30 * 86400
):
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=max_age,
        path="/api/relationship",
    )
    request.state.auth_token = token


def session_keys(key: str) -> tuple[str, str]:
    return PREFIX + "revoked:" + key, PREFIX + "session:" + key


def resolve_session(db: Session, key: str) -> str | None:
    revoked, cached = session_keys(key)
    with auth_cache() as cache:
        value = cache.eval(SESSION_SCRIPT, 2, revoked, cached)
    if value == "__revoked__":
        return None
    if value:
        return json.loads(value)["owner"]
    session = db.get(RelationshipSession, key)
    if not session or session.expires_at <= datetime.now(UTC):
        return None
    ttl = max(1, int((session.expires_at - datetime.now(UTC)).total_seconds()))
    with auth_cache() as cache:
        accepted = cache.eval(
            CACHE_SESSION_SCRIPT,
            2,
            revoked,
            cached,
            json.dumps({"owner": session.owner, "id": session.id}),
            ttl,
        )
    return session.owner if accepted else None


def owner_id(request: Request, response: Response, db: DB) -> str:
    token = request.cookies.get(COOKIE, "")
    request.state.auth_token = token
    if re.fullmatch(r"s_[0-9a-f]{64}", token):
        owner = resolve_session(db, token_hash(token))
        if owner:
            return owner
    elif re.fullmatch(r"(?:g_)?[0-9a-f]{64}", token):
        key = token_hash(token)
        # Support sessions and anonymous spaces created before the token prefixes.
        session = (
            db.get(RelationshipSession, key) if not token.startswith("g_") else None
        )
        if session:
            owner = resolve_session(db, key)
            if owner:
                return owner
        elif not db.scalar(
            select(RelationshipAccount.username).where(RelationshipAccount.owner == key)
        ):
            return key
    token = "g_" + secrets.token_hex(32)
    set_cookie(request, response, token)
    return token_hash(token)


Owner = Annotated[str, Depends(owner_id)]


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    result = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=32768,
        r=8,
        p=1,
        maxmem=64 * 1024 * 1024,
    ).hex()
    return salt + ":" + result


def password_matches(password: str, stored: str) -> bool:
    return hmac.compare_digest(hash_password(password, stored.split(":")[0]), stored)


def validate_password(password: str, confirmation: str):
    if not hmac.compare_digest(password.encode(), confirmation.encode()):
        raise AppError(422, "PASSWORD_MISMATCH", "两次输入的密码不一致")
    common = {
        "123456789012345",
        "1234567890123456",
        "passwordpassword",
        "qwertyuiopasdfgh",
        "abcdefghijklmno",
    }
    if (
        len(password) < 15
        or len(password) > 128
        or not password.strip()
        or len(set(password)) < 4
        or password.lower() in common
    ):
        raise AppError(
            422,
            "WEAK_PASSWORD",
            "请使用 15–128 位密码，避免重复字符、连续数字或常见密码；推荐一段好记的长口令",
        )


def address(request: Request) -> str:
    return request.client.host if request.client else "local"


def device(request: Request) -> str:
    ua = request.headers.get("user-agent", "")
    browser = next(
        (
            name
            for flag, name in [
                ("Edg/", "Edge"),
                ("Firefox/", "Firefox"),
                ("Chrome/", "Chrome"),
                ("Safari/", "Safari"),
            ]
            if flag in ua
        ),
        "其他客户端",
    )
    system = next(
        (
            name
            for flag, name in [
                ("Android", "Android"),
                ("iPhone", "iOS"),
                ("iPad", "iOS"),
                ("Windows", "Windows"),
                ("Macintosh", "macOS"),
                ("Linux", "Linux"),
            ]
            if flag in ua
        ),
        "未知系统",
    )
    return f"{browser} · {system}"


def log_event(db: Session, request: Request, owner: str | None, event: str):
    db.add(
        RelationshipAuthEvent(
            id=str(uuid4()),
            owner=owner,
            event=event,
            device_label=device(request),
            ip_tag=digest(address(request))[:12],
            created_at=datetime.now(UTC),
        )
    )


def auth_rate(request: Request):
    consume_rate(
        "request:" + digest(address(request)), 30, 60, "账号操作过于频繁，请稍后重试"
    )


def captcha_binding(request: Request) -> str:
    return token_hash(request.state.auth_token)


def require_account(db: Session, owner: str, lock: bool = False) -> RelationshipAccount:
    query = select(RelationshipAccount).where(RelationshipAccount.owner == owner)
    account = db.scalar(query.with_for_update() if lock else query)
    if not account:
        raise AppError(401, "LOGIN_REQUIRED", "请先登录账号")
    return account


def revoke_sessions(db: Session, rows: list[RelationshipSession]):
    if not rows:
        return
    # Tombstones are written before DB deletion, so a concurrent cache miss cannot rewarm a revoked session.
    with auth_cache() as cache, cache.pipeline(transaction=True) as pipe:
        for row in rows:
            revoked, cached = session_keys(row.token_hash)
            ttl = max(1, int((row.expires_at - datetime.now(UTC)).total_seconds()) + 1)
            pipe.set(revoked, "1", ex=ttl)
            pipe.delete(cached)
        pipe.execute()
    for row in rows:
        db.delete(row)


def issue_session(
    db: Session, request: Request, response: Response, owner: str, remember: bool
):
    # Callers hold the account row lock while changing the session set/password.
    rows = list(
        db.scalars(
            select(RelationshipSession)
            .where(RelationshipSession.owner == owner)
            .order_by(RelationshipSession.created_at)
        )
    )
    expired = [row for row in rows if row.expires_at <= datetime.now(UTC)]
    live = [row for row in rows if row not in expired]
    old_key = token_hash(request.cookies.get(COOKIE, ""))
    rotated = [row for row in live if row.token_hash == old_key]
    live = [row for row in live if row not in rotated]
    revoke_sessions(db, expired + rotated + live[: max(0, len(live) - 4)])
    ttl = 30 * 86400 if remember else 8 * 3600
    token = "s_" + secrets.token_hex(32)
    db.add(
        RelationshipSession(
            id=str(uuid4()),
            token_hash=token_hash(token),
            owner=owner,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
            device_label=device(request),
            ip_tag=digest(address(request))[:12],
        )
    )
    db.commit()
    set_cookie(request, response, token, ttl if remember else None)


class CaptchaFields(BaseModel):
    captcha_id: str = Field(min_length=48, max_length=48, pattern="^[0-9a-f]+$")
    captcha_code: str = Field(min_length=1, max_length=12, pattern="^[A-Za-z0-9 ]+$")


class Login(CaptchaFields):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = False


class Registration(Login):
    confirm_password: str = Field(min_length=1, max_length=128)


class PasswordChange(CaptchaFields):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=15, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)


@router.get("/captcha")
def captcha(request: Request, response: Response, owner: Owner):
    consume_rate(
        "captcha:" + digest(address(request)), 30, 60, "验证码刷新过于频繁，请稍后重试"
    )
    response.headers["Cache-Control"] = "no-store"
    return create_captcha(captcha_binding(request))


@router.get("/me")
def me(db: DB, owner: Owner):
    account = db.scalar(
        select(RelationshipAccount).where(RelationshipAccount.owner == owner)
    )
    return {"username": account.username if account else None}


@router.post("/register")
def register(
    payload: Registration, request: Request, response: Response, db: DB, owner: Owner
):
    auth_rate(request)
    verify_captcha(captcha_binding(request), payload.captcha_id, payload.captcha_code)
    validate_password(payload.password, payload.confirm_password)
    if db.scalar(select(RelationshipAccount).where(RelationshipAccount.owner == owner)):
        raise AppError(
            409, "ALREADY_REGISTERED", "当前空间已有账号，请先退出后注册其他账号"
        )
    account = RelationshipAccount(
        username=payload.username.lower(),
        owner=owner,
        password_hash=hash_password(payload.password),
        created_at=datetime.now(UTC),
    )
    db.add(account)
    log_event(db, request, owner, "registered")
    try:
        db.flush()
        issue_session(db, request, response, owner, payload.remember_me)
    except IntegrityError:
        db.rollback()
        raise AppError(409, "USERNAME_TAKEN", "用户名已被使用") from None
    return {"username": account.username}


@router.post("/login")
def login(payload: Login, request: Request, response: Response, db: DB, owner: Owner):
    auth_rate(request)
    username = payload.username.lower()
    check_failures(username, address(request))
    verify_captcha(captcha_binding(request), payload.captcha_id, payload.captcha_code)
    account = db.scalar(
        select(RelationshipAccount)
        .where(RelationshipAccount.username == username)
        .with_for_update()
    )
    # Recheck after acquiring the account lock: another in-flight attempt may have failed while we waited.
    check_failures(username, address(request))
    stored = account.password_hash if account else "00" * 16 + ":" + "00" * 64
    valid = password_matches(payload.password, stored)
    if not account or not valid:
        remaining = record_failure(username, address(request))
        log_event(db, request, account.owner if account else None, "login_failed")
        db.commit()
        if not remaining:
            check_failures(username, address(request))
        raise AppError(
            401,
            "INVALID_CREDENTIALS",
            "用户名或密码不正确",
            {"remaining_attempts": remaining},
        )
    clear_failures(username, address(request))
    log_event(db, request, account.owner, "logged_in")
    issue_session(db, request, response, account.owner, payload.remember_me)
    return {"username": account.username}


@router.post("/logout")
def logout(request: Request, response: Response, db: DB, owner: Owner):
    auth_rate(request)
    session = db.get(RelationshipSession, token_hash(request.state.auth_token))
    if session:
        require_account(db, owner, lock=True)
        revoke_sessions(db, [session])
        log_event(db, request, owner, "logged_out")
        db.commit()
    set_cookie(request, response, "g_" + secrets.token_hex(32))
    return {"ok": True}


@router.get("/sessions")
def sessions(request: Request, db: DB, owner: Owner):
    require_account(db, owner)
    rows = db.scalars(
        select(RelationshipSession)
        .where(
            RelationshipSession.owner == owner,
            RelationshipSession.expires_at > datetime.now(UTC),
        )
        .order_by(RelationshipSession.created_at.desc())
    )
    current_key = token_hash(request.state.auth_token)
    return [
        {
            "id": row.id,
            "current": row.token_hash == current_key,
            "device_label": row.device_label,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
        }
        for row in rows
    ]


@router.delete("/sessions/{session_id}")
def revoke(session_id: str, request: Request, response: Response, db: DB, owner: Owner):
    auth_rate(request)
    require_account(db, owner, lock=True)
    row = db.scalar(
        select(RelationshipSession).where(
            RelationshipSession.id == session_id, RelationshipSession.owner == owner
        )
    )
    if not row:
        raise AppError(404, "SESSION_NOT_FOUND", "会话不存在或已经退出")
    current = row.token_hash == token_hash(request.state.auth_token)
    revoke_sessions(db, [row])
    log_event(db, request, owner, "session_revoked")
    db.commit()
    if current:
        set_cookie(request, response, "g_" + secrets.token_hex(32))
    return {"ok": True, "current_revoked": current}


@router.post("/logout-all")
def logout_all(request: Request, db: DB, owner: Owner):
    auth_rate(request)
    require_account(db, owner, lock=True)
    key = token_hash(request.state.auth_token)
    rows = list(
        db.scalars(
            select(RelationshipSession).where(
                RelationshipSession.owner == owner,
                RelationshipSession.token_hash != key,
            )
        )
    )
    revoke_sessions(db, rows)
    log_event(db, request, owner, "other_sessions_revoked")
    db.commit()
    return {"ok": True, "revoked_count": len(rows)}


@router.post("/password")
def change_password(
    payload: PasswordChange, request: Request, response: Response, db: DB, owner: Owner
):
    auth_rate(request)
    account = require_account(db, owner, lock=True)
    check_failures(account.username, address(request))
    verify_captcha(captcha_binding(request), payload.captcha_id, payload.captcha_code)
    validate_password(payload.new_password, payload.confirm_password)
    if not password_matches(payload.current_password, account.password_hash):
        remaining = record_failure(account.username, address(request))
        log_event(db, request, owner, "password_change_failed")
        db.commit()
        if not remaining:
            check_failures(account.username, address(request))
        raise AppError(
            401,
            "CURRENT_PASSWORD_INVALID",
            "当前密码不正确",
            {"remaining_attempts": remaining},
        )
    if payload.new_password == payload.current_password:
        raise AppError(422, "PASSWORD_UNCHANGED", "新密码不能与当前密码相同")
    account.password_hash = hash_password(payload.new_password)
    revoke_sessions(
        db,
        list(
            db.scalars(
                select(RelationshipSession).where(RelationshipSession.owner == owner)
            )
        ),
    )
    db.flush()
    clear_failures(account.username, address(request))
    log_event(db, request, owner, "password_changed")
    issue_session(db, request, response, owner, remember=False)
    return {"ok": True}


@router.get("/events")
def events(db: DB, owner: Owner):
    require_account(db, owner)
    rows = db.scalars(
        select(RelationshipAuthEvent)
        .where(RelationshipAuthEvent.owner == owner)
        .order_by(RelationshipAuthEvent.created_at.desc())
        .limit(20)
    )
    return [
        {
            "id": row.id,
            "event": row.event,
            "device_label": row.device_label,
            "created_at": row.created_at,
        }
        for row in rows
    ]
