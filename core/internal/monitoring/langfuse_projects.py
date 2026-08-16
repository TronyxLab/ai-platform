#!/usr/bin/env python3
# GREP_SUMMARY: monitoring langfuse-projects HTTP-POST bearer-token project-creation 409-idempotent needs-llm
# STRUCTURE: ▶ create_langfuse_project(config) → ◇ needs_llm? → ◇ LANGFUSE_SECRET_KEY? → ⊕ POST /api/public/projects → ◇ 200/201|409 → ⎋ RenderResult
# region MODULE_CONTRACT
## @purpose  Langfuse project creation via HTTP API — extracted from monitoring_config_renderer.py
##           (DevPlan 117 G T54).
## @scope    Consumed by monitoring_config_renderer.main() (lazy import). Non-fatal.
## @invariants
##   - Skips if needs_llm is False (status="noop")
##   - HTTP-слой — shared/http_client (urllib stdlib, post_json-хелпер) — no requests dependency
##   - LANGFUSE_SECRET_KEY read from environment (missing → status="failed")
##   - HTTP 409 / "already exists" → status="skipped" (idempotent)
##   - Non-fatal: HTTP/network errors logged, continue
## @rationale  DevPlan 117 G T54 — extracted verbatim (create_langfuse_project, ~66 LOC).
##            DevPlan 177 W3.2 — HTTP-слой консолидирован в shared/http_client.py (post_json).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
## @changes  2026-08-16 · DevPlan 177 W3.2 — миграция на shared/http_client.post_json
# endregion MODULE_CONTRACT

import http.client
import logging
import os
import sys
import urllib.error
from pathlib import Path

# Dual-import pattern (monitoring_config_renderer L43-52): core.* path under pytest/python3 -m,
# parent-dir bootstrap for direct-script invocation.
try:
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport] — dual-import канон (monitoring_config_renderer L43-52); цикл статический — runtime lazy
        ProjectMonitoringConfig,
        RenderResult,
    )
    from monitoring.constants import (  # pyright: ignore[reportImplicitRelativeImport] — dual-import канон
        LANGFUSE_API_URL,
    )
except ImportError:  # pragma: no cover — direct-script invocation path
    _INTERNAL_DIR = str(Path(__file__).resolve().parent.parent)
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    # W2 T2.6 (DevPlan 136, латентный класс A): канон config_renderer.py — корень репо
    # (fallback добавляет И core/internal/ для top-level monitoring-импортов, И корень
    # для core.internal.* — единый документированный канон self-bootstrap).
    _PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport] — dual-import канон
        ProjectMonitoringConfig,
        RenderResult,
    )
    from monitoring.constants import (  # pyright: ignore[reportImplicitRelativeImport] — dual-import канон
        LANGFUSE_API_URL,
    )


# W1-A1 (план 170): timeout=10 литерал (Langfuse API) → канон SoT DOCKER_CMD_TIMEOUT
# (10) — короткий HTTP-подвызов (AMBER-зачистка research-D §D1).
from core.internal.shared import http_client  # W3.2 (177): HTTP-слой консолидирован в shared/http_client.py
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC_create_langfuse_project
HTTP_CONFLICT: int = 409  # Langfuse: проект уже существует (skip)


def create_langfuse_project(
    config: ProjectMonitoringConfig,
) -> RenderResult:
    """Create Langfuse project via HTTP API.

    ## @purpose  POST to Langfuse API to create a project for LLM monitoring.
    ##           Skips if needs_llm is False.
    ## @io
    ##   ⇥ config: ProjectMonitoringConfig — resolved monitoring config
    ##   ⎋ RenderResult — outcome: "created", "skipped" (exists or no LLM), "failed"
    ## @complexity O(1) HTTP call
    ## @invariants
    ##   - Skips if needs_llm is False (status="noop")
    ##   - HTTP-слой — shared/http_client (urllib stdlib, post_json-хелпер) — no requests dependency
    ##   - LANGFUSE_SECRET_KEY read from environment (missing → status="failed")
    ##   - HTTP 409 / "already exists" → status="skipped" (idempotent)
    ##   - Non-fatal: HTTP/network errors logged, continue
    """
    if not config.needs_llm:
        logger.info("[IMP:8][langfuse] No LLM needs declared — skipping Langfuse project")
        return RenderResult(component="langfuse", status="noop", detail="needs_llm=False")

    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not secret_key:
        logger.info("[IMP:6][langfuse] LANGFUSE_SECRET_KEY not set — skipping Langfuse project creation")
        return RenderResult(component="langfuse", status="failed", detail="LANGFUSE_SECRET_KEY not set")

    try:
        # W3.2 (177): POST-JSON-хелпер shared/http_client (сериализация тела + Content-Type
        # в одном месте); HTTPError/URLError пробрасываются как есть (инвариант 2 http_client).
        resp = http_client.post_json(
            LANGFUSE_API_URL,
            {"name": config.project_name, "retention": config.ai_retention_days},
            timeout=DOCKER_CMD_TIMEOUT,
            headers={"Authorization": f"Bearer {secret_key}"},
        )
        with resp:
            return _handle_langfuse_response(resp, config)
    except urllib.error.HTTPError as e:
        if e.code == HTTP_CONFLICT:
            logger.info("[IMP:8][langfuse] Langfuse project '%s' already exists — skipping", config.project_name)
            return RenderResult(component="langfuse", status="skipped", detail="HTTP 409 already exists")
        logger.info("[IMP:6][langfuse] Langfuse HTTP error %s for %s: %s", e.code, config.project_name, e)
        return RenderResult(component="langfuse", status="failed", detail=f"HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        logger.info("[IMP:6][langfuse] Langfuse network error for %s: %s", config.project_name, e)
        return RenderResult(component="langfuse", status="failed", detail=str(e))


def _handle_langfuse_response(resp: http.client.HTTPResponse, config: ProjectMonitoringConfig) -> RenderResult:
    """Обработка HTTP-ответа Langfuse API (извлечено для TRY-лимита, W11).

    ## @purpose  200/201 → created; иной статус → failed (не-фатально, лог IMP:6).
    ## @io       ⇥ resp: HTTPResponse, config → ⎋ RenderResult
    ## @complexity O(1)
    """
    status_code = resp.status
    if status_code in {200, 201}:
        logger.info("[IMP:9][langfuse] Langfuse project created: %s", config.project_name)
        return RenderResult(component="langfuse", status="created", detail=f"HTTP {status_code}")
    logger.info("[IMP:6][langfuse] Langfuse API returned HTTP %s for %s", status_code, config.project_name)
    return RenderResult(component="langfuse", status="failed", detail=f"HTTP {status_code}")


# endregion FUNC_create_langfuse_project
