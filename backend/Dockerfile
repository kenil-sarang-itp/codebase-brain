# CodeBase Brain — backend image.
# A single image serves both roles: the FastAPI API and the RQ workers.
# Docker Compose runs it with different commands for each service.

FROM python:3.12-slim

# --- System packages -----------------------------------------------------
# build-essential + git: some wheels (tree-sitter grammars) compile from source.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git curl \
    && rm -rf /var/lib/apt/lists/*

# --- Python environment --------------------------------------------------
# Don't write .pyc files; flush stdout/stderr immediately for clean logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# --- Dependencies (cached layer) ----------------------------------------
# Copy only requirements first so the pip layer is cached unless deps change.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# --- Application code ----------------------------------------------------
COPY backend/app ./app
COPY scripts ./scripts

# --- Runtime -------------------------------------------------------------
EXPOSE 8000

# Default command runs the API. The worker service overrides this in
# docker-compose.yml with: python -m app.workers.rq_worker
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
