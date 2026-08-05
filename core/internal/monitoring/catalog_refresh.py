#!/usr/bin/env python3
# GREP_SUMMARY: monitoring catalog-refresh generate-catalog subprocess non-fatal script-missing
# STRUCTURE: ▶ refresh_catalog(platform_root) → ◇ script exists? → ⊕ subprocess run → ⎋ RenderResult
# region MODULE_CONTRACT
## @purpose  Service catalog refresh — extracted from monitoring_config_renderer.py (DevPlan 117 G T54).
## @scope    Consumed by monitoring_config_renderer.main() (lazy import). Non-fatal.
## @invariants
##   - Script must be executable (check with is_file())
##   - Script not found → status="noop"
##   - Script failure → status="failed", logged at IMP:6
## @rationale  DevPlan 117 G T54 — extracted verbatim (refresh_catalog, ~34 LOC).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
# endregion MODULE_CONTRACT

import logging
import subprocess
import sys
from pathlib import Path

# Dual-import pattern (monitoring_config_renderer L43-52): core.* path under pytest/python3 -m,
# parent-dir bootstrap for direct-script invocation.
try:
    from monitoring_config_renderer import RenderResult

    from monitoring.constants import CATALOG_SCRIPT
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
    from monitoring_config_renderer import RenderResult

    from monitoring.constants import CATALOG_SCRIPT

logger = logging.getLogger(__name__)


# region FUNC_refresh_catalog
def refresh_catalog(platform_root: Path) -> RenderResult:
    """Invoke catalog generation script.

    ## @purpose  Run core/internal/catalog/generate-catalog.sh to refresh service catalog.
    ##           Non-fatal: script not found or failure → logged, continue.
    ## @io
    ##   ⇥ platform_root: Path — platform root for resolving catalog script
    ##   ⎋ RenderResult — outcome
    ## @complexity O(1) subprocess call
    ## @invariants
    ##   - Script must be executable (check with is_file())
    ##   - Script not found → status="noop"
    ##   - Script failure → status="failed", logged at IMP:6
    """
    script_path = platform_root / CATALOG_SCRIPT
    if not script_path.is_file():
        logger.info("[IMP:7][catalog] Catalog script not found: %s — skipping", script_path)
        return RenderResult(component="catalog", status="noop", detail=f"script not found: {script_path}")

    try:
        subprocess.run([str(script_path)], check=True, capture_output=True, text=True, timeout=60)
        logger.info("[IMP:8][catalog] Catalog refresh invoked")
        return RenderResult(component="catalog", status="created", detail="Catalog refreshed")
    except subprocess.CalledProcessError as e:
        logger.info("[IMP:6][catalog] Catalog generation failed (exit %s): %s", e.returncode, e.stderr.strip())
        return RenderResult(component="catalog", status="failed", detail=e.stderr.strip())
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.info("[IMP:6][catalog] Catalog generation error: %s", e)
        return RenderResult(component="catalog", status="failed", detail=str(e))


# endregion FUNC_refresh_catalog
