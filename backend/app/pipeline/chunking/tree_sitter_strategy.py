"""
Tree-sitter chunking strategy (spec Layer 1).

Splits source code at *syntactic* boundaries — one chunk per function or class,
each with exact start/end line numbers. This precision is the whole point:
citations become real function names and line ranges.

Tree-sitter ships grammars for 40+ languages. We use the `tree-sitter-languages`
bundle which packages pre-built grammars, so no per-language compilation step
is needed.

If Tree-sitter or a specific grammar is unavailable at runtime, `chunk()`
returns an empty list and the `Chunker` facade falls through to the next
strategy — the system never crashes over a missing grammar.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.chunking.base import ChunkingStrategy, CodeChunk
from app.pipeline.chunking.language_detector import TREE_SITTER_LANGUAGES

logger = get_logger(__name__)

# Tree-sitter node types that represent a "definition" worth its own chunk,
# per language. Different grammars name these nodes differently.
_DEFINITION_NODE_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition"},
    "javascript": {
        "function_declaration", "class_declaration",
        "method_definition", "arrow_function",
    },
    "typescript": {
        "function_declaration", "class_declaration",
        "method_definition", "interface_declaration",
    },
    "java": {"method_declaration", "class_declaration", "interface_declaration"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "rust": {"function_item", "struct_item", "impl_item", "trait_item"},
    "c": {"function_definition", "struct_specifier"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier"},
}


class TreeSitterStrategy(ChunkingStrategy):
    """Syntax-aware chunker for languages with a Tree-sitter grammar."""

    def can_handle(self, file_path: str, language: str) -> bool:
        """Handle any language we have a Tree-sitter grammar and node-map for."""
        return language in TREE_SITTER_LANGUAGES and language in _DEFINITION_NODE_TYPES

    def chunk(
        self, file_path: str, content: str, language: str
    ) -> list[CodeChunk]:
        """Parse `content` and emit one chunk per top-level definition.

        Never raises — on any failure it logs and returns [] so the facade can
        fall back to a coarser strategy.
        """
        try:
            parser = self._get_parser(language)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Tree-sitter unavailable for %s: %s", language, exc)
            return []

        try:
            source_bytes = content.encode("utf-8", errors="ignore")
            tree = parser.parse(source_bytes)
            lines = content.splitlines()
            wanted = _DEFINITION_NODE_TYPES[language]

            chunks: list[CodeChunk] = []
            # Walk only the top level + one nesting level: top-level functions
            # and classes, plus methods inside classes. Deeper nesting (a
            # closure inside a function) stays part of its parent chunk.
            self._collect(tree.root_node, wanted, lines, file_path, language, chunks)

            if not chunks:
                logger.debug("Tree-sitter found no definitions in %s", file_path)
            return chunks
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tree-sitter parse failed for %s: %s", file_path, exc)
            return []

    # ------------------------------------------------------------ helpers --
    def _get_parser(self, language: str):
        """Return a Tree-sitter parser for the language (lazy import)."""
        from tree_sitter_languages import get_parser

        return get_parser(language)

    def _collect(
        self,
        node,
        wanted: set[str],
        lines: list[str],
        file_path: str,
        language: str,
        out: list[CodeChunk],
        depth: int = 0,
    ) -> None:
        """Recursively gather definition nodes into chunks.

        We descend at most two levels (module → class → method) so that, for
        example, methods get their own chunks but nested closures do not.
        """
        for child in node.children:
            if child.type in wanted:
                chunk = self._node_to_chunk(child, lines, file_path, language)
                if chunk is not None:
                    out.append(chunk)
                # Descend into classes so their methods become chunks too.
                if depth < 1:
                    self._collect(
                        child, wanted, lines, file_path, language, out, depth + 1
                    )
            elif depth == 0:
                # At the top level keep scanning siblings (e.g. functions after
                # an import block) without treating non-definition nodes as
                # chunks.
                self._collect(
                    child, wanted, lines, file_path, language, out, depth
                )

    def _node_to_chunk(
        self, node, lines: list[str], file_path: str, language: str
    ) -> CodeChunk | None:
        """Convert a Tree-sitter definition node into a `CodeChunk`."""
        start_line = node.start_point[0] + 1  # Tree-sitter rows are 0-based
        end_line = node.end_point[0] + 1
        if start_line > len(lines):
            return None

        code = "\n".join(lines[start_line - 1 : end_line])
        name = self._extract_name(node) or f"anonymous_{start_line}"
        kind = "class" if "class" in node.type or "struct" in node.type else "function"

        return CodeChunk(
            name=name,
            kind=kind,
            file_path=file_path,
            language=language,
            start_line=start_line,
            end_line=end_line,
            code=code,
        )

    @staticmethod
    def _extract_name(node) -> str | None:
        """Pull the identifier name out of a definition node, if present."""
        # Most grammars expose the name via a field called "name".
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return name_node.text.decode("utf-8", errors="ignore")
        # Fallback: first identifier-like child.
        for child in node.children:
            if "identifier" in child.type:
                return child.text.decode("utf-8", errors="ignore")
        return None
