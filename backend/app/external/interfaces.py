"""
Abstract provider interfaces (Ports).

These Abstract Base Classes define the *contracts* the application depends on
for its external AI capabilities. Concrete implementations (Vertex AI, Cohere,
local fallbacks) live in sibling modules and are selected at runtime by
`provider_factory`.

This is the Dependency Inversion Principle in practice: high-level code
(agents, pipeline) depends on these abstractions, never on a concrete SDK. It
is also Interface Segregation — three small, focused interfaces rather than one
"AI service" god-interface.

Swapping Vertex AI for another provider, or running fully offline with the
local fallbacks, requires zero changes to any caller.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


# --------------------------------------------------------------------------- #
# Data transfer objects                                                       #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LLMResponse:
    """The result of a single LLM generation call.

    Attributes:
        text: The generated text.
        model: Identifier of the model that produced it.
        prompt_tokens: Tokens consumed by the prompt (0 if unknown).
        completion_tokens: Tokens produced (0 if unknown).
    """

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens billed for this call."""
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class RerankResult:
    """One reranked item: the original index plus its relevance score."""

    index: int      # position in the input list passed to the reranker
    score: float    # relevance score, higher is better


# --------------------------------------------------------------------------- #
# Ports (abstract interfaces)                                                 #
# --------------------------------------------------------------------------- #
class LLMProvider(ABC):
    """Contract for a large-language-model text generator."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifier of the underlying model."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        """Generate text for a single prompt.

        Args:
            prompt: The user/content prompt.
            system_instruction: Optional system-level steering instruction.
            temperature: Sampling temperature; low for factual doc generation.
            max_output_tokens: Hard cap on generated length.

        Returns:
            An `LLMResponse`.

        Raises:
            ExternalServiceError / RateLimitError: On provider failure.
        """


class EmbeddingProvider(ABC):
    """Contract for a text-embedding model."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors produced."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into fixed-size float vectors.

        Args:
            texts: Texts to embed (code snippets or doc text).

        Returns:
            One vector per input text, in the same order.
        """


class RerankerProvider(ABC):
    """Contract for a relevance reranker (e.g. Cohere Rerank)."""

    @abstractmethod
    async def rerank(
        self, query: str, documents: list[str], *, top_n: int
    ) -> list[RerankResult]:
        """Re-score `documents` against `query` and return the best `top_n`.

        Args:
            query: The search query.
            documents: Candidate document texts (e.g. Qdrant's top 15).
            top_n: How many results to keep.

        Returns:
            `RerankResult`s sorted by descending relevance.
        """
