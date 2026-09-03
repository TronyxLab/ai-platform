"""
# GREP_SUMMARY: test-converge flock fcntl lock reconciler-dispatch reconcile-dispatch exit-codes dry-run no-mutate node-yaml-resolve W3.5-1 post-reconcile-reload F6
# STRUCTURE: ▶ importlib load converge.py (shadowed by converge/ package) → FakeDispatch/FakeLock/FakeResolve/FakeReload → ◇ parse_args → ◇ build_*_cmd (argv) → ◇ main (exit 0/1/2/3, dry-run no-lock, reconcile upgrade, F6 post-reconcile reload) → ◇ acquire_flock (real fcntl conflict) → ⊕ LDD IMP:9 → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit-тесты converge.py (DevPlan 164 W3.5-1, SH→Python converge.sh): парсинг аргументов,
##           резолв node.yaml (FakeResolve), flock-семантика (dry-run/report-only SKIP; конфликт → 3),
##           диспатч argv в converge/reconciler.py и reconciler_projects.py (FakeDispatch), exit-
##           маппинг {0,1,2} + reconcile-failure upgrade. Модуль загружается importlib'ом
##           (spec_from_file_location) — имя converge.py перекрыто существующим пакетом converge/
##           (FileFinder приоритет пакета), прямой импорт невозможен.
## @scope    tests/unit/test_converge.py — native imports, tmp_path (node.yaml фикстура),
##           Fake-объекты DI (ноль monkeypatch, W4c). Реальные flock-тесты — fcntl на tmp_path.
## @invariants
##   - FakeDispatch/FakeLock/FakeResolve — конструкторная DI; ни одного реального subprocess
##   - node.yaml — реальный файл tmp_path (main проверяет is_file, как shell setup_environment)
##   - Каждая тест-функция: # 🧪 TRAP[TEST] + LDD IMP:9 траектория (@ldd_trajectory)
##   - acquire_flock — реальный fcntl (macOS/Linux семантика per-open-file-description)
## @rationale  W3.5-1: оркестрация converge.sh переехала в Python — тестируем args/dispatch/exit
##            через DI без Docker и без ноды; flock — реальный syscall на tmp_path (точная
##            семантика конфликта двух fd в одном процессе).
## @changes 2026-08-14 · DevPlan 164 W3.5-1 — Created
## @changes 2026-09-03 · F6 (DevPlan 031 T5) — +FakeReload +3 теста post-reconcile nginx reload
##           (rc=0 → reload 1×; rc=2 → reload SKIP — NEGATIVE R5 FINDING-p3-3; dry-run → SKIP)
## @links   core/internal/bootstrap/converge.py, converge/reconciler.py,
##          core/internal/reconciler_projects.py, tests/test_project_scaffold.py (subprocess-контур)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import fcntl
import importlib.util
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from core.internal.shared.exceptions import ConfigNotFoundError
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

# ── Загрузка converge.py (имя перекрыто пакетом converge/ — importlib, см. TRAP[DECISION] в модуле) ──
_CONVERGE_PY = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge.py"


def _load_converge_cli():
    """Загрузить core/internal/bootstrap/converge.py под именем bootstrap_converge_cli.

    ## @purpose — Прямой `import core.internal.bootstrap.converge` резолвит ПАКЕТ converge/
    ##            (FileFinder приоритет __init__.py над .py того же имени). importlib с
    ##            spec_from_file_location исполняет файл под отдельным именем — модульные
    ##            импорты (core.internal.shared.*) резолвятся штатно.
    """
    spec = importlib.util.spec_from_file_location("bootstrap_converge_cli", _CONVERGE_PY)
    assert spec is not None and spec.loader is not None, f"converge.py не загружен: {_CONVERGE_PY}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


converge = _load_converge_cli()


# ── Fakes (W4c конструкторная DI — ноль monkeypatch) ──


class FakeDispatch:
    """DispatchFn fake: запись argv + фиксированная очередь returncode."""

    def __init__(self, *rcs: int) -> None:
        self.calls: list[list[str]] = []
        self._rcs = list(rcs)

    def __call__(self, cmd: Sequence[str]) -> int:
        self.calls.append(list(cmd))
        return self._rcs.pop(0) if self._rcs else 0


class FakeLock:
    """LockFn fake: запись вызовов + фиксированная семантика (success | raise)."""

    def __init__(self, *, conflict: bool = False) -> None:
        self.calls: list[str] = []
        self._conflict = conflict

    def __call__(self) -> int:
        self.calls.append("lock")
        if self._conflict:
            msg = "Another converge or node-update is already running (lock: /x)"
            raise converge.LockConflictError(msg)
        return 42  # fake fd


class FakeReload:
    """ReloadFn fake (F6/DevPlan 031 T5): запись вызова post-reconcile reload (0 docker)."""

    def __init__(self) -> None:
        self.calls: int = 0

    def __call__(self) -> None:
        self.calls += 1


def _make_resolver(node_yaml_path: str) -> Callable[[str], str]:
    """ResolveFn fake: всегда возвращает заданный node.yaml путь."""

    def resolve(node_name: str) -> str:
        return node_yaml_path

    return resolve


def _write_node_yaml(tmp_path: Path) -> Path:
    """Создать реальный node.yaml (main проверяет is_file — shell setup_environment parity)."""
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("context: test-context\nprojects: []\n", encoding="utf-8")
    return node_yaml


# ═══════════════════════════════════════════════════════════════════════
# parse_args / build_*_cmd
# ═══════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_parse_args_all_flags(caplog) -> None:
    """Парсинг CLI: все флаги converge (shell case-эквивалент)."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — аргументы прежнего converge.sh сохранены 1:1
    # · Scenario: любой флаг прежнего .sh потерян → фасадный контракт сломан
    # · Last fail: N/A (позитив; наследование аргументов .sh)
    # · Remove if: converge CLI расширен/изменён контракт
    ns = converge.parse_args(["--node", "node-a", "--dry-run", "--report-only", "--units", "R1,R3", "--reconcile"])
    assert ns.node == "node-a"
    assert ns.dry_run is True
    assert ns.report_only is True
    assert ns.units == "R1,R3"
    assert ns.reconcile is True
    logger.critical("[IMP:9][test][args] Все флаги CLI распарсены: node-a/dry-run/report-only/R1,R3/reconcile")


