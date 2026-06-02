"""
Embedding pipeline stage.

A thin orchestration layer over the configured `EmbeddingProvider`. It exists
so the pipeline has a stable, intention-revealing API (`embed_code`,
`embed_docs`) and so batching/empty-input handling lives in one place rather
than being repeated at every call site.

The underlying provider (Vertex AI or local fallback) already batches network
calls; this stage adds semantic clarity, not a second batching layer.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.external.interfaces import EmbeddingProvider
from app.observability.tracing import traced_span

logger = get_logger(__name__)


class Embedder:
    """Produces embedding vectors for code and documentation text."""

    def __init__(self, provider: EmbeddingProvider) -> None:
        """Inject the embedding provider (Dependency Inversion)."""
        self._provider = provider

    @property
    def dimension(self) -> int:
        """Embedding dimensionality — must match the Qdrant collection."""
        return self._provider.dimension

    async def embed_docs(self, doc_texts: list[str]) -> list[list[float]]:
        """Embed generated documentation text (populates the `doc` vector).

        Empty strings are replaced with a single space so the provider never
        receives an empty instance (Vertex AI rejects them with 400).
        """
        if not doc_texts:
            return []
        # Never send empty strings to the embedding API.
        safe_texts = [t if t and t.strip() else " " for t in doc_texts]
        with traced_span("embedder.docs", {"count": len(safe_texts)}):
            return await self._provider.embed(safe_texts)

    async def embed_code(self, code_snippets: list[str]) -> list[list[float]]:
        """Embed raw source-code snippets (populates each point's `code` vector)."""
        if not code_snippets:
            return []
        safe_snippets = [t if t and t.strip() else " " for t in code_snippets]
        with traced_span("embedder.code", {"count": len(safe_snippets)}):
            return await self._provider.embed(safe_snippets)

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query string for retrieval.

        Returns the lone vector directly (not a one-element list) since callers
        always want exactly one vector for a search.
        """
        with traced_span("embedder.query"):
            vectors = await self._provider.embed([query])
        return vectors[0]
