# GREP_SUMMARY: deploy-history, facade, audit-package, re-export, snapshots, rollback, 170-W4-B3
# STRUCTURE: ▶ тонкий фасад → re-export из пакета deploy/audit/ → ⎋ импорт-путь сохранён
# region MODULE_CONTRACT
## @purpose  Фасад пакета audit/history.py, 170 W4-B3 — re-export DeployHistory/констант
##           (импорт-путь `core.internal.deploy.deploy_history` сохранён).
## @scope    engine/engine.py lazy-импорт, тесты (test_deploy_history, test_orchestrator,
##           test_deploy_concurrent_lock, test_deploy_e2e), DeployOrchestrator.
## @invariants — 1) re-export всех публичных символов монолита; 2) старый контент удалён;
##               3) фасад НЕ импортируется пакетом audit/ (отсутствие цикла; import-linter green)
## @changes 170 W4-B3 — extracted from deploy_history.py
## ⚠️ TRAP[DECISION] · — · Фасад сохранён (177 W4 S8): 13 импортёров старого пути
##   `core.internal.deploy.deploy_history` + engine lazy-import — миграция всех импортёров
##   на deploy/audit даёт нулевую функциональную выгоду (фасад — 12 строк re-export,
##   нулевая логика). · Rev: если фасад начнёт нести логику ИЛИ число импортёров
##   старого пути упадёт до 0 — удалить фасад и перевести импорты на deploy.audit.
# endregion MODULE_CONTRACT

from core.internal.deploy.audit import LOCK_DIR, MAX_SNAPSHOTS, SNAPSHOT_DIR, DeployHistory

__all__ = [
    "LOCK_DIR",
    "MAX_SNAPSHOTS",
    "SNAPSHOT_DIR",
    "DeployHistory",
]
