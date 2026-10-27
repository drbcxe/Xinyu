"""Encrypted local backup and fresh-namespace restore drill. No in-place restore."""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from cryptography.fernet import Fernet
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import PROJECT_ROOT, get_settings
from app.services import recovery_journal
from app.services.recovery_journal import (
    backup_key,
    checked_path,
    initialize_journal,
    read_intents,
)
from scripts.backup_crypto import decrypt_to_file, encrypt_chunks, file_chunks

BACKUP_NAME = re.compile(r"xinyu_backup_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{16}")
SOURCE_COLLECTION = re.compile(r"xinyu_[a-f0-9]{16}")
KEY_FILES = {"auth-captcha.key", "email.key", "weixin.key", "ops.key"}


def docker():
    value = shutil.which("docker") or "D:/docker_desktop/resources/bin/docker.exe"
    if not Path(value).is_file():
        raise ValueError("Docker CLI not available")
    return value


def connection_url(database=None):
    value = make_url(get_settings().database_url).set(drivername="postgresql")
    return value.set(database=database) if database else value


def connect(database=None, autocommit=False):
    return psycopg.connect(
        connection_url(database).render_as_string(hide_password=False),
        connect_timeout=5,
        autocommit=autocommit,
        options="-c timezone=UTC -c statement_timeout=120000 -c lock_timeout=5000",
    )


