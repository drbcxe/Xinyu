from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MemoryKey = Literal[
    "default_baseline", "preferred_metric", "response_length", "report_language"
]


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    value: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0)


class MemoryDeactivate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class ReviseMemoryArgs(MemoryWrite):
    key: MemoryKey
    source_quote: str = Field(min_length=2, max_length=1000)

    @model_validator(mode="after")
    def value_in_quote(self) -> "ReviseMemoryArgs":
        if self.value.casefold() not in self.source_quote.casefold():
            raise ValueError("偏好值必须来自用户原话，不能自行推断")
        return self


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: str
    key: MemoryKey
    value: str
    revision: int
    is_active: bool
    supersedes_id: UUID | None
    source: Literal["manual", "agent"]
    source_quote: str
    source_message_id: UUID | None
    created_at: datetime


class MemoryList(BaseModel):
    items: list[MemoryRead]
