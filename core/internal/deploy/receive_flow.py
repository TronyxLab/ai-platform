#!/usr/bin/env python3
# GREP_SUMMARY: receive-flow, receive, tar, stdin, unpack, validate, deploy, pre-deploy-gate, L1, PRACTICES-BLOCK, forced-command, sha-pinning, JSON, E2, orchestrator-decomposition, flock, payload-tx, backup-restore, stale-compose, orphan-sweep, concurrency, resource-guards, tar-bomb, uncompressed-ceiling, statvfs
# STRUCTURE: ▶ ReceiveFlow.run ┌stdin tar + project_name + version┐ → unpack (statvfs guard → stream-extract: ceiling + entry-cap → staging) → validate (ai-platform.yaml + name) → ◆ flock per-project (fail-closed, REF-0011) → payload-tx ┌backup ВНЕ target → staging-copy → os.replace (без pre-remove) → stale-compose delete┐ → deploy (LocalChannel) → post-deploy chain → ⎋ JSON + exit code
# region MODULE_CONTRACT
## @purpose  VPS-side forced-command receive flow (DevPlan 119 E2) — экстракция receive() из
##           deploy/orchestrator.py (127 LOC, CC=15). Класс ReceiveFlow: unpack → validate →
##           pre-deploy L1 gate → deploy — изолированные методы с typed-контрактами. Сохраняет
##           поведение receive(): JSON OrchestratorDeployResult в stdout + exit code {0,1}.
## @scope    Consumed by DeployOrchestrator.receive() (тонкий фасад-делегат). Вызывается из
##           orchestrator_cli dispatch receive (SSH forced-command). 170 W10-B: receive_flow —
##           leaf в deploy (0 импортов → orchestrator); DeployOrchestrator инжектится
##           конструкторным DI (orchestrator_factory) из receive()-фасада — цикл разорван.
##           176 A.2: pre-deploy L1-гейт (verify_contracts l1_only) закрывает C1 root-эскалацию —
##           violation блокирует деплой ДО orchestrator.deploy (контейнеры не запускаются).
## @invariants
##   - Пустой stdin → JSON-ошибка + exit 1 (fail-fast, БЕЗ || true-масок)
##   - Payload > лимита (env PLATFORM_MAX_PAYLOAD_BYTES, default 64 MiB — REF-0015 ↓ с 1 GiB;
##     W4a: ленивый резолв через AppConfig в run()/конструкторе) → reject
##     ДО распаковки (T9.9: потоковое чтение, лимит по ходу, не после)
##   - Resource guards (REF-0015): stream-extract с running uncompressed ceiling 200 MB +
##     entry-count cap 512 (tar-бомба: высокий коэффициент сжатия / тысячи членов) —
##     нарушение → ConfigValidationError ДО записи гигантского члена на диск;
##     statvfs guard: свободное место ≥ ceiling ДО старта extract (ENOSPC mid-extract
##     на общей FS с postgres WAL/docker layers = node-wide outage)
##   - ai-platform.yaml отсутствует → JSON-ошибка + exit 1 (fail-fast)
##   - project_name из аргументов (валидируется validate_project_name + verb-reserve U-56);
##     фолбэк на ai-platform.yaml `name` — ТОЛЬКО для локальных/ручных вызовов без аргументов
##   - version ТОЛЬКО из аргументов (D5 sha-pinning); service = project_name
##   - Деплой через LocalChannel (payload уже извлечён — TRAP[DECISION] 2026-07-31)
##   - 176 A.2: pre-deploy L1-гейт — ОБЯЗАТЕЛЕН в receive-канале (security-гейт C1);
##     SKIP_PREFLIGHT=1 НЕ применим к receive (обход = та же дыра; SKIP остаётся скоупом
##     `make up` платформенных модулей, makefiles/modules.mk) — нарушение → _PreDeployBlocked,
##     [PRACTICES:BLOCK]-отчёт в stderr + JSON FAILED в stdout + exit 1 (контракт forced-command
##     и deliver-JSON-парсинг сохранены); проверяется STAGING ДО копирования в target_dir
##   - Атомарная замена payload (T9.8): staging-copy → per-file os.replace — сбой не оставляет
##     частично перезаписанных файлов; существующие payload-файлы бэкапятся в payload_backup_dir
##     (metadata) → rollback восстанавливает payload, не только compose (L-6)
##   - Payload-транзакция (REF-0105): backup_dir ВНЕ target_dir (system tmp, prefix
##     payload-backup-); исключение в фазе replace → restore-from-backup ДО rmtree backup;
##     replace БЕЗ pre-remove (нет ENOENT-окна для читателей); канонические compose-имена
##     (PROJECT_COMPOSE_FILENAMES), отсутствующие в staging, удаляются из target (stale-
##     compose переживал переименование и ПОБЕЖДАЛ по резолюции); orphan tmpdir от crashed
##     receives выметаются prefix-sweep'ом (возраст > 1h — защита активного параллельного receive)
##   - Конкурентность (REF-0011): run() держит reentrant per-project flock
##     (platform_lock_path) НА ВЕСЬ периметр мутации target_dir (flock-before-copy);
##     контеншн/таймаут → JSON FAILED + exit 1; EOFError в except-кортеже stdin-пути
##   - Пост-деплой цепочка best-effort (сбой → WARN, деплой НЕ фейлится)
##   - DeployOrchestrator НЕ импортируется (ни module-level, ни lazy) — DI через конструктор
##     (170 W10-B); orchestrator_factory=None → RuntimeError в _make_orchestrator (fail-fast)
## @rationale DevPlan 119 E2 (AUDIT-2 M2): receive() CC=15 в монолите orchestrator.py (1157 LOC).
##           Вынос в ReceiveFlow (unpack/validate/deploy) снижает CC до ≤8 на метод и даёт
##           изолированное тестирование (R5: test_orchestrator_receive_flow_parity).
##           DevPlan 136 W9 T9.8 (L-6)/T9.9 (L-7): атомарность staging + размерный лимит.
##           DevPlan 176 A.2 (C1): единственная реальная root-эскалация — ci-deploy исполняет
##           произвольный compose ДО L1-проверок; pre-up L1-гейт (тот же verify_contracts,
##           l1_only — НЕ дублирование гейта) закрывает канал до orchestrator.deploy.
## @changes  2026-08-02 · DevPlan 119 E2 — экстракция из DeployOrchestrator.receive()
## @changes  2026-08-05 · DevPlan 136 W9 T9.8/T9.9 — atomic staging + payload backup; MAX_PAYLOAD_BYTES
## @changes  2026-08-13 · DevPlan 160 W4a — import-time env убран (AppConfig, ленивый резолв)
## @changes  2026-08-15 · DevPlan 170 W10-B — цикл receive_flow↔orchestrator разорван:
##           DeployOrchestrator-импорты (TYPE_CHECKING:58, lazy:231/382) → конструкторный DI
##           (orchestrator_factory); shared-листья (LocalChannel/project_yaml/project_registry/
##           deploy_paths) — module-level
## @changes  2026-08-16 · DevPlan 176 A.2 — pre-deploy L1-гейт в deploy() (C1 root-эскалация):
##           verify_contracts l1_only на staging ДО копирования/orchestrator.deploy;
##           _PreDeployBlocked → [PRACTICES:BLOCK] в stderr + JSON FAILED + exit 1
## @changes  2026-08-24 · REF-0003 (DevPlan 11 W0) — unhealthy/timeout healthcheck → FAILED
##           (∉success): exit≠0, critical-notify на unhealthy-ветке, полная chain — только
##           на success; failure_notifier DI (additive, W-H)
## @changes  2026-08-24 · REF-0011 (DevPlan 11 В1) — flock per-project в начале run()
##           (reentrant; rollback/remove — та же точка лока через orchestrator_cli dispatch,
##           интеграционный шов описан в отчёте волны); FileLockError → JSON FAILED + rc 1;
##           EOFError в except-кортеже
## @changes  2026-08-24 · REF-0105 (DevPlan 11 В1) — payload-транзакция: backup_dir вне
##           target_dir, restore-from-backup при сбое replace (ДО rmtree), replace без
##           pre-remove, удаление stale canonical compose, prefix-sweep orphan tmpdir,
##           restore_payload_from_backup() — отдельный шов для REF-0004
## @changes  2026-08-25 · REF-0006 (DevPlan 11 В2) — код канала не меняется: staging-гейт
##           (176 A.2) остаётся первым рубежом; ВТОРОЙ рубеж l1_only теперь внутри
##           DeployOrchestrator.deploy (target_dir в момент compose — TOCTOU-закрытие);
##           deny-set расширен volumes/socket/host-modes на обоих рубежах
## @changes  2026-08-25 · REF-0015 (DevPlan 11 В2) — resource guards receive: stream-extract
##           с running uncompressed ceiling (200 MB) + entry-count cap (512) + statvfs guard
##           ДО extract; default compressed payload cap ↓ 64 MiB (AppConfig); нарушение
##           потолка/cap → ConfigValidationError (B4 канон) → JSON FAILED + exit 1
## @modulemap
##   ReceiveFlow.unpack [W:3] — statvfs guard → stream-extract tar.gz → staging (ceiling +
##                              entry-cap guards, filter="data")
##   ReceiveFlow.validate [W:3] — ai-platform.yaml parse + project name resolve/validate
##   ReceiveFlow.deploy [W:3] — pre-deploy L1 gate → payload-tx (backup/replace/stale-delete)
##                              → LocalChannel deploy → result
##   ReceiveFlow.run [W:4] — оркестрация unpack→validate→flock→gate→tx→deploy→chain→JSON→exit
##   restore_payload_from_backup [W:1] — восстановление payload из backup (шов REF-0004)
##   sweep_orphan_payload_tmpdirs [W:1] — prefix-sweep crashed-receive tmpdir (age > 1h)
## @usecases
##   - orchestrator_cli dispatch receive <project> <sha> (prod forced-command)
##   - DeployOrchestrator.receive() → ReceiveFlow().run()
# endregion MODULE_CONTRACT

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Protocol

