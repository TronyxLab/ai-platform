# 02-DevPlan.md — RC-верификация волн 116-120

<!-- GREP_SUMMARY: rc-verification, drift-audit, behavior-diff, e2e, prod-deploy, debt, 121, fbe306d -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ 5 фаз (drift/diff/e2e/prod/debt) → ⊕ Problem Registry (10) → ⊕ поведенческие изменения (6 канонов) → ⎋ ночные команды -->

# region MODULE_CONTRACT
## @purpose  RC-верификация платформы ai-platform перед первым деплоем после пересоздания VPS: подтвердить, что волны 116-120 (Strangler-Fig, SoT-унификация, check-suite) не внесли поведенческих регрессий, и дать ночной сессии точный маршрут: drift-аудит → diff → e2e → прод → долги.
## @scope    5 фаз: (1) drift-аудит, (2) поведенческий diff vs RC-якорь fbe306d, (3) e2e на test-e2e (103.88.243.151), (4) прод-деплой, (5) долги 118/119. НЕ включает фиксы — найденные дрейфы регистрируются в Problem Registry без исправления.
## @invariants
##   1. Read-only подготовка: core-код НЕ изменяется, make check/gate НЕ запускаются (ночная сессия).
##   2. RC-якорь поведения — коммит fbe306d (114, 2026-07-31). Behavior diff = fbe306d..HEAD.
##   3. Реестр глаголов: HEAD = 69 make_target (якорь 64). Волны 119/120 добавляли 0 новых.
##   4. e2e-канал: NODE=test-e2e → node-configs/test-e2e/node.yaml (host 103.88.243.151, owner_key оператора).
##   5. AGE-ключ: ~/.ssh/age-key-personal.txt — дефолт цепочки node_detect (инвариант 2 AGENTS.md); SSH_KEY=~/.ssh/id_ed25519.
## @rationale Q: Зачем RC-верификация до деплоя? A: VPS пересоздан (инвариант 9 — обратная совместимость не требуется), между якорем fbe306d и HEAD — 68 коммитов (36 feat) за 3 дня, включая распил монолитов (119 E1-E9), SoT-унификацию (119 B1-B8) и новую систему проверок (120). Ошибка после деплоя на пересозданной ноде стоит полного bootstrap-цикла (~10-30 мин + риск ACME-рейт-лимитов).
## @changes 2026-08-03 | Создан подготовительной сессией (read-only): diff-данные, drift-скан, e2e-окружение, очистка worktrees.
# endregion MODULE_CONTRACT

---

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | RC-верификация волн 116-120 перед первым деплоем после пересоздания VPS |
| **DESCRIPTION** | 5 фаз — drift-аудит, поведенческий diff, e2e, прод-деплой, долги |
| **RATIONALE** | 68 коммитов (36 feat) поверх RC-якоря fbe306d за 3 дня; пересозданный VPS не имеет обратной совместимости; ошибка = полный bootstrap-цикл |
| **ACCEPTANCE_CRITERIA** | 1) Problem Registry верифицирован ночной сессией (CRITICAL/HIGH закрыты или эскалированы); 2) поведенческий diff подтверждён e2e на test-e2e; 3) прод-деплой выполнен и healthcheck зелёный; 4) долги 118/119 закрыты или переоценены |
| **IMPLEMENTS** | 119 08/09-DevPlan остаток + долги 118/119 (watchdog C2, letsencrypt C6, Strangler closeout) |
| **IMPACTS** | core/, node-configs/, CI |
| **REQUIRES** | зелёный `make gate MODE=fast`, VPS 103.88.243.151, AGE-ключ `~/.ssh/age-key-personal.txt` |

---

## Фаза 1 — Drift-аудит (12 измерений, read-only)

**Выполнено подготовительной сессией:** первичный скан (4 параллельных агента) — см. Problem Registry ниже. Ночная сессия верифицирует открытые пункты и коллапсы.

