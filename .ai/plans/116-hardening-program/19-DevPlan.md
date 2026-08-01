# 19-DevPlan — B7: Модульный контракт (make-контракт, конфиги, зависимости)

<!-- GREP_SUMMARY: module-contract module.mk Makefile.common restore restart restart-hard backup BACKUP_MODE BACKUP_SOURCE_FILE state.json nginx config dev-config docker-compose.dev.yml pyproject httpx import-gate monitoring_config_renderer render-monitoring volume-rename -->
<!-- STRUCTURE: ┌решения D1-D5┐ → ◇ T1 module.mk restart → ◇ T2 Makefile.common → ◇ T3 модульные Makefile → ◇ T4 AGENTS.md+глоссарий → ◇ T5 nginx config/dev-config → ◇ T6 pyproject+import-гейт → ◇ T7 renderer регистрация → ◇ T8 volume-rename+restart-поля → ⊕ T9 самоверификация -->
# region MODULE_CONTRACT
## @purpose  Волна B7 программы хардненинга (116): починить контракт модулей — make-таргеты (restore без рецепта, restart=recreate вопреки документации), backup-параметризация, nginx-конфиги (прод-дефолт уходил в dev-mode без TLS), pyproject-зависимости, регистрация monitoring_config_renderer.
## @scope    U-25, U-46, U-50, U-61, U-62, U-65. Файлы: core/templates/module.mk, core/Makefile.common, core/modules/*/Makefile (14), core/modules/nginx/{config,dev-config,docker-compose.base.yml,docker-compose.dev.yml,Makefile}, core/platform-infra.yaml, core/internal/scripts/sync_env_defaults.py, .env.example, .env, platform-env.yaml, pyproject.toml, core/templates/template-manifest.yaml, core/entrypoint-manifest.yaml, core/AGENTS.md, AGENTS.md (root), core/modules/AGENTS.md, core/schemas/module.schema.json (без изменений — поле есть), core/modules/{postgres,backup-cron,hermes-agent,clickhouse,redis,status-page}/module.yaml, tests/gates/test_gate_imports.py, tests/unit/test_monitoring_config_renderer.py, tests/gates/test_gate_make_contract.py (перенесён из tests/unit/, DRIFT-TRINITY фикс), Makefile (root).
## @invariants
##   1. module.mk — единственный источник make-контракта модуля; документация (AGENTS.md) не расходится с кодом (инвариант брифа).
##   2. Каждый .PHONY-таргет имеет рецепт — 0 пустых .PHONY (устранение тихого no-op U-25).
##   3. Один механизм шаблонизации на директорию (правило template-механизмов) — nginx config/ и dev-config/ не смешивают envsubst-механизмы.
##   4. Любое удаление файла/рецепта — consumer-scan (rg: код + тесты + CI + манифест + sudo-whitelist) → удаление консервирующих тестов → зелёный gate (инвариант программы).
##   5. state_machine.py НЕ трогается (мораторий B9).
##   6. Python-first: новые проверки волны — pytest-гейты (trinity: tests/gates/ + @pytest.mark.gate + entrypoint-manifest gates), не shell-скрипты.
## @rationale Бриф фиксирует цели; DevPlan фиксирует решения пользователя (D1-D5, 2026-08-01) и исполнительные шаги с точными файлами, чтобы Coder работал без архитектурных развилок. Consumer-scan выявил: NGINX_CONF_DIR дефолтится в ./dev-config по всему стеку (platform-infra.yaml:220 + sync_env_defaults.py:414 + .env + .env.example) — прод-дефолт = HTTP-only dev-режим без TLS; root Makefile НЕ имеет backup/restore-таргетов (глоссарий «root = оркестрация» — декларативен); Makefile.common включается только через module.mk (core/templates/module.mk:60).
## @changes 2026-08-01 · Решения пользователя (question 2026-08-01): (D1) backup/restore — сужение контракта: только stateful-модули (postgres, backup-cron, hermes-agent); stateless — таргеты не объявляются, .PHONY без рецепта удаляется; (D2) restart: stop = compose stop (не down!), down — отдельный реальный таргет, restart: stop start (soft), restart-hard: down && up -d --force-recreate; (D3) nginx: NGINX_CONF_DIR default → ./config во всех SoT, dev-config сжимается до dev-отличающихся файлов, dev-режим — явный opt-in через docker-compose.dev.yml; (D4) pyproject: httpx → runtime, requests/python-dotenv → [dev], новый AST-гейт test_gate_imports; (D5) renderer: регистрация make render-monitoring + entrypoint-manifest + глоссарий, pass-тесты чистятся ЗДЕСЬ (R1-чистка в той же волне, инвариант программы).
# endregion MODULE_CONTRACT

