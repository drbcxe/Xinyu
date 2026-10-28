"""Atomic logical storage accounting, including persisted graphs and job reservations."""

from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "f18be2a6d405"
down_revision = "ab3e94c520f1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "relationship_storage_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_bytes", sa.BigInteger(), nullable=False),
        sa.Column("global_bytes", sa.BigInteger(), nullable=False),
        sa.Column("graph_thread_bytes", sa.BigInteger(), nullable=False),
        sa.Column("job_reserve_bytes", sa.BigInteger(), nullable=False),
        sa.Column("profile_limit", sa.Integer(), nullable=False),
        sa.Column("emergency_bytes", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "relationship_storage_totals",
        sa.Column("scope", sa.String(80), primary_key=True),
        sa.Column("category", sa.String(32), primary_key=True),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "relationship_storage_reservations",
        sa.Column("job_id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("remaining", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_relationship_storage_reservations_owner",
        "relationship_storage_reservations",
        ["owner"],
    )
    op.create_table(
        "relationship_storage_threads",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_relationship_storage_threads_owner",
        "relationship_storage_threads",
        ["owner"],
    )
    op.execute(
        "INSERT INTO relationship_storage_policy VALUES(1,20971520,536870912,4194304,6291456,10000,65536)"
    )
    # Versioned source stays beside this migration rather than importing mutable application code.
    source = (Path(__file__).parents[1] / "sql" / "f18be2a6d405.sql").read_text(
        encoding="utf8"
    )
    op.get_bind().exec_driver_sql(source, execution_options={"no_parameters": True})
    op.execute("SELECT public.xinyu_storage_install()")
    op.execute("SELECT public.xinyu_storage_rebuild()")


def downgrade():
    op.execute(
        """DO $$ DECLARE r record; BEGIN FOR r IN SELECT n.nspname,c.relname,t.tgname FROM pg_trigger t JOIN pg_class c ON t.tgrelid=c.oid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE t.tgname IN ('xinyu_storage_accounting','xinyu_storage_lock') LOOP EXECUTE format('DROP TRIGGER %I ON %I.%I',r.tgname,r.nspname,r.relname); END LOOP; END $$"""
    )
    for signature in [
        "xinyu_storage_install()",
        "xinyu_storage_rebuild()",
        "xinyu_storage_row()",
        "xinyu_storage_stmt_lock()",
        "xinyu_storage_adjust(text,text,bigint)",
        "xinyu_storage_owner(jsonb)",
        "xinyu_storage_category(text,text)",
    ]:
        op.execute("DROP FUNCTION IF EXISTS public." + signature)
    for table in [
        "relationship_storage_threads",
        "relationship_storage_reservations",
        "relationship_storage_totals",
        "relationship_storage_policy",
    ]:
        op.drop_table(table)