| # | Проверка | Статус | Примечание |
|---|----------|--------|------------|
| 1.1 | DRIFT-1/2: дублирование знаний/логики (compose, образы, тайминги, shared/) | ⬜ | 5 находок в реестре; не верифицированы тела shared/docker_compose.py ↔ compose_files.py ↔ compose_profiles.py |
| 1.2 | DRIFT-3/10/11: README/ADR/инлайн-доки vs код | ⬜ | 8 находок в реестре; не прочитаны makefiles/manifest.mk, repair.mk, tests/e2e/README |
| 1.3 | DRIFT-4/5: контракты/тесты (импорты, skip, дубли) | ⬜ | Дубль test_yaml_query/test_unit_yaml_query; таймаут-литералы вне timeouts.py — проверить покрытие гейта |
| 1.4 | DRIFT-6/7/8/9: compose/CI/healthcheck/env | ⬜ | 2 LOW находки CI; healthcheck-канон единственный (healthcheck_poller.py) — подтверждён |
| 1.5 | DRIFT-12: правила (pre-commit vs CI vs lint) | ⬜ | Расхождений не выявлено в проверенной части; ruff-конфиг не сверен с v0.15.21 |
| 1.6 | Коллапсы суперпозиции (файл в >3 измерений) | ⬜ | Кандидат: core/entrypoint-manifest.yaml (сигнатуры vs код .mk) |
| 1.7 | Rev-даты TRAP: прошли ли (сегодня 2026-08-03) | ⬜ | Все датированные Rev в будущем (2026-10-21/22, 2027-02); условные — см. §Поведенческие изменения |

## Фаза 2 — Поведенческий diff vs RC-якорь fbe306d

**Выполнено подготовительной сессией** (данные в §Поведенческие изменения). Ночная сессия: подтвердить e2e-тестами, что каноны работают одинаково.

| # | Проверка | Статус | Примечание |
|---|----------|--------|------------|
| 2.1 | Реестр глаголов: 69, 0 незарегистрированных | ⬜ | Подтверждено: +5 за 116-118 (check, check-diff, check-domain-parity, check-exception-patterns, check-profiles-parity, preflight, render-monitoring; −2: audit, generate-manifests-atomic) |
| 2.2 | SSH_OPTS-канон: единственный источник флагов | ⬜ | shared/ssh_opts.py; lib/ssh.sh — фасад через python3 -m |
| 2.3 | Healthcheck-канон: running + (healthy\|""\|none) = здоров | ⬜ | healthcheck_poller.py; shell-примитив: unhealthy=1/fail, starting=2/wait |
| 2.4 | Timeout-каноны: shared/timeouts.py, 0 литералов в домене | ⬜ | Литералы найдены в check_suite/llm_provision/context_deployer/lifecycle — проверить покрытие гейта |
| 2.5 | deploy_paths: 6 канонических + резолверы (C7) | ⬜ | Подтверждён |
| 2.6 | atomic_writer: fsync+replace+validator, 10 генераторов | ⬜ | Подтверждён; json_writer исключён (bind-mount TRAP) |
| 2.7 | project_yaml: 0 grep + 0 yaml.safe_load вне модуля | ⬜ | Подтверждён (119 B1) |

## Фаза 3 — E2E на test-e2e

**Окружение подготовлено** (ШАГ 4): node.yaml на месте, ключи на месте. Ночная сессия выполняет:

| # | Команда | Статус | Примечание |
|---|---------|--------|------------|
| 3.1 | `export NODE=test-e2e; make test-node NODE=test-e2e` | ⬜ | 11 e2e-тестов; не входит в make gate |
| 3.2 | При фейле: `make test-node NODE=test-e2e -k "bootstrap_pipeline"` | ⬜ | Только happy-path (8 тестов) |
| 3.3 | Полный холодный bootstrap (если нода не задеплоена): `make bootstrap-node NODE=test-e2e` | ⬜ | ~10-30 мин; первый запуск |

**Окружение (подтверждено подготовительной сессией):**
- node-configs/test-e2e/node.yaml: host `103.88.243.151` ✓, owner_key = `ssh-ed25519 AAAAC3...DcsO+D Tronyx` (~/.ssh/id_ed25519.pub) ✓, modules=[], projects=[test-project] ✓, domain не задан (ACME-скип детерминирован) ✓
- AGE: `~/.ssh/age-key-personal.txt` существует (189 B, perms 600) ✓ — дефолт node_detect, инвариант 2
- SSH: `~/.ssh/id_ed25519` существует (444 B, perms 600) ✓
- Точная команда: `export NODE=test-e2e; make test-node NODE=test-e2e`

