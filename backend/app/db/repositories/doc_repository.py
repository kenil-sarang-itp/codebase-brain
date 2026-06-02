"""
Documentation & static-analysis persistence.

Covers four spec tables that the pipeline reads and writes constantly:
generated_docs, doc_status, call_graph, and flow_membership. Keeping them in
one repository is deliberate — they are always mutated together (a doc, its
status row, and its flow membership), so a single repository keeps those
multi-table writes consistent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import DocLevel
from app.db.models import (
    CallGraphEntry,
    DocStatus,
    FlowMembership,
    GeneratedDoc,
)
from app.db.repositories.base import BaseRepository


class DocRepository(BaseRepository[GeneratedDoc]):
    """Repository for generated docs, call graph, doc status, flow membership."""

    model = GeneratedDoc

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ----------------------------------------------------- generated docs --
    async def upsert_doc(self, doc: GeneratedDoc) -> GeneratedDoc:
        """Insert a generated doc, replacing any existing doc for the same
        (file_path, function_name, level) triple.

        On re-index we must not accumulate stale duplicate docs, so an existing
        doc for the same target is deleted first.
        """
        await self._session.execute(
            delete(GeneratedDoc).where(
                GeneratedDoc.file_path == doc.file_path,
                GeneratedDoc.function_name == doc.function_name,
                GeneratedDoc.level == doc.level,
            )
        )
        return await self.add(doc)

    async def get_module_doc(self, file_path: str) -> GeneratedDoc | None:
        """Return the Level-2 (module) doc for a file, if generated."""
        stmt = select(GeneratedDoc).where(
            GeneratedDoc.file_path == file_path,
            GeneratedDoc.level == DocLevel.MODULE.value,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_architecture_docs(self) -> list[GeneratedDoc]:
        """Return all Level-3 (architecture / data-flow) docs."""
        stmt = select(GeneratedDoc).where(
            GeneratedDoc.level == DocLevel.ARCHITECTURE.value
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_docs_by_ids(self, point_ids: list[str]) -> list[GeneratedDoc]:
        """Fetch generated docs by their Qdrant point ids (used after search)."""
        if not point_ids:
            return []
        stmt = select(GeneratedDoc).where(
            GeneratedDoc.qdrant_point_id.in_(point_ids)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_docs_for_file(self, file_path: str) -> None:
        """Remove every generated doc for a file (used before re-indexing it)."""
        await self._session.execute(
            delete(GeneratedDoc).where(GeneratedDoc.file_path == file_path)
        )

    # --------------------------------------------------------- call graph --
    async def upsert_call_graph_entry(self, entry: CallGraphEntry) -> None:
        """Insert-or-update one call-graph row keyed by function name.

        Uses PostgreSQL's native ON CONFLICT so re-running static analysis on a
        changed file overwrites cleanly with no read-modify-write race.
        """
        stmt = pg_insert(CallGraphEntry).values(
            function_name=entry.function_name,
            file_path=entry.file_path,
            calls=entry.calls,
            called_by=entry.called_by,
            language=entry.language,
            last_updated=datetime.now(timezone.utc),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[CallGraphEntry.function_name],
            set_={
                "file_path": stmt.excluded.file_path,
                "calls": stmt.excluded.calls,
                "called_by": stmt.excluded.called_by,
                "language": stmt.excluded.language,
                "last_updated": stmt.excluded.last_updated,
            },
        )
        await self._session.execute(stmt)

    async def get_call_graph_entry(
        self, function_name: str
    ) -> CallGraphEntry | None:
        """Return a single call-graph entry by function name."""
        return await self._session.get(CallGraphEntry, function_name)

    async def get_entry_points(self) -> list[CallGraphEntry]:
        """Return all entry points — functions that nothing else calls.

        Entry points (empty `called_by`) are the starts of data flows and the
        basis for Level-3 doc generation.
        """
        stmt = select(CallGraphEntry)
        result = await self._session.execute(stmt)
        return [e for e in result.scalars().all() if not e.called_by]

    async def get_all_call_graph(self) -> list[CallGraphEntry]:
        """Return the entire call graph (used for recursive flow tracing)."""
        result = await self._session.execute(select(CallGraphEntry))
        return list(result.scalars().all())

    # --------------------------------------------------------- doc status --
    async def mark_for_regeneration(self, item_id: str, level: int) -> None:
        """Flag a doc (file or function) as needing regeneration.

        Inserts the status row if missing — central to PR impact analysis.
        """
        stmt = pg_insert(DocStatus).values(
            item_id=item_id,
            level=level,
            last_code_changed=datetime.now(timezone.utc),
            needs_regeneration=True,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DocStatus.item_id],
            set_={
                "needs_regeneration": True,
                "last_code_changed": datetime.now(timezone.utc),
            },
        )
        await self._session.execute(stmt)

    async def clear_regeneration_flag(self, item_id: str) -> None:
        """Mark a doc freshly generated — flips the regeneration flag off."""
        await self._session.execute(
            update(DocStatus)
            .where(DocStatus.item_id == item_id)
            .values(
                needs_regeneration=False,
                last_generated=datetime.now(timezone.utc),
            )
        )

    async def get_items_needing_regeneration(
        self, level: int | None = None
    ) -> list[DocStatus]:
        """Return doc-status rows still flagged for regeneration.

        On worker restart this is exactly the "resume" query — whatever is
        still TRUE is unfinished work.
        """
        stmt = select(DocStatus).where(DocStatus.needs_regeneration.is_(True))
        if level is not None:
            stmt = stmt.where(DocStatus.level == level)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ----------------------------------------------------- flow membership --
    async def set_flow_membership(
        self, flow_name: str, members: list[tuple[str, str]]
    ) -> None:
        """Replace a flow's membership with (function_name, file_path) pairs."""
        await self._session.execute(
            delete(FlowMembership).where(FlowMembership.flow_name == flow_name)
        )
        self._session.add_all(
            [
                FlowMembership(
                    flow_name=flow_name,
                    function_name=fn,
                    file_path=fp,
                )
                for fn, fp in members
            ]
        )
        await self._session.flush()

    async def get_flows_for_file(self, file_path: str) -> list[str]:
        """Return the names of all flows a given file participates in."""
        stmt = select(FlowMembership.flow_name).where(
            FlowMembership.file_path == file_path
        )
        result = await self._session.execute(stmt)
        return list({row[0] for row in result.all()})

    async def get_flow_members(self, flow_name: str) -> list[FlowMembership]:
        """Return every membership row for a named flow."""
        stmt = select(FlowMembership).where(
            FlowMembership.flow_name == flow_name
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
