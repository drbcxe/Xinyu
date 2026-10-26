"""Add public device-session identifiers and account security events."""

import sqlalchemy as sa
from alembic import op

revision = "a04d5fc6e834"
down_revision = "f93c4eb5d723"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "relationship_sessions",
        sa.Column(
            "id",
            sa.String(36),
            nullable=False,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
    )
    op.create_unique_constraint(
        "uq_relationship_sessions_id", "relationship_sessions", ["id"]
    )
    op.alter_column("relationship_sessions", "id", server_default=None)
    op.add_column(
        "relationship_sessions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "relationship_sessions",
        sa.Column(
            "device_label", sa.String(120), nullable=False, server_default="Unknown"
        ),
    )
    op.add_column(
        "relationship_sessions",
        sa.Column("ip_tag", sa.String(16), nullable=False, server_default=""),
    )
    op.create_table(
        "relationship_auth_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=True),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("device_label", sa.String(120), nullable=False),
        sa.Column("ip_tag", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_relationship_auth_events_owner", "relationship_auth_events", ["owner"]
    )


def downgrade():
    op.drop_table("relationship_auth_events")
    op.drop_constraint(
        "uq_relationship_sessions_id", "relationship_sessions", type_="unique"
    )
    for name in ("id", "created_at", "device_label", "ip_tag"):
        op.drop_column("relationship_sessions", name)
