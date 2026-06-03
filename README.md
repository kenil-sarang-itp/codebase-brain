# CodeBase Brain

**An agentic AI system that auto-generates and maintains living documentation
for any codebase — and lets developers query it in plain English.**

CodeBase Brain indexes a repository, builds a three-level documentation layer
over it (architecture → modules → functions), keeps that documentation in sync
as pull requests merge, and answers developer questions with answers that cite
the exact files and lines they came from. It can also validate that a test's
expected behaviour is genuinely implemented in the code.

It is built with Google's Agent Development Kit (ADK), FastAPI, PostgreSQL,
Qdrant, Redis/RQ, and a React frontend — and the whole stack is dockerised,
with Arize Phoenix tracing running in its own container.

---

## What it does

| Capability | How |
|---|---|
| **Three-level docs** | L3 architecture/data-flow docs, L2 module docs, L1 five-section function docs — generated top-down so each level informs the next. |
| **Plain-English Q&A** | Two-stage retrieval (Qdrant vector search → Cohere rerank) feeds a Gemini answer agent that cites every claim. |
| **Always in sync** | A GitHub webhook triggers impact-based regeneration on every PR merge — only the affected docs are rebuilt. |
| **Test validation** | Parses a test's expected steps and verifies each one against the real call graph. |
| **Persistent memory** | Remembers a developer's recent questions and learns which parts of the codebase they care about. |
| **Full observability** | Every LLM call, retrieval, and pipeline phase is traced to Phoenix. |

---

## Quick start (zero credentials)

The stack is designed to **boot and run with no API keys at all**. With an empty
configuration it uses offline fallback AI providers and indexes a small bundled
sample repository — enough to click through every screen end to end.

```bash
# 1. Copy the environment template (defaults work as-is).
cp .env.example .env

# 2. Build and start the whole stack.
docker compose up --build

# 3. Open the app.
#    Frontend ........ http://localhost:8080
#    API docs ........ http://localhost:8000/docs
#    Phoenix traces .. http://localhost:6006
```

Then, in the UI: register an account → go to **Index** → enter `/repo` (the
bundled sample) → start indexing → once complete, go to **Ask** and ask a
question.

> With no credentials the answers are clearly-labelled placeholders — the
> point of the zero-credential path is to exercise the full system. See
> [`docs/SETUP.md`](docs/SETUP.md) to switch on real Vertex AI and Cohere.

---

## Going real

To get genuine AI answers, set a few values in `.env` (full detail in
[`docs/SETUP.md`](docs/SETUP.md)):

- **Vertex AI** — set `GCP_PROJECT_ID` and drop a service-account key at
  `./secrets/gcp-sa.json`. One service account covers both Gemini (the LLM) and
  `text-embedding-004` (embeddings).
- **Cohere** — set `COHERE_API_KEY` to enable real reranking.
- **GitHub** — set `REPO_SOURCE=github_mcp`, `GITHUB_REPO=owner/name`, and
  `GITHUB_TOKEN` to index a real GitHub repository through the official GitHub
  MCP server.

Every one of these is optional and independently switchable.

---

## Architecture at a glance

```
                         ┌──────────────┐
   Browser ──────────────│   Frontend   │  React + Nginx  (:8080)
                         └──────┬───────┘
                                │  /api/*  (proxied)
                         ┌──────▼───────┐
                         │   Backend    │  FastAPI        (:8000)
                         │              │
                         │  • auth      │
                         │  • /chat ────┼──►  ADK agents (orchestrator,
                         │  • /index    │       retrieval, answer,
                         │  • /webhook  │       validation, indexing)
                         └──┬───┬───┬───┘
            enqueue jobs    │   │   │
                ┌───────────▼┐  │   │
                │   Redis    │  │   │
                │   + RQ     │  │   │
                └─────┬──────┘  │   │
                ┌─────▼──────┐  │   │
                │  Workers   │  │   │   (heavy 6-phase indexing pipeline)
                │  (x2)      │  │   │
                └─────┬──────┘  │   │
        ┌─────────────┼─────────┼───┼──────────────┐
   ┌────▼────┐  ┌─────▼───┐ ┌───▼───────┐  ┌───────▼──────┐
   │PostgreSQL│  │ Qdrant  │ │  Phoenix  │  │  GitHub MCP  │
   │  (data)  │  │(vectors)│ │ (tracing) │  │   (repos)    │
   └──────────┘  └─────────┘ └───────────┘  └──────────────┘
```

A deeper walkthrough — including the six-phase indexing pipeline, the agent
roles, and the design patterns used — is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Documentation

- [`docs/SETUP.md`](docs/SETUP.md) — installation, configuration, and every
  environment variable explained.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the system works:
  pipeline, agents, data model, design patterns.
- [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md) — the folder layout
  with a one-line explanation of every file.
- [`docs/USAGE.md`](docs/USAGE.md) — how developers actually use the app, plus
  the full HTTP API reference.

---

## Tech stack

**Backend** — Python 3.12, FastAPI, SQLAlchemy 2 (async), PostgreSQL, Qdrant,
Redis + RQ, Google ADK, Google GenAI / Vertex AI, Cohere, Tree-sitter, Arize
Phoenix / OpenTelemetry, the GitHub MCP server.

**Frontend** — React 18, React Router, Vite, served by Nginx.

**Infrastructure** — Docker Compose orchestrates eight services.
