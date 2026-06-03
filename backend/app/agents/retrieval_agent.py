"""
Retrieval agent (Google ADK).

The retrieval agent's job (spec section 9) is to take a developer's question
and gather the most relevant documentation/code from the knowledge base. It is
implemented as an ADK `LlmAgent` equipped with the retrieval tools, so the
model itself decides whether a question needs function-level docs, an
architecture overview, or both — and issues the matching tool calls.

It is a *sub-agent*: the orchestrator delegates to it. Its output (the gathered
sources) is written to session state under `output_key` so the answer agent can
consume it.
"""

from __future__ import annotations

from app.config.settings import get_settings
from app.core.logging import get_logger
from app.services.retrieval_service import RetrievalService

logger = get_logger(__name__)

# Instruction given to the retrieval LLM. It is intentionally explicit about
# *when* to use each tool so retrieval quality is predictable.
_RETRIEVAL_INSTRUCTION = """\
You are the Retrieval Agent for a codebase knowledge base.

Your sole job is to gather the most relevant sources for the developer's
question. You do NOT answer the question yourself.

Guidelines:
- For questions about how a specific function or file works, call
  `search_knowledge_base`.
- For broad "how does the system do X" or architecture questions, also call
  `search_architecture` to pull data-flow documentation.
- You may call the tools more than once with refined queries if the first
  results look weak.
- When you have gathered good sources, respond with a brief note listing the
  citations you found. Do not invent content.
"""


def build_retrieval_agent(retrieval_service: RetrievalService):
    """Construct the retrieval `LlmAgent` and bind its tools.

    Args:
        retrieval_service: The RAG retrieval service the tools delegate to.

    Returns:
        A configured ADK `LlmAgent`.
    """
    # Imported lazily so importing this module does not hard-require ADK.
    from google.adk.agents import LlmAgent
    from google.adk.tools import FunctionTool

    from app.agents.tools import retrieval_tools

    # Give the tool functions access to the retrieval service.
    retrieval_tools.bind_retrieval_tools(retrieval_service)

    settings = get_settings()
    agent = LlmAgent(
        name="retrieval_agent",
        model=settings.llm_model,
        description=(
            "Gathers the most relevant documentation and code from the "
            "codebase knowledge base for a developer's question."
        ),
        instruction=_RETRIEVAL_INSTRUCTION,
        tools=[
            FunctionTool(retrieval_tools.search_knowledge_base),
            FunctionTool(retrieval_tools.search_architecture),
        ],
        # The gathered sources land in session state for the answer agent.
        output_key="retrieved_sources",
    )
    logger.info("Retrieval agent constructed.")
    return agent
