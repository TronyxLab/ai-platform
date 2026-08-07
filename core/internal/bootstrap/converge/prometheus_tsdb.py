#!/usr/bin/env python3
# GREP_SUMMARY: converge-monitoring, r10, prometheus, tsdb, wal, blocks, clock-skew, corruption-guard, self-heal, docker-logs
# STRUCTURE: ▶ reconcile_prometheus_tsdb → ◇ docker daemon? → ◇ prometheus container? → ○ docker logs --tail → ◇ corruption markers? → ○ exec wget /-/healthy (guard 2) → ○ backup wal/blocks + compose up -d → ⊕ report {R10}
# region MODULE_CONTRACT
## @purpose  R10 reconcile_prometheus_tsdb — Prometheus TSDB self-heal после clock-skew (142 W3, A4).
##           Строгий guard: очистка wal/blocks ТОЛЬКО при ДЕТЕКТИРОВАННОМ коррапте —
##           (а) docker logs prometheus содержит маркеры коррапта (too far into the future /
##           out of bounds / head truncated / invalid|corrupted block / error loading blocks)
##           И (б) targets недоступны (exec wget /-/healthy — канон healthcheck мониторинг-модуля).
##           Здоровый TSDB → no-op (никогда не чистим без детекции).
## @scope    converge/monitoring.py: reconcile_prometheus_tsdb. Вызывается оркестратором
##           reconciler.py (R10). Работает НА ХОСТЕ (host-путь volume через docker inspect
##           Mounts.Source — prom/prometheus scratch-based, очистка exec-шеллом невозможна).
## @invariants
##   - НИКОГДА не чистит TSDB без маркеров коррапта в логах (A4: ручная чистка после chaos T4 —
##     теперь платформенный self-heal; guard-логика обязательна — риск §8 W3)
##   - Второй guard — targets-проба: exec wget /-/healthy (канон healthcheck мониторинг-модуля;
##     wget присутствует в prom/prometheus — его же использует compose healthcheck)
##   - Очистка = backup (mv wal/ blocks/ → .corrupt-<ts>/) + compose up -d (канон R9 self-heal);
##     backup, НЕ rm — восстановимость при ложной детекции
##   - dry_run/report_only → LDD "WOULD" + report, 0 мутаций
##   - Docker-команды ТОЛЬКО через shared/docker_ops (гейт docker_sole_path, allowlist пуст;
##     docker logs — новый примитив docker_ops.docker_logs, 142 W3)
## @rationale 142 W3 (Q3 «а»): converge-юнит R10 + T4-assert. Сэмплы с будущими timestamp'ами
##           отклоняются prometheus → TSDB «мёртв» (wal/blocks не загружаются) → ручная чистка
##           в циклах 1/2 141 (A4). Платформенный self-heal с двойным guard-условием.
## @changes  2026-08-06 | Created (142 W3)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from core.internal.bootstrap.converge.infra import DOCKER_TIMEOUT, report_add, set_exit
from core.internal.shared import docker_ops
from core.internal.shared.compose_files import resolve_compose_file
from core.internal.shared.docker_compose import docker_compose_up as _shared_docker_compose_up
from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT

logger = logging.getLogger(__name__)

# ── Константы ──
PROMETHEUS_CONTAINER = "prometheus"
PROMETHEUS_VOLUME = "prometheus-data"
# Маркеры коррапта TSDB в docker logs prometheus. Главный — clock-skew «too far into the future»;
# прочие — структурные повреждения wal/blocks. Case-insensitive поиск.
CORRUPTION_MARKERS: tuple[str, ...] = (
    "too far into the future",
    "out of bounds",
    "head truncated",
    "invalid block",
    "corrupted block",
    "error loading blocks",
)
# Проба здоровья TSDB: тот же wget, что в healthcheck мониторинг-модуля
_HEALTH_PROBE = ["wget", "-q", "-O-", "http://127.0.0.1:9090/-/healthy"]
LOGS_TAIL_LINES = 400  # релевантное окно логов для детекции коррапта