# Контракт B4 (DevPlan 116 B4 T2): валидация payload → ConfigValidationError (не bare ValueError).
# 170 W10-B: импорты shared-листьев вынесены module-level (LocalChannel/project_yaml/project_registry/
# deploy_paths — чистые leaf; цикл receive_flow↔orchestrator держался ТОЛЬКО на DeployOrchestrator,
# который теперь инжектится конструктором (DI) — см. TRAP[DECISION] в __init__).
from core.internal.deploy.channels import DeliveryChannel, LocalChannel
from core.internal.deploy.hooks.post_deploy_chain import notify_deploy_failure

# 176 A.2 (C1): pre-deploy L1-гейт — переиспользует verify_contracts (тот же K3-канон,
# l1_only режим: ТОЛЬКО L1-статика, без docker-L2 латентности). НЕ дублирование гейта.
from core.internal.deploy.verify_contracts import SEVERITY_BLOCK, VerifyReport, verify_project_contracts
from core.internal.shared import project_yaml
from core.internal.shared.app_config import AppConfig
from core.internal.shared.compose_files import PROJECT_COMPOSE_FILENAMES
from core.internal.shared.deploy_paths import projects_base
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.file_lock import FileLock, FileLockError, platform_lock_path
from core.internal.shared.project_registry import validate_project_name
from core.internal.shared.timeouts import DEPLOY_TIMEOUT

logger = logging.getLogger(__name__)

# ── T9.9 (L-7, DevPlan 136 W9): лимит размера payload из stdin. Env-конфигурируемый,
# default 64 MiB (REF-0015: ↓ с 1 GiB — легитимные payload'ы канала receive — файлы
# конфигурации, KB-масштаб; 64 MiB = ×1000 запас). Потоковое чтение (chunked) — reject
# при превышении ДО распаковки.
# W4a (DevPlan 160 T4.1): import-time env-чтение убрано — ЧИСТЫЙ дефолт; env резолвится
# лениво (AppConfig.from_env) в ReceiveFlow.run()/конструкторе. SoT дефолта —
# app_config._DEFAULT_MAX_PAYLOAD_BYTES; константа здесь — документация fallback'а.
_DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024  # 64 MiB
_READ_CHUNK_BYTES = 1024 * 1024  # 1 MiB per chunk

# ── REF-0015 (DevPlan 11 В2): resource guards против tar-бомб и ENOSPC.
# Compressed-кап выше меряет только stdin; gzip даёт ~1000:1 на нулях → 1 MiB сжатых
# данных разворачивается в гигабайты. Три рубежа:
#   (1) running uncompressed ceiling 200 MB при stream-extract — сумма ОБЪЯВЛЕННЫХ
#       member.size проверяется по ходу распаковки ДО записи очередного члена
#       (tar-формат детерминирует объём записи из заголовка — учёт по заголовкам полон);
#   (2) entry-count cap 512 — тысячи пустых членов = inode/FD-бомба даже при малом
#       суммарном размере;
#   (3) statvfs guard перед extract — свободное место ≥ ceiling (extract жёстко ограничен
#       потолком, поэтому free < ceiling ⇒ потенциальный ENOSPC mid-extract на общей FS
#       с postgres WAL / docker layers).
_MAX_UNCOMPRESSED_CEILING_BYTES = 200 * 1024 * 1024  # 200 MB uncompressed ceiling
_MAX_TAR_ENTRIES = 512

# ── REF-0011: per-project flock периметр receive. Таймаут = полный деплой-бюджет:
# конкурентный receive ЖДЁТ окончания активного (CI concurrency-group сериализует пуш'ы,
# cancel-in-progress:false), а не падает; poll_interval 0.5s — минуты, не busy-loop.
_RECEIVE_LOCK_TIMEOUT = float(DEPLOY_TIMEOUT)
_RECEIVE_LOCK_POLL_INTERVAL = 0.5

# ── REF-0105: payload-транзакция. backup/staging tmpdir живут ВНЕ target_dir (system tmp)
# под каноническими префиксами; crashed receives оставляют orphan tmpdir, которые выметаются
# prefix-sweep'ом. Возрастной порог защищает tmpdir АКТИВНОГО параллельного receive (>1h —
# за пределами DEPLOY_TIMEOUT-бюджета любого легитимного прогона).
_ORPHAN_TMP_PREFIXES = ("payload-backup-", "payload-stage-")
_ORPHAN_MAX_AGE_S = 3600.0


