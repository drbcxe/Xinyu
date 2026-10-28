"""Synthetic-only OpenAI protocol peer for repeatable local failure/load probes."""

import json
import re
import threading
import time
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ProviderState:
    def __init__(self, delay):
        self.delay = delay
        self.lock = threading.Lock()
        self.active = 0
        self.peak_active = 0
        self.calls = Counter()
        self.main_attempts = Counter()
        self.entered = set()

    def snapshot(self):
        with self.lock:
            return {"calls": dict(self.calls), "peak_concurrent": self.peak_active}

    def has_entered(self, key):
        with self.lock:
            return key in self.entered

    def respond(self, payload):
        messages = payload["messages"]
        text = next(
            (
                row["content"]
                for row in reversed(messages)
                if row["role"] == "user"
                and isinstance(row.get("content"), str)
                and row["content"].startswith("[probe:")
            ),
            "",
        )
        match = re.search(
            r"\[probe:([a-f0-9]{32}):(normal|retry503|deny401|slow|malformed)\]", text
        )
        key, mode = match.groups() if match else ("rerank", "normal")
        rerank = not payload.get("tools")
        stage = (
            "rerank"
            if rerank
            else "answer"
            if any(row["role"] == "tool" for row in messages)
            else "tool"
        )
        with self.lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            self.calls[stage] += 1
            if not rerank:
                self.main_attempts[key] += 1
                attempt = self.main_attempts[key]
                self.entered.add(key)
            else:
                attempt = 0
        try:
            time.sleep(
                max(self.delay, 1.2) if mode == "slow" and not rerank else self.delay
            )
            if not rerank and (
                mode == "deny401" or mode == "retry503" and attempt == 1
            ):
                status = 401 if mode == "deny401" else 503
                with self.lock:
                    self.calls["http_" + str(status)] += 1
                return status, {"error": {"message": "synthetic protocol fault"}}
            if rerank:
                sources = json.loads(messages[-1]["content"])["sources"]
                message = {
                    "role": "assistant",
                    "content": json.dumps(list(range(min(8, len(sources))))),
                }
            elif mode == "malformed":
                message = {"role": "assistant", "content": "synthetic invalid JSON"}
            elif stage == "tool":
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "probe_" + key,
                            "type": "function",
                            "function": {
                                "name": "search_chat_history",
                                "arguments": json.dumps(
                                    {"query": "蓝色杯子 散步"}, ensure_ascii=False
                                ),
                            },
                        }
                    ],
                }
            else:
                output = json.loads(
                    next(
                        row["content"]
                        for row in reversed(messages)
                        if row["role"] == "tool"
                    )
                )
                citations = [
                    row["citation_id"] for row in output.get("excerpts", [])[:1]
                ]
                message = {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "analysis": "这是协议压测的合成回复，不能用于沟通质量评估。",
                            "uncertainty": "",
                            "replies": ["我们可以一起散步，再聊聊今天的感受。"],
                            "source_ids": citations,
                            "date_plans": [],
                        },
                        ensure_ascii=False,
                    ),
                }
            return 200, {
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "tool_calls"
                        if message.get("tool_calls")
                        else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "total_tokens": 100,
                },
            }
        finally:
            with self.lock:
                self.active -= 1


@contextmanager
def local_provider(delay=0.15):
    state = ProviderState(delay)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2 * 1024**2:
                self.send_error(413)
                return
            try:
                payload = json.loads(self.rfile.read(length))
                if payload.get("model") != "local-synthetic-probe":
                    raise ValueError("Unexpected model")
                status, response = state.respond(payload)
                body = json.dumps(response, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # Expected when the owned Worker is deliberately killed mid-request.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
