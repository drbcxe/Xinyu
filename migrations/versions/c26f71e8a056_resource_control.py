"""Durable token budgets, usage accounting and fenced worker leases."""

import sqlalchemy as sa
from alembic import op

revision = "c26f71e8a056"
down_revision = "b15e60d7f945"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "relationship_jobs", sa.Column("lease_token", sa.String(36), nullable=True)
    )
    op.add_column(
        "relationship_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "relationship_jobs",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "relationship_jobs",
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index(
        "ix_relationship_jobs_dispatch", "relationship_jobs", ["status", "available_at"]
    )
    op.create_table(
        "relationship_daily_usage",
        sa.Column("scope", sa.String(80), primary_key=True),
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("charged_tokens", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "relationship_model_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("operation_id", sa.String(80), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("model", sa.String(150), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("charged_tokens", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("usage_reported", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_relationship_model_calls_owner", "relationship_model_calls", ["owner"]
    )
    op.create_index(
        "ix_relationship_model_calls_operation_id",
        "relationship_model_calls",
        ["operation_id"],
    )


def downgrade():
    op.drop_table("relationship_model_calls")
    op.drop_table("relationship_daily_usage")
    op.drop_index("ix_relationship_jobs_dispatch", "relationship_jobs")
    for name in ("lease_token", "lease_expires_at", "available_at", "cancel_requested"):
        op.drop_column("relationship_jobs", name)
