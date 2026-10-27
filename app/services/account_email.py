"""One-time mail challenges and a durable, encrypted SMTP delivery queue."""

import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from functools import lru_cache
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, get_settings
from app.errors import AppError
from app.models.relationship import (
    RelationshipAccount,
    RelationshipEmailChallenge,
    RelationshipEmailOutbox,
)
from app.repositories.database import get_engine
from app.services.auth_cache import answer_digest, consume_rate, digest

TTL = 600
logger = logging.getLogger(__name__)


def normalize_email(value: str) -> str:
    value = value.strip()
    if len(value) > 254 or value.count("@") != 1:
        raise ValueError("请输入有效的邮箱地址")
    local, domain = value.rsplit("@", 1)
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("请输入有效的邮箱地址") from None
    if (
        not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.\-]{1,64}", local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+",
            domain,
        )
    ):
        raise ValueError("请输入有效的邮箱地址")
    normalized = local.lower() + "@" + domain
    if len(normalized) > 254:
        raise ValueError("邮箱地址过长")
    return normalized


def smtp_configured() -> bool:
    settings = get_settings()
    if not settings.smtp_host.strip() or settings.smtp_security not in {
        "starttls",
        "ssl",
        "plain",
    }:
        return False
    if settings.smtp_security == "plain" and settings.smtp_host not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        return False
    try:
        normalize_email(settings.smtp_from)
    except ValueError:
        return False
    return not settings.smtp_username or bool(settings.smtp_password.get_secret_value())


def require_smtp():
    if not smtp_configured():
        raise AppError(
            503,
            "EMAIL_NOT_CONFIGURED",
            "邮件服务尚未配置，请在本地 .env 填写 SMTP 后重启服务",
        )


def masked_email(value: str | None) -> str | None:
    if not value:
        return None
    local, domain = value.rsplit("@", 1)
    return local[:1] + "***@" + domain


@lru_cache
def mail_cipher() -> Fernet:
    folder = PROJECT_ROOT / ".runtime" / "secrets"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "email.key"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(Fernet.generate_key())
    try:
        return Fernet(path.read_bytes())
    except ValueError:
        raise AppError(
            503, "EMAIL_KEY_UNAVAILABLE", "邮件加密服务正在准备，请稍后重试"
        ) from None


def mail_rate(ip: str, email: str):
    consume_rate("mail:global", 100, 3600, "邮件发送繁忙，请稍后重试")
    consume_rate("mail:ip:" + digest(ip), 10, 3600, "本设备邮件请求过多，请稍后重试")
    consume_rate(
        "mail:recipient:" + digest(email), 5, 3600, "此邮箱请求过多，请稍后重试"
    )
    consume_rate("mail:cooldown:" + digest(email), 1, 60, "请等待一分钟再申请验证码")


def invalidate_challenges(db: Session, owner: str, purpose: str | None = None):
    now = datetime.now(UTC)
    query = update(RelationshipEmailChallenge).where(
        RelationshipEmailChallenge.owner == owner,
        RelationshipEmailChallenge.used_at.is_(None),
    )
    if purpose:
        query = query.where(RelationshipEmailChallenge.purpose == purpose)
    db.execute(query.values(used_at=now))


def queue_challenge(
    db: Session, account: RelationshipAccount, binding: str, purpose: str, email: str
) -> str:
    # Account lock precedes this global admission lock. Network I/O is performed by the worker later.
    db.execute(text("SELECT pg_advisory_xact_lock(718302613)"))
    pending = db.scalar(
        select(func.count())
        .select_from(RelationshipEmailOutbox)
        .where(RelationshipEmailOutbox.status.in_(["pending", "sending"]))
    )
    if pending >= 100:
        raise AppError(503, "EMAIL_QUEUE_FULL", "邮件发送队列繁忙，请稍后重试")
    invalidate_challenges(db, account.owner, purpose)
    challenge_id, code, now = (
        str(uuid4()),
        f"{secrets.randbelow(1000000):06d}",
        datetime.now(UTC),
    )
    db.add(
        RelationshipEmailChallenge(
            id=challenge_id,
            owner=account.owner,
            binding=binding,
            purpose=purpose,
            email=email,
            answer_hash=answer_digest(challenge_id, binding, code),
            password_version=digest(account.password_hash),
            attempts=0,
            created_at=now,
            expires_at=now + timedelta(seconds=TTL),
        )
    )
    encrypted = (
        mail_cipher()
        .encrypt(
            json.dumps({"email": email, "code": code, "purpose": purpose}).encode()
        )
        .decode()
    )
    db.add(
        RelationshipEmailOutbox(
            id=challenge_id,
            owner=account.owner,
            status="pending",
            encrypted_payload=encrypted,
            attempts=0,
            available_at=now,
            created_at=now,
            error="",
        )
    )
    return challenge_id


