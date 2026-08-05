#!/usr/bin/env python3
# GREP_SUMMARY: test-deploy-concurrent-lock, T9.1, flock, deploy-lock, concurrent-deploy, locked-by-pid, T9.10, deploy-history, snapshot, atomic, prune
# STRUCTURE: ▶ test_*_concurrent_* ┌PLATFORM_LOCK_DIR=tmp┐ → ◇ raw-flock holder (другой процесс) → deploy() → ◇ locked? → ⊕ FAILED "locked by PID" (×5 прогонов) │ ▶ test_*_release → deploy OK → повторный deploy не заблокирован │ ▶ test_*_snapshot_atomic → 12 snapshots → 10 (prune), payload_dir, 0 .tmp-мусора
# region MODULE_CONTRACT
## @purpose  Regression-тесты T9.1 (L-1/L-9/L-12) и T9.10 (L-12) DevPlan 136 W9: flock deploy
##           lock в DeployOrchestrator.deploy() + атомарность/прин DeployHistory под тем же lock.
## @scope    unit-тесты без Docker: contention-путь через raw-fcntl holder (имитация ДРУГОГО
##           процесса — FileLock reentrant в пределах процесса, registry module-level);
##           release-путь; snapshot-атомарность/прин/payload-персист.
## @invariants
##   - Native imports; tmp_path; PLATFORM_LOCK_DIR env → tmp (никаких /var/lock на dev-машине)
##   - concurrency-тесты параметризованы range(5) — assert на инвариант (consistency), не детерминизм
##   - LDD IMP:9 в успешных сценариях (anti-illusion)
## @rationale  $TEST_SPEC DevPlan 136 W9 T9.20: test_deploy_concurrent_lock.py — 2-нити/2-процесса
##            deploy → сериализация; R5-negative на точный вход: retry double-deploy (L-9).
## @changes  2026-08-05 · Created (DevPlan 136 W9)
# endregion MODULE_CONTRACT

import fcntl
import logging
import os
import sys
from pathlib import Path

import pytest

from core.internal.deploy.channels import LocalChannel
from core.internal.deploy.deploy_history import DeployHistory
from core.internal.deploy.orchestrator import DeployOrchestrator, DeployStatus
from core.internal.shared.file_lock import FileLock, platform_lock_path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


def _make_orchestrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DeployOrchestrator:
    """DeployOrchestrator с lock-dir и projects-base в tmp (никаких /var/lock)."""
    monkeypatch.setenv("PLATFORM_LOCK_DIR", str(tmp_path / "locks"))
    return DeployOrchestrator(projects_base=str(tmp_path / "projects"))


