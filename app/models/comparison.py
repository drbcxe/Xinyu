from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ComparisonRecord(Base):
    __tablename__ = "comparisons"
    __table_args__ = (
        UniqueConstraint(
            "agent_run_id", "tool_call_id", name="uq_comparisons_agent_tool"
        ),
        ForeignKeyConstraint(
            ["artifact_id", "project_id"],
            ["artifacts.id", "artifacts.project_id"],
            name="fk_comparisons_artifact_project",
            ondelete="CASCADE",
        ),
        Index(
            "ix_comparisons_scope_created", "project_id", "artifact_id", "created_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_id: Mapped[UUID] = mapped_column(nullable=False)
    baseline_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True
    )
    tool_call_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
