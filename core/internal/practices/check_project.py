#!/usr/bin/env python3
# GREP_SUMMARY: check_project, project-check, project-fix, baseline, full, L1-block, exit-codes, PRACTICES, K1-local, drift-gate, ruff, pytest, gitleaks
# STRUCTURE: ▶ main --project-dir [--level] [--fix] → load_manifest (exit 4) → resolve language → compute_maturity → evaluate (state) → select checks (baseline|full × language × local) → run each (subprocess|python, timeout) → [PRACTICES:...] report → exit 0|1
# region MODULE_CONTRACT
## @purpose  Локальный канал K1 практик (DevPlan 137 §2.1A/§4.7): исполнение проверок канона
##           по каталогу проекта — `python3 -m core.internal.practices.check_project
##           --project-dir PROJECT [--level LEVEL] [--fix]`. Выход: exit 0 (зелёный), 1 (L1-блок
##           всегда; L2/L3-блок в active-full), 4 (ConfigValidationError — сломанный канон).
##           L1-проверки исполняются ВСЕГДА (безопасность платформы, §3.1 п.4); L2/L3 —
##           warning в baseline/proposed, блок в active-full. [PRACTICES:...] вывод для
##           агента; LDD [IMP:9] в каждом результате.
## @scope    K1: локальная машина разработчика (есть git — maturity вычислима). Makefile:
##           project-check / project-fix (alias --fix). НЕ VPS (там verify_contracts, K3).
## @invariants
##   - exit-коды из shared/contracts.py (0/1/4) — НЕ хардкодить; ConfigValidationError → 4
##   - L1 FAIL → exit 1 при ЛЮБОМ состоянии; L2/L3 FAIL → exit 1 ТОЛЬКО в active-full
##   - missing tool → WARN (не блок, не FAIL) — окружение, не качество проекта
##   - pytest exit 5 (нет тестов) → PASS (allow_no_tests, §3.2); exit 2 (usage --timeout) →
##     повтор без --timeout (pytest-timeout может не быть установлен)
##   - --level override → форс состояния (baseline|full) как в escalator.evaluate
##   - main() -> int; sys.exit только в __main__ (контракт core/AGENTS.md)
##   - Read-only без --fix: НЕ пишет в проект (проверки не мутируют)
## @rationale Проверки исполняются платформенным Python (языковая политика), конфиги проекта —
##            единственный источник правил; L1 всегда — защита платформы, а не качество проекта.
## @changes  2026-08-05 · DevPlan 137 W1 — создан (baseline-исполнение + full-отчёты + drift)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.internal.practices.escalator import evaluate, validate_level_setting
from core.internal.practices.generators import (
    GENERATED_HEADER,
    RUFF_FULL_IGNORE,
    RUFF_FULL_SELECT,
    compute_generator_hash,
    read_lock,
    render_project_files,
)
from core.internal.practices.manifest import (
    LANGUAGE_FOR_TYPE,
    PracticesManifest,
    l1_checks,
    load_manifest,
)
from core.internal.practices.maturity import compute_maturity
from core.internal.shared.contracts import EXIT_CONFIG_VALIDATION, EXIT_GENERIC, EXIT_OK
from core.internal.shared.exceptions import ConfigValidationError, PlatformError
from core.internal.shared.project_yaml import get_name, get_project_type, load_project_yaml

logger = logging.getLogger(__name__)

# ── Conventional Commits regex (commit-msg проверка) ──
_CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|init)(\([a-z0-9_-]+\))?!?: .+"
)

# ── Директории, исключаемые из файловых проверок (hygiene/grep-summary/shellcheck) ──
_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "coverage",
        ".next",
        "__pycache__",
        ".git",
        ".pytest_cache",
        ".ruff_cache",
    }
)

# ── Расширения текстовых файлов для hygiene/grep-summary ──
_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".yaml", ".yml", ".toml", ".json", ".sh", ".md", ".txt", ".ts", ".tsx", ".js", ".jsx", ".html", ".css"}
)


