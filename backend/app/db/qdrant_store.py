"""
Qdrant vector store wrapper.

Encapsulates every interaction with Qdrant behind one class so the rest of the
app never imports the Qdrant SDK directly. Implements the collection design
from spec section 6:

    * One collection (`codebase_knowledge`).
    * Two NAMED vectors per point: `code` (raw source embedding) and `doc`
      (generated-doc embedding), both 768-dim, cosine distance.
    * Rich payload (file, name, line range, level, full code/doc text, flow
      membership) so the answer agent gets full context with no second lookup.

The wrapper is resilient: if Qdrant is unreachable it raises a typed
`ExternalServiceError` rather than leaking SDK exceptions.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import get_settings
from app.core.constants import VectorName
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class VectorPoint:
    """A single point to upsert into Qdrant.

    `code_vector` is None for Level-2/Level-3 docs (they have no raw code),
    matching the spec: those points carry only the `doc` named vector.
    """

    point_id: str
    doc_vector: list[float]
    payload: dict[str, Any]
    code_vector: list[float] | None = None

    @staticmethod
    def new_id() -> str:
        """Generate a fresh unique point id."""
        return str(uuid.uuid4())


@dataclass
class SearchHit:
    """One result returned from a Qdrant similarity search."""

    point_id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


class QdrantStore:
    """Thin, typed wrapper over the Qdrant client implementing the spec design."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._collection = self._settings.qdrant_collection
        self._dim = self._settings.embedding_dim
        self._client = None  # lazily constructed

    # ------------------------------------------------------------ client --
    def _ensure_client(self):
        """Lazily construct the Qdrant client."""
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(
                host=self._settings.qdrant_host,
                port=self._settings.qdrant_port,
                timeout=30.0,
            )
        except Exception as exc:  # pragma: no cover
            raise ExternalServiceError(
                "qdrant", f"Could not connect to Qdrant: {exc}"
            ) from exc
        return self._client

    # ------------------------------------------------------- collection ---
    async def ensure_collection(self) -> None:
        """Create the `codebase_knowledge` collection if it does not exist.

        Configures the two named vectors exactly as the spec prescribes. Safe
        to call on every startup — existing collections are left untouched.
        """
        await asyncio.to_thread(self._ensure_collection_sync)

    def _ensure_collection_sync(self) -> None:
        from qdrant_client.models import Distance, VectorParams

        client = self._ensure_client()
        try:
            existing = {c.name for c in client.get_collections().collections}
            if self._collection in existing:
                # Verify the existing collection has the right vector config.
                # If a previous run created it with the wrong dimensions (e.g.
                # the offline provider), we must recreate it.
                info = client.get_collection(self._collection)
                vectors_config = info.config.params.vectors
                doc_dim = None
                if isinstance(vectors_config, dict):
                    doc_cfg = vectors_config.get(VectorName.DOC.value)
                    if doc_cfg is not None:
                        doc_dim = getattr(doc_cfg, "size", None)
                if doc_dim is not None and doc_dim != self._dim:
                    logger.warning(
                        "Qdrant collection '%s' has wrong vector size %d "
                        "(expected %d) — recreating it.",
                        self._collection, doc_dim, self._dim,
                    )
                    client.delete_collection(self._collection)
                else:
                    count = client.count(self._collection).count
                    logger.info(
                        "Qdrant collection '%s' already exists (%d points).",
                        self._collection, count,
                    )
                    return

            client.create_collection(
                collection_name=self._collection,
                vectors_config={
                    VectorName.CODE.value: VectorParams(
                        size=self._dim, distance=Distance.COSINE
                    ),
                    VectorName.DOC.value: VectorParams(
                        size=self._dim, distance=Distance.COSINE
                    ),
                },
            )
            logger.info("Created Qdrant collection '%s' (%d-dim).",
                        self._collection, self._dim)
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                "qdrant", f"Failed to ensure collection: {exc}"
            ) from exc

    async def count_points(self) -> int:
        """Return the total number of points in the collection."""
        try:
            result = await asyncio.to_thread(
                lambda: self._ensure_client().count(self._collection).count
            )
            return result
        except Exception:  # noqa: BLE001
            return -1

    # ----------------------------------------------------------- upsert ---
    async def upsert_points(self, points: list[VectorPoint]) -> None:
        """Insert or overwrite a batch of points (idempotent by point id)."""
        if not points:
            return
        await asyncio.to_thread(self._upsert_sync, points)

    def _upsert_sync(self, points: list[VectorPoint]) -> None:
        from qdrant_client.models import PointStruct

        client = self._ensure_client()
        structs: list[PointStruct] = []
        for p in points:
            # Always include the doc vector; include code only when present.
            vectors: dict[str, list[float]] = {VectorName.DOC.value: p.doc_vector}
            if p.code_vector is not None:
                vectors[VectorName.CODE.value] = p.code_vector
            structs.append(
                PointStruct(id=p.point_id, vector=vectors, payload=p.payload)
            )
        try:
            client.upsert(collection_name=self._collection, points=structs)
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                "qdrant", f"Upsert of {len(points)} points failed: {exc}"
            ) from exc

    # ----------------------------------------------------------- search ---
    async def search(
        self,
        query_vector: list[float],
        *,
        using: VectorName,
        limit: int,
        level_filter: int | None = None,
    ) -> list[SearchHit]:
        """Nearest-neighbour search against one named vector.

        Args:
            query_vector: The embedded query.
            using: Which named vector to search (`doc` by default; `code` for
                implementation-specific questions).
            limit: Max hits to return.
            level_filter: If set, restricts results to one documentation level.
        """
        return await asyncio.to_thread(
            self._search_sync, query_vector, using, limit, level_filter
        )

    def _search_sync(
        self,
        query_vector: list[float],
        using: VectorName,
        limit: int,
        level_filter: int | None,
    ) -> list[SearchHit]:
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            MatchValue,
        )

        client = self._ensure_client()
        query_filter = None
        if level_filter is not None:
            query_filter = Filter(
                must=[
                    FieldCondition(
                        key="level", match=MatchValue(value=level_filter)
                    )
                ]
            )
        try:
            results = client.search(
                collection_name=self._collection,
                query_vector=(using.value, query_vector),
                limit=limit,
                query_filter=query_filter,
                with_payload=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                "qdrant", f"Search failed: {exc}"
            ) from exc

        return [
            SearchHit(point_id=str(r.id), score=r.score, payload=r.payload or {})
            for r in results
        ]

    # ----------------------------------------------------------- delete ---
    async def delete_by_file(self, file_path: str) -> None:
        """Delete every point belonging to a file (clean re-index, no orphans)."""
        await asyncio.to_thread(self._delete_by_file_sync, file_path)

    def _delete_by_file_sync(self, file_path: str) -> None:
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            FilterSelector,
            MatchValue,
        )

        client = self._ensure_client()
        try:
            client.delete(
                collection_name=self._collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="file", match=MatchValue(value=file_path)
                            )
                        ]
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                "qdrant", f"Delete-by-file failed: {exc}"
            ) from exc

    async def health_check(self) -> bool:
        """Return True if Qdrant is reachable. Used by the /health endpoint."""
        try:
            await asyncio.to_thread(lambda: self._ensure_client().get_collections())
            return True
        except Exception:  # noqa: BLE001
            return False


# Process-level singleton — one client/connection pool per process.
_store: QdrantStore | None = None


def get_qdrant_store() -> QdrantStore:
    """Return the process-wide QdrantStore singleton."""
    global _store
    if _store is None:
        _store = QdrantStore()
    return _store
