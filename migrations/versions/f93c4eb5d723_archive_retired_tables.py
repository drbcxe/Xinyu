"""Move retired business tables out of the active application schema.

Old migration revisions remain immutable so existing installations can upgrade.
No rows are deleted. The application does not query the archive schema.
"""

import sqlalchemy as sa
from alembic import op

revision = "f93c4eb5d723"
down_revision = "e82b3da4c612"
branch_labels = None
depends_on = None

TABLES = (
    "projects",
    "artifacts",
    "experiment_runs",
    "comparisons",
    "threads",
    "agent_runs",
    "messages",
    "run_events",
    "project_memories",
)


def upgrade():
    connection = op.get_bind()
    op.execute('CREATE SCHEMA IF NOT EXISTS "research_archive"')
    inspector = sa.inspect(connection)
    active = set(inspector.get_table_names(schema="public"))
    archived = set(inspector.get_table_names(schema="research_archive"))
    for name in TABLES:
        if name in active:
            if name in archived:
                raise RuntimeError(
                    f"Archive already contains {name}; refusing to overwrite"
                )
            op.execute(
                sa.text(f'ALTER TABLE public."{name}" SET SCHEMA research_archive')
            )
    schemas = set(inspector.get_schema_names())
    if "agent_checkpoints" in schemas:
        if "research_checkpoints_archive" in schemas:
            raise RuntimeError(
                "Checkpoint archive already exists; refusing to overwrite"
            )
        op.execute(
            'ALTER SCHEMA "agent_checkpoints" RENAME TO "research_checkpoints_archive"'
        )


def downgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    active = set(inspector.get_table_names(schema="public"))
    archived = set(inspector.get_table_names(schema="research_archive"))
    for name in TABLES:
        if name in archived:
            if name in active:
                raise RuntimeError(
                    f"Public schema already contains {name}; refusing to overwrite"
                )
            op.execute(
                sa.text(f'ALTER TABLE research_archive."{name}" SET SCHEMA public')
            )
    schemas = set(inspector.get_schema_names())
    if "research_checkpoints_archive" in schemas:
        if "agent_checkpoints" in schemas:
            raise RuntimeError(
                "Checkpoint schema already exists; refusing to overwrite"
            )
        op.execute(
            'ALTER SCHEMA "research_checkpoints_archive" RENAME TO "agent_checkpoints"'
        )
