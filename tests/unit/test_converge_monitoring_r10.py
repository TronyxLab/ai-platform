#!/usr/bin/env python3
# GREP_SUMMARY: test-converge-monitoring, r10, prometheus, tsdb, corruption-guard, wal, blocks, self-heal, docker-logs-mock, clock-skew
# STRUCTURE: ▶ no-corruption → converged no-op │ ▶ marker+targets-up → converged (guard 2) │ ▶ marker+targets-down → mutated (cleanup+restart) │ ▶ dry-run → WOULD │ ▶ no container → skipped
# region MODULE_CONTRACT
## @purpose  Unit-тесты R10 reconcile_prometheus_tsdb (142 W3, A4) — TSDB self-heal с двойным
##           guard: коррапт-маркер в docker logs И недоступные targets. Здоровый TSDB НЕ чистится.
## @scope    Тесты: все ветки guard-логики (converged no-op / guard-2 / mutated / dry-run / skipped).
##           docker_ops (ps/logs/exec/inspect/info) мокается — 0 реальных docker-команд.
## @invariants
##   - R5-negative: вход, поймавший A4 (маркер «too far into the future» + мёртвый /-/healthy) →
##     cleanup ВЫПОЛНЯЕТСЯ (mutated); здоровый TSDB (без маркеров) → converged, 0 мутаций
##   - shutil.move мокается (tmp_path-файлы, без реального volume)
##   - LDD: IMP:9-траектория в успешных сценариях (ldd_trajectory)
## @rationale 142 W3 (Q3 «а»): R10 converge-юнит + unit (эмуляция логов/состояния контейнера —
##           DevPlan W3 R5). Guard-логика обязательна (риск §8: «не чистить здоровый»).
## @changes  2026-08-06 | Created (142 W3)
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.bootstrap.converge.prometheus_tsdb import reconcile_prometheus_tsdb
from core.internal.shared import docker_ops
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _patch_docker(monkeypatch: pytest.MonkeyPatch, *, logs: str = "", health_rc: int = 0) -> None:
    """Mock docker_ops: контейнер есть, логи/проба параметризуются."""
    monkeypatch.setattr(docker_ops, "docker_info", lambda **k: _completed(returncode=0))
    monkeypatch.setattr(docker_ops, "docker_ps", lambda **k: _completed("prometheus\n"))
    monkeypatch.setattr(docker_ops, "docker_logs", lambda *a, **k: _completed(logs))
    monkeypatch.setattr(docker_ops, "docker_exec", lambda *a, **k: _completed(returncode=health_rc))
    monkeypatch.setattr(
        docker_ops, "docker_inspect", lambda *a, **k: _completed("/var/lib/docker/volumes/prometheus-data/_data")
    )


# 🧪 TRAP[TEST] · NEGATIVE (R5) · R10/142 W3 — коррапт (A4-вход) → cleanup выполняется
# · Scenario: docker logs содержит «too far into the future» (clock-skew T4) И wget /-/healthy
# ·   падает (rc=1) → R10 делает backup wal/blocks + restart → status=mutated, exit=1
# · Last fail: 2026-08-06 (цикл 2 141, A4) — wal/blocks чистились ВРУЧНУЮ после chaos T4
# · Remove if: R10 guard-логика меняется (двойной guard снимается)
@ldd_trajectory
def test_r10_corruption_triggers_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """R10: маркер коррапта + мёртвые targets → backup wal/blocks + restart (mutated)."""
    caplog.set_level(logging.INFO)
    _patch_docker(
        monkeypatch,
        logs='level=error ts=... msg="Error loading block" err="block too far into the future"',
        health_rc=1,
    )

    # TSDB-директория с wal/blocks (для реального move-теста через shutil.move)
    tsdb = tmp_path / "tsdb"
    (tsdb / "wal").mkdir(parents=True)
    (tsdb / "blocks").mkdir()
    monkeypatch.setattr(docker_ops, "docker_inspect", lambda *a, **k: _completed(str(tsdb)))

    moved: list[str] = []
    monkeypatch.setattr(
        "core.internal.bootstrap.converge.prometheus_tsdb.shutil.move",
        lambda src, dst: moved.append(f"{src}->{dst}"),
    )
    monkeypatch.setattr(
        "core.internal.bootstrap.converge.prometheus_tsdb._shared_docker_compose_up",
        lambda *a, **k: True,
    )
    # compose-файл: модуль monitoring существует в реальном дереве — резолвится через env
    import os

    os.environ["PLATFORM_MODULES_DIR"] = str(Path(__file__).resolve().parents[2] / "core" / "modules")

    result = reconcile_prometheus_tsdb("/tmp/nonexistent.yaml")

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
def test_r10_healthy_tsdb_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """R10: здоровый TSDB (без маркеров) → converged, 0 мутаций."""
    caplog.set_level(logging.INFO)
    _patch_docker(monkeypatch, logs='level=info msg="Server is ready to receive web requests."', health_rc=0)
    tsdb = tmp_path / "tsdb"
    (tsdb / "wal").mkdir(parents=True)
    (tsdb / "blocks").mkdir()
    monkeypatch.setattr(docker_ops, "docker_inspect", lambda *a, **k: _completed(str(tsdb)))
    cleanup_calls: list = []
    monkeypatch.setattr(
        "core.internal.bootstrap.converge.prometheus_tsdb._cleanup_tsdb", lambda d: cleanup_calls.append(d) or True
    )
    monkeypatch.setattr(
        "core.internal.bootstrap.converge.prometheus_tsdb._shared_docker_compose_up", lambda *a, **k: True
    )

    result = reconcile_prometheus_tsdb("/tmp/nonexistent.yaml")

    assert result["status"] == "converged", f"здоровый TSDB → converged, got {result}"
    assert not cleanup_calls, "очистка НЕ должна вызываться на здоровом TSDB"
    assert "No TSDB corruption" in result["detail"]
    logger.critical("[IMP:9][test] R10: здоровый TSDB → no-op (guard 1) ✓")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · R10/142 W3 — маркер есть, targets живы → НЕ чистим (guard 2)
