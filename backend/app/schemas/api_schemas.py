"""
API schemas — Pydantic request and response models.

These models define the HTTP contract: what each endpoint accepts and returns.
FastAPI uses them for automatic request validation, response serialisation, and
OpenAPI documentation.

Keeping schemas separate from ORM models is deliberate (the API contract and
the database schema evolve independently, and the API must never leak internal
fields such as password hashes).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# --------------------------------------------------------------------------- #
# Auth                                                                        #
# --------------------------------------------------------------------------- #
class RegisterRequest(BaseModel):
    """Payload for `POST /auth/register`."""

    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    """Payload for `POST /auth/login`."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """A successful auth response carrying the JWT access token."""

    access_token: str
    token_type: str = "bearer"
    username: str
    user_id: str


class UserResponse(BaseModel):
    """Public view of a user — never includes the password hash."""

    id: str
    username: str
    email: str
    is_active: bool
    created_at: datetime


class MessageResponse(BaseModel):
    """A generic message response (e.g. for logout)."""

    message: str


# --------------------------------------------------------------------------- #
# Chat                                                                        #
# --------------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    """Payload for `POST /chat`."""

    question: str = Field(min_length=1, max_length=4000)
    # Optional client-supplied session id; the server generates one if absent.
    session_id: str | None = None


class ChatResponse(BaseModel):
    """The answer to a chat question, with citations and metadata."""

    answer: str
    query_type: str
    citations: list[str] = Field(default_factory=list)
    session_id: str
    latency_ms: int


# --------------------------------------------------------------------------- #
# Indexing                                                                    #
# --------------------------------------------------------------------------- #
class IndexRequest(BaseModel):
    """Payload for `POST /index` — start indexing a repository."""

    # A GitHub "owner/name" slug (uses the MCP source) or a local path.
    repo: str = Field(min_length=1, max_length=1024)
    # Git ref to index when using the GitHub MCP source.
    ref: str = "main"


class IndexResponse(BaseModel):
    """Response to a queued indexing request."""

    session_id: str
    status: str
    message: str


class IndexStatusResponse(BaseModel):
    """Live status of an indexing session (`GET /index-status/{id}`)."""

    session_id: str
    repo_url: str | None = None
    status: str
    total_files: int = 0
    processed_files: int = 0
    total_functions: int = 0
    processed_functions: int = 0
    progress_percent: float = 0.0
    job_counts: dict[str, int] = Field(default_factory=dict)
    error_message: str | None = None


# --------------------------------------------------------------------------- #
# Query logs                                                                  #
# --------------------------------------------------------------------------- #
class QueryLogEntry(BaseModel):
    """One row of the query audit log."""

    id: str
    question: str
    query_type: str
    answer_preview: str | None = None
    num_sources: int
    latency_ms: int
    created_at: datetime


class QueryLogResponse(BaseModel):
    """A page of query-log entries."""

    entries: list[QueryLogEntry]
    count: int


# --------------------------------------------------------------------------- #
# Webhook                                                                     #
# --------------------------------------------------------------------------- #
class WebhookResponse(BaseModel):
    """Acknowledgement returned to GitHub after a webhook is processed."""

    received: bool
    action: str
    detail: str = ""


# --------------------------------------------------------------------------- #
# Health                                                                      #
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    """Service health report."""

    status: str
    database: bool
    qdrant: bool
    llm_provider: str
    reranker: str
