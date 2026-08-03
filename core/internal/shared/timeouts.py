#!/usr/bin/env python3
# GREP_SUMMARY: timeouts, shared, compose-up, pull, build, healthcheck-poll, ssh-connect, deploy, ssh-read, retry-backoff, image-check, docker-cmd, docker-stop, rsync, healthcheck-ports, constants
# STRUCTURE: ▶ ┌registry of operational timeouts┐ → ◇ docker domain (up/pull/build/healthcheck/cmd/stop/image) → ◇ ssh domain (connect/read/deploy) → ◇ retry (backoff list + exponential base) → ◇ healthcheck ports → ⎋ import targets
# region MODULE_CONTRACT
## @purpose  Единый реестр таймаутов операционных политик (DevPlan 116 B5 T1, U-11).
##           Единственный источник числовых значений timeout= в docker/ssh/healthcheck-домене
##           core/internal. Литералы {10,15,30,60,120,180,300,600} в этих доменах заменяются
##           импортом констант отсюда (гейт test_gate_timeout_literals.py enforce-ит).
## @scope    Все Python-модули core/internal + core/modules, выполняющие
##           docker/ssh/rsync/healthcheck операции.
##           Константы импортируются напрямую: `from core.internal.shared.timeouts import ...`.
##           shared/docker_compose.py и shared/ssh_opts.py используют эти константы как дефолты.
## @invariants
##   1. Числовые значения определены ТОЛЬКО здесь — 0 литералов {10,15,30,60,120,180,300,600}
##      вне этого файла в docker/ssh/healthcheck-домене (гейт timeout_literals).
##   2. Константы immutable (module-level ints/list) — мутация запрещена (гейт не ловит,
##      но ревью-стандарт).
##   3. RETRY_BACKOFF_SECONDS — список [5,10,20]; канал использует [0] с delay *= 2
##      (экспоненциальное поведение сохраняется).
##   4. Значения канонизированы: up=180, pull=300, build=300, healthcheck-poll=60,
##      ssh-connect=30, deploy=600, ssh-read=60, image-check=60, docker-cmd=10,
##      docker-stop=30, rsync=600, healthcheck-ports=[3000,4000,8000,8080,9000] (B6).
## @rationale U-11: 226 литералов timeout= (30/120/180/300/600) без констант. Единый реестр
##            делает значения grepable, гейт — enforce-емым. Значения стандартизированы из
##            существующих канонов (docker_orchestrator up=180, deploy-дефолт ssh.sh=600,
##            core_deliverer RSYNC_TIMEOUT=600, healthcheck_poll=60).
##            DevPlan 117 D (бриф D): + HEALTHCHECK_POLL_INTERVAL/MAX_RETRIES (D32/D34),
##            + RETRY_BACKOFF_EXPONENTIAL_BASE (D34), + SUDOERS_CMD_TIMEOUT (D28),
##            + PROJECT_HEALTHCHECK_PORTS (D36). Watchdog-домен удалён с подсистемой
##            watchdog (RC-сессия 2026-08-03, долг 119 C2).
## @changes  2026-08-01 | DevPlan 116 B5 T1 — Created (shared-реестр таймаутов)
## @changes  2026-08-01 | DevPlan 117 D — retry/ports домены (D28-D36)
## @changes  2026-08-02 | DevPlan 119 B7 — +CONVERGE_DOCKER_TIMEOUT (30), +FILE_OP_TIMEOUT (15)
## @changes  2026-08-03 | RC 121 — watchdog-домен (WATCHDOG_*, TOR_PROXY сохранён) удалён
##                      (converge/infra локальные константы удалены — импорт из канона)
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

# Интервал между опросами healthcheck (healthcheck_poll interval, docker_orchestrator retry_interval)
HEALTHCHECK_POLL_INTERVAL = 3

# Число retry-попыток healthcheck (HEALTHCHECK_POLL_TIMEOUT / HEALTHCHECK_POLL_INTERVAL = 20;
# docker_orchestrator run_healthcheck max_retries, healthcheck_poller max_retries)
HEALTHCHECK_POLL_MAX_RETRIES = 20

# Внутренние подвызовы docker ps/inspect/tag в shared-функциях (healthcheck_poll, docker_compose_*)
DOCKER_CMD_TIMEOUT = 10

# visudo -c -f <file> — валидация sudoers (sudoers_generator, отдельный от docker домен;
#   visudo на слабых VPS может занимать >DOCKER_CMD_TIMEOUT)
SUDOERS_CMD_TIMEOUT = 15

# docker stop/rm — lifecycle операции (orphan/legacy cleanup, rollback) — grace-period безопасный
DOCKER_STOP_TIMEOUT = 30

# converge/ таймаут docker/system команд (converge/infra DOCKER_TIMEOUT, DevPlan 119 B7):
#   docker info, docker volume inspect, docker compose config в R-юнитах reconcile.
#   Отдельный от DOCKER_CMD_TIMEOUT (10) — converge-домен использует 30s окно (C10 канон).
CONVERGE_DOCKER_TIMEOUT = 30

# converge/ таймаут файловых операций (converge/infra FILE_OP_TIMEOUT, DevPlan 119 B7):
#   chmod/chown/mkdir в R-юнитах + visudo -c (sudoers.py). 15s достаточно для файловых мутаций.
FILE_OP_TIMEOUT = 15

# systemctl restart docker — перезапуск docker-демона (docker_registry_auth _restart_docker,
# bootstrap φ3). systemctl restart на слабых VPS может занять >DOCKER_CMD_TIMEOUT.
DOCKER_RESTART_TIMEOUT = 60

# docker daemon readiness poll после systemctl restart docker (docker_registry_auth):
#   интервал опроса docker info + число попыток (6 × 5s = 30s окно ожидания демона)
DOCKER_RESTART_POLL_INTERVAL = 5
DOCKER_RESTART_POLL_RETRIES = 6

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

# База экспоненциального backoff state_machine (2**attempt: 2, 4, 8 — транзиентные ошибки шагов bootstrap)
RETRY_BACKOFF_EXPONENTIAL_BASE = 2

# Таймаут curl Tor-proxy healthcheck (tor_proxy_check.py, DevPlan 118 E5 — legacy MAX_TIME=30)
TOR_PROXY_CURL_TIMEOUT = 30

# ── Healthcheck ports domain ───────────────────────────────────────────────────

# Эвристические порты HTTP /health для проектов без healthcheck (healthcheck_poller _try_http)
# DevPlan 119 B6: расширен [8080,8000] → [3000,4000,8000,8080,9000] — покрывает реальные
# compose-порты платформы (grafana/langfuse 3000, litellm 4000, minio 9000) + Node/React
# (3000), Flask/Django (8000/8080), Go (8080/9000). Уникальные значения, отсортированы.
PROJECT_HEALTHCHECK_PORTS: list[int] = [3000, 4000, 8000, 8080, 9000]
