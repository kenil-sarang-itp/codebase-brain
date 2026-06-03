"""
Documentation generator.

Generates the three documentation levels using the configured `LLMProvider`.
The defining feature (spec section 5) is *cascading context*: levels are
generated L3 → L2 → L1, and each higher level's output is injected as context
into the level below it. That is what makes a Level-1 function doc aware of the
module it lives in and the architecture it serves.

This class is deliberately stateless and provider-agnostic: it receives already
-assembled context strings and returns doc text. Orchestration (deciding *what*
to generate and *in what order*) belongs to the indexing agent, not here —
Single Responsibility.
"""

from __future__ import annotations

from app.core.constants import DocLevel
from app.core.logging import get_logger
from app.external.interfaces import LLMProvider
from app.observability.tracing import traced_span
from app.pipeline import prompts

logger = get_logger(__name__)

# A function called by more than this many others gets richer context.
# (Mirrors the spec's "critical function" rule; the value comes from settings
# at the call site, this constant is only a safe default.)
_DEFAULT_MAX_CODE_CONTEXT_CHARS = 12_000


class DocGenerator:
    """Generates Level 1/2/3 documentation text from assembled context."""

    def __init__(self, llm: LLMProvider) -> None:
        """Inject the LLM provider (Dependency Inversion — any provider works)."""
        self._llm = llm

    # --------------------------------------------------------- Level 3 ----
    async def generate_flow_doc(
        self, flow_name: str, call_chain_text: str, function_code_snippets: str
    ) -> str:
        """Generate a Level-3 data-flow document for one entry point."""
        prompt = prompts.build_l3_flow_prompt(
            flow_name, call_chain_text, function_code_snippets
        )
        with traced_span(
            "doc_gen.l3_flow", {"flow": flow_name, "level": DocLevel.ARCHITECTURE.value}
        ):
            response = await self._llm.generate(
                prompt,
                system_instruction=prompts.DOC_SYSTEM_INSTRUCTION,
                temperature=0.2,
                max_output_tokens=4096,
            )
        logger.info("Generated L3 flow doc: %s", flow_name)
        return response.text

    async def generate_overview_doc(
        self, module_summary: str, entry_points_text: str, code_context: str = ""
    ) -> str:
        """Generate the single application-overview document (Level 3)."""
        prompt = prompts.build_l3_overview_prompt(
            module_summary, entry_points_text, code_context
        )
        with traced_span("doc_gen.l3_overview"):
            response = await self._llm.generate(
                prompt,
                system_instruction=prompts.DOC_SYSTEM_INSTRUCTION,
                temperature=0.2,
                max_output_tokens=4096,
            )
        logger.info("Generated L3 application overview")
        return response.text

    # --------------------------------------------------------- Level 2 ----
    async def generate_module_doc(
        self,
        file_path: str,
        code: str,
        app_overview: str,
        related_flows: str,
        call_graph_summary: str,
    ) -> str:
        """Generate a Level-2 module document for one source file.

        `app_overview` (a Level-3 output) is injected as context — the
        cascading-context design in action.
        """
        prompt = prompts.build_l2_module_prompt(
            file_path=file_path,
            code=self._truncate(code),
            app_overview=app_overview,
            related_flows=related_flows,
            call_graph_summary=call_graph_summary,
        )
        with traced_span(
            "doc_gen.l2_module",
            {"file": file_path, "level": DocLevel.MODULE.value},
        ):
            response = await self._llm.generate(
                prompt,
                system_instruction=prompts.DOC_SYSTEM_INSTRUCTION,
                temperature=0.2,
                max_output_tokens=1536,
            )
        logger.info("Generated L2 module doc: %s", file_path)
        return response.text

    # --------------------------------------------------------- Level 1 ----
    async def generate_function_doc(
        self,
        function_name: str,
        code: str,
        app_overview: str,
        module_doc: str,
        dependency_info: str,
        *,
        is_critical: bool = False,
        extended_context: str = "",
    ) -> str:
        """Generate a Level-1 five-section function/class document.

        Injects all three context layers (overview, module doc, dependency
        data). For "critical" functions (`is_critical`), `extended_context` —
        the full containing file plus related signatures — is appended so the
        LLM sees the bigger picture, exactly as the spec prescribes.
        """
        code_for_prompt = self._truncate(code)
        if is_critical and extended_context:
            code_for_prompt = (
                f"{code_for_prompt}\n\n"
                f"=== EXTENDED CONTEXT (critical function) ===\n"
                f"{self._truncate(extended_context)}"
            )

        prompt = prompts.build_l1_function_prompt(
            function_name=function_name,
            code=code_for_prompt,
            app_overview=app_overview,
            module_doc=module_doc,
            dependency_info=dependency_info,
        )
        with traced_span(
            "doc_gen.l1_function",
            {
                "function": function_name,
                "level": DocLevel.FUNCTION.value,
                "critical": is_critical,
            },
        ):
            response = await self._llm.generate(
                prompt,
                system_instruction=prompts.DOC_SYSTEM_INSTRUCTION,
                temperature=0.2,
                max_output_tokens=1280,
            )
        logger.debug("Generated L1 function doc: %s", function_name)
        return response.text

    # ------------------------------------------------------------ helpers --
    @staticmethod
    def _truncate(text: str, limit: int = _DEFAULT_MAX_CODE_CONTEXT_CHARS) -> str:
        """Bound prompt size — guards against pathologically large inputs.

        Even with a million-token context window, sending an unbounded blob is
        wasteful and slow; truncation keeps cost and latency predictable.
        """
        if len(text) <= limit:
            return text
        return text[:limit] + "\n... [truncated for length]"