def verify_challenge(
    db: Session,
    account: RelationshipAccount | None,
    challenge_id: str,
    binding: str,
    purpose: str,
    code: str,
) -> RelationshipEmailChallenge:
    challenge = db.scalar(
        select(RelationshipEmailChallenge)
        .where(RelationshipEmailChallenge.id == challenge_id)
        .with_for_update()
    )
    valid = bool(
        challenge
        and account
        and challenge.owner == account.owner
        and challenge.binding == binding
        and challenge.purpose == purpose
        and not challenge.used_at
        and challenge.expires_at > datetime.now(UTC)
        and challenge.attempts < 5
        and challenge.password_version == digest(account.password_hash)
    )
    if valid:
        challenge.attempts += 1
        if hmac.compare_digest(
            challenge.answer_hash, answer_digest(challenge.id, binding, code)
        ):
            challenge.used_at = datetime.now(UTC)
            return challenge
    # Commit the failed attempt even though the endpoint returns an error. No account mutations precede verification.
    db.commit()
    raise AppError(
        422, "EMAIL_CODE_INVALID", "邮箱验证码无效、已过期或已使用，请重新申请"
    )


def deliver_email(payload: dict, message_id: str):
    require_smtp()
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = payload["email"]
    message["Subject"] = "心语 · " + (
        "验证找回邮箱" if payload["purpose"] == "bind" else "重置密码验证码"
    )
    message["Message-ID"] = f"<{message_id}@xinyu.local>"
    message.set_content(
        f"你的心语验证码为：{payload['code']}\n\n验证码自申请起 10 分钟内有效，只能使用一次。请在申请的浏览器中填写。\n如果不是你本人操作，请忽略此邮件，不要向他人提供验证码。"
    )
    context = ssl.create_default_context()
    factory = smtplib.SMTP_SSL if settings.smtp_security == "ssl" else smtplib.SMTP
    options = {"context": context} if settings.smtp_security == "ssl" else {}
    with factory(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
        **options,
    ) as smtp:
        smtp.ehlo()
        if settings.smtp_security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if settings.smtp_username:
            smtp.login(
                settings.smtp_username, settings.smtp_password.get_secret_value()
            )
        smtp.send_message(message)


def process_email() -> bool:
    if not smtp_configured():
        return False
    now, token = datetime.now(UTC), str(uuid4())
    with Session(get_engine()) as db:
        db.execute(
            update(RelationshipEmailOutbox)
            .where(
                RelationshipEmailOutbox.status == "sending",
                RelationshipEmailOutbox.lease_expires_at <= now,
            )
            .values(status="pending", lease_token=None, lease_expires_at=None)
        )
        row = db.scalar(
            select(RelationshipEmailOutbox)
            .where(
                RelationshipEmailOutbox.status == "pending",
                RelationshipEmailOutbox.available_at <= now,
            )
            .order_by(RelationshipEmailOutbox.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not row:
            db.commit()
            return False
        challenge = db.get(RelationshipEmailChallenge, row.id)
        if (
            not challenge
            or challenge.used_at
            or challenge.expires_at <= now
            or row.attempts >= 3
        ):
            row.status, row.encrypted_payload = "expired", ""
            db.commit()
            return True
        row.status, row.lease_token = "sending", token
        row.lease_expires_at = now + timedelta(seconds=180)
        row.attempts += 1
        entry_id, encrypted, attempts = row.id, row.encrypted_payload, row.attempts
        db.commit()
    error, permanent = "", False
    try:
        payload = json.loads(mail_cipher().decrypt(encrypted.encode()))
        deliver_email(payload, entry_id)
    except (InvalidToken, ValueError):
        error, permanent = "EMAIL_PAYLOAD_INVALID", True
    except (smtplib.SMTPException, OSError) as exc:
        error = "EMAIL_DELIVERY_FAILED"
        permanent = (
            isinstance(exc, smtplib.SMTPResponseException)
            and 500 <= exc.smtp_code < 600
        )
        # SMTP exceptions can contain addresses/provider secrets. Never write their text to logs or API responses.
        logger.warning(
            "email_delivery_failed",
            extra={"mail_id": entry_id, "error_type": type(exc).__name__},
        )
    with Session(get_engine()) as db:
        row = db.scalar(
            select(RelationshipEmailOutbox)
            .where(
                RelationshipEmailOutbox.id == entry_id,
                RelationshipEmailOutbox.lease_token == token,
            )
            .with_for_update()
        )
        if row:
            row.status = (
                "sent"
                if not error
                else "failed"
                if permanent or attempts >= 3
                else "pending"
            )
            row.error, row.lease_token, row.lease_expires_at = error, None, None
            row.available_at = datetime.now(UTC) + timedelta(seconds=5 * 2**attempts)
            if row.status in {"sent", "failed"}:
                row.encrypted_payload = ""
            db.commit()
    return True
