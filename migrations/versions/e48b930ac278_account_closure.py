"""Account deletion receipts and private response feedback."""

import sqlalchemy as sa
from alembic import op

revision = "e48b930ac278"
down_revision = "d37a82f9b167"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "relationship_closed_spaces",
        sa.Column("owner", sa.String(64), primary_key=True),
        sa.Column("receipt_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleanup_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "relationship_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=False),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("comment", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner", "turn_id"),
    )
    op.create_index(
        "ix_relationship_feedback_owner", "relationship_feedback", ["owner"]
    )


def downgrade():
    op.drop_table("relationship_feedback")
    op.drop_table("relationship_closed_spaces")
