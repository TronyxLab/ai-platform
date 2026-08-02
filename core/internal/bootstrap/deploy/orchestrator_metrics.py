#!/usr/bin/env python3
# GREP_SUMMARY: orchestrator-metrics, severity, exit-code, status-metrics-json, hc-marker, llm-summary, pure-functions, E6, deploy-orchestrator-decomposition
# STRUCTURE: ▶ aggregate_severity ┌failed + severity_map┐ → ⊕ count crit/warn → ⎋ (crit, warn) │ ▶ exit_code_from_results ┌crit, warn, deployed┐ → ◇ crit>0? 2 : warn>0? 0 : 0 │ ▶ status_metrics_json → ⊕ json.dumps(template) │ ▶ hc_marker_path → ⎋ str │ ▶ render_llm_summary → ⎋ str
# region MODULE_CONTRACT
## @purpose  Pure functions extracted from deploy_orchestrator.py (DevPlan 119 E6): severity
##           aggregation, exit-code computation, status-metrics JSON serialization, hc-marker
##           path, LLM render summary. Все функции ЧИСТЫЕ — без I/O и сайд-эффектов
##           (детерминированные, тестируемые изолированно).
## @scope    Consumed by core/internal/bootstrap/deploy/deploy_orchestrator.py (I/O-обёртки
##           делегируют чистые вычисления сюда). Не выполняет file/network/subprocess операций.
## @invariants
##   - НИ ОДНА функция не выполняет I/O (no open, no subprocess, no os.environ mutation)
##   - Входные данные передаются явно (severity_map пред-резолвится caller'ом)
##   - Детерминированность: одинаковые аргументы → одинаковый результат
##   - exit-code контракт: CRIT>0 → 2, WARN>0 → 0, no failures → 0 (DEPLOY_BEST_EFFORT)
##   - severity_map: name → "critical" | "warn" (default "warn" для неизвестных)
## @rationale DevPlan 119 E6 (AUDIT-2 M5): deploy_orchestrator.py (941 LOC) — severity/exit-code/
##           status-metrics/hc-marker/llm-рендер выносятся в чистые функции для изолированного
##           тестирования (R5: test_orchestrator_metrics_pure — нет сайд-эффектов). I/O остаётся
##           в deploy_orchestrator (модуль.yaml fallback, touch маркера, json file write).
## @changes 2026-08-02 · DevPlan 119 E6 — создан, извлечено из deploy_orchestrator.py
## @modulemap
##   aggregate_severity [W:1] — pure: failed + severity_map → (crit_count, warn_count)
##   exit_code_from_results [W:1] — pure: (crit, warn, deployed) → int {0,2}
##   status_metrics_json [W:1] — pure: template → JSON string
##   hc_marker_path [W:1] — pure: → marker path constant
##   render_llm_summary [W:1] — pure: core_dir + status → summary string
## @usecases
##   - deploy_orchestrator.orchestrate() PHASE 5: crit,warn = aggregate_severity(...); exit_code_from_results(...)
##   - deploy_orchestrator._preflight: _create_status_metrics_json → status_metrics_json()
##   - deploy_orchestrator._postflight: _set_hc_marker → hc_marker_path()
## @links    CONSUMER(core/internal/bootstrap/deploy/deploy_orchestrator.py),
##           POLICY(core/internal/shared/contracts.py DEPLOY_BEST_EFFORT)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
from typing import Any

# ── Constants (paths mirror deploy-modules.sh facade / docker_orchestrator.py) ──
_HC_DONE_MARKER = "/var/lib/platform/.bootstrap/.hc_done_in_deploy"
_STATUS_METRICS_PATH = "/run/platform/status-metrics.json"
_STATUS_METRICS_TEMPLATE: dict[str, Any] = {
    "schema_version": 2,
    "generated_at": None,
    "containers": [],
    "certs": [],
    "projects": [],
    "host": {},
}


