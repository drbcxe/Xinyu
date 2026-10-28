"""Persist bounded capacity waits separately from execution failures."""

import sqlalchemy as sa
from alembic import op

revision = "27b13c74a6e8"
down_revision = "0c58ad62f901"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SELECT pg_advisory_xact_lock(718302612)")
    op.add_column(
        "relationship_jobs",
        sa.Column(
            "capacity_deferrals", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "relationship_jobs",
        sa.Column(
            "capacity_wait_started_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    # ADD COLUMN changes logical JSON row lengths without firing row accounting.
    op.execute("SELECT public.xinyu_storage_rebuild()")
    op.create_index(
        "ix_relationship_model_calls_pending",
        "relationship_model_calls",
        ["status", "created_at"],
    )


def downgrade():
    op.execute("SELECT pg_advisory_xact_lock(718302612)")
    op.drop_index(
        "ix_relationship_model_calls_pending", table_name="relationship_model_calls"
    )
    op.drop_column("relationship_jobs", "capacity_wait_started_at")
    op.drop_column("relationship_jobs", "capacity_deferrals")
    op.execute("SELECT public.xinyu_storage_rebuild()")