# region FUNC__prometheus_containers
## @purpose  Наличие контейнера prometheus (docker ps -a — B22-канон all=True).
## @io       ⇥ None → ⎋ list[str] — имена контейнеров ([] = нет)
## @complexity O(1) — 1 docker ps
def _prometheus_containers() -> list[str]:
    """Return prometheus container names via docker ps -a (all=True, B22)."""
    ps_r = docker_ops.docker_ps(
        filters=[f"name={PROMETHEUS_CONTAINER}"],
        format="{{.Names}}",
        timeout=DOCKER_TIMEOUT,
        all=True,
    )
    if ps_r.returncode != 0:
        logger.warning("[IMP:8][r10][ps] docker ps failed: %s", (ps_r.stderr or "").strip()[:120])
        return []
    return [c.strip() for c in ps_r.stdout.splitlines() if c.strip()]


# endregion FUNC__prometheus_containers


# region FUNC__tsdb_corruption_detected
## @purpose  Guard 1: детекция коррапта по docker logs --tail (docker_ops.docker_logs, 142 W3).
## @io       ⇥ container: str → ⎋ tuple[bool, str] — (коррапт, найденный маркер)
## @complexity O(L) — L = строк логов
## @invariants
##   - docker logs сбой/пусто → (False, "") — НЕ чистим (консервативно)
##   - Маркеры ищутся case-insensitive
def _tsdb_corruption_detected(container: str) -> tuple[bool, str]:
    """Scan prometheus docker logs for TSDB corruption markers (guard 1)."""
    logs_r = docker_ops.docker_logs(container, tail=LOGS_TAIL_LINES, timeout=DOCKER_TIMEOUT)
    lowered = (logs_r.stdout or "").lower()
    if not lowered:
        logger.info("[IMP:7][r10][detect] docker logs empty/failed — no corruption evidence")
        return False, ""
    for marker in CORRUPTION_MARKERS:
        if marker in lowered:
            logger.warning("[IMP:9][r10][detect] TSDB corruption marker found: %r", marker)
            return True, marker
    logger.info("[IMP:7][r10][detect] No TSDB corruption markers in logs (tail=%d)", LOGS_TAIL_LINES)
    return False, ""


# endregion FUNC__tsdb_corruption_detected


# region FUNC__targets_unreachable
## @purpose  Guard 2: проба TSDB через exec wget /-/healthy (канон healthcheck-модуля).
## @io       ⇥ container: str → ⎋ bool (True = targets недоступны → коррапт подтверждён)
## @complexity O(1) — 1 docker exec
## @invariants
##   - rc==0 (healthy) → False (ложная тревога — НЕ чистим)
##   - rc!=0/None → True (подтверждение коррапта)
def _targets_unreachable(container: str) -> bool:
    """Probe prometheus /-/healthy via docker exec wget (guard 2)."""
    exec_r = docker_ops.docker_exec(container, _HEALTH_PROBE, timeout=DOCKER_TIMEOUT)
    if exec_r is not None and exec_r.returncode == 0:
        logger.info("[IMP:7][r10][probe] /-/healthy responds — targets reachable, NO cleanup")
        return False
    logger.warning("[IMP:9][r10][probe] /-/healthy FAILS — targets unreachable, corruption confirmed")
    return True


# endregion FUNC__targets_unreachable


# region FUNC__tsdb_host_dir
## @purpose  Host-путь TSDB volume (docker inspect Mounts.Source для prometheus-data).
## @io       ⇥ container: str → ⎋ str | None (None = mountpoint не найден)
## @complexity O(1) — 1 docker inspect
def _tsdb_host_dir(container: str) -> str | None:
    """Resolve host source dir of prometheus-data volume (Mounts.Source)."""
    fmt = '{{range .Mounts}}{{if eq .Name "prometheus-data"}}{{.Source}}{{end}}{{end}}'
    inspect_r = docker_ops.docker_inspect(container, format=fmt, timeout=DOCKER_TIMEOUT)
    source = (inspect_r.stdout or "").strip()
    if not source:
        logger.warning("[IMP:7][r10][mount] prometheus-data volume mount not found for %s", container)
        return None
    logger.info("[IMP:8][r10][mount] TSDB host dir: %s", source)
    return source


