import asyncio
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.config import PROJECT_ROOT
from app.errors import AppError, app_error_handler, validation_error_handler
from app.routers.account_email import router as account_email_router
from app.routers.account_privacy import router as account_privacy_router
from app.routers.health import router as health_router
from app.routers.ops import router as ops_router
from app.routers.relationship import router as relationship_router
from app.services.observability import RequestTelemetry, configure_logging
from app.services.relationship_auth import router as relationship_auth_router
from app.services.retrieval_cache import corpus_cache


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("api")
    upload_temp = PROJECT_ROOT / ".runtime" / "upload-tmp"
    upload_temp.mkdir(parents=True, exist_ok=True)
    previous_temp = tempfile.tempdir
    tempfile.tempdir = str(upload_temp)

    async def sweep_cache():
        while True:
            await asyncio.sleep(15)
            corpus_cache.expire()

    sweeper = asyncio.create_task(sweep_cache())
    try:
        yield
    finally:
        sweeper.cancel()
        await asyncio.gather(sweeper, return_exceptions=True)
        corpus_cache.clear()
        tempfile.tempdir = previous_temp
        from app.providers.model_provider import model_http_client

        if model_http_client.cache_info().currsize:
            model_http_client().close()
            model_http_client.cache_clear()


app = FastAPI(
    title="心语 Xinyu API",
    description="现实情侣的私人沟通助手：聊天建议、伴侣偏好与重要事项管理。",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_middleware(RequestTelemetry, route_prefix="/api")

app.include_router(health_router, prefix="/api")
app.include_router(ops_router, prefix="/api")
app.include_router(relationship_router, prefix="/api")
app.include_router(relationship_auth_router, prefix="/api")
app.include_router(account_email_router, prefix="/api")
app.include_router(account_privacy_router, prefix="/api")
