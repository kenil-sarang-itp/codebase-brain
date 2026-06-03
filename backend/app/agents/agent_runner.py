"""
ADK agent runner.

A thin wrapper over ADK's `Runner` that executes an agent for one user message
and returns the final text. It hides ADK's event-stream mechanics behind a
simple `run(...)` coroutine so the service layer never deals with ADK
internals directly.

ADK's `Runner.run_async` yields a stream of `Event`s; the final response is the
last event with content. This wrapper consumes that stream and extracts the
text, while also surfacing the session state (where sub-agents write their
`output_key`s) for callers that need intermediate results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config.settings import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AgentRunResult:
    """The outcome of one agent run."""

    final_text: str
    state: dict = field(default_factory=dict)  # final session state snapshot


class AgentRunner:
    """Executes an ADK agent for a single turn and returns its final output."""

    def __init__(self, agent: object, app_name: str = "codebase-brain") -> None:
        """Wrap an ADK agent in a `Runner` with an in-memory session service.

        An in-memory session service is appropriate here because *our*
        persistent memory (conversation history, profiles) lives in PostgreSQL
        and is injected into the prompt explicitly. ADK sessions only need to
        survive a single multi-agent run.
        """
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        self._agent = agent
        self._app_name = app_name
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            agent=agent,
            app_name=app_name,
            session_service=self._session_service,
        )

    async def run(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        initial_state: dict | None = None,
    ) -> AgentRunResult:
        """Run the agent for one message and return its final text + state.

        Args:
            user_id: Stable id of the developer (for ADK session scoping).
            session_id: Conversation/session id.
            message: The user message to send to the agent.
            initial_state: Optional seed values for session state — used to
                pass pre-computed context (e.g. a validation report) to the
                agent.

        Returns:
            An `AgentRunResult` with the final text and the final state.
        """
        from google.genai import types

        # Ensure an ADK session exists for this run.
        await self._session_service.create_session(
            app_name=self._app_name,
            user_id=user_id,
            session_id=session_id,
            state=initial_state or {},
        )

        content = types.Content(
            role="user", parts=[types.Part(text=message)]
        )

        final_text = ""
        try:
            async for event in self._runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=content,
            ):
                # The final response event carries the answer text.
                if event.is_final_response() and event.content:
                    for part in event.content.parts:
                        if getattr(part, "text", None):
                            final_text = part.text
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent run failed: %s", exc)
            raise

        # Snapshot final session state so callers can read sub-agent outputs.
        session = await self._session_service.get_session(
            app_name=self._app_name,
            user_id=user_id,
            session_id=session_id,
        )
        state = dict(session.state) if session else {}

        return AgentRunResult(final_text=final_text, state=state)
