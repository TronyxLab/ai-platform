# GREP_SUMMARY: postgres Makefile start stop restart status logs backup restore BACKUP_MODE custom
# STRUCTURE: ┌postgres lifecycle┐ → ◇ module.mk include (start/restart/down/up) → ⊕ custom start/backup/restore + STOP_TIMEOUT=60
# region MODULE_CONTRACT
## @purpose  Module lifecycle management for postgres Docker module
## @scope    Called by platform users per role; sudo-whitelist enforces per-role restrictions
## @invariants
##   - All docker compose calls use docker-compose.base.yml
##   - env-file loaded from SECRETS_ENV_FILE (default: /run/platform/secrets.env)
##   - BACKUP_MODE := custom — postgres объявляет рецепты backup/restore ПОСЛЕ include (D1)
##   - STOP_TIMEOUT := 60 — WAL flush grace для compose stop (D2); наследуется restart/down
##   - backup target uses pg_dumpall via docker exec
##   - restore is allowed only for owner role (sudo-whitelist.conf enforces at OS level)
##   - restore contract (REF-0009, DATA-504 ≡ FAIL-0803): mandatory pre-restore
##     pg_dumpall snapshot (ABORT при отказе) → clean stop → up + pg_isready →
##     psql -v ON_ERROR_STOP=1; runbook (вкл. age-decrypt шаг) — docs-in-code выше
##   - Plan 012 T7 (F-031): restore работает ШТАТНО — ROOT-compose (SoT volumes:
##     «undefined volume postgres-data» при -f base.yml устранён) + --env-file
##     secrets.env + COMPOSE_PROFILES=postgres («no service selected» устранён)
##   - Plan 012 T7 (SEC-0018/F-032): pre_restore_* снапшоты пишутся в отдельный каталог
##     backup-spool/pre-restore — ВНЕ retry-скана (plaintext не попадает в S3-пайплайн)
## @rationale Targets match standard module lifecycle; postgres-specific: 60s stop grace,
##   pg_dumpall backup, psql restore — объявлены после include (last-recipe-wins).
# endregion MODULE_CONTRACT

MODULE_NAME := postgres
CONTAINER := postgres
SECRETS_ENV := $(or $(SECRETS_ENV_FILE),/run/platform/secrets.env)
# 60s grace для WAL flush при compose stop (наследуется restart/down/restart-hard из module.mk)
STOP_TIMEOUT := 60
BACKUP_MODE := custom

include ../../templates/module.mk

# ── Plan 012 T7 (F-031): DR restore-канон — root-compose + secrets + profiles ──
# Root docker-compose.yml — единственный SoT volumes (postgres-data объявлен там);
# base.yml не знает shared-томов → «undefined volume». COMPOSE_FILE переопределяется
# ТОЛЬКО для таргета restore (target-specific variable) — текст рецепта остаётся
# каноническим $(COMPOSE_CMD) (контракт docker_sole_path D70). Профиль фильтруется
# нативным env COMPOSE_PROFILES=postgres («no service selected» устранён).
PLATFORM_ROOT := $(abspath $(MODULE_DIR)/../..)
# SEC-0018: pre_restore снапшоты — ВНЕ retry-скана (spool_retry сканирует только postgres/ и app-data/)
PRE_RESTORE_DIR := /var/lib/platform/backup-spool/pre-restore

# ── Custom start ──
## start: Start postgres container
start: ## Start postgres (compose up -d)
	@echo "[IMP:7][postgres-make][start] Starting postgres"
	$(COMPOSE_CMD) up -d
	@echo "[IMP:8][postgres-make][start] postgres started"

# ── Custom backup: pg_dumpall via docker exec ──
## backup: Dump all databases to /var/lib/platform/backup-spool/
## ⚠️ TRAP[BUG] · 1.0.0 · HI · -U postgres хардкод → «role postgres does not exist» на кластерах
## · с кастомным POSTGRES_USER (secrets). Fix: суперюзер из env КОНТЕЙНЕРА (compose-инъекция).
backup: ## pg_dump all databases to backup spool
	@echo "[IMP:7][postgres-make][backup] Starting pg_dump backup"
	@TIMESTAMP=$$(date -u '+%Y%m%dT%H%M%SZ'); \
	BACKUP_DIR=/var/lib/platform/backup-spool/postgres; \
	mkdir -p "$$BACKUP_DIR"; \
	docker exec $(CONTAINER) sh -c 'pg_dumpall -U "$$POSTGRES_USER"' \
		> "$$BACKUP_DIR/pgdumpall_$${TIMESTAMP}.sql" && \
	echo "[IMP:9][postgres-make][backup] DONE: pgdumpall_$${TIMESTAMP}.sql" || \
	{ echo "[IMP:9][postgres-make][backup] FAIL: pg_dump failed" >&2; exit 1; }

