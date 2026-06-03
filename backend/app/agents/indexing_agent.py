"""
Indexing agent (Google ADK).

The indexing agent (spec section 9) supervises and reports on repository
indexing. The actual six-phase pipeline (discovery → static analysis → L3 → L2
→ L1 → complete) is executed by RQ workers calling the `IndexingService`; this
agent is the conversational surface over that process.

It is an ADK `LlmAgent` with the indexing tools bound, so a developer can ask
"how is the indexing going?" and the agent will check real status and explain
it. Triggering a *new* indexing run is done through the REST `/index` endpoint
(which enqueues an RQ job); the agent reports rather than launches, keeping the
heavy pipeline off the request path as the spec requires.
"""

from __future__ import annotations

from app.config.settings import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_INDEXING_INSTRUCTION = """\
You are the Indexing Agent for a codebase knowledge base.

You help developers understand the status of repository indexing runs.

Guidelines:
- When asked about progress, call `get_indexing_status` with the session id.
- When asked to summarise a finished run, call `summarise_indexing_result`.
- Explain the six indexing phases when useful: discovery, static analysis,
  Level-3 (architecture) docs, Level-2 (module) docs, Level-1 (function) docs,
  and completion.
- Be clear and concise. Report real numbers from the tools; never estimate.
"""


def build_indexing_agent(indexing_service: object):
    """Construct the indexing `LlmAgent` and bind its tools.

    Args:
        indexing_service: The `IndexingService` the tools delegate to.

    Returns:
        A configured ADK `LlmAgent`.
    """
    from google.adk.agents import LlmAgent
    from google.adk.tools import FunctionTool

    from app.agents.tools import indexing_tools

    indexing_tools.bind_indexing_tools(indexing_service)

    settings = get_settings()
    agent = LlmAgent(
        name="indexing_agent",
        model=settings.llm_model,
        description=(
            "Reports on and explains the status of repository indexing runs."
        ),
        instruction=_INDEXING_INSTRUCTION,
        tools=[
            FunctionTool(indexing_tools.get_indexing_status),
            FunctionTool(indexing_tools.summarise_indexing_result),
        ],
        output_key="final_answer",
    )
    logger.info("Indexing agent constructed.")
    return agent
