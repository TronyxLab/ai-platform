# GREP_SUMMARY: test-converge-monitoring, r10, prometheus, tsdb, corruption-guard, wal, blocks, self-heal, FakeDockerOps, DI, mover, clock-skew
# STRUCTURE: ▶ no-corruption → converged no-op │ ▶ marker+targets-up → converged (guard 2) │ ▶ marker+targets-down → mutated (cleanup+restart) │ ▶ dry-run → WOULD │ ▶ no container → skipped
# region MODULE_CONTRACT
## @purpose  Unit-тесты R10 reconcile_prometheus_tsdb (142 W3, A4) — TSDB self-heal с двойным
##           guard: коррапт-маркер в docker logs И недоступные targets. Здоровый TSDB НЕ чистится.
## @scope    Тесты: все ветки guard-логики (converged no-op / guard-2 / mutated / dry-run / skipped).
##           docker_ops (ps/logs/exec/inspect/info) — через FakeDockerOps (docker_ops_obj DI, E1),
##           0 monkeypatch docker_ops, shutil.move через mover DI.
## @invariants
##   - R5-negative: вход, поймавший A4 (маркер «too far into the future» + мёртвый /-/healthy) →
##     cleanup ВЫПОЛНЯЕТСЯ (mutated); здоровый TSDB (без маркеров) → converged, 0 мутаций
##   - mover мокается (tmp_path-файлы, без реального volume)
##   - LDD: IMP:9-траектория в успешных сценариях (ldd_trajectory)
## @rationale 142 W3 (Q3 «а»): R10 converge-юнит + unit (эмуляция логов/состояния контейнера —
##           DevPlan W3 R5). Guard-логика обязательна (риск §8: «не чистить здоровый»).
## @changes  2026-08-06 | Created (142 W3)
##           2026-08-13 | E1 (160) — DI-конвертация (setattr 17 → 0, −100%)
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.bootstrap.converge.prometheus_tsdb import reconcile_prometheus_tsdb
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class FakeDockerOps:
    """Fake docker_ops-объект (E1 DI): scripted docker_info/ps/logs/exec/inspect.

    ## @purpose — Замена monkeypatch docker_ops.* в тестах R10: docker_ops_obj DI-параметр.
    ## @io — ⇥ logs: str (docker logs stdout), health_rc: int (exec /-/healthy rc),
    ##       tsdb_dir: str | None (inspect Mounts.Source) → ⎋ scripted CompletedProcess
    ## @complexity — O(1) — scripted результаты
    """

    def __init__(self, logs: str = "", health_rc: int = 0, tsdb_dir: str | None = None, ps_out: str = "prometheus\n"):
        self._logs = logs
        self._health_rc = health_rc
        self._tsdb_dir = tsdb_dir
        self._ps_out = ps_out
        self.info_calls = 0
        self.ps_calls = 0
        self.logs_calls = 0
        self.exec_calls = 0
        self.inspect_calls = 0

    def docker_info(self, **_k):
        self.info_calls += 1
        return _completed(returncode=0)

    def docker_ps(self, **_k):
        self.ps_calls += 1
        return _completed(self._ps_out)

    def docker_logs(self, *_a, **_k):
        self.logs_calls += 1
        return _completed(self._logs)

    def docker_exec(self, *_a, **_k):
        self.exec_calls += 1
        return _completed(returncode=self._health_rc)

    def docker_inspect(self, *_a, **_k):
        self.inspect_calls += 1
        return _completed(self._tsdb_dir or "/var/lib/docker/volumes/prometheus-data/_data")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · R10/142 W3 — коррапт (A4-вход) → cleanup выполняется
# · Scenario: docker logs содержит «too far into the future» (clock-skew T4) И wget /-/healthy
# ·   падает (rc=1) → R10 делает backup wal/blocks + restart → status=mutated, exit=1
# · Last fail: 2026-08-06 (цикл 2 141, A4) — wal/blocks чистились ВРУЧНУЮ после chaos T4
# · Remove if: R10 guard-логика меняется (двойной guard снимается)
@ldd_trajectory
def test_r10_corruption_triggers_cleanup(tmp_path: Path, caplog) -> None:
    """R10: маркер коррапта + мёртвые targets → backup wal/blocks + restart (mutated)."""
    caplog.set_level(logging.INFO)

    # TSDB-директория с wal/blocks (для реального move-теста через mover DI)
    tsdb = tmp_path / "tsdb"
    (tsdb / "wal").mkdir(parents=True)
    (tsdb / "blocks").mkdir()

    moved: list[str] = []
    ops = FakeDockerOps(
        logs='level=error ts=... msg="Error loading block" err="block too far into the future"',
        health_rc=1,
        tsdb_dir=str(tsdb),
    )

    result = reconcile_prometheus_tsdb(
        "/tmp/nonexistent.yaml",
        docker_ops_obj=ops,
        mover=lambda src, dst: moved.append(f"{src}->{dst}"),
        compose_up_fn=lambda *_, **__: True,
        environ={"PLATFORM_MODULES_DIR": str(Path(__file__).resolve().parents[2] / "core" / "modules")},
    )

    assert result["status"] == "mutated", f"ожидался mutated, got {result}"
    assert "too far into the future" in result["detail"], f"маркер в detail: {result['detail']}"
    assert len(moved) >= 2, f"wal и blocks обязаны быть перемещены (backup): {moved}"
    assert all("->" in m for m in moved)
    logger.critical("[IMP:9][test] R10: коррапт → cleanup (backup wal/blocks) + restart ✓")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · R10/142 W3 — здоровый TSDB НЕ чистится (главный guard)
