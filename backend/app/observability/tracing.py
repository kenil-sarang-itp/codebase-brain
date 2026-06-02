"""
Distributed tracing with Arize Phoenix.

Phoenix runs as its own Docker container (see docker-compose.yml). It exposes:
    * a web UI on :6006 for inspecting traces, spans, latencies, and token use;
    * an OTLP collector that this module ships spans to.

`configure_tracing()` is called once per process at startup. It wires an
OpenTelemetry tracer provider to the Phoenix OTLP endpoint, then auto-instruments
the LLM client libraries so every model call becomes a span automatically.

Phoenix Sessions:
    Phoenix groups traces into "sessions" using the `session.id` span attribute
    (OpenInference semantic convention). We also set `user.id` so Phoenix can
    show per-user activity. Both are stamped onto every root span via
    `start_chat_trace()`, and the chat service calls this at the start of each
    /chat request.

Design notes:
    * Tracing is fully optional — `traced_span` degrades to a no-op if Phoenix
      is unreachable or disabled. An observability outage never takes down the app.
    * `traced_span` is the single helper the rest of the code uses for manual
      spans, so business code has no direct OpenTelemetry imports.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

from app.config.settings import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Module-level tracer handle. Stays None when tracing is disabled/unavailable.
_tracer: Any | None = None
_tracing_active: bool = False

# Context vars so session/user id flow through async call chains automatically.
_current_session_id: ContextVar[str] = ContextVar("session_id", default="")
_current_user_id: ContextVar[str] = ContextVar("user_id", default="")


def configure_tracing(service_name: str = "codebase-brain") -> None:
    """Initialise Phoenix tracing for the current process. Idempotent."""
    global _tracer, _tracing_active
    if _tracing_active:
        return

    settings = get_settings()
    if not settings.tracing_enabled:
        logger.info("Tracing disabled via configuration.")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        endpoint = f"{settings.phoenix_collector_endpoint.rstrip('/')}/v1/traces"

        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)

        _instrument_llm_libraries()

        _tracing_active = True
        logger.info("Phoenix tracing active; exporting spans to %s", endpoint)

    except Exception as exc:  # pragma: no cover
        logger.warning("Tracing setup failed (continuing without it): %s", exc)
        _tracer = None
        _tracing_active = False


def _instrument_llm_libraries() -> None:
    """Best-effort auto-instrumentation of LLM SDKs via OpenInference."""
    instrumentors = (
        ("openinference.instrumentation.google_genai", "GoogleGenAIInstrumentor"),
        ("openinference.instrumentation.vertexai", "VertexAIInstrumentor"),
    )
    for module_path, class_name in instrumentors:
        try:
            module = __import__(module_path, fromlist=[class_name])
            getattr(module, class_name)().instrument()
            logger.info("Instrumented %s", class_name)
        except Exception:  # noqa: BLE001
            logger.debug("Skipping instrumentor %s (not installed)", class_name)


def set_trace_context(session_id: str, user_id: str = "") -> None:
    """Set the session and user id for the current async context.

    Call this at the start of each /chat request. These values are then
    automatically stamped onto every span created within that request via
    `traced_span`, which is how Phoenix populates the Sessions tab.

    Args:
        session_id: The chat session id — becomes `session.id` on every span.
        user_id: The developer's user id — becomes `user.id` on every span.
    """
    _current_session_id.set(session_id)
    _current_user_id.set(user_id)


@contextlib.contextmanager
def traced_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Context manager creating one tracing span around a block of code.

    Automatically stamps `session.id` and `user.id` (OpenInference semantic
    conventions) onto every span so Phoenix can group traces into sessions and
    show per-user activity.

        with traced_span("retrieval.search", {"top_k": 15}):
            ...

    When tracing is inactive this is a zero-overhead no-op.
    """
    if _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(name) as span:
        # Stamp Phoenix session/user attributes on every span.
        session_id = _current_session_id.get()
        user_id = _current_user_id.get()
        with contextlib.suppress(Exception):
            if session_id:
                # OpenInference semantic convention — Phoenix reads this for
                # the Sessions tab.
                span.set_attribute("session.id", session_id)
            if user_id:
                span.set_attribute("user.id", user_id)

        if attributes:
            for key, value in attributes.items():
                with contextlib.suppress(Exception):
                    span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            with contextlib.suppress(Exception):
                span.record_exception(exc)
                span.set_attribute("error", True)
                span.set_attribute("error.message", str(exc))
            raise


@contextlib.contextmanager
def root_span(
    name: str,
    *,
    session_id: str,
    user_id: str = "",
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Create a root-level span that anchors a complete request in Phoenix.

    Use this for the top-level span of a chat turn or indexing run. The
    session_id is set both in the context var (for child spans) and directly
    on this span, making it the "root" Phoenix groups under a session.

    Args:
        name: Span name shown in Phoenix (e.g. "chat.turn", "indexing.run").
        session_id: Chat session id — used as `session.id` in Phoenix.
        user_id: Developer user id — used as `user.id` in Phoenix.
        attributes: Additional span attributes.
    """
    set_trace_context(session_id=session_id, user_id=user_id)
    attrs = dict(attributes or {})
    attrs["session.id"] = session_id
    if user_id:
        attrs["user.id"] = user_id
    # OpenInference input — Phoenix shows this as the span's "input" field.
    with traced_span(name, attrs) as span:
        yield span


def set_span_attribute(span: Any, key: str, value: Any) -> None:
    """Safely set an attribute on a possibly-None span."""
    if span is None:
        return
    with contextlib.suppress(Exception):
        span.set_attribute(key, value)
