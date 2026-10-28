import json
import smtplib
import socketserver
import threading
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import app
from app.models.relationship import (
    RelationshipAccount,
    RelationshipEmailChallenge,
    RelationshipEmailOutbox,
)
from app.repositories.database import get_engine
from app.services import account_email, auth_cache

PASSWORD = "Synthetic account passphrase 736"
NEW_PASSWORD = "Another secure phrase 456 for tests"


@pytest.fixture
def mailbox(backend, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_SECURITY", "plain")
    monkeypatch.setenv("SMTP_FROM", "noreply@example.test")
    monkeypatch.setenv("SMTP_USERNAME", "")
    get_settings.cache_clear()
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(account_email, "mail_cipher", lambda: cipher)
    monkeypatch.setattr(
        auth_cache, "captcha_secret", lambda: b"synthetic-fixture-secret-32bytes!!"
    )
    monkeypatch.setattr(auth_cache.secrets, "choice", lambda _: "A")
    monkeypatch.setattr(
        auth_cache, "render_captcha", lambda _: "data:image/png;base64,synthetic"
    )
    messages = []
    monkeypatch.setattr(
        account_email,
        "deliver_email",
        lambda payload, message_id: messages.append({**payload, "id": message_id}),
    )
    with TestClient(app) as client:
        yield client, messages, cipher


def captcha(client):
    result = client.get("/api/relationship/auth/captcha")
    assert result.status_code == 200
    return {"captcha_id": result.json()["challenge_id"], "captcha_code": "AAAAA"}


def register(client, username="email_fixture"):
    result = client.post(
        "/api/relationship/auth/register",
        json={
            **captcha(client),
            "username": username,
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )
    assert result.status_code == 200, result.text


def send_bind(client, email="fixture@example.test"):
    return client.post(
        "/api/relationship/auth/email/send",
        json={**captcha(client), "email": email, "current_password": PASSWORD},
    )


def bind(client, messages):
    result = send_bind(client)
    assert result.status_code == 202, result.text
    assert account_email.process_email()
    response = client.post(
        "/api/relationship/auth/email/verify",
        json={
            "challenge_id": result.json()["challenge_id"],
            "code": messages[-1]["code"],
        },
    )
    assert response.status_code == 200, response.text
    return result.json()["challenge_id"], messages[-1]["code"]


def send_recovery(client, email="fixture@example.test", username="email_fixture"):
    # Binding and recovery share a recipient cooldown, deliberately reset only in the isolated test cache.
    cache = auth_cache.get_redis()
    cache.delete(auth_cache.PREFIX + "rate:mail:cooldown:" + auth_cache.digest(email))
    return client.post(
        "/api/relationship/auth/recovery/send",
        json={**captcha(client), "username": username, "email": email},
    )


def verify_reset(client, challenge_id, code):
    return client.post(
        "/api/relationship/auth/recovery/verify",
        json={
            "challenge_id": challenge_id,
            "code": code,
            "username": "email_fixture",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )


def test_email_binding_outbox_encrypted_single_use_and_masked(mailbox):
    client, messages, cipher = mailbox
    register(client)
    result = send_bind(client)
    assert result.status_code == 202
    challenge_id = result.json()["challenge_id"]
    with Session(get_engine()) as db:
        row = db.get(RelationshipEmailOutbox, challenge_id)
        assert "fixture@example.test" not in row.encrypted_payload
        encrypted_payload = json.loads(cipher.decrypt(row.encrypted_payload.encode()))
        assert encrypted_payload["email"] == "fixture@example.test"
        assert db.get(RelationshipAccount, "email_fixture").email is None
    assert account_email.process_email()
    code = messages[-1]["code"]
    assert (
        client.get(f"/api/relationship/auth/email/delivery/{challenge_id}").json()[
            "status"
        ]
        == "sent"
    )
    assert (
        client.post(
            "/api/relationship/auth/email/verify",
            json={"challenge_id": challenge_id, "code": code},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/relationship/auth/email/verify",
            json={"challenge_id": challenge_id, "code": code},
        ).status_code
        == 422
    )
    status = client.get("/api/relationship/auth/email").json()
    assert status["verified"] and status["email"] == "f***@example.test"
    with Session(get_engine()) as db:
        assert db.get(RelationshipEmailOutbox, challenge_id).encrypted_payload == ""
        assert db.get(RelationshipEmailChallenge, challenge_id).answer_hash != code


def test_password_recovery_revokes_warmed_sessions_and_requires_login(mailbox):
    client, messages, _ = mailbox
    register(client)
    bind(client, messages)
    assert client.get("/api/relationship/auth/me").json()["username"] == "email_fixture"
    reset = send_recovery(client)
    assert reset.status_code == 202
    account_email.process_email()
    result = verify_reset(client, reset.json()["challenge_id"], messages[-1]["code"])
    assert result.status_code == 200, result.text
    assert client.get("/api/relationship/auth/me").json()["username"] is None
    assert (
        verify_reset(
            client, reset.json()["challenge_id"], messages[-1]["code"]
        ).status_code
        == 422
    )
    login = client.post(
        "/api/relationship/auth/login",
        json={**captcha(client), "username": "email_fixture", "password": NEW_PASSWORD},
    )
    assert login.status_code == 200


def test_recovery_unknown_and_unverified_accounts_have_identical_shape(mailbox):
    client, messages, _ = mailbox
    register(client)
    unknown = send_recovery(client, "unknown@example.test", "unknown_fixture")
    unverified = send_recovery(client)
    assert unknown.status_code == unverified.status_code == 202
    assert set(unknown.json()) == set(unverified.json())
    assert unknown.json()["message"] == unverified.json()["message"]
    assert not account_email.process_email() and not messages


@pytest.mark.parametrize(
    "invalid", ["attempts", "expired", "password_changed", "wrong_browser"]
)
def test_recovery_challenge_limits(mailbox, invalid):
    client, messages, _ = mailbox
    register(client)
    bind(client, messages)
    reset = send_recovery(client).json()
    account_email.process_email()
    code = messages[-1]["code"]
    if invalid == "attempts":
        wrong = "111111" if code != "111111" else "222222"
        for _ in range(5):
            assert verify_reset(client, reset["challenge_id"], wrong).status_code == 422
    elif invalid == "wrong_browser":
        with TestClient(app) as other:
            assert verify_reset(other, reset["challenge_id"], code).status_code == 422
        # A request from another browser must not consume the legitimate challenge.
        assert verify_reset(client, reset["challenge_id"], code).status_code == 200
        return
    else:
        with Session(get_engine()) as db:
            if invalid == "expired":
                db.get(RelationshipEmailChallenge, reset["challenge_id"]).expires_at = (
                    datetime.now(UTC) - timedelta(seconds=1)
                )
            else:
                db.get(
                    RelationshipAccount, "email_fixture"
                ).password_hash = "changed-hash-fixture"
            db.commit()
    assert verify_reset(client, reset["challenge_id"], code).status_code == 422


def test_binding_requires_current_password_and_rate_cannot_use_guest_rotation(mailbox):
    client, _, _ = mailbox
    register(client)
    wrong = client.post(
        "/api/relationship/auth/email/send",
        json={
            **captcha(client),
            "email": "fixture@example.test",
            "current_password": "incorrect",
        },
    )
    assert wrong.status_code == 401
    assert send_bind(client).status_code == 202
    assert send_bind(client).status_code == 429
    with TestClient(app) as other:
        result = other.post(
            "/api/relationship/auth/recovery/send",
            json={
                **captcha(other),
                "email": "fixture@example.test",
                "username": "email_fixture",
            },
        )
        assert result.status_code == 429


def test_expired_mail_is_not_sent_and_transient_smtp_failure_retries(
    mailbox, monkeypatch
):
    client, messages, _ = mailbox
    register(client)
    sent = send_bind(client).json()

    def fail(*_):
        raise smtplib.SMTPServerDisconnected("synthetic secret must never be exposed")

    monkeypatch.setattr(account_email, "deliver_email", fail)
    assert account_email.process_email()
    with Session(get_engine()) as db:
        row = db.get(RelationshipEmailOutbox, sent["challenge_id"])
        assert row.status == "pending" and row.error == "EMAIL_DELIVERY_FAILED"
        assert "secret" not in row.error
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
        db.get(RelationshipEmailChallenge, row.id).expires_at = datetime.now(
            UTC
        ) - timedelta(seconds=1)
        db.commit()
    assert account_email.process_email() and not messages
    with Session(get_engine()) as db:
        row = db.get(RelationshipEmailOutbox, sent["challenge_id"])
        assert row.status == "expired" and not row.encrypted_payload


def test_smtp_plain_transport_against_isolated_loopback_server(backend, monkeypatch):
    captured = []

    class SMTPHandler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"220 fixture ESMTP\r\n")
            while line := self.rfile.readline():
                command = line.decode().strip().upper()
                if command.startswith("EHLO"):
                    self.wfile.write(b"250-fixture\r\n250 SIZE 100000\r\n")
                elif command == "DATA":
                    self.wfile.write(b"354 send data\r\n")
                    body = []
                    while (chunk := self.rfile.readline()) != b".\r\n":
                        if not chunk:
                            return
                        body.append(chunk)
                    captured.append(b"".join(body))
                    self.wfile.write(b"250 queued\r\n")
                elif command == "QUIT":
                    self.wfile.write(b"221 bye\r\n")
                    return
                else:
                    self.wfile.write(b"250 OK\r\n")

    with socketserver.TCPServer(("127.0.0.1", 0), SMTPHandler) as server:
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(server.server_address[1]))
        monkeypatch.setenv("SMTP_SECURITY", "plain")
        monkeypatch.setenv("SMTP_USERNAME", "")
        monkeypatch.setenv("SMTP_FROM", "noreply@example.test")
        get_settings.cache_clear()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            account_email.deliver_email(
                {"email": "fixture@example.test", "purpose": "reset", "code": "123456"},
                "fixture-id",
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
    mail = BytesParser(policy=policy.default).parsebytes(captured[0])
    assert (
        mail["To"] == "fixture@example.test"
        and "123456" in mail.get_body().get_content()
    )
    assert "重置密码" in mail["Subject"]


@pytest.mark.parametrize(
    "address",
    ["a\r\nBcc: victim@example.com", "a@b", "a..b@example.com", "Name <a@example.com>"],
)
def test_email_header_injection_and_invalid_addresses_rejected(address):
    with pytest.raises(ValueError):
        account_email.normalize_email(address)


def test_remote_plain_smtp_is_rejected(backend, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "noreply@example.test")
    monkeypatch.setenv("SMTP_SECURITY", "plain")
    get_settings.cache_clear()
    assert not account_email.smtp_configured()
