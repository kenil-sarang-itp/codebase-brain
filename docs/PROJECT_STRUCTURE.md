# Project Structure

A map of the repository, with a one-line explanation of every file.

```
codebase-brain/
├── docker-compose.yml          Orchestrates all eight services.
├── .env.example                Documented environment-variable template.
├── .dockerignore               Keeps Docker build contexts lean.
├── .gitignore                  Standard ignore rules.
├── README.md                   Project overview and quick start.
├── sample-repo/                A tiny repo so local indexing works out of the box.
├── secrets/                    Mount point for the GCP service-account key.
├── scripts/
│   └── init_db.py              Creates all DB tables from the ORM models.
├── docs/                       This documentation set.
├── backend/                    The Python backend.
└── frontend/                   The React frontend.
```

---

## Backend — `backend/`

```
backend/
├── Dockerfile                  Builds the image used by both API and workers.
├── requirements.txt            Pinned Python dependencies.
└── app/
    ├── config/
    │   └── settings.py         Pydantic-settings config; every value has a default.
    │
    ├── core/                   Cross-cutting fundamentals (no app logic).
    │   ├── constants.py        Enums (doc levels, statuses, query types) + skip lists.
    │   ├── exceptions.py       Typed exception hierarchy with HTTP-status mapping.
    │   ├── logging.py          Structured logging + trace-id context.
    │   └── security.py         Password hashing and JWT create/decode.
    │
    ├── db/                     Persistence layer.
    │   ├── session.py          Async + lazy-sync SQLAlchemy engines and sessions.
    │   ├── models.py           All ORM models — the schema source of truth.
    │   ├── qdrant_store.py     Qdrant client wrapper (named vectors, search).
    │   └── repositories/       One repository class per aggregate.
    │       ├── base.py         Generic get/list/add/delete repository.
    │       ├── user_repository.py        User lookups.
    │       ├── doc_repository.py          Docs, call graph, flow membership.
    │       ├── indexing_repository.py     Indexing sessions and jobs.
    │       └── memory_repository.py       Conversation history, profiles, query logs.
    │
    ├── external/               Adapters to outside services (the "ports").
    │   ├── interfaces.py       Abstract LLM / Embedding / Reranker ports.
    │   ├── local_providers.py  Offline fallback LLM/embeddings/reranker.
    │   ├── vertex_provider.py  Real Vertex AI LLM + embedding adapters.
    │   ├── cohere_provider.py  Real Cohere rerank adapter.
    │   ├── provider_factory.py Picks real-vs-offline providers and caches them.
    │   ├── repository_source.py        Abstract RepositorySource port.
    │   ├── local_repository_source.py  Reads a repo from the filesystem.
    │   ├── github_mcp_source.py        Reads a repo via the GitHub MCP server.
    │   └── repository_source_factory.py  Picks local-vs-GitHub source.
    │
    ├── pipeline/               The indexing pipeline (pure, stateless logic).
    │   ├── chunking/
    │   │   ├── base.py             CodeChunk model + ChunkingStrategy interface.
    │   │   ├── language_detector.py  Maps file extensions to languages.
    │   │   ├── tree_sitter_strategy.py  Syntax-aware chunking.
    │   │   ├── fallback_strategies.py   Markdown + fixed-size chunking.
    │   │   └── chunker.py          Chain-of-Responsibility chunker facade.
    │   ├── static_analysis.py  Extracts functions and their calls.
    │   ├── call_graph.py       Builds the call graph; traces flows.
    │   ├── prompts.py          All LLM prompt templates.
    │   ├── doc_generator.py    Generates L1/L2/L3 docs via the LLM.
    │   ├── embedder.py         Embeds code, docs, and queries.
    │   └── indexer.py          Writes docs+vectors to PostgreSQL and Qdrant.
    │
    ├── agents/                 The Google ADK agent system.
    │   ├── orchestrator.py     Composes the retrieval→answer pipeline.
    │   ├── retrieval_agent.py  ADK agent that gathers relevant sources.
    │   ├── answer_agent.py     ADK agent that writes the cited answer.
    │   ├── validation_agent.py ADK agent that explains a validation report.
    │   ├── indexing_agent.py   ADK agent that reports indexing status.
    │   ├── agent_runner.py     Runs an ADK agent and extracts its result.
    │   └── tools/
    │       ├── retrieval_tools.py  Knowledge-base search, as ADK tools.
    │       └── indexing_tools.py   Indexing-status tools, as ADK tools.
    │
    ├── services/               Application logic — orchestrates the layers.
    │   ├── auth_service.py        Registration, login, token issuance.
    │   ├── memory_service.py      Short-term history + long-term profiles.
    │   ├── retrieval_service.py   Two-stage RAG (vector search + rerank).
    │   ├── validation_service.py  Test step parsing + call-chain verification.
    │   ├── chat_service.py        The /chat orchestration brain.
    │   ├── indexing_service.py    The six-phase indexing pipeline.
    │   └── service_factory.py     Composition root — wires services together.
    │
    ├── schemas/
    │   └── api_schemas.py      Pydantic request/response models (the HTTP contract).
    │
    ├── api/                    The FastAPI HTTP layer.
    │   ├── main.py             App factory: middleware, lifespan, routers.
    │   ├── dependencies.py     JWT auth dependency (get_current_user).
    │   ├── error_handlers.py   Maps typed exceptions to JSON responses.
    │   └── routes/
    │       ├── auth.py         /auth/register, /login, /logout, /me.
    │       ├── chat.py         /chat — the developer Q&A endpoint.
    │       ├── indexing.py     /index and /index-status.
    │       ├── webhook.py      /webhook — GitHub PR-merge handler.
    │       └── query_logs.py   /query-logs and /health.
    │
    ├── workers/                Background job processing.
    │   ├── queue.py            RQ enqueue helpers.
    │   ├── tasks.py            The RQ task functions (indexing, PR-sync).
    │   └── rq_worker.py        The worker process entry point.
    │
    └── observability/
        └── tracing.py          Phoenix / OpenTelemetry setup and span helpers.
```

