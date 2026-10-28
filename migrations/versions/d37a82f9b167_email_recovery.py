"""Verified recovery email, transactional challenges and encrypted mail outbox."""

import sqlalchemy as sa
from alembic import op

revision = "d37a82f9b167"
down_revision = "c26f71e8a056"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "relationship_accounts", sa.Column("email", sa.String(254), nullable=True)
    )
    op.add_column(
        "relationship_accounts",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_relationship_accounts_email", "relationship_accounts", ["email"]
    )
    op.create_table(
        "relationship_email_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("binding", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("answer_hash", sa.String(64), nullable=False),
        sa.Column("password_version", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_relationship_email_challenges_owner",
        "relationship_email_challenges",
        ["owner"],
    )
    op.create_table(
        "relationship_email_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("owner", "status"):
        op.create_index(
            f"ix_relationship_email_outbox_{column}",
            "relationship_email_outbox",
            [column],
        )


def downgrade():
    op.drop_table("relationship_email_outbox")
    op.drop_table("relationship_email_challenges")
    op.drop_constraint(
        "uq_relationship_accounts_email", "relationship_accounts", type_="unique"
    )
    op.drop_column("relationship_accounts", "email_verified_at")
    op.drop_column("relationship_accounts", "email")
