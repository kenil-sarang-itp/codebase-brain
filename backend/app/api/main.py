"""
FastAPI application entry point.

Builds and configures the ASGI application:
    * structured logging + Phoenix tracing on startup;
    * a per-request trace-id middleware for log correlation;
    * CORS for the React frontend;
    * all route routers (auth, chat, indexing, webhook, logs, health);
    * the typed-exception handlers;
    * graceful resource disposal on shutdown.

Run in development with:  uvicorn app.api.main:app --reload
In Docker the same `app` object is served by uvicorn (see the backend Dockerfile).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_exception_handlers
from app.api.routes import auth, chat, indexing, query_logs, webhook
from app.config.settings import get_settings
from app.core.logging import configure_logging, get_logger, set_trace_id
from app.observability.tracing import configure_tracing

logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks.

    On startup: configure logging/tracing and ensure the Qdrant collection
    exists. On shutdown: dispose database engine connections cleanly.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing(service_name="codebase-brain-api")
    logger.info("CodeBase Brain API starting up.")

    # Ensure the Qdrant collection exists so the first query never races setup.
    try:
        from app.db.qdrant_store import get_qdrant_store

        await get_qdrant_store().ensure_collection()
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: the API can still serve auth/health while Qdrant recovers.
        logger.warning("Could not ensure Qdrant collection at startup: %s", exc)

    yield  # ---- application runs ----

    # Shutdown: release database connections.
    from app.db.session import dispose_engines

    await dispose_engines()
    logger.info("CodeBase Brain API shut down cleanly.")


def create_app() -> FastAPI:
    """Application factory — builds and returns the configured FastAPI app."""
    settings = get_settings()

    app = FastAPI(
        title="CodeBase Brain API",
        description=(
            "Agentic AI documentation system: auto-generates and maintains "
            "three-level documentation over a codebase and answers developer "
            "questions with cited sources."
        ),
        version="1.0.0",
        lifespan=_lifespan,
    )

    # --- CORS: allow the React frontend's origin(s). ----------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Trace-id middleware: correlate every log line of a request. ------
    @app.middleware("http")
    async def _trace_id_middleware(request: Request, call_next):
        """Assign each request a trace id (from header or generated)."""
        trace_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
        set_trace_id(trace_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = trace_id
        return response

    # --- Typed-exception handlers. ----------------------------------------
    register_exception_handlers(app)

    # --- Routers. ----------------------------------------------------------
    # All API routes are served under a /api prefix so the frontend has one
    # clean namespace and Nginx can route /api/* to the backend.
    app.include_router(auth.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(indexing.router, prefix="/api")
    app.include_router(webhook.router, prefix="/api")
    app.include_router(query_logs.router, prefix="/api")

    @app.get("/", tags=["root"])
    async def root() -> dict:
        """Tiny root endpoint so hitting the bare host returns something useful."""
        return {
            "service": "CodeBase Brain API",
            "version": "1.0.0",
            "docs": "/docs",
        }

    logger.info("FastAPI application created.")
    return app


# The ASGI application object uvicorn serves.
app = create_app()
