"""
Indexing service — the six-phase indexing pipeline orchestrator.

This service runs the spec's initial-indexing flow end to end:

    Phase 1 — Discovery:        list repo files, apply the skip list.
    Phase 2 — Static analysis:  chunk every file, build the call graph.
    Phase 3 — Level-3 docs:     architecture / data-flow docs (generated FIRST).
    Phase 4 — Level-2 docs:     one module doc per file.
    Phase 5 — Level-1 docs:     one five-section doc per function/class.
    Phase 6 — Complete:         mark the session done.

Doc levels are generated 3→2→1 so each level's output becomes context for the
next (cascading context). The service also implements impact-based PR
regeneration: given a PR's changed files, it determines exactly which docs are
affected and regenerates only those.

It is written to run inside an RQ worker (synchronous entry points exist in
`workers/tasks.py`), but its core is async so it can also be exercised directly
in tests. Long-running work never touches the API request path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.constants import DocLevel, IndexingStatus
from app.core.logging import get_logger
from app.db.qdrant_store import QdrantStore
from app.db.repositories.doc_repository import DocRepository
from app.db.repositories.indexing_repository import IndexingRepository
from app.external.repository_source import RepositorySource
from app.observability.tracing import root_span, set_trace_context, traced_span
from app.pipeline.call_graph import CallGraph, CallGraphBuilder
from app.pipeline.chunking.chunker import Chunker
from app.pipeline.doc_generator import DocGenerator
from app.pipeline.embedder import Embedder
from app.pipeline.indexer import IndexItem, Indexer
from app.pipeline.static_analysis import StaticAnalyzer

logger = get_logger(__name__)


@dataclass
class _RepoData:
    """In-memory working set assembled during a run (files, chunks, graph)."""

    file_contents: dict[str, str]
    chunks_by_file: dict[str, list]
    call_graph: CallGraph


class IndexingService:
    """Orchestrates initial indexing and impact-based PR regeneration."""

    def __init__(
        self,
        *,
        repo_source: RepositorySource,
        indexing_repo: IndexingRepository,
        doc_repo: DocRepository,
        qdrant: QdrantStore,
        chunker: Chunker,
        analyzer: StaticAnalyzer,
        doc_generator: DocGenerator,
        embedder: Embedder,
        indexer: Indexer,
    ) -> None:
        """Inject every collaborator — the service owns orchestration only."""
        self._repo = repo_source
        self._sessions = indexing_repo
        self._docs = doc_repo
        self._qdrant = qdrant
        self._chunker = chunker
        self._analyzer = analyzer
        self._doc_gen = doc_generator
        self._embedder = embedder
        self._indexer = indexer
        self._graph_builder = CallGraphBuilder()

    # ===================================================================== #
    # Initial indexing                                                      #
    # ===================================================================== #
    async def run_initial_indexing(self, session_id: str) -> None:
        """Execute all six indexing phases for a session.

        Status is advanced in the DB after each phase so `/index-status` shows
        live progress. Any failure marks the session FAILED with the error
        message and re-raises so the RQ job is recorded as failed.
        """
        try:
            await self._qdrant.ensure_collection()

            with root_span("indexing.run", session_id=session_id, attributes={"indexing.session": session_id}):
                repo_data = await self._phase_discovery_and_analysis(session_id)
                overview = await self._phase_l3(session_id, repo_data)
                await self._phase_l2(session_id, repo_data, overview)
                await self._phase_l1(session_id, repo_data, overview)

            await self._sessions.update_status(
                session_id, IndexingStatus.COMPLETE
            )
            logger.info("Indexing session %s complete.", session_id)

        except Exception as exc:  # noqa: BLE001
            logger.exception("Indexing session %s failed.", session_id)
            await self._sessions.update_status(
                session_id,
                IndexingStatus.FAILED,
                error_message=str(exc),
            )
            raise

    # ----------------------------------------------- phases 1 & 2 ---------
    async def _phase_discovery_and_analysis(
        self, session_id: str
    ) -> _RepoData:
        """Phases 1-2: discover files, chunk them, build the call graph."""
        # Phase 1 — discovery.
        await self._sessions.update_status(
            session_id, IndexingStatus.DISCOVERING
        )
        all_paths = await self._repo.list_files()
        # Apply the skip list up front so counts reflect real work.
        code_paths = [p for p in all_paths if not self._chunker.should_skip(p)]
        await self._sessions.update_counters(
            session_id, total_files=len(code_paths)
        )
        logger.info(
            "Discovery: %d files (%d after skip list).",
            len(all_paths),
            len(code_paths),
        )

        # Phase 2 — static analysis: read, chunk, analyse.
        await self._sessions.update_status(
            session_id, IndexingStatus.ANALYSING
        )
        file_contents: dict[str, str] = {}
        chunks_by_file: dict[str, list] = {}
        all_functions = []

        # --- Concurrent file fetching -------------------------------------------
        # Fetch all files in parallel (5 at a time) using a single persistent
        # MCP session. This eliminates the per-file TCP+TLS+MCP handshake
        # overhead that made serial fetching take 4-5 minutes for a 100-file
        # repo. With 5 concurrent workers and one persistent session, the same
        # 100 files take ~30-60 seconds.
        #
        # The semaphore (max_concurrent=5) keeps us well within GitHub's
        # secondary rate limit of 100 concurrent requests. Increase to 10 if
        # you want more speed and your account has room.
        logger.info(
            "Fetching %d files concurrently (max 5 at a time)...", len(code_paths)
        )

        # Use concurrent batch fetching if the repo source supports it.
        if hasattr(self._repo, "get_files_concurrent"):
            raw_contents = await self._repo.get_files_concurrent(
                code_paths, max_concurrent=5
            )
        else:
            # Fallback for local repo source (already fast, no MCP overhead).
            raw_contents = {}
            for path in code_paths:
                try:
                    repo_file = await self._repo.get_file(path)
                    raw_contents[path] = repo_file.content
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Skipping file %s: %s", path, exc)
                    raw_contents[path] = ""

        # Process the fetched contents: chunk + analyse.
        fetched = 0
        skipped_empty = 0
        for path in code_paths:
            content = raw_contents.get(path, "")

            # Skip empty files — nothing to chunk or document.
            if not content or not content.strip():
                logger.debug("Skipping empty/failed file: %s", path)
                skipped_empty += 1
                await self._sessions.increment_processed_files(session_id)
                continue

            file_contents[path] = content

            # Chunk the file (syntax-aware where possible).
            file_chunks = self._chunker.chunk_file(path, content)
            chunks_by_file[path] = file_chunks.chunks

            # Static analysis → per-function call info.
            functions = self._analyzer.analyze_file(path, content)
            if functions:
                logger.debug(
                    "Static analysis: %s → %d functions: %s",
                    path, len(functions), [f.name for f in functions],
                )
            all_functions.extend(functions)

            fetched += 1
            await self._sessions.increment_processed_files(session_id)

        logger.info(
            "Fetched and processed %d/%d files (%d skipped empty).",
            fetched, len(code_paths), skipped_empty,
        )

        # Summarise what static analysis found before building the graph.
        lang_breakdown: dict[str, int] = {}
        file_function_counts: dict[str, int] = {}
        for fn_info in all_functions:
            lang_breakdown[fn_info.language] = (
                lang_breakdown.get(fn_info.language, 0) + 1
            )
            file_function_counts[fn_info.file_path] = (
                file_function_counts.get(fn_info.file_path, 0) + 1
            )

        if all_functions:
            logger.info(
                "Static analysis: %d functions found across %d files. "
                "Language breakdown: %s",
                len(all_functions),
                len(file_function_counts),
                dict(sorted(lang_breakdown.items())),
            )
        else:
            logger.warning(
                "Static analysis: NO functions found in any of the %d "
                "processed files. Call graph will be empty. "
                "Check that the repo contains .py / .js / .ts / .java / "
                ".go / .rs / .c / .cpp files that are not in the skip list.",
                fetched,
            )

        # Build and persist the call graph.
        call_graph = self._graph_builder.build(all_functions)
        await self._persist_call_graph(call_graph)

        total_functions = sum(len(c) for c in chunks_by_file.values())
        await self._sessions.update_counters(
            session_id, total_functions=total_functions
        )
        logger.info(
            "Analysis: %d chunks, %d call-graph nodes, %d entry points.",
            total_functions,
            len(call_graph.defined_in),
            len(call_graph.entry_points),
        )
        if call_graph.entry_points:
            logger.info(
                "Entry points detected: %s",
                call_graph.entry_points[:20],  # cap at 20 so log stays readable
            )

        return _RepoData(
            file_contents=file_contents,
            chunks_by_file=chunks_by_file,
            call_graph=call_graph,
        )

    # --------------------------------------------------- phase 3: L3 ------
    async def _phase_l3(
        self, session_id: str, repo: _RepoData
    ) -> str:
        """Phase 3: generate Level-3 architecture/data-flow docs.

        Generated FIRST so the overview can be cascaded into L2 and L1. Returns
        the application-overview text for that cascading.
        """
        await self._sessions.update_status(
            session_id, IndexingStatus.GENERATING_L3
        )
        graph = repo.call_graph

        # ── Overview: build rich code context so the LLM sees real code ──────
        module_summary = "\n".join(
            f"- {path}: {len(chunks)} definitions"
            for path, chunks in sorted(repo.chunks_by_file.items())
        )
        entry_points_text = "\n".join(
            f"- {ep} (in {graph.defined_in.get(ep, '?')})"
            for ep in graph.entry_points
        ) or "No entry points detected."

        # README gives the most human-readable project description.
        readme_content = ""
        for readme_name in ("README.md", "readme.md", "README.rst", "README"):
            if readme_name in repo.file_contents:
                readme_content = repo.file_contents[readme_name][:4000]
                break

        # Top files by function count — first lines typically have module-level
        # docstrings and imports that reveal the module's purpose.
        file_fn_counts: dict[str, int] = {}
        for fn, fp in graph.defined_in.items():
            file_fn_counts[fp] = file_fn_counts.get(fp, 0) + 1
        top_files = sorted(file_fn_counts.items(), key=lambda x: -x[1])[:12]
        file_summaries: list[str] = []
        for path, fn_count in top_files:
            content = repo.file_contents.get(path, "")
            first_lines = "\n".join(content.splitlines()[:15])
            fns_here = sorted(
                fn for fn, fp in graph.defined_in.items() if fp == path
            )[:12]
            file_summaries.append(
                f"--- {path} ({fn_count} functions: {', '.join(fns_here)}) ---\n"
                f"{first_lines}"
            )

        # Cross-file call graph: which files call into which other files.
        # This gives the LLM the dependency topology without dumping all edges.
        file_deps: dict[str, set[str]] = {}
        for fn, file_from in graph.defined_in.items():
            for callee in graph.calls.get(fn, set()):
                file_to = graph.defined_in.get(callee)
                if file_to and file_to != file_from:
                    file_deps.setdefault(file_from, set()).add(file_to)
        file_dep_lines = [
            f"- {src} → {', '.join(sorted(dsts))}"
            for src, dsts in sorted(file_deps.items())
        ]

        code_context = ""
        if readme_content:
            code_context += f"=== README ===\n{readme_content}\n\n"
        if file_summaries:
            code_context += (
                "=== KEY FILE SUMMARIES (top files by function count) ===\n"
                + "\n\n".join(file_summaries)
            )
        if file_dep_lines:
            code_context += (
                "\n\n=== CROSS-FILE CALL GRAPH (which files call into which) ===\n"
                + "\n".join(file_dep_lines)
            )

        overview = await self._doc_gen.generate_overview_doc(
            module_summary, entry_points_text, code_context=code_context
        )

        # Index the overview itself as a Level-3 doc.
        await self._index_doc_items(
            [
                self._make_doc_item(
                    file_path="<architecture>",
                    name="application_overview",
                    level=DocLevel.ARCHITECTURE,
                    doc_text=overview,
                )
            ]
        )

        # ── Flow docs: look up actual source for each fn in the chain ─────────
        # Build a name → formatted code snippet lookup from the already-chunked data.
        chunk_lookup: dict[str, str] = {}
        for path, chunks in repo.chunks_by_file.items():
            for chunk in chunks:
                snippet = (
                    f"### `{chunk.name}` — {path} "
                    f"(L{chunk.start_line}–L{chunk.end_line})\n"
                    f"```\n{chunk.code[:2500]}\n```"
                )
                chunk_lookup[chunk.name] = snippet

        # Only generate flow docs for entry points that actually orchestrate
        # other known functions — lone leaf functions produce trivial "flows".
        defined = set(graph.defined_in.keys())
        meaningful_eps = [
            ep for ep in graph.entry_points
            if len(graph.calls.get(ep, set()) & defined) >= 1
        ]
        if not meaningful_eps:
            # If the call graph is sparse (e.g. all functions are standalone),
            # fall back to all entry points so we still produce some L3 docs.
            meaningful_eps = list(graph.entry_points)

        # Rank by how many internal functions the entry point directly calls —
        # the most "orchestrating" ones produce the most informative flow docs.
        meaningful_eps = sorted(
            meaningful_eps,
            key=lambda ep: len(graph.calls.get(ep, set()) & defined),
            reverse=True,
        )[:15]  # cap at 15 to keep indexing time reasonable

        for entry_point in meaningful_eps:
            chain = self._graph_builder.trace_flow(graph, entry_point)
            flow_name = self._graph_builder.flow_name_for(entry_point, graph)
            chain_text = " → ".join(chain)

            # Collect code snippets for every function in the chain.
            # Hard-cap total context at 7 000 chars so the LLM prompt stays clean.
            snippets: list[str] = []
            total_chars = 0
            for fn in chain:
                snippet = chunk_lookup.get(fn)
                if snippet is None:
                    file_loc = graph.defined_in.get(fn, "unknown file")
                    snippet = (
                        f"### `{fn}` — {file_loc}\n"
                        f"(source not available in this repo's chunks)"
                    )
                if total_chars + len(snippet) <= 7000:
                    snippets.append(snippet)
                    total_chars += len(snippet)
                else:
                    snippets.append(
                        f"### `{fn}` — (omitted: total context limit reached)"
                    )
                    break

            flow_doc = await self._doc_gen.generate_flow_doc(
                flow_name, chain_text, "\n\n".join(snippets)
            )

            # Persist flow membership so PR impact analysis can use it later.
            members = [(fn, graph.defined_in.get(fn, "")) for fn in chain]
            await self._docs.set_flow_membership(flow_name, members)

            await self._index_doc_items(
                [
                    self._make_doc_item(
                        file_path="<architecture>",
                        name=flow_name,
                        level=DocLevel.ARCHITECTURE,
                        doc_text=flow_doc,
                        flow_membership=[flow_name],
                    )
                ]
            )

        logger.info(
            "Phase L3 complete: overview + %d flow docs (from %d total entry points).",
            len(meaningful_eps), len(graph.entry_points),
        )
        return overview

    # --------------------------------------------------- phase 4: L2 ------
    async def _phase_l2(
        self, session_id: str, repo: _RepoData, overview: str
    ) -> None:
        """Phase 4: generate one Level-2 module doc per file (overview cascaded)."""
        await self._sessions.update_status(
            session_id, IndexingStatus.GENERATING_L2
        )
        graph = repo.call_graph

        for path, content in repo.file_contents.items():
            if not content.strip():
                continue
            related_flows = await self._docs.get_flows_for_file(path)
            related_flows_text = ", ".join(related_flows) or "none"

            # Call-graph summary for this file's functions.
            file_funcs = [
                fn for fn, fp in graph.defined_in.items() if fp == path
            ]
            cg_summary = "\n".join(
                f"- {fn} calls: {sorted(graph.calls.get(fn, set()))}"
                for fn in file_funcs
            ) or "No analysed functions."

            module_doc = await self._doc_gen.generate_module_doc(
                file_path=path,
                code=content,
                app_overview=overview,
                related_flows=related_flows_text,
                call_graph_summary=cg_summary,
            )
            await self._index_doc_items(
                [
                    self._make_doc_item(
                        file_path=path,
                        name=path,
                        level=DocLevel.MODULE,
                        doc_text=module_doc,
                        flow_membership=related_flows,
                    )
                ]
            )

        logger.info("Phase L2 complete: %d module docs.", len(repo.file_contents))

    # --------------------------------------------------- phase 5: L1 ------
    async def _phase_l1(
        self, session_id: str, repo: _RepoData, overview: str
    ) -> None:
        """Phase 5: generate Level-1 function docs (overview + module cascaded)."""
        await self._sessions.update_status(
            session_id, IndexingStatus.GENERATING_L1
        )
        graph = repo.call_graph
        from app.config.settings import get_settings

        critical_threshold = get_settings().critical_function_threshold

        for path, chunks in repo.chunks_by_file.items():
            # The module doc is cascaded into every function doc in this file.
            module_doc_row = await self._docs.get_module_doc(path)
            module_doc = module_doc_row.doc_text if module_doc_row else ""

            for chunk in chunks:
                # Dependency facts from static analysis (authoritative).
                calls = sorted(graph.calls.get(chunk.name, set()))
                called_by = sorted(graph.called_by.get(chunk.name, set()))
                dependency_info = (
                    f"Calls: {calls or 'none'}\n"
                    f"Called by: {called_by or 'none'}\n"
                    f"Defined in: {path}"
                )

                # "Critical" functions get extended context (the whole file).
                is_critical = (
                    graph.callee_count(chunk.name) >= critical_threshold
                )
                extended = repo.file_contents.get(path, "") if is_critical else ""

                func_doc = await self._doc_gen.generate_function_doc(
                    function_name=chunk.name,
                    code=chunk.code,
                    app_overview=overview,
                    module_doc=module_doc,
                    dependency_info=dependency_info,
                    is_critical=is_critical,
                    extended_context=extended,
                )

                await self._index_doc_items(
                    [
                        self._make_doc_item(
                            file_path=path,
                            name=chunk.name,
                            level=DocLevel.FUNCTION,
                            doc_text=func_doc,
                            code_text=chunk.code,
                            language=chunk.language,
                            start_line=chunk.start_line,
                            end_line=chunk.end_line,
                        )
                    ]
                )
                await self._sessions.increment_processed_functions(session_id)

        logger.info("Phase L1 complete.")

    # ===================================================================== #
    # PR-driven impact-based regeneration                                   #
    # ===================================================================== #
    async def regenerate_for_pr(
        self, pr_number: str, changed_files: list[str]
    ) -> dict:
        """Impact-based doc regeneration after a PR merge (spec PR flow).

        Rather than re-indexing everything, this:
          1. marks the changed files' docs (and the flows they belong to) for
             regeneration;
          2. returns the impact set.
        The actual regeneration of marked items is performed by the worker
        calling `reindex_changed_files`, keeping each step small and
        crash-recoverable.
        """
        affected_flows: set[str] = set()
        for path in changed_files:
            if self._chunker.should_skip(path):
                continue
            # Mark the module doc.
            await self._docs.mark_for_regeneration(path, DocLevel.MODULE.value)
            # Mark every flow this file participates in.
            for flow in await self._docs.get_flows_for_file(path):
                affected_flows.add(flow)
                await self._docs.mark_for_regeneration(
                    flow, DocLevel.ARCHITECTURE.value
                )

        impact = {
            "pr_number": pr_number,
            "changed_files": changed_files,
            "affected_flows": sorted(affected_flows),
        }
        logger.info(
            "PR #%s impact: %d files, %d flows marked for regeneration.",
            pr_number,
            len(changed_files),
            len(affected_flows),
        )
        return impact

    async def reindex_changed_files(self, changed_files: list[str]) -> None:
        """Re-chunk, re-analyse, and re-document a set of changed files.

        Each file's stale docs/vectors are deleted before regeneration so no
        orphans remain. Used by the PR-sync worker task after `regenerate_for_pr`
        has computed the impact set.
        """
        for path in changed_files:
            if self._chunker.should_skip(path):
                continue
            try:
                repo_file = await self._repo.get_file(path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping changed file %s: %s", path, exc)
                continue

            # Remove stale docs/vectors first.
            await self._indexer.reindex_file(path)
            # Re-document the file at L1 (and refresh the call graph entries).
            functions = self._analyzer.analyze_file(path, repo_file.content)
            graph = self._graph_builder.build(functions)
            await self._persist_call_graph(graph)

            file_chunks = self._chunker.chunk_file(path, repo_file.content)
            module_doc_row = await self._docs.get_module_doc(path)
            module_doc = module_doc_row.doc_text if module_doc_row else ""

            for chunk in file_chunks.chunks:
                dependency_info = (
                    f"Calls: {sorted(graph.calls.get(chunk.name, set()))}\n"
                    f"Defined in: {path}"
                )
                func_doc = await self._doc_gen.generate_function_doc(
                    function_name=chunk.name,
                    code=chunk.code,
                    app_overview="",
                    module_doc=module_doc,
                    dependency_info=dependency_info,
                )
                await self._index_doc_items(
                    [
                        self._make_doc_item(
                            file_path=path,
                            name=chunk.name,
                            level=DocLevel.FUNCTION,
                            doc_text=func_doc,
                            code_text=chunk.code,
                            language=chunk.language,
                            start_line=chunk.start_line,
                            end_line=chunk.end_line,
                        )
                    ]
                )
            # Clear the regeneration flag now the file is fresh.
            await self._docs.clear_regeneration_flag(path)

        logger.info("Re-indexed %d changed files.", len(changed_files))

    # ===================================================================== #
    # Status                                                                #
    # ===================================================================== #
    async def get_status(self, session_id: str) -> dict:
        """Return a status dict for `/index-status` and the indexing agent."""
        session = await self._sessions.get(session_id)
        if session is None:
            return {"session_id": session_id, "status": "not_found"}

        job_counts = await self._sessions.job_counts_by_status(session_id)
        # Progress as a 0-100 percentage across files + functions.
        total = (session.total_files or 0) + (session.total_functions or 0)
        done = (session.processed_files or 0) + (session.processed_functions or 0)
        progress = round((done / total) * 100, 1) if total else 0.0

        return {
            "session_id": session_id,
            "repo_url": session.repo_url,
            "status": session.status,
            "total_files": session.total_files,
            "processed_files": session.processed_files,
            "total_functions": session.total_functions,
            "processed_functions": session.processed_functions,
            "progress_percent": progress,
            "job_counts": job_counts,
            "error_message": session.error_message,
        }

    # ===================================================================== #
    # Helpers                                                               #
    # ===================================================================== #
    async def _persist_call_graph(self, graph: CallGraph) -> None:
        """Write every call-graph node to PostgreSQL (`call_graph` table)."""
        from app.db.models import CallGraphEntry

        node_count = len(graph.defined_in)
        if node_count == 0:
            logger.info(
                "Call graph persist: 0 nodes — nothing to write to PostgreSQL."
            )
            return

        logger.info(
            "Call graph persist: writing %d nodes, %d entry points → PostgreSQL.",
            node_count, len(graph.entry_points),
        )
        if graph.entry_points:
            logger.debug(
                "Entry points: %s", graph.entry_points[:20]
            )

        for fn, file_path in graph.defined_in.items():
            calls_list = sorted(graph.calls.get(fn, set()))
            called_by_list = sorted(graph.called_by.get(fn, set()))
            language = graph.languages.get(fn, "unknown")
            await self._docs.upsert_call_graph_entry(
                CallGraphEntry(
                    function_name=fn,
                    file_path=file_path,
                    calls=calls_list,
                    called_by=called_by_list,
                    language=language,
                )
            )
            logger.debug(
                "  call-graph node: %s (%s) calls=%s called_by=%s",
                fn, file_path, calls_list, called_by_list,
            )

        await self._docs.flush()
        logger.info(
            "Call graph persist: %d nodes successfully flushed to PostgreSQL.",
            node_count,
        )

    async def _index_doc_items(self, items: list[IndexItem]) -> None:
        """Embed and index a small batch of prepared doc items.

        Flow for each item:
          1. Split doc_text into paragraph chunks (L2/L3 only — L1 is already
             function-sized and does not need further splitting).
          2. Embed every chunk in one batched call so the embedding model is
             called as few times as possible.
          3. Persist: PostgreSQL gets ONE row per item with the full doc_text;
             Qdrant gets ONE point per chunk for fine-grained retrieval.

        Failures are logged with full detail and re-raised so the pipeline
        marks the session FAILED rather than silently writing zero vectors.
        """
        if not items:
            return

        # Filter out items with empty doc text — Vertex AI rejects empty strings.
        items = [it for it in items if it.doc_text and it.doc_text.strip()]
        if not items:
            logger.warning("_index_doc_items: all items had empty doc text, skipping.")
            return

        names = [it.name for it in items]

        # ── Step 1: split long L2/L3 docs into paragraph chunks ─────────────
        # L1 function docs are already short (one function's code + explanation);
        # skip splitting so they remain a single focused vector.
        from app.pipeline.indexer import Indexer as _Indexer
        for item in items:
            if item.level != DocLevel.FUNCTION and len(item.doc_text) > 600:
                item.doc_chunks = _Indexer.split_doc_chunks(item.doc_text)
            else:
                item.doc_chunks = [item.doc_text]

        # ── Step 2: embed all chunks in one batch ────────────────────────────
        # Flatten chunks from all items into a single list, embed at once, then
        # redistribute vectors back to each item. This minimises API round-trips.
        all_chunk_texts: list[str] = []
        for item in items:
            all_chunk_texts.extend(item.doc_chunks)  # type: ignore[arg-type]

        total_chunks = len(all_chunk_texts)
        logger.info(
            "Embedding %d items → %d doc chunks: %s",
            len(items), total_chunks, names,
        )

        try:
            all_chunk_vectors = await self._embedder.embed_docs(all_chunk_texts)
        except Exception as exc:
            logger.error(
                "Embedding FAILED for %d chunk texts (%s): %s",
                total_chunks, names, exc, exc_info=True,
            )
            raise

        # Redistribute vectors back to each item.
        idx = 0
        for item in items:
            n = len(item.doc_chunks)  # type: ignore[arg-type]
            item.doc_chunk_vectors = all_chunk_vectors[idx : idx + n]
            item.doc_vector = item.doc_chunk_vectors[0]  # primary vector
            idx += n

        # ── Step 3: embed code text (L1 only) ────────────────────────────────
        code_texts = [it.code_text for it in items if it.code_text]
        try:
            code_vectors = (
                await self._embedder.embed_code(code_texts) if code_texts else []
            )
        except Exception as exc:
            logger.warning(
                "Code embedding failed for %d items (%s): %s — "
                "continuing with doc vectors only.",
                len(items), names, exc,
            )
            code_vectors = []

        code_iter = iter(code_vectors)
        for item in items:
            if item.code_text:
                item.code_vector = next(code_iter, None)

        # ── Step 4: persist (PostgreSQL full text + Qdrant per-chunk points) ─
        try:
            await self._indexer.index_items(items)
            logger.info(
                "Indexed %d items (%d Qdrant points) into Qdrant + PostgreSQL",
                len(items), total_chunks,
            )
        except Exception as exc:
            logger.error(
                "Qdrant/PostgreSQL write FAILED for %d items (%s): %s",
                len(items), names, exc, exc_info=True,
            )
            raise

    @staticmethod
    def _make_doc_item(
        *,
        file_path: str,
        name: str,
        level: DocLevel,
        doc_text: str,
        code_text: str | None = None,
        language: str = "unknown",
        start_line: int = 0,
        end_line: int = 0,
        flow_membership: list[str] | None = None,
    ) -> IndexItem:
        """Build an `IndexItem` with an empty vector (filled in by embedding)."""
        return IndexItem(
            file_path=file_path,
            name=name,
            level=level,
            doc_text=doc_text,
            doc_vector=[],  # populated by _index_doc_items
            code_text=code_text,
            language=language,
            start_line=start_line,
            end_line=end_line,
            flow_membership=flow_membership,
        )
