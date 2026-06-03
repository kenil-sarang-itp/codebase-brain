# Architecture

This document explains how CodeBase Brain works: the indexing pipeline, the
agent system, the data model, the request flows, and the design patterns that
hold it together.

---

## 1. The big picture

CodeBase Brain has two halves:

1. **The indexing pipeline** turns a repository into a queryable knowledge
   base — a three-level documentation layer plus vector embeddings and a call
   graph.
2. **The agent system** answers questions and validates tests against that
   knowledge base.

A FastAPI application exposes both over HTTP; the heavy indexing work runs in
separate RQ worker processes so the API stays responsive.

---

## 2. The three documentation levels

The spec's core idea is layered documentation, generated **top-down**:

- **Level 3 — Architecture / data-flow docs.** One per entry point (a function
  nobody calls). Generated **first** so the system's shape is established
  before anything below it.
- **Level 2 — Module docs.** One per file. Generated with the L3 overview as
  context.
- **Level 1 — Function docs.** One per function/class, in a fixed five-section
  form: *Purpose, Parameters, How it works, Dependencies, Gotchas.* Generated
  with both the L3 overview and the file's L2 doc as context.

This **cascading context** — each level feeding the next — is why the order
matters: an L1 function doc is written by a model that already "knows" the
module and the architecture it lives in.

---

## 3. The six-phase indexing pipeline

`IndexingService` runs initial indexing in six phases, advancing a status row
in PostgreSQL after each so the UI can show live progress:

| Phase | What happens |
|---|---|
| 1 · Discovery | List repository files; apply the skip list (binaries, vendored code, etc). |
| 2 · Static analysis | Chunk every file syntax-aware (Tree-sitter); build the call graph (who-calls-whom) via Python `ast` and Tree-sitter. |
| 3 · L3 generation | Generate the application overview, then one data-flow doc per entry point. |
| 4 · L2 generation | Generate one module doc per file, with the overview cascaded in. |
| 5 · L1 generation | Generate one five-section function doc per chunk, with overview + module doc cascaded in. |
| 6 · Complete | Mark the session done. |

Every generated doc is embedded and written to **both** PostgreSQL (the
authoritative copy) and **Qdrant** (for vector search).

### Static analysis & the call graph

`StaticAnalyzer` extracts each function and the calls it makes.
`CallGraphBuilder` assembles these into a `CallGraph` with three views:
`calls`, `called_by`, and `defined_in`. **Entry points** are functions with no
callers — they anchor the L3 data-flow docs. The call graph is persisted so it
can be rebuilt later for test validation without re-parsing source.

### Chunking

`Chunker` is a Chain-of-Responsibility: it tries `TreeSitterStrategy` first
(true syntax-aware chunks with exact line numbers), falls back to
`MarkdownStrategy` for docs, and finally to a `FixedSizeStrategy` window so
*every* file yields chunks no matter the language.

---

## 4. Keeping docs in sync — PR-driven regeneration

A full re-index on every change would be wasteful. Instead, when a pull request
merges:

1. GitHub calls `POST /api/webhook`; the signature is verified.
2. A PR-sync job is queued.
3. The worker fetches the PR's changed files (via the GitHub MCP server).
4. `IndexingService.regenerate_for_pr` computes the **impact set** — the
   changed files' module docs *and* every architecture flow those files
   participate in (flow membership is stored at index time).
5. Only those docs are regenerated.

This is **impact-based regeneration**: the cost is proportional to the change,
not the repository.

---

## 5. The agent system (Google ADK)

Five agents, built with Google's Agent Development Kit:

| Agent | Role |
|---|---|
| **Orchestrator** | Entry point for every request. Loads memory, classifies the request, delegates. |
| **Retrieval** | An ADK `LlmAgent` with knowledge-base search tools; gathers the most relevant sources. |
| **Answer** | An ADK `LlmAgent` that synthesises a grounded, fully-cited answer from the retrieved sources. |
| **Validation** | Explains, in developer terms, whether a test's expectations are implemented. |
| **Indexing** | Reports on and explains indexing-run status. |

