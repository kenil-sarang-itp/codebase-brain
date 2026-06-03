"""
RQ job queue — enqueue helpers.

The API must never run the heavy indexing pipeline on the request thread (spec:
"FastAPI delegates heavy work"). These helpers push jobs onto a Redis-backed RQ
queue; a separate worker process (`workers/rq_worker.py`) consumes them.

Each helper enqueues by *string path* to the task function. Enqueuing by path
(rather than importing the task) keeps this module free of heavy imports, so
the API process stays light.
"""

from __future__ import annotations

from functools import lru_cache

from app.config.settings import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Name of the single RQ queue used for all background work.
QUEUE_NAME = "codebase-brain"

# Generous timeout — a full repo index can legitimately take many minutes.
_JOB_TIMEOUT = 60 * 60          # 1 hour
_RESULT_TTL = 60 * 60 * 24      # keep results 24h for status inspection


@lru_cache(maxsize=1)
def _get_queue():
    """Return the process-wide RQ queue, constructing it once.

    Imported lazily so environments without Redis (e.g. some unit tests) can
    still import the API modules.
    """
    from redis import Redis
    from rq import Queue

    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    return Queue(QUEUE_NAME, connection=connection)


def enqueue_initial_indexing(*, session_id: str, repo: str, ref: str) -> str:
    """Enqueue a full initial-indexing job. Returns the RQ job id."""
    job = _get_queue().enqueue(
        "app.workers.tasks.run_initial_indexing_task",
        kwargs={"session_id": session_id, "repo": repo, "ref": ref},
        job_timeout=_JOB_TIMEOUT,
        result_ttl=_RESULT_TTL,
    )
    logger.info("Enqueued indexing job %s (session %s)", job.id, session_id)
    return job.id


def enqueue_pr_sync(*, pr_number: str, repo: str) -> str:
    """Enqueue an impact-based PR-sync regeneration job. Returns the job id."""
    job = _get_queue().enqueue(
        "app.workers.tasks.run_pr_sync_task",
        kwargs={"pr_number": pr_number, "repo": repo},
        job_timeout=_JOB_TIMEOUT,
        result_ttl=_RESULT_TTL,
    )
    logger.info("Enqueued PR-sync job %s (PR #%s)", job.id, pr_number)
    return job.id
