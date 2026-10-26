from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConversationThread(Base):
    __tablename__ = "threads"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_threads_id_project"),
        ForeignKeyConstraint(
            ["artifact_id", "project_id"],
            ["artifacts.id", "artifacts.project_id"],
            name="fk_threads_artifact_project",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(100), default="新会话")
    artifact_id: Mapped[UUID | None] = mapped_column(nullable=True)
    active_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "request_id", name="uq_agent_runs_thread_request"
        ),
        ForeignKeyConstraint(
            ["thread_id", "project_id"],
            ["threads.id", "threads.project_id"],
            name="fk_agent_runs_thread_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["artifact_id", "project_id"],
            ["artifacts.id", "artifacts.project_id"],
            name="fk_agent_runs_artifact_project",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    artifact_id: Mapped[UUID | None] = mapped_column(nullable=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="running")
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_name: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(32), default="xinyu-legacy-v3")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChatMessage(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("run_id", "role", name="uq_messages_run_role"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunEvent(Base):
    __tablename__ = "run_events"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