@ldd_trajectory
def test_build_reconciler_cmd_argv(tmp_path, caplog) -> None:
    """argv диспатча R1-R10: reconciler.py + --node-yaml/--node-name/--core-dir (shell recon_cmd parity)."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — диспатч argv
    # · Scenario: порядок/флаги reconciler.py изменены → контракт R-юнитов сломан
    # · Last fail: N/A (позитив; recon_cmd shell-эквивалент)
    # · Remove if: диспатч заменён прямым импортом reconciler.main()
    core_dir = Path(__file__).resolve().parent.parent.parent / "core"
    cmd = converge.build_reconciler_cmd(
        "/node-configs/node-a/node.yaml",
        "node-a",
        core_dir,
        dry_run=False,
        report_only=False,
        units="",
        python_exe="python3",
    )
    assert cmd[0] == "python3"
    assert str(cmd[1]).endswith("bootstrap/converge/reconciler.py")
    assert cmd[cmd.index("--node-yaml") + 1] == "/node-configs/node-a/node.yaml"
    assert cmd[cmd.index("--node-name") + 1] == "node-a"
    assert cmd[cmd.index("--core-dir") + 1] == str(core_dir)
    assert "--units" not in cmd, "--units пустой → не передаётся (shell [[ -n ]] parity)"
    logger.critical("[IMP:9][test][argv] reconciler.py argv собран: %d элементов", len(cmd))


@ldd_trajectory
def test_build_reconciler_cmd_flags_and_units(tmp_path, caplog) -> None:
    """argv диспатча: --dry-run/--report-only/--units passthrough (shell parity)."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — флаги диспатча
    # · Scenario: dry-run не проброшен в reconciler → мутация в dry-run режиме
    # · Last fail: N/A (позитив; recon_cmd += флагов shell-эквивалент)
    # · Remove if: диспатч заменён прямым импортом
    core_dir = Path(__file__).resolve().parent.parent.parent / "core"
    cmd = converge.build_reconciler_cmd(
        "/node-configs/node-a/node.yaml",
        "node-a",
        core_dir,
        dry_run=True,
        report_only=True,
        units="R1,R3",
        python_exe="python3",
    )
    assert "--dry-run" in cmd and "--report-only" in cmd
    assert cmd[cmd.index("--units") + 1] == "R1,R3"
    logger.critical("[IMP:9][test][argv] reconciler.py argv с флагами: dry-run/report-only/units подтверждены")


