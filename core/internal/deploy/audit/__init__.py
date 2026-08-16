# GREP_SUMMARY: deploy-audit, audit-package, re-export, DeployAuditLogger, DeployHistory, snapshots, 170-W4-B3
# STRUCTURE: ▶ deploy/audit/__init__ → re-export hub → ⊕ DeployAuditLogger (logger.py) + DeployHistory/SNAPSHOT_DIR/LOCK_DIR/MAX_SNAPSHOTS (history.py) → ⎋ единый публичный API пакета audit/
# region MODULE_CONTRACT
## @purpose  Публичный API пакета core/internal/deploy/audit/ (170 W4-B3) — re-export
##           DeployAuditLogger (logger.py) и DeployHistory/констант (history.py). Импорт-путь
##           `core.internal.deploy.audit` сохраняет прежние символы монолита deploy/orchestrator.py
##           (DeployAuditLogger) и deploy/deploy_history.py (DeployHistory/MAX_SNAPSHOTS/SNAPSHOT_DIR).
## @scope    Декомпозиция монолитов (research-A §3, B3): orchestrator.py (~80-149) +
##           deploy_history.py (407 LOC) → пакет audit/. Фасад deploy/deploy_history.py
##           ре-экспортирует отсюда, сохраняя прежний импорт-путь (engine/engine.py, тесты).
## @invariants
##   1. ВСЕ публичные символы монолитов доступны через этот пакет
##   2. Пакет НЕ импортирует фасад deploy/deploy_history.py и НЕ импортирует orchestrator.py
##      (отсутствуют циклы; import-linter green — acyclic-internal-domains)
## @changes 2026-08-15 | 170 W4-B3 — extracted from deploy/orchestrator.py + deploy/deploy_history.py
# endregion MODULE_CONTRACT

from core.internal.deploy.audit.history import LOCK_DIR, MAX_SNAPSHOTS, SNAPSHOT_DIR, DeployHistory
from core.internal.deploy.audit.logger import DeployAuditLogger

__all__ = [
    "LOCK_DIR",
    "MAX_SNAPSHOTS",
    "SNAPSHOT_DIR",
    "DeployAuditLogger",
    "DeployHistory",
]
