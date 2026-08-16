#!/usr/bin/env python3
# GREP_SUMMARY: receive-flow, receive, tar, stdin, unpack, validate, deploy, pre-deploy-gate, L1, PRACTICES-BLOCK, forced-command, sha-pinning, JSON, E2, orchestrator-decomposition
# STRUCTURE: ▶ ReceiveFlow.run ┌stdin tar + project_name + version┐ → unpack (tar → staging) → validate (ai-platform.yaml + name) → pre-deploy L1 gate (verify_contracts l1_only, 176 A.2) → copy → deploy (LocalChannel) → post-deploy chain → ⎋ JSON + exit code
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
##   - Payload > лимита (env PLATFORM_MAX_PAYLOAD_BYTES, default 1GiB; W4a: ленивый резолв
##     через AppConfig в run()/конструкторе) → reject
##     ДО распаковки (T9.9: потоковое чтение, лимит по ходу, не после)
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
## @modulemap
##   ReceiveFlow.unpack [W:2] — tar.gz → staging (filter="data", tarfile)
##   ReceiveFlow.validate [W:3] — ai-platform.yaml parse + project name resolve/validate
##   ReceiveFlow.deploy [W:2] — pre-deploy L1 gate → copy payload → LocalChannel deploy → result
##   ReceiveFlow.run [W:4] — оркестрация unpack→validate→gate→deploy→chain→JSON→exit
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
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Protocol

# Контракт B4 (DevPlan 116 B4 T2): валидация payload → ConfigValidationError (не bare ValueError).
# 170 W10-B: импорты shared-листьев вынесены module-level (LocalChannel/project_yaml/project_registry/
# deploy_paths — чистые leaf; цикл receive_flow↔orchestrator держался ТОЛЬКО на DeployOrchestrator,
# который теперь инжектится конструктором (DI) — см. TRAP[DECISION] в __init__).
from core.internal.deploy.channels import DeliveryChannel, LocalChannel

# 176 A.2 (C1): pre-deploy L1-гейт — переиспользует verify_contracts (тот же K3-канон,
# l1_only режим: ТОЛЬКО L1-статика, без docker-L2 латентности). НЕ дублирование гейта.
from core.internal.deploy.verify_contracts import SEVERITY_BLOCK, VerifyReport, verify_project_contracts
from core.internal.shared import project_yaml
from core.internal.shared.app_config import AppConfig
from core.internal.shared.deploy_paths import projects_base
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.project_registry import validate_project_name

logger = logging.getLogger(__name__)

