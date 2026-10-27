import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from qdrant_client import QdrantClient, models
from sqlalchemy.orm import Session

from app.models.relationship import (
    RelationshipEmailOutbox,
    RelationshipFeedback,
    RelationshipItem,
    RelationshipTurn,
)
from app.repositories.database import get_engine
from app.services import recovery_journal
from app.services.account_closure import close_account
from app.services.recovery_journal import backup_key, read_deletions, record_deletion
from scripts import backup
from scripts.backup_crypto import decrypt_to_file, encrypt_chunks, file_chunks
from tests.test_resource_control import owner


def private_root(backend):
    path = Path(backend.backup_key_file).parent / "fixture"
    path.mkdir(exist_ok=True)
    return path


def test_stream_encryption_detects_tampering_wrong_key_and_context(backend):
    root = private_root(backend)
    data = b"synthetic-private-data" * 100000
    key = os.urandom(32)
    expected = encrypt_chunks(
        file_chunks(io.BytesIO(data)), root / "source.enc", key, b"fixture:database"
    )
    assert b"synthetic-private-data" not in (root / "source.enc").read_bytes()
    assert (
        decrypt_to_file(root / "source.enc", root / "ok", key, b"fixture:database")
        == expected
    )
    assert (root / "ok").read_bytes() == data
    for badkey, aad in [
        (os.urandom(32), b"fixture:database"),
        (key, b"other:database"),
    ]:
        with pytest.raises(InvalidTag):
            decrypt_to_file(root / "source.enc", root / "bad", badkey, aad)
        assert not (root / "bad").exists()
    raw = bytearray((root / "source.enc").read_bytes())
    raw[100] ^= 1
    (root / "source.enc").write_bytes(raw)
    with pytest.raises(InvalidTag):
        decrypt_to_file(root / "source.enc", root / "bad", key, b"fixture:database")
    assert not (root / "bad").exists()


def test_deletion_journal_is_authenticated_and_durable_outside_db(backend):
    value = owner()
    with Session(get_engine()) as db:
        record_deletion(db, value)
        db.rollback()  # A deletion intent is conservatively retained even if DB commit fails.
    path = recovery_journal.journal_path()
    assert value.encode() not in path.read_bytes() and read_deletions() == {value}
    original = path.read_bytes()
    path.write_bytes(original[:-1])
    with pytest.raises(ValueError, match="incomplete"):
        read_deletions()
    path.write_bytes(original)
    with pytest.raises(InvalidTag):
        read_deletions(key=os.urandom(32))


def test_backup_retention_only_removes_owned_expired_complete_folders(backend):
    root = private_root(backend)
    folders = []
    for age in [1, 9, 10]:
        path = root / (
            "xinyu_backup_"
            + (datetime.now(UTC) - timedelta(days=age)).strftime("%Y%m%dT%H%M%SZ_")
            + uuid4().hex[:16]
        )
        path.mkdir()
        (path / "COMPLETE").write_text(path.name)
        folders.append(path)
    unrelated = root / "user-files"
    unrelated.mkdir()
    (unrelated / "keep").write_text("fixture")
    assert backup.prune_backups(root, days=7, keep=2) == 1
    assert folders[0].exists() and folders[1].exists() and not folders[2].exists()
    assert (unrelated / "keep").exists()


def test_restore_replays_scoped_deletes_and_preserves_post_clear_data(backend):
    value = owner()
    stamp = datetime.now(UTC)
    old_id, new_id, target_id = (str(uuid4()) for _ in range(3))
    turn_id = str(uuid4())
    with Session(get_engine()) as db:
        for item_id, age in [(old_id, -10), (new_id, 10), (target_id, 10)]:
            db.add(
                RelationshipItem(
                    id=item_id,
                    owner=value,
                    kind="memory",
                    title="synthetic",
                    quote="",
                    status="active",
                    created_at=stamp + timedelta(seconds=age),
                    revision=1,
                    details={},
                )
            )
        db.add(
            RelationshipTurn(
                id=turn_id,
                owner=value,
                request_id=str(uuid4()),
                content="synthetic",
                result={},
                created_at=stamp + timedelta(seconds=5),
            )
        )
        db.add(
            RelationshipFeedback(
                id=str(uuid4()),
                owner=value,
                turn_id=turn_id,
                rating="helpful",
                category="general",
                comment="",
                created_at=stamp + timedelta(seconds=6),
                updated_at=stamp + timedelta(seconds=6),
            )
        )
        db.commit()
    events = [
        {
            "owner": value,
            "scope": "content",
            "target_id": None,
            "at": stamp.isoformat(),
        },
        {
            "owner": value,
            "scope": "item",
            "target_id": target_id,
            "at": (stamp + timedelta(seconds=20)).isoformat(),
        },
        {
            "owner": value,
            "scope": "feedback",
            "target_id": turn_id,
            "at": (stamp + timedelta(seconds=20)).isoformat(),
        },
    ]
    with backup.connect() as conn:
        accounts, vector_owners, items = backup.apply_intents(conn, events)
        assert (
            not accounts
            and vector_owners == {value}
            and items == [{"owner": value, "source_id": target_id}]
        )
        assert conn.execute(
            "SELECT id FROM public.relationship_items WHERE owner=%s", (value,)
        ).fetchall() == [(new_id,)]
        assert (
            conn.execute(
                "SELECT count(*) FROM public.relationship_feedback WHERE owner=%s",
                (value,),
            ).fetchone()[0]
            == 0
        )
        assert conn.execute(
            "SELECT item_id,status FROM public.relationship_rag_indexes WHERE owner=%s",
            (value,),
        ).fetchall() == [(new_id, "pending")]


