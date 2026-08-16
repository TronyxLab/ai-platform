# GREP_SUMMARY: check-project-tool-checks, gitleaks, ruff-format, ruff-check, shellcheck, pyright, eslint, build, pytest, tool-missing-warn, timeout
# STRUCTURE: ▶ _pytest_cmd (addopts override) → ⊕ gitleaks (git pre-commit) → ⊕ ruff format/check (canon select) → ⊕ shellcheck -S error → ⊕ pyright / eslint / build (npm) → ⊕ pytest-baseline/full (allow_no_tests rc=5) → ⎋ CheckResult
# region MODULE_CONTRACT
## @purpose  Инструментальные handler-и K1-канала (DevPlan 137 §2.1A, 170 W10-A декомпозиция):
##           проверки, исполняющие ВНЕШНИЕ CLI-инструменты через exec.subprocess_run —
##           gitleaks (секреты в git, L1), ruff format/check (canon select/ignore, автофикс
##           через fixer), shellcheck, pyright, eslint, build (npm), pytest baseline/full
##           (allow_no_tests rc=5 → PASS; --timeout только если pytest-timeout установлен).
##           tool missing → WARN (окружение, НЕ качество проекта — инвариант K1).
## @scope    Потребители: checks/__init__.py (реестр), runner (через _run_check). DI-канал
##           facts: EnvironmentFacts — which-проверки инструментов (W4b, тесты передают fake).
## @invariants
##   - missing tool → WARN «not installed» (НЕ блок, НЕ FAIL) — окружение, не проект
##   - pytest rc=5 (нет тестов) → PASS (allow_no_tests §3.2); rc=2 (usage --timeout) → повтор
##     без --timeout; команда — sys.executable -m pytest (интерпретатор = find_spec-пробе)
##   - ruff-check: --select/--ignore ЯВНО (канон RUFF_FULL_SELECT/IGNORE — детерминизм
##     независимо от pyproject проекта, TRAP[DECISION] §10.2: НЕ в pre-commit)
##   - ruff format fix: rc ∈ {0, 1} успех; иной rc → WARN (не FAIL)
##   - gitleaks: rc=127 → WARN (unavailable в окружении); иной → FAIL «secrets detected»
## @rationale Группировка 9 tool-проверок: общий subprocess-слой (exec) + which-гейты;
##            отделён от файловых (checks/file.py) и compose-проверок (checks/compose.py).
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:353-685)
# endregion MODULE_CONTRACT

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TypedDict, cast

from core.internal.practices.check_project.exec import (
    CMD_NOT_FOUND_RC,
    PYTEST_NO_TESTS_RC,
    subprocess_run,
    tail,
)
from core.internal.practices.check_project.files import iter_project_files
from core.internal.practices.check_project.fixer import fix_ruff_check, fix_ruff_format
from core.internal.practices.check_project.models import CheckResult
from core.internal.practices.generators import RUFF_FULL_IGNORE, RUFF_FULL_SELECT
from core.internal.practices.manifest import PracticeCheck
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts


# Граница package.json (json.loads → TypedDict, W11): только scripts читается — остальные ключи вне контракта
class _PackageJson(TypedDict, total=False):
    """Минимальный контракт package.json для build-проверки (только scripts)."""

    scripts: dict[str, str]


# region FUNC__pytest_helpers
## @purpose  pytest-команда с контролем addopts: GENERATED pyproject уже содержит --timeout=60
##           (и strict-флаги в full) — дублирование флага ломает pytest (rc=4). Через
##           `-o addopts=...` переопределяем ini-addopts детерминированно: --timeout только
##           если pytest-timeout установлен в ИНТЕРПРЕТАТОРЕ (find_spec — без реального
##           импорта, гейт test_core_imports_covered_by_pyproject), strict-флаги только для full.
## @io       ⇥ strict: bool → ⎋ list[str] pytest-команда
## @complexity O(1)
def _pytest_timeout_available() -> bool:
    """True если pytest-timeout установлен (иначе --timeout в addopts ломает запуск)."""
    return importlib.util.find_spec("pytest_timeout") is not None


def _pytest_cmd(*, strict: bool) -> list[str]:
    """Build pytest command with controlled addopts (-o override)."""
    opts_parts = ["-q", "-x"]
    if _pytest_timeout_available():
        opts_parts.append("--timeout=60")
    if strict:
        opts_parts.extend(["--strict-markers", "--strict-config"])
    return [sys.executable, "-m", "pytest", "-o", f"addopts={' '.join(opts_parts)}"]


# endregion FUNC__pytest_helpers


