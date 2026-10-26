from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.models.comparison import ComparisonRecord
from app.repositories.database import get_db
from app.schemas.comparisons import ComparisonCreate, ComparisonList, ComparisonRead
from app.services import comparison_service

router = APIRouter(prefix="/projects/{project_id}/comparisons", tags=["comparisons"])
DatabaseSession = Annotated[Session, Depends(get_db)]


def _read(record: ComparisonRecord) -> ComparisonRead:
    return ComparisonRead.model_validate(
        {
            **record.result,
            "id": record.id,
            "project_id": record.project_id,
            "artifact_id": record.artifact_id,
            "created_at": record.created_at,
        }
    )


@router.post("", response_model=ComparisonRead, status_code=status.HTTP_201_CREATED)
def create_comparison(
    project_id: str, data: ComparisonCreate, db: DatabaseSession
) -> ComparisonRead:
    return _read(comparison_service.create_comparison(db, project_id, data))


@router.get("", response_model=ComparisonList)
def list_comparisons(
    project_id: str, artifact_id: UUID, db: DatabaseSession
) -> ComparisonList:
    return ComparisonList(
        items=[
            _read(record)
            for record in comparison_service.list_comparisons(
                db, project_id, artifact_id
            )
        ]
    )


@router.get("/{comparison_id}", response_model=ComparisonRead)
def get_comparison(
    project_id: str, comparison_id: UUID, db: DatabaseSession
) -> ComparisonRead:
    return _read(comparison_service.get_comparison(db, project_id, comparison_id))
