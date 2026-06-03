"""
Orchestrator agent (Google ADK).

The orchestrator (spec section 9) is the entry point for every developer
request. Its responsibilities:

    1. Load context — recent conversation history and the developer's long
       -term profile (the persistent-memory feature).
    2. Classify the request — a knowledge question vs. a test-validation
       request.
    3. Delegate — run the retrieval → answer pipeline for knowledge questions,
       or the validation pipeline for validation requests.
    4. Persist — record the exchange to short-term memory and update the
       developer profile.

ADK gives two ways to compose agents: LLM-driven delegation (an `LlmAgent` with
`sub_agents`) and deterministic workflow agents (`SequentialAgent` etc). The
knowledge path here is fundamentally a fixed pipeline — retrieve, then answer —
so a `SequentialAgent` models it precisely and predictably. Classification and
memory handling are done in the `ChatService` around the agent run, which keeps
this module focused purely on agent composition.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.services.retrieval_service import RetrievalService

logger = get_logger(__name__)


def build_knowledge_pipeline(retrieval_service: RetrievalService):
    """Build the knowledge-question agent pipeline: retrieval → answer.

    A `SequentialAgent` runs its children in order, threading session state
    between them. The retrieval agent writes `retrieved_sources`; the answer
    agent reads it. This deterministic composition is exactly the spec's query
    flow.

    Args:
        retrieval_service: The RAG retrieval service.

    Returns:
        An ADK `SequentialAgent` representing the full knowledge pipeline.
    """
    from google.adk.agents import SequentialAgent

    from app.agents.answer_agent import build_answer_agent
    from app.agents.retrieval_agent import build_retrieval_agent

    retrieval_agent = build_retrieval_agent(retrieval_service)
    answer_agent = build_answer_agent()

    pipeline = SequentialAgent(
        name="knowledge_pipeline",
        description=(
            "Answers a developer's codebase question by first retrieving "
            "relevant sources, then synthesising a cited answer."
        ),
        sub_agents=[retrieval_agent, answer_agent],
    )
    logger.info("Knowledge pipeline (retrieval -> answer) constructed.")
    return pipeline


def build_orchestrator(retrieval_service: RetrievalService):
    """Build the root orchestrator agent.

    For the knowledge path this returns the sequential retrieval→answer
    pipeline. The orchestrator-level concerns that are *not* agent reasoning —
    query classification and memory load/save — are handled deterministically
    by `ChatService`, which selects this pipeline or the validation pipeline.

    Keeping classification in code (not an LLM hop) makes routing fast,
    cheap, and testable, while the LLM is reserved for the work it is good at:
    retrieval reasoning and answer synthesis.

    Args:
        retrieval_service: The RAG retrieval service.

    Returns:
        The root agent for knowledge questions.
    """
    return build_knowledge_pipeline(retrieval_service)