def pg_container():
    value = os.environ.get("XINYU_PG_CONTAINER")
    if not value:
        result = subprocess.run(
            [docker(), "compose", "ps", "-q", "postgres"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            timeout=20,
            check=True,
        )
        value = result.stdout.decode().strip()
    if not re.fullmatch(r"[a-f0-9]{12,64}", value or ""):
        raise ValueError("An exact PostgreSQL container ID is required")
    with connect() as conn:
        expected = str(
            conn.execute(
                "SELECT system_identifier FROM pg_control_system()"
            ).fetchone()[0]
        )
    actual = (
        subprocess.run(
            [
                docker(),
                "exec",
                value,
                "psql",
                "-U",
                connection_url().username,
                "-d",
                connection_url().database,
                "-Atc",
                "SELECT system_identifier FROM pg_control_system()",
            ],
            capture_output=True,
            timeout=20,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    if actual != expected:
        raise ValueError(
            "PostgreSQL connection and backup container are different clusters"
        )
    return value


@contextmanager
def pg_process(container, program, database, arguments):
    url = connection_url(database)
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    command = [
        docker(),
        "exec",
        "-i",
        "--env",
        "PGPASSWORD",
        container,
        program,
        "--username",
        url.username,
        "--dbname",
        url.database,
        *arguments,
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    # Drain diagnostics without logging text, which may contain private identifiers.
    def drain_errors():
        while process.stderr.read(16384):
            pass

    drainer = threading.Thread(target=drain_errors, daemon=True)
    drainer.start()
    timer = threading.Timer(300, process.kill)
    timer.start()
    try:
        yield process
        process.wait(timeout=10)
        if process.returncode:
            raise RuntimeError("PostgreSQL backup/restore command failed")
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        drainer.join(timeout=2)


def fingerprints(conn):
    tables = conn.execute(
        "SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('public','xinyu_graph') ORDER BY schemaname,tablename"
    ).fetchall()
    result = {}
    for schema, table in tables:
        if (
            schema == "public"
            and table != "alembic_version"
            and not table.startswith("relationship_")
        ):
            raise ValueError("Unexpected public table; refusing unrelated data")
        digest, count = hashlib.sha256(), 0
        with conn.cursor(name="fingerprint_" + uuid4().hex) as cursor:
            cursor.execute(
                sql.SQL(
                    "SELECT md5(to_jsonb(t)::text) AS h FROM {}.{} t ORDER BY h"
                ).format(sql.Identifier(schema), sql.Identifier(table))
            )
            for row in cursor:
                digest.update(row[0].encode())
                count += 1
        result[schema + "." + table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def vector_fingerprint(client, collection):
    hashes, offset = [], None
    while True:
        payload = {"limit": 256, "with_payload": True, "with_vector": True}
        if offset is not None:
            payload["offset"] = offset
        response = client.post(f"/collections/{collection}/points/scroll", json=payload)
        response.raise_for_status()
        value = response.json()["result"]
        for row in value["points"]:
            hashes.append(
                hashlib.sha256(
                    json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
            )
        if len(hashes) > 2000000:
            raise ValueError("Vector fingerprint exceeds local drill boundary")
        offset = value["next_page_offset"]
        if offset is None:
            break
    return {
        "points": len(hashes),
        "sha256": hashlib.sha256("".join(sorted(hashes)).encode()).hexdigest(),
    }


def vector_client():
    return httpx.Client(
        base_url=get_settings().qdrant_url.rstrip("/"), trust_env=False, timeout=60
    )


def create_backup(root=None, collections=None):
    key = backup_key(create=True)
    journal = initialize_journal()
    container = pg_container()
    root = checked_path(root or PROJECT_ROOT / ".runtime" / "backups")
    root.mkdir(parents=True, exist_ok=True)
    run_id = (
        "xinyu_backup_"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ_")
        + uuid4().hex[:16]
    )
    folder = root / run_id
    folder.mkdir(mode=0o700)
    manifest = {
        "version": 1,
        "id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "parts": {},
        "vectors": {},
        "source_database": connection_url().database,
    }

    def add(name, chunks):
        manifest["parts"][name] = encrypt_chunks(
            chunks, folder / (name + ".enc"), key, (run_id + ":" + name).encode()
        )

    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
        manifest["database_fingerprints"] = fingerprints(conn)
        with pg_process(
            container,
            "pg_dump",
            connection_url().database,
            [
                "--format=custom",
                "--schema=public",
                "--schema=xinyu_graph",
                "--no-owner",
                "--no-privileges",
                "--snapshot=" + snapshot,
            ],
        ) as process:
            process.stdin.close()
            add("database", file_chunks(process.stdout))
    secrets = {
        path.name: base64.b64encode(path.read_bytes()).decode()
        for path in (PROJECT_ROOT / ".runtime" / "secrets").glob("*.key")
        if path.name in KEY_FILES
    }
    config = PROJECT_ROOT / ".env"
    add(
        "private-config",
        [
            json.dumps(
                {
                    "keys": secrets,
                    "env": base64.b64encode(config.read_bytes()).decode()
                    if config.is_file()
                    else "",
                }
            ).encode()
        ],
    )
    add("deletions", [journal.read_bytes()])
    with vector_client() as client:
        available = [
            row["name"]
            for row in client.get("/collections").json()["result"]["collections"]
        ]
        selected = (
            [name for name in available if SOURCE_COLLECTION.fullmatch(name)]
            if collections is None
            else collections
        )
        if any(
            name not in available
            or not (
                SOURCE_COLLECTION.fullmatch(name)
                or (
                    get_settings().app_env in {"test", "evaluation"}
                    and re.fullmatch(r"xinyu_eval_[a-f0-9]{16}", name)
                )
            )
            for name in selected
        ):
            raise ValueError("Invalid backup collection selection")
        for i, name in enumerate(selected):
            response = client.post(f"/collections/{name}/snapshots")
            response.raise_for_status()
            snapshot_name = response.json()["result"]["name"]
            path = f"/collections/{name}/snapshots/{snapshot_name}"
            try:
                # Fingerprint the snapshot copy during the drill; live indexes may change concurrently.
                part = f"vector-{i}"
                with client.stream("GET", path) as response:
                    response.raise_for_status()
                    add(part, response.iter_bytes(chunk_size=262144))
                with private_temp() as temporary:
                    decrypt_to_file(
                        folder / (part + ".enc"),
                        temporary / part,
                        key,
                        (run_id + ":" + part).encode(),
                    )
                    target = "xinyu_restore_" + uuid4().hex[:16]
                    if client.get(f"/collections/{target}").status_code != 404:
                        raise ValueError("Snapshot verification target exists")
                    try:
                        upload_snapshot(client, target, temporary / part)
                        manifest["vectors"][part] = {
                            "source_collection": name,
                            "fingerprint": vector_fingerprint(client, target),
                        }
                    finally:
                        delete_restore_collection(target)
            finally:
                client.delete(path).raise_for_status()
    encrypt_chunks(
        [json.dumps(manifest, sort_keys=True).encode()],
        folder / "manifest.enc",
        key,
        (run_id + ":manifest").encode(),
    )
    (folder / "COMPLETE").write_text(run_id, encoding="ascii")
    return folder


@contextmanager
def private_temp():
    root = checked_path(PROJECT_ROOT / ".runtime" / "restore-tmp")
    root.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="restore-", dir=root)
    path = Path(temporary.name).resolve()
    assert path.is_relative_to(root) and path.name.startswith("restore-")
    try:
        yield path
    finally:
        temporary.cleanup()


def suppress_deleted(conn, owners):
    if not owners:
        return 0
    for schema, table in conn.execute(
        "SELECT table_schema, table_name FROM information_schema.columns WHERE column_name='owner' AND table_schema='public' AND table_name LIKE 'relationship_%'"
    ).fetchall():
        if table == "relationship_closed_spaces":
            continue
        conn.execute(
            sql.SQL("DELETE FROM {}.{} WHERE owner = ANY(%s)").format(
                sql.Identifier(schema), sql.Identifier(table)
            ),
            (list(owners),),
        )
    for owner in owners:
        conn.execute(
            "INSERT INTO public.relationship_closed_spaces(owner,receipt_hash,closed_at,cleanup_at) VALUES(%s,%s,now(),NULL) ON CONFLICT(owner) DO NOTHING",
            (owner, hashlib.sha256((owner + ":restored").encode()).hexdigest()),
        )
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            conn.execute(
                sql.SQL("DELETE FROM xinyu_graph.{} WHERE thread_id LIKE %s").format(
                    sql.Identifier(table)
                ),
                ("xinyu:" + owner + ":%",),
            )
    return len(owners)


def clear_owner_graph(conn, owner):
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        conn.execute(
            sql.SQL("DELETE FROM xinyu_graph.{} WHERE thread_id LIKE %s").format(
                sql.Identifier(table)
            ),
            ("xinyu:" + owner + ":%",),
        )


def apply_intents(conn, events):
    events = list(
        {json.dumps(event, sort_keys=True): event for event in events}.values()
    )
    accounts = {event["owner"] for event in events if event["scope"] == "account"}
    suppress_deleted(conn, accounts)
    vector_owners = set(accounts)
    vector_items = []
    for event in sorted(events, key=lambda event: event["at"]):
        owner, scope = event["owner"], event["scope"]
        if owner in accounts:
            continue
        if scope == "content":
            vector_owners.add(owner)
            for table in [
                "relationship_feedback",
                "relationship_notices",
                "relationship_jobs",
                "relationship_items",
                "relationship_turns",
            ]:
                conn.execute(
                    sql.SQL(
                        "DELETE FROM public.{} WHERE owner=%s AND created_at <= %s::timestamptz"
                    ).format(sql.Identifier(table)),
                    (owner, event["at"]),
                )
            clear_owner_graph(conn, owner)
        elif scope == "item":
            item_id = event["target_id"]
            vector_items.append({"owner": owner, "source_id": item_id})
            conn.execute(
                "DELETE FROM public.relationship_items WHERE owner=%s AND id=%s",
                (owner, item_id),
            )
            conn.execute(
                "DELETE FROM public.relationship_notices WHERE owner=%s AND item_id=%s",
                (owner, item_id),
            )
            conn.execute(
                "DELETE FROM public.relationship_rag_indexes WHERE owner=%s AND item_id=%s",
                (owner, item_id),
            )
        elif scope == "feedback":
            conn.execute(
                "DELETE FROM public.relationship_feedback WHERE owner=%s AND turn_id=%s AND updated_at<=%s::timestamptz",
                (owner, event["target_id"], event["at"]),
            )
        elif scope == "retention":
            conn.execute(
                "UPDATE public.relationship_privacy SET history_days=%s WHERE owner=%s",
                (int(event["target_id"]), owner),
            )
    # Broad clears discard vectors; retained post-clear documents must be reindexed rather than lost.
    for owner in vector_owners - accounts:
        conn.execute(
            "DELETE FROM public.relationship_rag_indexes WHERE owner=%s", (owner,)
        )
        conn.execute(
            """INSERT INTO public.relationship_rag_indexes(item_id,owner,revision,status,signature,chunk_count,attempts,error,updated_at)
        SELECT id,owner,revision,'pending','',0,0,'',now() FROM public.relationship_items WHERE owner=%s AND kind IN ('memory','chat_log') AND status='active'""",
            (owner,),
        )
    return accounts, vector_owners, vector_items


def expire_restored_content(conn, events):
    # Reapply expiry at recovery time, before anything could be served from the old snapshot.
    conn.execute(
        "CREATE TEMP TABLE recovery_cutoffs(owner text PRIMARY KEY,cutoff timestamptz) ON COMMIT DROP"
    )
    for event in events:
        if event["scope"] == "expiry":
            conn.execute(
                "INSERT INTO recovery_cutoffs VALUES(%s,%s) ON CONFLICT(owner) DO UPDATE SET cutoff=GREATEST(recovery_cutoffs.cutoff,EXCLUDED.cutoff)",
                (event["owner"], event["target_id"]),
            )
    conn.execute("""CREATE TEMP TABLE expired_turns ON COMMIT DROP AS SELECT t.id,t.owner FROM public.relationship_turns t
        LEFT JOIN public.relationship_privacy p ON p.owner=t.owner LEFT JOIN recovery_cutoffs e ON e.owner=t.owner
        WHERE t.created_at < GREATEST(now()-COALESCE(p.history_days,90)*INTERVAL '1 day',e.cutoff)""")
    conn.execute(
        "DELETE FROM public.relationship_feedback f USING expired_turns t WHERE f.owner=t.owner AND f.turn_id=t.id"
    )
    conn.execute(
        "UPDATE public.relationship_items i SET source_turn_id=NULL FROM expired_turns t WHERE i.owner=t.owner AND i.source_turn_id=t.id"
    )
    conn.execute(
        "UPDATE public.relationship_jobs j SET turn_id=NULL FROM expired_turns t WHERE j.owner=t.owner AND j.turn_id=t.id"
    )
    conn.execute(
        "DELETE FROM public.relationship_turns t USING expired_turns e WHERE t.owner=e.owner AND t.id=e.id"
    )
    expired = conn.execute(
        """SELECT i.id,i.owner FROM public.relationship_items i LEFT JOIN public.relationship_privacy p ON p.owner=i.owner LEFT JOIN recovery_cutoffs e ON e.owner=i.owner WHERE i.kind='chat_log' AND i.created_at<GREATEST(now()-COALESCE(p.history_days,90)*INTERVAL '1 day',e.cutoff)"""
    ).fetchall()
    for item_id, owner in expired:
        conn.execute(
            "DELETE FROM public.relationship_items WHERE id=%s AND owner=%s",
            (item_id, owner),
        )
        conn.execute(
            "DELETE FROM public.relationship_rag_indexes WHERE item_id=%s AND owner=%s",
            (item_id, owner),
        )
    conn.execute(
        """DELETE FROM public.relationship_jobs j USING (SELECT j.id FROM public.relationship_jobs j LEFT JOIN public.relationship_privacy p ON p.owner=j.owner LEFT JOIN recovery_cutoffs e ON e.owner=j.owner WHERE j.created_at<GREATEST(now()-COALESCE(p.history_days,90)*INTERVAL '1 day',e.cutoff)) e WHERE j.id=e.id"""
    )
    conn.execute("""CREATE TEMP TABLE expired_threads ON COMMIT DROP AS
        SELECT c.thread_id FROM xinyu_graph.checkpoints c
        LEFT JOIN public.relationship_jobs j ON c.thread_id='xinyu:'||j.owner||':'||j.id
        GROUP BY c.thread_id,j.status
        HAVING MAX((c.checkpoint->>'ts')::timestamptz)<now()-CASE WHEN j.status='succeeded' THEN INTERVAL '1 day' ELSE INTERVAL '7 days' END""")
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        conn.execute(
            sql.SQL(
                "DELETE FROM xinyu_graph.{} WHERE thread_id IN (SELECT thread_id FROM expired_threads)"
            ).format(sql.Identifier(table))
        )
    for table in ("checkpoint_blobs", "checkpoint_writes"):
        conn.execute(
            sql.SQL(
                "DELETE FROM xinyu_graph.{} t WHERE NOT EXISTS (SELECT 1 FROM xinyu_graph.checkpoints c WHERE c.thread_id=t.thread_id)"
            ).format(sql.Identifier(table))
        )
    for statement in [
        "DELETE FROM public.relationship_auth_events WHERE created_at<now()-INTERVAL '30 days'",
        "DELETE FROM public.relationship_login_limits WHERE reset_at<now()-INTERVAL '1 day'",
        "DELETE FROM public.relationship_model_calls WHERE created_at<now()-INTERVAL '14 days' AND status<>'reserved'",
        "DELETE FROM public.relationship_daily_usage WHERE day<to_char(now()-INTERVAL '14 days','YYYY-MM-DD') AND reserved_tokens=0",
        "DELETE FROM public.relationship_notices WHERE created_at<now()-INTERVAL '30 days' AND status IN ('read','cancelled')",
    ]:
        conn.execute(statement)
    return [{"owner": owner, "source_id": item_id} for item_id, owner in expired]


def upload_snapshot(client, target, path):
    with path.open("rb") as stream:
        response = client.post(
            f"/collections/{target}/snapshots/upload",
            params={"priority": "snapshot", "wait": "true"},
            files={
                "snapshot": (
                    path.name + ".snapshot",
                    stream,
                    "application/octet-stream",
                )
            },
        )
        response.raise_for_status()


def validate_private_records(conn, keys):
    validated = 0
    for table, column, keyname in [
        ("relationship_email_outbox", "encrypted_payload", "email.key"),
        ("relationship_weixin_bindings", "token_encrypted", "weixin.key"),
    ]:
        rows = conn.execute(
            sql.SQL("SELECT {} FROM public.{} WHERE {} <> ''").format(
                sql.Identifier(column), sql.Identifier(table), sql.Identifier(column)
            )
        ).fetchall()
        for row in rows:
            Fernet(base64.b64decode(keys[keyname], validate=True)).decrypt(
                row[0].encode()
            )
            validated += 1
    return validated


def drill(folder, journal=None):
    folder = checked_path(folder)
    if (
        not BACKUP_NAME.fullmatch(folder.name)
        or (folder / "COMPLETE").read_text(encoding="ascii") != folder.name
    ):
        raise ValueError("Backup is not complete or has an invalid identity")
    key, run_id, container = backup_key(), folder.name, pg_container()
    journal = checked_path(journal or recovery_journal.journal_path())
    intents = read_intents(journal, key)
    started = time.monotonic()
    with private_temp() as temporary, ExitStack() as cleanup:
        decrypt_to_file(
            folder / "manifest.enc",
            temporary / "manifest",
            key,
            (run_id + ":manifest").encode(),
        )
        manifest = json.loads((temporary / "manifest").read_bytes())
        if manifest["version"] != 1 or manifest["id"] != run_id:
            raise ValueError("Unknown manifest")
        for name, expected in manifest["parts"].items():
            if not re.fullmatch(
                r"database|private-config|deletions|vector-[0-9]+", name
            ):
                raise ValueError("Invalid manifest component name")
            found = decrypt_to_file(
                folder / (name + ".enc"),
                temporary / name,
                key,
                (run_id + ":" + name).encode(),
            )
            if found != expected:
                raise ValueError("Backup component fingerprint mismatch")
        restored_keys = json.loads((temporary / "private-config").read_bytes())["keys"]
        if not set(restored_keys).issubset(KEY_FILES):
            raise ValueError("Unexpected secret file in backup")
        intents.extend(read_intents(temporary / "deletions", key))
        database = "xinyu_restore_" + uuid4().hex[:16]
        with connect(autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

        def remove_database():
            assert re.fullmatch(r"xinyu_restore_[a-f0-9]{16}", database)
            with connect(autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(database)
                    )
                )

        cleanup.callback(remove_database)
        with pg_process(
            container,
            "pg_restore",
            database,
            [
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--clean",
                "--if-exists",
            ],
        ) as process:
            with (temporary / "database").open("rb") as source:
                for value in file_chunks(source):
                    process.stdin.write(value)
            process.stdin.close()
        with connect(database) as conn:
            if fingerprints(conn) != manifest["database_fingerprints"]:
                raise ValueError("Restored database does not match the saved snapshot")
            encrypted_records = validate_private_records(conn, restored_keys)
            intents.extend(read_intents(journal, key))
            accounts, vector_owners, vector_items = apply_intents(conn, intents)
            vector_items.extend(expire_restored_content(conn, intents))
            # No restored environment or Worker is started; sessions/channels are never activated.
            conn.execute("DELETE FROM public.relationship_sessions")
            conn.execute("DELETE FROM public.relationship_email_outbox")
            conn.execute("DELETE FROM public.relationship_email_challenges")
            conn.execute("DELETE FROM public.relationship_weixin_bindings")
            conn.execute(
                "UPDATE public.relationship_privacy SET accepted_at=NULL,revoked_at=now()"
            )
            conn.execute(
                "UPDATE public.relationship_jobs SET status='cancelled',cancel_requested=true,lease_token=NULL,lease_expires_at=NULL WHERE status IN ('queued','running')"
            )
            for owner in accounts:
                if conn.execute(
                    "SELECT count(*) FROM public.relationship_profiles WHERE owner=%s",
                    (owner,),
                ).fetchone()[0]:
                    raise ValueError("Deleted owner survived restoration")
            conn.commit()
        vector_results = {}
        with vector_client() as client:
            for name in manifest["vectors"]:
                target = "xinyu_restore_" + uuid4().hex[:16]
                if client.get(f"/collections/{target}").status_code != 404:
                    raise ValueError("Restore collection already exists")
                cleanup.callback(
                    lambda target=target: delete_restore_collection(target)
                )
                upload_snapshot(client, target, temporary / name)
                before = vector_fingerprint(client, target)
                if before != manifest["vectors"][name]["fingerprint"]:
                    raise ValueError(
                        "Restored vectors do not match snapshot verification"
                    )
                if vector_owners or vector_items:
                    filters = []
                    if vector_owners:
                        filters.append(
                            {"key": "owner", "match": {"any": list(vector_owners)}}
                        )
                    filters.extend(
                        {
                            "must": [
                                {"key": "owner", "match": {"value": row["owner"]}},
                                {
                                    "key": "source_id",
                                    "match": {"value": row["source_id"]},
                                },
                            ]
                        }
                        for row in vector_items
                    )
                    response = client.post(
                        f"/collections/{target}/points/delete",
                        params={"wait": "true"},
                        json={"filter": {"should": filters}},
                    )
                    response.raise_for_status()
                vector_results[name] = {
                    "restored": before,
                    "after_deletions": vector_fingerprint(client, target),
                }
        result = {
            "backup": run_id,
            "database_verified": True,
            "tables_verified": len(manifest["database_fingerprints"]),
            "graph_rows_verified": sum(
                value["rows"]
                for name, value in manifest["database_fingerprints"].items()
                if name.startswith("xinyu_graph.")
                and name != "xinyu_graph.checkpoint_migrations"
            ),
            "keys_authenticated": len(restored_keys),
            "encrypted_records_verified": encrypted_records,
            "deleted_owners_suppressed": len(accounts),
            "deletion_intents_applied": len(
                {json.dumps(event, sort_keys=True) for event in intents}
            ),
            "vectors": vector_results,
            "seconds": round(time.monotonic() - started, 3),
            "isolated_resources_cleaned": False,
        }
    result["isolated_resources_cleaned"] = True
    return result


def delete_restore_collection(name):
    assert re.fullmatch(r"xinyu_restore_[a-f0-9]{16}", name)
    with vector_client() as client:
        response = client.delete("/collections/" + name)
        if response.status_code != 404:
            response.raise_for_status()


def prune_backups(root=None, days=7, keep=3):
    if not 1 <= days <= 365 or not 1 <= keep <= 100:
        raise ValueError("Invalid backup retention")
    root = checked_path(root or PROJECT_ROOT / ".runtime" / "backups")
    if not root.exists():
        return 0
    choices = []
    for path in root.iterdir():
        if (
            path.is_dir()
            and not path.is_symlink()
            and BACKUP_NAME.fullmatch(path.name)
            and (path / "COMPLETE").is_file()
        ):
            if path.resolve().parent != root:
                raise ValueError("Backup path escaped root")
            choices.append(path)
    deleted = 0
    for path in sorted(choices, key=lambda path: path.name, reverse=True)[keep:]:
        stamp = datetime.strptime(path.name[13:29], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=UTC
        )
        if stamp < datetime.now(UTC) - timedelta(days=days):
            assert path.resolve().is_relative_to(root) and BACKUP_NAME.fullmatch(
                path.name
            )
            shutil.rmtree(path)
            deleted += 1
    return deleted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "drill", "prune"])
    parser.add_argument("folder", nargs="?")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--deletion-journal")
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = {"backup_folder": str(create_backup())}
        elif args.command == "drill":
            if not args.folder:
                parser.error("drill requires a backup folder")
            result = drill(args.folder, args.deletion_journal)
        else:
            result = {
                "deleted_expired_backups": prune_backups(days=args.days, keep=args.keep)
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - CLI must not expose private diagnostics
        # Do not leak SQL rows, connection strings or provider responses in terminal diagnostics.
        print("Backup/recovery failed: " + type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