# ── T9.9 (L-7, DevPlan 136 W9): лимит размера payload из stdin. Env-конфигурируемый,
# default 1 GiB. Потоковое чтение (chunked) — reject при превышении ДО распаковки.
# W4a (DevPlan 160 T4.1): import-time env-чтение убрано — ЧИСТЫЙ дефолт; env резолвится
# лениво (AppConfig.from_env) в ReceiveFlow.run()/конструкторе.
_DEFAULT_MAX_PAYLOAD_BYTES = 1024**3  # 1 GiB
_READ_CHUNK_BYTES = 1024 * 1024  # 1 MiB per chunk


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
    ):
        self.projects_base = projects_base
        self.max_payload_bytes = max_payload_bytes
        self.orchestrator_factory = orchestrator_factory
        self.pre_deploy_gate = pre_deploy_gate

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
    ## @purpose  Extract tar.gz payload (from stdin bytes) into staging dir (filter="data").
    ## @io       ⇥ tar_bytes: bytes, staging: str → ⎋ bool (True = extracted)
    ## @complexity O(N) where N = tar entries
    ## @invariants
    ##   - mode="r:gz", filter="data" (tarfile 3.14 API — path traversal protection)
    ##   - Пустой tar_bytes → False (fail-fast)
    @staticmethod
    def unpack(tar_bytes: bytes, staging: str) -> bool:
        """Extract tar.gz bytes into staging. Returns True on success."""
        if not tar_bytes:
            logger.error("[IMP:10][ReceiveFlow][unpack] No data received on stdin")
            return False
        buf = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            tar.extractall(path=staging, filter="data")
        logger.info("[IMP:8][ReceiveFlow][unpack] Payload extracted to %s", staging)
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

        # ── T9.8: бэкап существующих payload-файлов ДО overwrite (для rollback) ──
        backup_dir = tempfile.mkdtemp(prefix="payload-backup-", dir=target_dir)
        for item in staging_files:
            dest = os.path.join(target_dir, item.name)
            if os.path.isfile(dest):
                try:
                    shutil.copy2(dest, os.path.join(backup_dir, item.name))
                except OSError as e:
                    logger.warning("[IMP:7][ReceiveFlow][deploy] Cannot backup existing %s (non-fatal): %s", dest, e)

        try:
            # ── T9.8: атомарная замена — staging-copy → per-file os.replace (rename) ──
            # Раньше файлы копировались из staging напрямую в target: сбой на середине
            # оставлял частично перезаписанные файлы. os.replace атомарен на POSIX
            # (читатель видит старый ИЛИ новый файл, не обрезанный).
            staging_copy = tempfile.mkdtemp(prefix="payload-stage-", dir=target_dir)
            try:
                for item in staging_files:
                    shutil.copy2(str(item), os.path.join(staging_copy, item.name))
                for item in Path(staging_copy).iterdir():
                    if not item.is_file():
                        continue
                    dest = os.path.join(target_dir, item.name)
                    # Bootstrap-стуб (context_deployer φ8, GENERATED-STUB) может быть root-owned —
                    # os.replace (rename) работает по правам ДИРЕКТОРИИ (ci-deploy-writable):
                    # удаляем существующий файл как старый путь (D11) + WARN при неудаче.
                    if os.path.lexists(dest):
                        try:
                            os.remove(dest)
                        except OSError:
                            logger.warning(
                                "[IMP:7][ReceiveFlow][deploy] Cannot remove existing %s — os.replace will surface the error",
                                dest,
                            )
                    Path(str(item)).replace(dest)
            finally:
                shutil.rmtree(staging_copy, ignore_errors=True)

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
                # T9.8: бэкап предыдущих payload-файлов — rollback восстановит их из snapshot
                metadata={"payload_backup_dir": backup_dir},
            )
            # D5: version (sha) попадает в OrchestratorDeployResult JSON
            result.version = version
            logger.info("[IMP:9][ReceiveFlow][deploy] Deploy result: %s", result.to_dict().get("status", ""))
            return result
        finally:
            shutil.rmtree(backup_dir, ignore_errors=True)

    # endregion FUNC_deploy

    # region FUNC_run
    ## @purpose  Оркестрация receive-флоу: unpack → validate → pre-deploy L1 gate → copy+deploy →
    ##           post-deploy chain → JSON stdout + exit code. Fail-fast на каждом шаге
    ##           (JSON-ошибка + exit 1).
    ## @io       ⇥ project_name: str | None, version: str, stream: BinaryIO | None = None
    ##              (DI, W-H DevPlan 163 — stdin-канал; None = sys.stdin.buffer) → ⎋ int (0/1)
    ## @complexity O(N + M) where N = tar entries, M = deploy lifecycle
    ## @invariants
    ##   - staging temp dir удаляется в finally (не мусорит)
    ##   - Post-deploy chain только при result.is_success() (best-effort)
    ##   - JSON OrchestratorDeployResult содержит version (AC2: project, version, sha, status)
    ##   - DI (170 W10-B): оркестратор (deploy + post-chain) — через конструкторную фабрику;
    ##     stream=None → sys.stdin.buffer (канонический канал)
    ##   - 176 A.2: _PreDeployBlocked (L1-гейт) → [PRACTICES:BLOCK]-отчёт в stderr + JSON FAILED
    ##     в stdout + exit 1 (контракт forced-command и deliver-JSON-парсинг сохранены)
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
            result = self.deploy(
                resolved_project,
                service,
                version,
                staging,
                target_dir,
                base=resolved_base,
            )

            # ── Пост-деплой цепочка (D4, U-24): best-effort, сбой → WARN, НЕ фейлит деплой ──
            # 170 W10-B: цепочка исполняется оркестратором из конструкторной фабрики (та же,
            # что и deploy) — тесты переопределяют _run_post_deploy_chain в субклассе.
            if result.is_success():
                node_name = os.environ.get("NODE_NAME", os.environ.get("NODE", ""))
                chain_orch = self._make_orchestrator(resolved_base)
                chain_orch._run_post_deploy_chain(
                    resolved_project, version, str(result.to_dict().get("status", "")), target_dir, node_name
                )

            output = json.dumps(result.to_dict())
            print(output)
            return 0 if result.is_success() else 1

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
        except (tarfile.TarError, OSError) as e:
            logger.error("[IMP:10][ReceiveFlow][run] Error: %s", e)
            print(json.dumps({"status": "FAILED", "error": str(e)}))
            return 1
        finally:
            if os.path.isdir(staging):
                shutil.rmtree(staging, ignore_errors=True)

    # endregion FUNC_run


# endregion CLASS_ReceiveFlow
