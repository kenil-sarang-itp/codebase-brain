"""
Chunker facade.

The single entry point the pipeline uses for chunking. It coordinates the
concrete strategies (Strategy pattern) using a Chain-of-Responsibility flow:

    1. Apply the skip list — binary/generated/lock files are never chunked.
    2. Try strategies in priority order: Tree-sitter → Markdown → fixed-size.
       The first strategy whose `can_handle` returns True *and* that yields at
       least one chunk wins.
    3. The fixed-size strategy is the universal terminal handler, so every
       non-skipped, non-empty file produces chunks.

Callers depend only on this facade, never on individual strategies — adding a
new language strategy is a one-line registration here.
"""

from __future__ import annotations

import os

from app.core.constants import (
    SKIP_DIRECTORIES,
    SKIP_FILE_EXTENSIONS,
    SKIP_FILE_NAMES,
)
from app.core.logging import get_logger
from app.pipeline.chunking.base import ChunkingStrategy, FileChunks
from app.pipeline.chunking.fallback_strategies import (
    FixedSizeStrategy,
    MarkdownStrategy,
)
from app.pipeline.chunking.language_detector import detect_language
from app.pipeline.chunking.tree_sitter_strategy import TreeSitterStrategy

logger = get_logger(__name__)


class Chunker:
    """Facade coordinating all chunking strategies."""

    def __init__(self, strategies: list[ChunkingStrategy] | None = None) -> None:
        """Build the chunker with an ordered strategy chain.

        Args:
            strategies: Optional explicit chain (mainly for testing). The
                default chain is Tree-sitter → Markdown → fixed-size, i.e.
                most-precise first, universal-fallback last.
        """
        self._strategies: list[ChunkingStrategy] = strategies or [
            TreeSitterStrategy(),
            MarkdownStrategy(),
            FixedSizeStrategy(),
        ]

    def should_skip(self, file_path: str) -> bool:
        """Return True if a file must not be chunked (skip list, spec §5)."""
        normalised = file_path.replace("\\", "/")
        parts = normalised.split("/")

        # Skip anything inside an ignored directory.
        if any(part in SKIP_DIRECTORIES for part in parts):
            return True

        name = parts[-1]
        if name in SKIP_FILE_NAMES:
            return True

        _, ext = os.path.splitext(name.lower())
        if ext in SKIP_FILE_EXTENSIONS:
            return True

        return False

    def chunk_file(self, file_path: str, content: str) -> FileChunks:
        """Chunk a single file, returning a `FileChunks` result.

        Skipped or empty files yield an empty result (`is_empty == True`).
        """
        language = detect_language(file_path)

        if self.should_skip(file_path):
            logger.debug("Skipping file (skip list): %s", file_path)
            return FileChunks(file_path=file_path, language=language)

        if not content or not content.strip():
            return FileChunks(file_path=file_path, language=language)

        # Chain of Responsibility: first capable, productive strategy wins.
        for strategy in self._strategies:
            if not strategy.can_handle(file_path, language):
                continue
            chunks = strategy.chunk(file_path, content, language)
            if chunks:
                logger.debug(
                    "Chunked %s into %d chunks via %s",
                    file_path,
                    len(chunks),
                    type(strategy).__name__,
                )
                return FileChunks(
                    file_path=file_path, language=language, chunks=chunks
                )

        # Should be unreachable — FixedSizeStrategy handles everything — but
        # return a safe empty result rather than risk a None leak.
        logger.warning("No strategy produced chunks for %s", file_path)
        return FileChunks(file_path=file_path, language=language)
