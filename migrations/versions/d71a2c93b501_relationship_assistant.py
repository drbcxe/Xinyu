"""Add isolated relationship assistant tables; preserve research data."""

import sqlalchemy as sa
from alembic import op

revision = "d71a2c93b501"
down_revision = "c60bdf0f37ba"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "relationship_profiles",
        sa.Column("owner", sa.String(64), primary_key=True),
        sa.Column("partner_name", sa.String(80), nullable=False),
        sa.Column("style", sa.String(500), nullable=False),
    )
    op.create_table(
        "relationship_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner", "request_id"),
    )
    op.create_index("ix_relationship_turns_owner", "relationship_turns", ["owner"])
    op.create_table(
        "relationship_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("source_turn_id", sa.String(36), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_relationship_items_owner", "relationship_items", ["owner"])


def downgrade():
    op.drop_table("relationship_items")
    op.drop_table("relationship_turns")
    op.drop_table("relationship_profiles")
