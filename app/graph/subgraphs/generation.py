import json
import time

from langgraph.runtime import Runtime
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.graph.state import AgentContext, AgentState
from app.graph.tools.experiments import ARGUMENT_SCHEMAS, TOOL_DEFINITIONS, execute_tool
from app.models.chat import AgentRun
from app.providers import model_provider
from app.repositories.database import get_engine
from app.services import memory_service
from app.services.run_event_service import append_event


def call_model(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    ctx = runtime.context
    if state["model_steps"] >= 8:
        raise AppError(422, "MODEL_STEP_LIMIT", "本轮达到模型调用次数上限")
    append_event(
        ctx.run_id,
        "model.started",
        {"model_name": get_settings().model_name, "step": state["model_steps"] + 1},
    )
    started = time.monotonic()
    with Session(get_engine()) as db:
        preferences = memory_service.prompt_memories(db, ctx.project_id)
    messages = list(state["messages"])
    messages[0] = {
        **messages[0],
        "content": messages[0]["content"]
        + "\n最新项目偏好（仅 is_active=true 生效）：\n"
        + json.dumps(preferences, ensure_ascii=False),
    }
    message, usage, retries = model_provider.complete(
        messages,
        TOOL_DEFINITIONS,
        ctx.deadline,
        max(0, 2 - state["retry_count"]),
        lambda attempt: append_event(
            ctx.run_id, "model.retry", {"attempt": state["retry_count"] + attempt}
        ),
    )
    calls = message.get("tool_calls") or []
    if state["tool_steps"] + len(calls) > 6:
        raise AppError(422, "TOOL_LIMIT", "本轮已达到最多 6 次工具调用的限制")
    if not calls and not (message.get("content") or "").strip():
        raise AppError(502, "MODEL_EMPTY_REPLY", "模型未返回可显示的答复")
    append_event(
        ctx.run_id,
        "model.finished",
        {
            "duration_ms": round((time.monotonic() - started) * 1000),
            "tool_calls": len(calls),
            "usage": usage,
        },
    )
    return {
        "messages": [*state["messages"], message],
        "model_steps": state["model_steps"] + 1,
        "retry_count": state["retry_count"] + retries,
        "usage": [*state["usage"], *([usage] if usage else [])],
        "reply": message.get("content") or "" if not calls else "",
    }


def call_tools(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    ctx = runtime.context
    messages = list(state["messages"])
    evidence = list(state["evidence"])
    selected = state["selected_run_ids"]
    seen = set(state.get("seen_tool_ids", []))
    calls = messages[-1]["tool_calls"]
    for call in calls:
        if time.monotonic() >= ctx.deadline:
            raise AppError(504, "AGENT_TIMEOUT", "本轮执行超过时间预算")
        call_id = call["id"]
        if call_id in seen:
            raise AppError(
                502, "REUSED_TOOL_CALL_ID", "模型重复使用了工具调用编号，本轮已停止"
            )
        seen.add(call_id)
        function = call["function"]
        name = str(function.get("name", ""))[:100]
        arguments = function.get("arguments", "{}")
        append_event(
            ctx.run_id, "tool.started", {"tool": name, "tool_call_id": call_id}
        )
        try:
            schema = ARGUMENT_SCHEMAS.get(name)
            if schema is None:
                raise AppError(422, "UNKNOWN_TOOL", "模型请求的工具不在允许列表中")
            if not isinstance(arguments, str) or len(arguments) > 8000:
                raise AppError(
                    422, "INVALID_TOOL_ARGUMENTS", "工具参数格式或长度不符合要求"
                )
            args = schema.model_validate(json.loads(arguments or "{}"))
            with Session(get_engine(), expire_on_commit=False) as db:
                output, comparison = execute_tool(db, ctx, name, args, call_id)
            if comparison is not None:
                if not any(item["id"] == comparison["id"] for item in evidence):
                    evidence.append(comparison)
                selected = [
                    comparison["baseline"]["run_id"],
                    comparison["candidate"]["run_id"],
                ]
                with Session(get_engine()) as db:
                    db.execute(
                        update(AgentRun)
                        .where(AgentRun.id == ctx.run_id)
                        .values(evidence=evidence)
                    )
                    db.commit()
        except AppError as error:
            output = {
                "ok": False,
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                },
            }
        except (ValidationError, ValueError, TypeError):
            output = {
                "ok": False,
                "error": {
                    "code": "INVALID_TOOL_ARGUMENTS",
                    "message": "工具参数校验失败，请按工具定义重新提供参数",
                },
            }
        text = json.dumps(output, ensure_ascii=False)
        if len(text) > 24_000:
            output = {
                "ok": False,
                "error": {
                    "code": "TOOL_OUTPUT_LIMIT",
                    "message": "工具结果过长，请减小 list_runs 的 limit 或缩小问题范围",
                },
            }
            text = json.dumps(output, ensure_ascii=False)
        append_event(
            ctx.run_id,
            "tool.finished",
            {
                "tool": name,
                "tool_call_id": call_id,
                "ok": output["ok"],
                "summary": f"返回 {len(output.get('items', []))} 条实验"
                if name == "list_runs" and output["ok"]
                else "比较结果已保存"
                if name == "compare_runs" and output["ok"]
                else "项目偏好已保存，可跨会话使用"
                if name == "revise_memory" and output["ok"]
                else "已读取项目偏好与版本"
                if name == "get_project_preferences" and output["ok"]
                else "已读取项目资料"
                if output["ok"]
                else output["error"]["message"],
                "error": output.get("error"),
            },
        )
        messages.append({"role": "tool", "tool_call_id": call_id, "content": text})
    return {
        "messages": messages,
        "tool_steps": state["tool_steps"] + len(calls),
        "evidence": evidence,
        "selected_run_ids": selected,
        "seen_tool_ids": list(seen),
    }
