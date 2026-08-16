# GREP_SUMMARY: status-page collectors aggregate get-all-checks fan-out overall vhosts containers platform-services
# STRUCTURE: ▶ get_all_checks ┌node_yaml+metrics+services┐ → load → containers (seq) → _fan_out_checks (vhosts+platform, пул)
#            → _compute_overall (DISABLED excluded) → ⎋ aggregate dict
# region MODULE_CONTRACT
## @purpose  Aggregate check orchestration — extracted from collectors.py get_all_checks
##           (91 LOC, CC13, DevPlan 170 W7-E2). Public entry point with unchanged signature;
##           fan-out (parallel pool) and overall summary split into fan_out_checks/compute_overall (приватные алиасы фасада).
## @scope    Consumed by app.py (thin wrapper with DI defaults)
## @invariants
##   - Total check timeout ≤30s (per-check timeout ≤5s)
##   - Anti-recursion: status-page container excluded (via _check_container → None)
##   - DISABLED excluded from overall (S-WARN A — WARN stays strict, DevPlan 158 W1 T1.2)
##   - fan_out_checks: best-effort — любой сбой воркера → FAIL-чек, общий статус не роняет
## @rationale  DevPlan 170 W7-E2 — get_all_checks декомпозирован: fan_out_checks (пул/последовательный
##            сбор) + compute_overall (сводка); сигнатура и поведение сохранены 1:1 (AC-G7).
## @changes  2026-08-15 · DevPlan 170 W7-E2 — extracted from collectors.py (CC13 → 2 helpers)
# endregion MODULE_CONTRACT

import functools
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypedDict

from .checks.containers import check_container as _check_container
from .checks.http import CheckResult
from .checks.http import curl_vhost as _curl_vhost
from .checks.platform import PlatformService
from .checks.platform import check_platform_service as _check_platform_service
from .config import MetricsData, get_vhosts, load_node_yaml, load_status_metrics
from .staleness import compute_staleness


# region DATA_CheckContext
class CheckContext(TypedDict):
    """Fallback-контекст задачи чека (target/type) для FAIL-ветки fan_out_checks."""

    target: str
    type: str


# endregion DATA_CheckContext


# region DATA_OverallData
class OverallData(TypedDict, total=False):
    """Агрегатный результат get_all_checks (граница /health + /status.json + render_html)."""

    status: str
    generated_at: str
    duration_ms: int
    metrics_freshness: str | None
    staleness: str | None
    checks: list[CheckResult]
    metrics: MetricsData


# endregion DATA_OverallData


# region FUNC_fan_out_checks
def fan_out_checks(
    tasks: list[tuple[Callable[[], CheckResult], CheckContext]],
    total_timeout: int,
) -> list[CheckResult]:
    """Parallel fan-out of check tasks (best-effort). Returns collected check results.

    # ▶ ┌tasks[(fn, fallback_ctx)]┐ → ThreadPoolExecutor(≤10 workers) → as_completed
    #    → ◇ future.result ok? → append result → ◇ exception/timeout → append FAIL-чек
    #    → ⎋ results list

    tasks: (zero-arg callable, fallback context {target, type}). Future exception →
    FAIL check with "future timeout: {e}" error (same format as pre-refactor get_all_checks).
    """
    if not tasks:
        return []
    results: list[CheckResult] = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 10)) as executor:
        futures = {executor.submit(fn): ctx for fn, ctx in tasks}
        for future in as_completed(futures):
            try:
                results.append(future.result(timeout=total_timeout))
            except Exception as e:  # ruff: ignore[PERF203, BLE001] — thread-pool future.result: любой сбой воркера → FAIL-чек, общий статус не роняет (best-effort)  # noqa: EXC
                ctx = futures[future]
                results.append({
                    "target": ctx["target"],
                    "type": ctx["type"],
                    "status": "FAIL",
                    "http_code": 0,
                    "duration_ms": 0,
                    "error": f"future timeout: {e}",
                })
    return results


