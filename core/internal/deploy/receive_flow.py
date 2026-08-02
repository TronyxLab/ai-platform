#!/usr/bin/env python3
# GREP_SUMMARY: receive-flow, receive, tar, stdin, unpack, validate, deploy, forced-command, sha-pinning, JSON, E2, orchestrator-decomposition
# STRUCTURE: ▶ ReceiveFlow.run ┌stdin tar + project_name + version┐ → unpack (tar → staging) → validate (ai-platform.yaml + name) → copy → deploy (LocalChannel) → post-deploy chain → ⎋ JSON + exit code
# region MODULE_CONTRACT
## @purpose  VPS-side forced-command receive flow (DevPlan 119 E2) — экстракция receive() из
##           deploy/orchestrator.py (127 LOC, CC=15). Класс ReceiveFlow: unpack → validate →
##           deploy — изолированные методы с typed-контрактами. Сохраняет поведение receive():
##           JSON OrchestratorDeployResult в stdout + exit code {0,1}.
## @scope    Consumed by DeployOrchestrator.receive() (тонкий фасад-делегат). Вызывается из
##           orchestrator_cli dispatch receive (SSH forced-command). Ленивые импорты
##           DeployOrchestrator/LocalChannel — избегают circular import (orchestrator → receive_flow).
## @invariants
##   - Пустой stdin → JSON-ошибка + exit 1 (fail-fast, БЕЗ || true-масок)
##   - ai-platform.yaml отсутствует → JSON-ошибка + exit 1 (fail-fast)
##   - project_name из аргументов (валидируется validate_project_name + verb-reserve U-56);
##     фолбэк на ai-platform.yaml `name` — ТОЛЬКО для локальных/ручных вызовов без аргументов
##   - version ТОЛЬКО из аргументов (D5 sha-pinning); service = project_name
##   - Деплой через LocalChannel (payload уже извлечён — TRAP[DECISION] 2026-07-31)
##   - Пост-деплой цепочка best-effort (сбой → WARN, деплой НЕ фейлится)
## @rationale DevPlan 119 E2 (AUDIT-2 M2): receive() CC=15 в монолите orchestrator.py (1157 LOC).
##           Вынос в ReceiveFlow (unpack/validate/deploy) снижает CC до ≤8 на метод и даёт
##           изолированное тестирование (R5: test_orchestrator_receive_flow_parity).
## @changes  2026-08-02 · DevPlan 119 E2 — экстракция из DeployOrchestrator.receive()
## @modulemap
##   ReceiveFlow.unpack [W:2] — tar.gz → staging (filter="data", tarfile)
##   ReceiveFlow.validate [W:3] — ai-platform.yaml parse + project name resolve/validate
##   ReceiveFlow.deploy [W:2] — copy payload → LocalChannel deploy → result
##   ReceiveFlow.run [W:4] — оркестрация unpack→validate→deploy→chain→JSON→exit
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
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

# Контракт B4 (DevPlan 116 B4 T2): валидация payload → ConfigValidationError (не bare ValueError).
from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)


