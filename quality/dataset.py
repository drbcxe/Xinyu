import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class Record(BaseModel):
    label: str = Field(pattern=r"^[a-z0-9_]+$")
    text: str = Field(min_length=1, max_length=20000)
    kind: Literal["memory", "chat_log", "reminder"] = "memory"
    status: Literal["active", "completed", "cancelled"] = "active"
    due_at: AwareDatetime | None = None
    delete_after_index: bool = False
    long_anchor: str = ""


class Evidence(BaseModel):
    label: str
    evidence: str = Field(min_length=1)


class Update(BaseModel):
    label: str
    text: str = Field(min_length=1, max_length=20000)


class Case(BaseModel):
    id: str = Field(pattern=r"^[a-z]+-[0-9]{2}$")
    split: Literal["dev", "holdout"]
    category: str
    messages: list[str] = Field(min_length=1, max_length=4)
    reference: str = Field(min_length=1)
    records: list[Record] = Field(default_factory=list)
    expected: list[Evidence] = Field(default_factory=list)
    updates: list[Update] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list, max_length=4)
    required_tools: list[str] = Field(default_factory=list)
    confirm_after: list[int] = Field(default_factory=list)
    mode: Literal["chat", "date"] = "chat"

    @model_validator(mode="after")
    def references_exist(self):
        labels = [record.label for record in self.records]
        if len(labels) != len(set(labels)) or any(
            row.label not in labels for row in [*self.expected, *self.updates]
        ):
            raise ValueError(
                "Case evidence/update refers to a missing or duplicate record"
            )
        if any(i < 0 or i >= len(self.messages) for i in self.confirm_after):
            raise ValueError("Confirmation refers to a missing turn")
        return self


class Dataset(BaseModel):
    version: str
    data_origin: Literal["synthetic"]
    clock: AwareDatetime
    noise: list[str]
    cases: list[Case] = Field(min_length=50, max_length=50)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate evaluation ID")
        return self


def load_dataset():
    raw = json.loads(
        Path(__file__).with_name("cases.json").read_text(encoding="utf-8-sig")
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return Dataset.model_validate(raw), fingerprint
