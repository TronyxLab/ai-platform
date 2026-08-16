# GREP_SUMMARY: status-page collectors readiness readiness-check healthz metrics-file-stale probe
# STRUCTURE: ▶ ┌status_metrics_path + run_cmd DI┐ → ◇ isfile? → FAIL missing → ◇ json.parse? → FAIL unreadable
#            → ◇ staleness_fn(>5min)? → FAIL stale_data → ⎋ (True, PASS details)
# region MODULE_CONTRACT
## @purpose  Fast readiness probe for /healthz — extracted from app.py:_handle_healthz
##           (DevPlan 170 W7-E2). Verifies the metrics pipeline is functional:
##           status-metrics.json exists → readable → fresh (≤5 min). I/O collection here,
##           HTTP handler stays thin (call + JSON response).
## @scope    Consumed by app.py StatusPageHandler._handle_healthz (Docker HEALTHCHECK fast-path)
## @invariants
##   - Returns (ok: bool, details: dict) — details is the exact /healthz JSON payload
##   - reasons: metrics_file_missing | metrics_file_unreadable | stale_data (contract W10 T10.13)
##   - run_cmd — injectable I/O collector (default: file read); staleness_fn — injectable staleness
##   - ok=True ONLY for fresh + readable + existing metrics (200 PASS)
## @rationale  DevPlan 170 W7-E2 — I/O-сбор вынесен из HTTP-хендлера; run_cmd параметр —
##            DI-граница для тестов (compose/HTTP-пробы future-ready). Контракт /healthz
##            JSON-полей сохранён 1:1 (domain_verifier/HEALTHCHECK не зависят от структуры).
## @changes  2026-08-15 · DevPlan 170 W7-E2 — extracted from app.py:_handle_healthz (79 LOC → thin)
# endregion MODULE_CONTRACT

import json
import os
import pathlib
import sys
import time
from collections.abc import Callable
from typing import TypedDict, cast

from .config import MetricsData
from .staleness import compute_staleness


# region DATA_ReadinessResult
class ReadinessResult(TypedDict, total=False):
    """Результат readiness-пробы (точный /healthz JSON-пайлоад)."""

    status: str
    reason: str
    message: str | None
    staleness: str | None
    schema_version: int
    generated_at: str | None
    duration_ms: int


# endregion DATA_ReadinessResult

ReadMetricsFn = Callable[[str], tuple[MetricsData | None, str | None, str | None]]
StalenessFn = Callable[[str | None], str | None]


def _read_metrics_file(path: str) -> tuple[MetricsData | None, str | None, str | None]:
    """Read metrics JSON → (metrics, reason, message). reason=None = ok.

    # ▶ ┌path┐ → ◇ isfile? → (None, "metrics_file_missing", msg)
    #          → ◇ json.load ok? → (data, None, None)
    #          → ◇ OSError/JSONDecodeError → (None, "metrics_file_unreadable", msg)
    """
    if not os.path.isfile(path):
        return None, "metrics_file_missing", f"{path} not found or not a regular file"
    try:
        with pathlib.Path(path).open(encoding="utf-8") as f:
            return cast("MetricsData", json.load(f)), None, None  # W11: json → Any → MetricsData
    except (OSError, json.JSONDecodeError) as e:
        return None, "metrics_file_unreadable", f"Cannot read {path}: {e}"


# region FUNC_readiness_check
def readiness_check(
    status_metrics_path: str,
    run_cmd: ReadMetricsFn | None = None,
    staleness_fn: StalenessFn | None = None,
) -> tuple[bool, ReadinessResult]:
    """Fast readiness probe → (ok, details). details = exact /healthz JSON payload.

    # ▶ ┌status_metrics_path┐ → run_cmd (default file read) → ◇ reason? → (False, FAIL details)
    #                        → ◇ staleness_fn(generated_at)? → (False, FAIL stale_data)
    #                        → ⎋ (True, PASS details)

    Unlike /health (full system checks: vhosts, containers, platform services),
    this is a fast (~50ms) readiness probe that verifies the data pipeline
    is functional — status-page is useless without fresh metrics data.

    Used by: Docker HEALTHCHECK (docker-compose.base.yml + Dockerfile) via /healthz.
    W10 T10.13 (M-7): stale data → 503 FAIL (sync with /health) — stale pipeline
    means status-page is useless; Docker HEALTHCHECK sees unhealthy → restart/alert.
    """
    read = run_cmd if run_cmd is not None else _read_metrics_file
    stale_fn = staleness_fn if staleness_fn is not None else compute_staleness
    start = time.monotonic()

    metrics, reason, message = read(status_metrics_path)
    duration_ms = int((time.monotonic() - start) * 1000)
    if reason is not None:
        return False, {
            "status": "FAIL",
            "reason": reason,
            "message": message,
            "duration_ms": duration_ms,
        }

    generated_at = metrics.get("generated_at")
    staleness = stale_fn(generated_at)
    if staleness:
        return False, {
            "status": "FAIL",
            "reason": "stale_data",
            "staleness": staleness,
            "schema_version": cast("int", metrics.get("schema_version", 0)),
            "duration_ms": duration_ms,
        }

    print(f"[IMP:9][status-page][healthz] readiness PASS ({duration_ms}ms)", file=sys.stderr)
    return True, {
        "status": "PASS",
        "schema_version": cast("int", metrics.get("schema_version", 0)),
        "generated_at": generated_at,
        "duration_ms": duration_ms,
    }


# endregion FUNC_readiness_check
