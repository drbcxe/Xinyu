import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx
import psutil
import pytest

from app.config import PROJECT_ROOT
from quality.local_provider import local_provider
from scripts import benchmark_pipeline
from scripts.benchmark_pipeline import stop_owned


def test_local_protocol_faults_and_tool_answer_use_current_question_not_history():
    old_key, current = uuid4().hex, uuid4().hex
    with (
        local_provider(delay=0) as (base, state),
        httpx.Client(trust_env=False) as client,
    ):
        payload = {
            "model": "local-synthetic-probe",
            "tools": [{"type": "function"}],
            "messages": [
                {
                    "role": "user",
                    "content": "Earlier background: " + f"[probe:{old_key}:deny401]",
                },
                {"role": "user", "content": f"[probe:{current}:retry503] synthetic"},
            ],
        }
        assert client.post(base + "/chat/completions", json=payload).status_code == 503
        response = client.post(base + "/chat/completions", json=payload)
        assert response.status_code == 200
        message = response.json()["choices"][0]["message"]
        assert message["tool_calls"][0]["function"]["name"] == "search_chat_history"
        payload["messages"].extend(
            [
                message,
                {
                    "role": "tool",
                    "tool_call_id": message["tool_calls"][0]["id"],
                    "content": json.dumps({"excerpts": [{"citation_id": "Rfixture"}]}),
                },
            ]
        )
        answer = client.post(base + "/chat/completions", json=payload).json()
        assert json.loads(answer["choices"][0]["message"]["content"])["source_ids"] == [
            "Rfixture"
        ]
        assert state.has_entered(current) and not state.has_entered(old_key)
        rerank = {
            "model": "local-synthetic-probe",
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        {"sources": [{"index": 0, "text": "synthetic"}]}
                    ),
                }
            ],
        }
        assert (
            client.post(base + "/chat/completions", json=rerank).json()["choices"][0][
                "message"
            ]["content"]
            == "[0]"
        )


def test_probe_shutdown_kills_only_its_owned_python_process_tree():
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    program = "import subprocess, sys, time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"
    with subprocess.Popen(
        [sys.executable, "-u", "-c", program],
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        **options,
    ) as child:
        descendant = int(child.stdout.readline().strip())
        parent = psutil.Process()
        assert psutil.pid_exists(descendant)
        try:
            stop_owned(child)
            assert child.poll() is not None
            assert (
                not psutil.pid_exists(descendant)
                or psutil.Process(descendant).status() == psutil.STATUS_ZOMBIE
            )
            assert parent.is_running()
        finally:
            stop_owned(child)


def test_probe_child_rejects_business_database_even_with_test_flag(monkeypatch):
    from app.config import get_settings

    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "rag_collection_override": "xinyu_eval_" + "a" * 16,
            "database_url": "postgresql+psycopg://fixture@localhost/business",
            "model_name": "local-synthetic-probe",
            "model_base_url": "http://127.0.0.1:1/v1",
        }
    )
    monkeypatch.setattr(benchmark_pipeline, "get_settings", lambda: settings)
    parent = PROJECT_ROOT / ".runtime" / "benchmarks"
    parent.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="probe-", dir=parent) as folder,
        pytest.raises(RuntimeError, match="isolated test configuration"),
    ):
        benchmark_pipeline.child_runtime("worker", Path(folder), 0)