# · Scenario: лог содержит «out of bounds», но /-/healthy отвечает (rc=0) → ложная тревога,
# ·   cleanup НЕ выполняется (guard 2 срабатывает консервативно)
# · Last fail: N/A (новый negative-тест — второй guard)
# · Remove if: guard 2 снимается
@ldd_trajectory
def test_r10_marker_but_targets_up_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """R10: маркер есть, но targets отвечают → converged (guard 2, НЕ чистим)."""
    caplog.set_level(logging.INFO)
    _patch_docker(monkeypatch, logs='err="block out of bounds"', health_rc=0)
    cleanup_calls: list = []
    monkeypatch.setattr(
        "core.internal.bootstrap.converge.prometheus_tsdb._cleanup_tsdb", lambda d: cleanup_calls.append(d) or True
    )
    monkeypatch.setattr(
        "core.internal.bootstrap.converge.prometheus_tsdb._shared_docker_compose_up", lambda *a, **k: True
    )

    result = reconcile_prometheus_tsdb("/tmp/nonexistent.yaml")

    assert result["status"] == "converged", f"guard 2 → converged, got {result}"
    assert not cleanup_calls, "cleanup НЕ должен выполняться при живых targets"
    assert "Guard 2" in result["detail"]
    logger.critical("[IMP:9][test] R10: маркер без target-дауна → no-op (guard 2) ✓")


# 🧪 TRAP[TEST] · R10/142 W3 — dry-run: коррапт подтверждён, но 0 мутаций
# · Scenario: dry_run=True при подтверждённом коррапте → report mutated (WOULD), move НЕ вызван
# · Remove if: dry-run семантика converge меняется
@ldd_trajectory
def test_r10_dry_run_no_mutations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """R10: dry_run → WOULD-лог + report, 0 мутаций."""
    caplog.set_level(logging.INFO)
    _patch_docker(monkeypatch, logs='err="block too far into the future"', health_rc=1)
    tsdb = tmp_path / "tsdb"
    (tsdb / "wal").mkdir(parents=True)
    monkeypatch.setattr(docker_ops, "docker_inspect", lambda *a, **k: _completed(str(tsdb)))
    moved: list[str] = []
    monkeypatch.setattr(
        "core.internal.bootstrap.converge.prometheus_tsdb.shutil.move", lambda s, d: moved.append(str(s))
    )

    result = reconcile_prometheus_tsdb("/tmp/nonexistent.yaml", dry_run=True)

    assert result["status"] == "mutated", f"dry-run → mutated (WOULD), got {result}"
    assert result["detail"].startswith("WOULD"), f"dry-run detail обязан начинаться с WOULD: {result['detail']}"
    assert not moved, "dry-run: файлы не перемещаются"
    logger.critical("[IMP:9][test] R10: dry-run → WOULD, 0 мутаций ✓")


# 🧪 TRAP[TEST] · R10/142 W3 — нет контейнера prometheus → skipped
# · Scenario: docker ps пуст → skipped (мониторинг-модуль не задеплоен), no-op
# · Remove if: R10 контейнер-резолв меняется
def test_r10_no_container_skipped(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """R10: без контейнера prometheus → skipped."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(docker_ops, "docker_info", lambda **k: _completed(returncode=0))
    monkeypatch.setattr(docker_ops, "docker_ps", lambda **k: _completed(""))

    result = reconcile_prometheus_tsdb("/tmp/nonexistent.yaml")

    assert result["status"] == "skipped", f"ожидался skipped, got {result}"
    assert "No prometheus container" in result["detail"]
    logger.info("[IMP:9][test] R10: нет контейнера → skipped ✓")