# ── Module-specific targets ──
## restore: Restore from dump file DUMP_FILE=<path> (owner only)
##
# F-031: restore исполняется против ROOT-compose (volumes SoT); остальной стек не
# трогается (COMPOSE_PROFILES=postgres в фазах ниже). Target-specific variable —
# только для этого таргета; текст рецепта остаётся каноническим $(COMPOSE_CMD) (D70).
restore: COMPOSE_FILE = $(PLATFORM_ROOT)/docker-compose.yml

## ── RESTORE RUNBOOK (docs-in-code, REF-0009 / DATA-504 ≡ FAIL-0803) ──
## Полная DR-процедура восстановления кластера из off-site бэкапа:
##   1. Достать последний ПОДТВЕРЖДЁННЫЙ дамп из S3 (sentinel-контракт REF-0009):
##        aws s3 cp "s3://$S3_BUCKET/$S3_PREFIX/postgres/pgdumpall_<TS>.sql.gz.age" .
##   2. Расшифровать (age-encrypt контракт REF-0009; приватный ключ — НЕ в backup-cron):
##        age -d -i /etc/age/key.txt -o pgdumpall_<TS>.sql.gz pgdumpall_<TS>.sql.gz.age
##   3. Восстановить: make -C core/modules/postgres restore DUMP_FILE=pgdumpall_<TS>.sql.gz
##      (этот таргет: pre-snapshot → down → up → psql ON_ERROR_STOP=1)
##   4. Верификация: make healthcheck + project-status по проектам.
## Порядок фаз внутри таргета (исполнимая семантика, см. TRAP[DECISION] ниже):
##   PHASE 1 pre-snapshot (кластер ЖИВОЙ — страховка до разрушения)
##   → PHASE 2 down (чистый стоп, STOP_TIMEOUT=60 WAL flush)
##   → PHASE 3 up + pg_isready wait
##   → PHASE 4 gunzip|psql -v ON_ERROR_STOP=1.
# 🧐 TRAP[DECISION] · 2026-08-25 · — · Restore-порядок: pre-dumpall ДО down (не down→dumpall)
# · Rejected: литеральный порядок карточки «down → psql → up» с pre-dumpall после down
# · Reason: pg_dumpall требует живой кластер — снапшот-страховка физически возможен
# ·   ТОЛЬКО до остановки; psql требует слушающий сервер — восстановление идёт после
# ·   up+pg_isready, а не между down и up. Смысл контракта сохранён: чистый down/up-
# ·   брекет вокруг заливки + обязательный pre-snapshot + fail-fast ON_ERROR_STOP.
restore: ## Restore from dump file (owner only, requires DUMP_FILE=)
	@echo "[IMP:7][postgres-make][restore] Restoring from DUMP_FILE=$(DUMP_FILE)"
	@if [[ -z "$(DUMP_FILE)" ]]; then \
		echo "[IMP:9][postgres-make][restore] ERROR: DUMP_FILE not set" >&2; exit 1; \
	fi
	@if [[ ! -f "$(DUMP_FILE)" ]]; then \
		echo "[IMP:9][postgres-make][restore] ERROR: DUMP_FILE not found: $(DUMP_FILE)" >&2; exit 1; \
	fi
	@# PHASE 1: MANDATORY pre-restore snapshot (страховка перед восстановлением).
	@# Отказ снапшота = ABORT до любых разрушающих действий (данные кластера не тронуты).
	@# SEC-0018: каталог ВНЕ retry-скана spool_retry (plaintext .sql не попадает в S3-пайплайн).
	@PRE_TS=$$(date -u '+%Y%m%dT%H%M%SZ'); \
	mkdir -p "$(PRE_RESTORE_DIR)"; \
	echo "[IMP:8][postgres-make][restore] Pre-restore snapshot -> $(PRE_RESTORE_DIR)/pre_restore_$${PRE_TS}.sql"; \
	docker exec $(CONTAINER) sh -c 'pg_dumpall -U "$$POSTGRES_USER"' \
		> "$(PRE_RESTORE_DIR)/pre_restore_$${PRE_TS}.sql" || \
		{ echo "[IMP:10][postgres-make][restore] ABORT: pre-restore snapshot failed — cluster NOT touched" >&2; exit 1; }; \
	echo "[IMP:9][postgres-make][restore] Pre-restore snapshot OK"
	@# ⚠️ TRAP[BUG] · 1.0.0 · HI · .sql.gz-дампы (backup-cron nightly) скармливались psql сырьём →
	@# «invalid byte sequence»; .sql — как есть (резервная совместимость).
	@# PHASE 2: clean stop (compose stop; STOP_TIMEOUT=60 наследуется) — writers отсечены,
	@# кластер не «полуживой» во время заливки (DATA-504: restore поверх живого кластера).
	@# F-031: root-compose (COMPOSE_FILE override выше) + профиль — «no service selected» guard.
	@echo "[IMP:7][postgres-make][restore] Stopping postgres (clean stop, WAL flush)"
	COMPOSE_PROFILES=postgres $(COMPOSE_CMD) stop --timeout $(STOP_TIMEOUT)
	@# PHASE 3: fresh start + readiness wait — psql требует слушающий сервер.
	@echo "[IMP:7][postgres-make][restore] Starting postgres for restore"
	COMPOSE_PROFILES=postgres $(COMPOSE_CMD) up -d
	@RETRY=30; until docker exec $(CONTAINER) sh -c 'pg_isready -U "$$POSTGRES_USER"' >/dev/null 2>&1; do \
		RETRY=$$((RETRY-1)); \
		if [ $$RETRY -le 0 ]; then \
			echo "[IMP:10][postgres-make][restore] ABORT: postgres not ready after wait" >&2; exit 1; \
		fi; sleep 2; done
	@# PHASE 4: заливка с fail-fast — ON_ERROR_STOP=1 прерывает на ПЕРВОЙ ошибке SQL
	@# (раньше молча получали «Restore complete» над полу-смесью). pipefail: rc gunzip не теряется.
	@if [[ "$(DUMP_FILE)" == *.gz ]]; then \
		set -o pipefail; \
		gunzip -c "$(DUMP_FILE)" | docker exec -i $(CONTAINER) sh -c 'psql -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" < /dev/stdin' \
			|| { echo "[IMP:10][postgres-make][restore] FAIL: psql ON_ERROR_STOP aborted — cluster in PARTIAL state; inspect pre_restore_*.sql snapshot in $(PRE_RESTORE_DIR)/" >&2; exit 1; } \
	else \
		set -o pipefail; \
		docker exec -i $(CONTAINER) sh -c 'psql -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" < /dev/stdin' < "$(DUMP_FILE)" \
			|| { echo "[IMP:10][postgres-make][restore] FAIL: psql ON_ERROR_STOP aborted — cluster in PARTIAL state; inspect pre_restore_*.sql snapshot in $(PRE_RESTORE_DIR)/" >&2; exit 1; } \
	fi
	@echo "[IMP:9][postgres-make][restore] Restore complete from $(DUMP_FILE) — run make healthcheck to verify"

