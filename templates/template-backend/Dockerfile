# GREP_SUMMARY: multi-stage python slim healthcheck pip requirements
# STRUCTURE: python:3.12-slim(run stage) → pip install → healthcheck
# region DOCKERFILE
## @purpose  Multi-stage Docker build for Python backend services (04-templates §3)
## @scope    Backend projects (FastAPI / Telegram bots)
## @invariants
##   - Slim Python image for minimal size
##   - Requirements installed from requirements.txt
##   - HEALTHCHECK on /health endpoint
##   - /ready endpoint for pre-stop readiness probe
## @rationale Healthcheck enables zero-downtime atomic deploys (06 §7)
# endregion DOCKERFILE

FROM python:3.12.13-slim

WORKDIR /app

# Install system deps (BuildKit cache mount for apt)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (BuildKit cache mount for pip)
COPY src/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ .

# Non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["python3", "main.py"]
