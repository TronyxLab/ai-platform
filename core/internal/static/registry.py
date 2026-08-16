"""Detector registry — ordered execution + aggregate report (DevPlan 163 W-C).

# GREP_SUMMARY: static registry detector-spec ordered-execution run-all human-report json-report severity-count
# STRUCTURE: ▶ DetectorSpec (name/description/detect) → ⊕ DETECTORS (ordered tuple) → ○ run_all
#            (root/changed/only) → ⊕ count_by_rule → ⊕ human_report/json_report → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Реестр детекторов статического слоя (DevPlan 163 W-C C1, T2.1): порядок
##           исполнения, единый вход run_all(root, changed, only), суммарный отчёт
##           (человекочитаемый + JSON для `static check --json`).
## @scope    Оркестрация — не содержит правил детекции. Каждый детектор — модуль
##           core/internal/static/<name>.py с сигнатурой detect(root, changed) → list[Finding].
## @invariants
##   - DETECTORS — упорядоченный кортеж (порядок = приоритет отчёта)
##   - Каждый детектор вызывается в try/except (сбой одного не блокирует остальные —
##     fail-visible: сбой логируется IMP:10 и попадает в отчёт как finding-ошибка)
##   - `only`: фильтр по именам правил (для agent-check точечных прогонов)
##   - run_all возвращает отсортированные находки (file, line, rule) — детерминизм
## @rationale Единая точка исполнения ~20 grep-гейтов (W2 T2.1): детекторы вызываются
##            из CLI (__main__.py) и из agent-check (W-E) с одинаковым интерфейсом.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import dataclasses
import importlib
import json
import logging
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

# Сигнатура детектора: detect(root: Path, changed: set[str] | None) -> list[Finding]
DetectFn = Callable[[Path, "set[str] | None"], list[Finding]]


@dataclasses.dataclass(frozen=True, slots=True)
class DetectorSpec:
    """Описание детектора для реестра.

    # ▶ DetectorSpec ┌name+description+detect┐ → ⎋ (для DETECTORS/отчётов)
    """

    name: str
    description: str
    detect: DetectFn


# region FUNC_import_detector
def _import_detector(module_name: str) -> ModuleType:
    """Импортировать модуль детектора по имени (top-level, без lazy-import).

    ## @purpose  Резолв модулей core.internal.static.<name> для реестра. Top-level
    ##           импорты (PLC0415-канон): DETECTORS строится при импорте registry.
    ## @io       ⇥ module_name: str → ⎋ ModuleType
    ## @complexity  O(1) (кэш importlib)
    """
    return importlib.import_module(f"core.internal.static.{module_name}")


# endregion FUNC_import_detector


# region DETECTORS
DETECTORS: tuple[DetectorSpec, ...] = (
    DetectorSpec(
        "dead-code",
        "Unreachable shell scripts under core/entrypoints/ and core/internal/ (call-graph BFS)",
        cast(DetectFn, _import_detector("dead_code").detect),
    ),
    DetectorSpec(
        "cross-layer",
        "Dotted-import layer isolation: core.<layer>.* outside allowed directions (entrypoints/internal/modules)",
        cast(DetectFn, _import_detector("cross_layer").detect),
    ),
    DetectorSpec(
        "bool-string-literals",
        "Strict bool-string comparisons (==/!= 'true'/'false') without .lower() normalization (T6)",
        cast(DetectFn, _import_detector("bool_string_literals").detect),
    ),
    DetectorSpec(
        "exception-patterns",
        "Bare except and broad `except Exception` without noqa:EXC + policy marker (B4 T8, U-39)",
        cast(DetectFn, _import_detector("exception_patterns").detect),
    ),
    DetectorSpec(
        "local-path-remote",
        "Local paths forwarded to remote arguments in passthrough/build_ssh_cmd (FL6)",
        cast(DetectFn, _import_detector("local_path_remote").detect),
    ),
    DetectorSpec(
        "docker-sole-path",
        "docker compose / docker ps|inspect|exec subprocess outside shared facades + shell/make direct calls (U-13/128/D70)",
        cast(DetectFn, _import_detector("docker_sole_path").detect),
    ),
    DetectorSpec(
        "audit-format",
        "Direct audit-file writes outside shared/audit_logger.py + free-text pipe format (R2, U-10)",
        cast(DetectFn, _import_detector("audit_format").detect),
    ),
    DetectorSpec(
        "env-chain",
        "Unresolved ${VAR} placeholders in prometheus.yml.tmpl + duplicate prometheus.yml (U-48)",
        cast(DetectFn, _import_detector("env_chain").detect),
    ),
    DetectorSpec(
        "verb-register",
        "Makefile .PHONY targets ↔ entrypoint-manifest allowed_verbs parity (G1.2 name-linter)",
        cast(DetectFn, _import_detector("verb_register").detect),
    ),
    DetectorSpec(
        "hardcoded-paths",
        "Hardcoded local (/Users/../home/) and server (/opt/platform/) paths in tests/ + core/ (P0 cross-platform)",
        cast(DetectFn, _import_detector("hardcoded_paths").detect),
    ),
    DetectorSpec(
        "bare-raise",
        "raise ValueError/RuntimeError in core/internal — typed PlatformError hierarchy only (U-12)",
        cast(DetectFn, _import_detector("bare_raise").detect),
    ),
    DetectorSpec(
        "sys-exit-contract",
        "sys.exit only in main()/__main__ + every main() -> int (U-29)",
        cast(DetectFn, _import_detector("sys_exit_contract").detect),
    ),
    DetectorSpec(
        "private-imports",
        "Private cross-module imports (from X import _name / X._attr) in core/ (U-07)",
        cast(DetectFn, _import_detector("private_imports").detect),
    ),
    DetectorSpec(
        "inline-secrets",
        "secrets.env inline parsing outside shared/secrets_env_parser (086)",
        cast(DetectFn, _import_detector("inline_secrets").detect),
    ),
)


# endregion DETECTORS


# region FUNC_run_all
def run_all(root: Path, changed: set[str] | None = None, only: set[str] | None = None) -> list[Finding]:
    """Исполнить все (или выбранные) детекторы в порядке реестра.

    # ▶ ┌root┐ → ○ for spec in DETECTORS → ◇ only-filter? → ○ detect() → ⊕ findings → ⎋ sorted

    ## @purpose  Единая точка полного прохода статического слоя. Fail-fast:
    ##           сбой детектора ПРОПАГИРУЕТСЯ (конституция §4 — все ошибки видимы;
    ##           BLE001-канон — никаких широких except). CLI ловит и печатает.
    ## @io       ⇥ root: Path, changed: set[str] | None, only: set[str] | None
    ##           ⎋ list[Finding] — сортировка (file, line, rule)
    ## @complexity  ∑ O(detector_i) — сумма детекторов
    ## @invariants  Детекторы исполняются в порядке DETECTORS; сортировка результата
    ##              детерминирована (file, line, rule)
    """
    findings: list[Finding] = []
    for spec in DETECTORS:
        if only is not None and spec.name not in only:
            logger.info("[IMP:7][registry][skip] %s (not in --only)", spec.name)
            continue
        logger.info("[IMP:7][registry][run] %s — %s", spec.name, spec.description)
        findings.extend(spec.detect(root, changed))
    findings.sort(key=lambda f: (f.file, f.line, f.rule))
    logger.info(
        "[IMP:9][registry] run_all finished: %d finding(s) across %d detector(s)", len(findings), len(DETECTORS)
    )
    return findings


# endregion FUNC_run_all


# region FUNC_count_by_rule
def count_by_rule(findings: list[Finding]) -> dict[str, int]:
    """Подсчитать находки по правилам (для суммарного отчёта).

    ## @purpose  {rule: count} — машиночитаемый сводный профиль.
    ## @io       ⇥ findings: list[Finding] → ⎋ dict[str, int]
    ## @complexity  O(N) — находки
    """
    return dict(sorted(Counter(f.rule for f in findings).items()))


# endregion FUNC_count_by_rule


# region FUNC_human_report
def human_report(findings: list[Finding]) -> str:
    """Человекочитаемый отчёт (дефолтный вывод CLI).

    ## @purpose  Стабильный текстовый отчёт: сводка по правилам + построчный список.
    ## @io       ⇥ findings: list[Finding] → ⎋ str
    ## @complexity  O(N)
    """
    if not findings:
        return "static check: PASS — 0 findings"
    lines = ["static check: FAIL"]
    counts = count_by_rule(findings)
    lines.append(f"  findings by rule: {counts}")
    lines.extend(f"  {f}" for f in findings)
    return "\n".join(lines)


# endregion FUNC_human_report


# region FUNC_json_report
def json_report(findings: list[Finding]) -> str:
    """JSON-отчёт (машиночитаемый вывод CLI, --json).

    ## @purpose  {findings: [...], summary: {total, by_rule}} — контракт T3.1
    ##           (agent-check JSON парсинг).
    ## @io       ⇥ findings: list[Finding] → ⎋ str (JSON)
    ## @complexity  O(N)
    """
    return json.dumps(
        {
            "findings": [f.to_dict() for f in findings],
            "summary": {"total": len(findings), "by_rule": count_by_rule(findings)},
        },
        ensure_ascii=False,
        indent=2,
    )


# endregion FUNC_json_report