# endregion FUNC__tsdb_host_dir


# region FUNC__cleanup_tsdb
## @purpose  Backup + очистка wal/ и blocks/ (mv в .corrupt-<ts>/, НЕ rm — восстановимость).
## @io       ⇥ tsdb_dir: str → ⎋ bool (True = очищено)
## @complexity O(1) — ≤2 shutil.move
## @invariants
##   - backup внутри volume ({tsdb_dir}/.corrupt-<ts>/) — тот же диск, мгновенный mv
##   - Отсутствующие wal/blocks → не ошибка (prometheus создаст при старте)
def _cleanup_tsdb(tsdb_dir: str) -> bool:
    """Move wal/ and blocks/ to .corrupt-<ts>/ backup (recoverable), 142 W3."""
    root = Path(tsdb_dir)
    ts = time.strftime("%Y%m%dT%H%M%S")
    backup_root = root / f".corrupt-{ts}"
    ok = True
    for sub in ("wal", "blocks"):
        src = root / sub
        if src.is_dir():
            dst = backup_root / sub
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                logger.info("[IMP:9][r10][cleanup] %s → %s (backup)", src, dst)
            except OSError as exc:
                logger.error("[IMP:10][r10][cleanup] Cannot move %s: %s", src, exc)
                ok = False
        else:
            logger.info("[IMP:7][r10][cleanup] %s absent — nothing to back up", src)
    return ok


# endregion FUNC__cleanup_tsdb


