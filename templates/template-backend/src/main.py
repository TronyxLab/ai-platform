# GREP_SUMMARY: main.py health ready metrics fastapi prometheus config uvicorn
# STRUCTURE: FastAPI app → /health(200) + /ready(200) + /metrics(prometheus) → uvicorn server(0.0.0.0:8000)
# region MODULE_CONTRACT
## @purpose  Backend service entrypoint: health/ready/metrics endpoints (DevPlan 141 B1/B2)
## @scope    Python FastAPI application (template-backend)
## @invariants
##   - /health returns 200 (Docker healthcheck target)
##   - /ready returns 200 (pre-stop readiness probe)
##   - /metrics returns Prometheus-формат (prometheus-client обязателен, контракт monitoring)
##   - Server listens on 0.0.0.0:8000 (metrics_port=8000 в ai-platform.yaml)
## @rationale Health and readiness endpoints are mandatory for zero-downtime deploys (06 §7);
##            /metrics — честный Prometheus-формат (не JSON-заглушка) — платформенный
##            мониторинг-контракт (monitoring.metrics=true, TRAP[RISK-5] DevPlan 141)
# endregion MODULE_CONTRACT

import logging
import os
import sys

try:
    import uvicorn
    from fastapi import FastAPI, Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

# Конфигурация через pydantic-settings (src/config.py) — PLATFORM_* из .env.platform
try:
    from config import settings
except ImportError:
    settings = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("{{PROJECT_NAME}}")

app = FastAPI(title="{{PROJECT_NAME}}", version="0.1.0") if FASTAPI_AVAILABLE else None


# v1.0.1 (TRAP[BUG] Фаза 3): роуты — на УРОВНЕ МОДУЛЯ (идиома FastAPI): dev-compose
# запускает `uvicorn main:app` (main() НЕ вызывается) — роуты внутри main() давали
# 404 /health на dev-стеке. Производственный CMD `python3 main.py` вызывает main()
# — оба пути теперь регистрируют одни и те же роуты.
if app is not None:

    @app.get("/health")
    def health() -> dict[str, str]:
        """Docker HEALTHCHECK target — must return 200."""
        return {"status": "OK", "service": "{{PROJECT_NAME}}"}

    @app.get("/ready")
    def ready() -> dict[str, str]:
        """Readiness probe — pre-stop check."""
        return {"status": "READY"}

    @app.get("/metrics")
    def metrics() -> Response:
        """Prometheus metrics endpoint (metrics: true в ai-platform.yaml).

        Кастомные метрики — см. snippets/metrics_prometheus.py.
        """
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"message": "{{PROJECT_NAME}} is running"}


def main():
    """Entry point: start HTTP server with health endpoints."""
    logger.info("[IMP:8][{{PROJECT_NAME}}][main] Starting {{PROJECT_NAME}} service")

    if not FASTAPI_AVAILABLE or app is None:
        logger.error("[IMP:10][{{PROJECT_NAME}}][main] FastAPI not installed; install: pip install fastapi uvicorn")
        sys.exit(1)

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
