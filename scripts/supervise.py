"""Local launcher: drain child output as bounded metadata, never persist raw stdout/stderr."""

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.config import PROJECT_ROOT
from app.services.observability import configure_logging


def command_for(role, node=None):
    if role == "api":
        return [
            sys.executable,
            "-u",
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ], PROJECT_ROOT
    if role == "worker":
        return [sys.executable, "-u", "-m", "scripts.worker"], PROJECT_ROOT
    script = PROJECT_ROOT / "admin-ui" / "node_modules" / "vite" / "bin" / "vite.js"
    if role != "web" or not node or not Path(node).is_file() or not script.is_file():
        raise ValueError("Local web runtime is unavailable")
    return [str(node), str(script), "--host", "localhost"], PROJECT_ROOT / "admin-ui"


def supervise(role, command, cwd):
    configure_logging("supervisor-" + role)
    logger = logging.getLogger("scripts.supervise")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **options,
    ) as child:
        logger.info("process_started", extra={"role": role})

        def drain(stream, name):
            count, reported = 0, time.monotonic()
            while data := stream.read1(8192):
                count += len(data)
                if time.monotonic() - reported >= 1:
                    logger.info(
                        "process_output",
                        extra={"role": role, "stream": name, "bytes": count},
                    )
                    count, reported = 0, time.monotonic()
            if count:
                logger.info(
                    "process_output",
                    extra={"role": role, "stream": name, "bytes": count},
                )

        threads = [
            threading.Thread(target=drain, args=(stream, name), daemon=True)
            for stream, name in [(child.stdout, "stdout"), (child.stderr, "stderr")]
        ]
        for thread in threads:
            thread.start()
        try:
            code = child.wait()
        except BaseException:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            raise
        finally:
            for thread in threads:
                thread.join(timeout=2)
        logger.info("process_exited", extra={"role": role, "exit_code": code})
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["api", "worker", "web"])
    parser.add_argument("--node")
    args = parser.parse_args()
    try:
        command, cwd = command_for(args.role, args.node)
        code = supervise(args.role, command, cwd)
    except Exception as exc:  # noqa: BLE001 - launcher diagnostics must never dump environment/secrets
        print(
            json.dumps(
                {
                    "event": "supervisor_failed",
                    "role": args.role,
                    "error_type": type(exc).__name__,
                }
            ),
            file=sys.stderr,
        )
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