def test_extending_retention_cannot_resurrect_already_expired_content(backend):
    from tests.test_privacy_retention import seed_retention

    value = owner()
    stamp = datetime.now(UTC)
    turn_id, memory_id, import_id = seed_retention(value, 60, stamp)
    events = [
        {
            "owner": value,
            "scope": "expiry",
            "target_id": (stamp - timedelta(days=30)).isoformat(),
            "at": stamp.isoformat(),
        },
        {
            "owner": value,
            "scope": "retention",
            "target_id": "180",
            "at": stamp.isoformat(),
        },
    ]
    with backup.connect() as conn:
        backup.apply_intents(conn, events)
        removed = backup.expire_restored_content(conn, events)
        assert removed == [{"owner": value, "source_id": import_id}]
        assert (
            conn.execute(
                "SELECT id FROM public.relationship_turns WHERE id=%s", (turn_id,)
            ).fetchone()
            is None
        )
        assert conn.execute(
            "SELECT id FROM public.relationship_items WHERE id=%s", (memory_id,)
        ).fetchone()
        assert (
            conn.execute(
                "SELECT history_days FROM public.relationship_privacy WHERE owner=%s",
                (value,),
            ).fetchone()[0]
            == 180
        )


def test_actual_postgres_and_qdrant_backup_restore_and_post_backup_deletion(
    backend, monkeypatch
):
    root = private_root(backend)
    container = backup.pg_container()
    monkeypatch.setenv("XINYU_PG_CONTAINER", container)
    monkeypatch.setattr(backup, "PROJECT_ROOT", root)
    secrets = root / ".runtime" / "secrets"
    secrets.mkdir(parents=True)
    email_key = Fernet.generate_key()
    (secrets / "email.key").write_bytes(email_key)
    (root / ".env").write_text("MODEL_API_KEY=synthetic-backup-key\n", encoding="utf8")
    removed, preserved = owner(), owner()
    from app.services import relationship_agent as agent
    from tests.test_agent_output_repair import no_retrieval, response

    monkeypatch.setattr(agent, "complete", lambda *_, **__: response(1))
    monkeypatch.setattr(agent, "retrieve", no_retrieval)
    with Session(get_engine()) as db:
        agent.run_agent(db, preserved, "synthetic checkpoint", request_id=str(uuid4()))
    collection = "xinyu_eval_" + uuid4().hex[:16]
    vector = QdrantClient(url=backend.qdrant_url, trust_env=False)
    vector.create_collection(
        collection,
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )
    try:
        point_ids = []
        with Session(get_engine()) as db:
            for value in [removed, preserved]:
                item_id = str(uuid4())
                point_ids.append(item_id)
                db.add(
                    RelationshipItem(
                        id=item_id,
                        owner=value,
                        kind="memory",
                        title="synthetic fixture",
                        quote="synthetic evidence",
                        status="active",
                        created_at=datetime.now(UTC),
                        revision=1,
                        details={},
                    )
                )
            db.add(
                RelationshipEmailOutbox(
                    id=str(uuid4()),
                    owner=removed,
                    status="failed",
                    encrypted_payload=Fernet(email_key)
                    .encrypt(b"synthetic secret mail")
                    .decode(),
                    attempts=0,
                    available_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                    error="",
                )
            )
            db.commit()
        vector.upsert(
            collection,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=[1.0, 0.0],
                    payload={"owner": value, "source_id": point_id},
                )
                for point_id, value in zip(point_ids, [removed, preserved], strict=True)
            ],
            wait=True,
        )
        folder = backup.create_backup(root / "bundles", collections=[collection])
        assert (folder / "COMPLETE").exists() and backup_key()
        with Session(get_engine()) as db:
            close_account(db, removed)
        result = backup.drill(folder)
        assert result["database_verified"] and result["isolated_resources_cleaned"]
        assert result["graph_rows_verified"] > 0 and result["tables_verified"] > 10
        assert (
            result["deleted_owners_suppressed"] == 1
            and result["encrypted_records_verified"] == 1
        )
        assert result["vectors"]["vector-0"]["restored"]["points"] == 2
        assert result["vectors"]["vector-0"]["after_deletions"]["points"] == 1
        assert (
            vector.count(collection).count == 2
        )  # The live collection was never restored/deleted.
        with backup.connect() as conn:
            assert not conn.execute(
                "SELECT datname FROM pg_database WHERE datname LIKE 'xinyu_restore_%'"
            ).fetchall()
        assert not [
            c
            for c in vector.get_collections().collections
            if c.name.startswith("xinyu_restore_")
        ]
        assert not list((root / ".runtime" / "restore-tmp").iterdir())
        encrypted = bytearray((folder / "database.enc").read_bytes())
        encrypted[50] ^= 1
        (folder / "database.enc").write_bytes(encrypted)
        with pytest.raises(InvalidTag):
            backup.drill(folder)
    finally:
        vector.delete_collection(collection)
        vector.close()