## Фаза 4 — Прод-деплой

| # | Шаг | Статус | Примечание |
|---|-----|--------|------------|
| 4.1 | `make gate MODE=fast` зелёный | ⬜ | Обязательное предусловие (REQUIRES) |
| 4.2 | `make check` до чистоты (WORKERS=6, кэш) | ⬜ | Если gate упал — check-цикл |
| 4.3 | Прод-деплой: `make deploy` / `make context-promote CONTEXT=<ctx>` | ⬜ | Выбрать канал по модели деплоя (инвариант 2) |
| 4.4 | Пост-деплой: `make healthcheck` + `make status` | ⬜ | Канон: running + (healthy\|""\|none) = здоров |
| 4.5 | Верификация CI-гейтов после деплоя | ⬜ | platform-gate-fast, push-gate, deploy-project |

## Фаза 5 — Долги 118/119

| # | Долг | Источник | Статус | Примечание |
|---|------|----------|--------|------------|
| 5.1 | C2 watchdog TRAP[DEBT] | 119 C2 | ⬜ | Зарегистрирован в debt-реестре 119 C |
| 5.2 | C6 letsencrypt nginx_harness TRAP[DEBT] | 119 C6 | ⬜ | Зарегистрирован |
| 5.3 | Strangler closeout (shell-исключения keep-решения) | 119 D8 | ⬜ | 9 записей с Rev-условиями — см. §Поведенческие изменения п.7 |
| 5.4 | D7 deploy.sh TRAP (Rev не выполнен — прод не верифицирован) | 119 D7 | ⬜ | **Снимается Фазой 4** — после прод-верификации |
| 5.5 | Новые долги из Problem Registry (если CRITICAL/HIGH) | 121 | ⬜ | Эскалация Архитектору |

---

## Problem Registry (находки drift-скана, БЕЗ фиксов)

Сводка: **10 находок** (0 CRITICAL, 1 HIGH, 7 MEDIUM, 2 LOW) + 2 кандидата на верификацию. Подготовительная сессия НЕ фиксила ничего.

### HIGH

**P-1. `make down` документирован как «docker compose down», код выполняет `down -v` (потеря volumes)**
- **Источник:** `makefiles/modules.mk:47` (`down -v`) → `core/AGENTS.md:69` и `core/entrypoint-manifest.yaml:561` («docker compose down»)
- **Последствия:** оператор/агент, доверяющий докам, при `make down` теряет volumes (данные). Контраст с `makefiles/scaffold.mk:8`, где «compose down without -v, no data loss» — эталон безопасной семантики.
- **Вероятность регрессии:** MEDIUM — поведение закреплено в коде, доки устарели в обе стороны
- **Дублированное знание:** семантика down (3 места: .mk, AGENTS.md, manifest)
- **Source of Truth:** `makefiles/modules.mk:47` (факт) — решение по канону за оператором
- **Severity:** HIGH
- **Режимы суперпозиции:** S1 (DRIFT-3+DRIFT-10), S2 (SRE), S3 (git log)

### MEDIUM

**P-2. LITELLM_HEALTH_URL — 3 расходящихся health-эндпоинта**
- **Источник:** `core/platform-infra.yaml:190` (`/health`) → `core/modules/litellm/docker-compose.base.yml:135` (`/health/readiness`) → `core/modules/litellm/docker-compose.test.yml:34` (`/health/liveliness`)
- **Последствия:** мониторинг проверяет `/health` (не реализован в модуле litellm), liveness — `/readiness`, test — `/liveliness`. Обновление роутинга LiteLLM молча ломает одну из проверок.
- **Source of Truth:** compose модуля (runtime-факт)
- **Вероятность регрессии:** MEDIUM

