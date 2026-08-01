#!/usr/bin/env python3
# GREP_SUMMARY: monitoring langfuse-projects HTTP-POST bearer-token project-creation 409-idempotent needs-llm
# STRUCTURE: ▶ create_langfuse_project(config) → ◇ needs_llm? → ◇ LANGFUSE_SECRET_KEY? → ⊕ POST /api/public/projects → ◇ 200/201|409 → ⎋ RenderResult
# region MODULE_CONTRACT
## @purpose  Langfuse project creation via HTTP API — extracted from monitoring_config_renderer.py
##           (DevPlan 117 G T54).
## @scope    Consumed by monitoring_config_renderer.main() (lazy import). Non-fatal.
## @invariants
##   - Skips if needs_llm is False (status="noop")
##   - Uses urllib.request (stdlib) — no requests dependency
##   - LANGFUSE_SECRET_KEY read from environment (missing → status="failed")
##   - HTTP 409 / "already exists" → status="skipped" (idempotent)
##   - Non-fatal: HTTP/network errors logged, continue
## @rationale  DevPlan 117 G T54 — extracted verbatim (create_langfuse_project, ~66 LOC).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
# endregion MODULE_CONTRACT

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Dual-import pattern (monitoring_config_renderer L43-52): core.* path under pytest/python3 -m,
# parent-dir bootstrap for direct-script invocation.
try:
    from monitoring_config_renderer import ProjectMonitoringConfig, RenderResult

    from monitoring.constants import LANGFUSE_API_URL
except ImportError:  # pragma: no cover — direct-script invocation path
    _INTERNAL_DIR = str(Path(__file__).resolve().parent.parent)
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    from monitoring_config_renderer import ProjectMonitoringConfig, RenderResult

    from monitoring.constants import LANGFUSE_API_URL

logger = logging.getLogger(__name__)


# region FUNC_create_langfuse_project
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
    ##   - Uses urllib.request (stdlib) — no requests dependency
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

    body = json.dumps(
        {
            "name": config.project_name,
            "retention": config.ai_retention_days,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        LANGFUSE_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — internal Langfuse API (localhost)
            status_code = resp.status
            if status_code in (200, 201):
                logger.info("[IMP:9][langfuse] Langfuse project created: %s", config.project_name)
                return RenderResult(component="langfuse", status="created", detail=f"HTTP {status_code}")
            logger.info("[IMP:6][langfuse] Langfuse API returned HTTP %s for %s", status_code, config.project_name)
            return RenderResult(component="langfuse", status="failed", detail=f"HTTP {status_code}")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            logger.info("[IMP:8][langfuse] Langfuse project '%s' already exists — skipping", config.project_name)
            return RenderResult(component="langfuse", status="skipped", detail="HTTP 409 already exists")
        logger.info("[IMP:6][langfuse] Langfuse HTTP error %s for %s: %s", e.code, config.project_name, e)
        return RenderResult(component="langfuse", status="failed", detail=f"HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        logger.info("[IMP:6][langfuse] Langfuse network error for %s: %s", config.project_name, e)
        return RenderResult(component="langfuse", status="failed", detail=str(e))


# endregion FUNC_create_langfuse_project
