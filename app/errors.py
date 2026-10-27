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


STORAGE_ERROR_CODES = {
    "XY001": (
        413,
        "SPACE_QUOTA_EXCEEDED",
        "当前空间容量不足，请删除不需要的记录后重试",
    ),
    "XY002": (503, "GLOBAL_STORAGE_QUOTA_EXCEEDED", "服务存储预算已满，请稍后重试"),
    "XY003": (
        413,
        "GRAPH_STORAGE_LIMIT",
        "本次执行的临时状态超过容量限制，请缩小问题范围",
    ),
    "XY004": (
        503,
        "STORAGE_POLICY_MISSING",
        "存储预算尚未初始化，请先升级并准备数据库",
    ),
    "XY005": (503, "SPACE_COUNT_LIMIT", "服务空间数量已达上限，请稍后重试"),
}


def storage_error(exc):
    state = getattr(exc, "sqlstate", None) or getattr(
        getattr(exc, "orig", None), "sqlstate", None
    )
    if state in STORAGE_ERROR_CODES:
        status, code, message = STORAGE_ERROR_CODES[state]
        return AppError(status, code, message)
    return None


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    headers = {}
    if exc.status_code in {429, 503} and isinstance(exc.details, dict):
        retry = exc.details.get("retry_after_seconds")
        if isinstance(retry, int):
            headers["Retry-After"] = str(max(retry, 1))
    return JSONResponse(
        status_code=exc.status_code,
        headers=headers,
        content={
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            "request_id": getattr(request.state, "request_id", None) or uuid4().hex,
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
            "request_id": getattr(request.state, "request_id", None) or uuid4().hex,
        },
    )