**P-3. STATUS_PAGE_PORT объявлен в SoT, но compose его не потребляет (hardcode 8080)**
- **Источник:** `core/platform-infra.yaml:233` → `core/modules/status-page/docker-compose.base.yml:70` (healthcheck hardcode `localhost:8080`)
- **Последствия:** смена порта через env — тихий no-op. Значение 8080 дублирует CADVISOR_PORT.
- **Вероятность регрессии:** MEDIUM

**P-4. CONTEXT_IMAGE — fallback `latest@sha` в compose против датированного тега в SoT**
- **Источник:** `core/modules/hermes-agent/docker-compose.base.yml:69` (`latest@sha256:dd36…`) → `core/platform-infra.yaml:146` (`v2026.7.1`)
- **Последствия:** без env-переменной деплой возьмёт скрытый второй пин образа.
- **Вероятность регрессии:** LOW-MEDIUM

**P-5. Дубль тестов: tests/test_yaml_query.py и tests/test_unit_yaml_query.py**
- **Источник:** `tests/test_yaml_query.py:20` ↔ `tests/test_unit_yaml_query.py` (оба тестируют core.internal.scripts.yaml_query)
- **Последствия:** расходящаяся эволюция, маскировка регрессий (R1-иллюзия зелёных тестов).
- **Вероятность регрессии:** HIGH (эволюция), фактический дрейф MEDIUM

**P-6. `make gate MODE=ci-docker` отсутствует в манифесте и сгенерированном core/AGENTS.md**
- **Источник:** `makefiles/ci.mk:136` (usage: MODE=fast|full|ci-docker) → `core/entrypoint-manifest.yaml:279` (signature: fast|full) и `core/AGENTS.md:44`
- **Последствия:** нарушение инварианта 11 (generated files) — агент не узнает о ci-docker; признак устаревшего генератора или ручной правки.
- **Вероятность регрессии:** LOW

**P-7. Сигнатуры `make restore`/`make backup`/`make up` в доке расходятся с кодом**
- **Источник:** `core/AGENTS.md:68,72,73` + `core/entrypoint-manifest.yaml:555,580-581,588` (backup [NODE=…], restore NODE=<n>, up [PROJECT=…]) → `makefiles/modules.mk:26-38,69-72,77-83` (up: MODULES-фильтр; backup: без переменных; restore: DUMP_FILE=<path>)
- **Последствия:** `make restore NODE=prod` падает с «DUMP_FILE not set»; `make backup NODE=…` молча игнорирует NODE; `make up PROJECT=…` не фильтрует.
- **Вероятность регрессии:** MEDIUM

### LOW

**P-8. GHCR_PUSH_TOKEN — разные дефолты между двумя SoT**
- **Источник:** `core/platform-infra.yaml:255` (`""`) → `core/secret-definitions.yaml:131` (`ci_default: "ci-ghcr-push-token"`)
- **Последствия:** при генераторе, читающем platform-infra как единственный SoT, CI-токен уйдёт в пустую строку.
- **Вероятность регрессии:** LOW

**P-9. SKIP_PRECOMMIT асимметрия: platform-test.yml ставит, push-gate.yml — нет**
- **Источник:** `.github/workflows/platform-test.yml:124` (SKIP_PRECOMMIT=1) → `.github/workflows/push-gate.yml:72` (без флага; pre-commit уже выполнен явно на :69)
- **Последствия:** ~15s двойной прогон pre-commit на каждый push не-main.
- **Вероятность регрессии:** LOW

**P-10. deploy-project.yml @changes обещает command_timeout 10m, ключа в шагах нет**
- **Источник:** `.github/workflows/deploy-project.yml:26` (заявка) → :140-155 (отсутствие)
- **Последствия:** искажённый аудит-трейл; фактический таймаут канала = DEPLOY_TIMEOUT=600 (timeouts.py:95).
- **Вероятность регрессии:** LOW

### Кандидаты на верификацию (не подтверждены полностью)

