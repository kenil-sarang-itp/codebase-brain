"""
Language detection from file paths.

A tiny, pure helper used by the chunker, static analyser, and indexer to map a
file path to a canonical language identifier. Kept separate so the mapping has
exactly one home.
"""

from __future__ import annotations

import os

# Canonical extension → language map. The language strings here are the same
# ones Tree-sitter grammars and the rest of the pipeline expect.
_EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".md": "markdown",
    ".markdown": "markdown",
}

# Languages for which we have first-class Tree-sitter support (spec Layer 1).
TREE_SITTER_LANGUAGES: frozenset[str] = frozenset(
    {
        "python", "javascript", "typescript", "java",
        "go", "rust", "c", "cpp",
    }
)


def detect_language(file_path: str) -> str:
    """Return the canonical language id for a file path.

    Returns "unknown" for unrecognised extensions — the chunker treats that as
    a signal to use its fixed-size fallback strategy.
    """
    _, ext = os.path.splitext(file_path.lower())
    return _EXTENSION_LANGUAGE.get(ext, "unknown")
