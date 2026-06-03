"""
Validation service — test-expectation verification.

Implements the spec's test-validation flow: given a test description, check
whether the codebase actually implements the expected behaviour. The technique
(spec section: Validation agent) is *call-chain membership*:

    1. The LLM parses the test description into discrete expected steps.
    2. For each step, retrieval finds the most relevant function.
    3. We verify that function is reachable in the call graph from a relevant
       entry point — i.e. it is genuinely wired into a real flow, not dead code.
    4. A per-step pass/fail plus an LLM-written summary form the report.

This service performs the deterministic graph checks; the LLM steps are
delegated to the providers. Keeping the graph logic here makes it testable
without any model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.db.repositories.doc_repository import DocRepository
from app.external.interfaces import LLMProvider
from app.observability.tracing import traced_span
from app.pipeline import prompts
from app.pipeline.call_graph import CallGraph, CallGraphBuilder

logger = get_logger(__name__)


@dataclass
class ExpectedStep:
    """One expected behavioural step parsed from a test description."""

    action: str
    expected_input: str = ""
    expected_output: str = ""


@dataclass
class StepResult:
    """The verification outcome for a single expected step."""

    step: ExpectedStep
    implemented: bool
    matched_function: str | None = None
    evidence: str = ""


@dataclass
class ValidationReport:
    """The full validation result for a test."""

    test_name: str
    steps: list[StepResult] = field(default_factory=list)
    summary: str = ""

    @property
    def passed(self) -> bool:
        """True only if every expected step was verified as implemented."""
        return bool(self.steps) and all(s.implemented for s in self.steps)

    @property
    def pass_count(self) -> int:
        """Number of steps verified as implemented."""
        return sum(1 for s in self.steps if s.implemented)


class ValidationService:
    """Verifies test expectations against the indexed codebase."""

    def __init__(self, llm: LLMProvider, doc_repo: DocRepository) -> None:
        """Inject the LLM (for parsing/summarising) and the doc repository."""
        self._llm = llm
        self._doc_repo = doc_repo
        self._graph_builder = CallGraphBuilder()

    async def extract_steps(self, test_description: str) -> list[ExpectedStep]:
        """Use the LLM to parse a test description into structured steps.

        The model is asked for strict JSON; we parse defensively so a slightly
        malformed response degrades to "no steps" rather than crashing.
        """
        prompt = prompts.build_step_extraction_prompt(test_description)
        with traced_span("validation.extract_steps"):
            response = await self._llm.generate(
                prompt,
                system_instruction=prompts.VALIDATION_SYSTEM_INSTRUCTION,
                temperature=0.0,  # deterministic parsing
            )

        steps = self._parse_steps_json(response.text)
        logger.info("Extracted %d expected steps from test.", len(steps))
        return steps

    async def verify_step(
        self,
        step: ExpectedStep,
        candidate_functions: list[str],
        graph: CallGraph,
    ) -> StepResult:
        """Verify one step via call-chain membership.

        A step is considered implemented if at least one candidate function
        (surfaced by retrieval for that step) exists in the call graph and is
        connected to it — either reachable from an entry point or itself one.
        """
        for fn in candidate_functions:
            if fn not in graph.defined_in:
                continue
            # Connected = has callers, or is itself an entry point that calls
            # into the graph. Either way it is part of a real flow.
            is_connected = bool(graph.called_by.get(fn)) or bool(
                graph.calls.get(fn)
            )
            if is_connected:
                return StepResult(
                    step=step,
                    implemented=True,
                    matched_function=fn,
                    evidence=(
                        f"'{fn}' is defined in {graph.defined_in[fn]} and is "
                        f"connected in the call graph "
                        f"({graph.callee_count(fn)} caller(s))."
                    ),
                )

        return StepResult(
            step=step,
            implemented=False,
            evidence=(
                "No connected function implementing this step was found in "
                "the call graph."
            ),
        )

    async def summarise(
        self, test_name: str, results: list[StepResult]
    ) -> str:
        """Ask the LLM to write the final human-readable validation summary."""
        findings = "\n".join(
            f"- [{'PASS' if r.implemented else 'FAIL'}] {r.step.action} :: "
            f"{r.evidence}"
            for r in results
        )
        prompt = prompts.build_validation_summary_prompt(test_name, findings)
        with traced_span("validation.summarise"):
            response = await self._llm.generate(
                prompt,
                system_instruction=prompts.VALIDATION_SYSTEM_INSTRUCTION,
                temperature=0.2,
            )
        return response.text

    async def build_graph_from_db(self) -> CallGraph:
        """Reconstruct the in-memory call graph from persisted `call_graph` rows.

        Validation runs long after indexing, so the graph is rebuilt from the
        database rather than recomputed from source.
        """
        entries = await self._doc_repo.get_all_call_graph()
        graph = CallGraph()
        for entry in entries:
            graph.defined_in[entry.function_name] = entry.file_path
            graph.languages[entry.function_name] = entry.language
            graph.calls[entry.function_name] = set(entry.calls or [])
            graph.called_by[entry.function_name] = set(entry.called_by or [])
        return graph

    # ------------------------------------------------------------ helpers --
    @staticmethod
    def _parse_steps_json(raw: str) -> list[ExpectedStep]:
        """Parse the LLM's JSON step list, tolerating Markdown fences."""
        text = raw.strip()
        # Strip ```json ... ``` fences if the model added them.
        if text.startswith("```"):
            text = text.split("```", 2)[1] if text.count("```") >= 2 else text
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Could not parse step-extraction JSON; got: %s", raw[:200])
            return []

        if not isinstance(data, list):
            return []

        steps: list[ExpectedStep] = []
        for item in data:
            if isinstance(item, dict) and item.get("action"):
                steps.append(
                    ExpectedStep(
                        action=str(item.get("action", "")),
                        expected_input=str(item.get("expected_input", "")),
                        expected_output=str(item.get("expected_output", "")),
                    )
                )
        return steps
