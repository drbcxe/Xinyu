from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.repositories.database import get_db
from app.schemas.chat import (
    MessageList,
    RunAccepted,
    RunCreate,
    RunRead,
    ThreadCreate,
    ThreadList,
    ThreadRead,
)
from app.services import chat_service

router = APIRouter(prefix="/projects/{project_id}", tags=["agent-chat"])
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.post("/threads", response_model=ThreadRead, status_code=status.HTTP_201_CREATED)
def create_thread(
    project_id: str, data: ThreadCreate, db: DatabaseSession
) -> ThreadRead:
    return ThreadRead.model_validate(chat_service.create_thread(db, project_id, data))


@router.get("/threads", response_model=ThreadList)
def list_threads(project_id: str, db: DatabaseSession) -> ThreadList:
    return ThreadList(
        items=[
            ThreadRead.model_validate(thread)
            for thread in chat_service.list_threads(db, project_id)
        ]
    )


@router.get("/threads/{thread_id}", response_model=ThreadRead)
def get_thread(project_id: str, thread_id: UUID, db: DatabaseSession) -> ThreadRead:
    return ThreadRead.model_validate(chat_service.get_thread(db, project_id, thread_id))


@router.get("/threads/{thread_id}/messages", response_model=MessageList)
def list_messages(project_id: str, thread_id: UUID, db: DatabaseSession) -> MessageList:
    return chat_service.list_messages(db, project_id, thread_id)


@router.post(
    "/threads/{thread_id}/runs",
    response_model=RunAccepted,
    status_code=status.HTTP_201_CREATED,
)
def submit_run(project_id: str, thread_id: UUID, data: RunCreate) -> RunAccepted:
    run = chat_service.submit_run(project_id, thread_id, data)
    return RunAccepted(run_id=run.id, status=run.status)


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(project_id: str, run_id: UUID, db: DatabaseSession) -> RunRead:
    return chat_service.read_run(db, project_id, run_id)
