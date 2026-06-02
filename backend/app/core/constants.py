"""
Domain constants and enumerations.

Centralising these values prevents "magic string" bugs — e.g. one module
writing status "complete" while another checks for "completed". Every status,
level, and job type the system understands lives here.
"""

from __future__ import annotations

from enum import Enum


class IndexingStatus(str, Enum):
    """Lifecycle states of an indexing session.

    The order mirrors the pipeline phases in the spec (discovery → analysis →
    L3 → L2 → L1 → complete). `str` mixin makes the values JSON-serialisable
    and directly comparable to plain strings.
    """

    DISCOVERING = "discovering"
    ANALYSING = "analysing"
    GENERATING_L3 = "generating_l3"
    GENERATING_L2 = "generating_l2"
    GENERATING_L1 = "generating_l1"
    COMPLETE = "complete"
    FAILED = "failed"


class JobStatus(str, Enum):
    """Lifecycle states of a single indexing job (one file or one function)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class JobType(str, Enum):
    """The kind of work a single indexing job performs."""

    FILE_ANALYSIS = "file_analysis"
    L3_DOC = "l3_doc"
    L2_DOC = "l2_doc"
    L1_DOC = "l1_doc"


class DocLevel(int, Enum):
    """The three documentation levels. Generation order is L3 → L2 → L1."""

    FUNCTION = 1      # Level 1 — per function/class
    MODULE = 2        # Level 2 — per source file
    ARCHITECTURE = 3  # Level 3 — per data-flow / entry point


class QueryType(str, Enum):
    """How the orchestrator classifies an incoming developer request."""

    KNOWLEDGE = "knowledge"      # normal "how does X work?" question
    VALIDATION = "validation"    # "does the code implement these steps?"


class VectorName(str, Enum):
    """Named vectors stored per Qdrant point (see spec section 6)."""

    CODE = "code"  # embedding of raw source code
    DOC = "doc"    # embedding of generated documentation text


# --------------------------------------------------------------------------- #
# Chunking skip list (spec section 5).                                        #
# Files/dirs matching these are never chunked or documented.                  #
# --------------------------------------------------------------------------- #
SKIP_FILE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".lock", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
        ".pyc", ".class", ".jar", ".zip", ".tar", ".gz", ".woff",
        ".woff2", ".ttf", ".eot", ".pdf", ".mp4", ".mp3", ".bin",
    }
)

SKIP_FILE_NAMES: frozenset[str] = frozenset(
    {"package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock"}
)

SKIP_DIRECTORIES: frozenset[str] = frozenset(
    {
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
        "target", "vendor",
    }
)