# region PROTOCOLS_Orchestrator (DI, 170 W10-B)
class _DeployResultProtocol(Protocol):
    """Минимальный контракт OrchestratorDeployResult (W11): поля/методы, используемые
    ReceiveFlow.deploy/run. DeployOrchestrator.OrchestratorDeployResult структурно совместим
    (status: DeployStatus — Enum-подкласс str, не assignable к str статически → object)."""

    version: str

    def is_success(self) -> bool: ...

    def to_dict(self) -> dict[str, object]: ...


class _OrchestratorProtocol(Protocol):
    """Минимальный DI-контракт DeployOrchestrator для ReceiveFlow (W11): deploy +
    post-deploy цепочка. DeployOrchestrator структурно удовлетворяет протоколу —
    импорт не нужен (цикл receive_flow↔orchestrator разорван, 170 W10-B)."""

    def deploy(
        self,
        project_name: str,
        channel: DeliveryChannel,
        version: str = "",
        service: str = "",
        project_dir: str | None = None,
        metadata: dict[str, object] | None = None,
        dry_run: bool = False,
    ) -> _DeployResultProtocol: ...

    def _run_post_deploy_chain(
        self,
        project: str,
        version: str,
        status: str,
        project_dir: str | None = None,
        node_name: str = "",
        *,
        run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        platform_root_override: str | None = None,
        reconfig_fn: Callable[..., object] | None = None,
    ) -> None: ...


# endregion PROTOCOLS_Orchestrator


def _read_stdin_limited(
    max_payload_bytes: int | None = None,
    stream: BinaryIO | None = None,
) -> bytes | None:
    """Stream sys.stdin.buffer up to max_payload_bytes. Returns None if the limit is exceeded.

    ▶ ┌stdin┐ → ○ read chunk (1 MiB) → ◇ total > MAX? → ⎋ None (reject) → ⊕ accumulate → ⎋ bytes

    ## @purpose — T9.9: потоковое чтение без загрузки всего stdin в память заранее; лимит
    ##            применяется по ходу чтения (не после) — гигантский payload не читается целиком.
    ## @io — ⇥ max_payload_bytes: int | None (None = ленивый env-фолбэк),
    ##          stream: BinaryIO | None = None (DI, W-H DevPlan 163 — stdin-канал;
    ##              None = sys.stdin.buffer; тесты передают io.BytesIO вместо патча sys.stdin)
    ##          → ⎋ bytes | None (None = превышен лимит)
    ## @complexity O(N) где N = прочитанные байты (≤ MAX_PAYLOAD_BYTES + chunk)
    ## @invariants
    ##   - Читает chunk-ами, а не одним .read() — память ограничена chunk'ом на шаг
    ##   - total > max_payload_bytes → None (reject; вызывающий печатает JSON-ошибку, exit 1)
    ##   - Чистый EOF ДО лимита → объединённые байты
    ##   - DI: stream=None → sys.stdin.buffer (поведение без изменений)
    """
    src = sys.stdin.buffer if stream is None else stream
    limit = max_payload_bytes if max_payload_bytes is not None else AppConfig.from_env().max_payload_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = src.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            logger.error(
                "[IMP:10][ReceiveFlow][read] Payload exceeds MAX_PAYLOAD_BYTES=%d (got >%d bytes) — rejecting (T9.9)",
                limit,
                total,
            )
            return None
        chunks.append(chunk)
    return b"".join(chunks)


# region CLASS__PreDeployBlocked
class _PreDeployBlocked(Exception):
    """Pre-deploy L1 gate block (DevPlan 176 A.2, C1): L1 violation до orchestrator.deploy.

    ## @purpose — fail-fast сигнал: staging-композ не прошёл L1-контракты (privileged/cap_add/
    ##            devices/ports/secrets/...) → деплой блокируется ДО запуска контейнеров.
    ##            Несёт VerifyReport для [PRACTICES:BLOCK]-вывода + реальное имя проекта.
    ## @io — ⇥ report: VerifyReport (findings блока), project: str → ⎋ _PreDeployBlocked
    ## @complexity — O(1)
    ## @invariants
    ##   - report.has_blocking_violation() == True (иначе raise не должен происходить)
    ##   - project — реальное имя проекта (для аудита/JSON; staging-dir имя неинформативно)
    """

    def __init__(self, report: VerifyReport, project: str) -> None:
        super().__init__(f"pre-deploy L1 gate blocked project '{project}'")
        self.report = report
        self.project = project


# endregion CLASS__PreDeployBlocked


# region FUNC__default_pre_deploy_gate
## @purpose  Дефолтный pre-deploy L1-гейт (176 A.2): verify_contracts l1_only на каталоге
##           payload'а (staging) — ТОЛЬКО L1-статика compose (без docker-L2 латентности).
##           audit_project_name — реальное имя проекта (staging-dir имя неинформативно для
##           аудит-трейла блок-событий).
## @io       ⇥ project_dir: str (staging/target каталог), project: str | None (audit override)
##           → ⎋ VerifyReport
## @complexity O(S) где S = размер compose (чистая статика, 0 docker-subprocess)
def _default_pre_deploy_gate(project_dir: str, project: str | None = None) -> VerifyReport:
    """L1-only pre-deploy gate: verify_contracts l1_only (176 A.2, C1 root-эскалация)."""
    return verify_project_contracts(Path(project_dir), l1_only=True, audit_project_name=project)


# endregion FUNC__default_pre_deploy_gate


# region FUNC_restore_payload_from_backup
## @purpose  Восстановить ПРЕДЫДУЩИЙ payload из backup (REF-0105, transactional revert):
##           для каждого файла staging-набора — вернуть копию из backup, а если файла в
##           backup НЕТ (файл был новым в упавшей транзакции) — удалить его из target.
##           Отдельная callable-точка: шов для REF-0004 (rollback-контур вызывает restore
##           ПОСЛЕ успешного compose-rollback — порядок на стороне orchestrator).
## @io       ⇥ backup_dir: str | Path, target_dir: str | Path,
##              staged_names: Iterable[str] → ⎋ None (best-effort; сбои — IMP:10 WARN)
## @complexity O(F) where F = |staged_names|
## @invariants
##   - Никогда не raise (вызывается из except/rollback-путей — не маскирует исходную ошибку)
##   - Файл без backup-копии и отсутствующий в target — no-op (идемпотентно)
def restore_payload_from_backup(
    backup_dir: str | Path,
    target_dir: str | Path,
    staged_names: list[str] | tuple[str, ...],
) -> None:
    """Restore previous payload files from backup (or drop new files of a failed tx)."""
    restored = 0
    for name in staged_names:
        bsrc = Path(backup_dir) / name
        dest = Path(target_dir) / name
        try:
            if bsrc.is_file():
                shutil.copy2(str(bsrc), str(dest))
                restored += 1
            elif dest.exists():
                dest.unlink()
        except OSError as e:
            logger.error("[IMP:10][ReceiveFlow][restore] Cannot restore %s from %s: %s", dest, bsrc, e)
    logger.info(
        "[IMP:9][ReceiveFlow][restore] Payload restored from backup %s (%d/%d files)",
        backup_dir,
        restored,
        len(staged_names),
    )


# endregion FUNC_restore_payload_from_backup