**P-11. Таймаут-литералы вне shared/timeouts.py (нарушение инварианта 1 timeouts.py?)**
- **Источник:** `core/internal/check_suite.py:526,598,1101,1111` (60/15/30/30), `llm_provision.py:62,79` (30/60), `context_deployer.py:806` (60), `context_overlay.py:183` (60), `lifecycle/state_store.py:248` (10), `secrets_manager.py:238,291` (30/30), `cli.py:525` (60), `phases/system.py:95` (600)
- **Примечание:** гейт `test_gate_timeout_literals.py` может покрывать только docker/ssh/healthcheck-домен — проверить скоуп гейта и легитимность каждого литерала. Severity: MEDIUM при подтверждении.

**P-12. Collapse-кандидат: core/entrypoint-manifest.yaml** (P-1, P-6, P-7 — 3 расхождения сигнатур/delegates_to vs код .mk)
- **Примечание:** если ночная сессия найдёт ещё одно расхождение манифеста → подтверждённый collapse (4 измерения) → CRITICAL по методологии drift-detection.

---

## Поведенческие изменения (fbe306d..HEAD, 68 коммитов / 36 feat)

### 1. Реестр глаголов: 64 → 69 (+5, все зарегистрированы)
Добавлены: `check`, `check-diff`, `check-domain-parity`, `check-exception-patterns`, `check-profiles-parity`, `preflight` (deprecated-алиас), `render-monitoring`. Удалены: `audit` (→ check), `generate-manifests-atomic`. Волны 119/120 — 0 новых глаголов. Все новые присутствуют в глоссарии root AGENTS.md и allowed_verbs — незарегистрированных нет.

### 2. SSH_OPTS — Python SoT (116 B5 T2, D1)
`core/internal/shared/ssh_opts.py` — ЕДИНСТВЕННОЕ определение `-o` флагов (BatchMode=yes, StrictHostKeyChecking=accept-new, ConnectTimeout=<SSH_CONNECT_TIMEOUT>, ServerAliveInterval=30, ServerAliveCountMax=10). Заменил 5 Python-копий (core_deliverer, overlay_deliverer, channels ×2, remote_executor). `lib/ssh.sh` — тонкий фасад через `python3 -m core.internal.shared.ssh_opts --shell`. ConnectTimeout берётся из timeouts.SSH_CONNECT_TIMEOUT (30). TRAP[DECISION] 2026-08-01: Rev при втором shell-потребителе флагов.

### 3. Healthcheck-канон (116 D5 B5)
Единый критерий: контейнер running AND (healthy|""|none) = здоров; unhealthy → ждать (стартовые гонки). Единственная Python-реализация — `core/internal/deploy/healthcheck_poller.py` (Docker-критерий делегирован в `shared/docker_compose.healthcheck_poll`, 20×3=60s окно). `lib/healthcheck.sh` — shell-фасад с тем же критерием (примитив: healthy=0, unhealthy=1/fail, starting=2/wait; поллер композирует). Заменил 5 расходящихся реализаций (ps-filter 60/3, wrapper 10/1, inspect 60/2, lib, poller 30). Гейт docker_sole_path enforce-ит. TRAP[DECISION] 2026-08-01: Rev при ином трактовании State.

### 4. Timeout-каноны (116 B5 T1, U-11; 117 D; 119 B7)
`core/internal/shared/timeouts.py` — единый реестр числовых timeout-значений docker/ssh/healthcheck-домена: up=180, pull=300, build=300, healthcheck-poll=60, ssh-connect=30, deploy=600, ssh-read=60, image-check=60, docker-cmd=10, docker-stop=30, rsync=600, watchdog=90/5/3/30, healthcheck-ports=[3000,4000,8000,8080,9000] (B6), CONVERGE_DOCKER_TIMEOUT=30, FILE_OP_TIMEOUT=15 (119 B7). Заменил 226 литералов. Гейт test_gate_timeout_literals.py enforce-ит (см. P-11).

### 5. deploy_paths (118 C7)
`core/internal/shared/deploy_paths.py` — канонический реестр путей доставки: CANONICAL_DEPLOY_PATHS (6 путей) + DEPRECATED_DEPLOY_PATHS (stub с removal-планом). Резолверы: projects_base (default /opt/projects, env-приоритет), letsencrypt_live (/etc/letsencrypt/live, 20 копий удалены), node_configs_remote (/opt/node-configs, 27 call sites, 119 B3), platform_remote_base (/opt/platform). Гейт test_gate_deploy_paths.py.