$START_DEVPLAN
$ARTIFACT_CONTRACT:
  PURPOSE: Реализация волны B7 — 9 задач от restart-семантики до гейтов самоверификации (make-контракт dry-run, import-гейт).
  DESCRIPTION: Приведение make-контракта модулей к реальности: stop≠down + soft restart, backup/restore только для stateful-модулей (BACKUP_MODE/BACKUP_SOURCE_FILE), nginx prod-дефолт config/ + dev-оверрайд, pyproject-чистка + AST-гейт импортов, регистрация monitoring_config_renderer, канонизация volume-rename и restart-полей module.yaml.
  RATIONALE: U-25: .PHONY restore без рецепта = тихий no-op в 11 модулях; restart = recreate вопреки AGENTS.md:167; U-46: прод-дефолт NGINX_CONF_DIR=./dev-config уводит ноду в HTTP-only без TLS; U-50: httpx импортируется без декларации; U-61: hermes-specific state.json в generic-шаблоне; U-65: живой renderer вне манифеста; U-62: volume-rename скопирован в 5 модулей без канона.
  ACCEPTANCE_CRITERIA: (1) restore реализован для stateful (postgres, backup-cron, hermes-agent) ИЛИ контракт сужен — таргеты отсутствуют у stateless, 0 пустых .PHONY; (2) restart = soft (stop+start без пересоздания), restart-hard = --force-recreate, AGENTS.md:167 точна; (3) backup параметризован (BACKUP_SOURCE_FILE, state.json только у hermes-agent), WARNING-путь удалён; (4) nginx: NGINX_CONF_DIR default ./config во всех SoT, dev-config ≤ dev-отличающихся файлов, dev.yml — явный opt-in, 0 идентичных файлов config/dev-config; (5) pyproject: httpx в runtime, requests/python-dotenv в dev, test_gate_imports зелёный; (6) render-monitoring в Makefile + entrypoint-manifest + core/AGENTS.md + глоссарий; pass-тесты (assert True/pass) удалены, контрактный CLI-тест добавлен; (7) volume-rename канонизирован (TRAP[DECISION] + раздел в modules/AGENTS.md); module.yaml restart-поля заполнены в 6 модулях и проходят restart-drift валидатор.
  IMPLEMENTS: U-25 (restore/restart/backup + restart-поле), U-46 (nginx configs), U-50 (pyproject), U-61 (state.json), U-62 (volume-rename), U-65 (renderer wiring)
  IMPACTS: core/templates/module.mk, core/Makefile.common, core/modules/*/Makefile, core/modules/nginx/{config,dev-config,docker-compose.base.yml,docker-compose.dev.yml}, core/platform-infra.yaml, core/internal/scripts/sync_env_defaults.py, .env.example, .env, platform-env.yaml, pyproject.toml, core/templates/template-manifest.yaml, core/entrypoint-manifest.yaml, core/AGENTS.md, AGENTS.md, core/modules/AGENTS.md, Makefile, core/modules/*/module.yaml (6), tests/
  REQUIRES: 08-Brief (B7); решения пользователя 2026-08-01 (D1-D5); B4 (контракты exit-кодов), B5 (shared-политики — не затрагиваются), B8 (dead-code — renderer остаётся живым, фантомный гейт не блокирует); чистое рабочее дерево на старте (пользователь коммитит перед началом)
$END_ARTIFACT_CONTRACT

---

## 1. Решения пользователя (подтверждены 2026-08-01)

| D | Вопрос | Решение |
|---|--------|---------|
| D1 | backup/restore контракт (U-25/U-61) | **Сужение**: backup/restore только для stateful (postgres, backup-cron — custom-рецепты; hermes-agent — BACKUP_MODE=file). Stateless (nginx, status-page, infra-metrics, litellm, langfuse, logging, monitoring, redis, minio, clickhouse) — таргеты НЕ объявляются, пустые .PHONY удаляются. Глоссарий: «backup/restore — опциональные таргеты stateful-модулей». |
| D2 | restart-семантика (U-25) | stop = `compose stop --timeout $(STOP_TIMEOUT)` (default 30), down = реальный `compose down`, restart: stop start (soft, без пересоздания), restart-hard: down && up -d --force-recreate. AGENTS.md:167 становится правдой. Кастомные grace-оверрайды postgres (60s)/backup-cron (120s) работают через restart: stop start. |
| D3 | nginx config/dev-config (U-46) | NGINX_CONF_DIR default → ./config во всех SoT (platform-infra.yaml, sync_env_defaults.py, .env.example, .env); dev-config сжимается до dev-отличающихся файлов; dev-режим — явный opt-in через docker-compose.dev.yml (override поверх config/). |
| D4 | pyproject (U-50) | httpx → runtime deps; requests, python-dotenv → [dev]; новый AST-гейт test_gate_imports (сторонние импорты core/ ⊆ runtime deps). |
| D5 | renderer (U-65) | Регистрация: make render-monitoring PROJECT_DIR= PROJECT= [NODE=] → python3 renderer; entrypoint-manifest + core/AGENTS.md + глоссарий (новый глагол render-monitoring); pass-тесты (assert True/pass) чистятся ЗДЕСЬ, заменяются контрактным CLI-тестом. |

