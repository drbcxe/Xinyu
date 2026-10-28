"""Encrypted, fsynced deletion intents outside PostgreSQL for restoring old backups."""

import base64
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

from app.config import PROJECT_ROOT, get_settings

JOURNAL_AAD = b"Xinyu deletion intent v1"


def valid_target(scope, target):
    if scope in {"item", "feedback"}:
        return bool(re.fullmatch(r"[a-f0-9-]{36}", target or ""))
    if scope == "retention":
        return target in {"30", "90", "180"}
    if scope == "expiry":
        try:
            return bool(datetime.fromisoformat(target).tzinfo)
        except (ValueError, TypeError):
            return False
    return target is None


def checked_path(path):
    path = Path(path).resolve()
    if os.name == "nt" and path.drive.lower() == "c:":
        raise ValueError("Backup and recovery paths must stay off C drive")
    return path


def backup_key(create=False):
    configured = get_settings().backup_key_file
    if get_settings().app_env == "production" and not configured:
        raise ValueError("Production requires an explicit BACKUP_KEY_FILE")
    path = checked_path(
        configured or PROJECT_ROOT / ".runtime" / "backup-keys" / "master.key"
    )
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(os.urandom(32))
                stream.flush()
                os.fsync(stream.fileno())
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("Invalid backup master key")
    return key


def journal_path():
    return checked_path(PROJECT_ROOT / ".runtime" / "recovery" / "deletions.log")


def initialize_journal():
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    os.close(descriptor)
    return path


def record_deletion(db, owner, scope="account", target_id=None):
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise ValueError("Invalid deletion owner")
    if scope not in {
        "account",
        "content",
        "item",
        "feedback",
        "retention",
        "expiry",
    } or not valid_target(scope, target_id):
        raise ValueError("Invalid deletion scope")
    # Serializes writes across API processes. One complete append + fsync precedes DB commit.
    from app.services.storage_budget import lock_storage

    lock_storage(db)
    db.execute(text("SELECT pg_advisory_xact_lock(718302655)"))
    key = backup_key(create=True)
    nonce = os.urandom(12)
    payload = json.dumps(
        {
            "owner": owner,
            "scope": scope,
            "target_id": target_id,
            "at": datetime.now(UTC).isoformat(),
        },
        separators=(",", ":"),
    ).encode()
    line = (
        base64.b64encode(nonce + AESGCM(key).encrypt(nonce, payload, JOURNAL_AAD))
        + b"\n"
    )
    descriptor = os.open(initialize_journal(), os.O_APPEND | os.O_WRONLY)
    with os.fdopen(descriptor, "ab") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def read_intents(path=None, key=None):
    path = checked_path(path or journal_path())
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("Deletion journal is incomplete; recovery must stop")
    cipher = AESGCM(key or backup_key())
    intents = []
    for line in raw.splitlines():
        value = base64.b64decode(line, validate=True)
        payload = cipher.decrypt(value[:12], value[12:], JOURNAL_AAD).decode()
        if payload.startswith("{"):
            event = json.loads(payload)
        else:
            owner, stamp = payload.split(" ", 1)
            event = {"owner": owner, "at": stamp, "scope": "account", "target_id": None}
        if not re.fullmatch(r"[a-f0-9]{64}", event["owner"]) or event["scope"] not in {
            "account",
            "content",
            "item",
            "feedback",
            "retention",
            "expiry",
        }:
            raise ValueError("Invalid deletion intent")
        if not valid_target(event["scope"], event["target_id"]):
            raise ValueError("Invalid deletion target")
        if datetime.fromisoformat(event["at"]).tzinfo is None:
            raise ValueError("Deletion timestamp must include timezone")
        intents.append(event)
    return intents


def read_deletions(path=None, key=None):
    return {
        event["owner"]
        for event in read_intents(path, key)
        if event["scope"] == "account"
    }
