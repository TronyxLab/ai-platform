"""
DeployHistory — snapshot-based deploy history storage for rollback support.
"""
# GREP_SUMMARY: deploy-history, snapshots, rollback, storage, json, retention, file-lock, audit-package, rollback-fact, 170-W4-B3
# STRUCTURE: ▶ DeployHistory.__init__(projects_base) → create_snapshot(project, version, compose_state, health_status, payload_hash)
#            → ○ write JSON to /opt/projects/<name>/.deploy-snapshots/<snapshot_id>.json → ○ prune to keep last 10 → ○ read_snapshot(snapshot_id)
#            → ○ list_snapshots(project) → rollback(project, snapshot_id) → ⎋ snapshot data
# region MODULE_CONTRACT
## @purpose  Deploy history storage using JSON snapshots on disk. Each snapshot captures
##           project version, docker compose state, health status, and payload hash.
##           Enables rollback() in DeployOrchestrator: restores compose state from snapshot.
## @scope    Пакет deploy/audit/ (170 W4-B3) — вынесен из deploy/deploy_history.py (1:1, 407 LOC).
##           Используется DeployOrchestrator для history tracking и rollback. File-based storage
##           at /opt/projects/<name>/.deploy-snapshots/<snapshot_id>.json.
## @invariants
##   1. Storage path: /opt/projects/<name>/.deploy-snapshots/<snapshot_id>.json
##   2. Snapshot format: { project, version, timestamp, compose_state, health_status, payload_hash,
##      payload_dir } — payload_dir (T9.8): пред-деплойные payload-файлы для rollback;
##      compose_state.previous_image (REF-0004, additive): docker image ID предыдущего релиза —
##      якорь compose-rollback (re-tag → deploy без doomed-pull из registry);
##      rollback/rollback_from_snapshot (D8, 2026-09-01, additive): rollback-факт внешнего/ручного
##      отката — `status` CLI показывает last_deploy с rollback=True вместо врущей записи
##   3. Retention: keep last 10 snapshots (prune on create, под deploy lock — T9.10)
##   4. File lock: platform_lock_path (shared/file_lock, T9.1): PLATFORM_LOCK_DIR env → иначе
##      /var/lock/platform-deploy-{project}.lock; reentrant (deploy() уже держит тот же замок)
##   5. Snapshot ID: ISO8601 timestamp (second precision)
##   6. Thread/process-safe via fcntl.flock on lock file; write — атомарный (atomic_writer, T9.10)
## @rationale DevPlan 089 DD5: In-memory history lost on VPS restart. File-based snapshots
##            survive crashes, enable audit trail, and support version-specific rollback.
##            Retention of 10 balances history vs disk (avg snapshot ~5 KB JSON).
##            DevPlan 136 W9 T9.10 (L-12): create_snapshot писал напрямую (частичная запись при
##            crash) и prune не был под lock (гонка двух create_snapshot → оба prune друг друга);
##            payload-бэкап (T9.8) персистится для восстановления payload-файлов при rollback.
## @changes 2026-07-30 | DevPlan 089 T6.5 — Created
## @changes 2026-08-05 | DevPlan 136 W9 T9.8/T9.10 — payload_dir; атомарная запись; flock-прин
## @changes 2026-08-15 | 170 W4-B3 — moved to deploy/audit/history.py (1:1, фасад deploy_history.py сохранён)
## @changes 2026-09-01 | D8 (внешний rollback) — create_snapshot(+rollback, +rollback_from_snapshot):
##             additive ключи rollback-факта (wire-freeze сохранён — потребители с фикс. схемой
##             игнорируют неизвестные поля)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import shutil
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

# DevPlan 136 W9 T9.10 (L-12): единый атомарный writer (unique tmp + fsync + replace).
from core.internal.shared.atomic_writer import atomic_write_json as _atomic_write_json

# B2: канонический дефолт PROJECTS_BASE — shared/deploy_paths (литерал /opt/projects удалён)
from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE

