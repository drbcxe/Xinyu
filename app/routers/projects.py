from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.repositories.database import get_db
from app.schemas.projects import ProjectCreate, ProjectList, ProjectRead
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(data: ProjectCreate, db: DatabaseSession) -> ProjectRead:
    project = project_service.create_project(db, data)
    return ProjectRead.model_validate(project)


@router.get("", response_model=ProjectList)
def list_projects(db: DatabaseSession) -> ProjectList:
    projects = project_service.list_projects(db)
    return ProjectList(
        items=[ProjectRead.model_validate(project) for project in projects]
    )


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, db: DatabaseSession) -> ProjectRead:
    return ProjectRead.model_validate(project_service.get_project(db, project_id))
