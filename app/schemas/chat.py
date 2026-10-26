from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.comparisons import ComparisonRead


class ThreadCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(default="新会话", min_length=1, max_length=100)
    artifact_id: UUID | None = None


class ThreadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: str
    title: str
    artifact_id: UUID | None
    active_run_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ThreadList(BaseModel):
    items: list[ThreadRead]


class RunCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    request_id: UUID
    artifact_id: UUID | None = None


class RunAccepted(BaseModel):
    run_id: UUID
    status: str


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sequence: int
    type: str
    payload: dict
    created_at: datetime


class RunRead(BaseModel):
    id: UUID
    request_id: UUID
    thread_id: UUID
    project_id: str
    artifact_id: UUID | None
    status: Literal["running", "succeeded", "failed"]
    reply: str | None
    error: dict | None
    evidence: list[ComparisonRead]
    events: list[EventRead]
    model_name: str
    usage: dict | None
    duration_ms: int | None
    created_at: datetime
    finished_at: datetime | None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    run_id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class MessageList(BaseModel):
    items: list[MessageRead]
    active_run_id: UUID | None
    runs: list[RunRead]
