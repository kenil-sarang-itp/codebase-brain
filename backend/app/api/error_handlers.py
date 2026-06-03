"""
FastAPI exception handlers.

Maps the application's typed exception hierarchy (`CodeBaseBrainError` and its
subclasses) onto clean JSON HTTP responses. Registering these once means route
handlers can simply `raise NotFoundError(...)` and trust that the client
receives a correct status code and a consistent error body.

A catch-all handler converts any *unexpected* exception into a 500 without
leaking internals — the real error is logged server-side with the trace id.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import CodeBaseBrainError
from app.core.logging import get_logger, get_trace_id

logger = get_logger(__name__)


def _error_body(message: str, *, error_type: str, context: dict | None = None) -> dict:
    """Build the consistent JSON error envelope returned for every failure."""
    body: dict = {
        "error": error_type,
        "detail": message,
        "trace_id": get_trace_id(),
    }
    if context:
        body["context"] = context
    return body


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to the FastAPI application."""

    @app.exception_handler(CodeBaseBrainError)
    async def _handle_domain_error(  # noqa: ANN202 - FastAPI handler
        request: Request, exc: CodeBaseBrainError
    ):
        """Handle any of our typed domain errors with its declared status."""
        # 5xx errors are genuine server faults — log them at error level.
        if exc.http_status >= 500:
            logger.error(
                "Domain error on %s %s: %s",
                request.method,
                request.url.path,
                exc.detail,
            )
        else:
            logger.info(
                "Request rejected on %s %s: %s",
                request.method,
                request.url.path,
                exc.detail,
            )
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(
                exc.detail,
                error_type=type(exc).__name__,
                context=exc.context or None,
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(  # noqa: ANN202
        request: Request, exc: Exception
    ):
        """Catch-all for unanticipated errors — never leak internals."""
        logger.exception(
            "Unhandled error on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content=_error_body(
                "An unexpected internal error occurred.",
                error_type="InternalServerError",
            ),
        )