@ldd_trajectory
def test_build_reconcile_cmd_with_node_host_map(tmp_path, caplog) -> None:
    """argv --reconcile: reconciler_projects.py + --node-host-map из env (shell rec_cmd parity)."""
    # 🧪 TRAP[TEST] · REGRESSION · B9 T4 D4 — reconcile-диспатч
    # · Scenario: NODE_HOST_MAP env потерян → SSH-host не резолвится, stub не деплоится
    # · Last fail: N/A (позитив; rec_cmd shell-эквивалент)
    # · Remove if: reconcile-механизм изменён
    core_dir = Path(__file__).resolve().parent.parent.parent / "core"
    cmd = converge.build_reconcile_cmd(
        "/node-configs/node-a/node.yaml",
        "node-a",
        core_dir,
        dry_run=True,
        node_host_map='{"node-a":"1.2.3.4"}',
        python_exe="python3",
    )
    assert str(cmd[1]).endswith("internal/reconciler_projects.py")
    assert cmd[cmd.index("--node") + 1] == "node-a"
    assert cmd[cmd.index("--node-host-map") + 1] == '{"node-a":"1.2.3.4"}'
    assert "--dry-run" in cmd
    logger.critical("[IMP:9][test][argv] reconciler_projects.py argv собран (NODE_HOST_MAP + dry-run)")


# ═══════════════════════════════════════════════════════════════════════
# main() — exit-маппинг и flock-семантика
# ═══════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_missing_node_returns_2(tmp_path, caplog) -> None:
    """--node отсутствует → exit 2 (fail-fast; девиация от shell usage-exit-0 задокументирована)."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — required-аргумент
    # · Scenario: converge без --node → должен сигналить ошибку (2), не успех
    # · Last fail: N/A (девиация: прежний shell usage() exit 0 — fail-fast канон)
    # · Remove if: --node перестанет быть обязательным
    node_yaml = _write_node_yaml(tmp_path)
    rc = converge.main(
        ["--dry-run"],
        env={},
        dispatch_fn=FakeDispatch(),
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=FakeLock(),
        python_exe="python3",
    )
    assert rc == 2, "missing --node → exit 2"
    logger.critical("[IMP:9][test][main] missing --node → exit 2 (fail-fast)")


@ldd_trajectory
def test_node_yaml_missing_returns_2(tmp_path, caplog) -> None:
    """node.yaml не найден (ConfigNotFoundError) → exit 2 (shell setup_environment parity)."""

    # 🧪 TRAP[TEST] · REGRESSION · shell setup_environment — FATAL exit 2
    # · Scenario: resolver не нашёл node.yaml → converge не должен молча продолжить
    # · Last fail: N/A (контракт; shell: `|| { FATAL; exit 2; }`)
    # · Remove if: резолв node.yaml изменён
    def failing_resolve(node_name: str) -> str:
        msg = f"node.yaml not found for {node_name}"
        raise ConfigNotFoundError(msg)

    rc = converge.main(
        ["--node", "node-a", "--dry-run"],
        env={},
        dispatch_fn=FakeDispatch(),
        resolve_fn=failing_resolve,
        lock_fn=FakeLock(),
        python_exe="python3",
    )
    assert rc == 2, "ConfigNotFoundError → exit 2"
    logger.critical("[IMP:9][test][main] node.yaml missing → exit 2")


@ldd_trajectory
def test_dispatch_to_reconciler_success(tmp_path, caplog) -> None:
    """Полный контур: resolve → flock → диспатч reconciler.py → exit 0 (fully converged)."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — полный сценарий
    # · Scenario: reconciler вернул 0 → converge exit 0
    # · Last fail: N/A (позитив; recon_rc passthrough)
    # · Remove if: exit-контракт изменён
    node_yaml = _write_node_yaml(tmp_path)
    dispatch = FakeDispatch(0)
    lock = FakeLock()
    reload_ = FakeReload()
    rc = converge.main(
        ["--node", "node-a"],
        env={},
        dispatch_fn=dispatch,
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=lock,
        reload_fn=reload_,
        python_exe="python3",
    )
    assert rc == 0
    assert len(dispatch.calls) == 1, "Ровно один диспатч (без --reconcile)"
    assert "reconciler.py" in str(dispatch.calls[0][1])
    assert lock.calls == ["lock"], "flock должен быть взят в CONVERGE-режиме"
    logger.critical("[IMP:9][test][main] exit 0 (converged), flock взят, диспатч выполнен")


