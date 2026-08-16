"""
# GREP_SUMMARY: check-suite, runner, subprocess, run-cmd, xdist, memory-guard, docker-lock, resolve-tokens, retry-once, allow-no-tests
# STRUCTURE: ▶ resolve_command_tokens (.venv) → ◇ has_xdist → ◇ xdist_worker_count (memory-guard) → ◇ apply_xdist (-n N) → ◇ apply_project_filter (-k) → ◇ docker_suite_lock (flock) → ○ run_cmd ┌timeout→killpg┐ → ○ run_pytest_check ┌dedup: xdist+docker-lock+allow_no_tests┐ → ○ run_retry_once ┌fix/gate pre-commit retry┐ → ⎋ CheckOutcome
# region MODULE_CONTRACT
## @purpose  Командный слой пакета check_suite (DevPlan 170 W3 — извлечено из монолита
##           core/internal/check_suite.py): резолв исполняемых в .venv, xdist-применение
##           (-n auto), project-фильтр (-k), процессный docker-лок (flock), исполнение
##           subprocess с таймаут-киллом process-group, плюс ДВА новых дедуп-хелпера:
##           run_pytest_check (pytest-цикл из run_diagnostic/run_gate/run_single) и
##           run_retry_once (pre-commit retry-once из fix-фазы и gate).
## @scope    core/internal/check_suite/runner.py — stdlib-only. Потребители: diagnostic.py,
##           gate.py, single.py, diff.py.
## @invariants
##   - run_cmd НЕ бросает исключений: timeout → exit 124, FileNotFoundError → exit 127
##   - docker_lock=True → команда оборачивается в docker_suite_lock (flock, DevPlan 124 T2c)
##   - apply_xdist: -n ТОЛЬКО ПРЯМЫМ pytest-командам при spec.xdist и наличии xdist;
##     TEST_NO_XDIST=1 отключает; число воркеров — xdist_worker_count (memory-guard:
##     min(cpu, free_gb), CHECK_XDIST_MAX_WORKERS — жёсткий потолок); полная память → -n auto
##     (семантика прежняя); shlex.join — повторная склейка с кавычками
##   - run_pytest_check/run_retry_once — семантика 1:1 с прежними inline-циклами
##   - monkeypatch-контракт: run_cmd/has_xdist/docker_suite_lock резолвятся через пакетную
##     атрибуцию (check_suite.X) НА МОМЕНТ ВЫЗОВА — DI-HYG тестов
## @rationale Дедуп pytest-цикла (3 места: diagnostic 1028-1044, gate 1186-1223, single 1383-1395)
##            и retry-once (fix 895-901, gate 1198-1204) — механическая консолидация с семантикой
##            1:1 (research-A §1). Late-binding через пакет — требование совместимости
##            monkeypatch-контракта тестов (check_suite.X), не рост DI.
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package); 170 private-imports:
##           приватные имена переименованы в публичные (U-07)
##           v1.0.1 (0.8) — xdist memory-guard: TRAP[BUG] OOM на dev-машине (16 GB, 12 CPU):
##           pytest-xdist -n auto = 12 воркеров + Docker Desktop → зависание macOS
##           (2 ребута 2026-08-14/15 при pre-push gate). Фикс: xdist_worker_count =
##           min(cpu, свободная_память_GB) + env CHECK_XDIST_MAX_WORKERS; при достаточной
##           памяти → -n auto (поведение CI неизменно). psutil — runtime-зависимость.
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from core.internal import check_suite as cs
from core.internal.check_suite.models import CheckOutcome, CheckSpec

logger = logging.getLogger(__name__)


# region COMMAND_EXEC

# Исполняемые, резолвящиеся в .venv при наличии (команды манифеста пишутся нейтрально:
# pytest/pre-commit/ruff/python3 — на машине разработчика живут в .venv)
_VENV_RESOLVABLE = ("pytest", "pre-commit", "ruff", "python3")

_PYTEST_NO_TESTS_RC: int = 5  # pytest exit 5 = тесты не собраны → PASS (allow_no_tests)


# region FUNC_resolve_command
## @purpose  Резолв исполняемых в .venv (pytest/pre-commit/ruff/python3) — команды манифеста
##           нейтральны, на машине разработчика исполняемые живут в .venv (Makefile использует
##           $(PYTHON) = .venv/bin/python). System python3 может не иметь pytest-зависимостей.
## @io       ⇥ tokens: list[str] (shlex-разбор команды), root: Path → list[str] (токены с резолвом)
## @complexity O(1)
def _resolve_command_tokens(tokens: list[str], root: Path) -> list[str]:
    """Resolve venv executables (pytest/pre-commit/ruff/python3) when present."""
    if not tokens:
        return tokens
    if tokens[0] in _VENV_RESOLVABLE:
        venv_bin = root / ".venv" / "bin" / tokens[0]
        if venv_bin.is_file():
            return [str(venv_bin), *tokens[1:]]
    return tokens


# endregion FUNC_resolve_command


# region FUNC_has_xdist
## @purpose  Проверка доступности pytest-xdist для venv-интерпретатора (дубль из прежнего
##           (DevPlan 120 §3.3: «перенос в shared или локальный дубль»).
## @io       ⇥ python_path: str → bool
## @complexity O(1) — subprocess python -c "import xdist"
def has_xdist(python_path: str) -> bool:
    """Best-effort availability check for pytest-xdist."""
    try:
        result = subprocess.run([python_path, "-c", "import xdist"], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):  # noqa: EXC — best-effort check
        return False
    else:
        return result.returncode == 0


# endregion FUNC_has_xdist


# region FUNC_memory_available_bytes
## @purpose  Доступная RAM в байтах через psutil (runtime-зависимость v1.0.1 0.8);
##           None при отсутствии psutil — best-effort fallback на cpu-only.
##           Отдельная функция — DI-HYG seam для детерминированных тестов apply_xdist.
## @io       → int | None
## @complexity O(1)
def memory_available_bytes() -> int | None:
    """Best-effort available memory; None when psutil is not installed."""
    try:
        import psutil
    except ImportError:  # noqa: EXC — best-effort guard: без psutil — прежняя семантика -n auto
        return None
    vm = psutil.virtual_memory()
    # cast: psutil 7.2.2 в этом окружении без .pyi-стабов → reportAny на vm.available;
    # граница нетипизируемого третьестороннего модуля (канон 170 W11 «cast с причинами»)
    return cast(int, vm.available)


# endregion FUNC_memory_available_bytes


# region FUNC_xdist_worker_count
## @purpose  Memory-guard для pytest-xdist (v1.0.1 0.8): число воркеров = min(cpu,
##           floor(свободная_память_GB)), опциональный жёсткий потолок. OOM-инциденты
##           2026-08-14/15: -n auto (12 CPU) на dev-машине с занятой Docker памятью →
##           зависание macOS. Чистая функция — тестируется без psutil/mock.
## @io       ⇥ available_bytes: int (vm.available), cpu_count: int | None,
##             hard_cap: int | None (env CHECK_XDIST_MAX_WORKERS) → int (≥1)
## @complexity O(1)
def xdist_worker_count(available_bytes: int, cpu_count: int | None, hard_cap: int | None = None) -> int:
    """Cap pytest-xdist workers by free memory and CPU; 1 worker per ~1 GiB available."""
    cpu = max(1, cpu_count or 1)
    mem_based = max(1, available_bytes // (1024**3))
    n = min(cpu, mem_based)
    if hard_cap is not None and hard_cap >= 1:
        n = min(n, hard_cap)
    return n


# endregion FUNC_xdist_worker_count


# region FUNC_apply_xdist
## @purpose  Применение -n к ПРЯМЫМ pytest-командам (первый токен pytest) при
##           spec.xdist и доступности xdist; TEST_NO_XDIST=1 отключает. Число воркеров —
##           xdist_worker_count (memory-guard): полная память → -n auto (прежняя семантика),
##           дефицит — -n \<count\> с IMP:8-логом; CHECK_XDIST_MAX_WORKERS — жёсткий потолок.
##           `make test MARKER=...` и test_runner-команды НЕ трогаются (xdist внутри
##           test_runner, Wave 1).
## @io       ⇥ cmd_str: str, spec: CheckSpec, root: Path → str (модифицированная команда)
## @complexity O(T) где T = токены
## @rationale DevPlan §3.1 xdist: true на gates/contract/static_audit/predeploy/smoke/component;
##            §3.3 — test_runner получает -n auto; добавление -n к `make ...` сломало бы make.
def apply_xdist(cmd_str: str, spec: CheckSpec, root: Path) -> str:
    """Insert `-n auto|N` after `pytest` for direct pytest commands when xdist enabled."""
    tokens = shlex.split(cmd_str)
    if not tokens or tokens[0] != "pytest":
        return cmd_str
    if not spec.xdist:
        return cmd_str
    if os.environ.get("TEST_NO_XDIST") == "1":
        return cmd_str
    venv_python = root / ".venv" / "bin" / "python"
    python_path = str(venv_python) if venv_python.is_file() else sys.executable
    if not cs.has_xdist(python_path):  # late-binding: monkeypatch-контракт (DI-HYG)
        return cmd_str
    # Memory-guard (v1.0.1 0.8): воркеры по свободной памяти (memory_available_bytes);
    # psutil отсутствует → fallback на cpu (прежнее -n auto).
    hard_cap: int | None = None
    if os.environ.get("CHECK_XDIST_MAX_WORKERS"):
        hard_cap = int(os.environ["CHECK_XDIST_MAX_WORKERS"])
    avail = cs.memory_available_bytes()  # late-binding: monkeypatch-контракт (DI-HYG)
    cpu = os.cpu_count() or 1
    if avail is None:
        n_workers = cpu
        if hard_cap is not None:
            n_workers = min(n_workers, hard_cap)
        tokens[1:1] = ["-n", "auto" if n_workers >= cpu else str(n_workers)]
        logger.info("[IMP:8][apply_xdist][resolve] %s → -n %s (psutil отсутствует)", spec.id, n_workers)
    else:
        n_workers = xdist_worker_count(avail, cpu, hard_cap)
        if n_workers >= cpu:
            # Полная память → прежняя семантика -n auto (CI-паритет, DevPlan 120 §3.3)
            tokens[1:1] = ["-n", "auto"]
            logger.info(
                "[IMP:8][apply_xdist][resolve] %s → -n auto (memory-guard: %d >= cpu %d)", spec.id, n_workers, cpu
            )
        else:
            tokens[1:1] = ["-n", str(n_workers)]
            logger.info(
                "[IMP:8][apply_xdist][resolve] %s → -n %d (memory-guard: cpu=%d, free=%d GiB)",
                spec.id,
                n_workers,
                cpu,
                avail // (1024**3),
            )
    # -n ПЕРЕД -m (DevPlan §3.3); pytest допускает любую позицию, но конвенция — после pytest
    # ⚠️ shlex.join (НЕ " ".join): исходные кавычки -m-выражения ("gate and not requires_docker")
    # уже сняты shlex.split — повторная склейка без кавычек ломала выражение → exit 5 (0 тестов)
    return shlex.join(tokens)


# endregion FUNC_apply_xdist


# region FUNC_apply_project_filter
## @purpose  PROJECT=\<name\> → -k \<name\> для project_filter-чеков (predeploy). Паритет ci.mk:
##           -k применялся ТОЛЬКО к прямой pytest-команде fast-predeploy, не к make test.
## @io       ⇥ cmd_str: str, project: str | None → str
## @complexity O(T)
def apply_project_filter(cmd_str: str, project: str | None) -> str:
    """Append `-k <project>` for direct pytest commands when project_filter is set."""
    if not project:
        return cmd_str
    tokens = shlex.split(cmd_str)
    if tokens and tokens[0] == "pytest":
        return f"{cmd_str} -k {shlex.quote(project)}"
    return cmd_str


# endregion FUNC_apply_project_filter


# region FUNC_docker_suite_lock
## @purpose  Процессный advisory flock на tests/.docker-suite.lock (DevPlan 124 T2c) —
##           зеркало test_runner.docker_suite_lock: ЕДИНЫЙ lock-файл для ВСЕХ
##           docker-pytest-процессов на машине (test_runner и check_suite). Два агента,
##           одновременно гоняющих docker-чеки, НЕ пересекаются по compose-стеку (F4).
##           Реализация fcntl.flock (прецедент counter.py, DevPlan 120 §3.3) вместо
##           flock-CLI (отсутствует на macOS; stdlib-only инвариант).
## @io       ⇥ root: Path → contextmanager (lock удерживается внутри with)
## @complexity O(1)
# ⚠️ TRAP[DECISION] · 2026-08-03 · — · docker-лок check_suite: fcntl-зеркало test_runner
# · Rejected: shell-префикс `flock tests/.docker-suite.lock` к команде чека (текст DevPlan
# ·   124 T2c) — flock-CLI отсутствует на macOS (`which flock` → not found, 2026-08-03);
# ·   prefix-подход не удержал бы лок при timeout-киле subprocess (flock-ребёнок остался бы)
# · Reason: in-process fcntl.flock вокруг subprocess.run держит лок ровно на время исполнения
# ·   команды и безусловно освобождается в finally/при завершении процесса; единый lock-файл
# ·   tests/.docker-suite.lock общий с test_runner (T2c: «Единый lock-файл для всех процессов»)
# · Rev: при появлении shell-потребителя лока — вынести в shared-модуль с CLI.
@contextlib.contextmanager
def docker_suite_lock(root: Path):
    """Context manager holding the process-level docker-suite flock (mirror of test_runner)."""
    import fcntl  # lazy — POSIX-only (darwin/linux)

    lock_path = root / "tests" / ".docker-suite.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("[IMP:8][docker_lock][acquire] flock held: %s", lock_path)
    with Path(lock_path).open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            logger.info("[IMP:8][docker_lock][release] flock released: %s", lock_path)


# endregion FUNC_docker_suite_lock


# region FUNC_run_cmd
## @purpose  Исполнение команды чека: subprocess с таймаутом, cwd=root, env; timeout → exit 124;
##           FileNotFoundError → exit 127. docker_lock=True → команда оборачивается в
##           docker_suite_lock (docker-чеки сериализуются межсессионно, DevPlan 124 T2c).
##           НЕ бросает исключений — caller собирает результат.
## @io       ⇥ cmd_str: str, timeout: int, env: dict, root: Path,
##             docker_lock: bool (spec.docker: true) → CheckOutcome
## @complexity O(1) + время subprocess
def run_cmd(
    cmd_str: str,
    timeout: int,
    env: dict[str, str],
    root: Path,
    docker_lock: bool = False,
) -> CheckOutcome:
    """Run a single check command; never raises on check failure."""
    tokens = _resolve_command_tokens(shlex.split(cmd_str), root)
    start = time.monotonic()
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        with cs.docker_suite_lock(root) if docker_lock else contextlib.nullcontext():  # late-binding: DI-HYG
            # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — таймаут-килл оставлял орфанов
            # · Symptom: static_audit >300s → subprocess.run(timeout) убивал ТОЛЬКО pytest-родителя;
            # ·   xdist-воркеры/дети осиротевали и ПРОДОЛЖАЛИ мутировать tests/ (junitxml, __pycache__)
            # ·   → последующий doxygen-check парсил дерево во время мутаций → 46 «unexpanded alias»
            # ·   (flex-баг 1.17.0) → gate/check флакали; орфан жил часами.
            # · Fix: start_new_session + killpg при TimeoutExpired (весь process-group).
            proc = subprocess.Popen(
                tokens,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(root),
                env=env,
                start_new_session=True,
            )
            try:
                proc_stdout, proc_stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                duration = (time.monotonic() - start) * 1000
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    logger.warning(
                        "[IMP:7][run_cmd][timeout] %s TIMEOUT after %ds — killed process group %d",
                        cmd_str[:80],
                        timeout,
                        proc.pid,
                    )
                except (ProcessLookupError, PermissionError):
                    pass  # процесс уже мёртв
                proc.wait()
                return CheckOutcome(
                    name=tokens[0] if tokens else "?",
                    exit_code=124,
                    stderr=f"Timeout after {timeout}s",
                    duration_ms=duration,
                )
        duration = (time.monotonic() - start) * 1000
        logger.info(
            "[IMP:8][run_cmd][exec] %s → exit=%d (%.1fs)",
            " ".join(tokens)[:120],
            proc.returncode,
            duration / 1000,
        )
        return CheckOutcome(
            name=tokens[0],
            exit_code=proc.returncode,
            stdout=proc_stdout,
            stderr=proc_stderr,
            duration_ms=duration,
        )
    except FileNotFoundError:
        duration = (time.monotonic() - start) * 1000
        logger.error("[IMP:9][run_cmd][missing] Command not found: %s", tokens[0] if tokens else "?")
        return CheckOutcome(
            name=tokens[0] if tokens else "?",
            exit_code=127,
            stderr=f"Command not found: {tokens[0] if tokens else '?'}",
            duration_ms=duration,
        )


# endregion FUNC_run_cmd


# region FUNC_run_pytest_check## @purpose  ОБЩИЙ pytest-цикл (дедуп DevPlan 170 W3 из run_diagnostic:1028-1044,
##           run_gate:1186-1223, run_single:1383-1395): xdist-применение → (gate-only)
##           project-фильтр → docker-лок → исполнение → allow_no_tests (rc=5 → PASS).
##           Семантика 1:1 с прежними inline-циклами.
## @io       ⇥ spec: CheckSpec, cmd_str: str (уже резолвленная команда), timeout: int,
##             env: dict, root: Path, project: str | None, log_tag: str (префикс IMP-сообщения)
##           → ⎋ CheckOutcome (passed_no_tests выставлен при allow_no_tests и rc=5)
## @complexity O(1) + время subprocess
def run_pytest_check(
    spec: CheckSpec,
    cmd_str: str,
    timeout: int,
    env: dict[str, str],
    root: Path,
    project: str | None = None,
    log_tag: str = "check",
) -> CheckOutcome:
    """Run one pytest-style check with xdist/project/docker-lock/allow_no_tests applied."""
    cmd_str = apply_xdist(cmd_str, spec, root)
    if project and spec.project_filter:
        cmd_str = apply_project_filter(cmd_str, project)
    # DevPlan 124 T2c: docker-чеки (spec.docker: true — gates-docker/predeploy-docker)
    # — под процессным локом (межсессионная сериализация docker-стека, F4)
    r = cs.run_cmd(cmd_str, timeout, env, root, docker_lock=spec.docker)  # late-binding: DI-HYG
    if spec.allow_no_tests and r.exit_code == _PYTEST_NO_TESTS_RC:
        r.passed_no_tests = True
        print(f"[IMP:8][{log_tag}] {spec.id}: 0 тестов (rc=5) → PASS (allow_no_tests)", file=sys.stderr)
    return r


# endregion FUNC_run_pytest_check


# region FUNC_run_retry_once
## @purpose  Единый retry-once helper (дедуп DevPlan 170 W3 из fix-фазы:895-901 и
##           gate pre-commit:1198-1204). Принимает ПЕРВУЮ попытку; при выполнении
##           retry_condition исполняет вторую. Паритет fix-фазы: успешный повтор →
##           auto_fixed (mark_auto_fixed), двойной провал → первая неудача
##           (keep_first_on_failure=True). Паритет gate: повтор заменяет результат
##           (keep_first_on_failure=False), условие — маркер «files were modified».
## @io       ⇥ first: CheckOutcome, cmd_str, timeout, env, root, docker_lock,
##             retry_condition: Callable[[CheckOutcome], bool],
##             retry_message: str, mark_auto_fixed: bool, keep_first_on_failure: bool
##           → ⎋ CheckOutcome
## @complexity O(1) + 2× subprocess
def run_retry_once(
    first: CheckOutcome,
    cmd_str: str,
    timeout: int,
    env: dict[str, str],
    root: Path,
    docker_lock: bool = False,
    *,
    retry_condition: Callable[[CheckOutcome], bool],
    retry_message: str,
    mark_auto_fixed: bool = False,
    keep_first_on_failure: bool = False,
) -> CheckOutcome:
    """Retry-once: re-run after a failed first attempt when retry_condition holds."""
    if first.passed:
        return first
    if not retry_condition(first):
        return first
    print(retry_message, file=sys.stderr)
    second = cs.run_cmd(cmd_str, timeout, env, root, docker_lock=docker_lock)  # late-binding: DI-HYG
    if mark_auto_fixed and second.passed:
        second.auto_fixed = True
        return second
    return first if keep_first_on_failure else second


# endregion FUNC_run_retry_once

# endregion COMMAND_EXEC
