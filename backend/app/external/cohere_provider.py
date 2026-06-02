"""
Cohere reranker provider.

Real implementation of `RerankerProvider` backed by the Cohere Rerank API.
After Qdrant returns the top-N nearest neighbours, this re-scores them for true
relevance and returns the best handful (spec section: Reranker — Cohere Rerank).

Cohere has no Vertex AI equivalent, so it keeps its own `COHERE_API_KEY`. When
that key is absent the factory substitutes `LocalReranker` instead.
"""

from __future__ import annotations

import asyncio

from app.config.settings import get_settings
from app.core.exceptions import ExternalServiceError, RateLimitError
from app.core.logging import get_logger
from app.external.interfaces import RerankerProvider, RerankResult

logger = get_logger(__name__)


class CohereReranker(RerankerProvider):
    """Relevance reranker backed by Cohere Rerank."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._model = self._settings.cohere_rerank_model
        self._client = None  # lazily constructed

    def _ensure_client(self):
        """Lazily construct the Cohere client (keeps import optional)."""
        if self._client is not None:
            return self._client
        try:
            import cohere  # type: ignore

            self._client = cohere.Client(api_key=self._settings.cohere_api_key)
        except Exception as exc:  # pragma: no cover
            raise ExternalServiceError(
                "cohere",
                "Failed to initialise the Cohere client. Check COHERE_API_KEY "
                "and that the `cohere` package is installed.",
            ) from exc
        return self._client

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int
    ) -> list[RerankResult]:
        """Re-score documents via Cohere Rerank and return the best `top_n`."""
        if not documents:
            return []

        client = self._ensure_client()
        try:
            # Blocking SDK call offloaded to a thread to protect the loop.
            response = await asyncio.to_thread(
                client.rerank,
                model=self._model,
                query=query,
                documents=documents,
                top_n=min(top_n, len(documents)),
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "rate" in msg or "429" in msg or "quota" in msg:
                raise RateLimitError(
                    "cohere", "Cohere rerank rate limit exceeded."
                ) from exc
            raise ExternalServiceError(
                "cohere", f"Cohere rerank failed: {exc}"
            ) from exc

        # Normalise the SDK response into our provider-agnostic DTO.
        return [
            RerankResult(index=item.index, score=item.relevance_score)
            for item in response.results
        ]