@ldd_trajectory
def test_exit_code_1_warnings(tmp_path, caplog) -> None:
    """Exit-маппинг: reconciler rc=1 (warnings) → converge exit 1."""
    # 🧪 TRAP[TEST] · REGRESSION · exit-контракт {0,1,2}
    # · Scenario: recon rc=1 теряется → warnings невидимы оператору
    # · Last fail: N/A (контракт; usage: 1=warnings)
    # · Remove if: exit-маппинг изменён
    node_yaml = _write_node_yaml(tmp_path)
    rc = converge.main(
        ["--node", "node-a"],
        env={},
        dispatch_fn=FakeDispatch(1),
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=FakeLock(),
        reload_fn=FakeReload(),
        python_exe="python3",
    )
    assert rc == 1
    logger.critical("[IMP:9][test][main] reconciler rc=1 → exit 1 (warnings)")


@ldd_trajectory
def test_exit_code_2_errors(tmp_path, caplog) -> None:
    """Exit-маппинг: reconciler rc=2 (errors) → converge exit 2."""
    # 🧪 TRAP[TEST] · REGRESSION · exit-контракт {0,1,2}
    # · Scenario: recon rc=2 теряется → R-unit ошибки не блокируют CI
    # · Last fail: N/A (контракт; usage: 2=errors)
    # · Remove if: exit-маппинг изменён
    node_yaml = _write_node_yaml(tmp_path)
    rc = converge.main(
        ["--node", "node-a"],
        env={},
        dispatch_fn=FakeDispatch(2),
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=FakeLock(),
        reload_fn=FakeReload(),
        python_exe="python3",
    )
    assert rc == 2
    logger.critical("[IMP:9][test][main] reconciler rc=2 → exit 2 (errors)")


