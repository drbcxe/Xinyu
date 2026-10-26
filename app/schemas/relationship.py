from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class ProfileInput(BaseModel):
    partner_name: str = Field(min_length=1, max_length=80)
    style: str = Field(min_length=1, max_length=500)


class ChatInput(BaseModel):
    request_id: UUID
    content: str = Field(min_length=1, max_length=16000)
    mode: Literal["chat", "date"] = "chat"


class ImageInput(BaseModel):
    images: list[str] = Field(min_length=1, max_length=2)


class Candidate(BaseModel):
    kind: Literal["memory", "reminder"]
    title: str = Field(min_length=1, max_length=500)
    quote: str = Field(min_length=1, max_length=1000)


class DateOption(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    budget: str = Field(max_length=200)
    schedule: list[str] = Field(min_length=1, max_length=8)
    reason: str = Field(max_length=1500)
    needs_confirmation: list[str] = Field(default_factory=list, max_length=6)


class Advice(BaseModel):
    transcript: str = Field(default="", max_length=16000)
    analysis: str = Field(min_length=1, max_length=5000)
    uncertainty: str = Field(default="", max_length=1000)
    replies: list[str] = Field(default_factory=list, max_length=3)
    candidates: list[Candidate] = Field(default_factory=list, max_length=6)
    date_plans: list[DateOption] = Field(default_factory=list, max_length=3)
    source_ids: list[str] = Field(default_factory=list, max_length=16)


class ItemInput(BaseModel):
    kind: Literal["memory", "reminder", "date_plan"]
    title: str = Field(min_length=1, max_length=500)
    quote: str = Field(default="", max_length=1000)
    source_turn_id: UUID | None = None
    due_at: AwareDatetime | None = None
    plan_index: int | None = Field(default=None, ge=0, le=2)


class ItemUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    due_at: AwareDatetime | None = None
    status: Literal["active", "completed", "cancelled"] = "active"
