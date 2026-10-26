from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | list | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details if details is not None else []


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    headers = {}
    if exc.status_code == 429 and isinstance(exc.details, dict):
        retry = exc.details.get("retry_after_seconds")
        if isinstance(retry, int):
            headers["Retry-After"] = str(max(retry, 1))
    return JSONResponse(
        status_code=exc.status_code,
        headers=headers,
        content={
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            "request_id": uuid4().hex,
        },
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = [
        {
            "location": list(error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]

    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数不符合要求",
                "details": details,
            },
            "request_id": uuid4().hex,
        },
    )