### How the backend layers fit together

Requests flow **downward** and dependencies point **inward**:

```
api/routes  →  services  →  pipeline / agents / repositories  →  external / db
```

- **`api/`** handles HTTP only — parse, authenticate, delegate, serialise.
- **`services/`** holds application logic and orchestrates everything below.
- **`pipeline/`**, **`agents/`**, **`db/repositories/`** are focused
  capabilities.
- **`external/`** and **`db/`** touch the outside world (SDKs, databases).

Nothing in `services/` or above imports a vendor SDK directly — they depend on
the abstract ports in `external/interfaces.py` and `external/repository_source.py`.

---

## Frontend — `frontend/`

```
frontend/
├── Dockerfile                  Multi-stage: Node build → Nginx serve.
├── nginx.conf                  Serves the SPA; proxies /api to the backend.
├── package.json                Dependencies and scripts.
├── vite.config.js              Vite config with a dev-time /api proxy.
├── index.html                  HTML entry point; loads the fonts.
└── src/
    ├── main.jsx                Mounts the React tree.
    ├── App.jsx                 Routing + auth provider + route guard.
    ├── api/
    │   └── client.js           Typed fetch wrapper for every endpoint.
    ├── context/
    │   └── AuthContext.jsx     Auth state + login/register/logout actions.
    ├── components/
    │   ├── AppShell.jsx        Persistent header + navigation frame.
    │   └── Feedback.jsx        Shared Banner and Spinner components.
    ├── views/
    │   ├── LoginView.jsx       Login / register screen.
    │   ├── ChatView.jsx        The "Ask" screen — Q&A with citations.
    │   ├── IndexingView.jsx    The "Index" screen — start + live progress.
    │   └── QueryLogsView.jsx   The "History" screen — query audit table.
    └── styles/
        └── global.css          The design system (CSS variables, primitives).
```
