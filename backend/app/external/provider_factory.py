"""
Provider factory (Factory + Singleton patterns).

This module is the single decision point for *which* concrete provider backs
each abstract interface. It inspects the configuration and returns either the
real cloud provider (Vertex AI, Cohere) or the offline local fallback.

Why a factory:
    * Callers ask for an `LLMProvider` and get a working one — they neither
      know nor care whether it is cloud-backed. (Dependency Inversion.)
    * The real/fallback decision lives in exactly one place.
    * Providers are cached per process (Singleton-ish) so SDK clients and their
      connection pools are created once, not per request.

The factory degrades gracefully: if a real provider is configured but fails to
construct, it logs a clear warning and falls back to the local provider so the
application still serves traffic.
"""

from __future__ import annotations

from app.config.settings import get_settings
from app.core.logging import get_logger
from app.external.interfaces import (
    EmbeddingProvider,
    LLMProvider,
    RerankerProvider,
)
from app.external.local_providers import (
    LocalEmbeddingProvider,
    LocalLLMProvider,
    LocalReranker,
)

logger = get_logger(__name__)

# Process-level cache of constructed providers (keyed by provider kind).
_cache: dict[str, object] = {}


def get_llm_provider() -> LLMProvider:
    """Return the active LLM provider (Vertex AI if configured, else local)."""
    if "llm" in _cache:
        return _cache["llm"]  # type: ignore[return-value]

    settings = get_settings()
    provider: LLMProvider
    if settings.llm_provider_is_real:
        try:
            from app.external.vertex_provider import VertexAILLMProvider

            provider = VertexAILLMProvider()
            logger.info("LLM provider: Vertex AI (%s)", provider.model_name)
        except Exception as exc:  # noqa: BLE001 - never let this kill startup
            logger.warning(
                "Vertex AI LLM unavailable (%s); using local fallback.", exc
            )
            provider = LocalLLMProvider()
    else:
        provider = LocalLLMProvider()
        logger.info("LLM provider: local fallback (no GCP credentials set)")

    _cache["llm"] = provider
    return provider


def get_embedding_provider() -> EmbeddingProvider:
    """Return the active embedding provider (Vertex AI if configured, else local)."""
    if "embedding" in _cache:
        return _cache["embedding"]  # type: ignore[return-value]

    settings = get_settings()
    provider: EmbeddingProvider
    if settings.llm_provider_is_real:
        try:
            from app.external.vertex_provider import VertexAIEmbeddingProvider

            provider = VertexAIEmbeddingProvider()
            logger.info("Embedding provider: Vertex AI text-embedding")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Vertex AI embeddings unavailable (%s); using local fallback.",
                exc,
            )
            provider = LocalEmbeddingProvider()
    else:
        provider = LocalEmbeddingProvider()
        logger.info("Embedding provider: local fallback (deterministic hashing)")

    _cache["embedding"] = provider
    return provider


def get_reranker_provider() -> RerankerProvider:
    """Return the active reranker (Cohere if configured, else local overlap)."""
    if "reranker" in _cache:
        return _cache["reranker"]  # type: ignore[return-value]

    settings = get_settings()
    provider: RerankerProvider
    if settings.reranker_is_real:
        try:
            from app.external.cohere_provider import CohereReranker

            provider = CohereReranker()
            logger.info("Reranker: Cohere Rerank")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Cohere unavailable (%s); using local reranker.", exc
            )
            provider = LocalReranker()
    else:
        provider = LocalReranker()
        logger.info("Reranker: local fallback (token overlap)")

    _cache["reranker"] = provider
    return provider


def reset_provider_cache() -> None:
    """Clear the provider cache. Used by tests to force reconstruction."""
    _cache.clear()
