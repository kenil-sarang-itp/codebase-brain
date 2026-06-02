"""
Prompt templates.

Every LLM prompt the system sends lives here as a template. Centralising them
keeps prompt engineering in one auditable place and keeps the agents/pipeline
free of large inline strings.

Each builder returns a fully-formed prompt string. The doc-generation prompts
implement the spec's cascading-context design: higher-level docs are injected
as context into the generation of lower-level docs.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# System instructions                                                         #
# --------------------------------------------------------------------------- #
DOC_SYSTEM_INSTRUCTION = (
    "You are a senior software engineer writing precise, accurate technical "
    "documentation. Document only what the provided code and context actually "
    "show. Never invent behaviour, parameters, or dependencies. Be concise and "
    "concrete."
)

ANSWER_SYSTEM_INSTRUCTION = """You are a senior software engineer and codebase expert. Your job is to give thorough, well-structured answers to developer questions about a codebase.

FORMATTING RULES — always follow these:
1. Start with a direct 1-2 sentence answer to the question.
2. Then provide a detailed explanation with these sections as relevant:
   - **How it works**: Step-by-step explanation of the mechanism or flow.
   - **Key components**: The files, functions, or classes involved and their roles.
   - **Data flow**: How data moves through the system (inputs → processing → outputs).
   - **Important details**: Edge cases, gotchas, design decisions worth knowing.
   - **Related code**: Mention nearby functions or files that are relevant context.