@ldd_trajectory
def test_dry_run_skips_lock_and_passes_flag(tmp_path, caplog) -> None:
    """--dry-run: флаг проброшен в reconciler, flock НЕ берётся (shell acquire_lock parity)."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — dry-run не мутирует
    # · Scenario: dry-run берёт flock → блокирует реальный converge параллельно
    # · Last fail: N/A (контракт; shell: dry-run/report-only → lock SKIP)
    # · Remove if: lock-политика dry-run изменена
    node_yaml = _write_node_yaml(tmp_path)
    dispatch = FakeDispatch(0)
    lock = FakeLock()
    rc = converge.main(
        ["--node", "node-a", "--dry-run"],
        env={},
        dispatch_fn=dispatch,
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=lock,
        python_exe="python3",
    )
    assert rc == 0
    assert lock.calls == [], "dry-run НЕ должен брать flock"
    assert "--dry-run" in dispatch.calls[0], "--dry-run должен быть проброшен в reconciler"
    logger.critical("[IMP:9][test][main] dry-run: flock SKIP, --dry-run проброшен, exit 0")


@ldd_trajectory
def test_report_only_skips_lock(tmp_path, caplog) -> None:
    """--report-only: JSON-режим НЕ берёт flock (shell parity)."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — report-only не мутирует
    # · Scenario: report-only берёт flock → блокирует реальный converge параллельно
    # · Last fail: N/A (контракт; shell: dry-run/report-only → lock SKIP)
    # · Remove if: lock-политика report-only изменена
    node_yaml = _write_node_yaml(tmp_path)
    lock = FakeLock()
    rc = converge.main(
        ["--node", "node-a", "--report-only"],
        env={},
        dispatch_fn=FakeDispatch(0),
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=lock,
        python_exe="python3",
    )
    assert rc == 0
    assert lock.calls == [], "report-only НЕ должен брать flock"
    logger.critical("[IMP:9][test][main] report-only: flock SKIP, exit 0")


@ldd_trajectory
def test_reconcile_dispatched_after_reconciler(tmp_path, caplog) -> None:
    """--reconcile: после reconciler.py диспатчится reconciler_projects.py (B9 T4 D4)."""
    # 🧪 TRAP[TEST] · REGRESSION · B9 T4 D4 — reconcile-шаг
    # · Scenario: --reconcile потерян → stub-проекты не деплоятся после converge
    # · Last fail: N/A (позитив; shell: reconcile после recon)
    # · Remove if: reconcile-механизм изменён
    node_yaml = _write_node_yaml(tmp_path)
    dispatch = FakeDispatch(0, 0)
    rc = converge.main(
        ["--node", "node-a", "--reconcile"],
        env={"NODE_HOST_MAP": '{"node-a":"1.2.3.4"}'},
        dispatch_fn=dispatch,
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=FakeLock(),
        reload_fn=FakeReload(),
        python_exe="python3",
    )
    assert rc == 0
    assert len(dispatch.calls) == 2, "Два диспатча: reconciler + reconcile"
    assert "reconciler.py" in str(dispatch.calls[0][1])
    assert "reconciler_projects.py" in str(dispatch.calls[1][1])
    assert "--node-host-map" in dispatch.calls[1], "NODE_HOST_MAP env → флаг reconcile"
    logger.critical("[IMP:9][test][main] --reconcile: reconciler_projects.py диспатчен, exit 0")


@ldd_trajectory
def test_reconcile_failure_upgrades_to_2(tmp_path, caplog) -> None:
    """Reconcile fail → exit 2 (shell `[[ 2 -gt $recon_rc ]] && recon_rc=2` parity)."""
    # 🧪 TRAP[TEST] · REGRESSION · shell recon_rc upgrade
    # · Scenario: reconcile упал (rc=1), recon был 0 → converge должен вернуть 2
    # · Last fail: N/A (контракт; shell: recon_rc=2 upgrade)
    # · Remove if: reconcile-fail не должен блокировать exit
    node_yaml = _write_node_yaml(tmp_path)
    rc = converge.main(
        ["--node", "node-a", "--reconcile"],
        env={},
        dispatch_fn=FakeDispatch(0, 1),
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=FakeLock(),
        reload_fn=FakeReload(),
        python_exe="python3",
    )
    assert rc == 2, "reconcile rc=1 при recon rc=0 → exit 2 (upgrade)"
    logger.critical("[IMP:9][test][main] reconcile fail → exit 2 (upgrade)")