## 2. Текущее состояние worktree (старт волны)

- B8 (128807a) закоммичен; рабочее дерево чистое (пользователь коммитит перед стартом — `git status` без изменений на момент начала).
- `module.mk` (121 LOC): `.PHONY: ... restore` без рецепта (строка 62); stop = `compose down --timeout 30` (строка 74); backup хардкодит `docker cp :/app/state.json` + WARNING (113-116); restart унаследован из Makefile.common (restart: stop start → recreate).
- `Makefile.common` (26 LOC): stop = `compose down`; restart: stop start; нет down/restart-hard; включается только через module.mk:60.
- Модульные Makefile: postgres/backup-cron — custom stop/restart-hard/status/logs/backup/restore (дублируют template); hermes-agent/clickhouse/minio/redis/nginx/status-page/infra-metrics/litellm/langfuse/logging/monitoring — minimal include (получают битый backup/restore из шаблона).
- nginx: NGINX_CONF_DIR=./dev-config в platform-infra.yaml:220, sync_env_defaults.py:414, .env:109, .env.example:205, platform-env.yaml:187; config/ (11 файлов) vs dev-config (10 файлов) — все общие файлы различаются; docker-compose.base.yml:54-65 монтирует `${NGINX_CONF_DIR:-./config}/<file>`.
- pyproject: dependencies = boto3, cryptography, jinja2, jsonschema, pydantic, pyyaml, python-dotenv, requests; httpx НЕ объявлен, но импортируется admin_client.py:26.
- monitoring_config_renderer.py: 938 LOC, CLI: --project-dir/--project (required), --node (default ""); вызывается только из monitoring/hooks/on-project-deploy.sh; в манифесте — нет.
- test_monitoring_config_renderer.py: 28 тестов, из них ≥5 `assert True`/`pass` (R1-нарушение).
- module.yaml restart: 0/14 (схема и restart-drift валидатор готовы — B6 D5); 6 модулей документируют политику в комментариях.

## 3. Задачи

### T1 — U-25: module.mk — restart-семантика + параметризация backup/restore [FUNDAMENT]

**Файл:** `core/templates/module.mk`

**Шаги:**

1. **stop ≠ down.** Заменить рецепт stop (сейчас `compose down --timeout 30`) на `compose stop --timeout $(STOP_TIMEOUT)`:
   ```makefile
   STOP_TIMEOUT ?= 30
   stop: ## Stop $(MODULE_NAME) (compose stop — containers preserved)
   	$(COMPOSE_CMD) stop --timeout $(STOP_TIMEOUT)
   ```
2. **down — реальный таргет** (сейчас алиас stop):
   ```makefile
   down: ## Remove $(MODULE_NAME) containers (compose down)
   	$(COMPOSE_CMD) down --timeout $(STOP_TIMEOUT)
   ```
3. **restart — soft** (определяется ЯВНО в module.mk, перекрывает Makefile.common):
   ```makefile
   restart: stop start ## Soft restart (stop + start, containers preserved)
   ```
4. **restart-hard** — остаётся `down && up -d --force-recreate` (рецепт уже есть, строки 79-82).
5. **up** — остаётся `up -d --force-recreate` (строки 106-110, документированная семантика).
6. **backup/restore — параметризация** (замена хардкода строк 112-116):
   ```makefile
   # Backup/restore capability — опциональный контракт stateful-модулей (D1).
   # BACKUP_MODE: none (default) | file | custom
   #   file   — generic docker cp: BACKUP_SOURCE_FILE + RESTORE_FILE
   #   custom — модуль объявляет рецепты backup/restore ПОСЛЕ include
   BACKUP_MODE ?= none
   BACKUP_SOURCE_FILE ?=
   RESTORE_FILE ?=
   ifeq ($(BACKUP_MODE),file)
   .PHONY: backup restore
   backup: ## Trigger $(MODULE_NAME) state snapshot (docker cp)
   	@if [[ -z "$(BACKUP_SOURCE_FILE)" ]]; then \
   		echo "[IMP:9][$(MODULE_NAME)-mk][backup] ERROR: BACKUP_SOURCE_FILE not set" >&2; exit 1; fi
   	@mkdir -p "$(MODULE_DIR)/backups"
   	docker cp $(CONTAINER):$(BACKUP_SOURCE_FILE) \
   		"$(MODULE_DIR)/backups/state-$$(date +%Y%m%d-%H%M%S).json"
   	@echo "[IMP:9][$(MODULE_NAME)-mk][backup] snapshot saved"
   restore: ## Restore state snapshot (RESTORE_FILE=<path>) + soft restart
   	@if [[ -z "$(RESTORE_FILE)" || -z "$(BACKUP_SOURCE_FILE)" ]]; then \
   		echo "[IMP:9][$(MODULE_NAME)-mk][restore] ERROR: RESTORE_FILE and BACKUP_SOURCE_FILE required" >&2; exit 1; fi
   	@if [[ ! -f "$(RESTORE_FILE)" ]]; then \
   		echo "[IMP:9][$(MODULE_NAME)-mk][restore] ERROR: RESTORE_FILE not found: $(RESTORE_FILE)" >&2; exit 1; fi
   	docker cp "$(RESTORE_FILE)" $(CONTAINER):$(BACKUP_SOURCE_FILE)
   	$(COMPOSE_CMD) restart
   	@echo "[IMP:8][$(MODULE_NAME)-mk][restore] state restored from $(RESTORE_FILE)"
   else ifeq ($(BACKUP_MODE),custom)
   .PHONY: backup restore
   endif
   ```
