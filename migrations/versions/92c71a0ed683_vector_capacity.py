"""Account for vector slots before embedding; preserve uncertain allocations until cleanup."""

import sqlalchemy as sa
from alembic import op

revision = "92c71a0ed683"
down_revision = "f18be2a6d405"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "relationship_rag_indexes",
        sa.Column(
            "reserved_points", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    for name, default in [
        ("owner_vector_points", "50000"),
        ("global_vector_points", "250000"),
        ("vector_untracked_points", "0"),
        ("vector_observed_points", "0"),
    ]:
        op.add_column(
            "relationship_storage_policy",
            sa.Column(name, sa.BigInteger(), nullable=False, server_default=default),
        )
    op.add_column(
        "relationship_storage_policy",
        sa.Column("vector_audited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "relationship_storage_policy",
        sa.Column("vector_scope", sa.String(100), nullable=False, server_default=""),
    )
    op.execute("SELECT set_config('xinyu.storage_bypass','on',true)")
    op.execute(
        "UPDATE relationship_rag_indexes SET reserved_points=GREATEST(chunk_count,0)"
    )
    op.execute("""CREATE FUNCTION public.xinyu_vector_budget() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE policy public.relationship_storage_policy%ROWTYPE; previous bigint:=0; total bigint;
    BEGIN
      IF NEW.reserved_points<0 THEN RAISE EXCEPTION USING ERRCODE='XY009',MESSAGE='VECTOR_RESERVATION_INVALID'; END IF;
      IF TG_OP='UPDATE' THEN previous:=OLD.reserved_points; END IF;
      IF NEW.reserved_points<=previous OR COALESCE(current_setting('xinyu.vector_bypass',true),'')='on' THEN RETURN NEW; END IF;
      SELECT * INTO policy FROM public.relationship_storage_policy WHERE id=1;
      IF policy.id IS NULL OR policy.vector_audited_at IS NULL THEN RAISE EXCEPTION USING ERRCODE='XY008',MESSAGE='VECTOR_AUDIT_REQUIRED'; END IF;
      IF policy.vector_untracked_points>0 THEN RAISE EXCEPTION USING ERRCODE='XY008',MESSAGE='VECTOR_AUDIT_REQUIRED'; END IF;
      SELECT COALESCE(sum(reserved_points),0) INTO total FROM public.relationship_rag_indexes;
      IF total+policy.vector_untracked_points>policy.global_vector_points THEN RAISE EXCEPTION USING ERRCODE='XY007',MESSAGE='GLOBAL_VECTOR_QUOTA_EXCEEDED'; END IF;
      SELECT COALESCE(sum(reserved_points),0) INTO total FROM public.relationship_rag_indexes WHERE owner=NEW.owner;
      IF total>policy.owner_vector_points THEN RAISE EXCEPTION USING ERRCODE='XY006',MESSAGE='VECTOR_QUOTA_EXCEEDED'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER xinyu_vector_capacity AFTER INSERT OR UPDATE ON relationship_rag_indexes FOR EACH ROW EXECUTE FUNCTION public.xinyu_vector_budget()"
    )
    op.execute("SELECT public.xinyu_storage_rebuild()")


def downgrade():
    op.execute("DROP TRIGGER xinyu_vector_capacity ON relationship_rag_indexes")
    op.execute("DROP FUNCTION public.xinyu_vector_budget()")
    op.drop_column("relationship_rag_indexes", "reserved_points")
    for name in (
        "vector_scope",
        "vector_audited_at",
        "vector_untracked_points",
        "vector_observed_points",
        "global_vector_points",
        "owner_vector_points",
    ):
        op.drop_column("relationship_storage_policy", name)
    op.execute("SELECT public.xinyu_storage_rebuild()")
