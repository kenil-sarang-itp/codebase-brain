"""
Answer agent (Google ADK).

The answer agent (spec section 9) takes the sources gathered by the retrieval
agent and synthesises a grounded, **cited** answer to the developer's question.

It is an ADK `LlmAgent` with no tools — its single responsibility is high
-quality answer synthesis. It reads the retrieved sources from session state
(the `{retrieved_sources}` placeholder, populated by the retrieval agent's
`output_key`) so the orchestrator can run retrieval → answer as a pipeline.

The instruction enforces the spec's hard rule: every claim must be cited, and
if the sources are insufficient the agent must say so rather than guess.
"""

from __future__ import annotations

from app.config.settings import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# The {retrieved_sources} placeholder is substituted by ADK from session state
# before the prompt reaches the model.
_ANSWER_INSTRUCTION = """\
You are the Answer Agent for a codebase knowledge base.

You will be given a developer's question and a set of retrieved sources from
the codebase. Sources are available here:

{retrieved_sources}

Rules:
- Answer using ONLY the retrieved sources. Never invent functions, files, or
  behaviour.
- Cite every factual claim inline, in the form
  (file_path::function_name, L<start>-L<end>).
- Structure the answer clearly: a direct answer first, then supporting detail.
- If the sources do not contain enough information to answer, say so plainly
  and suggest what the developer could look at or index next.
- Be concise and concrete. Prefer specifics from the sources over generalities.
"""


def build_answer_agent():
    """Construct the answer `LlmAgent`.

    Returns:
        A configured ADK `LlmAgent` with no tools (pure synthesis).
    """
    from google.adk.agents import LlmAgent

    settings = get_settings()
    agent = LlmAgent(
        name="answer_agent",
        model=settings.llm_model,
        description=(
            "Synthesises a grounded, fully-cited answer to a developer's "
            "question from retrieved codebase sources."
        ),
        instruction=_ANSWER_INSTRUCTION,
        output_key="final_answer",
    )
    logger.info("Answer agent constructed.")
    return agent