The knowledge-question path is composed as an ADK `SequentialAgent`:
**retrieval → answer**. The retrieval agent writes its sources into ADK session
state under an `output_key`; the answer agent reads them. This makes the
pipeline deterministic and inspectable.

Query **classification** (knowledge question vs. test validation) and
**memory** load/save are done in `ChatService` — deterministic code, not an LLM
hop — so routing is fast, free, and testable. The LLM is reserved for the work
it is genuinely good at: retrieval reasoning and answer synthesis.

### Retrieval — two stages

`RetrievalService` implements two-stage RAG:

1. **Vector search** — embed the query, pull the top *K* (default 15)
   nearest neighbours from Qdrant. Fast, approximate, high recall.
2. **Rerank** — Cohere Rerank scores those candidates and keeps the top *N*
   (default 5). Slower, precise, high precision.

Searching wide then reranking narrow gives both recall and precision.

### Test validation — call-chain membership

`ValidationService` verifies a test without running it:

1. The LLM parses the test description into discrete expected steps.
2. For each step, retrieval surfaces candidate functions.
3. A step is **verified** if a candidate function exists in the call graph and
   is genuinely connected to it (has callers or callees) — i.e. wired into a
   real flow, not dead code.
4. A per-step pass/fail plus an LLM-written summary form the report.

### Persistent memory

`MemoryService` provides two tiers: **short-term** (recent messages of the
current session, injected into prompts so follow-ups have context) and
**long-term** (a `DeveloperProfile` that tracks which modules a developer asks
about most). The learning rule is deliberately simple frequency-counting —
transparent rather than a black box.

---

## 6. Request flows

**Query flow** (`POST /api/chat`): authenticate → load memory → classify →
retrieve (Qdrant + rerank) → synthesise cited answer → persist the exchange and
a query-log row → respond. Typically a few seconds.

**Indexing flow** (`POST /api/index`): create a session row → enqueue an RQ job
→ return immediately. A worker runs the six phases; the client polls
`GET /api/index-status/{id}`.

**Webhook flow** (`POST /api/webhook`): verify signature → if a PR was merged,
enqueue impact-based regeneration → acknowledge.

---

## 7. Data model

PostgreSQL is the source of truth. Key tables: `users`; `call_graph` (one row
per function with its calls/called-by); `generated_docs` (every L1/L2/L3 doc);
`doc_status` (regeneration flags); `flow_membership` (which functions belong to
which architecture flow); `indexing_sessions` and `indexing_jobs` (pipeline
progress); `conversation_history` and `developer_profiles` (memory);
`query_logs` (the audit log).

Qdrant holds one collection, `codebase_knowledge`, with two **named vectors**
per point — `code` and `doc` — so a query can search either the implementation
or its documentation.

---

## 8. Design patterns

The codebase applies patterns deliberately, not decoratively:

- **Repository pattern** — every table is accessed through a repository class;
  services never write SQL.
- **Strategy + Chain-of-Responsibility** — the chunker tries strategies in
  order until one succeeds.
- **Factory + Singleton** — `provider_factory` and `repository_source_factory`
  build the right implementation (real vs. offline, local vs. GitHub) and cache
  it.
- **Adapter / Port** — `LLMProvider`, `EmbeddingProvider`, `RerankerProvider`,
  and `RepositorySource` are abstract ports; concrete adapters (Vertex, Cohere,
  GitHub MCP, local) implement them.
- **Dependency Inversion** — services depend on those interfaces, never on a
  concrete SDK.
- **Composition Root** — `service_factory` is the single place object graphs
  are assembled.

The payoff: the entire system runs with zero credentials (offline adapters
swapped in by the factories) and the same code runs in production (real
adapters) — because nothing above the adapter layer knows the difference.

---

## 9. Observability

`observability/tracing.py` configures OpenTelemetry to export to Phoenix.
Google GenAI / Vertex calls are auto-instrumented; the pipeline adds explicit
spans (`indexing.full_run`, `chat.handle`, `retrieval.retrieve`, …). Tracing
degrades gracefully — if Phoenix is unreachable the app runs normally, just
without traces.