# DevPlan 136 W9 T9.1/T9.10: канонический flock + путь deploy-замка (shared — deploy-слой
# НЕ импортирует bootstrap/). Reentrant: deploy() держит lock → create_snapshot → prune
# берёт тот же замок без дедлока (depth-счётчик в FileLock).
from core.internal.shared.file_lock import FileLock as _FileLock
from core.internal.shared.file_lock import platform_lock_path as _platform_lock_path

# B4: единый канон subprocess (shared/subprocess_io) — chown снапшот-директории (B19)
from core.internal.shared.subprocess_io import run_subprocess as _run_subprocess

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = ".deploy-snapshots"
LOCK_DIR = "/var/lock"
MAX_SNAPSHOTS = 10
# DeployHistory-owned snapshot file pattern: <ISO8601-ts>-<8-hex>.json (create_snapshot format)
_SNAPSHOT_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}\.json$")
# Payload backup dir for a snapshot: payload/<snapshot_id>/ (T9.8/T9.10)
_PAYLOAD_BACKUP_DIR = "payload"


# region CLASS_DeployHistory


class DeployHistory:
    """Manage deploy snapshots for rollback support.

    ## @purpose — Create, read, list, and prune deploy snapshots. Each snapshot captures
    ##            the full deploy state for later rollback.
    ## @io — ⇥ project, version, compose_state, health_status, payload_hash → ⎋ snapshot_id (str)
    ##        ⇥ project, snapshot_id → ⎋ dict (snapshot data)
    ## @complexity — O(1) create, O(1) read, O(N) list, O(N) prune where N = snapshots
    ## @invariants
    ##   - Retention: always keep last MAX_SNAPSHOTS (10)
    ##   - Lock file: /var/lock/platform-deploy-{project}.lock
    ##   - Snapshot dir created automatically if absent
    ##   - Prune happens AFTER successful write (never lose current snapshot)
    """

    def __init__(
        self, projects_base: str = DEFAULT_PROJECTS_BASE, *, run_subprocess: Callable[..., object] | None = None
    ):
        self.projects_base = projects_base
        # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · run_subprocess-инъекция (167 D3/D6)
        # · Rejected: тест патчил module-level _run_subprocess через monkeypatch
        # · Reason: seam = тестируемость реального вызова — chown-вызов (B19) наблюдается
        # ·   fake-раннером без глобального патча модуля
        # · Rev: при выносе subprocess-канала в shared runner — синхронизировать протокол
        self._run_subprocess: Callable[..., object] = run_subprocess if run_subprocess is not None else _run_subprocess

    def _snapshot_dir(self, project: str) -> str:
        """Get the snapshot directory for a project.

        Args:
            project: Project name.

        Returns:
            Path to snapshot directory.
        """
        return os.path.join(self.projects_base, project, SNAPSHOT_DIR)

    @staticmethod
    def _lock_path(project: str) -> str:
        """Get the lock file path for a project.

        Args:
            project: Project name.

        Returns:
            Path to lock file.
        """
        # T9.1/T9.10: канонический путь — shared/file_lock.platform_lock_path
        # (PLATFORM_LOCK_DIR env override / /var/lock/platform-deploy-{project}.lock)
        return _platform_lock_path(project)

    def _snapshot_path(self, project: str, snapshot_id: str) -> str:
        """Get the full path for a snapshot file.

        Args:
            project: Project name.
            snapshot_id: Snapshot ID (ISO8601 timestamp).

        Returns:
            Full path to snapshot JSON file.
        """
        return os.path.join(self._snapshot_dir(project), f"{snapshot_id}.json")

    def create_snapshot(
        self,
        project: str,
        version: str = "",
        compose_state: dict[str, object] | None = None,
        health_status: str = "",
        payload_hash: str = "",
        payload_backup_dir: str | None = None,
        rollback: bool = False,
        rollback_from_snapshot: str = "",
    ) -> str:
        """Create a deploy snapshot.

        Args:
            project: Project name.
            version: Deployed version/tag.
            compose_state: Docker compose state (containers, images).
            health_status: Health after deploy.
            payload_hash: SHA256 hash of deployed payload.
            payload_backup_dir: Optional dir with the PREVIOUS payload files (captured before
                overwrite — T9.8). Copies persisted into payload/<snapshot_id>/ for rollback.
            rollback: D8 (2026-09-01) — снапшот помечается как rollback-факт (внешний/ручной
                rollback). `status` CLI показывает last_deploy с rollback=True — история не врёт.
            rollback_from_snapshot: D8 — snapshot_id источника отката (traceability).

        Returns:
            Snapshot ID (ISO8601 timestamp).
        """
        snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-") + uuid4().hex[:8]

        snapshot: dict[str, object] = {
            "snapshot_id": snapshot_id,
            "project": project,
            "version": version,
            "timestamp": time.time(),
            "compose_state": compose_state or {},
            "health_status": health_status,
            "payload_hash": payload_hash,
        }
        # D8 (2026-09-01): rollback-факт — additive ключи (wire-freeze: потребители с фикс.
        # схемой игнорируют неизвестные поля; list/status читают dict как есть).
        if rollback:
            snapshot["rollback"] = True
        if rollback_from_snapshot:
            snapshot["rollback_from_snapshot"] = rollback_from_snapshot

        # Ensure snapshot dir exists
        snap_dir = self._snapshot_dir(project)
        os.makedirs(snap_dir, exist_ok=True)

        # ⚠️ TRAP[BUG] · 2026-08-06 · HI · B19 (141 r2): снапшот-директория root:root блокировала receive
        # · Symptom: бутстрап (φ8 context_deployer, root) создал .deploy-snapshots root:root →
        # ·   receive-деплой под ci-deploy падал «[Errno 13] Permission denied .../payload» (auditing FAILED).
        # · Fix: best-effort chown ci-deploy:ci-deploy — под root (бутстрап) чинит владельца;
        # ·   под ci-deploy (receive) chown вернёт rc=1 → non_fatal WARN (владелец уже он).
        # ·   OSError (экзотика: execl, тестовые mock) — WARN, НЕ проброс (verify не должен падать).
        # · Rev: если снапшоты переедут под другого системного юзера — обновить имя.
        try:
            self._run_subprocess(["chown", "ci-deploy:ci-deploy", snap_dir], non_fatal=True, fatal_rc=(127,))
        except OSError as e:
            logger.warning("[IMP:8][DeployHistory][chown] chown %s non-fatal skip: %s", snap_dir, e)

        # T9.10 (L-12): prune + write под reentrant deploy lock (тот же, что T9.1 —
        # deploy() уже держит его; вне deploy (manual snapshot) — acquire здесь).
        lock = _FileLock(self._lock_path(project), timeout=30.0)
        lock.acquire()
        try:
            # ── T9.8: персист payload-бэкапа (предыдущие payload-файлы) ──
            payload_dir: str | None = None
            if payload_backup_dir and os.path.isdir(payload_backup_dir):
                payload_dir = os.path.join(snap_dir, _PAYLOAD_BACKUP_DIR, snapshot_id)
                os.makedirs(payload_dir, exist_ok=True)
                for item in os.listdir(payload_backup_dir):
                    src = os.path.join(payload_backup_dir, item)
                    if os.path.isfile(src):
                        try:
                            shutil.copy2(src, os.path.join(payload_dir, item))
                        except OSError as e:
                            logger.warning(
                                "[IMP:7][DeployHistory][create] Cannot persist payload backup %s (non-fatal): %s",
                                src,
                                e,
                            )
                if payload_dir:
                    snapshot["payload_dir"] = payload_dir
                    logger.info(
                        "[IMP:9][DeployHistory][create] Payload backup persisted for %s → %s",
                        project,
                        payload_dir,
                    )

            # Write snapshot — T9.10: atomic (unique tmp + fsync + os.replace), не прямой f.write
            filepath = self._snapshot_path(project, snapshot_id)
            try:
                _atomic_write_json(filepath, snapshot, mode=0o644)
                logger.info(
                    "[IMP:9][DeployHistory][create] Created snapshot %s for %s (version=%s)",
                    snapshot_id,
                    project,
                    version,
                )
            except OSError as e:
                logger.error(
                    "[IMP:10][DeployHistory][create] Failed to write snapshot for %s: %s",
                    project,
                    e,
                )
                raise

            # Prune old snapshots (под тем же lock — T9.10)
            self._prune_snapshots(project)
        finally:
            lock.release()

        return snapshot_id

    def read_snapshot(self, project: str, snapshot_id: str) -> dict[str, object] | None:
        """Read a deploy snapshot.

        Args:
            project: Project name.
            snapshot_id: Snapshot ID to read.

        Returns:
            Snapshot dict or None if not found.
        """
        filepath = self._snapshot_path(project, snapshot_id)
        if not os.path.isfile(filepath):
            logger.warning(
                "[IMP:8][DeployHistory][read] Snapshot not found: %s for %s",
                snapshot_id,
                project,
            )
            return None

        try:
            with pathlib.Path(filepath).open(encoding="utf-8") as f:
                data = cast(dict[str, object], json.load(f))
            logger.info(
                "[IMP:9][DeployHistory][read] Read snapshot %s for %s",
                snapshot_id,
                project,
            )
        except (OSError, json.JSONDecodeError) as e:
            logger.error(
                "[IMP:9][DeployHistory][read] Failed to read snapshot %s for %s: %s",
                snapshot_id,
                project,
                e,
            )
            return None
        else:
            return data

    def list_snapshots(self, project: str) -> list[dict[str, object]]:
        """List all snapshots for a project, newest first.

        Args:
            project: Project name.

        Returns:
            List of snapshot metadata dicts.
        """
        snap_dir = self._snapshot_dir(project)
        if not os.path.isdir(snap_dir):
            return []

        snapshots: list[dict[str, object]] = []
        # ruff: ignore[PLW0717] — внутри try есть break/continue/await/yield — извлечение ломает управляющий поток
        try:
            for fname in sorted(os.listdir(snap_dir), reverse=True):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(snap_dir, fname)
                try:
                    with pathlib.Path(fpath).open(encoding="utf-8") as f:
                        data = cast(dict[str, object], json.load(f))
                    snapshots.append(data)
                except (OSError, json.JSONDecodeError) as exc:
                    # 170 W2-A2 (B3): тихий continue → debug с контекстом (повреждённый/нечитаемый
                    # снапшот пропускается, остальные обрабатываются)
                    logger.debug(
                        "[IMP:5][DeployHistory][list] Skipping unreadable snapshot %s: %s",
                        fname,
                        exc,
                    )
                    continue

            logger.info(
                "[IMP:9][DeployHistory][list] Found %d snapshots for %s",
                len(snapshots),
                project,
            )
        except OSError as e:
            logger.warning(
                "[IMP:8][DeployHistory][list] Cannot list snapshots for %s: %s",
                project,
                e,
            )
            return []
        else:
            return snapshots

    def latest_snapshot(self, project: str, *, require_healthy: bool = False) -> dict[str, object] | None:
        """Get the latest snapshot for a project.

        REF-0004 (DevPlan 11 В1): авто-откат не должен целиться в заведомо нездоровый релиз —
        ``require_healthy=True`` выбирает последний снапшот с health_status="healthy";
        если здоровых нет — WARN-fallback на newest (caller решает, годится ли цель).

        Args:
            project: Project name.
            require_healthy: Prefer the newest HEALTHY snapshot (WARN-fallback to newest).

        Returns:
            Latest snapshot dict or None.
        """
        snapshots = self.list_snapshots(project)
        if not snapshots:
            return None
        if require_healthy:
            for snap in snapshots:
                if snap.get("health_status") == "healthy":
                    return snap
            logger.warning(
                "[IMP:8][DeployHistory][latest] No healthy snapshot for %s — falling back to newest "
                "(may be unhealthy; REF-0004)",
                project,
            )
        return snapshots[0]

    def rollback(self, project: str, snapshot_id: str | None = None) -> dict[str, object] | None:
        """Get snapshot data for rollback. Reads latest snapshot if snapshot_id is None.

        Args:
            project: Project name.
            snapshot_id: Specific snapshot ID, or None for latest.

        Returns:
            Snapshot data for rollback, or None if no snapshot available.
        """
        if snapshot_id:
            return self.read_snapshot(project, snapshot_id)
        return self.latest_snapshot(project)

    def _prune_snapshots(self, project: str) -> None:
        """Prune snapshots exceeding MAX_SNAPSHOTS retention.

        Args:
            project: Project name.
        """
        snap_dir = self._snapshot_dir(project)
        if not os.path.isdir(snap_dir):
            return

        # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
        try:
            # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · prune удалял СВЕЖИЕ снапшоты DeployHistory
            # · Symptom: DeployResult.snapshot_id непустой, но файл <snapshot_id>.json отсутствует;
            # ·   count в .deploy-snapshots не растёт (10→10). E2E DevPlan 095 T16 на tronyx-vps.
            # · Root: в .deploy-snapshots/ лежат ДВА namespace: DeployEngine (images-<epoch>.json,
            # ·   ps-<epoch>.json) и DeployHistory (<ISO8601>-<hex8>.json). Prune сортировал ВСЕ
            # ·   *.json по имени и pop(0) удалял первые — history-файлы ("2026..." < "images-...")
            # ·   удалялись первыми, включая только что записанный снапшот (после 5 деплоев
            # ·   = 10 engine-файлов → каждый новый history-снапшот мгновенно принудился).
            # · Fix: prune учитывает ТОЛЬКО собственные файлы DeployHistory (паттерн
            # ·   YYYYMMDDTHHMMSS-<8hex>.json) — engine-снапшоты не трогаются.
            # · Prevention: не смешивать namespace'ы в одном prune; фильтр по формату имени —
            # ·   инвариант формата snapshot_id (create_snapshot, строка ~121).
            # · 2026-08-02 (DevPlan 118 A7): DeployEngine._capture_deploy_snapshot УДАЛЁН —
            # ·   namespace engine-файлов (ps-<epoch>.json / images-<epoch>.json / .deploy-started)
            # ·   больше НЕ пишется. Фильтр по формату остаётся defensive (существующие файлы
            # ·   на VPS не удаляются, только новые записи — чистый history-namespace).
            files = sorted(f for f in os.listdir(snap_dir) if _SNAPSHOT_ID_RE.match(f))
            while len(files) > MAX_SNAPSHOTS:
                oldest = files.pop(0)
                pathlib.Path(os.path.join(snap_dir, oldest)).unlink()
                # T9.8/T9.10: payload-бэкап pruned snapshot удаляется вместе с ним
                payload_dir = os.path.join(snap_dir, _PAYLOAD_BACKUP_DIR, oldest[:-5])  # .json → id
                if os.path.isdir(payload_dir):
                    shutil.rmtree(payload_dir, ignore_errors=True)
                logger.info(
                    "[IMP:8][DeployHistory][prune] Pruned old snapshot: %s (retention=%d)",
                    oldest,
                    MAX_SNAPSHOTS,
                )
        except OSError as e:
            logger.warning(
                "[IMP:7][DeployHistory][prune] Failed to prune snapshots for %s: %s",
                project,
                e,
            )


# endregion CLASS_DeployHistory