7. **.PHONY сужается**: строка 62 → `start stop restart restart-hard status logs build up down help`; backup/restore — только в условных блоках (п.6). Условная .PHONY обязательна: иначе `make -n restore` на stateless-модуле = тихий no-op (возврат U-25).
8. **Обновить MODULE_CONTRACT-документацию шаблона** (@invariants): stop/down/restart-семантика, BACKUP_MODE-контракт, ссылка на канон volume-rename (T8).

**Критерий:** `make -n stop/restart/restart-hard/down` из любого модуля — не падает; для stateless-модуля `make restore` → «No rule to make target» (ожидаемое поведение, не тихий no-op).

### T2 — U-25: Makefile.common — гармонизация [FUNDAMENT]

**Файл:** `core/Makefile.common`

**Шаги:**

1. stop → `compose stop` (не down); down → отдельный таргет `compose down`.
2. restart: stop start (уже так — становится корректным после п.1); добавить `restart-hard: down && up -d --force-recreate`.
3. .PHONY: `start stop restart restart-hard status logs build up down`.
4. Обновить заголовочный комментарий (Usage).

**Критерий:** Makefile.common не противоречит module.mk ни по одному таргету (проверка T9).

### T3 — U-25: Модульные Makefile — stateful custom / stateless minimal [FUNDAMENT]

**Файлы:** `core/modules/{postgres,backup-cron,hermes-agent,clickhouse,minio,redis,nginx,status-page,infra-metrics,litellm,langfuse,logging,monitoring}/Makefile` (13 docker-модулей; platform-secrets — module-system.mk, вне скоупа)

**Шаги:**

1. **postgres** (`BACKUP_MODE=custom`): добавить `BACKUP_MODE := custom`; удалить дублирующие рецепты, которые теперь даёт module.mk с тем же поведением: restart-hard (идентичен шаблону), status, logs (compose logs -f --tail=100 ≈ шаблону — шаблон тоже --tail 100 -f). Оставить только реально кастомные: start, stop (60s grace), backup (pg_dumpall), restore (psql, DUMP_FILE). TRAP[DECISION] 2026-07-18 обновить (Rev-условие «configurable stop_timeout» — выполнено STOP_TIMEOUT).
2. **backup-cron** (`BACKUP_MODE=custom`): аналогично — оставить start, stop (120s grace), backup (docker exec), restore (делегирует postgres). TRAP обновить.
3. **hermes-agent** (`BACKUP_MODE=file`): добавить `BACKUP_MODE := file` + `BACKUP_SOURCE_FILE := /app/state.json` (U-61). Удалить пустой restart-hard/status/logs-дубли, если есть (сейчас minimal — только добавить 2 строки).
4. **stateless (clickhouse, minio, redis, nginx, status-page, infra-metrics, litellm, langfuse, logging, monitoring)**: остаются minimal include; удалить дублирующие рецепты, если есть (redis — проверить кастомный start с PONG-ридингом: если реальная логика — оставить, но это не про backup/restore). Никаких BACKUP_MODE — контракт сужен (D1). Проверить отсутствие кастомных backup/restore-рецептов (consumer-scan: rg `^backup:|^restore:` по модулям).
5. **module.mk-контракт в шапках**: обновить @invariants-комментарии, где упоминается «backup target: triggers state snapshot via HTTP POST» (устаревшее описание) — проверить фактическое содержимое.

**Критерий:** ровно 3 модуля декларируют backup/restore (postgres, backup-cron, hermes-agent); 0 пустых .PHONY; каждый кастомный рецепт существует по делу (consumer-scan).

### T4 — U-25: Документация — modules/AGENTS.md + root-глоссарий + core/AGENTS.md [FUNDAMENT]

**Файлы:** `core/modules/AGENTS.md`, `AGENTS.md` (root, глоссарий), `core/AGENTS.md` (canon_table — если правки глоссария триггерят регенерацию)

**Шаги:**

