from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.repositories.database import get_db
from app.schemas.memories import (
    MemoryDeactivate,
    MemoryKey,
    MemoryList,
    MemoryRead,
    MemoryWrite,
)
from app.services import memory_service

router = APIRouter(prefix="/projects/{project_id}/memories", tags=["memories"])
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=MemoryList)
def list_memories(project_id: str, db: DatabaseSession) -> MemoryList:
    return MemoryList(
        items=[
            MemoryRead.model_validate(row)
            for row in memory_service.list_memories(db, project_id)
        ]
    )


@router.get("/{key}/history", response_model=MemoryList)
def memory_history(project_id: str, key: MemoryKey, db: DatabaseSession) -> MemoryList:
    return MemoryList(
        items=[
            MemoryRead.model_validate(row)
            for row in memory_service.history(db, project_id, key)
        ]
    )


@router.put("/{key}", response_model=MemoryRead)
def save_memory(
    project_id: str, key: MemoryKey, data: MemoryWrite, db: DatabaseSession
) -> MemoryRead:
    return MemoryRead.model_validate(
        memory_service.revise_memory(
            db, project_id, key, data.value, data.expected_revision
        )
    )


@router.post("/{key}/deactivate", response_model=MemoryRead)
def deactivate_memory(
    project_id: str, key: MemoryKey, data: MemoryDeactivate, db: DatabaseSession
) -> MemoryRead:
    return MemoryRead.model_validate(
        memory_service.revise_memory(db, project_id, key, None, data.expected_revision)
    )
