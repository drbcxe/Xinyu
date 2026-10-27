import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.errors import AppError
from app.repositories.database import get_engine
from app.services import relationship_agent as agent
from app.services.relationship_jobs import process_one
from tests.test_job_reliability import create_job, job_state
from tests.test_resource_control import owner


def no_retrieval(*_, **__):
    return {"sources": [], "mode": "empty", "warnings": [], "milliseconds": 0}


def response(count):
    return (
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "analysis": "synthetic",
                    "replies": ["independent alternative"] * count,
                    "source_ids": [],
                }
            ),
        },
        {"total_tokens": 10},
        0,
    )


def test_invalid_reply_count_gets_specific_feedback_and_repairs(backend, monkeypatch):
    value = owner()
    calls = []

    def model(messages, *_, **__):
        calls.append(messages)
        if len(calls) == 1:
            return response(4)
        assert "replies" in messages[-1]["content"] and "3" in messages[-1]["content"]
        return response(3)

    monkeypatch.setattr(agent, "complete", model)
    monkeypatch.setattr(agent, "retrieve", no_retrieval)
    with Session(get_engine()) as db:
        result = agent.run_agent(db, value, "synthetic", request_id=str(uuid4()))
    assert len(result["replies"]) == 3 and result["execution"]["model_steps"] == 2


def test_failed_format_has_bounded_repairs_and_manual_retry_starts_fresh(
    backend, monkeypatch
):
    value = owner()
    request_id = str(uuid4())
    calls = []

    def invalid(*_, **__):
        calls.append(1)
        return response(4)

    monkeypatch.setattr(agent, "complete", invalid)
    monkeypatch.setattr(agent, "retrieve", no_retrieval)
    with Session(get_engine()) as db, pytest.raises(AppError) as failure:
        agent.run_agent(db, value, "synthetic", request_id=request_id)
    assert failure.value.code == "ADVICE_INVALID" and len(calls) == 3
    monkeypatch.setattr(agent, "complete", lambda *_, **__: response(2))
    with Session(get_engine()) as db:
        result = agent.run_agent(db, value, "synthetic", request_id=request_id)
    assert (
        result["execution"]["model_steps"] == 1 and not result["execution"]["resumed"]
    )


def test_permanent_model_auth_failure_does_not_automatically_retry(
    backend, monkeypatch
):
    _, job_id, _ = create_job()
    from app.services import relationship_jobs

    def broken(*_, **__):
        raise AppError(502, "MODEL_AUTH_ERROR", "synthetic")

    monkeypatch.setattr(relationship_jobs, "run_agent", broken)
    assert process_one()
    assert (
        job_state(job_id)["status"] == "failed" and job_state(job_id)["attempts"] == 1
    )
