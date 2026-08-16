#!/usr/bin/env python3
# GREP_SUMMARY: deploy-engine-flow, pull-retry, atomic-up, health-poll, flow-steps, docker-compose, 170-W4-B2
# STRUCTURE: ▶ ┌project_dir, service, ref┐ → pull_images (retry_pull, backoff [5,10,20,40,60]) →
#            up_atomic (compose up, IMAGE_TAG=ref) → wait_health (healthcheck_poll ≤ max_wait) → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Извлечённые приватные шаги DeployEngine.deploy() (170 W4-B2): pull-ретраи, atomic-up, health-poll.
## @scope    core/internal/deploy/engine/flow.py — вызывается из DeployEngine.deploy (engine.py).
##           Семантика 1:1 с монолитом deploy_engine.py (821 LOC) — поведение НЕ изменено.
## @invariants
##   1. Все Docker-операции — через shared docker_compose_* (sole path, гейт docker_sole_path)
##   2. pull_images — 5 попыток, backoff [5,10,20,40,60] (~2 мин окно транзиентов, ночная сессия 141)
##   3. up_atomic — env_override={"IMAGE_TAG": ref}; shared = {**os.environ, **override} (D7)
##   4. wait_health — healthcheck_poll(project_name=service, timeout=max_wait, interval=2) == "healthy"
## @changes 170 W4-B2 — extracted from deploy_engine.py; 170 private-imports: приватные имена
##           шагов переименованы в публичные (U-07), единый holder `shared_docker_compose_up`
##           (без _-префикса) — lifecycle читает атрибут flow-модуля в рантайме
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.shared.docker_compose import (
    docker_compose_up as shared_docker_compose_up,
)
from core.internal.shared.docker_compose import (
    healthcheck_poll as _shared_healthcheck_poll,
)
from core.internal.shared.docker_compose import (
    retry_pull as _shared_retry_pull,
)
from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT, PULL_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC_pull_images
## @purpose  Pull image with retry (T5.1: shared retry_pull — backoff [5,10,20,40,60], env IMAGE_TAG).
## @io       ⇥ project_dir: str, service: str, ref: str → ⎋ bool (True = pull success)
## @complexity — O(5) — до 5 попыток с backoff
## @invariants
##   - max_attempts=5; backoff_seconds=[5,10,20,40,60]; timeout=PULL_TIMEOUT (SoT)
##   - env_override={"IMAGE_TAG": ref} (контракт T5.1, проверяется тестом test_deploy_retry_pull_wiring)
def pull_images(project_dir: str, service: str, ref: str) -> bool:
    """Pull image with retry (extracted from DeployEngine.deploy — 170 W4-B2)."""
    # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — first-deploy пул = FATAL при 15s ретраев
    # · Symptom: холодный бутстрап — пул tronyx-site (nginx:alpine) упал 3× подряд за ~20s
    # ·   (транзиент: mirror/DNS на окне параллельных пулов 21 модуля) → «First deploy failed —
    # ·   no rollback possible» (exit 10) → deploy_services FAILED → весь bootstrap падает.
    # ·   Ручной пул через минуту — успех (образ в кеше). Ошибка транзиентная, окно ретраев мало.
    # · Fix: first-deploy пул — 5 попыток, backoff [5,10,20,40,60] (~2 мин окно транзиентов);
    # ·   повторный пул после частичного кеша = cache_hit (доказательство в 04-TimingsReport).
    # · Rev: если транзиентные фейлы пулов станут >2 мин — поднять max_attempts/backoff.
    return _shared_retry_pull(
        project_dir,
        max_attempts=5,
        backoff_seconds=[5, 10, 20, 40, 60],
        timeout=PULL_TIMEOUT,
        service=service,
        env_override={"IMAGE_TAG": ref},
    )


# endregion FUNC_pull_images


# region FUNC_up_atomic
## @purpose  Execute docker compose up -d for single service (тонкая обёртка над shared, T5.2).
## @io       ⇥ project_dir: str, service: str, ref: str → ⎋ bool
## @complexity — O(1) — делегирование в shared docker_compose_up
## @invariants — env_override={"IMAGE_TAG": ref}; shared = {**os.environ, **override} (D7)
def up_atomic(project_dir: str, service: str, ref: str) -> bool:
    """Start service via docker compose up -d (extracted from DeployEngine._atomic_up — 170 W4-B2)."""
    logger.info("[IMP:9][up] Atomic up: %s (IMAGE_TAG=%s)", service, ref)
    return shared_docker_compose_up(
        project_dir,
        timeout=COMPOSE_UP_TIMEOUT,
        service=service,
        env_override={"IMAGE_TAG": ref},
    )


# endregion FUNC_up_atomic


# region FUNC_wait_health
## @purpose  Poll healthcheck until healthy or max_wait (T5.3: shared healthcheck_poll — inspect-критерий,
##           service-фильтр). Единственный holder `_shared_healthcheck_poll` — тест-патч целится сюда.
## @io       ⇥ service: str, max_wait: int → ⎋ bool (True = healthy)
## @complexity — O(T/I) где T = max_wait, I = interval=2
## @invariants — healthcheck_poll(project_name=service, timeout=max_wait, interval=2, service=service) == "healthy"
def wait_health(service: str, max_wait: int) -> bool:
    """Poll healthcheck (extracted from DeployEngine.deploy — 170 W4-B2)."""
    return _shared_healthcheck_poll(project_name=service, timeout=max_wait, interval=2, service=service) == "healthy"


# endregion FUNC_wait_health
