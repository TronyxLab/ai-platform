# GREP_SUMMARY: check-project-runner, check_project, resolve-language, select-checks, exit-code, L1-block, maturity, escalator, level-override
# STRUCTURE: ▶ check_project(project_dir, level, fix, facts) → load_manifest (exit 4) → project_profile (languages+level, T2.12) → compute_maturity → read_lock → evaluate (state) → select_checks (baseline|full × language × local + L1 always) → ⊕ run_check × N → _compute_exit_code (L1 | active-full) → ⎋ CheckReport
# region MODULE_CONTRACT
## @purpose  Оркестратор K1-канала практик (DevPlan 137 §2.1A/§4.7, 170 W10-A декомпозиция):
##           канон → язык → maturity → state эскалатора → выбор проверок (L1 всегда;
##           baseline всегда; full — в proposed/active-full; язык-фильтр) → исполнение
##           (exec.run_check) → exit-код (L1 FAIL → 1 при ЛЮБОМ состоянии; L2/L3 FAIL → 1
##           только в active-full). Library-функция check_project() — тесты вызывают напрямую.
## @scope    Потребители: cli.py (main → check_project), __init__.py (re-export), drift.py
##           (project_profile для canon-hash), checks/file.py (resolve_language re-export),
##           tests/unit/test_practices_check_project.py.
## @invariants
##   - exit-коды из shared/contracts.py (0/1/4) — НЕ хардкодить; ConfigValidationError → 4
##   - L1 FAIL → exit 1 при ЛЮБОМ состоянии; L2/L3 FAIL → exit 1 ТОЛЬКО в active-full
##   - --level override → validate_level_setting (baseline|full|auto) — форс состояния
##   - Неизвестный/отсутствующий type → пустой кортеж языков (только all-проверки — безопасно)
##   - Read-only без --fix: НЕ пишет в проект (проверки не мутируют)
##   - main() -> int; sys.exit только в __main__ (контракт core/AGENTS.md)
## @rationale Выделение runner-слоя из монолита: оркестрация (maturity/evaluate/select/exit)
##            отделена от исполнения (exec) и CLI (cli) — тестируемость и SRP (research-A §2).
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:142-305)
##           2026-08-22 · T2.12 — resolve_language перенесён в practices/profile.py (re-export
##                      сохранён для drift.py/checks/file.py); check_project → project_profile()
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

from core.internal.practices.check_project.exec import run_check
from core.internal.practices.check_project.models import CheckReport, CheckResult
from core.internal.practices.escalator import evaluate, validate_level_setting
from core.internal.practices.generators import read_lock
from core.internal.practices.manifest import (
    PracticeCheck,
    PracticesManifest,
    l1_checks,
    load_manifest,
)
from core.internal.practices.maturity import compute_maturity
from core.internal.practices.profile import (
    project_profile,
    resolve_language,  # ruff: ignore[F401] — re-export: checks/file.py импортирует runner.resolve_language (T2.12, слой совместимости)
)
from core.internal.shared.contracts import EXIT_GENERIC, EXIT_OK
from core.internal.shared.env_facts import EnvironmentFacts

logger = logging.getLogger(__name__)


# region FUNC_select_checks
## @purpose  Выбрать проверки канона для исполнения: канал local, язык ∈ project languages,
##           уровень по state (baseline → только baseline; proposed/active-full → baseline+full),
##           + L1-проверки ВСЕГДА (безопасность платформы).
## @io       ⇥ manifest, languages, state_name → ⎋ list[PracticeCheck] в порядке канона
## @complexity O(C)
def select_checks(manifest: PracticesManifest, languages: tuple[str, ...], state_name: str) -> list[PracticeCheck]:
    """Select checks to run locally (language × channel=local × level-by-state + L1 always).

    ## @purpose  L1-проверки — ВСЕГДА (безопасность платформы, §3.1 п.4); baseline-проверки —
    ##           всегда; full-проверки — только в proposed/active-full (эскалатор). Язык:
    ##           проверка применяется, если languages содержит "all" ИЛИ пересекается с языками
    ##           проекта (frontend → python+ts/react обе ветки, DevPlan 137 Q3).
    ## @io       ⇥ manifest, languages, state_name → ⎋ list[PracticeCheck] в порядке канона
    ## @complexity O(C)
    """
    full = state_name in {"proposed", "active-full"}
    l1_ids = {c.id for c in l1_checks()}
    selected: list[PracticeCheck] = []
    for check in manifest.checks:
        if not check.runs_in("local"):
            continue
        if check.id not in l1_ids and check.level == "full" and not full:
            continue  # full-проверки только в proposed/active-full (эскалатор)
        if not any(check.applies_to(lang) for lang in languages) and not check.applies_to("all"):
            continue
        selected.append(check)
    logger.info(
        "[IMP:9][check_project][select] %d checks selected (state=%s, languages=%s)",
        len(selected),
        state_name,
        languages,
    )
    return selected


