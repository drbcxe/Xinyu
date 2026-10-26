from dataclasses import dataclass
from typing import TypedDict
from uuid import UUID


class AgentState(TypedDict, total=False):
    input: str
    messages: list[dict]
    artifact_id: str | None
    tool_steps: int
    model_steps: int
    retry_count: int
    evidence: list[dict]
    selected_run_ids: list[str]
    usage: list[dict]
    reply: str
    seen_tool_ids: list[str]


@dataclass(frozen=True)
class AgentContext:
    project_id: str
    thread_id: UUID
    run_id: UUID
    artifact_id: UUID | None
    deadline: float
