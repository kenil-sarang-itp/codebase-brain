"""
Local filesystem repository source.

Implements `RepositorySource` by reading a directory on disk. This is the
zero-setup path: it needs no GitHub token and no MCP container, so the system
can index and demo against any local checkout immediately.

It is also what the test suite uses, keeping tests fast and hermetic.
"""

from __future__ import annotations

import os

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.external.repository_source import PRChange, RepoFile, RepositorySource

logger = get_logger(__name__)

# Files larger than this are skipped — almost certainly data/binaries, not code.
_MAX_FILE_BYTES = 1_000_000


class LocalRepositorySource(RepositorySource):
    """Reads repository files from a local directory."""

    def __init__(self, root_path: str) -> None:
        """Bind to a directory root.

        Raises:
            ValidationError: If the path does not exist or is not a directory.
        """
        self._root = os.path.abspath(root_path)
        if not os.path.isdir(self._root):
            raise ValidationError(
                f"Repository path is not a directory: {root_path}"
            )
        logger.info("LocalRepositorySource bound to %s", self._root)

    async def list_files(self) -> list[str]:
        """Walk the directory tree and return repo-relative file paths."""
        paths: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(self._root):
            for name in filenames:
                abs_path = os.path.join(dirpath, name)
                rel = os.path.relpath(abs_path, self._root)
                # Normalise to forward slashes for cross-platform consistency.
                paths.append(rel.replace(os.sep, "/"))
        return sorted(paths)

    async def get_file(self, path: str) -> RepoFile:
        """Read one file, guarding against path traversal and oversized files."""
        abs_path = self._safe_join(path)
        if not os.path.isfile(abs_path):
            raise NotFoundError(f"File not found in repository: {path}")

        try:
            if os.path.getsize(abs_path) > _MAX_FILE_BYTES:
                # Too large to be source — return empty so it is skipped.
                logger.debug("Skipping oversized file: %s", path)
                return RepoFile(path=path, content="")
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                return RepoFile(path=path, content=fh.read())
        except OSError as exc:
            # I/O errors degrade to "empty file" so one bad file cannot abort
            # a whole indexing run.
            logger.warning("Could not read %s: %s", path, exc)
            return RepoFile(path=path, content="")

    async def get_pr_changes(self, pr_number: str) -> list[PRChange]:
        """Not supported for a local directory — local sources have no PRs."""
        raise ValidationError(
            "PR diffs are not available for a local repository source. "
            "Use the GitHub MCP source for webhook-driven PR sync."
        )

    # ------------------------------------------------------------ helpers --
    def _safe_join(self, rel_path: str) -> str:
        """Join `rel_path` to the root, rejecting path-traversal attempts.

        SECURITY: prevents a crafted path like `../../etc/passwd` from escaping
        the repository root.
        """
        candidate = os.path.abspath(os.path.join(self._root, rel_path))
        if not candidate.startswith(self._root + os.sep) and candidate != self._root:
            raise ValidationError(f"Illegal path outside repository: {rel_path}")
        return candidate
