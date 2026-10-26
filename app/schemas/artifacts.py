from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: str
    original_name: str
    sha256: str
    size_bytes: int
    row_count: int
    created_at: datetime


class ArtifactUploadResult(ArtifactRead):
    reused: bool


class ArtifactList(BaseModel):
    items: list[ArtifactRead]


class ExperimentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: str
    artifact_id: UUID
    run_id: str
    model_name: str
    dataset_version: str
    split_id: str
    protocol_version: str
    metric_name: str
    metric_unit: str
    optimization: str
    metric_value: str
    seed: str
    source_line: int


class ExperimentRunList(BaseModel):
    items: list[ExperimentRunRead]