# ═══════════════════════════════════════════════════════════════════
# region CHECK_gitleaks
def check_gitleaks(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,
) -> CheckResult:
    """gitleaks git --pre-commit — секреты в git. L1: блок всегда."""
    if (facts or default_env_facts()).which("gitleaks") is None:
        return CheckResult(check.id, "WARN", "gitleaks not installed (pre-commit installs it)", 0.0)
    rc, out, err, dur = subprocess_run(
        ["gitleaks", "git", "--pre-commit", "--no-banner"], project_dir, check.timeout_sec
    )
    if rc == 0:
        return CheckResult(check.id, "PASS", "no secrets found", dur)
    if rc == CMD_NOT_FOUND_RC:
        return CheckResult(check.id, "WARN", "gitleaks unavailable in this environment", dur)
    return CheckResult(check.id, "FAIL", f"secrets detected (rc={rc}): {tail(err or out)}", dur)


# endregion CHECK_gitleaks


# ═══════════════════════════════════════════════════════════════════
# region CHECK_ruff_format
def check_ruff_format(
    check: PracticeCheck, project_dir: Path, *, fix: bool, facts: EnvironmentFacts | None = None
) -> CheckResult:
    """ruff format --check (auto_fix: ruff format в --fix)."""
    if (facts or default_env_facts()).which("ruff") is None:
        return CheckResult(check.id, "WARN", "ruff not installed", 0.0)
    if fix:
        rc_fix, err_fix = fix_ruff_format(project_dir, check.timeout_sec)
        if rc_fix not in {0, 1}:
            return CheckResult(check.id, "WARN", f"ruff format failed (rc={rc_fix}): {err_fix}", 0.0)
    rc, out, _, dur = subprocess_run(["ruff", "format", "--check", "."], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "ruff format clean", dur)
    return CheckResult(
        check.id, "FAIL", f"{len(out.splitlines())} file(s) need formatting (run: make project-check --fix)", dur
    )


# endregion CHECK_ruff_format


# ═══════════════════════════════════════════════════════════════════
# region CHECK_ruff_check
def check_ruff_check(
    check: PracticeCheck, project_dir: Path, *, fix: bool, facts: EnvironmentFacts | None = None
) -> CheckResult:
    """ruff check — полный набор правил (full-only, TRAP[DECISION] §10.2: НЕ в pre-commit).
    --select/--ignore передаются ЯВНО (канон из generators.RUFF_FULL_SELECT/IGNORE):
    CLI --select перекрывает select конфига, а ignore конфига НЕ применяется к CLI-селекту
    (детерминизм канона независимо от pyproject проекта)."""
    if (facts or default_env_facts()).which("ruff") is None:
        return CheckResult(check.id, "WARN", "ruff not installed", 0.0)
    select = ["--select", ",".join(RUFF_FULL_SELECT), "--ignore", ",".join(RUFF_FULL_IGNORE)]
    if fix:
        fix_ruff_check(project_dir, check.timeout_sec, select)
    rc, out, _, dur = subprocess_run(["ruff", "check", *select, "."], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "ruff check clean (full rules)", dur)
    return CheckResult(check.id, "FAIL", f"{len(out.splitlines())} violation(s): {tail(out)}", dur)


# endregion CHECK_ruff_check


# ═══════════════════════════════════════════════════════════════════
# region CHECK_shellcheck
def check_shellcheck(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,
) -> CheckResult:
    """shellcheck -S error по sh-файлам проекта (L3)."""
    sh_files = [p for p in iter_project_files(project_dir) if p.suffix == ".sh"]
    if not sh_files:
        return CheckResult(check.id, "PASS", "no shell scripts", 0.0)
    if (facts or default_env_facts()).which("shellcheck") is None:
        return CheckResult(check.id, "WARN", "shellcheck not installed", 0.0)
    rc, out, err, dur = subprocess_run(
        ["shellcheck", "-S", "error", *(str(p) for p in sh_files)], project_dir, check.timeout_sec
    )
    if rc == 0:
        return CheckResult(check.id, "PASS", f"{len(sh_files)} script(s) clean", dur)
    return CheckResult(check.id, "FAIL", f"shellcheck found issues (rc={rc}): {tail(err or out)}", dur)


# endregion CHECK_shellcheck


# ═══════════════════════════════════════════════════════════════════
# region CHECK_pyright_eslint
def check_pyright(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,
) -> CheckResult:
    """pyright (full) — типы; tool missing → WARN."""
    if (facts or default_env_facts()).which("pyright") is None:
        return CheckResult(check.id, "WARN", "pyright not installed", 0.0)
    rc, out, err, dur = subprocess_run(["pyright"], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "pyright clean", dur)
    return CheckResult(check.id, "FAIL", f"pyright (rc={rc}): {tail(err or out)}", dur)