### 6. atomic_writer (119 E5)
`core/internal/shared/atomic_writer.py` — единый канон атомарной записи: tempfile (та же директория) + write + flush + fsync + chmod → validator(tmp)? → os.replace. Заменил 12+ копий os.replace/NamedTemporaryFile с разной семантикой (fsync/chmod/cleanup). 10 генераторов мигрированы (secrets_env_parser, docker_registry_auth, s3_ssl_cache, docker_daemon, sudoers_generator, lifecycle helpers, metrics cache, sync_env_defaults, template_engine, node_yaml._write_back). Исключён: healthcheck/metrics/json_writer.py (Docker bind-mount — os.replace создаёт новый inode → stale data, TRAP[DOCKER-BIND-MOUNT]).

### 7. project_yaml (118 E11 + 119 B1)
`core/internal/shared/project_yaml.py` — ЕДИНСТВЕННЫЙ читатель ai-platform.yaml: load_project_yaml + типизированные аксессоры (expose/domain/target_node/needs/llm/monitoring/name/type). Мигрированы 8 потребителей yaml.safe_load (vhost_renderer, vhost_configurator, conflict_checks, monitoring_config_renderer, project_registry, deploy_engine, generate_catalog, orchestrator). Инварианты: 0 grep + 0 yaml.safe_load ai-platform.yaml вне модуля; отсутствующий yaml → пустой dict; get_target_node(required=True) → ConfigValidationError.

### 8. Прочие поведенческие сдвиги 116-120
- **119 E1-E9:** распил монолитов (docker_orchestrator 1419→фазы+PHASES dispatch, receive→ReceiveFlow, lifecycle phases/{system,docker,secrets,certs}, preflight.py+first_deploy.py, orchestrator_metrics чистые функции) — канонические каналы сохранены, R5 negative-тесты на каждую экстракцию.
- **120:** check-suite (единый SoT core/check-suite.yaml, 155 строк, 3 hardcoded-списка устранены) + check_suite.py (fingerprint-кэш, xdist -n auto, diff-таргет). check-diff = pre-commit --files + ruff по diff + pytest изменённых. Нейминг: check-* (диагностика) + gate (арбитр), MODE=fast/full/ci-docker (решение пользователя 2026-08-02).
- **119 D1-D6:** shell→Python миграции (issue-cert → ssl_certs CLI, tor_setup.py, privoxy_config.py, module-interface.sh 206→26, hermes init.sh 157→8, healthcheck.sh deps → healthcheck_deps.py).
- **down -v семантика** — НЕ является изменением 116-120 (было в 114 тоже? проверить ночной сессией через git log -L) — приоритет P-1.

### TRAP[DECISION] с Rev-условиями (актуальны на 2026-08-03)

| TRAP | Дата | Rev-условие | Тип Rev |
|------|------|-------------|---------|
| Enforcement языковой политики (pre-commit, не CI gate) | 2026-07-21 | **2026-10-21** — пересмотр при >3 нарушениях | Дата в будущем |
| Enforcement-гейты с allowlist (116 T9) | 2026-07-31 | **2026-10-21** — пересмотр при false-positive блокировках | Дата в будущем |
| Decision Gate Python-First (метрики Strangler) | 2026-07-22 | **2026-10-22** — переоценка метрик после ≥2 нед на production | Дата в будущем |
| Shell-исключение: issue-cert.sh (119 D8) | 2026-08-02 | **2027-02** — после стабилизации acme.sh API ≥6 мес | Дата в будущем |
| Shell-исключение: deploy.sh (119 D7) | 2026-08-02 | После верификации brief A на production | **Условие — снимается Фазой 4** |
| SSH_OPTS Python SoT | 2026-08-01 | Второй shell-потребитель флагов | Условный |
| Healthcheck-канон | 2026-08-01 | Иное трактование State | Условный |
| L1 pushed ghcr.io | 2026-07-15 | L1 начнёт нести context-specific данные | Условный |
| Strangler-Fig | 2026-07-22 | Новый shell >500 LOC с inline python3 | Условный |
| Bootstrap deploy-context as step 18 | 2026-07-22 | Шаг >5 мин → async | Условный |
| lib/ssh.sh staging-gate | 2026-07-21 | CI-deploy стабильно <300s → 400s | Условный |
| yaml_read.sh keep | 2026-08-02 | 0 ссылок 90 дней → удалить | Условный |

