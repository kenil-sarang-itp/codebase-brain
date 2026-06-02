"""
Vertex AI provider implementations.

Real implementations of `LLMProvider` and `EmbeddingProvider` backed by Google
Cloud Vertex AI. One GCP service account authenticates *both* — Gemini for
generation and `text-embedding-004` for embeddings — which is why Vertex AI was
chosen as the single cloud provider.

Both classes:
    * import the Google SDK lazily (so the app starts even if the package is
      absent and the local fallback is in use);
    * run blocking SDK calls in a thread via `asyncio.to_thread`, so they never
      stall the FastAPI event loop;
    * implement bounded retry with exponential backoff, translating provider
      failures into the app's typed `ExternalServiceError` / `RateLimitError`.
"""

from __future__ import annotations

import asyncio
import random

from app.config.settings import get_settings
from app.core.exceptions import ExternalServiceError, RateLimitError
from app.core.logging import get_logger
from app.external.interfaces import EmbeddingProvider, LLMProvider, LLMResponse

logger = get_logger(__name__)

# Retry tuning for transient cloud failures.
_MAX_RETRIES = 4
_BASE_BACKOFF_SECONDS = 2.0


def _is_rate_limit(exc: Exception) -> bool:
    """Heuristically detect a rate-limit / quota error from its message."""
    msg = str(exc).lower()
    return any(
        kw in msg
        for kw in ("rate limit", "quota", "429", "resource exhausted")
    )


class VertexAILLMProvider(LLMProvider):
    """Gemini text generation via Vertex AI.

    The model id is fully configurable through `LLM_MODEL` (the spec's
    deprecated "gemini-1.5-pro" can be swapped for any current model without a
    code change).
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._model_id = self._settings.llm_model
        self._client = None  # lazily constructed on first use

    @property
    def model_name(self) -> str:
        return self._model_id

    def _ensure_client(self):
        """Lazily construct the Vertex AI GenAI client.

        Done lazily (not in __init__) so importing this module never requires
        the SDK or credentials to be present.
        """
        if self._client is not None:
            return self._client
        try:
            from google import genai  # type: ignore

            # vertexai=True routes through the GCP project's Vertex endpoint.
            self._client = genai.Client(
                vertexai=True,
                project=self._settings.gcp_project_id,
                location=self._settings.gcp_location,
            )
        except Exception as exc:  # pragma: no cover - env-dependent
            raise ExternalServiceError(
                "vertex-ai-llm",
                "Failed to initialise the Vertex AI client. Check GCP "
                "credentials and that `google-genai` is installed.",
            ) from exc
        return self._client

    async def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        """Generate text with bounded exponential-backoff retry."""
        client = self._ensure_client()
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                # The SDK call is blocking — offload it to a worker thread.
                response = await asyncio.to_thread(
                    self._sync_generate,
                    client,
                    prompt,
                    system_instruction,
                    temperature,
                    max_output_tokens,
                )
                return response
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if _is_rate_limit(exc) and attempt < _MAX_RETRIES:
                    # Exponential backoff with jitter for rate limits.
                    delay = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    delay += random.uniform(0, 1)
                    logger.warning(
                        "Vertex AI rate limited (attempt %d/%d); "
                        "retrying in %.1fs",
                        attempt,
                        _MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                break  # non-retryable, or retries exhausted

        # All attempts failed — surface a typed error for the caller / RQ retry.
        if last_error and _is_rate_limit(last_error):
            raise RateLimitError(
                "vertex-ai-llm", "Vertex AI rate limit exceeded after retries."
            ) from last_error
        raise ExternalServiceError(
            "vertex-ai-llm", f"Vertex AI generation failed: {last_error}"
        ) from last_error

    def _sync_generate(
        self,
        client,
        prompt: str,
        system_instruction: str | None,
        temperature: float,
        max_output_tokens: int,
    ) -> LLMResponse:
        """Blocking single generation call. Runs inside a worker thread."""
        from google.genai import types  # type: ignore

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=system_instruction,
        )
        result = client.models.generate_content(
            model=self._model_id,
            contents=prompt,
            config=config,
        )
        usage = getattr(result, "usage_metadata", None)
        return LLMResponse(
            text=result.text or "",
            model=self._model_id,
            prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            completion_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        )


class VertexAIEmbeddingProvider(EmbeddingProvider):
    """Text embeddings via Vertex AI `text-embedding-004` (768-dimensional).

    Inputs are sent in batches (configurable, default 250 per the spec) to stay
    within request-size limits and reduce round trips.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._model_id = self._settings.embedding_model
        self._dim = self._settings.embedding_dim
        self._batch_size = self._settings.embedding_batch_size
        self._client = None

    @property
    def dimension(self) -> int:
        return self._dim

    def _ensure_client(self):
        """Lazily construct the Vertex AI client for embeddings."""
        if self._client is not None:
            return self._client
        try:
            from google import genai  # type: ignore

            self._client = genai.Client(
                vertexai=True,
                project=self._settings.gcp_project_id,
                location=self._settings.gcp_location,
            )
        except Exception as exc:  # pragma: no cover
            raise ExternalServiceError(
                "vertex-ai-embeddings",
                "Failed to initialise the Vertex AI embeddings client.",
            ) from exc
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed all texts, processing them in configured-size batches."""
        if not texts:
            return []
        client = self._ensure_client()
        vectors: list[list[float]] = []

        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            try:
                batch_vectors = await asyncio.to_thread(
                    self._sync_embed_batch, client, batch
                )
            except Exception as exc:  # noqa: BLE001
                if _is_rate_limit(exc):
                    raise RateLimitError(
                        "vertex-ai-embeddings",
                        "Embedding rate limit exceeded.",
                    ) from exc
                raise ExternalServiceError(
                    "vertex-ai-embeddings",
                    f"Embedding request failed: {exc}",
                ) from exc
            vectors.extend(batch_vectors)

        return vectors

    def _sync_embed_batch(self, client, batch: list[str]) -> list[list[float]]:
        """Blocking embedding call for one batch. Runs in a worker thread."""
        result = client.models.embed_content(
            model=self._model_id,
            contents=batch,
        )
        return [list(e.values) for e in result.embeddings]
