"""Allow bounded indexing/control statuses to use reserved system headroom."""

from pathlib import Path

from alembic import op

revision = "0c58ad62f901"
down_revision = "92c71a0ed683"
branch_labels = None
depends_on = None


def install_row_function(*, extended):
    # Both upgrade/downgrade use immutable versioned SQL, never current application code.
    source = (Path(__file__).parents[1] / "sql" / "f18be2a6d405.sql").read_text(
        encoding="utf8"
    )
    start = source.index("CREATE FUNCTION public.xinyu_storage_row()")
    end = source.index("-- Acquire the accounting lock", start)
    source = source[start:end].replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )
    if extended:
        original = (
            "after_value->>'status' IN ('deleting','delete_failed','failed','ready')"
        )
        assert original in source
        source = source.replace(
            original,
            "after_value->>'status' IN ('deleting','delete_failed','failed','ready','indexing','quota_blocked')",
        )
    op.get_bind().exec_driver_sql(source, execution_options={"no_parameters": True})


def upgrade():
    install_row_function(extended=True)


def downgrade():
    install_row_function(extended=False)
