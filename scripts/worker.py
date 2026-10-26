"""Run with: uv run python -m scripts.worker (independent from FastAPI)."""

import concurrent.futures
import time

from app.config import PROJECT_ROOT
from app.services.relationship_jobs import dispatch_reminders, process_one
from app.services.relationship_rag import process_index


def heartbeat(name: str):
    folder = PROJECT_ROOT / ".runtime" / "workers"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).touch()


def main():
    from app.services.relationship_weixin import deliver_notices, poll_bindings

    print(
        "Xinyu worker started: durable advice jobs, reminders and optional Weixin channel"
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        future = None
        channel_future = None
        index_future = None
        while True:
            try:
                heartbeat("reminder.heartbeat")
                dispatch_reminders()
                deliver_notices()
                if index_future is None or index_future.done():
                    if index_future is not None:
                        completed_index = index_future
                        index_future = None
                        completed_index.result()
                    index_future = pool.submit(process_index)
                if channel_future is None or channel_future.done():
                    if channel_future is not None:
                        completed_channel = channel_future
                        channel_future = None
                        completed_channel.result()
                    channel_future = pool.submit(poll_bindings)
                if future is None or future.done():
                    if future is not None:
                        completed_job = future
                        future = None
                        completed_job.result()
                    future = pool.submit(process_one)
                time.sleep(2)
            except KeyboardInterrupt:
                break
            except Exception as exc:  # noqa: BLE001 - isolate failures at worker boundary
                print(f"worker iteration failed: {type(exc).__name__}")
                time.sleep(5)


if __name__ == "__main__":
    main()
