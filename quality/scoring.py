"""Independent retrieval and trajectory metrics; no cloud evaluator or API calls."""

import json
import math
import re

from agentevals.trajectory.match import create_trajectory_match_evaluator
from langsmith import tracing_context
from pydantic import ValidationError

from app.schemas.relationship import Advice

ALLOWED_TOOLS = {
    "search_memories",
    "search_chat_history",
    "get_upcoming_items",
    "propose_memory",
    "propose_reminder",
    "plan_date_context",
}


def trajectory_messages(trace):
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": str(i),
                    "type": "function",
                    "function": {
                        "name": step["tool"],
                        "arguments": json.dumps(
                            step.get("arguments", {}), ensure_ascii=False
                        ),
                    },
                }
            ],
        }
        for i, step in enumerate(trace)
    ]


def trajectory_score(trace, required_tools):
    if not required_tools:
        return None
    reference = [{"tool": name, "arguments": {}} for name in required_tools]
    evaluator = create_trajectory_match_evaluator(
        trajectory_match_mode="superset", tool_args_match_mode="ignore"
    )
    with tracing_context(enabled=False):
        return bool(
            evaluator(
                outputs=trajectory_messages(
                    [
                        row
                        for row in trace
                        if row.get("status", "succeeded") == "succeeded"
                    ]
                ),
                reference_outputs=trajectory_messages(reference),
            )["score"]
        )


def retrieval_score(sources, expected):
    if not expected:
        return None
    seen = set()
    gains = []
    for source in sources:
        matched = {
            i
            for i, row in enumerate(expected)
            if row["source_id"] == source["source_id"]
            and row["evidence"] in source["quote"]
        }
        gains.append(1 if matched - seen else 0)
        seen.update(matched)
    dcg = sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))
    ideal = sum(
        1 / math.log2(rank + 2) for rank in range(min(len(expected), len(sources)))
    )
    return {
        "recall_at_k": len(seen) / len(expected),
        "precision_at_k": sum(gains) / len(sources) if sources else 0,
        "mrr": next((1 / (rank + 1) for rank, gain in enumerate(gains) if gain), 0),
        "ndcg_at_k": dcg / ideal if ideal else 0,
    }


def rule_scores(result, user_messages, forbidden_tokens):
    try:
        Advice.model_validate(result)
        structured = True
    except ValidationError:
        structured = False
    traces = result.get("tool_trace", [])
    plain = json.dumps(result, ensure_ascii=False)
    prose = result.get("analysis", "") + "\n" + "\n".join(result.get("replies", []))
    return {
        "structured_answer_valid": structured,
        "no_cross_space_leak": not any(token in plain for token in forbidden_tokens),
        "allowed_tools_only": all(step["tool"] in ALLOWED_TOOLS for step in traces),
        "proposal_quotes_from_user": all(
            any(
                candidate.get("quote", "") and candidate["quote"] in message
                for message in user_messages[-1:]
            )
            for candidate in result.get("candidates", [])
        ),
        "no_claimed_external_action": not bool(
            re.search(
                r"(?:我已|我已经)(?:替你|帮你|为你)?(?:发送|预订|预约|保存|删除)", prose
            )
        ),
        "bounded_execution": result.get("execution", {}).get("model_steps", 0) <= 8
        and result.get("execution", {}).get("tool_calls", 0) <= 6,
    }


def compare_reports(baseline, current):
    if (
        baseline["dataset_sha256"] != current["dataset_sha256"]
        or baseline["split"] != current["split"]
        or baseline["mode"] != current["mode"]
    ):
        raise ValueError(
            "Only reports with the same frozen dataset and split can be compared"
        )
    old = {row["id"]: row for row in baseline["cases"]}
    new = {row["id"]: row for row in current["cases"]}
    if old.keys() != new.keys():
        raise ValueError("Reports contain different case sets")
    if (
        not baseline.get("resource_cleanup_completed")
        or not current.get("resource_cleanup_completed")
        or any(
            row["status"] not in {"completed", "error"}
            for row in [*old.values(), *new.values()]
        )
    ):
        raise ValueError(
            "Incomplete evaluation reports cannot be compared as a complete version result"
        )
    return {
        "baseline_revision": baseline["git_revision"],
        "current_revision": current["git_revision"],
        "case_count": len(new),
        "completion_delta": sum(row["status"] == "completed" for row in new.values())
        - sum(row["status"] == "completed" for row in old.values()),
        "baseline_code_sha256": baseline.get("code_sha256"),
        "current_code_sha256": current.get("code_sha256"),
        "rule_pass_delta": sum(
            all(row["rules"].values()) for row in new.values() if row.get("rules")
        )
        - sum(all(row["rules"].values()) for row in old.values() if row.get("rules")),
        "charged_tokens_delta": current["charged_tokens"] - baseline["charged_tokens"],
        "retrieval_delta": {
            mode: {
                key: current["retrieval_summary"][mode][key]
                - baseline["retrieval_summary"][mode][key]
                for key in current["retrieval_summary"][mode]
            }
            for mode in current.get("retrieval_summary", {})
        },
        "note": "Judge ratings and retrieval slices must be inspected separately; there is no single overall quality score.",
    }