@ldd_trajectory
def test_lock_conflict_returns_3(tmp_path, caplog) -> None:
    """Lock-конфликт → exit 3 (shell acquire_lock FATAL exit 3 parity)."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — flock конфликт
    # · Scenario: параллельный converge уже держит flock → exit 3, БЕЗ диспатча
    # · Last fail: N/A (контракт; shell: flock -n fail → FATAL exit 3)
    # · Remove if: lock-конфликт перестанет быть fatal
    node_yaml = _write_node_yaml(tmp_path)
    dispatch = FakeDispatch(0)
    rc = converge.main(
        ["--node", "node-a"],
        env={},
        dispatch_fn=dispatch,
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=FakeLock(conflict=True),
        python_exe="python3",
    )
    assert rc == 3, "lock-conflict → exit 3"
    assert dispatch.calls == [], "lock-conflict → диспатч НЕ выполняется"
    logger.critical("[IMP:9][test][main] lock-conflict → exit 3, диспатч заблокирован")


@ldd_trajectory
def test_units_passthrough_to_reconciler(tmp_path, caplog) -> None:
    """--units: фильтр проброшен в reconciler.py argv (R3-only прогон, users.py вызов)."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — unit-фильтр
    # · Scenario: --units R3 потерян → users.py прогоняет ВСЕ R-юниты (медленно/мутирует)
    # · Last fail: N/A (позитив; users.py вызывает converge --units R3)
    # · Remove if: unit-фильтрация перенесена из reconciler
    node_yaml = _write_node_yaml(tmp_path)
    dispatch = FakeDispatch(0)
    rc = converge.main(
        ["--node", "node-a", "--units", "R3"],
        env={},
        dispatch_fn=dispatch,
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=FakeLock(),
        reload_fn=FakeReload(),
        python_exe="python3",
    )
    assert rc == 0
    assert dispatch.calls[0][dispatch.calls[0].index("--units") + 1] == "R3"
    logger.critical("[IMP:9][test][main] --units R3 проброшен в reconciler.py")


