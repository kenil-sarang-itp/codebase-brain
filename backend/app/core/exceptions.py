"""
Application-wide exception hierarchy.

A single, well-structured exception tree lets every layer raise *meaningful*
errors and lets the API layer translate them into correct HTTP responses in one
place (see `app.api.error_handlers`). This is far more robust than scattering
`raise Exception(...)` calls everywhere.

Design:
    * `CodeBaseBrainError` is the root — catching it catches everything ours.
    * Each subclass carries an `http_status` so the API layer needs no
      giant if/elif ladder to map errors to responses.
    * `detail` is always a safe, user-facing string; sensitive context goes
      into `context` (logged, never returned to clients).
"""

from __future__ import annotations

from typing import Any


class CodeBaseBrainError(Exception):
    """Root of every exception this application raises deliberately.

    Attributes:
        detail: Human-readable, client-safe message.
        http_status: Suggested HTTP status code for the API layer.
        context: Extra structured data for logging/debugging (never returned).
    """

    http_status: int = 500
    default_detail: str = "An internal error occurred."

    def __init__(
        self,
        detail: str | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.detail = detail or self.default_detail
        self.context = context or {}
        super().__init__(self.detail)


# --------------------------------------------------------------------------- #
# 4xx — client / request errors                                               #
# --------------------------------------------------------------------------- #
class ValidationError(CodeBaseBrainError):
    """Input failed a business-rule or schema validation check."""

    http_status = 422
    default_detail = "The request data is invalid."


class NotFoundError(CodeBaseBrainError):
    """A requested resource (session, repo, doc, user) does not exist."""

    http_status = 404
    default_detail = "The requested resource was not found."


class AuthenticationError(CodeBaseBrainError):
    """Credentials are missing, malformed, or incorrect."""

    http_status = 401
    default_detail = "Authentication failed."


class AuthorizationError(CodeBaseBrainError):
    """The caller is authenticated but not permitted to do this."""

    http_status = 403
    default_detail = "You are not authorized to perform this action."


class ConflictError(CodeBaseBrainError):
    """The request conflicts with current state (e.g. duplicate user)."""

    http_status = 409
    default_detail = "The request conflicts with the current state."


class WebhookVerificationError(AuthenticationError):
    """A GitHub webhook payload failed HMAC signature verification."""

    default_detail = "Webhook signature verification failed."


# --------------------------------------------------------------------------- #
# 5xx — server / dependency errors                                            #
# --------------------------------------------------------------------------- #
class ExternalServiceError(CodeBaseBrainError):
    """A downstream dependency (LLM, embeddings, GitHub, Qdrant) failed.

    Carrying `service` lets us log *which* dependency broke without parsing
    the message string.
    """

    http_status = 502
    default_detail = "An external service is currently unavailable."

    def __init__(
        self,
        service: str,
        detail: str | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.service = service
        merged = {"service": service, **(context or {})}
        super().__init__(detail, context=merged)


class RateLimitError(ExternalServiceError):
    """A downstream provider rejected the call due to rate limiting.

    Raised so RQ's retry machinery can re-queue the specific failed job.
    """

    http_status = 429
    default_detail = "Rate limit exceeded for an external provider."


class PipelineError(CodeBaseBrainError):
    """A documentation-generation pipeline stage failed irrecoverably."""

    http_status = 500
    default_detail = "The documentation pipeline encountered an error."


class ConfigurationError(CodeBaseBrainError):
    """The application is misconfigured (missing required setting)."""

    http_status = 500
    default_detail = "The application is misconfigured."