3. Use markdown formatting: **bold** for important terms, `code` for function/file names, bullet points for lists, numbered steps for flows.
4. Cite EVERY factual claim inline like this: `(file.py::function_name, L10-L25)`
5. Aim for comprehensive answers — 200 to 500 words unless the question is trivial.
6. Never say "based on the sources" or similar — just answer directly as an expert.
7. If sources are genuinely insufficient, say specifically what is missing and suggest what the developer should look for.
"""

VALIDATION_SYSTEM_INSTRUCTION = (
    "You are a test-validation engineer. Compare expected behaviour against "
    "what the codebase actually implements. Be strict and specific: only mark "
    "a step as implemented if the evidence clearly supports it."
)


# --------------------------------------------------------------------------- #
# Level 3 — architecture / data-flow docs                                     #
# --------------------------------------------------------------------------- #
def build_l3_flow_prompt(
    flow_name: str, call_chain_text: str, function_code_snippets: str
) -> str:
    """Prompt for a Level-3 data-flow document for one entry point.

    `function_code_snippets` contains the actual source code of every function
    in the call chain — the LLM must ground every claim in that code, not guess.
    """
    return (
        f"Write a data-flow document for the flow named '{flow_name}'.\n\n"
        "The flow's call chain (in execution order) and the actual source code "
        "for each function are shown below. Base every statement on the code — "
        "do not invent behaviour that is not visible in it.\n\n"
        f"=== CALL CHAIN (execution order) ===\n{call_chain_text}\n\n"
        f"=== FUNCTION SOURCE CODE ===\n{function_code_snippets}\n\n"
        "Write clearly structured prose with exactly these sections:\n"
        "1. **Overview** — what this flow accomplishes end-to-end.\n"
        "2. **Trigger** — what starts the flow and with what input data.\n"
        "3. **Step-by-step walkthrough** — each function in call-chain order: "
        "what it receives, what it does, what it returns or side-effects.\n"
        "4. **Data persisted** — what is written, where (DB table / store), "
        "and under what conditions.\n"
        "5. **Return value** — what is handed back to the original caller.\n"
        "6. **Failure modes** — how each step can fail and what the system does."
    )


def build_l3_overview_prompt(
    module_summary: str,
    entry_points_text: str,
    code_context: str = "",
) -> str:
    """Prompt for the single application-overview document.

    `code_context` carries the README (if present) and opening lines of the
    top files so the LLM can ground the overview in real code rather than
    guessing from file names alone.
    """
    code_section = (
        f"\n=== CODE CONTEXT (README + key file summaries) ===\n{code_context}\n"
        if code_context
        else ""
    )
    return (
        "Write a high-level architecture overview of this application.\n\n"
        "Base every claim on the code context provided. Do not invent "
        "components, frameworks, or behaviours that are not shown.\n\n"
        f"=== MODULE STRUCTURE ===\n{module_summary}\n\n"
        f"=== ENTRY POINTS (functions nothing else calls — roots of data flows) ===\n"
        f"{entry_points_text}\n"
        f"{code_section}\n"
        "Produce a clear narrative covering:\n"
        "1. **What this system does** — its purpose in one paragraph.\n"
        "2. **Major components** — the key files/modules and their roles.\n"
        "3. **Main data flows** — how data moves from entry points through "
        "the components to storage or output.\n"
        "4. **External dependencies** — databases, APIs, queues, or services "
        "the system integrates with.\n"
        "5. **Key design decisions** — patterns or constraints visible in the code.\n\n"
        "Keep it concise enough that a new engineer can read it in three minutes."
    )


# --------------------------------------------------------------------------- #
# Level 2 — module docs                                                       #
# --------------------------------------------------------------------------- #
def build_l2_module_prompt(
    file_path: str,
    code: str,
    app_overview: str,
    related_flows: str,
    call_graph_summary: str,
) -> str:
    """Prompt for a Level-2 module document for one source file.

    Per the spec, the application overview (L3) and the file's flow membership
    and call-graph summary are injected as context.
    """
    return (
        f"Write a module-level document for the source file '{file_path}'.\n\n"
        f"=== APPLICATION OVERVIEW (context) ===\n{app_overview}\n\n"
        f"=== FLOWS THIS FILE PARTICIPATES IN ===\n{related_flows}\n\n"
        f"=== CALL-GRAPH SUMMARY FOR THIS FILE ===\n{call_graph_summary}\n\n"
        f"=== SOURCE CODE ===\n{code}\n\n"
        "Cover: the module's role, what it exposes to the rest of the system, "
        "what it depends on, what data flows through it, and which entry-point "
        "flows it participates in."
    )


# --------------------------------------------------------------------------- #
# Level 1 — function / class docs                                             #
# --------------------------------------------------------------------------- #
def build_l1_function_prompt(
    function_name: str,
    code: str,
    app_overview: str,
    module_doc: str,
    dependency_info: str,
) -> str:
    """Prompt for a Level-1 five-section function/class document.

    Injects all three context layers: application overview (L3), the module
    doc (L2), and exact static-analysis dependency data.
    """
    return (
        f"Write documentation for the function or class '{function_name}'.\n\n"
        f"=== APPLICATION OVERVIEW (context) ===\n{app_overview}\n\n"
        f"=== MODULE DOCUMENTATION (context) ===\n{module_doc}\n\n"
        f"=== DEPENDENCY DATA (from static analysis — authoritative) ===\n"
        f"{dependency_info}\n\n"
        f"=== SOURCE CODE ===\n{code}\n\n"
        "Produce exactly these five sections:\n"
        "Purpose — what this does and why it exists.\n"
        "Parameters — inputs, outputs, types, and edge cases.\n"
        "How it works — a plain-English walkthrough of the logic.\n"
        "Dependencies — what it calls, what calls it, and which services or "
        "tables it touches (use the dependency data above, do not guess).\n"
        "Gotchas — non-obvious behaviour, side effects, and warnings."
    )


# --------------------------------------------------------------------------- #
# Query answering                                                             #
# --------------------------------------------------------------------------- #
def build_answer_prompt(
    question: str,
    sources_text: str,
    history_text: str,
    profile_text: str,
) -> str:
    """Prompt for the answer agent — grounded, detailed, cited Q&A."""
    context_block = ""
    if profile_text:
        context_block += f"=== DEVELOPER CONTEXT ===\n{profile_text}\n\n"
    if history_text:
        context_block += (
            f"=== RECENT CONVERSATION (for follow-up context) ===\n"
            f"{history_text}\n\n"
        )

    return (
        f"{context_block}"
        f"=== CODEBASE SOURCES ===\n"
        f"The following sources were retrieved from the indexed codebase.\n"
        f"Each source shows the file path, function/component name, line "
        f"numbers, documentation, and code.\n\n"
        f"{sources_text}\n\n"
        f"=== DEVELOPER'S QUESTION ===\n{question}\n\n"
        "=== YOUR TASK ===\n"
        "Write a thorough, well-formatted answer following the system "
        "instructions. Structure your answer clearly with sections and "
        "bullet points. Explain the HOW and WHY, not just the WHAT. "
        "Cite every specific claim with (file::function, L<start>-L<end>). "
        "A good answer is detailed and educational — help the developer "
        "truly understand the codebase."
    )


# --------------------------------------------------------------------------- #
# Test validation                                                             #
# --------------------------------------------------------------------------- #
def build_step_extraction_prompt(test_description: str) -> str:
    """Prompt asking the LLM to parse a test into structured expected steps."""
    return (
        "Extract the expected behavioural steps from the test description "
        "below. Return a JSON array; each element must be an object with "
        "keys: 'action' (string), 'expected_input' (string), and "
        "'expected_output' (string). Return ONLY the JSON array, no prose, no "
        "Markdown fences.\n\n"
        f"=== TEST DESCRIPTION ===\n{test_description}"
    )


def build_validation_summary_prompt(
    test_name: str, per_step_findings: str
) -> str:
    """Prompt asking the LLM to write a final validation report summary."""
    return (
        f"Write a concise validation summary for the test '{test_name}'.\n\n"
        f"=== PER-STEP FINDINGS ===\n{per_step_findings}\n\n"
        "State how many steps were verified, list any gaps, and give specific, "
        "actionable next steps for each gap."
    )
