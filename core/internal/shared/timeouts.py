#!/usr/bin/env python3
# GREP_SUMMARY: timeouts, shared, compose-up, pull, build, healthcheck-poll, ssh-connect, deploy, ssh-read, retry-backoff, image-check, docker-cmd, docker-stop, rsync, constants
# STRUCTURE: ▶ ┌registry of operational timeouts┐ → ◇ docker domain (up/pull/build/healthcheck/cmd/stop/image) → ◇ ssh domain (connect/read/deploy) → ◇ retry (backoff list) → ⎋ import targets
# region MODULE_CONTRACT
## @purpose  Единый реестр таймаутов операционных политик (DevPlan 116 B5 T1, U-11).
##           Единственный источник числовых значений timeout= в docker/ssh/healthcheck-домене
##           core/internal. Литералы {30,60,120,180,300,600} в этих доменах заменяются
##           импортом констант отсюда (гейт test_gate_timeout_literals.py enforce-ит).
## @scope    Все Python-модули core/internal, выполняющие docker/ssh/rsync/healthcheck операции.
##           Константы импортируются напрямую: `from core.internal.shared.timeouts import ...`.
##           shared/docker_compose.py и shared/ssh_opts.py используют эти константы как дефолты.
## @invariants
##   1. Числовые значения определены ТОЛЬКО здесь — 0 литералов {30,60,120,180,300,600}
##      вне этого файла в docker/ssh/healthcheck-домене (гейт timeout_literals).
##   2. Константы immutable (module-level ints/list) — мутация запрещена (гейт не ловит,
##      но ревью-стандарт).
##   3. RETRY_BACKOFF_SECONDS — список [5,10,20]; канал использует [0] с delay *= 2
##      (экспоненциальное поведение сохраняется).
##   4. Значения канонизированы: up=180, pull=300, build=300, healthcheck-poll=60,
##      ssh-connect=30, deploy=600, ssh-read=60, image-check=60, docker-cmd=10,
##      docker-stop=30, rsync=600.
## @rationale U-11: 226 литералов timeout= (30/120/180/300/600) без констант. Единый реестр
##            делает значения grepable, гейт — enforce-емым. Значения стандартизированы из
##            существующих канонов (docker_orchestrator up=180, deploy-дефолт ssh.sh=600,
##            core_deliverer RSYNC_TIMEOUT=600, healthcheck_poll=60).
## @changes  2026-08-01 | DevPlan 116 B5 T1 — Created (shared-реестр таймаутов)
# endregion MODULE_CONTRACT

# ── Docker domain ────────────────────────────────────────────────────────────

# docker compose up -d (docker_compose_up, docker_orchestrator, deploy_engine, reconciler self-heal)
COMPOSE_UP_TIMEOUT = 180

# docker compose pull / retry_pull (docker_compose_pull, retry_pull, deploy_engine, docker_orchestrator)
PULL_TIMEOUT = 300

# docker compose build (docker_compose_build, docker_orchestrator build-skip/inline)
BUILD_TIMEOUT = 300

# Poll окно healthcheck (healthcheck_poll, context_deployer, deploy_engine, bash-healthcheck invoke)
HEALTHCHECK_POLL_TIMEOUT = 60

# Внутренние подвызовы docker ps/inspect/tag в shared-функциях (healthcheck_poll, docker_compose_*)
DOCKER_CMD_TIMEOUT = 10

# docker stop/rm — lifecycle операции (orphan/legacy cleanup, rollback) — grace-period безопасный
DOCKER_STOP_TIMEOUT = 30

# docker manifest inspect + docker image inspect/prune (check_image_exists, image-майнтенанс)
IMAGE_CHECK_TIMEOUT = 60

# ── SSH domain ───────────────────────────────────────────────────────────────

# SSH connect timeout (ssh_opts.SSH_OPTS ConnectTimeout, context_promoter github-probe,
# ssh mkdir/keygen подвызовы)
SSH_CONNECT_TIMEOUT = 30

# Деплой-таймаут SSH (channels DEFAULT_DEPLOY_TIMEOUT, remote_executor, deploy-many)
DEPLOY_TIMEOUT = 600

# Read-only SSH команды (ssh_read-эквиваленты, SCPChannel unpack)
SSH_READ_TIMEOUT = 60

# rsync передачи (overlay_deliverer node.yaml/overlays — канон core_deliverer RSYNC_TIMEOUT=600)
RSYNC_TIMEOUT = 600

# ── Retry domain ─────────────────────────────────────────────────────────────

# Backoff-список retry_pull (docker_compose) и экспоненциальный backoff каналов (channels)
RETRY_BACKOFF_SECONDS: list[int] = [5, 10, 20]

# Число retry-попыток (channels DEFAULT_RETRY_COUNT)
RETRY_COUNT = 2