# region FUNC_CheckResult
## @purpose  Frozen-результат одной проверки (DevPlan 137 §2.1A): id + статус + сообщение + время.
## @io       ⇥ check_id/status/message/duration_s → ⎋ CheckResult
## @complexity O(1)
@dataclass(frozen=True)
class CheckResult:
    """Result of a single check execution."""

    check_id: str
    status: str  # PASS | FAIL | WARN | SKIP
    message: str
    duration_s: float


# endregion FUNC_CheckResult


# region FUNC_CheckReport
## @purpose  Frozen-отчёт прогона project-check: state + level + результаты + warning-блок.
## @io       ⇥ state/level_setting/results/warnings/exit_code → ⎋ CheckReport
## @complexity O(1)
@dataclass(frozen=True)
class CheckReport:
    """Full report of a project-check run."""

    state: str
    level_setting: str
    results: tuple[CheckResult, ...]
    warnings: tuple[str, ...]
    exit_code: int


# endregion FUNC_CheckReport


# region FUNC_resolve_language
## @purpose  Определить языки проекта из ai-platform.yaml type (см. LANGUAGE_FOR_TYPE).
##           Неизвестный/отсутствующий type → ("python",) дефолт? НЕТ — "all"-only безопаснее:
##           пустой кортеж → только all-проверки (безопасно). fallback: type backend-подобный.
## @io       ⇥ project_dir: Path → ⎋ tuple[str, ...] языков канона
## @complexity O(1)
## @invariants
##   - type frontend → (typescript, react); fullstack → (python, typescript, react)
##   - Неизвестный type → ("all",)-эквивалент: пустой кортеж языков → только all-проверки
def resolve_language(project_dir: Path) -> tuple[str, ...]:
    """Resolve canon languages from ai-platform.yaml type (fullstack → python+ts).

    ## @purpose  type из ai-platform.yaml (backend|frontend|fullstack|python|typescript|react|sh)
    ##           → кортеж языков канона (§3.2). Неизвестный/отсутствующий type → пустой кортеж
    ##           (только all-проверки — безопасный fallback, не угадываем язык).
    ## @io       ⇥ project_dir: Path → ⎋ tuple[str, ...]
    ## @complexity O(1)
    """
    data = load_project_yaml(project_dir)
    ptype = get_project_type(data)
    languages = LANGUAGE_FOR_TYPE.get(ptype)
    if languages is None:
        logger.info("[IMP:7][check_project][lang] Unknown type '%s' — only all-language checks", ptype or "<none>")
        return ()
    logger.info("[IMP:8][check_project][lang] type=%s → languages=%s", ptype, languages)
    return languages


# endregion FUNC_resolve_language


# region FUNC_select_checks
## @purpose  Выбрать проверки канона для исполнения: канал local, язык ∈ project languages,
##           уровень по state (baseline → только baseline; proposed/active-full → baseline+full),
##           + L1-проверки ВСЕГДА (безопасность платформы).
## @io       ⇥ manifest, languages, state_name → ⎋ list[PracticeCheck] в порядке канона
## @complexity O(C)
def select_checks(manifest: PracticesManifest, languages: tuple[str, ...], state_name: str) -> list[Any]:
    """Select checks to run locally (language × channel=local × level-by-state + L1 always).

    ## @purpose  L1-проверки — ВСЕГДА (безопасность платформы, §3.1 п.4); baseline-проверки —
    ##           всегда; full-проверки — только в proposed/active-full (эскалатор). Язык:
    ##           проверка применяется, если languages содержит "all" ИЛИ пересекается с языками
    ##           проекта (fullstack → python+ts/react обе ветки, DevPlan 137 Q3).
    ## @io       ⇥ manifest, languages, state_name → ⎋ list[PracticeCheck] в порядке канона
    ## @complexity O(C)
    """
    full = state_name in ("proposed", "active-full")
    l1_ids = {c.id for c in l1_checks()}
    selected: list[Any] = []
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
## @io       ⇥ project_dir: Path, level: str | None (override), fix: bool → ⎋ CheckReport
## @raises   ConfigValidationError — сломанный канон (exit 4)
## @complexity O(C * T) где C = число проверок, T = их таймауты
def check_project(project_dir: Path, *, level: str | None = None, fix: bool = False) -> CheckReport:
    """Run practices checks on project dir → CheckReport (exit 0/1/4 semantics)."""
    project_dir = Path(project_dir)
    manifest = load_manifest()

    # ── language + level_setting ──
    languages = resolve_language(project_dir)
    data = load_project_yaml(project_dir)
    quality = data.get("quality") or {}
    level_setting = str(quality.get("level", "auto") or "auto")
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
        result = _run_check(check, project_dir, fix=fix)
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


