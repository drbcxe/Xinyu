from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    id: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Z][A-Z0-9_-]{0,31}$",
    )
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    created_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectRead]
