"""
Chunking domain models and the strategy interface.

A "chunk" is one unit of code (a function or class) with exact line numbers —
this is what makes citations precise (real function names + line ranges, never
arbitrary character offsets, per spec section 5).

`ChunkingStrategy` is the abstract base for the Strategy pattern: each concrete
strategy knows how to split *one family* of input. The `Chunker` facade then
picks the right strategy per file and falls back gracefully.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CodeChunk:
    """One chunk of source code with precise location metadata.

    Attributes:
        name: Logical name — a function/class name, or "<module>" for
            whole-file fallback chunks.
        kind: "function", "class", "section" (markdown) or "block" (fallback).
        file_path: Repo-relative path of the source file.
        language: Detected language identifier.
        start_line: 1-based first line of the chunk (inclusive).
        end_line: 1-based last line of the chunk (inclusive).
        code: The raw source text of the chunk.
    """

    name: str
    kind: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    code: str

    @property
    def qualified_name(self) -> str:
        """A globally-unique name: file path + symbol name.

        Used as the call-graph / doc key so two files may both define a
        function called `process` without collision.
        """
        return f"{self.file_path}::{self.name}"


@dataclass
class FileChunks:
    """The full chunking result for one source file."""

    file_path: str
    language: str
    chunks: list[CodeChunk] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when the file produced no chunks (skipped or unparseable)."""
        return not self.chunks


class ChunkingStrategy(ABC):
    """Abstract base for a language/format-specific chunking strategy.

    Each strategy answers two questions: *can I handle this file?* and *split
    it into chunks*. The `Chunker` facade tries strategies in priority order.
    """

    @abstractmethod
    def can_handle(self, file_path: str, language: str) -> bool:
        """Return True if this strategy should be used for the given file."""

    @abstractmethod
    def chunk(self, file_path: str, content: str, language: str) -> list[CodeChunk]:
        """Split file `content` into `CodeChunk`s. Must never raise.

        A strategy that hits an internal error should return an empty list so
        the facade can fall through to the next strategy.
        """
