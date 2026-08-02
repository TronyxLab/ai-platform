#!/usr/bin/env python3
# GREP_SUMMARY: first-deploy, handle-first-deploy, PlatformFatalError, exit-10, no-rollback, E4, deploy-engine-decomposition
# STRUCTURE: ▶ handle_first_deploy ┌project, service, ref, reason┐ → ⊕ log CRITICAL → ⊕ raise PlatformFatalError (exit 10) → ⎋ никогда не возвращает
# region MODULE_CONTRACT
## @purpose  First-deploy failure handler (DevPlan 119 E4) — extracted from DeployEngine._handle_first_deploy
##           (deploy_engine.py 874 LOC монолит): первый деплой без предыдущего образа = нет rollback,
##           требуется ручное вмешательство → PlatformFatalError (exit code 10, DevPlan 116 B4 T3.1).
## @scope    core/internal/deploy/first_deploy.py — consumed by DeployEngine (тонкий фасад-делегат).
## @invariants
##   - ВСЕГДА raise PlatformFatalError — функция никогда не возвращает (unreachable после вызова)
##   - exit code 10 (FATAL — ручное вмешательство, контракт shared/contracts.py)
##   - Первый деплой = нет предыдущего image → rollback невозможен → escalate
## @rationale E4 (DevPlan 119, AUDIT-2 M9): _handle_first_deploy вынесен из монолита deploy_engine.py
##           в изолированный модуль — тестируемость + декомпозиция 874→<600 LOC.
## @changes  2026-08-02 · DevPlan 119 E4 — экстракция из DeployEngine._handle_first_deploy
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.shared.exceptions import PlatformFatalError

logger = logging.getLogger(__name__)


# region FUNC_handle_first_deploy
## @purpose  Handle first deploy failure — no rollback possible, escalate to FATAL (exit 10).
## @io       ⇥ project: str, service: str, ref: str, reason: str → ⎋ None
##           ⚡ PlatformFatalError — ВСЕГДА (no rollback possible, requires manual intervention)
## @complexity O(1) — log + raise
## @invariants
##   - Never returns — always raises PlatformFatalError (exit 10)
##   - Сообщение включает reason для диагностики
def handle_first_deploy(project: str, service: str, ref: str, reason: str) -> None:
    """Handle first deploy failure — no rollback possible.

    Args:
        project: Project name.
        service: Service name.
        ref: Image ref.
        reason: Failure reason for logging.

    Raises:
        PlatformFatalError: Always — no rollback possible, requires manual intervention
            (DevPlan 116 B4 T3.1: sys.exit(1) → raise PlatformFatalError, exit code 10).
    """
    logger.error(
        "[IMP:10][first-deploy] CRITICAL: %s — %s no previous image to rollback (project=%s ref=%s)",
        reason,
        service,
        project,
        ref,
    )
    raise PlatformFatalError(f"First deploy failed — no rollback possible: {reason}")


# endregion FUNC_handle_first_deploy
