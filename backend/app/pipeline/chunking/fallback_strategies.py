"""
Fallback chunking strategies (spec Layers 2 & 3).

When Tree-sitter cannot handle a file, the `Chunker` facade falls back here:

    * `MarkdownStrategy` — splits Markdown at `##` heading boundaries so docs
      and READMEs become navigable, individually-citable sections.
    * `FixedSizeStrategy` — the universal last resort: splits any text into
      fixed-size token windows with overlap. This guarantees *every* file is
      chunkable, even an unknown language, so nothing is silently dropped.

Both never raise — they always return at least one chunk for non-empty input.
"""

from __future__ import annotations

from app.pipeline.chunking.base import ChunkingStrategy, CodeChunk

# Approximate chars-per-token used to size fixed windows without a tokenizer.
_CHARS_PER_TOKEN = 4


class MarkdownStrategy(ChunkingStrategy):
    """Splits Markdown files at level-2 (`##`) headings."""

    def can_handle(self, file_path: str, language: str) -> bool:
        return language == "markdown"

    def chunk(
        self, file_path: str, content: str, language: str
    ) -> list[CodeChunk]:
        """Emit one chunk per `##` section (text before the first heading is
        its own chunk too)."""
        lines = content.splitlines()
        chunks: list[CodeChunk] = []
        section_start = 0
        section_title = "preamble"

        def flush(end_idx: int) -> None:
            """Emit the accumulated section as a chunk if it has content."""
            body = "\n".join(lines[section_start:end_idx]).strip()
            if body:
                chunks.append(
                    CodeChunk(
                        name=section_title,
                        kind="section",
                        file_path=file_path,
                        language="markdown",
                        start_line=section_start + 1,
                        end_line=end_idx,
                        code=body,
                    )
                )

        for idx, line in enumerate(lines):
            if line.startswith("## "):
                flush(idx)  # close the previous section
                section_start = idx
                section_title = line[3:].strip() or f"section_{idx}"

        flush(len(lines))  # close the final section

        # An empty or heading-less file still yields one whole-file chunk.
        if not chunks and content.strip():
            chunks.append(
                CodeChunk(
                    name="document",
                    kind="section",
                    file_path=file_path,
                    language="markdown",
                    start_line=1,
                    end_line=max(1, len(lines)),
                    code=content.strip(),
                )
            )
        return chunks


class FixedSizeStrategy(ChunkingStrategy):
    """Universal fallback: fixed-size windows with overlap (spec Layer 3).

    Defaults to 500-token windows with 50-token overlap. Overlap ensures a
    construct straddling a window boundary still appears whole in one chunk.
    """

    def __init__(self, window_tokens: int = 500, overlap_tokens: int = 50) -> None:
        self._window = window_tokens * _CHARS_PER_TOKEN
        self._overlap = overlap_tokens * _CHARS_PER_TOKEN

    def can_handle(self, file_path: str, language: str) -> bool:
        """The last-resort strategy handles everything."""
        return True

    def chunk(
        self, file_path: str, content: str, language: str
    ) -> list[CodeChunk]:
        """Slice `content` into overlapping fixed-size windows."""
        if not content.strip():
            return []

        # Precompute the char offset at which each line starts, so a character
        # window can be reported with accurate 1-based line numbers.
        line_starts: list[int] = [0]
        for ch in content:
            if ch == "\n":
                line_starts.append(line_starts[-1] + 1)
            else:
                line_starts[-1] = line_starts[-1]  # no-op for clarity
        # Rebuild properly: cumulative offsets per line.
        line_starts = [0]
        offset = 0
        for line in content.splitlines(keepends=True):
            offset += len(line)
            line_starts.append(offset)

        def line_of(char_index: int) -> int:
            """Map a character index to its 1-based line number."""
            lo, hi = 0, len(line_starts) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if line_starts[mid] <= char_index:
                    lo = mid + 1
                else:
                    hi = mid
            return max(1, lo)

        chunks: list[CodeChunk] = []
        step = max(1, self._window - self._overlap)
        idx = 0
        part = 0
        while idx < len(content):
            window = content[idx : idx + self._window]
            if window.strip():
                chunks.append(
                    CodeChunk(
                        name=f"block_{part}",
                        kind="block",
                        file_path=file_path,
                        language=language,
                        start_line=line_of(idx),
                        end_line=line_of(min(idx + self._window, len(content)) - 1),
                        code=window,
                    )
                )
                part += 1
            idx += step
        return chunks
