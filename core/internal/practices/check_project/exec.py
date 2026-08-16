# GREP_SUMMARY: check-project-exec, dispatch, run_check, subprocess-run, timeout, tail, handlers-registry, exit-codes
# STRUCTURE: ┌HANDLERS: dict[id→handler]┐ → ▶ run_check(check, project_dir, fix, facts) → ◇ handler найден? → ⊕ handler(...) → ⎋ CheckResult (SKIP для неизвестного id)
# region MODULE_CONTRACT
## @purpose  Исполнительный слой check_project (DevPlan 170 W10-A декомпозиция): диспетчер
##           run_check (kebab-case id канона → Python-функция через реестр HANDLERS),
##           безопасный subprocess_run (таймаут, НЕ кидает на ненулевом rc — возвращает
##           (rc, stdout, stderr, duration); FileNotFoundError → 127, TimeoutExpired → 124),
##           tail (ограниченный сниппет вывода для сообщений). Реестр HANDLERS наполняется
##           в checks/__init__.py (регистрация handler-ов при импорте пакета check_project).
## @scope    Потребители: runner.py (диспетчеризация каждой проверки), checks/*.py
##           (subprocess_run/tail для CLI-инструментов), checks/__init__.py (реестр),
##           tests/unit/test_practices_check_project.py (run_check напрямую).
## @invariants
##   - Неизвестный check.id → SKIP «no local handler» (0.0s) — НЕ исключение
##   - subprocess_run НЕ кидает: rc 127 (нет команды) / 124 (таймаут) / 1..N (команда)
##   - exit-коды контракта (0/1/4) — НЕ в exec.py: расчёт в runner._compute_exit_code
##     (shared/contracts.py), ConfigValidationError → 4 в cli.main
##   - facts: EnvironmentFacts | None (DI-канал W4b — which-проверки инструментов)
## @rationale  Диспетчер + subprocess-инфраструктура — общий слой 18 handler-ов; разделение
##             с реестром (checks/__init__) разрывает цикл exec↔checks (checks импортирует
##             exec-хелперы, exec не импортирует handler-и).
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:308-348)
# endregion MODULE_CONTRACT

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from core.internal.practices.check_project.models import CheckResult
from core.internal.practices.manifest import PracticeCheck
from core.internal.shared.env_facts import EnvironmentFacts

# ═══════════════════════════════════════════════════════════════════
# region FUNCrun_check (dispatch)
## @purpose  Диспетчер исполнения одной проверки: handler по id (kebab-case → функция).
## @io       ⇥ check, project_dir, fix, facts → ⎋ CheckResult
## @complexity O(1) + handler
HANDLERS: dict[str, Callable[..., CheckResult]] = {}
CMD_NOT_FOUND_RC: int = 127  # shell: команда не найдена (gitleaks unavailable → WARN)
PYTEST_NO_TESTS_RC: int = 5  # pytest: тесты не собраны → PASS (allow_no_tests)


def run_check(
    check: PracticeCheck, project_dir: Path, *, fix: bool, facts: EnvironmentFacts | None = None
) -> CheckResult:
    """Dispatch single check to its local handler (unknown id → SKIP)."""
    handler = HANDLERS.get(check.id)
    if handler is None:
        return CheckResult(check.id, "SKIP", f"no local handler for check '{check.id}'", 0.0)
    return handler(check, project_dir, fix=fix, facts=facts)


# endregion FUNCrun_check


# ═══════════════════════════════════════════════════════════════════
# region FUNC_subprocess_run
## @purpose  Безопасный subprocess.run с таймаутом (для CLI-проверок). НЕ кидает при
##           ненулевом rc — возвращает (rc, stdout, stderr, duration).
## @io       ⇥ cmd, cwd, timeout → ⎋ tuple[int, str, str, float]
## @complexity O(1)
def subprocess_run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, str, float]:
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


# endregion FUNC_subprocess_run


# region FUNC_tail
## @purpose  Ограниченный сниппет вывода для сообщений результата (bounded, с многоточием).
## @io       ⇥ text: str, limit: int → ⎋ str — ≤limit символов (strip + …)
## @complexity O(L) — длина text
def tail(text: str, limit: int = 200) -> str:
    """First/last snippet of command output for messages (bounded)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


# endregion FUNC_tail
