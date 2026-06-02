"""
Repository source abstraction (Port).

The pipeline needs to read repository contents — the file tree, file bodies,
and PR diffs — without caring *how* those are obtained. `RepositorySource` is
that contract.

Two implementations satisfy it:
    * `GitHubMCPSource` — talks to a real GitHub MCP server (the production
      path, per the project decision to use MCP).
    * `LocalRepositorySource` — reads a directory on disk (the zero-setup demo
      and test path).

High-level code depends only on this interface, so switching sources is a
configuration change, not a code change (Dependency Inversion).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RepoFile:
    """A single file's path and decoded text content."""

    path: str       # repo-relative path
    content: str    # decoded UTF-8 text


@dataclass(frozen=True)
class PRChange:
    """One file changed in a pull request."""

    path: str
    status: str          # "added" | "modified" | "removed" | "renamed"
    patch: str = ""      # unified-diff patch text, if available


class RepositorySource(ABC):
    """Contract for reading a repository's files and PR diffs."""

    @abstractmethod
    async def list_files(self) -> list[str]:
        """Return every file path in the repository (unfiltered).

        Skip-list filtering is applied later by the chunker, so this returns
        the raw tree.
        """

    @abstractmethod
    async def get_file(self, path: str) -> RepoFile:
        """Fetch and decode a single file's contents.

        Raises:
            NotFoundError: If the path does not exist.
            ExternalServiceError: On a transport failure.
        """

    @abstractmethod
    async def get_pr_changes(self, pr_number: str) -> list[PRChange]:
        """Return the list of files changed by a pull request."""

    async def get_files(self, paths: list[str]) -> list[RepoFile]:
        """Fetch many files. Default implementation calls `get_file` per path.

        Subclasses may override with a batched implementation.
        """
        result: list[RepoFile] = []
        for path in paths:
            result.append(await self.get_file(path))
        return result