# region FUNC_delete_stale_compose_names
## @purpose  Удалить канонические compose-имена (PROJECT_COMPOSE_FILENAMES), отсутствующие
##           в staging (REF-0105/DATA-703): переименованный compose.yaml переживал доставку,
##           ПОБЕЖДАЛ по резолюции COMPOSE_FILENAMES → нода тихо гоняла старый конфиг.
## @io       ⇥ target_dir: str | Path, staged_names: Iterable[str] → ⎋ int (удалено файлов)
## @complexity O(C) where C = |PROJECT_COMPOSE_FILENAMES|
## @invariants
##   - Только канонические PROJECT_COMPOSE_FILENAMES (не произвольные файлы payload'а)
##   - Best-effort: OSError удаления → IMP:7 WARN, деплой НЕ блокируется (R5-negative тест)
def _delete_stale_compose_names(target_dir: str | Path, staged_names: list[str] | tuple[str, ...]) -> int:
    """Remove canonical compose names absent from staging. Returns removed count."""
    removed = 0
    for fname in PROJECT_COMPOSE_FILENAMES:
        if fname in staged_names:
            continue
        stale_path = os.path.join(str(target_dir), fname)
        if not os.path.lexists(stale_path):
            continue
        try:
            os.remove(stale_path)
            removed += 1
            logger.info(
                "[IMP:9][ReceiveFlow][deploy] Removed stale compose %s (absent in staging)",
                stale_path,
            )
        except OSError as e:
            logger.warning(
                "[IMP:7][ReceiveFlow][deploy] Cannot remove stale %s (non-fatal): %s",
                stale_path,
                e,
            )
    return removed


# endregion FUNC_delete_stale_compose_names


# region FUNC_sweep_orphan_payload_tmpdirs
## @purpose  Prefix-sweep orphan tmpdir crashed receives (REF-0105): payload-backup-* /
##           payload-stage-* в system tmp старше _ORPHAN_MAX_AGE_S. Возрастной порог
##           защищает tmpdir АКТИВНОГО параллельного receive (крэш = никто не приберёт).
## @io       ⇥ now: float | None (DI для тестов; None = time.time()) → ⎋ int (удалено каталогов)
## @complexity O(D) where D = число payload-*-каталогов в tmp
## @invariants
##   - Только каталоги с каноническими префиксами (_ORPHAN_TMP_PREFIXES) в tempfile.gettempdir()
##   - Best-effort: OSError на stat/rmtree — skip (sweep не должен ломать receive)
def sweep_orphan_payload_tmpdirs(now: float | None = None) -> int:
    """Remove orphaned payload-tx tmpdirs older than the age threshold. Returns removed count."""
    tmp_root = Path(tempfile.gettempdir())
    cutoff = (now if now is not None else time.time()) - _ORPHAN_MAX_AGE_S
    removed = 0
    try:
        candidates = sorted(tmp_root.glob("payload-*"))
    except OSError:
        return 0
    for d in candidates:
        if not d.name.startswith(_ORPHAN_TMP_PREFIXES) or not d.is_dir():
            continue
        try:
            if d.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
        except OSError as e:
            logger.warning("[IMP:7][ReceiveFlow][sweep] Cannot sweep orphan %s: %s", d, e)
            continue
    if removed:
        logger.info("[IMP:8][ReceiveFlow][sweep] Swept %d orphan payload tmpdir(s) in %s", removed, tmp_root)
    return removed


# endregion FUNC_sweep_orphan_payload_tmpdirs


# region FUNC_commit_payload_tx
## @purpose  Commit-фаза payload-транзакции (REF-0105/T9.8): staging-copy → os.replace
##           БЕЗ pre-remove → удаление stale canonical compose. При ЛЮБОМ исключении до
##           commit-точки — restore_payload_from_backup из backup (вне target_dir).
## @io       ⇥ staging_files: list[Path], target_dir: str, backup_dir: str,
##              staged_names: list[str], project: str → ⎋ None ⚡ (re-raise исходное)
## @complexity O(F) where F = |staging_files|
## @invariants
##   - tx_committed=True строго ПОСЛЕ успешного replace+stale-sweep (точка commit)
##   - Исключение транзакции пробрасывается ДАЛЬШЕ после restore (не глотается)
def _commit_payload_tx(
    staging_files: list[Path],
    target_dir: str,
    backup_dir: str,
    staged_names: list[str],
    project: str,
) -> None:
    """Atomic payload transaction: copy→replace→stale-sweep with restore-on-crash."""
    tx_committed = False
    try:
        staging_copy = tempfile.mkdtemp(prefix="payload-stage-")
        try:
            for item in staging_files:
                shutil.copy2(str(item), os.path.join(staging_copy, item.name))
            for item in sorted(Path(staging_copy).iterdir()):
                if not item.is_file():
                    continue
                # 🧐 TRAP[DECISION] · 2026-08-24 · — · replace без pre-remove (REF-0105/FAIL-0704)
                # · Rejected: сохранить os.remove(dest) перед rename («root-owned стуб»)
                # · Reason: rename(2) требует прав записи только на КАТАЛОГ (ci-deploy-writable),
                # ·   не на сам файл — D11-комментарий подтверждает; pre-remove создавал
                # ·   ENOENT-окно для читателей (converge resolve/compose config) и терял файл
                # ·   при падении между remove и replace.
                # · Rev: если появится ФС без POSIX-rename поверх чужих файлов — вернуть guard.
                Path(str(item)).replace(os.path.join(target_dir, item.name))
            _delete_stale_compose_names(target_dir, staged_names)
        finally:
            shutil.rmtree(staging_copy, ignore_errors=True)
        tx_committed = True
    finally:
        if not tx_committed:
            logger.error(
                "[IMP:10][ReceiveFlow][tx] Payload tx FAILED mid-replace for %s — restoring from %s",
                project,
                backup_dir,
            )
            restore_payload_from_backup(backup_dir, target_dir, staged_names)


# endregion FUNC_commit_payload_tx


