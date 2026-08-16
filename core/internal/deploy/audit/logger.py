# GREP_SUMMARY: deploy-audit, audit-logger, DeployAuditLogger, write-audit-entry, deploy-audit-trail, adapter, 170-W4-B3
# STRUCTURE: ▶ DeployAuditLogger.log/log_many → ⊕ маппинг deploy-полей (operation/project/channel/result/duration_s/snapshot_id) → shared write_audit_entry(tag="deploy:<op>") → ⎋ audit.jsonl (единый файл)
# region MODULE_CONTRACT
## @purpose  DeployAuditLogger — тонкий адаптер DeployOrchestrator → единый writer
##           shared/audit_logger (D1, DevPlan 116 B11 T2). Маппит deploy-поля
##           (operation/project/channel/result/duration_s/snapshot_id) в расширенную
##           схему write_audit_entry(tag="deploy:<operation>", **extra).
## @scope    Пакет deploy/audit/ (170 W4-B3) — вынесен из монолита deploy/orchestrator.py
##           (класс DeployAuditLogger, ~80-149). Все deploy-записи идут в ЕДИНЫЙ файл
##           audit.jsonl (DEFAULT_LOG_FILE shared/audit_logger).
## @invariants
##   1. .log()/.log_many() интерфейс сохраняется (тесты/вызовы не ломаются)
##   2. НИКАКОГО прямого f.write — все записи через shared write_audit_entry
##   3. tag = "deploy:<operation>", status = result (или "UNKNOWN")
##   4. Расширенная схема: extra-поля (operation, project, channel, result, duration_s,
##      snapshot_id, projects, per_project_results) — в ту же JSON-строку
## @rationale DevPlan 089 T6 + 116 B11 T2: единый audit-writer для всех deploy-операций
##            (audit.jsonl — единый формат, shared/audit_logger). Вынос в пакет audit/
##            декомпозирует монолит orchestrator.py (1388 LOC → фасад) без изменения API.
## @changes 2026-08-15 | 170 W4-B3 — extracted from deploy/orchestrator.py (1:1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

# Единый audit-writer — shared/audit_logger. DeployAuditLogger пишет через
# write_audit_entry (tag="deploy:<operation>") — единственный канал записей.
from core.internal.shared.audit_logger import DEFAULT_LOG_FILE as _SHARED_AUDIT_LOG_FILE
from core.internal.shared.audit_logger import write_audit_entry as _shared_write_audit_entry

logger = logging.getLogger(__name__)


# region CLASS_DeployAuditLogger
class DeployAuditLogger:
    """Thin adapter: DeployOrchestrator .log()/.log_many() interface → shared write_audit_entry.

    ## @purpose — Единственный мост между DeployOrchestrator'ом и единым writer'ом
    ##            shared/audit_logger (D1, DevPlan 116 B11 T2). Маппит deploy-поля
    ##            (operation/project/channel/result/duration_s/snapshot_id) в расширенную
    ##            схему write_audit_entry(tag="deploy:<operation>", **extra).
    ##            Все записи идут в ЕДИНЫЙ файл audit.jsonl (DEFAULT_LOG_FILE).
    ## @io — ⇥ log_file: str (default shared DEFAULT_LOG_FILE) → ⎋ None
    ## @complexity O(1) per call
    ## @invariants
    ##   - .log()/.log_many() интерфейс сохраняется (тесты/вызовы не ломаются)
    ##   - НИКАКОГО прямого f.write — все записи через shared write_audit_entry
    ##   - tag = "deploy:<operation>", status = result (или "UNKNOWN")
    ##   - Расширенная схема: extra-поля (operation, project, channel, result, duration_s,
    ##     snapshot_id, projects, per_project_results) — в ту же JSON-строку
    """

    def __init__(self, log_file: str = _SHARED_AUDIT_LOG_FILE):
        self.log_file = log_file

    def log(
        self,
        operation: str,
        project: str,
        channel: str = "",
        result: str = "",
        duration_s: float = 0.0,
        snapshot_id: str | None = None,
        **extra: object,  # строковые/структурные audit-поля (error_info, projects, ...) — проброс в shared write_audit_entry
    ) -> None:
        """Write a single-project deploy audit entry via shared write_audit_entry."""
        _shared_write_audit_entry(
            tag=f"deploy:{operation}",
            status=result or "UNKNOWN",
            message=f"{operation} project={project} channel={channel or '-'}",
            log_file=self.log_file,
            operation=operation,
            project=project,
            channel=channel,
            result=result,
            duration_s=round(duration_s, 3),
            snapshot_id=snapshot_id or "",
            **extra,  # pyright: ignore[reportArgumentType] — kwargs-проброс произвольных extra-полей аудита (W11)
        )

    def log_many(
        self,
        operation: str,
        projects: list[str],
        channel: str = "",
        results: list[str] | None = None,
        overall_result: str = "",
    ) -> None:
        """Write a multi-project deploy audit entry via shared write_audit_entry."""
        _shared_write_audit_entry(
            tag=f"deploy:{operation}",
            status=overall_result or "UNKNOWN",
            message=f"{operation} {len(projects)} project(s)",
            log_file=self.log_file,
            operation=operation,
            projects=projects,
            channel=channel,
            result=overall_result,
            project_count=len(projects),
            per_project_results=results or [],
        )


# endregion CLASS_DeployAuditLogger
