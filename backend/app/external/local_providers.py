"""
Local fallback providers.

Deterministic, dependency-free implementations of the three provider interfaces.
They let the entire system boot, run, and be tested with **no cloud account** —
essential for CI, local development, and demos before GCP credentials exist.

They are intentionally simple:
    * `LocalEmbeddingProvider` hashes text into a stable pseudo-vector. Two
      identical texts always map to the same vector, and similar texts share
      hash buckets, so nearest-neighbour search is *meaningful enough* to demo.
    * `LocalLLMProvider` returns structured, clearly-labelled placeholder text
      so the pipeline and UI exercise the real code paths.
    * `LocalReranker` scores by simple term overlap — a reasonable proxy.

These are NOT meant to match real model quality; they exist so that the
*architecture* is fully runnable. Supplying real keys swaps them out via
`provider_factory` with zero caller changes.
"""

from __future__ import annotations

import hashlib
import math
import re

from app.config.settings import get_settings
from app.core.logging import get_logger
from app.external.interfaces import (
    EmbeddingProvider,
    LLMProvider,
    LLMResponse,
    RerankerProvider,
    RerankResult,
)

logger = get_logger(__name__)


class LocalEmbeddingProvider(EmbeddingProvider):
    """Deterministic hash-based embedding provider for offline use.

    Produces L2-normalised vectors of the configured dimension. The same text
    always yields the same vector (determinism), and shared tokens pull two
    texts' vectors closer (rough semantic proxy).
    """

    def __init__(self, dimension: int | None = None) -> None:
        self._dim = dimension or get_settings().embedding_dim

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each text into a deterministic, normalised pseudo-vector."""
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        """Hash the text's tokens into a bag-of-words vector, then normalise."""
        vec = [0.0] * self._dim
        tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
        if not tokens:
            # Avoid an all-zero vector (undefined cosine distance).
            vec[0] = 1.0
            return vec

        for token in tokens:
            # Map each token deterministically to a bucket and a sign.
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[bucket] += sign

        # L2-normalise so cosine similarity behaves correctly.
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class LocalLLMProvider(LLMProvider):
    """Offline placeholder LLM.

    Returns clearly-labelled, structured text so every downstream consumer
    (doc pipeline, answer agent, validation agent) runs end-to-end. The output
    is obviously a placeholder so it is never mistaken for a real answer.
    """

    _MODEL = "local-fallback-llm"

    @property
    def model_name(self) -> str:
        return self._MODEL

    async def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        """Return a deterministic placeholder response derived from the prompt."""
        snippet = prompt.strip().replace("\n", " ")[:200]
        text = (
            "[LOCAL FALLBACK MODEL OUTPUT — configure GCP/Vertex AI for real "
            "generation]\n\n"
            f"Prompt received ({len(prompt)} chars). Summary of request: "
            f"{snippet}..."
        )
        # Rough token estimate (~4 chars/token) keeps usage metrics populated.
        return LLMResponse(
            text=text,
            model=self._MODEL,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(text) // 4,
        )


class LocalReranker(RerankerProvider):
    """Offline reranker scoring by query/document token overlap (Jaccard)."""

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int
    ) -> list[RerankResult]:
        """Score each document by token overlap with the query, keep top_n."""
        q_tokens = set(re.findall(r"[A-Za-z0-9_]+", query.lower()))
        scored: list[RerankResult] = []

        for idx, doc in enumerate(documents):
            d_tokens = set(re.findall(r"[A-Za-z0-9_]+", doc.lower()))
            if not q_tokens or not d_tokens:
                score = 0.0
            else:
                # Jaccard similarity: |intersection| / |union|.
                overlap = len(q_tokens & d_tokens)
                union = len(q_tokens | d_tokens)
                score = overlap / union if union else 0.0
            scored.append(RerankResult(index=idx, score=score))

        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_n]
