"""Run with: uv run python -m scripts.worker (independent from FastAPI)."""

import concurrent.futures
import time
from uuid import uuid4

from app.config import get_settings
from app.services.account_closure import reconcile_closed_spaces
from app.services.account_email import process_email
from app.services.relationship_jobs import dispatch_reminders, process_one
from app.services.relationship_rag import process_index
from app.services.resource_control import reconcile_abandoned_calls
from app.services.runtime_state import remove_heartbeat, write_heartbeat


def main():
    from app.services.relationship_weixin import deliver_notices, poll_bindings

    print(
        "Xinyu worker started: durable advice jobs, reminders and optional Weixin channel"
    )
    concurrency = get_settings().worker_advice_concurrency
    worker_id = str(uuid4())
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency + 3) as pool:
        futures = []
        channel_future = None
        index_future = None
        email_future = None
        reconciled_at = 0
        while True:
            try:
                write_heartbeat(worker_id)
                dispatch_reminders()
                if email_future is None or email_future.done():
                    if email_future is not None:
                        completed_email = email_future
                        email_future = None
                        completed_email.result()
                    email_future = pool.submit(process_email)
                if time.monotonic() - reconciled_at > 60:
                    reconcile_abandoned_calls()
                    reconcile_closed_spaces()
                    reconciled_at = time.monotonic()
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

                    def channel_work():
                        deliver_notices()
                        poll_bindings()

                    channel_future = pool.submit(channel_work)
                completed = [future for future in futures if future.done()]
                futures = [future for future in futures if not future.done()]
                for future in completed:
                    future.result()
                while len(futures) < concurrency:
                    futures.append(pool.submit(process_one))
                time.sleep(2)
            except KeyboardInterrupt:
                remove_heartbeat(worker_id)
                break
            except Exception as exc:  # noqa: BLE001 - isolate failures at worker boundary
                print(f"worker iteration failed: {type(exc).__name__}")
                time.sleep(5)


if __name__ == "__main__":
    main()
