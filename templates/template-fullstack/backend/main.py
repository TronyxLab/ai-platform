# GREP_SUMMARY: main.py health ready fastapi fullstack uvicorn
# STRUCTURE: FastAPI app → /health(200) + /ready(200) → uvicorn server(0.0.0.0:8000)
# region MODULE_CONTRACT
## @purpose  Backend service entrypoint: health/ready endpoints (04-templates §3)
## @scope    Python FastAPI application for fullstack project
## @invariants
##   - /health returns 200 (Docker healthcheck target)
##   - /ready returns 200 (pre-stop readiness probe)
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
logger = logging.getLogger("{{PROJECT_NAME}}")

app = FastAPI(title="{{PROJECT_NAME}}", version="0.1.0") if FASTAPI_AVAILABLE else None


def main():
    """Entry point: start HTTP server with health endpoints."""
    logger.info("[IMP:8][{{PROJECT_NAME}}][main] Starting {{PROJECT_NAME}} service")

    if not FASTAPI_AVAILABLE:
        logger.error("[IMP:10][{{PROJECT_NAME}}][main] FastAPI not installed; install: pip install fastapi uvicorn")
        sys.exit(1)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Docker HEALTHCHECK target — must return 200."""
        return {"status": "OK", "service": "{{PROJECT_NAME}}"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        """Readiness probe — pre-stop check."""
        return {"status": "READY"}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "{{PROJECT_NAME}} is running"}

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