def check_eslint(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,
) -> CheckResult:
    """eslint (full) — ts/react; npx eslint . если package.json."""
    if not (project_dir / "package.json").is_file():
        return CheckResult(check.id, "PASS", "no package.json", 0.0)
    if (facts or default_env_facts()).which("npx") is None:
        return CheckResult(check.id, "WARN", "npx not installed", 0.0)
    rc, out, err, dur = subprocess_run(["npx", "eslint", "."], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "eslint clean", dur)
    return CheckResult(check.id, "FAIL", f"eslint (rc={rc}): {tail(err or out)}", dur)


# endregion CHECK_pyright_eslint


# ═══════════════════════════════════════════════════════════════════
# region CHECK_build
def check_build(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,
) -> CheckResult:
    """build: npm run build, если package.json с build-скриптом (L2)."""
    pkg = project_dir / "package.json"
    if not pkg.is_file():
        return CheckResult(check.id, "PASS", "no package.json", 0.0)
    try:
        import json

        pkg_data = cast(_PackageJson, json.loads(pkg.read_text(encoding="utf-8")))
        scripts = pkg_data.get("scripts", {})
    except (OSError, ValueError):
        return CheckResult(check.id, "WARN", "package.json unparseable", 0.0)
    if "build" not in scripts:
        return CheckResult(check.id, "PASS", "no build script in package.json", 0.0)
    if (facts or default_env_facts()).which("npm") is None:
        return CheckResult(check.id, "WARN", "npm not installed", 0.0)
    # v1.0.1 (TRAP[BUG] Фаза 3): свежий проект без node_modules → `npm run build` падал
    # «tsc: command not found» (rc=127). CI deploy-project.yml делает `npm ci` перед build —
    # локальная проверка K1 повторяет паритет: npm ci при отсутствии node_modules.
    if not (project_dir / "node_modules").is_dir():
        rc_ci, out_ci, err_ci, _ = subprocess_run(["npm", "ci"], project_dir, check.timeout_sec)
        if rc_ci != 0:
            return CheckResult(check.id, "FAIL", f"npm ci failed (rc={rc_ci}): {tail(err_ci or out_ci)}", 0.0)
    rc, out, err, dur = subprocess_run(["npm", "run", "build"], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "npm run build passed", dur)
    return CheckResult(check.id, "FAIL", f"npm run build failed (rc={rc}): {tail(err or out)}", dur)


# endregion CHECK_build


# ═══════════════════════════════════════════════════════════════════
# region CHECK_pytest_baseline
def check_pytest_baseline(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,  # ruff: ignore[ARG001]
) -> CheckResult:
    """pytest -q -x [--timeout=60] — если тесты есть; exit 5 (нет тестов) → PASS (allow_no_tests)."""
    if not (project_dir / "tests").is_dir():
        return CheckResult(check.id, "PASS", "no tests directory", 0.0)
    rc, out, err, dur = subprocess_run(_pytest_cmd(strict=False), project_dir, check.timeout_sec)
    if rc == PYTEST_NO_TESTS_RC:  # no tests collected → PASS (allow_no_tests, §3.2)
        return CheckResult(check.id, "PASS", "no tests collected (allow_no_tests)", dur)
    if rc == 0:
        return CheckResult(check.id, "PASS", "pytest passed", dur)
    return CheckResult(check.id, "FAIL", f"pytest failed (rc={rc}): {tail(err or out)}", dur)


# endregion CHECK_pytest_baseline


# ═══════════════════════════════════════════════════════════════════
# region CHECK_pytest_full
def check_pytest_full(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,  # ruff: ignore[ARG001]
) -> CheckResult:
    """pytest-full: strict-маркеры + strict-config (full)."""
    if not (project_dir / "tests").is_dir():
        return CheckResult(check.id, "PASS", "no tests directory", 0.0)
    rc, out, err, dur = subprocess_run(_pytest_cmd(strict=True), project_dir, check.timeout_sec)
    if rc == PYTEST_NO_TESTS_RC:
        return CheckResult(check.id, "PASS", "no tests collected (allow_no_tests)", dur)
    if rc == 0:
        return CheckResult(check.id, "PASS", "pytest (strict) passed", dur)
    return CheckResult(check.id, "FAIL", f"pytest strict failed (rc={rc}): {tail(err or out)}", dur)


# endregion CHECK_pytest_full
