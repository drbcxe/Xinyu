import time
from collections.abc import Callable
from functools import lru_cache
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.errors import AppError
from app.services.resource_control import (
    ensure_active,
    estimate_input,
    provider_slot,
    reserve_call,
    settle_call,
)


@lru_cache
def model_http_client() -> httpx.Client:
    concurrency = max(4, get_settings().global_model_concurrency)
    return httpx.Client(
        trust_env=False,
        limits=httpx.Limits(
            max_connections=concurrency, max_keepalive_connections=concurrency
        ),
    )


def controlled_post(
    base_url: str, headers: dict, payload: dict, timeout: float, deadline: float
) -> httpx.Response:
    with provider_slot(deadline):
        call_id, _ = reserve_call(
            str(payload["model"]),
            estimate_input(payload["messages"], payload.get("tools", [])),
            int(payload["max_tokens"]),
        )
        usage, sent, rejected = None, False, False
        try:
            ensure_active()
            sent = True
            response = model_http_client().post(
                base_url + "/chat/completions",
                headers=headers,
                json=payload,
                timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
            )
            rejected = 400 <= response.status_code < 500
            if response.is_success:
                try:
                    raw = response.json().get("usage")
                    if isinstance(raw, dict):
                        usage = {
                            key: value
                            for key, value in raw.items()
                            if key
                            in {"prompt_tokens", "completion_tokens", "total_tokens"}
                            and type(value) is int
                            and value >= 0
                        }
                except (ValueError, AttributeError):
                    pass
            return response
        finally:
            settle_call(call_id, usage=usage, sent=sent, rejected=rejected)


def require_model_config(
    base_url: str | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
) -> None:
    settings = get_settings()
    try:
        url = urlsplit((base_url or settings.model_base_url).strip())
        valid = (
            url.scheme in {"http", "https"}
            and url.hostname
            and not url.username
            and not url.password
        )
    except ValueError:
        valid = False
    if (
        not valid
        or not (model_name or settings.model_name).strip()
        or (api_key or settings.model_api_key.get_secret_value()).strip()
        in {"", "replace_me"}
    ):
        raise AppError(
            503, "MODEL_NOT_CONFIGURED", "请先在后端 .env 填写模型地址、名称和 API Key"
        )


def complete(
    messages: list[dict],
    tools: list[dict],
    deadline: float,
    retry_budget: int,
    on_retry: Callable[[int], None],
    model_override: str | None = None,
    base_url_override: str | None = None,
    api_key_override: str | None = None,
) -> tuple[dict, dict | None, int]:
    require_model_config(base_url_override, model_override, api_key_override)
    settings = get_settings()
    base_url = (base_url_override or settings.model_base_url).strip().rstrip("/")
    payload = {
        "model": model_override or settings.model_name.strip(),
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 1,
        "max_tokens": settings.model_max_tokens,
        "stream": False,
    }
    if not tools:
        payload.pop("tools")
        payload.pop("tool_choice")
    effective_model = str(payload["model"]).lower()
    if (
        urlsplit(base_url).hostname == "open.bigmodel.cn"
        and effective_model.startswith("glm-")
        and (not model_override or effective_model.startswith("glm-5.3"))
    ):
        payload["thinking"] = {"type": "enabled", "clear_thinking": False}
        if effective_model.startswith("glm-5.3"):
            payload["reasoning_effort"] = "low"
    headers = {
        "Authorization": "Bearer "
        + (api_key_override or settings.model_api_key.get_secret_value()).strip()
    }
    for attempt in range(retry_budget + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AppError(
                504, "AGENT_TIMEOUT", "本轮执行超过时间预算，请查看已完成的工具记录"
            )
        timeout = min(settings.model_timeout_seconds, remaining)
        try:
            response = controlled_post(base_url, headers, payload, timeout, deadline)
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt < retry_budget and deadline - time.monotonic() > 2:
                on_retry(attempt + 1)
                time.sleep(min(2**attempt, 2))
                continue
            raise AppError(
                502,
                "MODEL_CONNECTION_ERROR",
                "模型服务连接失败或超时，请检查模型平台与网络",
            ) from None
        if (
            response.status_code in {429, 500, 502, 503, 504}
            and attempt < retry_budget
            and deadline - time.monotonic() > 2
        ):
            on_retry(attempt + 1)
            time.sleep(min(2**attempt, 2))
            continue
        if not response.is_success:
            code, message = {
                401: ("MODEL_AUTH_ERROR", "模型 API Key 无效或已失效，请检查后端配置"),
                403: (
                    "MODEL_PERMISSION_ERROR",
                    "当前 Key 无权调用这个模型，请检查模型平台权限",
                ),
                429: (
                    "MODEL_RATE_LIMIT",
                    "模型平台限流或配额不足，请检查账户额度后重试",
                ),
                400: (
                    "MODEL_REQUEST_REJECTED",
                    "模型平台拒绝了请求，请确认模型名称和工具调用支持",
                ),
                404: ("MODEL_NOT_FOUND", "模型或 API 地址不存在，请检查后端模型配置"),
            }.get(
                response.status_code,
                ("MODEL_SERVICE_ERROR", "模型平台暂时无法处理请求"),
            )
            raise AppError(502, code, message)
        try:
            body = response.json()
            choice = body["choices"][0]
            raw = choice["message"]
            if choice.get("finish_reason") == "length":
                raise AppError(
                    502,
                    "MODEL_OUTPUT_LIMIT",
                    "模型输出达到本轮长度上限，未获得完整答复；可缩短问题后重试",
                )
            if not isinstance(raw, dict) or raw.get("role") != "assistant":
                raise ValueError("Invalid model message")
            message = {
                key: raw[key]
                for key in (
                    "role",
                    "content",
                    "tool_calls",
                    "reasoning_content",
                    "reasoning_details",
                )
                if key in raw
            }
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                raise TypeError("Invalid tool calls")
            for call in calls:
                if (
                    not isinstance(call.get("id"), str)
                    or not call["id"]
                    or len(call["id"]) > 200
                    or not isinstance(call.get("function"), dict)
                ):
                    raise ValueError("Invalid tool call")
            if not calls and not isinstance(message.get("content"), str):
                raise ValueError("Missing assistant content")
            usage = body.get("usage")
            clean_usage = (
                {
                    key: value
                    for key, value in usage.items()
                    if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                    and isinstance(value, int)
                }
                if isinstance(usage, dict)
                else None
            )
            return message, clean_usage, attempt
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AppError(
                502, "MODEL_INVALID_RESPONSE", "模型返回格式不符合工具调用约定"
            ) from None
    raise AppError(502, "MODEL_SERVICE_ERROR", "模型暂时不可用")