# region CLASS_ReceiveFlow
class ReceiveFlow:
    """VPS-side receive flow: unpack tar → validate payload → deploy via LocalChannel.

    ## @purpose — DevPlan 119 E2: extracted from DeployOrchestrator.receive() (CC=15 → ≤8/method).
    ##            Изолированные шаги (unpack/validate/pre-deploy-gate/deploy) с typed-контрактами.
    ##            DevPlan 176 A.2: pre-deploy L1-гейт (C1) перед orchestrator.deploy.
    ## @io — ⇥ projects_base: str | None (None = env-резолв в run(), канон projects_base()),
    ##             max_payload_bytes: int | None (None = env-резолв в run(), T9.9),
    ##             orchestrator_factory: Callable[..., Any] | None (DI, 170 W10-B — фабрика
    ##             DeployOrchestrator; None = fail-fast в _make_orchestrator: receive_flow
    ##             БОЛЬШЕ не импортирует DeployOrchestrator, фабрику инжектит
    ##             DeployOrchestrator.receive() — цикл receive_flow↔orchestrator разорван),
    ##             pre_deploy_gate: Callable[[str, str | None], VerifyReport] | None (DI, 176 A.2 —
    ##             pre-deploy L1-гейт; None = _default_pre_deploy_gate (verify_contracts l1_only,
    ##             ОБЯЗАТЕЛЕН — security-гейт C1, SKIP_PREFLIGHT=1 НЕ применим))
    ##             → ⎋ ReceiveFlow
    ## @complexity — O(N) where N = tar entries + deploy lifecycle
    ## @invariants
    ##   - DeployOrchestrator НЕ импортируется (ни module-level, ни lazy) — DI через конструктор
    ##   - run() возвращает int exit code {0,1} + печатает JSON в stdout (контракт диспетчера)
    ##   - Валидация fail-fast: каждый шаг печатает JSON-ошибку и возвращает 1
    ##   - projects_base резолвится в run() (env-цепочка PROJECTS_BASE → /opt/projects) —
    ##     receive() семантика (резолв на момент вызова, не импорта)
    ##   - max_payload_bytes резолвится в run() (AppConfig.from_env, лениво) — T9.9
    ##   - 176 A.2: pre-deploy L1-гейт исполняется ДО копирования в target_dir и ДО
    ##     orchestrator.deploy; violation → _PreDeployBlocked (контейнеры не запускаются)
    """

    # 🧐 TRAP[DECISION] · 2026-08-15 · — · receive_flow ↔ orchestrator цикл разорван DI (170 W10-B)
    # · Rejected: lazy-импорт DeployOrchestrator внутри deploy()/run() (держал цикл; import-linter
    # ·   видит function-level импорты — acyclic-internal-domains был RED без ignore-ребра)
    # · Reason: DeployOrchestrator.receive() инжектит фабрику (конструкторный DI), receive_flow
    # ·   остаётся leaf (0 рёбер → orchestrator). None → RuntimeError (fail-fast): единственный
    # ·   production-caller — receive(), который ВСЕГДА передаёт фабрику.
    # · Rev: если появится прямой caller ReceiveFlow().run() без фабрики — добавить дефолт.
    # 🧐 TRAP[DECISION] · 2026-08-16 · — · pre_deploy_gate DI (176 A.2): None → ДЕФОЛТНЫЙ L1-гейт
    # · Rejected: SKIP_PREFLIGHT=1-обход в receive-канале (паритет `make up` фасаду)
    # · Reason: SKIP_PREFLIGHT=1 — осознанный обход ДЛЯ ПЛАТФОРМЕННЫХ МОДУЛЕЙ (makefiles/modules.mk,
    # ·   175 W4.2), где compose контролирует сама платформа; receive принимает ПРОИЗВОЛЬНЫЙ
    # ·   compose проекта — обход гейта = та же C1 root-эскалация (владелец CI_DEPLOY_KEY
    # ·   шлёт privileged:true + /:/host). Security-гейт обязателен всегда; тесты инжектят
    # ·   fake-гейт/валидный staging — поведение по умолчанию неизменно.
    # · Rev: если появится легитимный канал приёма непроверенного compose — ввести явный
    # ·   allowlist-флаг, НЕ SKIP_PREFLIGHT.
    def __init__(
        self,
        projects_base: str | None = None,
        max_payload_bytes: int | None = None,
        *,
        orchestrator_factory: Callable[[str], _OrchestratorProtocol] | None = None,
        pre_deploy_gate: Callable[[str, str | None], VerifyReport] | None = None,
        failure_notifier: Callable[[str, str, str], None] | None = None,
    ):
        self.projects_base = projects_base
        self.max_payload_bytes = max_payload_bytes
        self.orchestrator_factory = orchestrator_factory
        self.pre_deploy_gate = pre_deploy_gate
        # REF-0003 (DevPlan 11 W0): critical-пуш на unhealthy/timeout-ветке; None → канонический
        # notify_deploy_failure (hooks/post_deploy_chain). DI — для тестов (W-H DevPlan 163).
        self.failure_notifier = failure_notifier

    # region FUNC__make_orchestrator
    ## @purpose  DI-фабрика оркестратора (170 W10-B): единственная точка создания
    ##           DeployOrchestrator-инстанса из конструкторного orchestrator_factory.
    ##           receive_flow НЕ импортирует DeployOrchestrator — цикл разорван.
    ## @io       ⇥ base: str (resolved projects_base) → ⎋ Any (оркестратор)
    ## @complexity O(1) — инъектированная фабрика
    ## @invariants
    ##   - orchestrator_factory=None → RuntimeError (fail-fast): единственный production-caller
    ##     (DeployOrchestrator.receive) ВСЕГДА инжектит фабрику; None = ошибка конфигурации
    def _make_orchestrator(self, base: str) -> _OrchestratorProtocol:
        """Create an orchestrator via the injected factory (DI, 170 W10-B)."""
        if self.orchestrator_factory is None:
            msg = (
                "No orchestrator_factory injected — ReceiveFlow requires DI (170 W10-B); "
                "DeployOrchestrator.receive() injects the factory"
            )
            raise ConfigValidationError(msg)
        return self.orchestrator_factory(base)

    # endregion FUNC__make_orchestrator

    # region FUNC_unpack
    ## @purpose  Stream-extract tar.gz payload (stdin bytes) into staging (filter="data")
    ##           с resource guards (REF-0015): statvfs headroom ДО старта, running
    ##           uncompressed ceiling + entry-count cap по ходу распаковки. Члены
    ##           обрабатываются последовательно — нарушение потолка прерывает extract
    ##           ДО записи очередного члена (гигантский член не попадает на диск).
    ## @io       ⇥ tar_bytes: bytes, staging: str,
    ##              max_uncompressed_bytes: int | None = None (DI; None = _MAX_UNCOMPRESSED_CEILING_BYTES),
    ##              max_entries: int | None = None (DI; None = _MAX_TAR_ENTRIES)
    ##           → ⎋ bool (True = extracted)
    ##           ⚡ ConfigValidationError — ceiling/entry-cap/statvfs нарушен (fail-fast,
    ##             B4 канон: валидация payload → ConfigValidationError)
    ## @complexity O(N) where N = tar entries до нарушения (запись ограничена потолком)
    ## @invariants
    ##   - mode="r:gz", filter="data" (tarfile API — path traversal protection)
    ##   - Пустой tar_bytes → False (fail-fast)
    ##   - Running ceiling по ОБЪЯВЛЕННЫМ member.size: объём записи члена детерминирован
    ##     заголовком tar → учёт по заголовкам полон; превышение → ConfigValidationError
    ##     до tar.extract() данного члена
    ##   - statvfs guard: f_bavail×f_frsize < ceiling → ConfigValidationError ДО открытия tar
    ##   - DI-параметры потолка/cap — для unit-тестов (0 monkeypatch констант)
    @staticmethod
    def unpack(
        tar_bytes: bytes,
        staging: str,
        *,
        max_uncompressed_bytes: int | None = None,
        max_entries: int | None = None,
    ) -> bool:
        """Extract tar.gz bytes into staging with tar-bomb guards. Returns True on success."""
        if not tar_bytes:
            logger.error("[IMP:10][ReceiveFlow][unpack] No data received on stdin")
            return False

        ceiling = max_uncompressed_bytes if max_uncompressed_bytes is not None else _MAX_UNCOMPRESSED_CEILING_BYTES
        entry_cap = max_entries if max_entries is not None else _MAX_TAR_ENTRIES

        # ── REF-0015: statvfs guard — свободное место обязано покрывать полный uncompressed
        # ceiling ЕЩЁ ДО открытия tar. Staging живёт в system tmp на одной FS с postgres WAL
        # и docker layers: ENOSPC mid-extract валит ноду целиком. Extract жёстко ограничен
        # потолком ниже ⇒ требование free >= ceiling гарантирует, что сам extract не может
        # исчерпать диск даже при полном развёртывании.
        usage = os.statvfs(staging)
        free_bytes = usage.f_bavail * usage.f_frsize
        if free_bytes < ceiling:
            logger.error(
                "[IMP:10][ReceiveFlow][unpack] Insufficient disk headroom for %s: free=%d required>=%d (REF-0015)",
                staging,
                free_bytes,
                ceiling,
            )
            msg = f"Insufficient disk space before extract: free={free_bytes} bytes, required>={ceiling}"
            raise ConfigValidationError(msg)

        buf = io.BytesIO(tar_bytes)
        total_declared = 0
        entries = 0
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            while True:
                member = tar.next()
                if member is None:
                    break
                entries += 1
                if entries > entry_cap:
                    logger.error(
                        "[IMP:10][ReceiveFlow][unpack] Tar entry-count cap exceeded: %d > %d — rejecting (REF-0015)",
                        entries,
                        entry_cap,
                    )
                    msg = f"Tar payload exceeds entry-count cap ({entry_cap} entries)"
                    raise ConfigValidationError(msg)
                total_declared += member.size
                if total_declared > ceiling:
                    logger.error(
                        "[IMP:10][ReceiveFlow][unpack] Uncompressed ceiling exceeded: >%d declared bytes "
                        "(cap=%d) — rejecting BEFORE write of member %r (REF-0015)",
                        total_declared,
                        ceiling,
                        member.name,
                    )
                    msg = f"Tar payload exceeds uncompressed ceiling ({ceiling} bytes)"
                    raise ConfigValidationError(msg)
                tar.extract(member, path=staging, filter="data")
        logger.info(
            "[IMP:8][ReceiveFlow][unpack] Payload extracted to %s (%d entries, %d declared bytes)",
            staging,
            entries,
            total_declared,
        )
        return True

    # endregion FUNC_unpack

    # region FUNC_validate
    ## @purpose  Parse ai-platform.yaml (shared reader B1), resolve + validate project name.
    ## @io       ⇥ staging: str, project_name: str | None → ⎋ tuple[str, str] (project, service)
    ##           ⚡ ConfigValidationError — ai-platform.yaml missing / name invalid / no name (fail-fast)
    ## @complexity O(1) — file read + shared parser + name validation
    ## @invariants
    ##   - ai-platform.yaml обязателен (отсутствие → ConfigValidationError)
    ##   - project_name из аргументов приоритетен; фолбэк на yaml `name` (локальные вызовы)
    ##   - validate_project_name (verb-reserve U-56) — невалидное имя → ConfigValidationError
    @staticmethod
    def validate(staging: str, project_name: str | None) -> tuple[str, str]:
        """Parse + validate payload. Returns (resolved_project, service)."""
        ai_yaml = Path(staging) / "ai-platform.yaml"
        if not ai_yaml.is_file():
            logger.error("[IMP:10][ReceiveFlow][validate] ai-platform.yaml not found in payload")
            msg = "ai-platform.yaml not found in payload"
            raise ConfigValidationError(msg)

        config = project_yaml.load_project_yaml(Path(staging))

        # D5: проект — из аргументов SSH-команды (приоритет), фолбэк на yaml `name` для
        # локальных/ручных вызовов. version — ТОЛЬКО из аргументов (sha-pinning).
        resolved_project = project_name or project_yaml.get_name(config)
        if not resolved_project:
            logger.error("[IMP:10][ReceiveFlow][validate] No project name in args or ai-platform.yaml")
            msg = "No project name in args or ai-platform.yaml"
            raise ConfigValidationError(msg)

        # U-56 verb-reserve + canonical name validation (проект «status» невалиден)
        if not validate_project_name(resolved_project):
            logger.error("[IMP:10][ReceiveFlow][validate] Invalid/reserved project name: %r", resolved_project)
            msg = f"Invalid or reserved project name: {resolved_project}"
            raise ConfigValidationError(msg)

        service = resolved_project  # D5: service = project_name (чтение service из yaml удалено, U-37)
        logger.info("[IMP:9][ReceiveFlow][validate] Validated project=%s service=%s", resolved_project, service)
        return resolved_project, service

    # endregion FUNC_validate

    # region FUNC_deploy
    ## @purpose  Copy payload files to project dir + execute full deploy pipeline via LocalChannel.
    ##           DevPlan 176 A.2 (C1): ПЕРВЫЙ шаг — pre-deploy L1-гейт на staging (ДО копирования
    ##           в target_dir и ДО orchestrator.deploy) — violation → _PreDeployBlocked
    ##           (контейнеры НЕ запускаются; единственная реальная root-эскалация закрыта).
    ## @io       ⇥ project: str, service: str, version: str, staging: str, target_dir: str,
    ##              base: str | None = None (projects_base для оркестратора; None → env-резолв)
    ##           ⎋ Any (OrchestratorDeployResult)
    ##           ⚡ _PreDeployBlocked — L1-нарушение в staging-compose (pre-up gate, 176 A.2)
    ## @complexity O(F) where F = payload files + deploy lifecycle
    ## @invariants
    ##   - pre-deploy L1-гейт: staging (payload-каталог) ДО os.makedirs/backup/copy — блок
    ##     НЕ мутирует target_dir (старые контейнеры продолжают работать со старым compose;
    ##     следующий легитимный receive перезапишет payload)
    ##   - LocalChannel (no-op transport — payload уже на месте, TRAP[DECISION] 2026-07-31)
    ##   - version (sha) прокидывается в deploy() → DeployHistory snapshot (sha-pinning)
    ##   - Оркестратор — через self._make_orchestrator (DI-фабрика из конструктора, 170 W10-B)
    def deploy(
        self,
        project: str,
        service: str,
        version: str,
        staging: str,
        target_dir: str,
        base: str | None = None,
    ) -> _DeployResultProtocol:
        """Copy payload + deploy via LocalChannel. Returns OrchestratorDeployResult.

        T9.8 (L-6): атомарная замена payload (staging-copy → per-file os.replace) + бэкап
        существующих payload-файлов (payload_backup_dir в metadata) — rollback восстанавливает
        их, а не только compose (см. DeployOrchestrator._rollback_deploy).

        REF-0105: payload-транзакция — backup_dir ВНЕ target_dir; исключение в фазе replace →
        restore_payload_from_backup ДО rmtree backup (half-applied payload невозможен);
        replace без pre-remove; канонические compose-имена вне staging удаляются.
        После входа в orchestrator.deploy restore не вызывается — порядок отката
        (compose-rollback → payload-restore) принадлежит контуру REF-0004.

        DI (W-H DevPlan 163 / 170 W10-B): оркестратор создаётся фабрикой из конструктора
        (orchestrator_factory); тесты инжектят субкласс-фабрику (0 патчей _deploy_compose/healthcheck).

        176 A.2 (C1): pre-deploy L1-гейт на staging ДО любых изменений target_dir — violation
        блокирует деплой ДО запуска контейнеров (единственная реальная root-эскалация:
        ci-deploy исполнял произвольный compose без L1-проверок).
        """
        # ── A.2 pre-deploy L1 gate (C1, DevPlan 176): ДО копирования/orchestrator.deploy ──
        # SKIP_PREFLIGHT=1 НЕ применим здесь (см. TRAP[DECISION] в __init__): receive принимает
        # ПРОИЗВОЛЬНЫЙ compose проекта — обход гейта = та же root-эскалация.
        gate = self.pre_deploy_gate if self.pre_deploy_gate is not None else _default_pre_deploy_gate
        gate_report = gate(staging, project)
        n_block = sum(1 for f in gate_report.findings if f.severity == SEVERITY_BLOCK)
        if gate_report.has_blocking_violation():
            logger.error(
                "[IMP:10][ReceiveFlow][pre-deploy] BLOCKED project=%s (%d L1 violations) — containers NOT started (C1)",
                project,
                n_block,
            )
            raise _PreDeployBlocked(report=gate_report, project=project)
        logger.info("[IMP:9][ReceiveFlow][pre-deploy] L1 gate PASS project=%s", project)

        os.makedirs(target_dir, exist_ok=True)
        staging_files = [p for p in Path(staging).iterdir() if p.is_file()]
        staged_names = [item.name for item in staging_files]

        # ── REF-0105: бэкап существующих payload-файлов ВНЕ target_dir (system tmp).
        # Раньше backup_dir создавался ВНУТРИ target_dir под target'ом и уничтожался
        # в finally даже при исключении ДО orchestrator-rollback — единственная
        # rollback-копия гибла ровно тогда, когда была нужна (см. TRAP[BUG] ниже).
        backup_dir = tempfile.mkdtemp(prefix="payload-backup-")
        for item in staging_files:
            dest = os.path.join(target_dir, item.name)
            if os.path.isfile(dest):
                try:
                    shutil.copy2(dest, os.path.join(backup_dir, item.name))
                except OSError as e:
                    logger.warning("[IMP:7][ReceiveFlow][deploy] Cannot backup existing %s (non-fatal): %s", dest, e)

        # ⚠️ TRAP[BUG] · 2026-08-24 · P1 · finally-rmtree уничтожал backup при любом сбое (REF-0105/DATA-101)
        # · Symptom: исключение между replace'ами (или в L1-гейте/копировании) выходило из
        # ·   deploy(), finally делал rmtree(backup_dir) → half-applied payload на ноде без
        # ·   средств отката до следующего receive; git↔node divergence молча.
        # · Root: rmtree стоял в общем finally без различения «tx упала» vs «tx прошла»;
        # ·   restore существовал только внутри orchestrator._rollback_deploy (недостижим
        # ·   при исключении ДО orchestrator.deploy).
        # · Fix: restore_payload_from_backup() в failure-ветке tx-фазы (ДО rmtree backup);
        # ·   после orchestrator.deploy restore НЕ вызывается — порядок
        # ·   «compose-rollback → payload-restore» принадлежит контуру REF-0004.
        # · Prevention: транзакционные мутации обязаны иметь явную commit-точку и
        # ·   restore-ветку до освобождения backup-ресурсов.
        try:
            _commit_payload_tx(staging_files, target_dir, backup_dir, staged_names, project)

            # Bootstrap-стуб (context_deployer φ8, GENERATED-STUB): os.replace (rename)
            # работает по правам ДИРЕКТОРИИ (ci-deploy-writable), root-owned файл заменяется
            # напрямую (D11) — отдельный remove больше не нужен (см. TRAP[DECISION] выше).

            # 🧐 TRAP[DECISION] · 2026-07-31 · HI · receive() local delivery channel
            # · Rejected: SCPChannel() with empty metadata (bug — deliver() always FAILED:
            #   "SCPChannel requires 'host' in payload.metadata"; the payload is already
            #   extracted to target_dir, so a transport hop is meaningless)
            # · Reason: LocalChannel is a no-op delivery preserving the full pipeline
            # · Rev: if receive() ever needs to ship payload to a THIRD host, switch channels.
            local_channel = LocalChannel()
            orchestrator = self._make_orchestrator(base or self.projects_base or "")
            result = orchestrator.deploy(
                project_name=project,
                channel=local_channel,
                version=version,
                service=service,
                project_dir=target_dir,
                # T9.8/REF-0105: бэкап предыдущих payload-файлов (вне target_dir) —
                # create_snapshot персистит его в snapshot; rollback восстанавливает
                # payload из снапшота (контур REF-0004), а не из этого tmpdir.
                metadata={"payload_backup_dir": backup_dir},
            )
            # D5: version (sha) попадает в OrchestratorDeployResult JSON
            result.version = version
            logger.info("[IMP:9][ReceiveFlow][deploy] Deploy result: %s", result.to_dict().get("status", ""))
            return result
        finally:
            shutil.rmtree(backup_dir, ignore_errors=True)

    # endregion FUNC_deploy

    # region FUNC_handle_deploy_outcome
    ## @purpose  Пост-деплой ветвление (D4/U-24 + REF-0003): success → полная post-deploy
    ##           chain; unhealthy/timeout (FAILED ∉ success) → critical-notify БЕЗ
    ##           catalog/reconfig/hooks поверх больного деплоя. Вынесен из run() (REF-0011:
    ##           flock-периметр увеличил CC run — ветвление результата живёт здесь).
    ## @io       ⇥ result: _DeployResultProtocol, project/version/base/target_dir
    ##           → ⎋ None (best-effort, сбой chain → WARN внутри chain)
    ## @complexity O(1) + стоимость chain/notify
    ## @invariants
    ##   - Полная chain — ТОЛЬКО на result.is_success(); иначе только critical-notify
    def _handle_deploy_outcome(
        self,
        result: _DeployResultProtocol,
        project: str,
        version: str,
        base: str,
        target_dir: str,
    ) -> None:
        """Run post-deploy chain on success / critical notify on unhealthy outcome."""
        result_status = str(result.to_dict().get("status", ""))
        healthcheck_status = str(result.to_dict().get("healthcheck_status", ""))
        if result.is_success():
            node_name = os.environ.get("NODE_NAME", os.environ.get("NODE", ""))
            chain_orch = self._make_orchestrator(base)
            # DI-хук пост-деплой цепочки: _run_post_deploy_chain объявлен в
            # _OrchestratorProtocol выше (170 W10-B); публичного алиаса нет
            # (wire-freeze P3.2 — имена orchestrator заморожены), тесты переопределяют
            # хук в субклассе фабрики. SLF001 — advisory-сигнал agent-check по контракту.
            run_post_deploy_chain = chain_orch._run_post_deploy_chain
            run_post_deploy_chain(project, version, result_status, target_dir, node_name)
        elif healthcheck_status and healthcheck_status != "healthy":
            logger.error(
                "[IMP:10][ReceiveFlow][outcome] Healthcheck FAILED for %s: status=%s — critical notify, rc!=0 (REF-0003)",
                project,
                healthcheck_status,
            )
            notifier = self.failure_notifier if self.failure_notifier is not None else notify_deploy_failure
            notifier(project, version, result_status or "FAILED")

    # endregion FUNC_handle_deploy_outcome

    # region FUNC_run
    ## @purpose  Оркестрация receive-флоу: unpack → validate → pre-deploy L1 gate → copy+deploy →
    ##           post-deploy chain → JSON stdout + exit code. Fail-fast на каждом шаге
    ##           (JSON-ошибка + exit 1).
    ## @io       ⇥ project_name: str | None, version: str, stream: BinaryIO | None = None
    ##              (DI, W-H DevPlan 163 — stdin-канал; None = sys.stdin.buffer) → ⎋ int (0/1)
    ## @complexity O(N + M) where N = tar entries, M = deploy lifecycle
    ## @invariants
    ##   - staging temp dir удаляется в finally (не мусорит)
    ##   - Post-deploy chain только при result.is_success() (best-effort);
    ##     unhealthy/timeout → FAILED (∉success, REF-0003) → critical-notify (failure_notifier
    ##     DI / notify_deploy_failure) БЕЗ catalog/reconfig/hooks + exit 1
    ##   - JSON OrchestratorDeployResult содержит version (AC2: project, version, sha, status)
    ##   - DI (170 W10-B): оркестратор (deploy + post-chain) — через конструкторную фабрику;
    ##     stream=None → sys.stdin.buffer (канонический канал)
    ##   - 176 A.2: _PreDeployBlocked (L1-гейт) → [PRACTICES:BLOCK]-отчёт в stderr + JSON FAILED
    ##     в stdout + exit 1 (контракт forced-command и deliver-JSON-парсинг сохранены)
    ##   - REF-0011: после validate run() держит per-project reentrant flock
    ##     (platform_lock_path) на ВЕСЬ периметр мутации target_dir (flock-before-copy:
    ##     два быстрых push не дают интерливинга os.replace = mixed payload); контеншн/
    ##     таймаут → JSON FAILED + exit 1; вложенные acquire (orchestrator.deploy →
    ##     history snapshot) реентрантны через общий holder-реестр file_lock
    def run(
        self,
        project_name: str | None = None,
        version: str = "latest",
        *,
        stream: BinaryIO | None = None,
    ) -> int:
        """Run the full receive flow. Returns exit code {0,1}."""
        logger.info("[IMP:9][ReceiveFlow][run] Receiving deploy payload via stdin (version=%s)", version)

        # Read tar from stdin — T9.9: потоковое чтение с лимитом (W4a: ленивый env-резолв
        # лимита через AppConfig; конструкторный параметр приоритетнее).
        max_bytes = (
            self.max_payload_bytes if self.max_payload_bytes is not None else AppConfig.from_env().max_payload_bytes
        )
        tar_bytes = _read_stdin_limited(max_bytes, stream=stream)
        if tar_bytes is None:
            print(
                json.dumps({
                    "status": "FAILED",
                    "error": f"Payload exceeds MAX_PAYLOAD_BYTES ({max_bytes} bytes) — rejected (T9.9)",
                })
            )
            return 1

        staging = tempfile.mkdtemp(prefix="deploy-receive-")
        # ruff: ignore[PLW0717] — нужно >5 свободных локальных переменных — извлечение неразумно
        try:
            if not self.unpack(tar_bytes, staging):
                print(json.dumps({"status": "FAILED", "error": "No data received on stdin"}))
                return 1

            try:
                resolved_project, service = self.validate(staging, project_name)
            except ConfigValidationError as e:
                print(json.dumps({"status": "FAILED", "error": str(e)}))
                return 1

            # B2: канонический projects_base из shared (literal удалён) — env-резолв на момент
            # вызова (receive() семантика: env PROJECTS_BASE приоритетнее дефолта).
            resolved_base = self.projects_base or str(projects_base())
            target_dir = os.path.join(resolved_base, resolved_project)

            # ── REF-0011: flock-before-copy — периметр лока покрывает ВСЕ мутации target_dir.
            # ⚠️ TRAP[BUG] · 2026-08-24 · P1 · payload копировался ДО взятия per-project flock
            # · Symptom: два быстрых push → интерливинг per-file os.replace в target_dir =
            # ·   mixed payload (compose от v2, .env.platform от v1) с ЗЕЛЁНЫМ CI обоих прогонов
            # ·   (DATA-302≡DATA-806); orchestrator.deploy внутри брал лок слишком поздно.
            # · Root: lock-периметр уже mutation-периметра — копирование шло вне T9.1-лока.
            # · Fix: reentrant flock сразу после resolve имени проекта (fail-closed file_lock,
            # ·   таймаут = DEPLOY_TIMEOUT — конкурентный receive ждёт очереди, не падает);
            # ·   rollback()/remove() берут тот же лок на входе dispatch (шов orchestrator_cli).
            # · Prevention: периметр блокировки обязан накрывать первый байт мутации ресурса.
            deploy_lock = FileLock(
                platform_lock_path(resolved_project),
                timeout=_RECEIVE_LOCK_TIMEOUT,
                poll_interval=_RECEIVE_LOCK_POLL_INTERVAL,
            )
            try:
                deploy_lock.acquire()
            except FileLockError as e:
                logger.error("[IMP:10][ReceiveFlow][run] Concurrent receive blocked for %s: %s", resolved_project, e)
                print(json.dumps({"status": "FAILED", "error": f"Concurrent deploy blocked: {e}"}))
                return 1
            try:
                result = self.deploy(
                    resolved_project,
                    service,
                    version,
                    staging,
                    target_dir,
                    base=resolved_base,
                )
                self._handle_deploy_outcome(result, resolved_project, version, resolved_base, target_dir)

                output = json.dumps(result.to_dict())
                print(output)
                return 0 if result.is_success() else 1
            finally:
                deploy_lock.release()

        except _PreDeployBlocked as exc:
            # A.2 (DevPlan 176, C1): L1-гейт заблокировал деплой ДО orchestrator.deploy.
            # Контракт forced-command сохранён: [PRACTICES:BLOCK]-отчёт → stderr (виден в
            # CI-логах ssh + deliver stderr), stdout — машинный JSON FAILED (deliver-парсинг
            # и CI exit-код не ломаются — риск §5 «A.2 ломает CI-канал»).
            logger.error(
                "[IMP:10][ReceiveFlow][run] pre-deploy L1 gate BLOCKED project=%s (%d violations)",
                exc.project,
                sum(1 for f in exc.report.findings if f.severity == SEVERITY_BLOCK),
            )
            print(exc.report.format_for_ssh(), file=sys.stderr)
            print(
                json.dumps({
                    "status": "FAILED",
                    "project": exc.project,
                    "error": (
                        f"[PRACTICES:BLOCK] L1 pre-deploy gate blocked {exc.project} — "
                        "containers NOT started (C1); см. stderr-отчёт"
                    ),
                })
            )
            return 1
        except (tarfile.TarError, OSError, EOFError, FileLockError, ConfigValidationError) as e:
            # EOFError (REF-0105/FAIL-0711): обрыв stdin/JSON-канала — тот же JSON-контракт
            # FAILED, а не traceback диспетчеру forced-command.
            # FileLockError (REF-0011): контеншн на вложенных локах (history snapshot и т.п.)
            # — JSON FAILED + rc 1 вместо traceback в forced-command канал.
            # ConfigValidationError (REF-0015): resource guards unpack (uncompressed ceiling /
            # entry-count cap / statvfs headroom) — валидация payload (B4 канон) →
            # тот же JSON FAILED + rc 1 (traceback диспетчеру forced-command недопустим).
            logger.error("[IMP:10][ReceiveFlow][run] Error: %s", e)
            print(json.dumps({"status": "FAILED", "error": str(e)}))
            return 1
        finally:
            if os.path.isdir(staging):
                shutil.rmtree(staging, ignore_errors=True)
            # REF-0105: prefix-sweep orphan tmpdir от crashed receives (best-effort; возрастной
            # порог защищает tmpdir активного параллельного receive).
            try:
                sweep_orphan_payload_tmpdirs()
            except OSError as e:  # защитная ветка — sweep никогда не ломает receive
                logger.warning("[IMP:7][ReceiveFlow][run] Orphan tmpdir sweep skipped: %s", e)

    # endregion FUNC_run


# endregion CLASS_ReceiveFlow
