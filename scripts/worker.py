"""Run with: uv run python -m scripts.worker (independent from FastAPI)."""

import concurrent.futures
import logging
import time
from uuid import uuid4

from app.config import get_settings
from app.services.account_closure import reconcile_closed_spaces
from app.services.account_email import process_email
from app.services.data_retention import prune_online_data
from app.services.observability import configure_logging
from app.services.ops_status import operational_status
from app.services.relationship_jobs import dispatch_reminders, process_one
from app.services.relationship_rag import process_index
from app.services.resource_control import reconcile_abandoned_calls
from app.services.retrieval_cache import corpus_cache
from app.services.runtime_state import remove_heartbeat, write_heartbeat
from app.services.vector_capacity import audit_vectors_if_due


def main():
    from app.services.relationship_weixin import deliver_notices, poll_bindings

    configure_logging("worker")
    logger = logging.getLogger(__name__)
    logger.info("worker_started")
    concurrency = get_settings().worker_advice_concurrency
    worker_id = str(uuid4())
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency + 4) as pool:
        futures = []
        channel_future = None
        index_future = None
        email_future = None
        maintenance_future = None
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
                if maintenance_future is not None and maintenance_future.done():
                    finished_maintenance = maintenance_future
                    maintenance_future = None
                    try:
                        finished_maintenance.result()
                    except Exception as exc:  # noqa: BLE001 - keep unrelated dispatchers alive
                        logger.warning(
                            "maintenance_failed",
                            extra={"error_type": type(exc).__name__},
                        )
                if maintenance_future is None and time.monotonic() - reconciled_at > 60:

                    def maintenance():
                        for action in (
                            reconcile_abandoned_calls,
                            reconcile_closed_spaces,
                            prune_online_data,
                            audit_vectors_if_due,
                            corpus_cache.expire,
                        ):
                            try:
                                action()
                            except Exception as exc:  # noqa: BLE001 - independent maintenance failures
                                logger.warning(
                                    "maintenance_failed",
                                    extra={"error_type": type(exc).__name__},
                                )
                        operational_status(update=True)

                    maintenance_future = pool.submit(maintenance)
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
                logger.warning(
                    "worker_iteration_failed", extra={"error_type": type(exc).__name__}
                )
                time.sleep(5)


if __name__ == "__main__":
    main()
