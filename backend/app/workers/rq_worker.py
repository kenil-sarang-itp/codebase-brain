"""
RQ worker entry point.

This is the process launched in the `worker` Docker container. It connects to
Redis and consumes jobs from the `codebase-brain` queue, running the task
functions in `workers/tasks.py`.

Run directly:

    python -m app.workers.rq_worker

The Docker Compose file scales this service to multiple replicas so indexing
and PR-sync jobs are processed in parallel.
"""

from __future__ import annotations

from app.config.settings import get_settings
from app.core.logging import configure_logging, get_logger
from app.observability.tracing import configure_tracing
from app.workers.queue import QUEUE_NAME

logger = get_logger(__name__)


def main() -> None:
    """Start an RQ worker that processes the application's job queue."""
    configure_logging()
    configure_tracing(service_name="codebase-brain-worker")

    from redis import Redis
    from rq import Queue, Worker

    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(QUEUE_NAME, connection=connection)

    logger.info(
        "RQ worker starting; listening on queue '%s' (redis=%s)",
        QUEUE_NAME,
        settings.redis_url,
    )

    worker = Worker([queue], connection=connection)
    # with_scheduler=True lets RQ handle any scheduled/retried jobs too.
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
