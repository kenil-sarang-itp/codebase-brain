"""
SQLAlchemy ORM models — the complete persistence schema.

These models implement every table from spec section 7 (call_graph,
generated_docs, doc_status, flow_membership, indexing_sessions, indexing_jobs,
structural_changes, conversation_history, developer_profiles) plus a `users`
table required for the FastAPI authentication layer.

The ORM is the single source of truth for the schema; `scripts/init_db.py`
creates these tables directly, so `schema.sql` is generated from here rather
than hand-maintained separately.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _utcnow() -> datetime:
    """Timezone-aware UTC now — used as a column default factory."""
    return datetime.now(timezone.utc)


def _uuid_str() -> str:
    """Generate a string UUID primary key."""
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Authentication                                                              #
# --------------------------------------------------------------------------- #
class User(Base):
    """An application user. Required for login/logout and per-developer memory.

    `developer_id` links a user to their `DeveloperProfile`, so the long-term
    memory system personalises answers to the logged-in developer.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# Static analysis                                                             #
# --------------------------------------------------------------------------- #
class CallGraphEntry(Base):
    """One row per function: who it calls and who calls it (spec: call_graph)."""

    __tablename__ = "call_graph"

    function_name: Mapped[str] = mapped_column(String(512), primary_key=True)
    file_path: Mapped[str] = mapped_column(String(1024), index=True, nullable=False)
    # JSONB-equivalent lists of function names.
    calls: Mapped[list] = mapped_column(JSON, default=list)
    called_by: Mapped[list] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(String(32), default="unknown")
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


# --------------------------------------------------------------------------- #
# Generated documentation                                                     #
# --------------------------------------------------------------------------- #
class GeneratedDoc(Base):
    """The authoritative copy of every generated doc (spec: generated_docs).

    Qdrant stores the *vectors*; this table stores the canonical *text* so the
    answer agent can always recover full context and so docs survive a Qdrant
    rebuild.
    """

    __tablename__ = "generated_docs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    file_path: Mapped[str] = mapped_column(String(1024), index=True, nullable=False)
    function_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    level: Mapped[int] = mapped_column(Integer, index=True, nullable=False)  # 1/2/3
    doc_text: Mapped[str] = mapped_column(Text, nullable=False)
    code_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # L1 only
    qdrant_point_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    needs_regeneration: Mapped[bool] = mapped_column(Boolean, default=False)


class DocStatus(Base):
    """Impact-based regeneration tracker (spec: doc_status).

    Workers query `needs_regeneration = TRUE` to find work, which is also what
    makes crash recovery automatic — a restarted worker simply re-reads this
    table and resumes.
    """

    __tablename__ = "doc_status"

    item_id: Mapped[str] = mapped_column(String(1024), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    last_generated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_code_changed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    needs_regeneration: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class FlowMembership(Base):
    """Maps functions/files to the Level-3 data-flows they belong to."""

    __tablename__ = "flow_membership"

    flow_name: Mapped[str] = mapped_column(String(256), primary_key=True)
    function_name: Mapped[str] = mapped_column(String(512), primary_key=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)


# --------------------------------------------------------------------------- #
# Indexing job tracking                                                       #
# --------------------------------------------------------------------------- #
class IndexingSession(Base):
    """One end-to-end indexing run of a repository (spec: indexing_sessions)."""

    __tablename__ = "indexing_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    repo_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="discovering", index=True)
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    processed_files: Mapped[int] = mapped_column(Integer, default=0)
    total_functions: Mapped[int] = mapped_column(Integer, default=0)
    processed_functions: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    jobs: Mapped[list["IndexingJob"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class IndexingJob(Base):
    """A single unit of indexing work (spec: indexing_jobs)."""

    __tablename__ = "indexing_jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("indexing_sessions.session_id"), index=True
    )
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    session: Mapped["IndexingSession"] = relationship(back_populates="jobs")


class StructuralChange(Base):
    """Records call-graph structural changes per PR (spec: structural_changes).

    Used to decide whether a Level-3 flow doc actually needs regeneration.
    """

    __tablename__ = "structural_changes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    pr_number: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    added_calls: Mapped[list] = mapped_column(JSON, default=list)
    removed_calls: Mapped[list] = mapped_column(JSON, default=list)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# --------------------------------------------------------------------------- #
# Memory: short-term history + long-term profiles                             #
# --------------------------------------------------------------------------- #
class ConversationHistory(Base):
    """Short-term memory: one row per chat message (spec: conversation_history)."""

    __tablename__ = "conversation_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    developer_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user / assistant
    message: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)


class DeveloperProfile(Base):
    """Long-term memory: a personalisation profile per developer."""

    __tablename__ = "developer_profiles"

    developer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    common_modules: Mapped[list] = mapped_column(JSON, default=list)
    question_style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferred_depth: Mapped[str | None] = mapped_column(String(32), nullable=True)
    interaction_count: Mapped[int] = mapped_column(Integer, default=0)
    profile_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class QueryLog(Base):
    """Audit log of every developer query — powers the /query-logs view."""

    __tablename__ = "query_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    developer_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    query_type: Mapped[str] = mapped_column(String(16), nullable=False)
    answer_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    num_sources: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    __table_args__ = (
        UniqueConstraint("id", name="uq_query_logs_id"),
    )
