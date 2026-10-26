from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.project import Project
from app.schemas.projects import ProjectCreate


def project_exists_error() -> AppError:
    return AppError(
        status_code=409,
        code="PROJECT_EXISTS",
        message="项目编号已存在，请使用其他编号",
    )


def create_project(db: Session, data: ProjectCreate) -> Project:
    if db.get(Project, data.id) is not None:
        raise project_exists_error()

    project = Project(
        id=data.id,
        name=data.name,
        description=data.description,
    )
    db.add(project)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.get(Project, data.id) is not None:
            raise project_exists_error() from None
        raise

    db.refresh(project)
    return project


def list_projects(db: Session) -> list[Project]:
    statement = select(Project).order_by(Project.created_at.desc(), Project.id.asc())
    return list(db.scalars(statement).all())


def get_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise AppError(404, "PROJECT_NOT_FOUND", "项目不存在，请返回项目列表")
    return project
