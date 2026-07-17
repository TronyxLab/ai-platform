# GREP_SUMMARY: main.py health ready fastapi bot-stub uvicorn
# STRUCTURE: FastAPI app → /health(200) + /ready(200) → uvicorn server(0.0.0.0:8000)
# region MODULE_CONTRACT
## @purpose  Backend service entrypoint: health/ready endpoints + bot stub (04-templates §3)
## @scope    Python FastAPI application
## @invariants
##   - /health returns 200 (Docker healthcheck target)
##   - /ready returns 200 (pre-stop readiness probe)
##   - /metrics returns 200 (Prometheus metrics endpoint)
##   - Server listens on 0.0.0.0:8000
## @rationale Health and readiness endpoints are mandatory for zero-downtime deploys (06 §7)
# endregion MODULE_CONTRACT

import logging
import os
import sys

try:
    import uvicorn
    from fastapi import FastAPI

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("__PROJECT_NAME__")

app = FastAPI(title="__PROJECT_NAME__", version="0.1.0") if FASTAPI_AVAILABLE else None


def main():
    """Entry point: start HTTP server with health endpoints."""
    logger.info("[IMP:8][__PROJECT_NAME__][main] Starting __PROJECT_NAME__ service")

    if not FASTAPI_AVAILABLE:
        logger.error("[IMP:10][__PROJECT_NAME__][main] FastAPI not installed; install: pip install fastapi uvicorn")
        sys.exit(1)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Docker HEALTHCHECK target — must return 200."""
        return {"status": "OK", "service": "__PROJECT_NAME__"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        """Readiness probe — pre-stop check."""
        return {"status": "READY"}

    @app.get("/metrics")
    async def metrics() -> dict[str, str]:
        """Prometheus metrics endpoint — required when metrics: true in ai-platform.yaml."""
        return {"status": "OK", "metrics": "exposed"}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "__PROJECT_NAME__ is running"}

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
