import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.config import PROJECT_ROOT
from app.errors import AppError, app_error_handler, validation_error_handler
from app.routers.artifacts import router as artifacts_router
from app.routers.chat import router as chat_router
from app.routers.comparisons import router as comparisons_router
from app.routers.health import router as health_router
from app.routers.memories import router as memories_router
from app.routers.projects import router as projects_router
from app.routers.relationship import router as relationship_router
from app.services.relationship_auth import router as relationship_auth_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    upload_temp = PROJECT_ROOT / ".runtime" / "upload-tmp"
    upload_temp.mkdir(parents=True, exist_ok=True)
    previous_temp = tempfile.tempdir
    tempfile.tempdir = str(upload_temp)
    try:
        yield
    finally:
        from app.graph.checkpoint import close_checkpointer

        close_checkpointer()
        tempfile.tempdir = previous_temp


app = FastAPI(
    title="心语 Xinyu API",
    description="现实情侣的私人沟通助手：聊天建议、伴侣偏好与重要事项管理。",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)

app.include_router(health_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(memories_router, prefix="/api")
app.include_router(artifacts_router, prefix="/api")
app.include_router(comparisons_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(relationship_router, prefix="/api")
app.include_router(relationship_auth_router, prefix="/api")
