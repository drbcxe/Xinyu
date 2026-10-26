from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProjectMemory(Base):
    __tablename__ = "project_memories"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "key", "revision", name="uq_memories_project_key_revision"
        ),
        UniqueConstraint("agent_run_id", "tool_call_id", name="uq_memories_run_tool"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id"), index=True
    )
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    supersedes_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_memories.id")
    )
    source: Mapped[str] = mapped_column(String(16))
    source_quote: Mapped[str] = mapped_column(Text, default="")
    source_message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id"))
    agent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    tool_call_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
