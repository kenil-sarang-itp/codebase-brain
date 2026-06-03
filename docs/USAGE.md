# Usage & API Reference

How developers actually use CodeBase Brain, followed by the full HTTP API.

---

## Using the app

The UI has three screens, reached from the top navigation once you sign in.

### 1. Sign in

On first visit you land on the login screen. Toggle to **register**, create an
account (username, email, password), and you are signed straight in. Sessions
survive a page refresh; **Log out** is in the top-right.

### 2. Index a repository — the "Index" screen

Before you can ask anything, a repository must be indexed.

1. Enter a **repository**:
   - `/repo` — the directory mounted into the container (the bundled
     `sample-repo` by default, or your own project via `LOCAL_REPO_MOUNT`).
   - `owner/name` — a GitHub repository (requires `REPO_SOURCE=github_mcp` and a
     token; see [SETUP.md](SETUP.md)).
2. Optionally set a **git ref** (GitHub only; defaults to `main`).
3. Click **Start indexing**.

A live dashboard then shows the six pipeline phases, a progress bar, and
file/function counters, polled every couple of seconds. Indexing runs in the
background — you can navigate away and come back.

### 3. Ask questions — the "Ask" screen

Type a question in plain English and press Enter. CodeBase Brain retrieves the
most relevant documentation, synthesises an answer, and shows the **sources** it
cited (file and line range). The conversation has memory — follow-up questions
understand the context of earlier ones.

Good questions to try:

- *"How does authentication work?"*
- *"Where are GitHub webhook signatures verified?"*
- *"What does the indexing pipeline do in phase three?"*

**Validating a test:** describe what a test expects, using a cue like
*"validate"*, *"verify the test"*, or *"does the code implement…"*. CodeBase
Brain parses the expected steps and checks each against the real call graph,
returning a pass/fail breakdown. Example:

> *"Validate this test: it expects that submitting a merged pull request
> triggers documentation regeneration."*

Each answer is tagged with its type — **knowledge** or **validation**.

### 4. Review history — the "History" screen

A table of every question you have asked: the question, how it was classified,
how many sources were cited, the answer latency, and when it happened.

---

## Observability

Open Phoenix at `http://localhost:6006` to see traces for every request and
indexing run — LLM calls, retrievals, and each pipeline phase as nested spans.

---

## HTTP API reference

The API is served under `/api`. Interactive OpenAPI docs are at
`http://localhost:8000/docs`.

Authentication is **JWT bearer**: obtain a token from `/auth/login` (or
`/auth/register`) and send it as `Authorization: Bearer <token>` on every
protected endpoint.

### Authentication

#### `POST /api/auth/register`
Create an account. Returns an access token (registration also logs you in).
```json
Request:  { "username": "dev", "email": "dev@example.com", "password": "secret123" }
Response: { "access_token": "...", "token_type": "bearer", "username": "dev", "user_id": "..." }
```

#### `POST /api/auth/login`
Exchange credentials for an access token.
```json
Request:  { "username": "dev", "password": "secret123" }
Response: { "access_token": "...", "token_type": "bearer", "username": "dev", "user_id": "..." }
```

#### `POST /api/auth/logout`
Requires auth. Confirms logout; the client then discards its token. (Tokens are
stateless JWTs, so logout is client-side disposal.)

#### `GET /api/auth/me`
Requires auth. Returns the current user's public profile.

### Chat

#### `POST /api/chat`
Requires auth. Ask a question about the indexed codebase.
```json
Request:  { "question": "How does auth work?", "session_id": null }
Response: {
  "answer": "...",
  "query_type": "knowledge",
  "citations": ["auth_service.py::authenticate (L61-L88)"],
  "session_id": "sess-...",
  "latency_ms": 2840
}
```
Pass the returned `session_id` on subsequent requests to continue the same
conversation (this is what gives the assistant short-term memory). Omit it (or
send `null`) to start a fresh session.

### Indexing

#### `POST /api/index`
Requires auth. Queue a repository for indexing. Returns immediately.
```json
Request:  { "repo": "/repo", "ref": "main" }
Response: { "session_id": "...", "status": "queued", "message": "..." }
```

#### `GET /api/index-status/{session_id}`
Requires auth. Poll an indexing session's live progress.
```json
Response: {
  "session_id": "...", "status": "generating_l1",
  "total_files": 42, "processed_files": 42,
  "total_functions": 310, "processed_functions": 188,
  "progress_percent": 65.2, "job_counts": { "finished": 1 },
  "error_message": null
}
```
`status` progresses: `queued` → `discovering` → `analysing` →
`generating_l3` → `generating_l2` → `generating_l1` → `complete`
(or `failed`).

### Webhook

#### `POST /api/webhook`
Called by GitHub, not by clients. Verifies the `X-Hub-Signature-256` HMAC; on a
merged pull request, queues impact-based documentation regeneration. See
[SETUP.md](SETUP.md) for webhook configuration.

### Logs & health

#### `GET /api/query-logs?limit=50`
Requires auth. Returns the current developer's recent query log.

#### `GET /api/health`
No auth. Liveness/readiness probe — reports database and Qdrant connectivity
and which AI providers are active. Used by Docker health checks.

---

## Error format

Every error returns a consistent JSON envelope with the right HTTP status:

```json
{
  "error": "NotFoundError",
  "detail": "Human-readable explanation.",
  "trace_id": "req-..."
}
```

The `trace_id` correlates the response with the server logs and Phoenix traces
for that request.