# · Scenario: docker logs БЕЗ маркеров коррапта → converged no-op; wal/blocks НЕ трогаются;
# ·   docker compose НЕ вызывается; exit не меняется (0)
# · Last fail: N/A (новый negative-тест — риск §8 W3 «не чистить здоровый TSDB»)
# · Remove if: R10 начинает чистить без детекции
@ldd_trajectory
def test_r10_healthy_tsdb_noop(tmp_path: Path, caplog) -> None:
    """R10: здоровый TSDB (без маркеров) → converged, 0 мутаций."""
    caplog.set_level(logging.INFO)
    ops = FakeDockerOps(logs='level=info msg="Server is ready to receive web requests."', health_rc=0)

    moved: list[str] = []
    up_calls: list = []

    result = reconcile_prometheus_tsdb(
        "/tmp/nonexistent.yaml",
        docker_ops_obj=ops,
        mover=lambda s, _: moved.append(str(s)),
        compose_up_fn=lambda *_, **__: up_calls.append(1) or True,
        environ={"PLATFORM_MODULES_DIR": str(Path(__file__).resolve().parents[2] / "core" / "modules")},
    )

    assert result["status"] == "converged", f"здоровый TSDB → converged, got {result}"
    assert not moved, "очистка НЕ должна вызываться на здоровом TSDB (0 move)"
    assert not up_calls, "compose up НЕ должен вызываться на здоровом TSDB"
    assert "No TSDB corruption" in result["detail"]
    logger.critical("[IMP:9][test] R10: здоровый TSDB → no-op (guard 1) ✓")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · R10/142 W3 — маркер есть, targets живы → НЕ чистим (guard 2)
# · Scenario: лог содержит «out of bounds», но /-/healthy отвечает (rc=0) → ложная тревога,
# ·   cleanup НЕ выполняется (guard 2 срабатывает консервативно)
# · Last fail: N/A (новый negative-тест — второй guard)
# · Remove if: guard 2 снимается
@ldd_trajectory
def test_r10_marker_but_targets_up_noop(caplog) -> None:
    """R10: маркер есть, но targets отвечают → converged (guard 2, НЕ чистим)."""
    caplog.set_level(logging.INFO)
    ops = FakeDockerOps(logs='err="block out of bounds"', health_rc=0)

    moved: list[str] = []
    up_calls: list = []

    result = reconcile_prometheus_tsdb(
        "/tmp/nonexistent.yaml",
        docker_ops_obj=ops,
        mover=lambda s, _: moved.append(str(s)),
        compose_up_fn=lambda *_, **__: up_calls.append(1) or True,
        environ={"PLATFORM_MODULES_DIR": str(Path(__file__).resolve().parents[2] / "core" / "modules")},
    )

    assert result["status"] == "converged", f"guard 2 → converged, got {result}"
    assert not moved, "cleanup НЕ должен выполняться при живых targets (0 move)"
    assert not up_calls, "compose up НЕ должен вызываться при живых targets"
    assert "Guard 2" in result["detail"]
    logger.critical("[IMP:9][test] R10: маркер без target-дауна → no-op (guard 2) ✓")


# 🧪 TRAP[TEST] · R10/142 W3 — dry-run: коррапт подтверждён, но 0 мутаций
# · Scenario: dry_run=True при подтверждённом коррапте → report mutated (WOULD), move НЕ вызван
# · Remove if: dry-run семантика converge меняется
@ldd_trajectory
def test_r10_dry_run_no_mutations(caplog) -> None:
    """R10: dry_run → WOULD-лог + report, 0 мутаций."""
    caplog.set_level(logging.INFO)
    ops = FakeDockerOps(logs='err="block too far into the future"', health_rc=1)

    moved: list[str] = []

    result = reconcile_prometheus_tsdb(
        "/tmp/nonexistent.yaml",
        dry_run=True,
        docker_ops_obj=ops,
        mover=lambda s, _: moved.append(str(s)),
        compose_up_fn=lambda *_, **__: pytest.fail("compose up НЕ должен вызываться в dry-run"),
        environ={"PLATFORM_MODULES_DIR": str(Path(__file__).resolve().parents[2] / "core" / "modules")},
    )

    assert result["status"] == "mutated", f"dry-run → mutated (WOULD), got {result}"
    assert result["detail"].startswith("WOULD"), f"dry-run detail обязан начинаться с WOULD: {result['detail']}"
    assert not moved, "dry-run: файлы не перемещаются"
    logger.critical("[IMP:9][test] R10: dry-run → WOULD, 0 мутаций ✓")


# 🧪 TRAP[TEST] · R10/142 W3 — нет контейнера prometheus → skipped
# · Scenario: docker ps пуст → skipped (мониторинг-модуль не задеплоен), no-op
# · Remove if: R10 контейнер-резолв меняется
def test_r10_no_container_skipped(caplog) -> None:
    """R10: без контейнера prometheus → skipped."""
    caplog.set_level(logging.INFO)
    ops = FakeDockerOps(ps_out="")

    result = reconcile_prometheus_tsdb("/tmp/nonexistent.yaml", docker_ops_obj=ops)

    assert result["status"] == "skipped", f"ожидался skipped, got {result}"
    assert "No prometheus container" in result["detail"]
    logger.info("[IMP:9][test] R10: нет контейнера → skipped ✓")