def _hold_raw_flock(lock_path: str, holder_pid: int = 99999) -> int:
    """Open + flock LOCK_EX (не через FileLock — обход reentrancy-registry, имитация ЧУЖОГО процесса).

    ▶ ┌lock_path┐ → open O_RDWR|O_CREAT → flock LOCK_EX|LOCK_NB → write holder PID → ⎋ fd
    """
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.ftruncate(fd, 0)
    os.write(fd, str(holder_pid).encode())
    return fd


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.1/L-9 — retry double-deploy блокируется
# · Scenario: lock platform-deploy-<project>.lock занят ДРУГИМ процессом (raw-flock holder) →
# ·   deploy() → FAILED «locked by PID 99999» (не параллельный compose-up)
# · Last fail: 2026-08-05 — lock был задокументирован в контракте, но НЕ реализован (L-1)
# · Remove if: deploy-lock semantics change
@ldd_trajectory
@pytest.mark.parametrize("run", range(5), ids=[f"run{i}" for i in range(5)])
def test_deploy_lock_blocked_when_held_by_other_process(
    run: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T9.1: deploy() при занятом lock → FAILED + «locked by PID X» (N прогонов — инвариант)."""
    caplog.set_level(logging.INFO)
    orch = _make_orchestrator(tmp_path, monkeypatch)
    lock_path = platform_lock_path("testproj")
    fd = _hold_raw_flock(lock_path, holder_pid=99999)
    try:
        result = orch.deploy(
            project_name="testproj",
            channel=LocalChannel(),
            project_dir=str(tmp_path / "projects" / "testproj"),
        )
        assert result.status == DeployStatus.FAILED, "конкурентный deploy обязан завершиться FAILED"
        assert "locked by PID 99999" in (result.error_info or ""), (
            f"ошибка обязана содержать PID владельца: {result.error_info!r}"
        )
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    logger.critical("[IMP:9][test] run=%d: concurrent deploy blocked with holder PID — OK (T9.1)", run)


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.1 — lock RELEASED после успешного deploy
# · Scenario: deploy() успешен → повторный deploy того же проекта НЕ заблокирован (flock снят)
# · Last fail: N/A (новое поведение W9)
# · Remove if: deploy-lock semantics change
@ldd_trajectory
def test_deploy_lock_released_after_successful_deploy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T9.1: после deploy lock снят — второй deploy проходит (try/finally release)."""
    caplog.set_level(logging.INFO)
    orch = _make_orchestrator(tmp_path, monkeypatch)
    lock_path = platform_lock_path("testproj")

    # Первый deploy: _prepare валидирует имя → dry-run SKIPPED (без side-effect, lock снимается)
    result1 = orch.deploy(
        project_name="testproj",
        channel=LocalChannel(),
        project_dir=str(tmp_path / "projects" / "testproj"),
        dry_run=True,
    )
    assert result1.is_success(), f"dry-run deploy обязан пройти: {result1.error_info}"

    # Lock файл существует, но flock снят — повторный non-blocking acquire проходит
    lock = FileLock(lock_path, timeout=0.0)
    lock.acquire()  # не должно быть FileLockError (иначе lock не освобождён)
    lock.release()
    logger.critical("[IMP:9][test] deploy lock released after deploy — second acquire OK (T9.1)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.10/L-12 — snapshot атомарный + prune под lock
# · Scenario: 12× create_snapshot → retention 10 (prune), 0 .tmp-мусора (атомарность),
# ·   payload_backup_dir персистится в payload/<id>/; lock не остаётся зажат (reentrant release)
# · Last fail: 2026-08-05 — create_snapshot писал напрямую (частичная запись), prune без lock,
# ·   payload-бэкап не сохранялся (T9.8/T9.10)
# · Remove if: snapshot semantics change
@ldd_trajectory
def test_deploy_history_snapshot_atomic_prune_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T9.10: атомарная запись snapshot + prune под lock + payload_dir персист."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("PLATFORM_LOCK_DIR", str(tmp_path / "locks"))
    history = DeployHistory(projects_base=str(tmp_path / "projects"))

    # Payload-бэкап (T9.8): dir с предыдущими payload-файлами
    backup = tmp_path / "payload-backup"
    backup.mkdir()
    (backup / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx:alpine\n", encoding="utf-8")

    snap_ids: list[str] = [
        history.create_snapshot(
            project="testproj", version=f"v{i}", health_status="healthy", payload_backup_dir=str(backup)
        )
        for i in range(12)
    ]

    snap_dir = tmp_path / "projects" / "testproj" / ".deploy-snapshots"
    remaining = sorted(f for f in os.listdir(snap_dir) if f.endswith(".json"))
    assert len(remaining) == 10, f"retention=10 нарушен: {len(remaining)} snapshot(ов)"
    # snapshot_id = <ISO8601-ts>-<uuid8>: в тесте все 12 созданы в одну секунду, поэтому
    # порядок prune = лексикографический по (ts, uuid). Детерминированный инвариант:
    # принуждаются ровно 2 лексикографически НАИМЕНЬШИХ имени, остаются 10 (включая максимум).
    all_ids_sorted = sorted(snap_ids)
    assert all_ids_sorted[-1] + ".json" in remaining, "лексикографический максимум обязан сохраниться"
    pruned = [sid for sid in all_ids_sorted[:2] if sid + ".json" not in remaining]
    assert len(pruned) == 2, f"prune должен удалить ровно 2 наименьших из 12: pruned={pruned}"

    # Атомарность: никакого .tmp-мусора от partial write
    tmp_leftovers = [f for f in os.listdir(snap_dir) if f.endswith(".tmp")]
    assert not tmp_leftovers, f"атомарная запись не должна оставлять tmp: {tmp_leftovers}"

    # Payload-персист (T9.8): payload/<snapshot_id>/ содержит файл бэкапа
    # ⚠️ TRAP[BUG] · 2026-08-05 · MED · Flaky assertion: payload бьётся по snap_ids[-1]
    # · Symptom: test_deploy_history_snapshot_atomic_prune_payload падал ~1 из 6 прогонов
    # ·   («payload-бэкап обязан персиститься»), проходил в check, падал в gate (static_audit)
    # · Root: все 12 snapshot-ов создаются в одну секунду (<ts>-<uuid8>); prune (retention=10)
    # ·   удаляет 2 ЛЕКСИКОГРАФИЧЕСКИ НАИМЕНЬШИХ id (и их payload-диры, _prune_snapshots).
    # ·   snap_ids[-1] (последний созданный) имеет СЛУЧАЙНЫЙ uuid8 → с вероятностью ~2/12 он
    # ·   попадает в pruned → его payload-дир удалён → assertion ложно падает.
    # · Fix: assertion бьётся по all_ids_sorted[-1] (лексикографический максимум) — он
    # ·   ГАРАНТИРОВАННО переживает prune (уже проверено выше: all_ids_sorted[-1] в remaining).
    # · Prevention: assertion на выживший снапшот, не на «последний созданный» (случайный ключ).
    surviving_id = all_ids_sorted[-1]
    payload_dir = snap_dir / "payload" / surviving_id
    assert (payload_dir / "docker-compose.yml").is_file(), "payload-бэкап обязан персиститься в snapshot"

    # Содержимое снапшота: payload_dir записан + compose_state
    data = history.read_snapshot("testproj", surviving_id)
    assert data is not None
    assert data["payload_dir"] == str(payload_dir), "snapshot JSON обязан содержать payload_dir"
    logger.critical("[IMP:9][test] snapshot atomic + prune(10) + payload_dir persisted — OK (T9.10)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.1 — deploy_many блокируется на занятом lock
# · Scenario: lock занят → deploy_many по проекту возвращает FAILED (lock per-project)
# · Last fail: N/A (новое поведение W9)
# · Remove if: deploy-lock semantics change
@ldd_trajectory
def test_deploy_many_respects_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T9.1: deploy_many → deploy() каждого проекта проверяет lock."""
    caplog.set_level(logging.INFO)
    orch = _make_orchestrator(tmp_path, monkeypatch)
    lock_path = platform_lock_path("blockedproj")
    fd = _hold_raw_flock(lock_path, holder_pid=4242)
    try:
        results = orch.deploy_many(
            project_names=["okproj", "blockedproj"],
            channel=LocalChannel(),
            project_base_dir=str(tmp_path / "projects"),
            dry_run=True,
        )
        assert results[0].is_success(), "проект без lock деплоится"
        assert results[1].status == DeployStatus.FAILED, "проект под lock блокируется"
        assert "locked by PID 4242" in (results[1].error_info or "")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    logger.critical("[IMP:9][test] deploy_many respects per-project lock — OK (T9.1)")
