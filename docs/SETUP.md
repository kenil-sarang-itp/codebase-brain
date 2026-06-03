# Setup & Configuration

This guide covers installing CodeBase Brain, running it, and configuring it for
real use.

---

## Prerequisites

- **Docker** and **Docker Compose v2** (`docker compose`, not `docker-compose`).
  Nothing else is required — Python, Node, and all databases run in containers.

For local development *outside* Docker you would also want Python 3.12 and
Node 22, but the supported path is Docker Compose.

---

## Running the stack

```bash
cp .env.example .env          # copy the config template
docker compose up --build     # build images and start all services
```

This starts eight services:

| Service | Port | Purpose |
|---|---|---|
| `frontend` | 8080 | React UI (Nginx) — **start here** |
| `backend` | 8000 | FastAPI API (`/docs` for OpenAPI) |
| `worker` ×2 | — | RQ workers running the indexing pipeline |
| `postgres` | — | Relational database |
| `redis` | — | Job queue backend |
| `qdrant` | — | Vector store |
| `phoenix` | 6006 | Tracing UI |
| `github-mcp` | — | GitHub MCP server (used when indexing GitHub repos) |

The backend container runs the database initialisation script
(`scripts/init_db.py`) automatically on startup before serving traffic, so the
schema is always present.

To stop everything: `docker compose down`. Add `-v` to also delete the data
volumes (Postgres, Redis, Qdrant) for a completely clean slate.

---

## The zero-credential path

CodeBase Brain is built so that **an empty `.env` still produces a fully working
system**. When no AI credentials are present it transparently substitutes:

- a **local LLM provider** that returns clearly-labelled placeholder text;
- a **local embedding provider** that produces deterministic hash-based
  vectors;
- a **local reranker** that scores by term overlap;
- a **local repository source** that reads a directory mounted into the
  container (the bundled `sample-repo/`).

This path is for demos, CI, and exploring the architecture — not for real
documentation quality. Everything below switches on the real providers.

---

## Configuration reference

All configuration is via environment variables (read from `.env`). Every
variable has a working default; the table groups them by concern.

### Datastores

| Variable | Default | Notes |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `codebrain` | PostgreSQL credentials. |

Redis and Qdrant need no configuration in the default Compose setup — they are
reached by service name.

### Security

| Variable | Default | Notes |
|---|---|---|
| `JWT_SECRET_KEY` | `change-me-in-production` | **Must be changed** for any real deployment — it signs access tokens. |
| `CORS_ORIGINS` | `http://localhost:8080` | Comma-separated list of browser origins allowed to call the API. |
| `GITHUB_WEBHOOK_SECRET` | `local-webhook-secret` | Shared secret GitHub uses to sign webhook payloads. |

### AI providers — Vertex AI

One Google Cloud service account covers **both** the LLM and embeddings.

| Variable | Default | Notes |
|---|---|---|
| `GCP_PROJECT_ID` | _(empty)_ | Set to your GCP project to enable Vertex AI. |
| `GCP_LOCATION` | `us-central1` | Vertex AI region. |
| `GOOGLE_API_KEY` | _(empty)_ | Alternative to Vertex: a Gemini API key. |
| `GCP_CREDENTIALS_DIR` | `./secrets` | Host directory mounted to `/secrets`; place the service-account JSON as `gcp-sa.json` inside it. |
| `LLM_MODEL` | `gemini-2.0-flash` | Any Gemini model id. The spec's `gemini-1.5-pro` is deprecated, hence the current default. |
| `EMBEDDING_MODEL` | `text-embedding-004` | Embedding model id (768-dimensional). |

**To enable real Vertex AI:**

1. Create a GCP service account with the *Vertex AI User* role.
2. Download its JSON key and save it as `./secrets/gcp-sa.json`.
3. Set `GCP_PROJECT_ID` in `.env`.
4. `docker compose up --build`.

### AI providers — Cohere

| Variable | Default | Notes |
|---|---|---|
| `COHERE_API_KEY` | _(empty)_ | Enables the real Cohere rerank stage. Blank → local reranker. |

### Repository source

CodeBase Brain can index either a **local directory** or a **real GitHub
repository** (through the official GitHub MCP server).

| Variable | Default | Notes |
|---|---|---|
| `REPO_SOURCE` | `local` | `local` or `github_mcp`. |
| `LOCAL_REPO_MOUNT` | `./sample-repo` | Host directory mounted to `/repo` (used when `REPO_SOURCE=local`). |
| `GITHUB_REPO` | _(empty)_ | `owner/name` slug to index (used when `REPO_SOURCE=github_mcp`). |
| `GITHUB_TOKEN` | _(empty)_ | GitHub personal access token with repo read scope. |
| `GITHUB_MCP_TRANSPORT` | `stdio` | `stdio` (default Compose setup) or `http`. |

**To index your own local project:** point `LOCAL_REPO_MOUNT` at it, e.g.
`LOCAL_REPO_MOUNT=/home/me/my-project`, then in the UI start indexing with the
repository path `/repo`.

**To index a GitHub repository:** set `REPO_SOURCE=github_mcp`,
`GITHUB_REPO=owner/name`, and `GITHUB_TOKEN`, then in the UI start indexing with
`owner/name`.

### Observability

| Variable | Default | Notes |
|---|---|---|
| `TRACING_ENABLED` | `true` | When true, traces are exported to Phoenix. |

Phoenix is always started as its own container; open it at
`http://localhost:6006` to inspect traces.

### Pipeline tuning (advanced)

These have sensible defaults and rarely need changing: `RETRIEVAL_TOP_K` (15
candidates from Qdrant), `RERANK_TOP_K` (5 kept after rerank),
`EMBEDDING_BATCH_SIZE` (250), `LLM_RATE_LIMIT_RPM` (60),
`CRITICAL_FUNCTION_THRESHOLD` (5 — functions with this many callers get
richer doc-generation context).

---

## GitHub webhook (keeping docs in sync)

To have documentation regenerate automatically when pull requests merge, add a
webhook to your GitHub repository:

- **Payload URL**: `https://<your-host>/api/webhook`
- **Content type**: `application/json`
- **Secret**: the same value as `GITHUB_WEBHOOK_SECRET`
- **Events**: *Pull requests*

On a merged PR the backend verifies the signature, computes the impacted docs,
and queues an impact-based regeneration job.

---

## Troubleshooting

- **The UI loads but every request fails** — the backend may still be
  initialising the database. Watch `docker compose logs backend`.
- **Indexing stays queued forever** — check the workers are healthy:
  `docker compose logs worker`.
- **Answers look like placeholders** — no real LLM credentials are configured;
  see the Vertex AI section above.
- **A container can't reach the internet** — corporate networks may block
  image pulls; ensure Docker can reach the relevant registries.