# endregion FUNC_select_checks


# region FUNC_check_project
## @purpose  Исполнить project-check: канон → язык → maturity → state → проверки → отчёт.
##           Library-функция (тесты вызывают напрямую); CLI main() оборачивает.
## @io       ⇥ project_dir: Path, level: str | None (override), fix: bool,
##              facts: EnvironmentFacts | None (W4b DI: which gitleaks/docker/ruff/...) → ⎋ CheckReport
## @raises   ConfigValidationError — сломанный канон (exit 4)
## @complexity O(C * T) где C = число проверок, T = их таймауты
## @changes 2026-08-13 | DevPlan 160 W4b — +facts (DI для which-проверок инструментов)
##           2026-08-15 | DevPlan 170 W10-A — выделен в пакет check_project/runner.py
def check_project(
    project_dir: Path,
    *,
    level: str | None = None,
    fix: bool = False,
    facts: EnvironmentFacts | None = None,
) -> CheckReport:
    """Run practices checks on project dir → CheckReport (exit 0/1/4 semantics)."""
    project_dir = Path(project_dir)
    manifest = load_manifest()

    # ── language + level_setting (T2.12: единый project_profile — 1 чтение ai-platform.yaml) ──
    profile = project_profile(project_dir)
    languages = profile.languages
    level_setting = profile.level
    if level is not None:
        level_setting = validate_level_setting(level)

    # ── maturity + escalator state (локально есть git) ──
    maturity = compute_maturity(project_dir)
    lock = read_lock(project_dir)
    decision = evaluate(maturity, level_setting, lock)

    # ── select + run ──
    selected = select_checks(manifest, languages, decision.state_name)
    results: list[CheckResult] = []
    warnings: list[str] = []
    if decision.warning:
        warnings.append(decision.warning)
    for check in selected:
        result = run_check(check, project_dir, fix=fix, facts=facts)
        results.append(result)
        logger.info(
            "[IMP:9][check_project][run] %s=%s (%ds) %s",
            result.check_id,
            result.status,
            result.duration_s,
            result.message,
        )

    exit_code = _compute_exit_code(manifest, decision.state_name, results)
    report = CheckReport(
        state=decision.state_name,
        level_setting=level_setting,
        results=tuple(results),
        warnings=tuple(warnings),
        exit_code=exit_code,
    )
    logger.info(
        "[IMP:9][check_project][done] state=%s level=%s exit=%d (results=%d)",
        report.state,
        report.level_setting,
        report.exit_code,
        len(report.results),
    )
    return report


# endregion FUNC_check_project


# region FUNC__compute_exit_code
## @purpose  Exit-код по результатам: L1 FAIL → 1 всегда; L2/L3 FAIL → 1 только в active-full.
##           WARN/SKIP/PASS не влияют.
## @io       ⇥ manifest, state_name, results → ⎋ int (0 | 1)
## @complexity O(R)
def _compute_exit_code(manifest: PracticesManifest, state_name: str, results: list[CheckResult]) -> int:
    """Compute exit code (L1 always blocks; L2/L3 block only in active-full)."""
    by_id = manifest.by_id()
    blocking = False
    for result in results:
        if result.status != "FAIL":
            continue
        check = by_id.get(result.check_id)
        if check is None:
            continue
        if check.klass == "L1" or state_name == "active-full":
            blocking = True
    if blocking:
        logger.info("[IMP:9][check_project][exit] blocking violation → exit %d", EXIT_GENERIC)
        return EXIT_GENERIC
    return EXIT_OK


# endregion FUNC__compute_exit_code