1. **core/modules/AGENTS.md:167** — «Makefile-контракт (Docker-модули)»:
   - Точная семантика: `stop` = compose stop (контейнеры сохраняются), `down` = compose down (удаление контейнеров), `restart` = stop + start (soft, БЕЗ пересоздания — сохраняет сеть, монтирования, состояние), `restart-hard` = down + up -d --force-recreate.
   - backup/restore: «опциональные таргеты stateful-модулей»; матрица: postgres (pg_dumpall/psql), backup-cron (exec/делегация), hermes-agent (state.json, BACKUP_SOURCE_FILE); остальные модули НЕ объявляют backup/restore (контракт сужен, D1).
   - Канон volume-rename (T8): «test-оверрайды переименовывают volumes через суффикс -test — compose deep-merge не умеет удалять ключи; паттерн канонический».
2. **root AGENTS.md глоссарий** — строки про `restart`/`restart-hard`/`backup`/`restore`:
   - `restart` — «Soft restart (stop + start, без пересоздания контейнеров). Root = оркестрация стека, module = один модуль»;
   - `restart-hard` — «Hard restart c --force-recreate (module-level target only — нет root Makefile target)» (уже так);
   - `backup`/`restore` — добавить пометку «stateful-модули (postgres, backup-cron, hermes-agent); остальные модули не объявляют»;
   - новый глагол `render-monitoring` (T7) — строка в таблицу глаголов.
3. **core/AGENTS.md** — canon_table: `make restart` описание (уже «Мягкий перезапуск... stop && start» — сверить), `make backup`/`make restore` — пометка stateful. Файл генерируется (generate_agents_md.py) — правки вносить в генератор/манифест, а не вручную (invariant 11: generated files не редактируются).

**Критерий:** AGENTS.md:167 не врёт (сверка с T1); глоссарий содержит render-monitoring; `make generate-agents-md` не даёт диффа после правок источника.

### T5 — U-46: nginx — прод-дефолт config/ + dev-оверрайд [CRITICAL]

**Файлы:** `core/platform-infra.yaml`, `core/internal/scripts/sync_env_defaults.py`, `.env.example`, `.env`, `platform-env.yaml` (generated), `core/modules/nginx/{dev-config/*,config/*,docker-compose.dev.yml,docker-compose.base.yml,Makefile}`, `core/templates/template-manifest.yaml`

**Шаги:**

1. **SoT default → ./config**:
   - `core/platform-infra.yaml:220`: `NGINX_CONF_DIR: "./config"` (SoT, инвариант B2-паритета);
   - `core/internal/scripts/sync_env_defaults.py:414`: default `"./config"` (там же NGINX_CERT_DIR — оставить ./dev-certs? Проверить семантику: NGINX_CERT_DIR=./dev-certs при config/ (prod-TLS) — вероятно надо ./certs; consumer-scan: кто читает NGINX_CERT_DIR — см. п.4).
   - Регенерация: `make generate-platform-env` → platform-env.yaml; `make generate-env-example` → .env.example; `.env` — правка строки 109 вручную (локальный файл, gitignored-копия .env.example).
2. **dev-config → dev-оверрайды**: оставить в dev-config ТОЛЬКО файлы, реально отличающиеся от config/ или уникальные для dev: nginx.conf (dev-вариант), ssl-dev.conf, dev-варианты vhost'ов (grafana, hermes-dashboard, langfuse, loki, prometheus), platform-http.conf, platform-default.conf.template — по факту диффов (consumer-scan по каждому файлу: если содержимое идентично config/ → удалить из dev-config). Удалить из dev-config файлы, идентичные config/ (security-headers.conf, если идентичен — проверить diff).
3. **docker-compose.dev.yml** (новый, в core/modules/nginx/): compose-оверрайд dev-режима — монтирует dev-config-файлы ПОВЕРХ config/:
   ```yaml
   # dev-mode: docker compose -f docker-compose.base.yml -f docker-compose.dev.yml up
   services:
     nginx:
       volumes: ...  # переопределить ТОЛЬКО dev-отличающиеся файлы (dev-config/<file> → /etc/nginx/...)
   ```
   Правило: каждый файл dev-config обязан быть смонтирован в dev.yml; файлы config/, не переопределённые в dev.yml, монтируются base.yml автоматически.
4. **Consumer-scan NGINX_CERT_DIR/NGINX_CONF_DIR**: rg по node-configs/, overlays/, provisioner.py, docker-compose*.yml, smoke-тестам — кто ещё задаёт NGINX_CONF_DIR/NGINX_CERT_DIR. Если прод-ноды задают NGINX_CERT_DIR=./dev-certs — исправить на ./certs (или что там в node.yaml). Если NGINX_CONF_DIR переопределяется где-то ещё — привести к ./config.
5. **template-manifest.yaml** (строки 135-149): обновить комментарий про dev-режим («dev-режим NGINX_CONF_DIR=./dev-config» → «dev-режим: docker-compose.dev.yml») + проверить регистрацию dev-config файлов (TRAP 2026-07-31: зарегистрирован только реальный platform-default.conf.template; после сжатия dev-config — обновить список, гейт template_manifest_coverage не должен RED).
6. **nginx/Makefile**: @invariants-комментарий про NGINX_CONF_DIR (строка 9) — уточнить: default ./config, dev — docker-compose.dev.yml.

