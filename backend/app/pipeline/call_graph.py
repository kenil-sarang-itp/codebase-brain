"""
Call-graph assembly and data-flow tracing.

`StaticAnalyzer` produces per-function call sets. This module assembles those
into a *graph* and provides the two operations the pipeline needs:

    * `build()` — merge all `FunctionInfo` into `calls` / `called_by` /
      `defined_in` maps and identify entry points (functions nothing calls).
    * `trace_flow()` — from an entry point, recursively follow `calls` to
      produce the ordered call chain that becomes a Level-3 data-flow doc.

Pure graph operations — no LLM, no I/O — exactly as the spec prescribes for
flow tracing. Cycles are handled with a visited-set so recursion always
terminates.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.pipeline.static_analysis import FunctionInfo


@dataclass
class CallGraph:
    """An assembled call graph over a whole codebase.

    Attributes:
        calls: function name -> set of names it calls.
        called_by: function name -> set of names that call it.
        defined_in: function name -> file path where it is defined.
        languages: function name -> language of its file.
    """

    calls: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    called_by: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    defined_in: dict[str, str] = field(default_factory=dict)
    languages: dict[str, str] = field(default_factory=dict)

    @property
    def entry_points(self) -> list[str]:
        """Functions that nothing else calls — the starts of data flows.

        These are API handlers, webhook receivers, scheduled jobs, CLI mains:
        the natural roots from which to trace complete flows.
        """
        return sorted(
            fn for fn in self.defined_in if not self.called_by.get(fn)
        )

    def callee_count(self, function_name: str) -> int:
        """How many distinct functions call `function_name`.

        Used to flag "critical" functions that warrant richer LLM context.
        """
        return len(self.called_by.get(function_name, set()))


class CallGraphBuilder:
    """Assembles a `CallGraph` and traces flows through it."""

    def build(self, all_functions: list[FunctionInfo]) -> CallGraph:
        """Merge per-function analysis results into a complete call graph.

        Only *internal* calls (callees that are themselves defined functions)
        populate `called_by`; calls to library/builtin functions are kept in
        `calls` but do not create phantom graph nodes.
        """
        graph = CallGraph()

        # Pass 1: register every defined function.
        for info in all_functions:
            graph.defined_in[info.name] = info.file_path
            graph.languages[info.name] = info.language

        defined = set(graph.defined_in.keys())

        # Pass 2: wire up the edges.
        for info in all_functions:
            graph.calls[info.name] |= info.calls
            for callee in info.calls:
                if callee in defined:  # only internal edges get reverse links
                    graph.called_by[callee].add(info.name)

        return graph

    def trace_flow(
        self, graph: CallGraph, entry_point: str, max_depth: int = 25
    ) -> list[str]:
        """Trace the full call chain reachable from `entry_point`.

        Returns function names in depth-first discovery order — the order a
        reader would follow the logic. A visited-set prevents infinite loops
        on recursive or cyclic code, and `max_depth` bounds pathological cases.

        This is pure graph traversal — no LLM is involved, per the spec.
        """
        ordered: list[str] = []
        visited: set[str] = set()

        def dfs(node: str, depth: int) -> None:
            if depth > max_depth or node in visited:
                return
            visited.add(node)
            ordered.append(node)
            # Visit internal callees in a stable, deterministic order.
            for callee in sorted(graph.calls.get(node, set())):
                if callee in graph.defined_in:
                    dfs(callee, depth + 1)

        dfs(entry_point, 0)
        return ordered

    def flow_name_for(self, entry_point: str, graph: CallGraph) -> str:
        """Derive a human-readable flow name from its entry point.

        e.g. entry point `checkout_endpoint` → flow `checkout_endpoint_flow`.
        """
        return f"{entry_point}_flow"
