"""
Retrieval service — the RAG retrieval core.

Implements the spec's two-stage retrieval (spec section 8):

    1. Embed the query, then search Qdrant for the top-K nearest neighbours
       (default K=15) against the `doc` named vector.
    2. Rerank those candidates with the reranker (Cohere or local fallback) and
       keep the top-N (default N=5).

Two-stage retrieval matters because vector search is fast but approximate;
the reranker is slower but far more precise. Searching wide then reranking
narrow gives both recall and precision.

The service is deliberately free of agent/ADK concerns — the retrieval agent
simply calls `retrieve()`. That keeps the RAG logic unit-testable on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import get_settings
from app.core.constants import VectorName
from app.core.logging import get_logger
from app.db.qdrant_store import QdrantStore
from app.external.interfaces import RerankerProvider
from app.observability.tracing import traced_span
from app.pipeline.embedder import Embedder

logger = get_logger(__name__)


@dataclass
class RetrievedSource:
    """One retrieved, reranked source passed to the answer agent.

    Carries everything needed to cite it: file, symbol name, line range, the
    documentation text, and (for L1) the raw code.
    """

    file_path: str
    name: str
    level: int
    level_label: str
    start_line: int
    end_line: int
    doc_text: str
    code_text: str
    score: float            # reranker relevance score
    flow_membership: list[str]

    @property
    def citation(self) -> str:
        """A compact citation string, e.g. `auth.py::login (L10-L42)`."""
        loc = (
            f" (L{self.start_line}-L{self.end_line})"
            if self.start_line and self.end_line
            else ""
        )
        return f"{self.file_path}::{self.name}{loc}"


class RetrievalService:
    """Coordinates embedding, vector search, and reranking."""

    def __init__(
        self,
        embedder: Embedder,
        qdrant: QdrantStore,
        reranker: RerankerProvider,
    ) -> None:
        """Inject the three collaborators (all behind interfaces)."""
        self._embedder = embedder
        self._qdrant = qdrant
        self._reranker = reranker
        settings = get_settings()
        self._top_k = settings.retrieval_top_k   # candidates from Qdrant
        self._top_n = settings.rerank_top_k       # results kept after rerank

    async def retrieve(
        self,
        query: str,
        *,
        level_filter: int | None = None,
        search_code: bool = False,
    ) -> list[RetrievedSource]:
        """Retrieve and rerank the most relevant sources for a query.

        Args:
            query: The developer's natural-language question.
            level_filter: Optionally restrict to one documentation level.
            search_code: If True, search the `code` vector instead of `doc` —
                useful for "show me the implementation of X" questions.

        Returns:
            Up to `rerank_top_k` sources, ordered most-relevant first.
        """
        with traced_span(
            "retrieval.retrieve",
            {"top_k": self._top_k, "top_n": self._top_n, "code": search_code},
        ):
            # Stage 0 — embed the query.
            query_vector = await self._embedder.embed_query(query)

            # Stage 1 — wide vector search in Qdrant.
            hits = await self._qdrant.search(
                query_vector,
                using=VectorName.CODE if search_code else VectorName.DOC,
                limit=self._top_k,
                level_filter=level_filter,
            )
            if not hits:
                logger.info("Retrieval found no candidates for query.")
                return []

            # Stage 2 — narrow rerank for precision.
            documents = [self._hit_text(h.payload) for h in hits]
            reranked = await self._reranker.rerank(
                query, documents, top_n=self._top_n
            )

            # Map reranked indices back to the original hits.
            sources: list[RetrievedSource] = []
            for result in reranked:
                hit = hits[result.index]
                payload = hit.payload
                # If this point is a paragraph chunk of a longer doc, return
                # the full doc text to the answer agent for complete context.
                # The matched chunk text is still used by the reranker (accurate
                # relevance scoring), but the answer agent sees the full doc.
                doc_text = (
                    payload.get("full_doc_text")
                    or payload.get("doc_text", "")
                )
                sources.append(
                    RetrievedSource(
                        file_path=payload.get("file", ""),
                        name=payload.get("name", ""),
                        level=payload.get("level", 0),
                        level_label=payload.get("level_label", ""),
                        start_line=payload.get("start_line", 0),
                        end_line=payload.get("end_line", 0),
                        doc_text=doc_text,
                        code_text=payload.get("code_text", ""),
                        score=result.score,
                        flow_membership=payload.get("flow_membership", []),
                    )
                )

        logger.info(
            "Retrieval returned %d sources (from %d candidates).",
            len(sources),
            len(hits),
        )
        return sources

    @staticmethod
    def _hit_text(payload: dict) -> str:
        """Build the text the reranker scores for one hit.

        Doc text plus code gives the reranker the richest signal for judging
        true relevance.
        """
        doc = payload.get("doc_text", "")
        code = payload.get("code_text", "")
        return f"{doc}\n\n{code}".strip()
