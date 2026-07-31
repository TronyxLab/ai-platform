# 03-DevPlan — B2: Генераторный контур и паритет-гейты

<!-- GREP_SUMMARY: generators parity-gates COMPOSE_PROFILES MINIO_PORT PLATFORM_DOMAIN scan_compose_ports sync_env_defaults secrets-manifest template-manifest discover_modules -->
<!-- STRUCTURE: ┌решения архитектора┐ → ◇ T1 scan_compose_ports → ◇ T2 COMPOSE_PROFILES SoT → ◇ T3 PLATFORM_DOMAIN/env-цепочка → ◇ T4 secrets-парсеры → ◇ T5 generate-manifests → ◇ T6 template-manifest → ◇ T7 предикат → ◇ T8 комментарии → ◇ T9 parity-гейты → ⊕ T10 самоверификация -->
# region MODULE_CONTRACT
## @purpose  Волна B2 программы хардненинга (116): починить генераторную цепочку env/профилей/шаблонов и сделать расхождения значений структурно невозможными через parity-гейты с allowlist.
## @scope    U-01, U-02, U-16, U-17, U-33, U-43, U-44, U-47, U-59, U-68. Файлы: core/internal/scripts/*, core/internal/bootstrap/{discover_modules.py,deploy/*,lifecycle/*}, core/internal/scaffold/project_adopter.py, Makefile, makefiles/*, core/templates/template-manifest.yaml, core/platform-infra.yaml, core/secret-definitions.yaml, core/secrets-manifest.yaml, tests/gates/*, tests/unit/*.
## @invariants
##   1. Инвариант 11 (Manifest Generation Contract): генерируемые файлы коммитятся, но НЕ редактируются вручную; check-manifests — красный гейт на divergence.
##   2. Гейты с allowlist (решение 01-Brief §1): parity-гейты разрешают хардкод ТОЛЬКО в SoT и generated-файлах; всё остальное — RED.
##   3. Формат parity-гейтов (решение архитектора, подтверждено пользователем 2026-07-31): pytest-гейты по trinity (файл tests/gates/ + @pytest.mark.gate + entrypoint-manifest с repair-полями L1) + тонкие make-таргеты-обёртки.
##   4. «Один код» (решение пользователя 2026-07-31 по U-59): дублирование кода опаснее изоляции — предикат discover_modules сводится к одному месту; TRAP[DECISION] об изоляции отменяется.
##   5. Языковая политика: новый код — только Python; никаких inline python3 в shell; runtime-чтение SoT через существующий core/internal/scripts/yaml_query.py (dotted-ключи).
##   6. Consumer-scan обязателен при любом удалении кода (инвариант 2 программы): rg по потребителям + обновление консервирующих тестов.
##   7. Fail-fast вместо silent fallback: генераторы и парсеры НЕ содержат хардкод-фолбэков, расходящихся с SoT (устранение «gate зелёный, система врёт»).
## @rationale Два аудита сошлись: генераторная цепочка — лучший engineered-механизм, но имеет слепые зоны (хардкоды в генераторах, 3-8 ручных копий, fallback-значения) — эталонный источник дрейфа (U-01). Паритет-гейты делают копии структурно невозможными.
## @changes 2026-07-31 · Решения пользователя: (D1) pytest-гейты + make-обёртки; (D2) smoke.py читает platform-env.yaml runtime; (D3) предикат — один код, без изоляции. SoT PLATFORM_DOMAIN — platform-infra.yaml env_defaults (следствие инварианта 11, ломает и обновляет test_gate_env_example_drift::test_platform_domain_default).
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Реализация волны B2 — 10 задач от фикса scan_compose_ports до parity-гейтов с allowlist.
  DESCRIPTION: Пошаговый план с точными файлами/строками, критериями приёмки на каждую U-проблему, новыми и обновлёнными гейтами (trinity), порядком самоверификации.
  RATIONALE: Бриф фиксирует цели; DevPlan фиксирует решения архитектора и исполнительные шаги, чтобы Coder работал без архитектурных развилок.
  ACCEPTANCE_CRITERIA: (1) port_mappings.MINIO_PORT == env_defaults.MINIO_PORT == 9000; (2) один SoT COMPOSE_PROFILES, 0 хардкод-копий вне allowlist; (3) PLATFORM_DOMAIN — одно определение, 0 вхождений test.local/admin@test.local вне tests/; (4) secrets-manifest регенерирован и закоммичен, G1 --check зелёный; (5) generate-manifests покрывает G1-G6, fix-gate чинит check-manifests; (6) все *.template + 9 nginx-монтирований в template-manifest, templates-check зелёный; (7) один предикат discover_modules; (8) 0 комментариев «12 модулей»; (9) make gate MODE=fast зелёный; (10) новый TRAP[DECISION] об enforcement-гейтах зафиксирован в root AGENTS.md.
  IMPLEMENTS: U-01, U-02, U-16, U-17, U-33, U-43, U-44, U-47, U-59, U-68
  IMPACTS: core/internal/scripts/{generate_platform_env,sync_env_defaults,module_discovery}.py, core/internal/shared/secrets_manifest_reader.py (NEW), core/internal/bootstrap/{discover_modules.py,deploy/docker_orchestrator.py,deploy/secrets_validator.py,lifecycle/secrets_manager.py}, core/internal/scaffold/project_adopter.py, Makefile, makefiles/{manifest.mk,helpers.mk}, core/platform-infra.yaml, core/secret-definitions.yaml, core/secrets-manifest.yaml, core/templates/template-manifest.yaml, platform-env.yaml, .env.example, tests/_conftest/{smoke.py,smoke_env_generated.py}, tests/helpers/env_defaults_generated.py, tests/gates/*, tests/unit/*, docker-compose.yml, core/entrypoint-manifest.yaml, core/AGENTS.md, root AGENTS.md
  REQUIRES: Решения пользователя 2026-07-31 (D1-D3); greenfield-сервер (инвариант 9 программы) — backward-compat не нужна
---

## 1. Решения архитектора (утверждены пользователем 2026-07-31)

| # | Вопрос | Решение |
|---|--------|---------|
| D1 | Формат parity-гейтов | Pytest-гейты по trinity + repair-поля L1 + тонкие make-обёртки (`check-profiles-parity` и др.) для entrypoint-manifest |
| D2 | Источник env-значений smoke.py | Runtime-чтение `platform-env.yaml` env_defaults в smoke.py; `SMOKE_ENV_GENERATED` не меняет контракт (остаётся ci_defaults секретов) |
| D3 | Унификация предиката discover_modules | Один код: канонический предикат в `module_discovery.py` (точный, zero-dep), bootstrap импортирует его. Изоляция отменяется — дрейф кода опаснее |
| D4 | SoT PLATFORM_DOMAIN | `platform-infra.yaml` env_defaults.PLATFORM_DOMAIN (как все env-дефолты); генераторы без fallback (fail-fast); существующий гейт test_platform_domain_default ОБНОВЛЯЕТСЯ (SoT переезжает из генератора в platform-infra.yaml) |
| D5 | COMPOSE_PROFILES | По брифу: SoT = platform-infra.yaml env_defaults; остальное — generated или runtime-чтение через `yaml_query.py --get env_defaults.COMPOSE_PROFILES` (dotted-ключи поддержаны) |
| D6 | U-43 свежесть manifest | Достаточно byte-parity (G1 --check + test_gate_manifests_up_to_date) + регенерация/коммит; mtime-гейт не нужен |

**Текущее состояние worktree (старт волны):** незакоммиченные изменения `core/secret-definitions.yaml`, `core/secrets-manifest.yaml`, `tests/_conftest/smoke_env_generated.py`, `tests/helpers/env_defaults_generated.py`, `tests/_conftest/networks.py` — регенерация DEEPSEEK_API_KEY ci_default (TRAP[BUG] 2026-07-31). Это часть U-43: волна коммитит регенерированные файлы целиком.

---

## 2. Задачи

### T1 — U-01: Фикс scan_compose_ports (MINIO_PORT) [FUNDAMENT]

**Файл:** `core/internal/scripts/generate_platform_env.py:278-288` (функция `scan_compose_ports`).

**Проблема:** счётчик `service_port_count` инкрементируется только в ветке `elif service_port_count == 0`; при `service_upper == module_upper` (сервис minio в модуле minio) счётчик не растёт → второй порт (9001) перезаписывает первый (9000): `port_mappings.MINIO_PORT: 9001` (platform-env.yaml:120) при `env_defaults.MINIO_PORT: '9000'`.

**Фикс:**
```python
service_upper = service_name.upper().replace("-", "_")
if service_port_count == 0:
    var_name = f"{module_upper}_PORT"
else:
    var_name = f"{module_upper}_{service_upper}_PORT"
service_port_count += 1
```
Счётчик инкрементируется для ПЕРВОГО порта каждого сервиса, включая service==module.

**Ожидаемый результат:** `MINIO_PORT: 9000` и `MINIO_MINIO_PORT: 9001` в port_mappings (имя второго порта по схеме MODULE_SERVICE_PORT; MINIO_CONSOLE_PORT остаётся в env_defaults из platform-infra.yaml). Consumer-scan: `rg MINIO_MINIO_PORT` — потребителей нет, задокументировать в TRAP[BUG].

**Тесты:** расширить `tests/unit/test_generate_platform_env.py`: (а) фикстура minio-style (сервис==модуль, 2 порта) → первый порт в MODULE_PORT, второй не затирает; (б) multi-service модуль (infra-metrics-style) — регрессия.

**Критерий:** `port_mappings.MINIO_PORT == env_defaults.MINIO_PORT == 9000`; `make generate-platform-env` идемпотентен; `make check-manifests` G2 зелёный.

---

### T2 — U-02: COMPOSE_PROFILES — единый SoT, 0 хардкод-копий [FUNDAMENT]

**SoT:** `core/platform-infra.yaml:234` `env_defaults.COMPOSE_PROFILES` (13-item, уже корректен).

**Устраняемые копии (все — runtime-чтение или generated):**

| Место | Сейчас | Станет |
|-------|--------|--------|
| `Makefile:30` | `export COMPOSE_PROFILES ?= <13-item хардкод>` | `export COMPOSE_PROFILES ?= $(shell python3 core/internal/scripts/yaml_query.py --file core/platform-infra.yaml --get env_defaults.COMPOSE_PROFILES)` |
| `makefiles/helpers.mk:89-90` (`_get_all_profiles`) | `@echo "<13-item хардкод>"` | `@echo "$$(python3 core/internal/scripts/yaml_query.py --file core/platform-infra.yaml --get env_defaults.COMPOSE_PROFILES)"` (или `$(COMPOSE_PROFILES)`) |
| `core/internal/bootstrap/deploy/docker_orchestrator.py:514-518` | `os.environ.setdefault("COMPOSE_PROFILES", "<13-item>")` | Runtime-чтение: yaml-загрузка `env_defaults.COMPOSE_PROFILES` из `core/platform-infra.yaml` (путь резолвится относительно repo root; platform-infra.yaml доставляется с core/ — проверить, что VPS-деплой имеет файл; TRAP-комментарий при подтверждении) |
| `core/internal/scaffold/project_adopter.py:74` (`_DEFAULT_COMPOSE_PROFILES`) | Константа-хардкод (комментарий «synchronized with Makefile») | Удалить константу; чтение `platform-env.yaml` env_defaults.COMPOSE_PROFILES (локальный инструмент, файл всегда в репо). Обновить TRAP[DECISION] 2026-07-26 (строка 130-134) — «читается из platform-env.yaml» исполнено |
| `core/internal/scripts/sync_env_defaults.py:456-463` | Комментарий «Все 12 профилей» + fallback 12-item (без status-page) | (а) fallback удалить: `get_val("COMPOSE_PROFILES")` без default + явная ошибка при отсутствии (fail-fast, инвариант 7); (б) комментарий — вычисляемый: `f"Все {len(csv.split(','))} профилей"` |
| `platform-env.yaml:200`, `.env.example:255` | Generated | Generated (остаются; это allowlist) |

**Гейт «копий нет»** (T9): полная 13-item строка ищется rg по репо; allowlist = {`core/platform-infra.yaml` (SoT), `platform-env.yaml`, `.env.example`}. Всё остальное — RED.

**Consumer-scan:** `make _get_all_profiles` (вызывает test_gate_compose_profiles_consistency), `.github/actions/compose-profiles/action.yml` (уже читает `profiles` из platform-env.yaml — не трогаем), `makefiles/modules.mk:20` (runtime-переменная — не трогаем).

**Тесты:** обновить `tests/gates/test_gate_compose_profiles_consistency.py` (см. T9); unit-тест docker_orchestrator/project_adopter на runtime-чтение (fixture с tmp platform-infra.yaml/platform-env.yaml).

---

### T3 — U-16/U-17: PLATFORM_DOMAIN — одно определение; env-цепочка без test.local [FUNDAMENT]

**D4 (решение):** SoT = `core/platform-infra.yaml` env_defaults.

1. **platform-infra.yaml** env_defaults += `PLATFORM_DOMAIN: "ai-platform.local"` (в секцию Platform/Context; комментарий-схема домена: `PLATFORM_DOMAIN=<context>.local` при контексте, DevPlan 012).
2. **`core/internal/scripts/sync_env_defaults.py:176`**: `get_val("PLATFORM_DOMAIN", "ai-platform.local")` → `get_val("PLATFORM_DOMAIN")` без fallback (fail-fast). Комментарий-схема (строки 171-175) остаётся.
3. **`core/secret-definitions.yaml:152`**: `PLATFORM_MASTER_EMAIL` ci_default `"admin@test.local"` → `"admin@ai-platform.local"` (литерал — generated .py требуют литералы; не выводить admin@${PLATFORM_DOMAIN} в ci_default). Обновить TRAP[BUG]-комментарий при необходимости.
4. **`tests/_conftest/smoke.py:94-125`**: убрать из `_STATIC_SMOKE_ENV` значения, дублирующие env_defaults: `PLATFORM_DOMAIN` (test.local!), `POSTGRES_USER`, `POSTGRES_DB`, `NODE_NAME`, `HERMES_DASHBOARD_USERNAME`, `GF_SECURITY_ADMIN_USER`, `S3_BUCKET`, `PROMETHEUS_TARGETS_DIR`, `PROMETHEUS_RULES_DIR`, `NGINX_CONF_DIR` — заменить runtime-загрузкой env_defaults из `platform-env.yaml` (repo root; helper из tests/helpers/gate_helpers.py::repo_root): `PLATFORM_ENV_DEFAULTS = load_platform_env_defaults()` и `SMOKE_ENV = {**_STATIC_SMOKE_ENV, **PLATFORM_ENV_DEFAULTS, **SMOKE_ENV_GENERATED}`. ОСТАВИТЬ тест-специфику: test-порты (LITELLM_TEST_PORT и т.д.), tmp-директории, `S3_ENDPOINT_URL: ""` (TRAP[FIX] 2026-07-24 — намеренный оверрайд), `CONTEXT_IMAGE: :latest` (TRAP[BUG] 2026-07-27), `COMPOSE_PROJECT_NAME`, `NGINX_CERT_DIR` test-путь. Consumer-scan: `rg "SMOKE_ENV"` по tests/ — обновить затронутые тесты.
5. **`makefiles/helpers.mk:40-41`** (dev-certs): fallback `ai-platform.local` → runtime: `PLATFORM_DOMAIN="$${PLATFORM_DOMAIN:-$${_env_pd:-$$(python3 core/internal/scripts/yaml_query.py --file platform-env.yaml --get env_defaults.PLATFORM_DOMAIN)}}"` (приоритет: env → .env → platform-env.yaml; сохранить TRAP[BUG] 2026-07-16).
6. **U-17 AWS-алиасы** (`sync_env_defaults.py:280-281`): перенести в SoT — `platform-infra.yaml` env_defaults += `AWS_ACCESS_KEY_ID: "${S3_ACCESS_KEY}"`, `AWS_SECRET_ACCESS_KEY: "${S3_SECRET_KEY}"` (литералы-алиасы, compose резолвит). Генератор эмитит их через get_val (без хардкода). Consumer-scan: upload-s3.sh и др. — `rg "AWS_ACCESS_KEY_ID"` — проверить, что нигде не ожидается иной источник.

**Регенерация:** `make generate-manifests` (все цепочки) → platform-env.yaml, .env.example, smoke_env_generated.py, env_defaults_generated.py, secrets-manifest.yaml.

**Критерии:** rg по `core/ Makefile makefiles/ .github/ platform-env.yaml .env.example templates/` → 0 вхождений `test.local` и `admin@test.local`; `platform-env.yaml` env_defaults содержит PLATFORM_DOMAIN: ai-platform.local.

**Обновление гейта:** `tests/gates/test_gate_env_example_drift.py::test_platform_domain_default` — SoT переезжает: assert platform-infra.yaml содержит PLATFORM_DOMAIN=ai-platform.local; assert sync_env_defaults НЕ содержит fallback-значения; assert env_defaults_generated.py не содержит PLATFORM_DOMAIN (сохраняется). Тот же файл — S3_ENDPOINT-чеки не трогаем.

---

### T4 — U-33/U-43: secrets-manifest — один shared-парсер, регенерация [FUNDAMENT]

1. **Новый shared-модуль** `core/internal/shared/secrets_manifest_reader.py` (паттерн DevPlan 086, импорт через `from core.internal.shared...`):
   - `iter_secrets(path: Path) -> list[dict[str, Any]]` — единственная точка чтения secrets-manifest.yaml;
   - СТРОГИЙ режим: отсутствие файла / не-dict / не-list → raise с читаемым сообщением (никаких `return []`-фолбэков — инвариант 7; manifest всегда доставляется с core/);
   - типизированные хелперы: `tier(secret)`, `consumers(secret)`, `charset(secret)`, `gen_command(secret)`.
2. **Консолидация 3 парсеров:**
   - `core/internal/bootstrap/lifecycle/secrets_manager.py:201` `_read_manifest` → использует `iter_secrets` + фильтр tier==generated; hardcoded fallback-список УДАЛИТЬ (fail-visible). Consumer-scan: вызовы `_read_manifest` и тесты на fallback-ветку (`tests/unit/` — удалить/переписать консервирующие).
   - `core/internal/bootstrap/deploy/secrets_validator.py:62` `_check_env_requires` → `iter_secrets` + фильтр consumers; graceful degradation «manifest absent → []» УДАЛИТЬ (raise; инвариант 7).
   - `core/internal/bootstrap/deploy/secrets_validator.py:146` `_validate_secret_charsets` → `iter_secrets` + фильтр charset.
3. **U-43:** регенерировать + закоммитить secrets-manifest (вместе с регенерацией T3); G1 `--check` зелёный. Свежесть обеспечивается byte-parity (check-manifests + test_gate_manifests_up_to_date), mtime-гейт не добавляем (D6).
4. **Гейт импорта:** `tests/gates/test_gate_secrets_parser_import.py` — проверить, что контракт canonical-импорта сохранён (дополнить проверкой нового модуля, если гейт сканирует shared-импорты).

**Тесты:** `tests/unit/test_secrets_manifest_reader.py` (NEW): норма, отсутствие файла → raise, malformed → raise, фильтры tier/consumers/charset; обновить unit-тесты secrets_manager/secrets_validator (fallback-ветки удалены).

---

### T5 — U-44: generate-manifests покрывает G1-G6; fix-gate чинит всё [FUNDAMENT]

**Файл:** `makefiles/manifest.mk:34`.

```make
generate-manifests: generate-secrets-manifest generate-platform-env generate-env-example \
                    generate-entrypoint-manifest generate-agents-md generate-litellm-config
```
(Порядок обеспечивается цепочками: generate-env-example → generate-platform-env → generate-secrets-manifest; generate-agents-md → generate-entrypoint-manifest.)

- Удалить TRAP[DEBT] 2026-07-31 (строки 25-33) — проблема закрыта.
- Проверка repair-пути: `make fix-gate` (repair.mk:147-151 вызывает generate-manifests) → `make check-manifests` зелёный после индуцированного дрейфа (коснуться generated-файла → fix-gate → check-manifests).

**Критерий:** check-manifests зелёный после fix-gate без ручных шагов; G2/G4/G5 восстанавливаются.

---

### T6 — U-47: template-manifest — полное покрытие [FUNDAMENT]

**Файл:** `core/templates/template-manifest.yaml` + `core/internal/template_engine.py::check_all` (валидация путей/vars).

**Добавить записи** (type: single, output: null, consumer — фактический потребитель, vars — документируемые; для ${VAR}-файлов envsubst-синтаксиса {{}}-плейсхолдеров нет → check тривиально проходит; проверить `make templates-check`):

| Запись | Consumer |
|--------|----------|
| nginx: `../modules/nginx/config/platform-http.conf` | core/modules/nginx/docker-compose.base.yml:56 (envsubst) |
| nginx: `../modules/nginx/config/grafana-vhost.conf` | docker-compose.base.yml:57 |
| nginx: `../modules/nginx/config/hermes-dashboard.conf` | docker-compose.base.yml:58 |
| nginx: `../modules/nginx/config/langfuse-vhost.conf` | docker-compose.base.yml:59 |
| nginx: `../modules/nginx/config/loki-vhost.conf` | docker-compose.base.yml:60 |
| nginx: `../modules/nginx/config/prometheus-vhost.conf` | docker-compose.base.yml:61 |
| nginx: `../modules/nginx/config/platform-vhost.conf.template` | docker-compose.base.yml:62 |
| nginx dev: `../modules/nginx/dev-config/platform-default.conf.template`, `platform-vhost.conf.template`, `ssl-params.conf.template` | dev-режим NGINX_CONF_DIR=./dev-config (docker-compose.base.yml:54-55) |
| tor: `../../bootstrap/tor/torrc.template`, `../../bootstrap/tor/privoxy-config.template` | core/internal/bootstrap/install-tor-proxy.sh (найти фактический envsubst/рендер; consumer-строка точная) |

Уже зарегистрированы: sudo-whitelist.template, ssl-params.conf.template, platform-default.conf.template, alert-rules.yml, template-* директории.

**Критерии:** все 9 *.template файлов репо (find) имеют запись; 9 nginx-монтирований в /etc/nginx/templates/* покрыты; `make templates-check` зелёный.

---

### T7 — U-59: единый предикат discover_modules [CODE]

**D3 (решение пользователя):** один код, изоляция отменяется.

1. **Канонический предикат** — `core/internal/scripts/module_discovery.py::discover_docker_modules()` (строки 49-71):
   - заменить substring `SYSTEM_INSTALL_MARKER in content` на точный line-anchored regex: `re.search(r"^\s*install_type:\s*['\"]?system\b", content, re.MULTILINE)` (zero-dep сохраняется — PyYAML CI-раннеру не нужен; семантически exact, как YAML-парс);
   - compose-check оставить (`docker-compose.base.yml exists`);
   - `SYSTEM_INSTALL_MARKER` константу переопределить/удалить.
2. **bootstrap** — `core/internal/bootstrap/discover_modules.py:124-139`: `discover_modules()` удалить inline-предикат; импортировать канонический `discover_docker_modules` (паттерн импорта с fallback как в secrets_manager.py:54-70 — canonical `core.internal.scripts.module_discovery` + sys.path fallback для script-инвокации; единый, без дублирования логики). `discover_test_infra` и `update_compose_include` не трогаем (они не про предикат).
3. **Документация:** обновить @rationale module_discovery.py (строки 19-22 — изоляция отменена, причина: дрейф кода опаснее; зафиксировать TRAP[DECISION] 2026-07-31 в обоих файлах).
4. **Проверка эквивалентности:** на реальном core/modules оба потребителя дают 13 модулей (текущий результат не меняется).

**Тесты:** `tests/unit/test_module_discovery.py` (NEW): exact-детект (module.yaml с комментарием, содержащим «install_type: system», НЕ исключается; `install_type: system` с кавычками/отступом исключается); compose-check (модуль без compose исключается); bootstrap-эквивалентность на tmp-фикстуре. Обновить существующие тесты, если ссылаются на старое поведение.

---

### T8 — U-68: комментарии «12 модулей» [CODE]

- `docker-compose.yml:2` — «include: 12 docker modules» → «13 docker modules» (файл не generated — ручная правка + TRAP-заметка в MODULE_CONTRACT).
- `sync_env_defaults.py:456` — «Все 12 профилей» → вычисляемое (T2, п.5).
- `.env.example:254` — регенерируется автоматически (комментарий-заголовок из генератора).

**Критерий:** rg «12 модулей|Все 12 профилей» → 0 совпадений.

---

### T9 — Parity-гейты (формат: pytest trinity + make-обёртки) [ENFORCEMENT]

**D1 (решение):** pytest-гейты + repair L1 + make-таргеты-обёртки.

**Новые гейты (файлы tests/gates/ + @pytest.mark.gate + entrypoint-manifest):**

1. **`test_gate_profiles_parity.py`** (U-02, make-обёртка `check-profiles-parity`):
   - (a) SoT: platform-infra.yaml env_defaults.COMPOSE_PROFILES == discovered docker-модули (канонический предикат T7, set-equality; сейчас 13==13);
   - (b) generated-паритет: platform-env.yaml env_defaults.COMPOSE_PROFILES == SoT; .env.example COMPOSE_PROFILES == SoT;
   - (c) `make _get_all_profiles` == SoT;
   - (d) «копий нет»: полная 13-item строка отсутствует во всех tracked-файлах кроме allowlist {platform-infra.yaml, platform-env.yaml, .env.example} (rg с allowlist — гейты с allowlist, решение 01-Brief §1).
2. **`test_gate_domain_parity.py`** (U-16/U-17):
   - (a) PLATFORM_DOMAIN определён ровно один раз в SoT (platform-infra.yaml env_defaults) и присутствует в generated (platform-env.yaml, .env.example);
   - (b) 0 вхождений `test.local` / `admin@test.local` в {core/, Makefile, makefiles/, .github/, platform-env.yaml, .env.example, templates/} (tests/ исключены — фикстуры test_add_vhost.py используют test.local как данные, не дефолт);
   - (c) env_defaults_generated.py не содержит PLATFORM_DOMAIN (перенос из test_gate_env_example_drift.py — не дублировать: там оставить, здесь не проверять).
3. **`test_gate_template_manifest_coverage.py`** (U-47):
   - (a) каждый `*.template` файл репо (find, исключая .git/node_modules) зарегистрирован в template-manifest;
   - (b) каждый volume-источник nginx/docker-compose.base.yml, монтируемый в `/etc/nginx/templates/*.conf.template`, зарегистрирован.

**Обновления существующих гейтов:**
- `test_gate_compose_profiles_consistency.py`: canonical → platform-infra.yaml (не `make _get_all_profiles`); CALLSITES-тест конвертировать в «хардкод-копий нет» (или удалить в пользу profiles_parity d — не дублировать; выбрать один механизм, второй удалить с consumer-scan по entrypoint-manifest).
- `test_gate_env_example_drift.py::test_platform_domain_default`: SoT → platform-infra.yaml (T3 п.6).

**Make-обёртки:** `check-profiles-parity` (в makefiles/manifest.mk) — тонкий вызов pytest-файла гейта; `check-domain-parity` — аналогично (или только profiles — по брифу; domain покрывается pytest-gate автоматически в make gate). Регистрация: core/entrypoint-manifest.yaml (G3 регенерация — новые таргеты/гейты попадут автоматически; repair-поля L1: repair_command: `make generate-manifests` для profiles/domain, `make templates-check`-стиль для coverage), core/AGENTS.md (G4 регенерация), root AGENTS.md глоссарий — добавить `check-profiles-parity` в таблицу глаголов.

**Критерий:** все новые гейты зелёные; `make gate MODE=fast` проходит.

---

### T10 — Самоверификация волны + TRAP [VERIFY]

1. `make fix-gate && git add -u` — чистое дерево.
2. `make gate MODE=fast` — зелёный (локально, macOS).
3. `make check-manifests` — зелёный; `make templates-check` — зелёный; `make check-profiles-parity` — зелёный.
4. Repair-путь: индуцировать дрейф (правка generated) → `make fix-gate` → check-manifests зелёный → откат.
5. Consumer-scan чек-лист по каждому удалению (инвариант 2 программы): SMOKE_ENV (test_smoke_platform.py, test_platform_endpoints.py), _read_manifest fallback, _DEFAULT_COMPOSE_PROFILES, smoke static entries.
6. **Новый TRAP[DECISION] в root AGENTS.md** (01-Brief инвариант 6 «новый TRAP обязателен»): enforcement-гейты с allowlist — решение 2026-07-31; пересмотр TRAP[DECISION] 2026-07-21 (enforcement через pre-commit) — CI-гейты приняты, allowlist = generated/SoT.
7. Коммит одним или несколькими логическими коммитами (стиль репо: `fix(116): ...` / `feat(116): ...`), включая регенерированные файлы.

---

## 3. Порядок и зависимости

T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 (независимы, после T1-T4) → T9 (гейты после предметов) → T10.

Критический путь: T1 (фикс генератора) → регенерации (T3/T4) → T9 (гейты на регенерированное).

## 4. Риски

| Риск | Митигация |
|------|-----------|
| VPS-деплой docker_orchestrator без platform-infra.yaml (T2) | Файл в core/ — доставляется rsync; unit-тест + TRAP-пометка; при обнаружении проблемы — fallback на os.environ без хардкода (fail-fast) |
| templates-check падает на новых записях (T6) | Регистрация с `output: null` и документируемыми vars; проверка `make templates-check` до коммита |
| Удаление fallback в secrets_validator ломает старые VPS | Greenfield (инвариант 9 программы) — manifest всегда доставляется; raise с читаемым сообщением |
| Дрейф регенерации между T1-T9 (порядок) | Все регенерации в конце T3/T4; T9-гейты зелёные только на финальном состоянии |

## 5. Сдача волны

Все 8 AC брифа + критерии T1-T10; `make gate MODE=fast` зелёный; коммит включает регенерированные файлы; root AGENTS.md — новый TRAP[DECISION].
