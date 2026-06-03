"""
Validation agent (Google ADK).

The validation agent (spec section 9 + test-validation flow) checks whether the
codebase actually implements a described test's expected behaviour.

Unlike the other agents, most of its work is *deterministic* — parsing steps,
call-chain membership checks — and lives in `ValidationService`. This agent is
a thin ADK `LlmAgent` wrapper that presents the validation report
conversationally and explains gaps to the developer.

The orchestrator invokes this agent only when it classifies a request as a
validation request; otherwise the knowledge-Q&A path is used.
"""

from __future__ import annotations

from app.config.settings import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# The {validation_report} placeholder is filled from session state by the
# orchestrator, which runs the deterministic ValidationService first.
_VALIDATION_INSTRUCTION = """\
You are the Validation Agent for a codebase knowledge base.

A deterministic validation pass has already compared the test's expected
behaviour against the codebase's call graph. Its structured report is here:

{validation_report}

Your job:
- Present the result clearly: state whether the test's expectations are fully
  implemented, partially implemented, or not implemented.
- For every step that failed verification, explain specifically what is missing
  and give an actionable next step.
- Reference the matched functions and files from the report.
- Do not contradict the deterministic report — it is authoritative on call
  -graph membership. Add explanation and guidance on top of it.
"""


def build_validation_agent():
    """Construct the validation `LlmAgent`.

    Returns:
        A configured ADK `LlmAgent`.
    """
    from google.adk.agents import LlmAgent

    settings = get_settings()
    agent = LlmAgent(
        name="validation_agent",
        model=settings.llm_model,
        description=(
            "Explains, in developer-friendly terms, whether the codebase "
            "implements a test's expected behaviour, based on a deterministic "
            "call-graph validation report."
        ),
        instruction=_VALIDATION_INSTRUCTION,
        output_key="final_answer",
    )
    logger.info("Validation agent constructed.")
    return agent
