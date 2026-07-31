# 04-Brief — B5: Shared-консолидация операционных политик

<!-- GREP_SUMMARY: docker-compose healthcheck ssh-opts timeouts retry shared policies sole-path platform_config -->
<!-- STRUCTURE: ┌scope┐ → ◇ docker-политики → ◇ healthcheck → ◇ SSH → ◇ константы → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B5: сделать shared-модули операционных политик ЕДИНСТВЕННЫМ путём (docker/healthcheck/SSH/timeouts/retry) и удалить расходящиеся копии.
## @scope    U-11, U-13, U-14, U-15, U-34, U-63
## @invariants
##   - Sole-path: каждая операционная политика имеет ровно одну реализацию; копии запрещены гейтом.
##   - Комментарии «Mirror lib/ssh.sh» устраняются — импорт канона вместо копирования.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Консолидировать 4-5 копий каждой операционной политики в shared-модули и перевести всех потребителей.
  DESCRIPTION: docker_compose.py становится единственным путём (up/pull/retry), healthcheck — единый критерий, SSH_OPTS — единая константа, timeouts — shared/timeouts.py, platform_config без ручных fallback-копий SoT.
  RATIONALE: Shared-модули созданы, но мёртвые (docker_compose_up — 0 production-потребителей, shared/AGENTS.md:31 врёт). Каждая новая волна добавляет 4-ю копию вместо перехода на shared (RC7). Расхождение уже началось: --remove-orphans только в одной копии, timeout 120/180/30, ConnectTimeout 10 vs 30.
  ACCEPTANCE_CRITERIA: (1) docker compose up/pull — одна реализация (shared/docker_compose.py), 4 копии удалены; флаги/timeouts едины; (2) retry_pull — одна реализация с backoff [5,10,20] (docker_orchestrator получает retry); (3) healthcheck — единый критерий «здоров» (inspect State.Health), 5 реализаций → 1 + тонкие обёртки с параметрами; (4) SSH_OPTS — одна константа в lib/ssh.sh или shared, все Python-потребители импортируют; ConnectTimeout единый; (5) shared/timeouts.py: COMPOSE_UP_TIMEOUT/PULL_TIMEOUT/SSH_TIMEOUT/HEALTHCHECK_TIMEOUT — все литералы заменены; (6) platform_config: fallback-константы удалены, чтение platform-env.yaml через deploy_paths; (7) healthcheck-интервалы в compose: стандартизированы (10s/30s/60s политика) + гейт.
  IMPLEMENTS: U-11 (timeouts ×226), U-13 (docker ops ×4 + retry ×3), U-14 (healthcheck ×5), U-15 (SSH_OPTS ×5), U-34 (platform_config fallbacks), U-63 (интервалы healthcheck)
  IMPACTS: core/internal/shared/docker_compose.py, deploy_engine.py, docker_orchestrator.py, context_deployer.py, reconciler.py, channels.py, overlay_deliverer.py, core_deliverer.py, context_promoter.py, remote_executor.py, lib/ssh.sh, lib/healthcheck.sh, platform_config.py
  REQUIRES: B2 (паритет-гейты), B4 (контракт исключений — shared-модули используют PlatformError)

---

## Scope

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-11 | 226 timeout=, значения 30/120/180/300/600, нет констант; state_machine:16 «120s standard» | core/internal/**/*.py |
| U-13 | 4 compose up (120/180/120/30 + флаги), 3 retry_pull [5,10,20], shared мёртв | deploy_engine.py:791-822, docker_orchestrator.py:567-575,647-661, shared/docker_compose.py:155-195,269-313, reconciler.py:2057-2068, channels.py:118-177 |
| U-14 | Healthcheck ×5: ps-filter 60/3, wrapper 10/1, inspect 60/2, lib/healthcheck.sh, poller 30 | docker_compose.py:203-257, context_deployer.py:464-466, deploy_engine.py:834-875, lib/healthcheck.sh:179-213, healthcheck_poller.py:36-38 |
| U-15 | SSH_OPTS ×5 «Mirror lib/ssh.sh», ConnectTimeout=10 outlier | lib/ssh.sh:57-62, overlay_deliverer.py:83-84, core_deliverer.py:37-38, channels.py:202-213,390-399, context_promoter.py:74 |
| U-34 | platform_config: fallback-копии SoT + cwd-эвристика | platform_config.py:33-40,70-87 |
| U-63 | healthcheck-интервалы 10/15/30/60 без гейта; postgres сам себе противоречит | core/modules/*/docker-compose.base.yml |

## Ключевые артефакты

1. `shared/timeouts.py` (или секция в shared/contracts.py): COMPOSE_UP_TIMEOUT=180, PULL_TIMEOUT=300, SSH_TIMEOUT=30, HEALTHCHECK_POLL_TIMEOUT=60; ruff-гейт на литералы timeout= в core/internal (allowlist на время миграции).
2. docker_compose.py — sole path: migrate docker_orchestrator/deploy_engine/reconciler на `docker_compose_up`; флаги политики (--remove-orphans/--force-recreate) — параметры с дефолтами; удаление 4 локальных реализаций.
3. retry_pull: единая функция в docker_compose.py; docker_orchestrator подключает retry.
4. healthcheck: единый `healthcheck_poll` (inspect State.Health + таймауты параметрами); deploy_engine._poll_health и context_deployer-обёртка — тонкие вызовы; lib/healthcheck.sh остаётся shell-фасадом для модулей (вызывает тот же критерий).
5. SSH_OPTS: экспорт константы из lib/ssh.sh (через shared/ssh_opts.py для Python), замена 5 копий; ConnectTimeout единый (30); TRAP[DECISION] vps_readiness:37-42 («extract when consumers > 3») — триггер сработал, решение зафиксировать.
6. platform_config: чтение через shared/deploy_paths.py; fallback-константы удалены (или гейт «fallback == SoT»).
7. Интервалы healthcheck: политика 15s/30s/60s по классам модулей; гейт на интервалы.

## Гейт самоверификации волны

- Sole-path-гейт: rg «docker compose up» в core/internal — только shared/docker_compose.py + entrypoints.
- Гейт на SSH_OPTS-флаги: 0 «Mirror lib/ssh.sh» комментариев.
- Гейт timeout-литералов (allowlist сжимается до 0).

## Зависимости

- От: B2 (паритет), B4 (PlatformError в shared-модулях).
- К: B9 (SRP-декомпозиция — монолиты упрощаются после консолидации политик).
