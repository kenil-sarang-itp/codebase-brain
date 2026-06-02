"""
Central application configuration.

All runtime configuration is loaded from environment variables (or a `.env`
file) into a single, validated `Settings` object. Nothing else in the codebase
reads `os.environ` directly — this keeps configuration in one place (SOLID:
Single Responsibility) and makes the app trivially testable by injecting a
different Settings instance.

The settings object is cached via `get_settings()` so it is parsed exactly once
per process.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed, validated application settings.

    Every field has a sensible default so the application can boot in "local
    fallback" mode with **zero** external credentials. Supplying real API keys
    automatically upgrades the relevant providers (see `app.external`).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----------------------------------------------------------------- app --
    app_name: str = "CodeBase Brain"
    environment: Literal["local", "development", "production"] = "local"
    debug: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Comma-separated list of allowed CORS origins for the React frontend.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # ------------------------------------------------------------ security --
    # SECURITY: override `jwt_secret_key` in every real deployment.
    jwt_secret_key: str = "change-me-in-production-this-is-not-secure"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 hours
    # Secret used to verify GitHub webhook HMAC signatures.
    github_webhook_secret: str = "local-webhook-secret"

    # ------------------------------------------------------------ database --
    postgres_user: str = "codebase"
    postgres_password: str = "codebase"
    postgres_db: str = "codebase_brain"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # --------------------------------------------------------------- redis --
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # -------------------------------------------------------------- qdrant --
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "codebase_knowledge"
    embedding_dim: int = 768

    # ------------------------------------------------------- llm / embeddings
    # When these keys are empty the app uses deterministic local fallbacks so
    # the whole system runs without any cloud account. See app/external/*.
    google_api_key: str = ""
    gcp_project_id: str = ""
    gcp_location: str = "us-central1"
    # Model identifiers are fully configurable — the spec's "gemini-1.5-pro"
    # is deprecated, so we default to a current model but allow any override.
    llm_model: str = "gemini-2.0-flash"
    embedding_model: str = "text-embedding-004"
    cohere_api_key: str = ""
    cohere_rerank_model: str = "rerank-english-v3.0"

    # -------------------------------------------------------------- github --
    # Personal access token used by the GitHub MCP server to read repos/PRs.
    github_token: str = ""
    # Which repository source backs indexing: "local" (filesystem, zero-setup
    # demo/test path) or "github_mcp" (real GitHub MCP server, production).
    repo_source: str = "local"
    # Root directory used when repo_source == "local".
    local_repo_path: str = "/repo"
    # Default "owner/name" slug used when repo_source == "github_mcp".
    github_repo: str = ""
    # GitHub MCP server connection. The official github-mcp-server can run as a
    # container exposing a streamable-HTTP endpoint, or be launched over stdio.
    # "http" uses github_mcp_url; "stdio" launches github_mcp_command.
    github_mcp_transport: str = "http"
    github_mcp_url: str = "http://github-mcp:8080/mcp"
    github_mcp_command: str = "github-mcp-server stdio"

    # --------------------------------------------------------- observability
    # Phoenix runs as its own Docker container; this is its OTLP collector URL.
    phoenix_collector_endpoint: str = "http://localhost:6006"
    tracing_enabled: bool = True
    # Logging verbosity for every process (API + workers). One of
    # DEBUG / INFO / WARNING / ERROR.
    log_level: str = "INFO"

    # ------------------------------------------------------------- pipeline --
    # Rate limit for LLM calls during indexing (requests per minute).
    llm_rate_limit_rpm: int = 60
    embedding_batch_size: int = 250
    # Delay between every 5 file fetches to avoid GitHub API rate limits.
    github_fetch_delay_seconds: float = 1.0
    retrieval_top_k: int = 20  # candidates pulled from Qdrant before rerank
    rerank_top_k: int = 10  # results kept after Cohere rerank and given to LLM
    # Functions called by more than this many others get richer LLM context.
    critical_function_threshold: int = 5

    # ----------------------------------------------------------- validators --
    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, v: str) -> str:
        """Normalise the CORS origins string (defensive against stray spaces)."""
        return ",".join(part.strip() for part in v.split(",") if part.strip())

    # ------------------------------------------------------ derived helpers --
    @property
    def database_url(self) -> str:
        """Async SQLAlchemy connection string (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """Synchronous connection string — used by RQ workers and migrations."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Redis connection string used by RQ for the job queue."""
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list, ready for FastAPI's CORSMiddleware."""
        return [o for o in self.cors_origins.split(",") if o]

    @property
    def llm_provider_is_real(self) -> bool:
        """True when a real Gemini credential is configured."""
        return bool(self.google_api_key or self.gcp_project_id)

    @property
    def reranker_is_real(self) -> bool:
        """True when a real Cohere credential is configured."""
        return bool(self.cohere_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached Settings instance.

    Using an LRU cache guarantees the `.env` file is parsed only once and that
    every module sees the exact same configuration object.
    """
    return Settings()
