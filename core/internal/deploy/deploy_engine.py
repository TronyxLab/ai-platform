#!/usr/bin/env python3
# GREP_SUMMARY: deploy-engine, facade, engine-package, re-export, atomic-deploy, rollback, remove, status, 170-W4-B2
# STRUCTURE: ▶ тонкий фасад → re-export из пакета engine/ → ⎋ импорт-путь сохранён
# region MODULE_CONTRACT
## @purpose  Фасад пакета engine/, 170 W4-B2 — re-export DeployEngine/датаклассы/main (импорт-путь сохранён)
## @scope    orchestrator.py lazy-импорты (B3-скоуп), тесты, CLI python3 -m deploy_engine
## @invariants — 1) re-export всех публичных символов монолита; 2) старый контент удалён; 3) пакет engine/ фасад не импортирует
## @changes 170 W4-B2 — extracted from deploy_engine.py
# endregion MODULE_CONTRACT

import sys

from core.internal.deploy.engine import DeployEngine, ImageInfo, RemoveResult, ServiceDeployResult, StatusResult, main
from core.internal.deploy.preflight import DeployError, ValidationError

__all__ = [
    "DeployEngine",
    "DeployError",
    "ImageInfo",
    "RemoveResult",
    "ServiceDeployResult",
    "StatusResult",
    "ValidationError",
    "main",
]

if __name__ == "__main__":
    sys.exit(main())