# ═══════════════════════════════════════════════════════════════════
# region FUNC_run_check (dispatch)
## @purpose  Диспетчер исполнения одной проверки: handler по id (kebab-case → функция).
## @io       ⇥ check, project_dir, fix → ⎋ CheckResult
## @complexity O(1) + handler
_HANDLERS: dict[str, Any] = {}


def _run_check(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """Dispatch single check to its local handler (unknown id → SKIP)."""
    handler = _HANDLERS.get(check.id)
    if handler is None:
        return CheckResult(check.id, "SKIP", f"no local handler for check '{check.id}'", 0.0)
    return handler(check, project_dir, fix=fix)


# endregion FUNC_run_check


# ═══════════════════════════════════════════════════════════════════
# region FUNC__subprocess_run
## @purpose  Безопасный subprocess.run с таймаутом (для CLI-проверок). НЕ кидает при
##           ненулевом rc — возвращает (rc, stdout, stderr, duration).
## @io       ⇥ cmd, cwd, timeout → ⎋ tuple[int, str, str, float]
## @complexity O(1)
def _subprocess_run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, str, float]:
    """Run command with timeout; never raises on non-zero rc (returns rc/stdout/stderr/duration)."""
    start = time.monotonic()
    try:
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, result.stdout, result.stderr, time.monotonic() - start
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}", time.monotonic() - start
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s", time.monotonic() - start
    except OSError as exc:
        return 127, "", str(exc), time.monotonic() - start


# endregion FUNC__subprocess_run


