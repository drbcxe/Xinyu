from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ComparisonCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    artifact_id: UUID
    baseline_run_id: str = Field(min_length=1, max_length=131072)
    candidate_run_id: str = Field(min_length=1, max_length=131072)


class MetricRead(BaseModel):
    name: str
    unit: str
    optimization: Literal["min", "max"]


class ComparedRunRead(BaseModel):
    run_id: str
    model_name: str
    value: str
    source_line: int


class CalculationRead(BaseModel):
    candidate_minus_baseline: str
    improvement: str
    improvement_pct: str | None
    outcome: Literal["improved", "regressed", "tied"]
    better_run_id: str | None
    scope: str


class ProvenanceRead(BaseModel):
    original_name: str
    sha256: str
    dataset_version: str
    split_id: str
    protocol_version: str
    metric_name: str
    metric_unit: str
    optimization: Literal["min", "max"]
    seed: str


class ComparisonRead(BaseModel):
    id: UUID
    project_id: str
    artifact_id: UUID
    created_at: datetime
    status: Literal["succeeded"]
    metric: MetricRead
    baseline: ComparedRunRead
    candidate: ComparedRunRead
    comparison: CalculationRead
    provenance: ProvenanceRead


class ComparisonList(BaseModel):
    items: list[ComparisonRead]