**Критерий:** 0 упоминаний `./dev-config` как дефолта в SoT/генерируемых; dev-config не содержит файлов, идентичных config/ (проверка T9); `docker compose -f docker-compose.base.yml -f docker-compose.dev.yml config` — валиден (dry-run).

### T6 — U-50: pyproject + AST-гейт импортов [FUNDAMENT]

**Файлы:** `pyproject.toml`, `tests/gates/test_gate_imports.py` (новый), `core/entrypoint-manifest.yaml` (gates-секция)

**Шаги:**

1. **pyproject.toml**:
   - dependencies: добавить `"httpx>=0.27.0"` (мин. версия под Python 3.10, consumer-scan: admin_client.py использует httpx.Client/AsyncClient — совместимо);
   - перенести `"requests>=2.31.0"` и `"python-dotenv>=1.0.0"` из dependencies в [project.optional-dependencies] dev (0 runtime-импортов в core/ — подтверждено rg; тесты используют load_dotenv — dev-extra покрывает).
2. **test_gate_imports.py** (trinity: файл в tests/gates/ + @pytest.mark.gate + запись в entrypoint-manifest.yaml gates):
   - AST-сканер: собрать все сторонние импорты (import X / from X import, X без точки в начале — не relative) из `core/**/*.py`;
   - сверка с pyproject runtime dependencies (парсинг `[project].dependencies` через tomllib);
   - allowlist-константа в тесте (паттерн B2/B4/B5): исключения (stdlib — не сканируются; dev-инструменты внутри тестов не в core/);
   - negative-тест: подставить фиктивный импорт → RED (R5 anti-survivorship).
3. **entrypoint-manifest.yaml**: gates-секция — запись `test_imports_covered_by_pyproject` → `test_gate_imports.py` (генерируется автодискавери или вручную — следовать механике существующих gates-записей).

**Критерий:** `make gate MODE=fast` зелёный; `pytest tests/gates/test_gate_imports.py -m gate` зелёный; negative-тест RED при подстановке необъявленного импорта.

### T7 — U-65: Регистрация monitoring_config_renderer [FUNDAMENT]

**Файлы:** `Makefile` (root), `core/entrypoint-manifest.yaml`, `core/AGENTS.md` (canon_table — через генератор), `AGENTS.md` (root, глоссарий), `tests/unit/test_monitoring_config_renderer.py`, `tests/unit/test_render_monitoring_cli.py` (новый)

**Шаги:**

1. **make render-monitoring** (прецедент generate-litellm-config — Makefile зовёт python3 напрямую):
   ```makefile
   ## render-monitoring: Рендер конфигурации мониторинга после деплоя проекта
   render-monitoring:
   	python3 core/internal/monitoring_config_renderer.py \
   		--project-dir "$(PROJECT_DIR)" --project "$(PROJECT)" \
   		$(if $(NODE),--node "$(NODE)",)
   ```
   **Цепочка регистрации (задокументировано QA-верификацией B7, DRIFT-MANIFEST):** таргет определён в `makefiles/manifest.mk` (строки 87-94), который include'ится из корневого `Makefile` (`include makefiles/manifest.mk`, корневой Makefile строка 50) — НЕ напрямую в корневом Makefile. Функционально эквивалентно: `make render-monitoring` работает как корневой таргет, генератор entrypoint-manifest (`extract_phony_targets` → `gmake -np --dry-run` из корневого Makefile) обрабатывает include-цепочку и подхватывает `render-monitoring` из `.PHONY` (makefiles/manifest.mk:25). Решение принято: таргет оставлен в manifest.mk — это существующий канон платформы (DevPlan 090: корневой Makefile <150 строк, все генераторы живут в makefiles/*.mk); корневой Makefile НЕ трогается (манифест-интегрити гейты зелёные — `test_module_targets_in_manifest` и `test_agents_md_synced_with_manifest` проверяют манифест↔AGENTS.md, а не физическое расположение таргета).
   Сигнатура: `make render-monitoring PROJECT_DIR=<dir> PROJECT=<name> [NODE=<node>]`; отсутствие PROJECT_DIR/PROJECT → argparse fail (exit 1, fail-fast).