# ═══════════════════════════════════════════════════════════════════
# region CHECK_gitleaks
def _check_gitleaks(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """gitleaks git --pre-commit — секреты в git. L1: блок всегда."""
    if shutil.which("gitleaks") is None:
        return CheckResult(check.id, "WARN", "gitleaks not installed (pre-commit installs it)", 0.0)
    rc, out, err, dur = _subprocess_run(
        ["gitleaks", "git", "--pre-commit", "--no-banner"], project_dir, check.timeout_sec
    )
    if rc == 0:
        return CheckResult(check.id, "PASS", "no secrets found", dur)
    if rc == 127:
        return CheckResult(check.id, "WARN", "gitleaks unavailable in this environment", dur)
    return CheckResult(check.id, "FAIL", f"secrets detected (rc={rc}): {_tail(err or out)}", dur)


# endregion CHECK_gitleaks


# ═══════════════════════════════════════════════════════════════════
# region CHECK_hygiene
def _check_hygiene(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """hygiene: trailing/EOF/merge-conflict/private-key/toml/json (auto_fix в --fix)."""
    start = time.monotonic()
    violations: list[str] = []
    for path, _is_text in _iter_text_files(project_dir):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if content.endswith((" \n", "\t\n")) or "\r\n" in content:
            violations.append(f"trailing/CRLF: {path.relative_to(project_dir)}")
        if content and not content.endswith("\n"):
            violations.append(f"no-final-newline: {path.relative_to(project_dir)}")
        if "<<<<<<<" in content or ">>>>>>>" in content:
            violations.append(f"merge-conflict: {path.relative_to(project_dir)}")
        if "PRIVATE KEY" in content:
            violations.append(f"private-key: {path.relative_to(project_dir)}")
        if path.suffix in (".toml", ".json") and not _parse_structured(path):
            violations.append(f"invalid-syntax: {path.relative_to(project_dir)}")
    if violations:
        if fix and check.auto_fix:
            _auto_fix_hygiene(project_dir)
            message = (
                f"{len(violations)} violation(s) auto-fixed: {violations[0]} (+{max(0, len(violations) - 1)} more)"
            )
            return CheckResult(check.id, "PASS", message, time.monotonic() - start)
        return CheckResult(
            check.id,
            "FAIL",
            f"{len(violations)} violation(s): {violations[0]} (+{max(0, len(violations) - 1)} more)",
            time.monotonic() - start,
        )
    return CheckResult(check.id, "PASS", "no hygiene violations", time.monotonic() - start)


# endregion CHECK_hygiene


# ═══════════════════════════════════════════════════════════════════
# region CHECK_commit_msg
def _check_commit_msg(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """commit-msg: последнее сообщение коммита — Conventional Commits (L3, non-blocking)."""
    rc, out, _, dur = _subprocess_run(
        ["git", "-C", str(project_dir), "log", "-1", "--format=%s"], project_dir, check.timeout_sec
    )
    if rc != 0 or not out.strip():
        return CheckResult(check.id, "PASS", "no commits yet", dur)
    message = out.strip()
    if _CONVENTIONAL_COMMIT_RE.match(message):
        return CheckResult(check.id, "PASS", f"conventional: {message[:60]}", dur)
    return CheckResult(check.id, "FAIL", f"not conventional commit: {message[:60]}", dur)


# endregion CHECK_commit_msg


# ═══════════════════════════════════════════════════════════════════
# region CHECK_compose_config
def _check_compose_config(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """compose-config: docker compose config --quiet (L2: warning baseline, блок active-full)."""
    from core.internal.shared.compose_files import PROJECT_COMPOSE_FILENAMES

    if not any((project_dir / name).is_file() for name in PROJECT_COMPOSE_FILENAMES):
        return CheckResult(check.id, "PASS", "no compose file", 0.0)
    if shutil.which("docker") is None:
        return CheckResult(check.id, "WARN", "docker not available — compose config skipped", 0.0)
    rc, out, err, dur = _subprocess_run(["docker", "compose", "config", "--quiet"], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "compose config valid", dur)
    return CheckResult(check.id, "FAIL", f"compose config invalid (rc={rc}): {_tail(err or out)}", dur)


# endregion CHECK_compose_config


# ═══════════════════════════════════════════════════════════════════
# region CHECK_ruff_format
def _check_ruff_format(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """ruff format --check (auto_fix: ruff format в --fix)."""
    if shutil.which("ruff") is None:
        return CheckResult(check.id, "WARN", "ruff not installed", 0.0)
    if fix:
        rc_fix, _, err_fix, _ = _subprocess_run(["ruff", "format", "."], project_dir, check.timeout_sec)
        if rc_fix not in (0, 1):
            return CheckResult(check.id, "WARN", f"ruff format failed (rc={rc_fix}): {_tail(err_fix)}", 0.0)
    rc, out, _, dur = _subprocess_run(["ruff", "format", "--check", "."], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "ruff format clean", dur)
    return CheckResult(
        check.id, "FAIL", f"{len(out.splitlines())} file(s) need formatting (run: make project-fix)", dur
    )


# endregion CHECK_ruff_format


# ═══════════════════════════════════════════════════════════════════
# region CHECK_shellcheck
def _check_shellcheck(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """shellcheck -S error по sh-файлам проекта (L3)."""
    sh_files = [p for p in _iter_project_files(project_dir) if p.suffix == ".sh"]
    if not sh_files:
        return CheckResult(check.id, "PASS", "no shell scripts", 0.0)
    if shutil.which("shellcheck") is None:
        return CheckResult(check.id, "WARN", "shellcheck not installed", 0.0)
    rc, out, err, dur = _subprocess_run(
        ["shellcheck", "-S", "error", *(str(p) for p in sh_files)], project_dir, check.timeout_sec
    )
    if rc == 0:
        return CheckResult(check.id, "PASS", f"{len(sh_files)} script(s) clean", dur)
    return CheckResult(check.id, "FAIL", f"shellcheck found issues (rc={rc}): {_tail(err or out)}", dur)


# endregion CHECK_shellcheck


# ═══════════════════════════════════════════════════════════════════
# region CHECK_pytest_baseline
def _pytest_timeout_available() -> bool:
    """True если pytest-timeout установлен (иначе --timeout в addopts ломает запуск).

    find_spec — без реального импорта (гейт test_core_imports_covered_by_pyproject:
    сторонние импорты обязаны быть в pyproject deps или allowlist)."""
    import importlib.util

    return importlib.util.find_spec("pytest_timeout") is not None


def _pytest_cmd(*, strict: bool) -> list[str]:
    """Build pytest command with controlled addopts (-o override).

    ## @purpose  Полный контроль addopts: GENERATED pyproject уже содержит --timeout=60
    ##           (и strict-флаги в full) — дублирование флага ломает pytest (rc=4). Через
    ##           `-o addopts=...` переопределяем ini-addopts детерминированно: --timeout
    ##           только если pytest-timeout установлен в ИНТЕРПРЕТАТОРЕ, strict-флаги только
    ##           для full. Команда: sys.executable -m pytest — интерпретатор совпадает с
    ##           find_spec-пробой (иначе pytest из PATH может не иметь pytest_timeout).
    ## @io       ⇥ strict: bool → ⎋ list[str] pytest-команда
    ## @complexity O(1)
    """
    opts_parts = ["-q", "-x"]
    if _pytest_timeout_available():
        opts_parts.append("--timeout=60")
    if strict:
        opts_parts.extend(["--strict-markers", "--strict-config"])
    return [sys.executable, "-m", "pytest", "-o", f"addopts={' '.join(opts_parts)}"]


def _check_pytest_baseline(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """pytest -q -x [--timeout=60] — если тесты есть; exit 5 (нет тестов) → PASS (allow_no_tests)."""
    if not (project_dir / "tests").is_dir():
        return CheckResult(check.id, "PASS", "no tests directory", 0.0)
    rc, out, err, dur = _subprocess_run(_pytest_cmd(strict=False), project_dir, check.timeout_sec)
    if rc == 5:  # no tests collected → PASS (allow_no_tests, §3.2)
        return CheckResult(check.id, "PASS", "no tests collected (allow_no_tests)", dur)
    if rc == 0:
        return CheckResult(check.id, "PASS", "pytest passed", dur)
    return CheckResult(check.id, "FAIL", f"pytest failed (rc={rc}): {_tail(err or out)}", dur)


# endregion CHECK_pytest_baseline


# ═══════════════════════════════════════════════════════════════════
# region CHECK_build
def _check_build(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """build: npm run build, если package.json с build-скриптом (L2)."""
    pkg = project_dir / "package.json"
    if not pkg.is_file():
        return CheckResult(check.id, "PASS", "no package.json", 0.0)
    try:
        import json

        scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, ValueError):
        return CheckResult(check.id, "WARN", "package.json unparseable", 0.0)
    if "build" not in scripts:
        return CheckResult(check.id, "PASS", "no build script in package.json", 0.0)
    if shutil.which("npm") is None:
        return CheckResult(check.id, "WARN", "npm not installed", 0.0)
    rc, out, err, dur = _subprocess_run(["npm", "run", "build"], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "npm run build passed", dur)
    return CheckResult(check.id, "FAIL", f"npm run build failed (rc={rc}): {_tail(err or out)}", dur)


# endregion CHECK_build


# ═══════════════════════════════════════════════════════════════════
# region CHECK_ruff_check
def _check_ruff_check(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """ruff check — полный набор правил (full-only, TRAP[DECISION] §10.2: НЕ в pre-commit).
    --select/--ignore передаются ЯВНО (канон из generators.RUFF_FULL_SELECT/IGNORE):
    CLI --select перекрывает select конфига, а ignore конфига НЕ применяется к CLI-селекту
    (детерминизм канона независимо от pyproject проекта)."""
    if shutil.which("ruff") is None:
        return CheckResult(check.id, "WARN", "ruff not installed", 0.0)
    select = ["--select", ",".join(RUFF_FULL_SELECT), "--ignore", ",".join(RUFF_FULL_IGNORE)]
    if fix:
        _subprocess_run(["ruff", "check", "--fix", *select, "."], project_dir, check.timeout_sec)
    rc, out, _, dur = _subprocess_run(["ruff", "check", *select, "."], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "ruff check clean (full rules)", dur)
    return CheckResult(check.id, "FAIL", f"{len(out.splitlines())} violation(s): {_tail(out)}", dur)


# endregion CHECK_ruff_check


# ═══════════════════════════════════════════════════════════════════
# region CHECK_pyright_eslint
def _check_pyright(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """pyright (full) — типы; tool missing → WARN."""
    if shutil.which("pyright") is None:
        return CheckResult(check.id, "WARN", "pyright not installed", 0.0)
    rc, out, err, dur = _subprocess_run(["pyright"], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "pyright clean", dur)
    return CheckResult(check.id, "FAIL", f"pyright (rc={rc}): {_tail(err or out)}", dur)


def _check_eslint(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """eslint (full) — ts/react; npx eslint . если package.json."""
    if not (project_dir / "package.json").is_file():
        return CheckResult(check.id, "PASS", "no package.json", 0.0)
    if shutil.which("npx") is None:
        return CheckResult(check.id, "WARN", "npx not installed", 0.0)
    rc, out, err, dur = _subprocess_run(["npx", "eslint", "."], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "eslint clean", dur)
    return CheckResult(check.id, "FAIL", f"eslint (rc={rc}): {_tail(err or out)}", dur)


# endregion CHECK_pyright_eslint


# ═══════════════════════════════════════════════════════════════════
# region CHECK_pytest_full
def _check_pytest_full(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """pytest-full: strict-маркеры + strict-config (full)."""
    if not (project_dir / "tests").is_dir():
        return CheckResult(check.id, "PASS", "no tests directory", 0.0)
    rc, out, err, dur = _subprocess_run(_pytest_cmd(strict=True), project_dir, check.timeout_sec)
    if rc == 5:
        return CheckResult(check.id, "PASS", "no tests collected (allow_no_tests)", dur)
    if rc == 0:
        return CheckResult(check.id, "PASS", "pytest (strict) passed", dur)
    return CheckResult(check.id, "FAIL", f"pytest strict failed (rc={rc}): {_tail(err or out)}", dur)


# endregion CHECK_pytest_full


# ═══════════════════════════════════════════════════════════════════
# region CHECK_grep_summary
def _check_grep_summary(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """grep-summary: GREP_SUMMARY в первых 10 строках файлов кода (full)."""
    missing: list[str] = []
    for path in _iter_code_files(project_dir):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                head = "".join(f.readline() for _ in range(10))
        except OSError:
            continue
        if "GREP_SUMMARY" not in head:
            missing.append(str(path.relative_to(project_dir)))
    if missing:
        return CheckResult(
            check.id,
            "FAIL",
            f"{len(missing)} file(s) missing GREP_SUMMARY: {missing[0]} (+{max(0, len(missing) - 1)})",
            0.0,
        )
    return CheckResult(check.id, "PASS", "GREP_SUMMARY present in code files", 0.0)


# endregion CHECK_grep_summary


# ═══════════════════════════════════════════════════════════════════
# region CHECK_drift_gate
def _check_drift_gate(check: Any, project_dir: Path, *, fix: bool) -> CheckResult:
    """drift-gate: practices.lock version + file-level drift (lock.files vs disk) + canon-hash
    против актуального канона (L2). auto_fix (--fix) → перегенерация sync_practices (repair).
    Дрейф-детект: (1) lock.version < canon — версия устарела; (2) диск GENERATED-файла
    расходится с lock.files hash (ручная правка); (3) lock.generator_hash != актуальный
    канон-рендер (канон изменился с момента sync). Любой → make project-sync-practices."""
    manifest = load_manifest()
    lock = read_lock(project_dir)
    if lock is None:
        if fix:
            from core.internal.practices.sync_practices import sync_practices

            sync_practices(project_dir, force=True)
            return CheckResult(check.id, "PASS", "practices.lock created via project-sync-practices", 0.0)
        return CheckResult(check.id, "WARN", "practices.lock missing — run: make project-sync-practices", 0.0)

    if lock.version < manifest.version:
        if fix:
            from core.internal.practices.sync_practices import sync_practices

            sync_practices(project_dir, force=True)
            return CheckResult(check.id, "PASS", "practices.lock version updated via project-sync-practices", 0.0)
        return CheckResult(
            check.id,
            "FAIL",
            f"practices.lock version {lock.version} < canon {manifest.version} — run: make project-sync-practices",
            0.0,
        )

    # ── file-level drift: диск GENERATED-файла vs hash в lock (ручная правка) ──
    drifted: list[str] = []
    for rel, expected_hash in lock.files.items():
        path = project_dir / rel
        if not path.is_file():
            drifted.append(f"{rel} (missing)")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if GENERATED_HEADER not in content:
            continue  # ручной файл — вне дрейф-гейта (sync его пропустит)
        actual = f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
        if actual != expected_hash:
            drifted.append(f"{rel} (modified)")

    # ── canon-hash: lock устарел относительно актуального рендера канона ──
    data = load_project_yaml(project_dir)
    project_name = get_name(data) or project_dir.name
    languages = resolve_language(project_dir)
    language = languages[0] if languages else "python"
    files = render_project_files(project_name, language, lock.level, manifest.pins)
    expected_canon = compute_generator_hash(files, manifest.version, lock.level)
    if lock.generator_hash != expected_canon:
        drifted.append("practices.lock (canon stale)")

    if drifted:
        if fix:
            from core.internal.practices.sync_practices import sync_practices

            sync_practices(project_dir, force=True)
            return CheckResult(check.id, "PASS", "GENERATED files regenerated via project-sync-practices", 0.0)
        detail = drifted[0] + (f" (+{len(drifted) - 1} more)" if len(drifted) > 1 else "")
        return CheckResult(check.id, "FAIL", f"practices drift: {detail} — run: make project-sync-practices", 0.0)
    return CheckResult(check.id, "PASS", "practices.lock in sync with canon", 0.0)


# endregion CHECK_drift_gate


# ── Регистрация обработчиков ──
_HANDLERS.update(
    {
        "gitleaks": _check_gitleaks,
        "hygiene": _check_hygiene,
        "commit-msg": _check_commit_msg,
        "compose-config": _check_compose_config,
        "ruff-format": _check_ruff_format,
        "shellcheck": _check_shellcheck,
        "pytest-baseline": _check_pytest_baseline,
        "build": _check_build,
        "ruff-check": _check_ruff_check,
        "pyright": _check_pyright,
        "eslint": _check_eslint,
        "pytest-full": _check_pytest_full,
        "grep-summary": _check_grep_summary,
        "drift-gate": _check_drift_gate,
    }
)


# ═══════════════════════════════════════════════════════════════════
# region HELPERS_iter_files
def _iter_project_files(project_dir: Path):
    """Iterate project files skipping excluded dirs (node_modules/.venv/...)."""
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        for name in files:
            yield Path(root) / name


def _iter_text_files(project_dir: Path):
    """Iterate text files (known text extensions) for hygiene scan."""
    for path in _iter_project_files(project_dir):
        # docker-compose.yml покрыт .yml расширением — отдельный литерал запрещён
        # (гейт compose_files_sole_path: compose-имена только из shared/compose_files)
        if path.suffix.lower() in _TEXT_EXTENSIONS or path.name in ("Dockerfile", "Makefile"):
            yield path, True


def _iter_code_files(project_dir: Path):
    """Iterate code files (py/ts/tsx/js/jsx/sh) for grep-summary scan."""
    for path in _iter_project_files(project_dir):
        if path.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx", ".sh"}:
            yield path


def _parse_structured(path: Path) -> bool:
    """Validate TOML/JSON syntax of a file."""
    try:
        if path.suffix == ".json":
            import json

            json.loads(path.read_text(encoding="utf-8"))
        else:
            import tomllib

            tomllib.loads(path.read_text(encoding="utf-8"))
        return True
    except (OSError, ValueError):
        return False


def _auto_fix_hygiene(project_dir: Path) -> None:
    """Auto-fix trailing whitespace + CRLF + final newline (in-place, best-effort)."""
    from core.internal.shared.atomic_writer import atomic_write_text

    for path, _ in _iter_text_files(project_dir):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fixed = content.replace("\r\n", "\n")
        lines = [line.rstrip(" \t") for line in fixed.split("\n")]
        while lines and lines[-1] == "":
            lines.pop()
        fixed = "\n".join(lines) + "\n"
        if fixed != content:
            atomic_write_text(path, fixed)


def _tail(text: str, limit: int = 200) -> str:
    """First/last snippet of command output for messages (bounded)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


# endregion HELPERS_iter_files


# region FUNC_format_report
## @purpose  Форматирование [PRACTICES:...] вывода для агента (stdout): state-строка,
##           варнинг эскалатора (PROPOSE), по одной строке на проверку, exit-сводка.
## @io       ⇥ report: CheckReport → ⎋ str
## @complexity O(R)
def format_report(report: CheckReport) -> str:
    """Render [PRACTICES:...] report lines (agent-visible)."""
    lines: list[str] = [f"[PRACTICES:STATE][{report.state}][level:{report.level_setting}]"]
    lines.extend(report.warnings)
    lines.extend(
        f"[PRACTICES:CHECK][{r.check_id}] {r.status} ({r.duration_s:.1f}s) — {r.message}" for r in report.results
    )
    lines.append(f"[PRACTICES:RESULT] exit={report.exit_code} ({len(report.results)} checks)")
    return "\n".join(lines)


# endregion FUNC_format_report


# region FUNC_main
## @purpose  CLI entry point: python3 -m core.internal.practices.check_project
##           --project-dir DIR [--level baseline|full|auto] [--fix].
## @io       stdout: [PRACTICES:...] report; stderr: LDD logs
## @exitcode 0 — зелёный; 1 — L1-блок или L2/L3-блок в active-full; 4 — ConfigValidationError
def main(argv: list[str] | None = None) -> int:
    """CLI for project-check / project-fix (exit 0/1/4)."""
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
        format="[%(levelname)s][practices_check] %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="Check project practices (K1 local channel)")
    parser.add_argument("--project-dir", required=True, type=str, help="Project directory to check")
    parser.add_argument("--level", type=str, default="", help="Override level: baseline | full | auto")
    parser.add_argument("--fix", action="store_true", help="Apply auto-fix checks (project-fix)")
    args = parser.parse_args(argv)

    try:
        report = check_project(Path(args.project_dir), level=args.level or None, fix=args.fix)
    except ConfigValidationError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG_VALIDATION
    except PlatformError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return exc.exit_code

    print(format_report(report))
    logger.info("[IMP:9][check_project][main] exit=%d", report.exit_code)
    return report.exit_code


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
