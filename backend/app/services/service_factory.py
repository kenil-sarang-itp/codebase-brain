"""
Service composition root.

Centralises the wiring of services to their dependencies. This is the
"composition root" pattern: object graphs are assembled in exactly one place,
so the rest of the code only ever *receives* fully-built collaborators.

API routes call these builders (via FastAPI dependencies) with a request-scoped
DB session; worker tasks call them with a worker-scoped session. Either way the
wiring logic is identical and lives here, not scattered across call sites.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.qdrant_store import get_qdrant_store
from app.db.repositories.doc_repository import DocRepository
from app.db.repositories.indexing_repository import IndexingRepository
from app.db.repositories.memory_repository import MemoryRepository
from app.db.repositories.user_repository import UserRepository
from app.external.provider_factory import (
    get_embedding_provider,
    get_llm_provider,
    get_reranker_provider,
)
from app.external.repository_source import RepositorySource
from app.external.repository_source_factory import get_repository_source
from app.pipeline.chunking.chunker import Chunker
from app.pipeline.doc_generator import DocGenerator
from app.pipeline.embedder import Embedder
from app.pipeline.indexer import Indexer
from app.pipeline.static_analysis import StaticAnalyzer
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.indexing_service import IndexingService
from app.services.memory_service import MemoryService
from app.services.retrieval_service import RetrievalService


def build_auth_service(session: AsyncSession) -> AuthService:
    """Build the auth service for a DB session."""
    return AuthService(UserRepository(session))


def build_retrieval_service() -> RetrievalService:
    """Build the retrieval service (no DB session needed — uses Qdrant)."""
    embedder = Embedder(get_embedding_provider())
    return RetrievalService(
        embedder=embedder,
        qdrant=get_qdrant_store(),
        reranker=get_reranker_provider(),
    )


def build_chat_service(session: AsyncSession) -> ChatService:
    """Build the chat service and all of its collaborators for a DB session."""
    memory_repo = MemoryRepository(session)
    doc_repo = DocRepository(session)
    return ChatService(
        retrieval_service=build_retrieval_service(),
        memory_service=MemoryService(memory_repo),
        memory_repo=memory_repo,
        doc_repo=doc_repo,
    )


def build_indexing_service(
    session: AsyncSession,
    *,
    repo_source: RepositorySource | None = None,
    repo_identifier: str | None = None,
) -> IndexingService:
    """Build the indexing service and its full pipeline for a DB session.

    Args:
        session: The DB session (request- or worker-scoped).
        repo_source: An explicit repository source, or None to construct one.
        repo_identifier: Repo slug/path used when `repo_source` is None.
    """
    source = repo_source or get_repository_source(repo_identifier)
    llm = get_llm_provider()
    embedder = Embedder(get_embedding_provider())
    doc_repo = DocRepository(session)

    return IndexingService(
        repo_source=source,
        indexing_repo=IndexingRepository(session),
        doc_repo=doc_repo,
        qdrant=get_qdrant_store(),
        chunker=Chunker(),
        analyzer=StaticAnalyzer(),
        doc_generator=DocGenerator(llm),
        embedder=embedder,
        indexer=Indexer(get_qdrant_store(), doc_repo),
    )