Вывод: датированные Rev-условия в будущем (2026-10-21/22, 2027-02) — просроченных нет. Условные Rev требуют runtime-проверок (производительность, ссылки) — вне read-only скоупа.

---

## Окружение e2e (ШАГ 4, подтверждено)

- `node-configs/test-e2e/node.yaml`: host `103.88.243.151` ✓, owner_key = `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOY0cwBbb9jKQgCJ0qX6hKnRfvQwqaeHMhC3V4DcsO+D Tronyx` (соответствует ~/.ssh/id_ed25519.pub) ✓, modules=[], projects=[test-project] ✓, domain не задан (ACME-скип) ✓
- `~/.ssh/age-key-personal.txt`: существует (189 B, 600) ✓ — дефолт цепочки node_detect (инвариант 2 AGENTS.md), подтверждён tests/e2e/README.md:56
- `~/.ssh/id_ed25519`: существует (444 B, 600) ✓
- **Точная команда ночной сессии:**
  ```
  export NODE=test-e2e; make test-node NODE=test-e2e
  ```
- ⚠️ **Рабочее дерево грязное (12 незакоммиченных изменений, НЕ мои):** `M core/entrypoints/bootstrap.sh`, `M core/internal/shared/AGENTS.md`, `M core/internal/shared/node_detect.py`, `D core/internal/shared/node_yaml.py`, `M tests/e2e/README.md`, `M tests/gates/.test_counter.json`, `M tests/gates/test_gate_node_yaml_single_source.py`, `M tests/gates/test_gate_single_project_parser.py`, `M tests/test_bootstrap_auto.py`, `M tests/unit/test_node_detect.py`, `?? core/internal/shared/node_yaml/` (пакет). Похоже на незавершённую миграцию node_yaml файл→пакет (119/120). test-node будет исполнять РАБОЧЕЕ дерево, а не HEAD — ночная сессия должна либо закоммитить, либо зафиксировать состояние до e2e.

## Очистка (ШАГ 5, выполнено)

- 6 worktrees `.kilo/worktrees/117-brief-{c,d,e,f,g,h}` удалены (`git worktree remove --force`) — все ветки были merged в main, незакоммиченной работы не было (чистые статусы, у g только untracked .venv)
- Освобождено ≈3.4 ГБ (631+629+630+628+244+638 МБ)
- 6 веток `117-brief-{c,d,e,f,g,h}` удалены (`git branch -d` — все merged)
- Верификация: `git worktree list` → только main; `git branch | grep 117-brief` → 0

---

## Команды для ночной сессии

```bash
# 1. Предусловие (Фаза 4.1) — только после готовности фиксов P-1..P-7 по решению оператора
make gate MODE=fast
make check   # до чистоты, если gate упал

# 2. E2E (Фаза 3)
export NODE=test-e2e
make test-node NODE=test-e2e                     # все 11 тестов
# при фейле: make test-node NODE=test-e2e -k "bootstrap_pipeline"   # happy-path (8)
# при необход. холодном bootstrap: make bootstrap-node NODE=test-e2e  # ~10-30 мин, ТОЛЬКО если нода не задеплоена

# 3. Прод-деплой (Фаза 4)
make deploy                                       # или make context-promote CONTEXT=<ctx>
make healthcheck && make status

# 4. Верификация долгов (Фаза 5)
# - D7 deploy.sh TRAP снимается после успешного прод-деплоя
# - Решить P-1..P-12 из Problem Registry (эскалация Архитектору для CRITICAL/HIGH)

# 5. Пост-верификация дрейфа
# - P-6/P-7: обновить entrypoint-manifest.yaml (make generate-entrypoint-manifest) — НЕ вручную
# - P-12: перепроверить манифест на collapse (3+ расхождения = CRITICAL)
```