2. **entrypoint-manifest.yaml**: новая секция (verb `render-monitoring`): make_target, delegates_to: `python3 core/internal/monitoring_config_renderer.py`, signature, operation_ru, description (U-65 — «жив через module-hook, вне manifest» закрывается); allowed_verbs + core/AGENTS.md canon_table — через `make generate-entrypoint-manifest` + `make generate-agents-md` (generated files не правятся руками).
3. **Глоссарий (root AGENTS.md)**: строка `render-monitoring` в таблицу глаголов (✅).
4. **R1-чистка тестов** (D5): tests/unit/test_monitoring_config_renderer.py — удалить тесты с `assert True`/`pass` (проверить каждый: если тест не фальсифицируем — удалить; если реально проверяет поведение — оставить); консервирующие (grep-ассерты на исходники — проверить).
5. **Контрактный CLI-тест** tests/unit/test_render_monitoring_cli.py: `main()` c --project-dir/--project (tmp_path-фикстура, без сервера — правило UI-тестов: вызов функций напрямую): exit 0 на валидном конфиге, exit 1 (argparse) на отсутствии аргументов — через вызов _build_arg_parser/parse или subprocess? НЕТ subprocess для бизнес-логики (правило testing.md) — тестировать main() с monkeypatch sys.argv.
6. **Hook остаётся** (on-project-deploy.sh — тонкий фасад, жив; регистрация в module.yaml hooks уже есть — гейт test_gate_module_hooks не RED).

**Критерий:** render-monitoring в манифесте + core/AGENTS.md (regen без диффа); глоссарий содержит глагол; 0 assert True/pass в тестах рендерера; CLI-тест зелёный.

### T8 — U-62: Канонизация volume-rename + restart-поля module.yaml [FUNDAMENT]

**Файлы:** `core/modules/AGENTS.md`, `core/templates/module.mk` (комментарий), `core/modules/{postgres,backup-cron,hermes-agent,clickhouse,redis,status-page}/module.yaml`

**Шаги:**

1. **volume-rename канон** (U-62): в core/modules/AGENTS.md «Makefile-контракт» + TRAP[DECISION]:
   - Паттерн: test-оверрайды (docker-compose.test.yml) НЕ переопределяют volume in-place (compose deep-merge не удаляет ключи: driver_opts/bind-mount сохраняются), а объявляют новый volume с суффиксом `-test` и перепривязывают сервис;
   - Канон: `postgres-data-test` (postgres:20-28), backup-spool-test/backup-logs-test (backup-cron:38-39), clickhouse:48, hermes-agent:52;
   - Отклонено: override-механизм (deep-merge не может удалить ключ volume — нет механизма);
   - Rev-дата: 2026-10-21 (вместе с TRAP-ревью программы).
2. **module.yaml restart-поля** (U-25 «restart 0/14»): заполнить в 6 модулях согласно документированным политикам (комментарии уже есть):
   - postgres: `restart: always`; redis: `restart: always`; backup-cron: `restart: always`; clickhouse: `restart: unless-stopped`; status-page: `restart: unless-stopped`.
   - **hermes-agent: `restart: unless-stopped` (ОТКЛОНЕНИЕ от первоначального `restart: always`, задокументировано QA-верификацией B7, DRIFT-HERMES-RESTART).** Consumer-scan docker-compose.base.yml:93 показал `restart: unless-stopped` (per Hermes recommendation — allows controlled stop). Coder согласовал module.yaml с compose ground-truth (module.yaml:35, комментарий объясняет deviation). Компромисс: hermes-agent не `severity: critical` → restart-drift валидатор зелёный (module.yaml = compose, carve-out не требуется). `restart: unless-stopped` ≠ `always`: контейнер НЕ перезапускается только если остановлен вручную оператором (docker stop) — при crash/host-reboot перезапуск идентичен `always`.
   - Валидация: `make validate-modules` (restart-drift: module.yaml.restart ↔ docker-compose.base.yml per-service restart; carve-out severity:critical). Перед заполнением — consumer-scan restart-значений в docker-compose.base.yml этих модулей: если compose говорит иначе — согласовать (или комментарий, или правка compose; критичные модули — carve-out всегда OK).
3. **module.mk-комментарий** (T1 п.8): ссылка на канон volume-rename для test-оверрайдов.

**Критерий:** `make validate-modules` зелёный (0 restart-drift); TRAP[DECISION] volume-rename в AGENTS.md; 6 module.yaml с restart.

### T9 — Самоверификация волны (порядок) [GATE]

**Файлы:** `tests/gates/test_gate_make_contract.py` (новый gate-тест — перенесён из tests/unit/, DRIFT-TRINITY), `core/entrypoint-manifest.yaml` (gates-запись), `core/modules/nginx/docker-compose.base.yml`+`dev.yml` (dry-run)

**Шаги (строго по порядку):**

