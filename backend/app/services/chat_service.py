"""
Chat service — the orchestration brain behind the /chat endpoint.

This service performs the deterministic orchestrator responsibilities from the
spec, around the LLM agent pipelines:

    1. Load memory — recent history + developer profile (via `MemoryService`).
    2. Classify the request — knowledge question vs. test-validation request.
    3. Delegate:
         * knowledge  → retrieval→answer ADK pipeline.
         * validation → deterministic `ValidationService` + validation agent.
    4. Persist — record the exchange to memory and write a query-log row.

Classification is rule-based on purpose: it must be fast, free, and
predictable. The LLM is reserved for retrieval reasoning and answer synthesis,
where it adds real value.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.config.settings import get_settings
from app.core.constants import QueryType
from app.core.logging import get_logger
from app.db.models import QueryLog
from app.db.repositories.doc_repository import DocRepository
from app.db.repositories.memory_repository import MemoryRepository
from app.external.provider_factory import get_llm_provider
from app.observability.tracing import root_span, set_trace_context, traced_span
from app.pipeline import prompts
from app.services.memory_service import MemoryService
from app.services.retrieval_service import RetrievalService
from app.services.validation_service import ValidationService

logger = get_logger(__name__)

# Keywords that signal a test-validation request rather than a question.
_VALIDATION_CUES = (
    "validate",
    "verify the test",
    "does the code implement",
    "test expects",
    "check the test",
    "expected behaviour",
    "expected behavior",
)


@dataclass
class ChatResult:
    """The result of handling one /chat request."""

    answer: str
    query_type: QueryType
    citations: list[str] = field(default_factory=list)
    latency_ms: int = 0


class ChatService:
    """Coordinates memory, classification, agent delegation, and logging."""

    def __init__(
        self,
        retrieval_service: RetrievalService,
        memory_service: MemoryService,
        memory_repo: MemoryRepository,
        doc_repo: DocRepository,
    ) -> None:
        """Inject all collaborators (every one behind an interface/repository)."""
        self._retrieval = retrieval_service
        self._memory_service = memory_service
        self._memory_repo = memory_repo
        self._doc_repo = doc_repo

    async def handle_chat(
        self,
        *,
        developer_id: str,
        session_id: str,
        question: str,
    ) -> ChatResult:
        """Handle one developer chat turn end to end.

        Args:
            developer_id: Stable id of the developer (the logged-in user).
            session_id: Conversation session id.
            question: The developer's message.

        Returns:
            A `ChatResult` with the answer, its type, and citations.
        """
        started = time.monotonic()

        with root_span(
            "chat.turn",
            session_id=session_id,
            user_id=developer_id,
            attributes={"query": question[:200]},
        ):
            # 1. Load conversational memory.
            history_text, profile_text = await self._memory_service.load_context(
                session_id=session_id, developer_id=developer_id
            )

            # 2. Classify the request.
            query_type = self._classify(question)
            logger.info("Classified query as %s", query_type.value)

            # 3. Delegate to the right pipeline.
            if query_type is QueryType.VALIDATION:
                answer, citations = await self._handle_validation(question)
            else:
                answer, citations = await self._handle_knowledge(
                    question, history_text, profile_text
                )

        latency_ms = int((time.monotonic() - started) * 1000)

        # 4. Persist memory + query log.
        await self._memory_service.record_exchange(
            session_id=session_id,
            developer_id=developer_id,
            question=question,
            answer=answer,
            referenced_files=[c.split("::")[0] for c in citations],
        )
        await self._memory_repo.add_query_log(
            QueryLog(
                developer_id=developer_id,
                session_id=session_id,
                question=question,
                query_type=query_type.value,
                answer_preview=answer[:1000],
                num_sources=len(citations),
                latency_ms=latency_ms,
            )
        )

        return ChatResult(
            answer=answer,
            query_type=query_type,
            citations=citations,
            latency_ms=latency_ms,
        )

    # ---------------------------------------------------------- knowledge ---
    async def _handle_knowledge(
        self, question: str, history_text: str, profile_text: str
    ) -> tuple[str, list[str]]:
        """Handle a knowledge question via retrieval + cited answer synthesis.

        Retrieval is run directly through `RetrievalService` (deterministic,
        fast) and the answer is synthesised by the LLM with the spec's grounded
        -answer prompt. This is the retrieval→answer pipeline; it is run here
        directly rather than through the ADK runner so memory/profile context
        is injected precisely and citations are extracted reliably.
        """
        sources = await self._retrieval.retrieve(question)
        if not sources:
            return (
                "I could not find anything relevant in the indexed codebase "
                "for that question. The repository may not be indexed yet, or "
                "the question may need rephrasing.",
                [],
            )

        # Assemble the sources block for the prompt.
        sources_text = "\n\n".join(
            f"[Source {i + 1}] {s.citation} (level: {s.level_label})\n"
            f"Documentation:\n{s.doc_text}\n"
            f"Code:\n{s.code_text}"
            for i, s in enumerate(sources)
        )

        prompt = prompts.build_answer_prompt(
            question=question,
            sources_text=sources_text,
            history_text=history_text,
            profile_text=profile_text,
        )
        llm = get_llm_provider()
        with traced_span("chat.answer_synthesis", {"llm.model": get_settings().llm_model}):
            response = await llm.generate(
                prompt,
                system_instruction=prompts.ANSWER_SYSTEM_INSTRUCTION,
                temperature=0.2,
                max_output_tokens=4096,
            )

        citations = [s.citation for s in sources]
        return response.text, citations

    # --------------------------------------------------------- validation ---
    async def _handle_validation(
        self, question: str
    ) -> tuple[str, list[str]]:
        """Handle a test-validation request.

        Runs the deterministic `ValidationService` (step extraction, call-chain
        membership checks) then has the validation agent explain the report.
        """
        llm = get_llm_provider()
        validator = ValidationService(llm, self._doc_repo)

        # 1. Parse the test into expected steps.
        steps = await validator.extract_steps(question)
        if not steps:
            return (
                "I could not extract concrete expected steps from that test "
                "description. Try describing the test as a sequence of "
                "expected actions.",
                [],
            )

        # 2. Rebuild the call graph and verify each step.
        graph = await validator.build_graph_from_db()
        results = []
        citations: list[str] = []
        for step in steps:
            # Retrieval surfaces candidate functions for this step.
            candidates = await self._retrieval.retrieve(
                step.action, level_filter=1
            )
            candidate_names = [c.name for c in candidates]
            citations.extend(c.citation for c in candidates[:2])
            result = await validator.verify_step(step, candidate_names, graph)
            results.append(result)

        # 3. Summarise the findings.
        summary = await validator.summarise(question[:80], results)

        # Build a clear, structured report prefix.
        passed = sum(1 for r in results if r.implemented)
        header = (
            f"Validation result: {passed}/{len(results)} expected steps "
            f"verified against the codebase.\n\n"
        )
        return header + summary, list(dict.fromkeys(citations))

    # ----------------------------------------------------- classification ---
    @staticmethod
    def _classify(question: str) -> QueryType:
        """Classify a request as a knowledge question or a validation request.

        Rule-based and deterministic — see the module docstring for the
        rationale. The cues are specific phrases that reliably indicate the
        developer wants test verification.
        """
        lowered = question.lower()
        if any(cue in lowered for cue in _VALIDATION_CUES):
            return QueryType.VALIDATION
        return QueryType.KNOWLEDGE
