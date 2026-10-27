"""Versioned model-processing choices and chat retention preferences."""

import sqlalchemy as sa
from alembic import op

revision = "ab3e94c520f1"
down_revision = "e48b930ac278"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "relationship_privacy",
        sa.Column("owner", sa.String(64), primary_key=True),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("provider_fingerprint", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("history_days", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "history_days IN (30, 90, 180)", name="ck_privacy_history_days"
        ),
    )


def downgrade():
    op.drop_table("relationship_privacy")
