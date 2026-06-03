"""
RQ task definitions — the background job functions.

These are the functions RQ workers actually execute. Each is a *synchronous*
entry point (RQ calls plain functions) that sets up a worker-scoped DB session
and drives the async `IndexingService` via `asyncio.run`.

Key responsibilities of this thin layer:
    * Bridge RQ's synchronous world to our async service layer.
    * Own a worker-scoped DB transaction (`sync`-style usage via an async
      session created per job).
    * Translate job outcomes into clear logs and re-raise on failure so RQ
      records the job as failed (and retry policy can apply).
"""

from __future__ import annotations

import asyncio

from app.core.logging import configure_logging, get_logger, set_trace_id
from app.observability.tracing import configure_tracing

logger = get_logger(__name__)


def _run_async(coro):
    """Run an async coroutine to completion from a synchronous RQ task.

    A fresh event loop per job keeps jobs fully isolated from one another.
    """
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Initial indexing                                                            #
# --------------------------------------------------------------------------- #
def run_initial_indexing_task(*, session_id: str, repo: str, ref: str) -> dict:
    """RQ task: run the full six-phase indexing pipeline for a repository.

    Args:
        session_id: The pre-created indexing session id to drive.
        repo: Repo slug ("owner/name") or local path.
        ref: Git ref to index (GitHub MCP source only).

    Returns:
        A small result dict (stored by RQ for inspection).
    """
    # Workers are separate processes — configure their own logging/tracing.
    configure_logging()
    configure_tracing(service_name="codebase-brain-worker")
    set_trace_id(f"index-{session_id}")

    logger.info("Worker starting indexing: session=%s repo=%s", session_id, repo)

    async def _job() -> dict:
        # A worker-scoped async session, committed at the end of the job.
        from app.db.session import _AsyncSessionFactory  # type: ignore
        from app.services.service_factory import build_indexing_service

        session = _AsyncSessionFactory()
        try:
            indexing_service = build_indexing_service(
                session, repo_identifier=repo
            )
            await indexing_service.run_initial_indexing(session_id)
            await session.commit()
            return {"session_id": session_id, "status": "complete"}
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    try:
        result = _run_async(_job())
        logger.info("Worker finished indexing session %s", session_id)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("Indexing task failed for session %s", session_id)
        # Re-raise so RQ marks the job failed.
        raise


# --------------------------------------------------------------------------- #
# PR-driven sync                                                              #
# --------------------------------------------------------------------------- #
def run_pr_sync_task(*, pr_number: str, repo: str) -> dict:
    """RQ task: impact-based doc regeneration after a PR merge.

    Fetches the PR's changed files, computes the impact set, and regenerates
    only the affected documentation.

    Args:
        pr_number: The merged pull request number.
        repo: The "owner/name" slug of the repository.

    Returns:
        A result dict summarising the regeneration.
    """
    configure_logging()
    configure_tracing(service_name="codebase-brain-worker")
    set_trace_id(f"pr-sync-{pr_number}")

    logger.info("Worker starting PR-sync: PR #%s repo=%s", pr_number, repo)

    async def _job() -> dict:
        from app.db.session import _AsyncSessionFactory  # type: ignore
        from app.external.repository_source_factory import get_repository_source
        from app.services.service_factory import build_indexing_service

        session = _AsyncSessionFactory()
        try:
            # The PR-sync path needs the GitHub MCP source to read PR diffs.
            repo_source = get_repository_source(repo)
            indexing_service = build_indexing_service(
                session, repo_source=repo_source
            )

            # 1. Discover which files the PR changed.
            changes = await repo_source.get_pr_changes(pr_number)
            changed_paths = [
                c.path for c in changes if c.status != "removed"
            ]

            # 2. Compute the impact set and mark docs for regeneration.
            impact = await indexing_service.regenerate_for_pr(
                pr_number, changed_paths
            )

            # 3. Actually regenerate the affected files' docs.
            await indexing_service.reindex_changed_files(changed_paths)

            await session.commit()
            return {
                "pr_number": pr_number,
                "status": "complete",
                "files_regenerated": len(changed_paths),
                "affected_flows": impact["affected_flows"],
            }
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    try:
        result = _run_async(_job())
        logger.info("Worker finished PR-sync for PR #%s", pr_number)
        return result
    except Exception:  # noqa: BLE001
        logger.exception("PR-sync task failed for PR #%s", pr_number)
        raise