# ═══════════════════════════════════════════════════════════════════════
# acquire_flock — реальный fcntl (POSIX per-open-file-description семантика)
# ═══════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_acquire_flock_conflict_and_release(tmp_path, caplog) -> None:
    """fcntl.flock: второй держатель того же файла → LockConflictError; после release — успех."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — flock семантика (Rev TRAP[DECISION] 2026-07-22)
    # · Scenario: flock НЕ сработал бы (напр. fcntl поломан) → параллельные converge гоняются
    # · Last fail: N/A (контракт; flock per-open-file-description — два fd конфликтуют)
    # · Remove if: лок-механизм заменён (не flock)
    lock_path = tmp_path / "platform-converge.lock"
    fd1 = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)  # первый держатель
        with pytest.raises(converge.LockConflictError, match="already running"):
            converge.acquire_flock(str(lock_path))
        fcntl.flock(fd1, fcntl.LOCK_UN)  # release
        fd2 = converge.acquire_flock(str(lock_path))  # теперь успех
        os.close(fd2)
    finally:
        os.close(fd1)
    logger.critical("[IMP:9][test][lock] flock: конфликт → LockConflictError, release → успех")


@ldd_trajectory
def test_should_acquire_lock_logic(caplog) -> None:
    """should_acquire_lock: только CONVERGE-режим требует flock."""
    # 🧪 TRAP[TEST] · REGRESSION · W3.5-1 — lock-решение
    # · Scenario: dry-run/report-only требует flock → блокировка параллельных прогонов
    # · Last fail: N/A (чистый предикат)
    # · Remove if: lock-политика изменена
    assert converge.should_acquire_lock(dry_run=False, report_only=False) is True
    assert converge.should_acquire_lock(dry_run=True, report_only=False) is False
    assert converge.should_acquire_lock(dry_run=False, report_only=True) is False
    assert converge.should_acquire_lock(dry_run=True, report_only=True) is False
    logger.critical("[IMP:9][test][lock] should_acquire_lock: dry/report → False, converge → True")


# ═══════════════════════════════════════════════════════════════════════
# F6 (DevPlan 031 T5): post-reconcile nginx reload
# ═══════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_post_reconcile_reload_on_clean_converge(tmp_path, caplog) -> None:
    """F6: converge rc=0 → post-reconcile nginx reload выполнен ровно 1 раз (после всех R-units)."""
    # 🧪 TRAP[TEST] · 2026-09-03 · REGRESSION (R5) · F6/DevPlan 031 T5 — reload ПОСЛЕ всех R-units
    # · Scenario: серт восстановлен/выпущен R-ssl'ом mid-cycle → nginx обязан подхватить его
    #   reload'ом ПОСЛЕ верификации R6 (rc=0 = конфиг валиден). Без post-reconcile reload
    #   docker-nginx продолжал бы сервить старый серт (issue_cert systemctl reload — no-op).
    # · Last fail: FINDING-p3-3 (ночной прогон 2026-09-03) — reload mid-run до R6-верификации
    # · Remove if: converge перестаёт reload'ить nginx после R-units
    node_yaml = _write_node_yaml(tmp_path)
    reload_ = FakeReload()
    rc = converge.main(
        ["--node", "node-a"],
        env={},
        dispatch_fn=FakeDispatch(0),
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=FakeLock(),
        reload_fn=reload_,
        python_exe="python3",
    )
    assert rc == 0
    assert reload_.calls == 1, f"F6: post-reconcile reload должен выполниться 1 раз, got {reload_.calls}"
    logger.critical("[IMP:9][test][F6] rc=0 → post-reconcile nginx reload выполнен")


@ldd_trajectory
def test_post_reconcile_reload_skipped_on_r_errors(tmp_path, caplog) -> None:
    """F6: R-юниты сообщили ошибки (rc=2, напр. R6 vhost fail) → reload НЕ выполняется."""
    # 🧪 TRAP[TEST] · 2026-09-03 · NEGATIVE (R5) · F6 — reload при ошибках = downtime window
    # · Scenario: дрилл (vhost .conf удалён) → R6 fail → converge rc=2. Reload в этом состоянии
    #   перечитал бы overlay и сбросил живой vhost из running-конфига (FINDING-p3-3). НЕ reload'им:
    #   running-конфиг nginx остаётся нетронутым до ручного восстановления оператором.
    # · Last fail: FINDING-p3-3 — converge reload'ил nginx даже когда R6 позже падал
    # · Remove if: reload-гейт по ошибкам R-юнитов изменён
    node_yaml = _write_node_yaml(tmp_path)
    reload_ = FakeReload()
    rc = converge.main(
        ["--node", "node-a"],
        env={},
        dispatch_fn=FakeDispatch(2),
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=FakeLock(),
        reload_fn=reload_,
        python_exe="python3",
    )
    assert rc == 2
    assert reload_.calls == 0, f"F6: при rc=2 reload запрещён, got {reload_.calls}"
    logger.critical("[IMP:9][test][F6] rc=2 → reload SKIP (running-конфиг нетронут)")


@ldd_trajectory
def test_post_reconcile_reload_skipped_dry_run(tmp_path, caplog) -> None:
    """F6: --dry-run → reload НЕ выполняется (mode-контракт «no mutations»)."""
    # 🧪 TRAP[TEST] · 2026-09-03 · SCENARIO · F6 — preview не мутирует
    # · Scenario: dry-run/report-only converge не должен reload'ить nginx (никаких side-effects)
    # · Last fail: N/A (preventive — mode-контракт reconciler)
    # · Remove if: dry-run получает reload-семантику
    node_yaml = _write_node_yaml(tmp_path)
    reload_ = FakeReload()
    rc = converge.main(
        ["--node", "node-a", "--dry-run"],
        env={},
        dispatch_fn=FakeDispatch(0),
        resolve_fn=_make_resolver(str(node_yaml)),
        lock_fn=FakeLock(),
        reload_fn=reload_,
        python_exe="python3",
    )
    assert rc == 0
    assert reload_.calls == 0, f"F6: dry-run не должен reload'ить nginx, got {reload_.calls}"
    logger.critical("[IMP:9][test][F6] dry-run → reload SKIP")