# region CLASS_ReceiveFlow
class ReceiveFlow:
    """VPS-side receive flow: unpack tar → validate payload → deploy via LocalChannel.

    ## @purpose — DevPlan 119 E2: extracted from DeployOrchestrator.receive() (CC=15 → ≤8/method).
    ##            Изолированные шаги (unpack/validate/deploy) с typed-контрактами.
    ## @io — ⇥ projects_base: str | None (None = env-резолв в run(), канон projects_base()) → ⎋ ReceiveFlow
    ## @complexity — O(N) where N = tar entries + deploy lifecycle
    ## @invariants
    ##   - DeployOrchestrator/LocalChannel импортируются лениво (circular-import guard)
    ##   - run() возвращает int exit code {0,1} + печатает JSON в stdout (контракт диспетчера)
    ##   - Валидация fail-fast: каждый шаг печатает JSON-ошибку и возвращает 1
    ##   - projects_base резолвится в run() (env-цепочка PROJECTS_BASE → /opt/projects) —
    ##     legacy receive() семантика (резолв на момент вызова, не импорта)
    """

    def __init__(self, projects_base: str | None = None):
        self.projects_base = projects_base

    # region FUNC_unpack
    ## @purpose  Extract tar.gz payload (from stdin bytes) into staging dir (filter="data").
    ## @io       ⇥ tar_bytes: bytes, staging: str → ⎋ bool (True = extracted)
    ## @complexity O(N) where N = tar entries
    ## @invariants
    ##   - mode="r:gz", filter="data" (tarfile 3.14 API — path traversal protection)
    ##   - Пустой tar_bytes → False (fail-fast)
    def unpack(self, tar_bytes: bytes, staging: str) -> bool:
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
    def validate(self, staging: str, project_name: str | None) -> tuple[str, str]:
        """Parse + validate payload. Returns (resolved_project, service)."""
        from core.internal.shared import project_yaml as shared_project_yaml
        from core.internal.shared.exceptions import ConfigValidationError
        from core.internal.shared.project_registry import validate_project_name

        ai_yaml = Path(staging) / "ai-platform.yaml"
        if not ai_yaml.is_file():
            logger.error("[IMP:10][ReceiveFlow][validate] ai-platform.yaml not found in payload")
            raise ConfigValidationError("ai-platform.yaml not found in payload")

        config = shared_project_yaml.load_project_yaml(Path(staging))

        # D5: проект — из аргументов SSH-команды (приоритет), фолбэк на yaml `name` для
        # локальных/ручных вызовов. version — ТОЛЬКО из аргументов (sha-pinning).
        resolved_project = project_name or shared_project_yaml.get_name(config)
        if not resolved_project:
            logger.error("[IMP:10][ReceiveFlow][validate] No project name in args or ai-platform.yaml")
            raise ConfigValidationError("No project name in args or ai-platform.yaml")

        # U-56 verb-reserve + canonical name validation (проект «status» невалиден)
        if not validate_project_name(resolved_project):
            logger.error("[IMP:10][ReceiveFlow][validate] Invalid/reserved project name: %r", resolved_project)
            raise ConfigValidationError(f"Invalid or reserved project name: {resolved_project}")

        service = resolved_project  # D5: service = project_name (чтение service из yaml удалено, U-37)
        logger.info("[IMP:9][ReceiveFlow][validate] Validated project=%s service=%s", resolved_project, service)
        return resolved_project, service

    # endregion FUNC_validate

    # region FUNC_deploy
    ## @purpose  Copy payload files to project dir + execute full deploy pipeline via LocalChannel.
    ## @io       ⇥ project: str, service: str, version: str, staging: str, target_dir: str,
    ##              base: str | None = None (projects_base для DeployOrchestrator; None → env-резолв)
    ##           ⎋ Any (OrchestratorDeployResult)
    ## @complexity O(F) where F = payload files + deploy lifecycle
    ## @invariants
    ##   - LocalChannel (no-op transport — payload уже на месте, TRAP[DECISION] 2026-07-31)
    ##   - version (sha) прокидывается в deploy() → DeployHistory snapshot (sha-pinning)
    def deploy(
        self, project: str, service: str, version: str, staging: str, target_dir: str, base: str | None = None
    ) -> Any:
        """Copy payload + deploy via LocalChannel. Returns OrchestratorDeployResult."""
        from core.internal.deploy.channels import LocalChannel
        from core.internal.deploy.orchestrator import DeployOrchestrator

        os.makedirs(target_dir, exist_ok=True)
        for item in Path(staging).iterdir():
            if item.is_file():
                shutil.copy2(str(item), os.path.join(target_dir, item.name))

        # 🧐 TRAP[DECISION] · 2026-07-31 · HI · receive() local delivery channel
        # · Rejected: SCPChannel() with empty metadata (bug — deliver() always FAILED:
        #   "SCPChannel requires 'host' in payload.metadata"; the payload is already
        #   extracted to target_dir, so a transport hop is meaningless)
        # · Reason: LocalChannel is a no-op delivery preserving the full pipeline
        # · Rev: if receive() ever needs to ship payload to a THIRD host, switch channels.
        local_channel = LocalChannel()
        orchestrator = DeployOrchestrator(projects_base=base or self.projects_base or "")
        result = orchestrator.deploy(
            project_name=project,
            channel=local_channel,
            version=version,
            service=service,
            project_dir=target_dir,
        )
        # D5: version (sha) попадает в OrchestratorDeployResult JSON
        result.version = version
        logger.info("[IMP:9][ReceiveFlow][deploy] Deploy result: %s", result.status.value)
        return result

    # endregion FUNC_deploy

    # region FUNC_run
    ## @purpose  Оркестрация receive-флоу: unpack → validate → copy+deploy → post-deploy chain →
    ##           JSON stdout + exit code. Fail-fast на каждом шаге (JSON-ошибка + exit 1).
    ## @io       ⇥ project_name: str | None, version: str → ⎋ int (0 = success, 1 = failure)
    ## @complexity O(N + M) where N = tar entries, M = deploy lifecycle
    ## @invariants
    ##   - staging temp dir удаляется в finally (не мусорит)
    ##   - Post-deploy chain только при result.is_success() (best-effort)
    ##   - JSON OrchestratorDeployResult содержит version (AC2: project, version, sha, status)
    def run(self, project_name: str | None = None, version: str = "latest") -> int:
        """Run the full receive flow. Returns exit code {0,1}."""
        logger.info("[IMP:9][ReceiveFlow][run] Receiving deploy payload via stdin (version=%s)", version)

        # Read tar from stdin — пустой stdin → fail-fast (БЕЗ || true-масок)
        tar_bytes = sys.stdin.buffer.read()

        staging = tempfile.mkdtemp(prefix="deploy-receive-")
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
            # вызова (legacy receive() семантика: env PROJECTS_BASE приоритетнее дефолта).
            from core.internal.shared.deploy_paths import projects_base

            resolved_base = self.projects_base or str(projects_base())
            target_dir = os.path.join(resolved_base, resolved_project)
            result = self.deploy(resolved_project, service, version, staging, target_dir, base=resolved_base)

            # ── Пост-деплой цепочка (D4, U-24): best-effort, сбой → WARN, НЕ фейлит деплой ──
            if result.is_success():
                node_name = os.environ.get("NODE_NAME", os.environ.get("NODE", ""))
                from core.internal.deploy.orchestrator import DeployOrchestrator

                DeployOrchestrator(projects_base=self.projects_base)._run_post_deploy_chain(
                    resolved_project, version, result.status.value, target_dir, node_name
                )

            output = json.dumps(result.to_dict())
            print(output)
            return 0 if result.is_success() else 1

        except (tarfile.TarError, OSError) as e:
            logger.error("[IMP:10][ReceiveFlow][run] Error: %s", e)
            print(json.dumps({"status": "FAILED", "error": str(e)}))
            return 1
        finally:
            if os.path.isdir(staging):
                shutil.rmtree(staging, ignore_errors=True)

    # endregion FUNC_run


# endregion CLASS_ReceiveFlow
