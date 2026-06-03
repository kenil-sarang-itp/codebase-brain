"""
GitHub repository source backed by a real MCP server.

This is the production path for reading repositories. It speaks the Model
Context Protocol (MCP) to the official `github-mcp-server`, which exposes
GitHub's API as MCP tools (`get_file_contents`, `get_pull_request_files`, etc).

Why MCP rather than calling the REST API directly: the project decision is to
integrate the *real* GitHub MCP server, so tool discovery, auth, pagination,
and schema evolution are handled by a maintained server rather than reimple-
mented here. This class is purely an adapter — it maps our `RepositorySource`
port onto MCP tool calls.

Transport is configurable:
    * "http"  — connect to a long-running github-mcp-server container over
      streamable HTTP (the docker-compose default).
    * "stdio" — launch `github-mcp-server stdio` as a subprocess.

The official MCP Python SDK (`mcp` package) is imported lazily so the app — and
the local-filesystem path — never require it to be installed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from typing import Any

from app.config.settings import get_settings
from app.core.exceptions import ConfigurationError, ExternalServiceError, NotFoundError
from app.core.logging import get_logger
from app.external.repository_source import PRChange, RepoFile, RepositorySource

logger = get_logger(__name__)


class GitHubMCPSource(RepositorySource):
    """`RepositorySource` implementation that calls a GitHub MCP server.

    Each public method opens a short-lived MCP session, invokes the relevant
    tool(s), and closes the session. MCP sessions are cheap and this keeps the
    adapter stateless and safe to use from async request handlers and workers
    alike.
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        *,
        ref: str = "main",
    ) -> None:
        """Bind the adapter to a specific GitHub repository.

        Args:
            owner: Repository owner (user or org).
            repo: Repository name.
            ref: Git ref (branch/tag/SHA) to read. Defaults to "main".
        """
        self._owner = owner
        self._repo = repo
        self._ref = ref
        self._settings = get_settings()
        # Holds a persistent MCP session when open via _open_persistent_session().
        # None means each call opens its own short-lived session.
        self._persistent_session = None

    @contextlib.asynccontextmanager
    async def _open_persistent_session(self):
        """Context manager that opens one MCP session and reuses it for all
        file fetches within the block.

        Using a single session eliminates the TCP+TLS+MCP handshake for every
        file — the biggest source of latency in large repo indexing.

            async with source._open_persistent_session():
                # All get_file() calls inside here reuse one connection
                await source.get_file("src/app.jsx")
        """
        async with self._session() as session:
            self._persistent_session = session
            try:
                yield session
            finally:
                self._persistent_session = None

    # --------------------------------------------------------------- public --
    async def list_files(self) -> list[str]:
        """Return every file path in the repository tree.

        Uses the MCP `get_file_contents` tool on the repo root with a trailing
        slash, which the GitHub MCP server resolves to a directory listing, and
        recurses into sub-directories.
        """
        paths: list[str] = []
        async with self._session() as session:
            await self._walk_tree(session, "", paths)
        logger.info(
            "GitHub MCP listed %d files for %s/%s",
            len(paths),
            self._owner,
            self._repo,
        )
        return sorted(paths)

    async def get_file(self, path: str) -> RepoFile:
        """Fetch a single file, trying MCP first then GitHub REST API.

        The GitHub MCP server in HTTP transport mode sometimes returns a
        download-confirmation message (``"successfully downloaded text file
        (SHA: …)"`` ) instead of the actual file content — this is a quirk of
        how the server caches files locally.  When we detect that pattern we
        fall back to the GitHub REST API, which always returns inline content.
        """
        if self._persistent_session is not None:
            result = await self._call_tool(
                self._persistent_session,
                "get_file_contents",
                {
                    "owner": self._owner,
                    "repo": self._repo,
                    "path": path,
                    "ref": self._ref,
                },
            )
        else:
            async with self._session() as session:
                result = await self._call_tool(
                    session,
                    "get_file_contents",
                    {
                        "owner": self._owner,
                        "repo": self._repo,
                        "path": path,
                        "ref": self._ref,
                    },
                )
        content = self._decode_file_payload(result, path)

        # Detect the MCP "download confirmation" response and fall back to the
        # GitHub REST API which always returns inline base64 content.
        if self._is_download_confirmation(content):
            logger.info(
                "MCP returned download confirmation for %s — "
                "falling back to GitHub REST API", path,
            )
            content = await self._fetch_via_github_api(path)

        return RepoFile(path=path, content=content)

    async def get_files_concurrent(
        self,
        paths: list[str],
        *,
        max_concurrent: int = 5,
    ) -> dict[str, str]:
        """Fetch multiple files concurrently with a semaphore to respect rate limits.

        Uses a single persistent MCP session for all fetches — eliminating the
        per-file TCP+TLS+MCP handshake overhead that made serial fetching slow.
        A semaphore caps concurrency so GitHub's secondary rate limits are not
        triggered (safe up to ~10, risky above ~20, forbidden above 100).

        Args:
            paths: File paths to fetch.
            max_concurrent: Maximum simultaneous in-flight requests. Default 5
                is conservative but safe; increase to 10 for speed if your
                GitHub account has a higher rate limit tier.

        Returns:
            Dict mapping path → file content (empty string if fetch failed).
        """
        results: dict[str, str] = {}
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_one(path: str) -> None:
            async with semaphore:
                try:
                    repo_file = await self.get_file(path)
                    results[path] = repo_file.content
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Skipping %s: %s", path, exc)
                    results[path] = ""

        async with self._open_persistent_session():
            await asyncio.gather(*(fetch_one(p) for p in paths))

        return results

    async def get_pr_changes(self, pr_number: str) -> list[PRChange]:
        """Return the files changed by a pull request.

        Uses the MCP `get_pull_request_files` tool. This is what the webhook
        flow calls to compute the impact set for doc regeneration.
        """
        async with self._session() as session:
            result = await self._call_tool(
                session,
                "get_pull_request_files",
                {
                    "owner": self._owner,
                    "repo": self._repo,
                    "pullNumber": int(pr_number),
                },
            )
        return self._parse_pr_files(result)

    # ----------------------------------------------------------- MCP session --
    def _session(self):
        """Return an async context manager yielding an open MCP `ClientSession`.

        The MCP SDK is imported here (lazily) so importing this module never
        requires the `mcp` package — the local-filesystem source stays usable
        in minimal environments.
        """
        try:
            from mcp import ClientSession  # type: ignore  # noqa: F401
        except Exception as exc:  # pragma: no cover
            raise ConfigurationError(
                "The `mcp` package is required for GitHub MCP integration. "
                "Install it or set REPO_SOURCE=local."
            ) from exc

        transport = self._settings.github_mcp_transport.lower()
        if transport == "http":
            return self._http_session()
        if transport == "stdio":
            return self._stdio_session()
        raise ConfigurationError(
            f"Unknown GITHUB_MCP_TRANSPORT '{transport}' (expected http/stdio)."
        )

    def _http_session(self):
        """Async context manager: MCP session over streamable HTTP."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _ctx():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            url = self._settings.github_mcp_url
            # The GitHub MCP server authenticates the caller via a bearer token.
            headers = (
                {"Authorization": f"Bearer {self._settings.github_token}"}
                if self._settings.github_token
                else {}
            )
            try:
                async with streamablehttp_client(url, headers=headers) as (
                    read_stream,
                    write_stream,
                    _,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        yield session
            except ConfigurationError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ExternalServiceError(
                    "github-mcp",
                    f"Could not open MCP HTTP session to {url}: {exc}",
                ) from exc

        return _ctx()

    def _stdio_session(self):
        """Async context manager: MCP session over a stdio subprocess."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _ctx():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            parts = self._settings.github_mcp_command.split()
            params = StdioServerParameters(
                command=parts[0],
                args=parts[1:],
                # The server reads the token from the environment.
                env={"GITHUB_PERSONAL_ACCESS_TOKEN": self._settings.github_token},
            )
            try:
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        yield session
            except Exception as exc:  # noqa: BLE001
                raise ExternalServiceError(
                    "github-mcp",
                    f"Could not launch MCP stdio server: {exc}",
                ) from exc

        return _ctx()

    async def _call_tool(
        self, session: Any, tool_name: str, arguments: dict
    ) -> Any:
        """Invoke one MCP tool and return its raw result content.

        Wraps transport/tool errors in a typed `ExternalServiceError` so
        callers handle a single, predictable exception type.
        """
        try:
            response = await session.call_tool(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                "github-mcp", f"MCP tool '{tool_name}' failed: {exc}"
            ) from exc

        if getattr(response, "isError", False):
            raise ExternalServiceError(
                "github-mcp",
                f"MCP tool '{tool_name}' returned an error for {arguments}.",
            )
        return response.content

    # ------------------------------------------------------------- helpers ---
    async def _walk_tree(
        self, session: Any, directory: str, out: list[str]
    ) -> None:
        """Recursively list files under `directory` via repeated tool calls."""
        content = await self._call_tool(
            session,
            "get_file_contents",
            {
                "owner": self._owner,
                "repo": self._repo,
                # A trailing slash signals "directory listing" to the server.
                "path": f"{directory}/" if directory else "/",
                "ref": self._ref,
            },
        )
        entries = self._parse_directory_listing(content)

        # Recurse into sub-directories concurrently for speed, but cap the fan
        # -out so a huge repo cannot open thousands of simultaneous calls.
        sub_dirs: list[str] = []
        for entry in entries:
            etype = entry.get("type")
            epath = entry.get("path", "")
            if etype == "file":
                out.append(epath)
            elif etype == "dir":
                sub_dirs.append(epath)

        semaphore = asyncio.Semaphore(8)

        async def _recurse(path: str) -> None:
            async with semaphore:
                await self._walk_tree(session, path, out)

        if sub_dirs:
            await asyncio.gather(*(_recurse(d) for d in sub_dirs))

    @staticmethod
    def _content_to_dicts(content: Any) -> list[dict]:
        """Normalise MCP tool `content` blocks into a list of dicts.

        MCP returns a list of typed content blocks; the GitHub server packs its
        JSON payloads into text blocks. This parses each text block as JSON and
        flattens the results.
        """
        results: list[dict] = []
        if content is None:
            return results

        blocks = content if isinstance(content, list) else [content]
        for block in blocks:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, list):
                results.extend(p for p in parsed if isinstance(p, dict))
            elif isinstance(parsed, dict):
                results.append(parsed)
        return results

    def _parse_directory_listing(self, content: Any) -> list[dict]:
        """Extract directory entries from a `get_file_contents` result."""
        dicts = self._content_to_dicts(content)
        # The server may return the entries directly, or nested under a key.
        if len(dicts) == 1 and "entries" in dicts[0]:
            return dicts[0]["entries"]
        return dicts

    # --------------------------------------------------------- decode helpers --
    @staticmethod
    def _is_download_confirmation(content: str) -> bool:
        """Return True when the MCP gave us a download confirmation instead
        of actual file content.

        The GitHub MCP server (HTTP transport) downloads files to a local
        cache and returns a short status string like::

            successfully downloaded text file (SHA: <40-hex-chars>)

        This is never valid source code, so we detect it and trigger the
        GitHub REST API fallback.
        """
        s = (content or "").strip()
        return bool(s) and s.lower().startswith("successfully downloaded")

    async def _fetch_via_github_api(self, path: str) -> str:
        """Fetch file content directly from the GitHub REST API.

        Called when the MCP server returns a download confirmation instead of
        inline content.  The REST API always returns base64-encoded content
        regardless of file size or transport mode.
        """
        try:
            import httpx
        except ImportError:  # pragma: no cover
            logger.error(
                "httpx is not installed — cannot use GitHub REST API fallback "
                "for %s. Add httpx to requirements.", path,
            )
            return ""

        url = (
            f"https://api.github.com/repos/{self._owner}/{self._repo}"
            f"/contents/{path.lstrip('/')}"
        )
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._settings.github_token:
            headers["Authorization"] = f"Bearer {self._settings.github_token}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    url, headers=headers, params={"ref": self._ref}
                )
                resp.raise_for_status()
                data = resp.json()

            encoding = (data.get("encoding") or "").lower()
            raw = data.get("content", "")

            if encoding == "base64" and raw:
                decoded = base64.b64decode(
                    raw.replace("\n", "").replace(" ", "")
                ).decode("utf-8", errors="replace")
                logger.info(
                    "GitHub REST API: fetched %s (%d chars)", path, len(decoded)
                )
                return decoded

            if isinstance(raw, str) and raw.strip():
                logger.info(
                    "GitHub REST API: plain-text content for %s (%d chars)",
                    path, len(raw),
                )
                return raw

            logger.warning(
                "GitHub REST API: no usable content for %s "
                "(encoding=%r, content_len=%d)",
                path, encoding, len(str(raw)),
            )
            return ""

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "GitHub REST API fallback FAILED for %s: %s", path, exc,
                exc_info=True,
            )
            return ""

    @staticmethod
    def _try_base64_decode(s: str) -> str | None:
        """Attempt to decode `s` as standard base64.

        Returns the decoded UTF-8 string, or None when:
          * `s` contains characters outside the base64 alphabet (so it's
            already plain text — no need to decode), or
          * decoding produces too many Unicode replacement chars (binary data).

        The character-set check is the fast path: real Python/JS source always
        contains spaces, colons, parens, quotes, etc. — none of which appear in
        base64. So the regex rejects plain text in microseconds without calling
        b64decode at all.
        """
        import re as _re
        try:
            cleaned = s.replace("\n", "").replace("\r", "").replace(" ", "")
            if len(cleaned) < 16:
                return None
            # Base64 alphabet: A-Z a-z 0-9 + / =
            if not _re.match(r"^[A-Za-z0-9+/=]+$", cleaned):
                return None
            # Restore missing padding.
            rem = len(cleaned) % 4
            if rem:
                cleaned += "=" * (4 - rem)
            decoded_bytes = base64.b64decode(cleaned, validate=True)
            decoded_str = decoded_bytes.decode("utf-8", errors="replace")
            # Reject binary-ish content (> 5 % replacement chars).
            if decoded_str.count("�") / max(len(decoded_str), 1) > 0.05:
                return None
            return decoded_str
        except Exception:  # noqa: BLE001
            return None

    def _decode_file_payload(self, content: Any, path: str) -> str:
        """Extract and decode a file body from a `get_file_contents` result.

        The GitHub MCP server returns file content as one or more text content
        blocks.  The normal format is a single block whose `text` field is a
        JSON string containing the GitHub REST API file object
        (``encoding: "base64"``, ``content: "<base64>"``, …).  However the
        server sometimes prepends informational text blocks, and older / newer
        versions occasionally omit the ``encoding`` field or return the payload
        differently.

        Strategy:
          1. Collect all text blocks from the MCP response.
          2. Log the raw first block at INFO so decode problems are always
             visible in worker logs.
          3. Pass 1 — scan every block that parses as JSON for a ``content``
             field.  Regardless of the ``encoding`` field, always try
             ``_try_base64_decode`` first; fall back to returning the field
             value verbatim if it is already plain text.
          4. Pass 2 — for blocks that are not JSON, save them and pick the
             longest one as a last-resort plain-text fallback (the longest is
             most likely the real file body, not a short status message).
          5. Also try ``_try_base64_decode`` on that fallback block.

        Returns ``""`` when nothing decodable is found so one bad file never
        crashes the pipeline.
        """
        import json as _json

        if content is None:
            logger.warning("GitHub MCP returned None content for %s", path)
            return ""

        blocks = content if isinstance(content, list) else [content]

        # Collect every piece of text from the MCP response.
        # The GitHub MCP HTTP server returns TWO blocks per file:
        #   • TextContent  — "successfully downloaded text file (SHA: …)"
        #   • EmbeddedResource — the actual file content at block.resource.text
        # Previous code only read block.text and completely missed the resource
        # block, so static analysis received the confirmation string while the
        # doc generator somehow got real content (likely from Gemini's training
        # data for public repos).  We now read both block types.
        raw_texts: list[str] = []
        for block in blocks:
            # --- TextContent / plain text block ---
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text and isinstance(text, str):
                raw_texts.append(text)
                continue

            # --- EmbeddedResource block (type == "resource") ---
            # MCP SDK: block.resource is a TextResourceContents whose .text
            # field holds the actual file body.
            resource = getattr(block, "resource", None)
            if resource is not None:
                # TextResourceContents has a .text attribute
                res_text = getattr(resource, "text", None)
                if res_text and isinstance(res_text, str):
                    raw_texts.append(res_text)
                    continue
                # Dict-style fallback
                if isinstance(resource, dict):
                    res_text = resource.get("text")
                    if res_text and isinstance(res_text, str):
                        raw_texts.append(res_text)

        if not raw_texts:
            logger.warning(
                "GitHub MCP returned no text blocks for %s — "
                "content type: %s repr: %s",
                path, type(content).__name__, repr(content)[:200],
            )
            return ""

        # Log ALL blocks so we can see exactly what the MCP returned.
        logger.info(
            "GitHub MCP raw response for %s: %d block(s). "
            "Block[0] first 300 chars: %r",
            path, len(raw_texts), raw_texts[0][:300],
        )
        if len(raw_texts) > 1:
            logger.info(
                "GitHub MCP Block[1] first 300 chars for %s: %r",
                path, raw_texts[1][:300],
            )

        plain_text_blocks: list[str] = []

        # Pass 1 — find a JSON block with a ``content`` field.
        for raw in raw_texts:
            try:
                payload = _json.loads(raw)
            except (_json.JSONDecodeError, TypeError):
                stripped = raw.strip()
                if stripped:
                    plain_text_blocks.append(stripped)
                continue

            # Unwrap arrays (e.g. directory listing accidentally returned).
            if isinstance(payload, list):
                if payload and isinstance(payload[0], dict):
                    payload = payload[0]
                else:
                    continue

            if not isinstance(payload, dict):
                continue

            # ``encoding`` may be missing, None, "base64", "utf-8", etc.
            encoding = (payload.get("encoding") or "").lower()
            file_content = payload.get("content") or ""

            if not file_content:
                continue

            logger.info(
                "GitHub MCP JSON payload for %s: encoding=%r, "
                "content starts: %r",
                path, encoding, str(file_content)[:80],
            )

            # Always try base64 decode regardless of the encoding field —
            # the field is sometimes absent or set to a non-"base64" value
            # even when the content IS base64-encoded.
            decoded = self._try_base64_decode(str(file_content))
            if decoded and decoded.strip():
                logger.info(
                    "GitHub MCP: base64-decoded %s (%d chars, encoding=%r)",
                    path, len(decoded), encoding,
                )
                return decoded

            # Content is already plain text (passed the base64 character-set
            # check → not base64, or decoded to empty).
            if isinstance(file_content, str) and file_content.strip():
                logger.info(
                    "GitHub MCP: plain-text content for %s (%d chars)",
                    path, len(file_content),
                )
                return file_content

        # Pass 2 — no usable JSON payload; try plain-text blocks.
        if plain_text_blocks:
            best = max(plain_text_blocks, key=len)
            # Try base64 decode on the plain-text block too.
            decoded = self._try_base64_decode(best)
            if decoded and decoded.strip():
                logger.info(
                    "GitHub MCP: base64-decoded plain-text block for %s "
                    "(%d chars)", path, len(decoded),
                )
                return decoded
            logger.info(
                "GitHub MCP: using plain-text block for %s (%d chars, "
                "from %d candidate block(s))",
                path, len(best), len(plain_text_blocks),
            )
            return best

        logger.warning(
            "GitHub MCP: could not extract content for %s. "
            "%d block(s) examined. First block: %r",
            path, len(raw_texts),
            raw_texts[0][:300] if raw_texts else "none",
        )
        return ""

    def _parse_pr_files(self, content: Any) -> list[PRChange]:
        """Convert a `get_pull_request_files` result into `PRChange` objects."""
        changes: list[PRChange] = []
        for entry in self._content_to_dicts(content):
            changes.append(
                PRChange(
                    path=entry.get("filename", entry.get("path", "")),
                    status=entry.get("status", "modified"),
                    patch=entry.get("patch", ""),
                )
            )
        return changes
