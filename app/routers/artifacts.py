from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.repositories.database import get_db
from app.schemas.artifacts import (
    ArtifactList,
    ArtifactRead,
    ArtifactUploadResult,
    ExperimentRunList,
    ExperimentRunRead,
)
from app.services import artifact_service

router = APIRouter(prefix="/projects/{project_id}/artifacts", tags=["artifacts"])
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.post(
    "", response_model=ArtifactUploadResult, status_code=status.HTTP_201_CREATED
)
def upload_artifact(
    project_id: str,
    file: Annotated[
        UploadFile, File(description="UTF-8 CSV，最多 5 MiB、10000 条实验")
    ],
    db: DatabaseSession,
    response: Response,
) -> ArtifactUploadResult:
    try:
        raw = file.file.read(artifact_service.MAX_FILE_BYTES + 1)
        artifact, reused = artifact_service.upload_artifact(
            db, project_id, file.filename or "", raw
        )
    finally:
        file.file.close()
    if reused:
        response.status_code = status.HTTP_200_OK
    return ArtifactUploadResult(
        **ArtifactRead.model_validate(artifact).model_dump(), reused=reused
    )


@router.get("", response_model=ArtifactList)
def list_artifacts(project_id: str, db: DatabaseSession) -> ArtifactList:
    return ArtifactList(
        items=[
            ArtifactRead.model_validate(item)
            for item in artifact_service.list_artifacts(db, project_id)
        ]
    )


@router.get("/{artifact_id}/runs", response_model=ExperimentRunList)
def list_runs(
    project_id: str, artifact_id: UUID, db: DatabaseSession
) -> ExperimentRunList:
    return ExperimentRunList(
        items=[
            ExperimentRunRead.model_validate(item)
            for item in artifact_service.list_runs(db, project_id, artifact_id)
        ]
    )


@router.get("/{artifact_id}", response_model=ArtifactRead)
def get_artifact(
    project_id: str, artifact_id: UUID, db: DatabaseSession
) -> ArtifactRead:
    return ArtifactRead.model_validate(
        artifact_service.get_artifact(db, project_id, artifact_id)
    )