1. **Регенерация манифестов**: `make generate-manifests` (entrypoint-manifest + platform-env + env-defaults + agents-md) → `git diff` — проверить, что изменения соответствуют T5/T7 (не ручная правка generated).
2. **Гейт make-контракта** (бриф «для каждого модуля make -n restore/restart/backup не падает» — адаптирован под D1): новый gate-тест `tests/gates/test_gate_make_contract.py` (@pytest.mark.gate):
   - для каждого docker-модуля: каждый таргет из `.PHONY` (парсинг make -qp) имеет рецепт — 0 пустых .PHONY (U-25 не возвращается);
   - dry-run: `make -n <target>` для всех .PHONY-таргетов всех 13 docker-модулей — exit 0 (без фактического docker);
   - backup/restore объявлены ровно у postgres, backup-cron, hermes-agent (матрица D1);
   - restart: рецепт содержит `stop start` (soft, не `down`) — семантическая проверка (AGENTS.md:167-контракт);
   - запись в entrypoint-manifest.yaml gates.
3. **Гейт nginx-паритета** (T5): проверка в test_make_contract.py или отдельно: 0 файлов dev-config с содержимым, идентичным config/ (dup-детекция U-46); `docker compose -f docker-compose.base.yml -f docker-compose.dev.yml config --quiet` — exit 0.
4. **Гейт импортов** (T6): `pytest tests/gates/test_gate_imports.py -m gate`.
5. **Гейт модульного манифеста** (бриф): `pytest tests/gates/test_gate_module_hooks.py -m gate` + manifest-integrity (render-monitoring зарегистрирован) — авто-дискавери.
6. **Валидация модулей**: `make validate-modules` (T8).
7. **Полный gate**: `make gate MODE=fast` — зелёный; `make test MARKER=static` — зелёный.
8. **Финальный consumer-scan**: rg по удалённым/изменённым именам (backup-рецепты, dev-config-файлы, пустые таргеты) — 0 висячих ссылок в CI/тестах/манифестах.

**Критерий:** все шаги зелёные; `git status` — только ожидаемые файлы волны.

## 4. Риски и решения

| Риск | Митигация |
|------|-----------|
| Смена stop с down на compose stop меняет поведение операторов (контейнеры не удаляются) | Документируется в AGENTS.md + глоссарий; sudo-whitelist не зависит от реализации (разрешены имена таргетов); root `make down` не затронут (отдельный таргет). |
| restart=soft не подхватывает новые конфиги (нужен restart-hard) | Явная семантика в AGENTS.md:167: restart — состояние, restart-hard — конфиги/образы. Потребители (node-update, converge) используют свои механизмы, не module restart (проверить rg в T9). |
| dev-config сжатие ломает dev-режим (недостающий файл → mount fail) | docker-compose.dev.yml перечисляет ВСЕ dev-файлы явно; base.yml default ./config остаётся полным; dry-run п.3 T9. |
| platform-env.yaml/.env.example регенерация разъезжается с .env (локальный) | .env — локальный gitignored-файл; правка строки 109 вручную в той же волне. |
| Gейт импортов зафейлит существующие необъявленные импорты | Consumer-scan до гейта: полный список сторонних импортов core/ сверяется с pyproject; необъявленные — либо добавляются в runtime (если runtime-использование), либо в allowlist с обоснованием. |
| restart-drift валидатор заблокирует module.yaml restart-поля | Согласование restart с docker-compose.base.yml до заполнения (T8 п.2); severity:critical carve-out уже есть. |
| render-monitoring без аргументов = неудобный make-таргет | Fail-fast через argparse (exit 1) + сигнатура в манифесте; hook остаётся основным runtime-путём. |

## 5. Критерии завершения волны (AC брифа 08-Brief)

- [ ] (1) restore реализован для postgres/backup-cron/hermes-agent; stateless — таргетов нет; 0 пустых .PHONY (test_make_contract).
- [ ] (2) restart = soft (stop+start), restart-hard = --force-recreate; AGENTS.md:167 и глоссарий точны (T4).
- [ ] (3) backup параметризован: BACKUP_SOURCE_FILE, state.json только у hermes-agent; WARNING-путь удалён (T1/T3).
- [ ] (4) nginx: NGINX_CONF_DIR default ./config во всех SoT + регенераты; dev-config ≤ dev-файлов, 0 дублей с config/; dev-режим = docker-compose.dev.yml (T5).
- [ ] (5) pyproject: httpx в runtime, requests/python-dotenv в dev; test_gate_imports зелёный (T6).
- [ ] (6) render-monitoring в Makefile + entrypoint-manifest + core/AGENTS.md + глоссарий; pass-тесты удалены; CLI-тест добавлен (T7).
- [ ] (7) volume-rename канонизирован (TRAP[DECISION]); module.yaml restart — 6 модулей, validate-modules зелёный (T8).
- [ ] Гейт волны: make-контракт dry-run + nginx-паритет + imports + module-hooks + `make gate MODE=fast` зелёный (T9).
- [ ] `make fix-gate && git add -u` выполнен перед коммитом (CI pre-flight, .kilo/rules/_project.md).

$END_DEVPLAN