# region FUNC_reconcile_prometheus_tsdb
## @purpose  R10: Prometheus TSDB self-heal — ТОЛЬКО при двойном guard. Здоровый TSDB → no-op.
##           Коррапт → backup wal/blocks + compose up -d (канон R9). Аналог A4 — автоматизирован.
## @io       ⇥ node_yaml_path: str (резерв, контейнер фиксирован), dry_run, report_only →
##              ⎋ dict drift-entry {R10}
## @complexity O(1) + docker logs/inspect/exec/compose
## @invariants
##   - Docker daemon недоступен → fail (exit 2, как R9)
##   - Контейнера prometheus нет → skipped (модуль monitoring не задеплоен)
##   - Нет коррапт-маркеров → converged (no-op — здоровый TSDB НЕ чистится, риск §8 W3)
##   - Маркер есть, но targets отвечают → converged (ложная тревога — НЕ чистим)
##   - Коррапт подтверждён: dry_run/report_only → mutated (WOULD), 0 мутаций
def reconcile_prometheus_tsdb(
    node_yaml_path: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict:
    """Reconcile Prometheus TSDB — self-heal только при детектированном коррапте (142 W3)."""
    unit = "R10"
    logger.info("[IMP:8][converge][%s] START: reconcile_prometheus_tsdb (TSDB self-heal)", unit)

    # ── 1. Docker daemon ──
    docker_info_r = docker_ops.docker_info(timeout=DOCKER_TIMEOUT)
    if docker_info_r.returncode != 0:
        msg = "Docker daemon not available — skipping TSDB reconciliation"
        logger.error("[IMP:10][converge][%s] FAIL: %s", unit, msg)
        report_add(unit, "fail", msg)
        set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── 2. Контейнер prometheus ──
    containers = _prometheus_containers()
    if not containers:
        logger.info("[IMP:9][converge][%s] SKIP: no prometheus container (monitoring module not deployed)", unit)
        report_add(unit, "skipped", "No prometheus container")
        return {"unit": unit, "status": "skipped", "detail": "No prometheus container"}

    container = containers[0]

    # ── 3. Guard 1: коррапт-маркеры в логах ──
    corrupted, marker = _tsdb_corruption_detected(container)
    if not corrupted:
        logger.info("[IMP:9][converge][%s] CONVERGED: no TSDB corruption markers (no-op)", unit)
        report_add(unit, "converged", "No TSDB corruption detected")
        return {"unit": unit, "status": "converged", "detail": "No TSDB corruption detected"}

    # ── 4. Guard 2: targets недоступны ──
    if not _targets_unreachable(container):
        logger.info(
            "[IMP:9][converge][%s] CONVERGED: corruption marker but /-/healthy responds — NOT cleaning (guard 2)",
            unit,
        )
        report_add(unit, "converged", "Corruption marker without target outage — no cleanup (guard 2)")
        return {"unit": unit, "status": "converged", "detail": "Guard 2 not satisfied — no cleanup"}

    # ── 5. Коррапт подтверждён: mountpoint + backup + очистка + restart ──
    tsdb_dir = _tsdb_host_dir(container)
    if not tsdb_dir:
        logger.error("[IMP:10][converge][%s] FAIL: cannot resolve TSDB volume mount — manual cleanup required", unit)
        report_add(unit, "fail", "Cannot resolve prometheus-data mountpoint")
        set_exit(2)
        return {"unit": unit, "status": "fail", "detail": "Cannot resolve prometheus-data mountpoint"}

    if dry_run or report_only:
        logger.info(
            "[IMP:9][converge][%s] WOULD clean TSDB (wal/blocks backup) at %s and restart prometheus (marker=%r)",
            unit,
            tsdb_dir,
            marker,
        )
        report_add(unit, "mutated", f"WOULD clean TSDB at {tsdb_dir} (corruption: {marker})")
        set_exit(1)
        return {"unit": unit, "status": "mutated", "detail": f"WOULD clean TSDB (marker: {marker})"}

    # ── Реальная очистка ──
    logger.warning(
        "[IMP:9][converge][%s] TSDB corruption confirmed (marker=%r) — backing up wal/blocks and restarting",
        unit,
        marker,
    )
    if not _cleanup_tsdb(tsdb_dir):
        logger.error("[IMP:10][converge][%s] FAIL: TSDB backup/move failed — prometheus NOT restarted", unit)
        report_add(unit, "fail", "TSDB cleanup failed")
        set_exit(2)
        return {"unit": unit, "status": "fail", "detail": "TSDB cleanup failed"}

    # Restart через compose up -d (канон R9 self-heal; контейнер пересоздаётся на чистом TSDB)
    modules_root = os.environ.get(
        "PLATFORM_MODULES_DIR",
        str(Path(__file__).resolve().parents[2] / "modules"),
    )
    compose_file = resolve_compose_file(os.path.join(modules_root, "monitoring"))
    if not compose_file:
        logger.error("[IMP:10][converge][%s] compose file for monitoring module not found", unit)
        report_add(unit, "fail", "monitoring compose file not found after TSDB cleanup")
        set_exit(2)
        return {"unit": unit, "status": "fail", "detail": "monitoring compose file not found"}

    up_ok = _shared_docker_compose_up(
        str(Path(compose_file).parent),
        timeout=COMPOSE_UP_TIMEOUT,
        compose_args=["-f", str(compose_file)],
    )
    if up_ok:
        logger.info("[IMP:9][converge][%s] MUTATED: TSDB cleaned + prometheus restarted (self-heal)", unit)
        report_add(unit, "mutated", f"TSDB cleaned (corruption: {marker}), prometheus restarted")
        set_exit(1)
        return {"unit": unit, "status": "mutated", "detail": f"TSDB cleaned + restarted (marker: {marker})"}

    logger.error("[IMP:10][converge][%s] FAIL: TSDB cleaned but prometheus restart failed", unit)
    report_add(unit, "fail", "TSDB cleaned but prometheus restart failed")
    set_exit(2)
    return {"unit": unit, "status": "fail", "detail": "Prometheus restart failed after TSDB cleanup"}


# endregion FUNC_reconcile_prometheus_tsdb