# endregion FUNC_fan_out_checks


# region FUNC_compute_overall
def compute_overall(
    checks: list[CheckResult],
    freshness: str | None,
    duration_ms: int,
    metrics: MetricsData,
    generated_at: str,
) -> OverallData:
    """Compute aggregate status from collected checks (summary phase).

    # ▶ ┌checks┐ → ◇ filter DISABLED (S-WARN A) → ◇ all PASS? → overall PASS|FAIL
    #    → ⊕ staleness (WARN-indicator) → ⎋ aggregate dict

    # ⚠️ TRAP[DECISION] · 2026-08-12 · HI · DISABLED не валит overall (DevPlan 158 W1 T1.2)
    # · Rejected: DISABLED учитывается в overall (риск: asiteam=FAIL при здоровой ноде без Grafana)
    # · Reason: сервис not-deployed — это конфигурация, не сбой. Оператор сам решил не подключать.
    # · S-WARN A (2026-08-12): WARN ОСТАЁТСЯ строгим — PASS требует все АКТИВНЫЕ = PASS,
    # ·   любой WARN (деградация: контейнер running+not-healthy, HTTP ≥400) → overall FAIL.
    # ·   Исключается ТОЛЬКО DISABLED. Контракт /health не меняется.
    # · Rev: если появится сценарий где DISABLED должен влиять на overall — пересмотреть.
    """
    active_checks = [c for c in checks if c.get("status") != "DISABLED"]
    all_pass = all(c.get("status") == "PASS" for c in active_checks) if active_checks else True

    # Stale data → still WARN indicator, not FAIL (data exists but may be old)
    staleness = compute_staleness(freshness)
    overall = "PASS" if all_pass else "FAIL"

    return {
        "status": overall,
        "generated_at": generated_at,
        "duration_ms": duration_ms,
        "metrics_freshness": freshness,
        "staleness": staleness,
        "checks": checks,
        # Full metrics data for HTML template
        "metrics": metrics,
    }


# endregion FUNC_compute_overall


# region FUNC_get_all_checks
def get_all_checks(
    node_yaml_path: str,
    status_metrics_json: str,
    platform_services: list[PlatformService],
    per_check_timeout: int = 5,
    total_timeout: int = 30,
) -> OverallData:
    """Run all checks (vhosts + containers from metrics) with parallel fan-out. Returns aggregate dict."""
    start = time.monotonic()
    checks: list[CheckResult] = []

    node_data = load_node_yaml(node_yaml_path)
    metrics = load_status_metrics(status_metrics_json)
    freshness = metrics.get("generated_at")

    # ── Container checks (from status-metrics.json containers; sequential — in-memory, fast) ──
    containers = metrics.get("containers", [])
    for c in containers:
        result = _check_container(c)
        if result is not None:
            checks.append(result)

    # ── Vhost checks (live curl, parallel fan-out) ──
    vhosts = get_vhosts(node_data)
    vhost_tasks: list[tuple[Callable[[], CheckResult], CheckContext]] = [
        (functools.partial(_curl_vhost, v["domain"], per_check_timeout), {"target": v["domain"], "type": "vhost"})
        for v in vhosts
    ]
    checks += fan_out_checks(vhost_tasks, total_timeout)

    # ── Platform service checks (DNS probe → DISABLED if unresolved, else curl; parallel fan-out) ──
    svc_tasks: list[tuple[Callable[[], CheckResult], CheckContext]] = [
        (
            functools.partial(
                _check_platform_service, svc.get("internal", ""), svc.get("health_path", ""), per_check_timeout
            ),
            {"target": (svc.get("internal") or "").split(":")[0], "type": "platform_service"},
        )
        for svc in platform_services
    ]
    checks += fan_out_checks(svc_tasks, total_timeout)

    # ── Compute aggregate status ──
    duration_ms = int((time.monotonic() - start) * 1000)
    return compute_overall(
        checks,
        freshness,
        duration_ms,
        metrics,
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


# endregion FUNC_get_all_checks
