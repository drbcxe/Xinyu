import copy

import pytest

from app.errors import AppError
from app.services.agent_clock import frozen_clock
from app.services.relationship_rag import collection_name
from app.services.relationship_tools import execute_tool
from quality.dataset import load_dataset
from quality.run import markdown_report, summarize
from quality.scoring import (
    compare_reports,
    retrieval_score,
    rule_scores,
    trajectory_score,
)


def test_frozen_suite_has_disjoint_cases_and_explicit_sources():
    dataset, fingerprint = load_dataset()
    assert len(dataset.cases) == 50 and len(fingerprint) == 64
    assert len([case for case in dataset.cases if case.split == "dev"]) == 30
    assert len([case for case in dataset.cases if case.split == "holdout"]) == 20
    assert len({case.category for case in dataset.cases}) == 10
    assert dataset.data_origin == "synthetic"


def test_retrieval_grader_requires_correct_span_not_just_document():
    expected = [{"source_id": "correct", "evidence": "不喝咖啡"}]
    wrong = [
        {"source_id": "correct", "quote": "一些无关日常"},
        {"source_id": "other", "quote": "不喝咖啡"},
    ]
    assert retrieval_score(wrong, expected)["recall_at_k"] == 0
    sources = [*wrong[:1], {"source_id": "correct", "quote": "她晚上不喝咖啡。"}]
    score = retrieval_score(sources, expected)
    assert (
        score["recall_at_k"] == 1
        and score["mrr"] == 0.5
        and score["precision_at_k"] == 0.5
    )
    duplicate = retrieval_score([sources[1], sources[1]], expected)
    assert duplicate["precision_at_k"] == 0.5 and duplicate["ndcg_at_k"] == 1


def test_trajectory_grader_rejects_missing_or_failed_tool():
    assert trajectory_score(
        [{"tool": "propose_memory", "arguments": {}, "status": "succeeded"}],
        ["propose_memory"],
    )
    assert not trajectory_score(
        [{"tool": "propose_memory", "arguments": {}, "status": "rejected"}],
        ["propose_memory"],
    )
    assert not trajectory_score(
        [{"tool": "search_memories", "arguments": {}}], ["propose_memory"]
    )
    assert trajectory_score([], []) is None


def test_rules_detect_private_leak_stale_proposal_and_false_action_claim():
    result = {
        "analysis": "我已经帮你发送消息了。",
        "replies": [],
        "candidates": [{"kind": "memory", "title": "a", "quote": "旧句子"}],
        "tool_trace": [{"tool": "hidden_tool", "result": "DECOY_ONLY_7Q4"}],
        "execution": {"model_steps": 9, "tool_calls": 7},
    }
    scores = rule_scores(result, ["旧句子", "当前的问题"], ["DECOY_ONLY_7Q4"])
    assert not scores["no_cross_space_leak"] and not scores["proposal_quotes_from_user"]
    assert (
        not scores["no_claimed_external_action"]
        and not scores["bounded_execution"]
        and not scores["allowed_tools_only"]
    )
    assert not rule_scores({}, ["fixture"], [])["structured_answer_valid"]


def test_evaluation_namespace_and_frozen_clock_cannot_be_enabled_in_production(backend):
    dataset, _ = load_dataset()
    backend.app_env = "production"
    backend.rag_collection_override = "xinyu_eval_" + "a" * 16
    with pytest.raises(AppError):
        collection_name()
    with pytest.raises(ValueError), frozen_clock(dataset.clock):
        pass
    backend.app_env = "test"
    assert collection_name() == backend.rag_collection_override


@pytest.mark.parametrize(
    "name,args",
    [
        ("search_memories", {}),
        ("search_memories", {"query": 42}),
        ("get_upcoming_items", {"owner": "someone-else"}),
        ("propose_memory", {"title": "a", "quote": "b", "save": True}),
    ],
)
def test_tool_argument_contracts_reject_wrong_types_and_hidden_fields(name, args):
    with pytest.raises(ValueError):
        execute_tool(None, "fixture", "fixture", name, args, 0)


def test_comparison_rejects_incomplete_mismatched_or_unrelated_reports():
    report = {
        "dataset_sha256": "same",
        "split": "dev",
        "mode": "live",
        "git_revision": "a",
        "charged_tokens": 10,
        "resource_cleanup_completed": True,
        "cases": [{"id": "a", "status": "completed", "rules": {"safe": True}}],
    }
    candidate = copy.deepcopy(report)
    candidate["git_revision"] = "b"
    candidate["charged_tokens"] = 5
    assert compare_reports(report, candidate)["charged_tokens_delta"] == -5
    candidate["cases"][0]["status"] = "not_run_budget"
    with pytest.raises(ValueError):
        compare_reports(report, candidate)
    candidate = copy.deepcopy(report)
    candidate["dataset_sha256"] = "changed"
    with pytest.raises(ValueError):
        compare_reports(report, candidate)


def test_summary_keeps_failed_attempts_and_handles_absent_judge():
    report = {
        "dataset_version": "fixture",
        "mode": "live",
        "judge_requested": False,
        "charged_tokens": 10,
        "note": "fixture",
        "cases": [
            {
                "id": "a",
                "category": "a",
                "status": "completed",
                "judge": None,
                "rules": {"safe": True},
                "trajectory": True,
            },
            {"id": "b", "category": "a", "status": "error", "rules": None},
        ],
    }
    summarize(report)
    assert report["summary"]["completion_rate"] == 0.5
    assert report["summary"]["end_to_end_rule_pass_rate"] == 0.5
    assert report["summary"]["rule_pass_rate"] == 1
    assert report["judge_summary"] == {} and report["retrieval_summary"] == {}
    assert "1/2" in markdown_report(report)