# region FUNC_aggregate_severity
## @purpose  PHASE 5 (pure): map failed module names to severity counts. severity_map пред-резолвится
##           caller'ом (deploy_orchestrator: enriched modules dict + per-module module.yaml fallback);
##           здесь — ТОЛЬКО чистая агрегация (AUDIT-2 M5, E6).
## @io       ⇥ failed: list[str], severity_map: dict[str, str] → ⎋ tuple[int, int] (crit, warn)
## @complexity O(N) где N = len(failed)
## @invariants
##   - severity_map.get(name, "warn") — default warn для неизвестных модулей
##   - Только "critical" считается CRIT; всё остальное — WARN
##   - Без I/O: severity_map уже содержит резолв (fallback — в deploy_orchestrator)
def aggregate_severity(failed: list[str], severity_map: dict[str, str]) -> tuple[int, int]:
    """Count critical/warn failures from a pre-resolved severity map (pure)."""
    crit = 0
    warn = 0
    for name in failed:
        severity = severity_map.get(name, "warn")
        if severity == "critical":
            crit += 1
        else:
            warn += 1
    return crit, warn


# endregion FUNC_aggregate_severity


# region FUNC_exit_code_from_results
## @purpose  Compute final exit code (pure): CRIT>0 → 2, WARN>0 → 0 (non-critical), none → 0.
## @io       ⇥ crit: int, warn: int, deployed: int → ⎋ int {0,2}
## @complexity O(1) — two comparisons
## @invariants
##   - WARN → exit 0 (DEPLOY_BEST_EFFORT policy — warnings are non-critical by definition)
##   - Only CRIT failures escalate to exit 2
def exit_code_from_results(crit: int, warn: int, deployed: int) -> int:
    """Severity-based exit code (DEPLOY_BEST_EFFORT contract: CRIT→2, WARN→0, DONE→0)."""
    if crit > 0:
        return 2
    if warn > 0:
        return 0
    return 0


# endregion FUNC_exit_code_from_results


# region FUNC_status_metrics_json
## @purpose  Serialize the status-metrics template to JSON (pure). I/O (file write) остаётся
##           в deploy_orchestrator._create_status_metrics_json.
## @io       ⇥ None → ⎋ str (JSON-сериализованный шаблон)
## @complexity O(1) — json.dumps
## @invariants
##   - Всегда валидный JSON объект (пустой шаблон) — Docker bind-mount P1 fix
##   - indent=2 + ensure_ascii=False (читаемый, UTF-8-safe)
def status_metrics_json() -> str:
    """Serialize the status-metrics template to a JSON string (pure)."""
    return json.dumps(_STATUS_METRICS_TEMPLATE, indent=2, ensure_ascii=False)


# endregion FUNC_status_metrics_json


# region FUNC_hc_marker_path
## @purpose  Path of the healthcheck-done marker (pure constant) — signals state_machine.py to
##           skip the standalone healthcheck (already ran inside deploy_docker_group).
## @io       ⇥ None → ⎋ str
## @complexity O(1)
## @invariants
##   - Единый источник пути (дедупликация с phases.py hc_done_marker)
##   - Значение: /var/lib/platform/.bootstrap/.hc_done_in_deploy
def hc_marker_path() -> str:
    """Return the healthcheck-done marker path (single source of truth)."""
    return _HC_DONE_MARKER


# endregion FUNC_hc_marker_path


# region FUNC_render_llm_summary
## @purpose  Build a human-readable LLM config render summary line (pure). Фактический рендер
##           (config_renderer.render_to_file — I/O) остаётся в deploy_orchestrator._render_litellm_config.
## @io       ⇥ core_dir: str, policy_path: str, output_path: str → ⎋ str
## @complexity O(1)
## @invariants
##   - Без I/O — только форматирование строки
##   - Включает исходный и целевой путь для диагностики
def render_llm_summary(core_dir: str, policy_path: str, output_path: str) -> str:
    """Build an LLM config render summary (pure formatting)."""
    return f"Rendering litellm-config.yml from {policy_path} (core_dir={core_dir}) → {output_path}"


# endregion FUNC_render_llm_summary
