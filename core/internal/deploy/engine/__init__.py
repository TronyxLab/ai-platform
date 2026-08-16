#!/usr/bin/env python3
# GREP_SUMMARY: deploy-engine, engine-package, re-export, atomic-deploy, rollback, remove, status, 170-W4-B2, facade
# STRUCTURE: ▶ re-export hub → ⊕ DeployEngine (engine.py) + 4 dataclass (results.py) + main (cli.py) → ⎋ единый публичный API пакета engine/
# region MODULE_CONTRACT
## @purpose  Публичный API пакета core/internal/deploy/engine/ (170 W4-B2) — re-export DeployEngine,
##           result-dataclass'ов и CLI main. Фасад core/internal/deploy/deploy_engine.py ре-экспортирует
##           отсюда, сохраняя прежний импорт-путь (orchestrator.py lazy-импорты, тесты).
## @scope    Декомпозиция монолита deploy_engine.py (821 LOC → пакет): engine.py (класс DeployEngine),
##           flow.py (шаги deploy: pull/up/health), lifecycle.py (save-prev/rollback/first-deploy),
##           results.py (4 dataclass), cli.py (main).
## @invariants
##   1. ВСЕ публичные символы монолита доступны через этот пакет (и через фасад deploy_engine.py)
##   2. Пакет НЕ импортирует фасад deploy_engine.py (отсутствует цикл; import-linter green)
## @changes 170 W4-B2 — extracted from deploy_engine.py
# endregion MODULE_CONTRACT

from core.internal.deploy.engine.cli import main
from core.internal.deploy.engine.engine import DeployEngine
from core.internal.deploy.engine.results import (
    ImageInfo,
    RemoveResult,
    ServiceDeployResult,
    StatusResult,
)

__all__ = [
    "DeployEngine",
    "ImageInfo",
    "RemoveResult",
    "ServiceDeployResult",
    "StatusResult",
    "main",
]
