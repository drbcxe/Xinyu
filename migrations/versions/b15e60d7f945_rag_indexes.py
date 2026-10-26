"""Durable RAG index outbox and isolated LangGraph checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "b15e60d7f945"
down_revision = "a04d5fc6e834"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS xinyu_graph")
    op.create_table(
        "relationship_rag_indexes",
        sa.Column("item_id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(300), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_relationship_rag_indexes_owner", "relationship_rag_indexes", ["owner"]
    )
    op.create_index(
        "ix_relationship_rag_indexes_status", "relationship_rag_indexes", ["status"]
    )
    op.execute("""INSERT INTO relationship_rag_indexes
        (item_id, owner, revision, status, signature, chunk_count, attempts, error, updated_at)
        SELECT id, owner, revision, 'pending', '', 0, 0, '', now()
        FROM relationship_items WHERE kind IN ('memory','chat_log') AND status='active'""")


def downgrade():
    # Checkpoint data stays available for recovery; never drop a populated private schema here.
    op.drop_table("relationship_rag_indexes")