## ── DR-порядок и стратегия чистоты (plan 012 T7 / F-032, docs-in-code) ──
## Порядок DR: «postgres-only → restore → приложения» — восстановление выполняется на
## ноде БЕЗ приложений (COMPOSE_PROFILES=postgres), затем поднимается остальной стек.
## Стратегия чистоты — ПО ФАКТУ КОДА: pg_dumpall пишет дамп БЕЗ --clean/--create →
## заливка поверх кластера с init-инициализацией конфликтует («role/database already
## exists», F-032). Честные варианты: (а) свежая нода / ПУСТОЙ volume postgres-data
## перед restore (канон пересоздания, инвариант 9 root); (б) --clean-дампы (смена
## стратегии backup — отдельное решение владельца). ON_ERROR_STOP=1 делает частичную
## заливку громкой (rc=3), pre_restore_* снапшот в $(PRE_RESTORE_DIR) — страховка.

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Custom overrides: postgres-specific lifecycle
# · Rejected: use module.mk defaults only (not flexible enough)
# · Reason: postgres требует 60s stop grace (WAL flush), pg_dump backup via docker exec,
# ·   restore через psql. Эти опции не покрываются module.mk без variable-параметров.
# · Rev: 2026-08-01 ВЫПОЛНЕНО — module.mk получил configurable stop_timeout
# ·   через STOP_TIMEOUT := 60 (этот Makefile) + BACKUP_MODE=custom контракт.
# ·   stop/restart-hard/status/logs предоставляет module.mk (канон lifecycle).
# ·   Новое Rev: если потребуется кастомный stop/status для postgres — override ПОСЛЕ include.
