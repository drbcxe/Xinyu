import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.relationship import (
    RelationshipAccount,
    RelationshipLoginLimit,
    RelationshipSession,
)
from app.repositories.database import get_db

router = APIRouter(prefix="/relationship/auth", tags=["account"])
DB = Annotated[Session, Depends(get_db)]
COOKIE = "relationship_session"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def set_cookie(request: Request, response: Response, token: str):
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=30 * 24 * 3600,
        path="/api/relationship",
    )


def owner_id(request: Request, response: Response, db: DB) -> str:
    token = request.cookies.get(COOKIE, "")
    if len(token) == 64 and re.fullmatch(r"[0-9a-f]+", token):
        session = db.get(RelationshipSession, token_hash(token))
        if session and session.expires_at > datetime.now(UTC):
            return session.owner
        # Legacy anonymous spaces keep working; registered spaces require a valid session.
        account = db.scalar(
            select(RelationshipAccount).where(
                RelationshipAccount.owner == token_hash(token)
            )
        )
        if not session and not account:
            return token_hash(token)
    token = secrets.token_hex(32)
    set_cookie(request, response, token)
    return token_hash(token)


Owner = Annotated[str, Depends(owner_id)]


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=32768,
        r=8,
        p=1,
        maxmem=64 * 1024 * 1024,
    ).hex()
    return salt + ":" + digest


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")
    password: str = Field(min_length=10, max_length=128)


def issue_session(db: Session, request: Request, response: Response, owner: str):
    token = secrets.token_hex(32)
    db.add(
        RelationshipSession(
            token_hash=token_hash(token),
            owner=owner,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    db.commit()
    set_cookie(request, response, token)


def check_limit(db: Session, request: Request):
    address = request.client.host if request.client else "local"
    key = token_hash("login:" + address)
    now = datetime.now(UTC)
    db.execute(
        insert(RelationshipLoginLimit)
        .values(key=key, failures=0, reset_at=now + timedelta(minutes=15))
        .on_conflict_do_nothing()
    )
    limit = db.scalar(
        select(RelationshipLoginLimit)
        .where(RelationshipLoginLimit.key == key)
        .with_for_update()
    )
    if limit.reset_at <= now:
        limit.failures = 0
        limit.reset_at = now + timedelta(minutes=15)
    if limit.failures >= 20:
        db.rollback()
        raise AppError(429, "LOGIN_LIMIT", "尝试次数过多，请 15 分钟后重试")
    limit.failures += 1
    db.commit()


@router.get("/me")
def me(db: DB, owner: Owner):
    account = db.scalar(
        select(RelationshipAccount).where(RelationshipAccount.owner == owner)
    )
    return {"username": account.username if account else None}


@router.post("/register")
def register(
    payload: Credentials, request: Request, response: Response, db: DB, owner: Owner
):
    check_limit(db, request)
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
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "USERNAME_TAKEN", "用户名已被使用") from None
    issue_session(db, request, response, owner)
    return {"username": account.username}


@router.post("/login")
def login(payload: Credentials, request: Request, response: Response, db: DB):
    check_limit(db, request)
    account = db.get(RelationshipAccount, payload.username.lower())
    stored = account.password_hash if account else "00" * 16 + ":" + "00" * 64
    actual = hash_password(payload.password, stored.split(":")[0])
    if not account or not hmac.compare_digest(actual, stored):
        raise AppError(401, "INVALID_CREDENTIALS", "用户名或密码不正确")
    old_token = request.cookies.get(COOKIE, "")
    old_session = db.get(RelationshipSession, token_hash(old_token))
    if old_session:
        db.delete(old_session)
    issue_session(db, request, response, account.owner)
    return {"username": account.username}


@router.post("/logout")
def logout(request: Request, response: Response, db: DB):
    session = db.get(RelationshipSession, token_hash(request.cookies.get(COOKIE, "")))
    if session:
        db.delete(session)
        db.commit()
    set_cookie(request, response, secrets.token_hex(32))
    return {"ok": True}
