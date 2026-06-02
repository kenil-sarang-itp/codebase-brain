"""
Indexer — vector-store and doc-store write stage.

The final pipeline stage. It takes generated docs plus their embeddings and
persists them to *both* stores, keeping them consistent (spec section 6):

    * Qdrant — one point per item, with the `code` and/or `doc` named vectors
      and a rich payload (file, name, lines, level, full text, flow membership).
    * PostgreSQL `generated_docs` — the authoritative full-text copy that
      survives a Qdrant rebuild and feeds the answer agent.

On re-index it deletes a file's old points by `payload.file` first, so no
orphaned vectors accumulate.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.constants import DocLevel
from app.core.logging import get_logger
from app.db.models import GeneratedDoc
from app.db.qdrant_store import QdrantStore, VectorPoint
from app.db.repositories.doc_repository import DocRepository
from app.observability.tracing import traced_span

logger = get_logger(__name__)


@dataclass
class IndexItem:
    """One fully-prepared item ready to be indexed.

    Bundles everything the indexer needs for a single function/module/flow:
    identity, text, the generated doc, embeddings, and metadata.

    For long L2/L3 docs, `doc_chunks` and `doc_chunk_vectors` are populated
    by `_index_doc_items` before calling `index_items`. The indexer then
    creates one Qdrant point per chunk so retrieval can pinpoint the exact
    paragraph rather than the whole document. PostgreSQL always gets the full
    `doc_text` in a single row (the authoritative copy).
    """

    file_path: str
    name: str                       # function name, or file path for L2/L3
    level: DocLevel
    doc_text: str
    doc_vector: list[float]
    code_text: str | None = None    # raw code (L1 only)
    code_vector: list[float] | None = None  # code embedding (L1 only)
    language: str = "unknown"
    start_line: int = 0
    end_line: int = 0
    flow_membership: list[str] | None = None
    # Populated for long L2/L3 docs — one entry per paragraph chunk.
    doc_chunks: list[str] | None = None
    doc_chunk_vectors: list[list[float]] | None = None


class Indexer:
    """Writes generated docs to Qdrant and PostgreSQL atomically-ish."""

    def __init__(self, qdrant: QdrantStore, doc_repo: DocRepository) -> None:
        """Inject the vector store and the doc repository.

        The repository's session is owned by the caller, so the caller's
        transaction boundary also covers the Postgres writes here.
        """
        self._qdrant = qdrant
        self._doc_repo = doc_repo

    async def index_items(self, items: list[IndexItem]) -> None:
        """Index a batch of prepared items into both stores.

        Storage strategy (crash-safety order: PostgreSQL first, then Qdrant):

          PostgreSQL — ONE row per item, full doc_text. The authoritative copy.
          Qdrant      — ONE point per doc chunk so retrieval can pinpoint the
                        exact paragraph in a long L2/L3 doc. L1 function docs
                        are already chunk-sized so they produce a single point.

        If Qdrant fails, the full text survives in PostgreSQL and vectors can
        be rebuilt without data loss.
        """
        if not items:
            return

        with traced_span("indexer.index_items", {"count": len(items)}):
            qdrant_points: list[VectorPoint] = []

            for item in items:
                # ── PostgreSQL: one row, full doc text ───────────────────────
                # Use the first chunk's point-id as the "primary" reference so
                # PostgreSQL always has one citable Qdrant id per doc row.
                primary_point_id = VectorPoint.new_id()

                await self._doc_repo.upsert_doc(
                    GeneratedDoc(
                        file_path=item.file_path,
                        # L2 module docs are keyed by file_path alone (function_name=NULL).
                        # L1 and L3 both need item.name so each row is unique:
                        #   L1 → chunk.name (function/class name)
                        #   L3 → flow name or "application_overview"
                        # Without this, all L3 docs share the same key and each
                        # overwrites the previous, leaving only the last one in the DB.
                        function_name=(
                            None
                            if item.level == DocLevel.MODULE
                            else item.name
                        ),
                        level=item.level.value,
                        doc_text=item.doc_text,       # full LLM response, never truncated
                        code_text=item.code_text,
                        qdrant_point_id=primary_point_id,
                    )
                )

                # ── Qdrant: one point per paragraph chunk ────────────────────
                chunks = item.doc_chunks or [item.doc_text]
                chunk_vecs = item.doc_chunk_vectors or [item.doc_vector]
                total_chunks = len(chunks)

                base_payload = self._build_payload(item)

                for i, (chunk_text, chunk_vec) in enumerate(zip(chunks, chunk_vecs)):
                    point_id = primary_point_id if i == 0 else VectorPoint.new_id()

                    # Each chunk carries the parent's metadata plus which slice
                    # of the doc it represents, so the answer agent can reassemble.
                    payload = {
                        **base_payload,
                        "doc_text": chunk_text,        # override: just this chunk
                        "full_doc_text": item.doc_text if total_chunks > 1 else None,
                        "chunk_index": i,
                        "total_chunks": total_chunks,
                    }
                    qdrant_points.append(
                        VectorPoint(
                            point_id=point_id,
                            doc_vector=chunk_vec,
                            # Only the first chunk carries the code vector so
                            # code-search still finds the right item.
                            code_vector=item.code_vector if i == 0 else None,
                            payload=payload,
                        )
                    )

            # 3. Flush writes to PostgreSQL within the worker's transaction.
            await self._doc_repo.flush()

            # 4. Upsert all Qdrant points in one batch.
            await self._qdrant.upsert_points(qdrant_points)

        total_qdrant = sum(
            len(it.doc_chunks) if it.doc_chunks else 1 for it in items
        )
        logger.info(
            "Indexed %d items → %d PostgreSQL rows, %d Qdrant points",
            len(items), len(items), total_qdrant,
        )

    async def reindex_file(self, file_path: str) -> None:
        """Remove a file's existing docs/vectors before it is re-indexed.

        Deletes from Qdrant (by `payload.file`) and PostgreSQL so a re-index
        replaces rather than duplicates.
        """
        with traced_span("indexer.reindex_file", {"file": file_path}):
            await self._qdrant.delete_by_file(file_path)
            await self._doc_repo.delete_docs_for_file(file_path)
        logger.info("Cleared existing docs/vectors for %s", file_path)

    # ------------------------------------------------------------ helpers --
    @staticmethod
    def split_doc_chunks(doc_text: str, max_chars: int = 900) -> list[str]:
        """Split a long generated doc into paragraph-sized chunks for embedding.

        Splits on double-newline paragraph boundaries (the natural structure of
        LLM-generated markdown). Short paragraphs are merged together until the
        chunk approaches `max_chars`. This keeps each vector focused on one
        topic (e.g. "Failure modes" or "Data flow") rather than averaging over
        the whole document.

        A doc shorter than `max_chars` is returned as a single-element list so
        the caller never needs to special-case short vs. long docs.
        """
        paragraphs = [p.strip() for p in doc_text.split("\n\n") if p.strip()]
        if not paragraphs:
            return [doc_text.strip()] if doc_text.strip() else []

        chunks: list[str] = []
        current_parts: list[str] = []
        current_len = 0

        for para in paragraphs:
            if current_parts and current_len + len(para) + 2 > max_chars:
                chunks.append("\n\n".join(current_parts))
                current_parts = [para]
                current_len = len(para)
            else:
                current_parts.append(para)
                current_len += len(para) + 2

        if current_parts:
            chunks.append("\n\n".join(current_parts))

        return chunks if chunks else [doc_text]

    @staticmethod
    def _build_payload(item: IndexItem) -> dict:
        """Build the Qdrant payload for an item (spec section 6 structure).

        The payload stores both the code text and doc text so the answer agent
        gets full context from the search result with no second lookup.
        """
        level_label = {
            DocLevel.FUNCTION: "function",
            DocLevel.MODULE: "module",
            DocLevel.ARCHITECTURE: "architecture",
        }[item.level]

        return {
            "file": item.file_path,
            "name": item.name,
            "start_line": item.start_line,
            "end_line": item.end_line,
            "language": item.language,
            "level": item.level.value,          # 1 / 2 / 3 — enables filtering
            "level_label": level_label,
            "code_text": item.code_text or "",
            "doc_text": item.doc_text,
            "flow_membership": item.flow_membership or [],
        }
