#!/usr/bin/env python3
# GREP_SUMMARY: deploy-engine-results, dataclass, ServiceDeployResult, RemoveResult, StatusResult, ImageInfo, status-contract, 170-W4-B2
# STRUCTURE: ▶ DataClasses → ServiceDeployResult(deploy) | RemoveResult(remove) | StatusResult(status, to_dict) | ImageInfo(saved image) → ⎋ контракты операций
# region MODULE_CONTRACT
## @purpose  Result-dataclass'ы операций DeployEngine (170 W4-B2): ServiceDeployResult, RemoveResult,
##           StatusResult, ImageInfo. Перенесены из монолита deploy_engine.py БЕЗ изменения тел.
## @scope    core/internal/deploy/engine/results.py — импортируются engine.py, lifecycle.py и наружу
##           (фасад deploy_engine.py, тесты test_deploy_engine/test_project_status_contract).
## @invariants
##   1. StatusResult — ТОТ ЖЕ канон, что ProjectStatus (orchestrator.py): поля {project, status, containers,
##      last_deploy}; `node` — расширение on-node статусов; JSON-канон диспетчера — ProjectStatus.to_dict()
##   2. Поля StatusResult НЕ расходятся с ProjectStatus (тест set-сравнения ключей, T3 п.4)
## @changes 170 W4-B2 — extracted from deploy_engine.py
# endregion MODULE_CONTRACT

from __future__ import annotations

from dataclasses import dataclass, field


# region CLASS_ServiceDeployResult
@dataclass
class ServiceDeployResult:
    """Result of a deploy operation."""

    success: bool
    project: str
    ref: str
    service: str
    previous_image: str | None = None
    rollback_performed: bool = False
    first_deploy_failed: bool = False
    error_message: str | None = None


# endregion CLASS_ServiceDeployResult


# region CLASS_RemoveResult
@dataclass
class RemoveResult:
    """Result of a remove operation."""

    success: bool
    project: str
    already_removed: bool = False
    error_message: str | None = None


# endregion CLASS_RemoveResult


# region CLASS_StatusResult
@dataclass
class StatusResult:
    """Result of a status operation.

        ## @purpose — Status-контракт (DevPlan 116 B1 T3, U-36): StatusResult = ТОТ ЖЕ канон, что
        ##            ProjectStatus (orchestrator.py) — поля {project, status, containers, last_deploy}.
        ##            Поле `node` — расширение (заполняется на on-node статусах); в JSON-каноне
        ##            диспетчера (orchestrator_cli dispatch status) используется ProjectStatus.to_dict().
        ## @invariants
        ##   - status ∈ {"found", "not_found", "stub"} — тот же словарь, что у ProjectStatus
        ##   - containers: list[dict] — docker compose ps JSON-строки (та же структура)
    ##   - last_deploy: dict | None — последний DeployHistory snapshot (единый
    ##     механизм с DeployOrchestrator.status())
    ##   - Поля НЕ расходятся с ProjectStatus: тест set-сравнения ключей (T3 п.4)
    """

    project: str
    node: str
    status: str  # "found" | "not_found" | "stub"
    containers: list[dict[str, object]] = field(default_factory=list)
    last_deploy: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize to JSON — канон ProjectStatus {project, status, containers, last_deploy} + node."""
        return {
            "project": self.project,
            "node": self.node,
            "status": self.status,
            "containers": self.containers,
            "last_deploy": self.last_deploy,
        }


# endregion CLASS_StatusResult


# region CLASS_ImageInfo
@dataclass
class ImageInfo:
    """Info about a saved previous image."""

    id: str
    tag: str | None = None


# endregion CLASS_ImageInfo
