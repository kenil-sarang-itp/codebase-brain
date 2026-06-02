"""
Repository source factory.

A single function that constructs the right `RepositorySource` implementation
based on configuration and per-request overrides. Callers (the indexing
service, the webhook handler) ask for a source by repo identifier and receive a
ready object — they never branch on `repo_source` themselves (Factory pattern,
Dependency Inversion).

Selection rules:
    * An explicit "owner/name" repo slug forces the GitHub MCP source.
    * A filesystem path, or no argument with `REPO_SOURCE=local`, yields the
      local source.
    * `REPO_SOURCE=github_mcp` with a configured `GITHUB_REPO` uses MCP.
"""

from __future__ import annotations

import os

from app.config.settings import get_settings
from app.core.exceptions import ConfigurationError
from app.core.logging import get_logger
from app.external.local_repository_source import LocalRepositorySource
from app.external.repository_source import RepositorySource

logger = get_logger(__name__)


def _looks_like_repo_slug(value: str) -> bool:
    """True if `value` looks like a GitHub 'owner/name' slug (not a path)."""
    return (
        "/" in value
        and not os.path.isabs(value)
        and not value.startswith(".")
        and value.count("/") == 1
        and not os.path.exists(value)
    )


def get_repository_source(
    repo_identifier: str | None = None,
    *,
    ref: str = "main",
) -> RepositorySource:
    """Construct the appropriate `RepositorySource`.

    Args:
        repo_identifier: Either a GitHub "owner/name" slug, a local filesystem
            path, or None to use the configured default.
        ref: Git ref to read when using the GitHub MCP source.

    Returns:
        A ready `RepositorySource`.

    Raises:
        ConfigurationError: If the configuration is inconsistent (e.g. MCP
            requested but no repo slug available).
    """
    settings = get_settings()
    identifier = repo_identifier or ""

    # 1. An explicit owner/name slug always means GitHub MCP.
    if _looks_like_repo_slug(identifier):
        return _build_github_mcp(identifier, ref)

    # 2. An explicit existing path always means the local source.
    if identifier and os.path.isdir(identifier):
        logger.info("Repository source: local (%s)", identifier)
        return LocalRepositorySource(identifier)

    # 3. Fall back to the configured default.
    if settings.repo_source == "github_mcp":
        slug = settings.github_repo
        if not _looks_like_repo_slug(slug):
            raise ConfigurationError(
                "REPO_SOURCE=github_mcp requires GITHUB_REPO to be set to a "
                "valid 'owner/name' slug."
            )
        return _build_github_mcp(slug, ref)

    # Default: local filesystem source rooted at LOCAL_REPO_PATH.
    logger.info("Repository source: local (%s)", settings.local_repo_path)
    return LocalRepositorySource(settings.local_repo_path)


def _build_github_mcp(slug: str, ref: str) -> RepositorySource:
    """Construct a `GitHubMCPSource` from an 'owner/name' slug."""
    # Imported lazily so the local path never imports the MCP adapter.
    from app.external.github_mcp_source import GitHubMCPSource

    owner, name = slug.split("/", 1)
    logger.info("Repository source: GitHub MCP (%s/%s @ %s)", owner, name, ref)
    return GitHubMCPSource(owner=owner, repo=name, ref=ref)
