# 01-DevPlan — Фикс Problem Registry 121 (P-1..P-7)

<!-- GREP_SUMMARY: audit-fixes, down-volumes, litellm-health-url, status-page-port, context-image, yaml-query-dup, manifest-signatures, 122, P-1 -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ Контекст (верификация P-1..P-7, 119-H НЕ покрывает) → ◇ Решения по канонам → ⊕ 7 задач (T1-T7) → ⊕ test-spec (4 новых гейта) → ◇ волны → ⎋ верификация -->

# region MODULE_CONTRACT
## @purpose  Исправление 7 подтверждённых дрейфов из Problem Registry RC-верификации 121 (P-1..P-7): data-loss риск `make down -v`, 3 расходящихся health-эндпоинта LiteLLM, STATUS_PAGE_PORT не потребляется compose, скрытый fallback CONTEXT_IMAGE, дубль yaml_query-тестов, сигнатурный дрейф entrypoint-manifest (инвариант 11).
## @scope    makefiles/modules.mk, core/entrypoint-manifest.yaml + core/AGENTS.md (generated), core/platform-infra.yaml, litellm/hermes-agent/status-page compose и healthcheck, sync_env_defaults.py, консолидация yaml_query-тестов, 4 новых parity-гейта.
## @invariants
##   1. Инвариант 11: generated-файлы (entrypoint-manifest.yaml make_target-секции, core/AGENTS.md, platform-env.yaml, .env.example) — манифест НЕ перегенерируется для signature/delegates_to (merge() сохраняет make_target-секции verbatim — подтверждено generate_entrypoint_manifest.py merge/load_structural_sections); правки сигнатур — вручную в манифесте, затем `make generate-agents-md` + `make check-manifests`.
##   2. Канон безопасности down: root `make down` = `docker compose down` БЕЗ -v (совпадает с AGENTS.md/manifest, с remove-project в scaffold.mk:8 и module-level down в modules/AGENTS.md). Деструктивный снос — только явный таргет `down-volumes`.
##   3. Канон health-URL LiteLLM (решение оператора 2026-08-03): единственный эндпоинт `/health/liveliness` во всех источниках. НЕ `/health` — production-config НЕ имеет `disable_auth_for_health_check: true` (есть только в test.yml:25), bare `/health` требует Bearer-ключ → 401 для unauth-пробы (compose HEALTHCHECK и hermes healthcheck_deps.py ходят urllib без заголовков); текущий дефолт LITELLM_HEALTH_URL=`/health` — латентный баг production. НЕ `/health/readiness` — readiness проверяет коннект к БД, сбой БД = рестарт-цикл; liveliness = чистая проверка «сервер поднят», уже обкатан в test.yml.
##   4. Канон образов: default compose-образа совпадает с SoT (platform-infra.yaml env_defaults); скрытые вторые пины запрещены (продолжение U-60/116 B3).
##   5. Гейты-детекторы регрессий: на каждый фикс — parity-гейт с R5 negative (анти-выживаемость).
## @rationale  Подготовительная сессия 121 зарегистрировала дрейфы БЕЗ фиксов (read-only). Волна 119-H (NodeYaml-декомпозиция) НЕ пересекается с P-1..P-7 ни одним файлом — подтверждено git status рабочего дерева (только node_yaml-миграция). Дрейфы сигнатур невидимы существующим гейтам (test_gate_manifest_integrity валидирует пути/forbidden/наименования, НЕ сигнатуры make_target) — требуется сигнатурный parity-гейт.
## @changes 2026-08-03 | Создан по результатам верификации Problem Registry 121 (все 7 пунктов подтверждены кодом).
# endregion MODULE_CONTRACT

---

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Закрыть 7 подтверждённых дрейфов RC-аудита (P-1..P-7) с parity-гейтами против регрессии |
| **DESCRIPTION** | T1 down без -v + down-volumes; T2 единый LITELLM health-URL; T3 STATUS_PAGE_PORT через compose; T4 CONTEXT_IMAGE default = SoT; T5 консолидация yaml_query-тестов; T6+T7 сигнатуры gate/up/backup/restore в манифесте |
| **RATIONALE** | P-1 — риск потери данных при `make down` (docs обещают без -v); P-6/P-7 — нарушение инварианта 11, гейтами не ловится; P-2/P-3/P-4 — тихие no-op при смене env; P-5 — R1-иллюзия зелёных тестов |
| **ACCEPTANCE_CRITERIA** | 1) `make gate MODE=fast` зелёный; 2) `make check-manifests` зелёный (манифест и AGENTS.md консистентны после ручных правок); 3) `make down` не удаляет volumes, `make down-volumes` удаляет; 4) 4 новых parity-гейта зелёные с R5 negative; 5) `pytest tests/ -m "not requires_node"` — 0 регрессий |
| **IMPLEMENTS** | Problem Registry 121 (`.ai/plans/121-rc-verification/02-DevPlan.md`, P-1..P-7); P-12 (collapse манифеста) снимается закрытием P-1/P-6/P-7 |
| **IMPACTS** | makefiles/modules.mk, core/entrypoint-manifest.yaml, core/AGENTS.md (generated), root AGENTS.md глоссарий (generated), core/platform-infra.yaml, core/modules/{litellm,hermes-agent,status-page}/*, core/internal/scripts/sync_env_defaults.py, tests/{test_yaml_query,test_unit_yaml_query}.py, platform-env.yaml/.env.example (generated) |
| **REQUIRES** | Предусловие: закоммитить незавершённую миграцию node_yaml (рабочее дерево 119-H) ДО прогона gate/e2e — иначе test-node исполняет грязное дерево, а не HEAD |

---

## Контекст — верификация всех 7 пунктов (выполнена 2026-08-03)

Все пункты Problem Registry 121 подтверждены по коду. 119-H (`.ai/plans/119-wave2-synthesis/09-DevPlan.md`, NodeYaml-декомпозиция: H1-миксины/H2-atomic_writer/H3-consumers) **НЕ поглощает ни один пункт** — рабочее дерево (незавершённый 119-H: `node_yaml.py`→пакет, `test_node_yaml_mixins/consumers`) не трогает ни один файл из P-1..P-7.

| # | Severity | Верифицировано | Источник-факт | Доки (расходятся) | 119-H |
|---|----------|----------------|---------------|-------------------|-------|
| P-1 | HIGH | ✅ | `makefiles/modules.mk:44-47` — `down` = `docker compose ... down -v` (комментарий «remove volumes») | `core/AGENTS.md:69` + `entrypoint-manifest.yaml:561` = «docker compose down»; эталон безопасности `scaffold.mk:8` (remove-project, без -v) | ❌ |
| P-2 | MED | ✅ | `platform-infra.yaml:190` LITELLM_HEALTH_URL=`/health`; `litellm/base.yml:135`=`/health/readiness`; `litellm/test.yml:34`=`/health/liveliness` | hermes-agent consumes `/health` (compose:164, healthcheck.sh:57, sync_env_defaults:458, .env.example:65) | ❌ |
| P-3 | MED | ✅ | `platform-infra.yaml:233` STATUS_PAGE_PORT=8080; `status-page/base.yml:49-52` НЕ инжектит env; healthcheck:70 hardcode 8080; `app.py:91` default 8080 | смена порта = тихий no-op; 8080 дублирует CADVISOR_PORT (platform-infra:248) | ❌ |
| P-4 | MED | ✅ | `hermes-agent/base.yml:69` fallback `${CONTEXT_IMAGE:-...:latest@sha256:dd36…}` vs SoT `v2026.7.1` (platform-infra:146, .env.example:53, platform-env.yaml:128); TRAP[PERF] :62-68 подтверждает | скрытый второй пин образа | ❌ |
| P-5 | MED | ✅ | `tests/test_yaml_query.py` (direct-import, 144 LOC) + `tests/test_unit_yaml_query.py` (subprocess CLI, 306 LOC) — один модуль `core.internal.scripts.yaml_query` | R1-иллюзия: дубль маскирует регрессии | ❌ |
| P-6 | MED | ✅ | `ci.mk:136` + `check_suite.py:71` = MODE `fast\|full\|ci-docker` | `manifest:279` + `core/AGENTS.md:44` = `fast\|full` | ❌ |
| P-7 | MED | ✅ | `modules.mk:26-41` up=[MODULES], `:69-72` backup(0 vars), `:77-84` restore[DUMP_FILE] | `AGENTS.md:68,72,73` + `manifest:555,580-581,588` = up[PROJECT], backup[NODE], restore NODE=\<n\> | ❌ |

**Критический факт для P-6/P-7 (и строки down в манифесте):** генератор `generate_entrypoint_manifest.py` перегенерирует ТОЛЬКО `allowed_verbs` и `gates[]` (G3 cycle-break); секции `make_target` (signature/delegates_to/description) сохраняются из существующего манифеста verbatim (`merge()`/`load_structural_sections`). ⇒ `make generate-entrypoint-manifest` **НЕ починит** расхождения сигнатур. Правки — вручную в манифесте, затем `make generate-agents-md` (чтение signature из манифеста, `generate_agents_md.py:128`) и `make check-manifests`. Существующий `test_gate_manifest_integrity` сигнатуры НЕ валидирует — дрейф был невидим.

---

## $TASKS

### TASK-T1: `make down` — не-деструктивный канон + явный `down-volumes` (P-1, HIGH)

| Поле | Значение |
|------|----------|
| **ID** | T1 |
| **Sev** | HIGH |
| **Сложность** | 2/10 |
| **Файлы** | `makefiles/modules.mk`, `core/entrypoint-manifest.yaml`, `core/AGENTS.md`+root глоссарий (generated), `tests/gates/test_gate_down_no_volumes.py` (NEW) |
| **Зависимости** | — |

**Описание:** выровнять поведение к документированному контракту. `make down` — БЕЗ `-v` (данные сохраняются). Деструктивный снос — отдельный явный таргет `down-volumes` (`docker compose ... down -v`), чтобы потеря данных требовала осознанного действия.

**Шаги:**
1. `makefiles/modules.mk`: `down` → `docker compose $(COMPOSE_BASE_FILES) down` (убрать `-v`, обновить комментарий); добавить `.PHONY: down-volumes` и таргет `down-volumes` = `down -v` с явным предупреждением в echo `[IMP:9] WARNING: volumes will be removed`.
2. `core/entrypoint-manifest.yaml` (вручную, генератор не трогает make_target): `down` → `delegates_to: docker compose down`, description «Stop local compose lifecycle (data preserved)»; НОВАЯ запись `down-volumes` с `delegates_to: docker compose down -v`, description «Stop and remove compose volumes (destructive)».
3. `make generate-agents-md` → `core/AGENTS.md:69` обновится + root-глоссарий получит `down-volumes` (новый глагол, счётчик 69→70, регистрация в allowed_verbs обязательна — генератор сделает автоматически из .PHONY).
4. Новый гейт `test_gate_down_no_volumes.py`: парсит `makefiles/modules.mk`, рецепт `down:` НЕ содержит `-v`; рецепт `down-volumes:` содержит. R5 negative: inline-фикстура с `down:` = `down -v` → RED.
5. Проверить: `rg "make down"` в CI/tests — потребителей с ожиданием деструктивного поведения нет (teardown-ы используют `docker compose down -v` напрямую).

**Acceptance Criteria:**
- AC-T1.1: `make down` НЕ удаляет volumes; `make down-volumes` удаляет
- AC-T1.2: манифест + AGENTS.md + глоссарий консистентны (`make check-manifests`)
- AC-T1.3: гейт `test_gate_down_no_volumes` зелёный с R5 negative

---

### TASK-T2: LITELLM health-URL — единый эндпоинт `/health/liveliness` (P-2, MED)

| Поле | Значение |
|------|----------|
| **ID** | T2 |
| **Sev** | MED |
| **Сложность** | 3/10 |
| **Файлы** | `core/platform-infra.yaml`, `core/modules/litellm/docker-compose.base.yml`, `core/modules/litellm/docker-compose.test.yml` (комментарий), `core/modules/litellm/module.yaml`+`healthcheck.sh` (комментарии), `core/modules/hermes-agent/docker-compose.base.yml`, `core/modules/hermes-agent/healthcheck.sh`, `core/internal/scripts/sync_env_defaults.py`, `.env.example`+`platform-env.yaml` (generated), `tests/gates/test_gate_litellm_health_url_parity.py` (NEW) |
| **Зависимости** | — |

**Описание:** выровнять все источники на `/health/liveliness` (решение оператора 2026-08-03). Обоснование: LiteLLM требует auth для bare `/health` (production-config без `disable_auth_for_health_check: true`), а `/health/readiness` проверяет коннект к БД (сбой БД = unhealthy = рестарт-цикл). `/health/liveliness` — unauth, чистая проверка «сервер поднят», уже эталон в test.yml. Текущий дефолт `LITELLM_HEALTH_URL=/health` — латентный баг production (hermes deps-проверка получает 401).

**Шаги:**
1. `core/platform-infra.yaml:190` → `LITELLM_HEALTH_URL: "http://litellm:4000/health/liveliness"` (SoT).
2. `litellm/docker-compose.base.yml:135` → `/health/liveliness` (заменить `/health/readiness`; обновить комментарии :12/:128).
3. `hermes-agent/docker-compose.base.yml:164` default → `http://litellm:4000/health/liveliness`; `hermes-agent/healthcheck.sh:57` default → то же.
4. `sync_env_defaults.py:458` fallback-литерал → `/health/liveliness`.
5. Комментарии `litellm/module.yaml:12`, `litellm/healthcheck.sh:9,12,28` актуализировать.
6. `make generate-platform-env && make sync-env-defaults` → platform-env.yaml + .env.example.
7. Новый гейт `test_gate_litellm_health_url_parity.py`: все 5 источников (platform-infra, litellm base.yml, litellm test.yml, hermes-agent base.yml, sync_env_defaults fallback) содержат одинаковый путь эндпоинта. R5 negative: источник с `/health/readiness` → RED.

**Acceptance Criteria:**
- AC-T2.1: 0 вхождений `/health/readiness` и `:4000/health"` (без суффикса) в compose-источниках; единственный эндпоинт `/health/liveliness`
- AC-T2.2: гейт parity зелёный с R5 negative

---

### TASK-T3: STATUS_PAGE_PORT — потребление SoT через compose (P-3, MED)

| Поле | Значение |
|------|----------|
| **ID** | T3 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `core/modules/status-page/docker-compose.base.yml`, `tests/gates/test_gate_status_page_port_parity.py` (NEW) |
| **Зависимости** | — |

**Описание:** устранить тихий no-op: compose должен инжектить `STATUS_PAGE_PORT` в контейнер (app.py уже читает env, `app.py:91`) и healthcheck должен пробовать тот же порт.

**Шаги:**
1. `status-page/docker-compose.base.yml` environment → добавить `STATUS_PAGE_PORT: ${STATUS_PAGE_PORT:-8080}`.
2. Healthcheck (:70) → `http://localhost:${STATUS_PAGE_PORT:-8080}/healthz`.
3. `platform-infra.yaml:233` остаётся SoT-дефолтом. Дублирование значения с CADVISOR_PORT:8080 — задокументировать комментарием (разные интерфейсы: status-page internal без host-порта; cadvisor host-bind 127.0.0.1) — не менять значения.
4. Новый гейт `test_gate_status_page_port_parity.py`: compose environment и healthcheck ссылаются на `${STATUS_PAGE_PORT}`; дефолт в compose == `env_defaults.STATUS_PAGE_PORT` в platform-infra. R5 negative: healthcheck с hardcode-портом без переменной → RED.

**Acceptance Criteria:**
- AC-T3.1: смена `STATUS_PAGE_PORT` в .env меняет порт приложения И healthcheck (не no-op)
- AC-T3.2: гейт parity зелёный с R5 negative

---

### TASK-T4: CONTEXT_IMAGE — default compose = SoT (P-4, MED)

| Поле | Значение |
|------|----------|
| **ID** | T4 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `core/modules/hermes-agent/docker-compose.base.yml`, `tests/gates/test_gate_image_tag_form.py` (расширение) |
| **Зависимости** | — |

**Описание:** убрать скрытый второй пин `latest@sha256:dd36…`. Default fallback в compose должен совпадать с SoT `ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1`.

**Шаги:**
1. `hermes-agent/docker-compose.base.yml:69` → `image: ${CONTEXT_IMAGE:-ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1}`.
2. Актуализировать TRAP[PERF] (:62-68): digest-pin вернётся, когда CI начнёт публиковать `v2026.x.y@sha256:…`; до тех пор версионный тег — единый источник (платформенный `.env`/platform-env уже задаёт `v2026.7.1`).
3. Расширить `test_gate_image_tag_form.py`: parity-assert — тег в default compose == тег `env_defaults.CONTEXT_IMAGE` (значение без `:latest`-fallback). R5 negative: fallback `:latest@sha256` → RED.

**Acceptance Criteria:**
- AC-T4.1: 1 источник тега образа (SoT); compose fallback совпадает с SoT
- AC-T4.2: расширенный гейт зелёный с R5 negative

---

### TASK-T5: консолидация дубля yaml_query-тестов (P-5, MED)

| Поле | Значение |
|------|----------|
| **ID** | T5 |
| **Sev** | MED |
| **Сложность** | 3/10 |
| **Файлы** | `tests/unit/test_yaml_query.py` (NEW канон), `tests/test_yaml_query.py` (del), `tests/test_unit_yaml_query.py` (del), `tests/test_inventory.yaml` (regen) |
| **Зависимости** | — |

**Описание:** один канонический файл тестов yaml_query с обоими покрытиями (direct-import edge-cases + CLI subprocess/JSON-repr regression). Канон-путь — `tests/unit/` (тест Python-модуля без Docker, tests/AGENTS.md).

**Шаги:**
1. Создать `tests/unit/test_yaml_query.py`: перенести из `tests/test_yaml_query.py` (nested-key, missing-key+default, missing-key без default, malformed, not-found — R5 negative) + из `tests/test_unit_yaml_query.py` (CLI-субпроцесс, JSON dict/list/scalar, single-quote repr regression TRAP[BUG] 2026-07-21).
2. Удалить `tests/test_yaml_query.py` и `tests/test_unit_yaml_query.py`.
3. `make test-inventory-sync` → обновить `tests/test_inventory.yaml` (удаления в changelog — правило inventory rename U-79).
4. Проверить отсутствие внешних импортов удаляемых файлов (`rg "test_unit_yaml_query|test_yaml_query" tests/ --include='*.py'` — только сам канон).

**Acceptance Criteria:**
- AC-T5.1: ровно 1 файл тестов yaml_query; покрытие не уменьшилось (все кейсы обоих файлов)
- AC-T5.2: `test_gate_test_inventory` зелёный (удаления задокументированы)

---

### TASK-T6: сигнатура `gate` в манифесте — `ci-docker` (P-6, MED)

| Поле | Значение |
|------|----------|
| **ID** | T6 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `core/entrypoint-manifest.yaml` (ручная правка), `core/AGENTS.md` (generated), `tests/gates/test_gate_manifest_signature_parity.py` (NEW, совместно с T7) |
| **Зависимости** | T7 (общий файл манифеста — одна волна) |

**Описание:** код поддерживает `MODE=fast|full|ci-docker` (`ci.mk:136`, `check_suite.py:71`), манифест и AGENTS.md — только `fast|full`. Нарушение инварианта 11.

**Шаги:**
1. `core/entrypoint-manifest.yaml`, запись `make_target: gate` (строка ~279): signature → `make gate [MODE=fast|full|ci-docker]`; description дополнить: «MODE=ci-docker: predeploy-docker → smoke → component».
2. ⚠️ НЕ запускать `make generate-entrypoint-manifest` для фикса — он сохранит устаревшую signature (make_target-секции verbatim). После ручной правки — `make generate-agents-md` (обновит `core/AGENTS.md:44`) и `make check-manifests`.
3. Сигнатурный parity-гейт (общий с T7, см. ниже).

**Acceptance Criteria:**
- AC-T6.1: манифест + core/AGENTS.md содержат `ci-docker` в signature gate
- AC-T6.2: `make check-manifests` зелёный

---

### TASK-T7: сигнатуры `up`/`backup`/`restore` в манифесте (P-7, MED)

| Поле | Значение |
|------|----------|
| **ID** | T7 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `core/entrypoint-manifest.yaml` (ручная правка), `core/AGENTS.md` (generated), `tests/gates/test_gate_manifest_signature_parity.py` (NEW, совместно с T6) |
| **Зависимости** | T6 (общий файл манифеста — одна волна) |

**Описание:** выровнять доки к фактическому коду (код — факт, докам обновляться):
- `up`: signature `make up [PROJECT=...]` → `make up [MODULES=<comma-list>]` (код фильтрует по MODULES, переменной PROJECT в рецепте нет);
- `backup`: `make backup [NODE=...]` → `make backup` (делегация в backup-cron module make backup, переменных нет);
- `restore`: `make restore NODE=<n>` → `make restore DUMP_FILE=<path>` (код падает «DUMP_FILE not set» без аргумента; NODE не читается).

**Шаги:**
1. `entrypoint-manifest.yaml` записи `up`/`backup`/`restore` (~555, 580-581, 588): обновить signature; `up.delegates_to` дополнить `provision-environment.sh → docker compose up (MODULES filter)`.
2. `make generate-agents-md` → обновится `core/AGENTS.md:68,72,73`.
3. Совместный гейт `test_gate_manifest_signature_parity.py` (T6+T7): карта таргет→ожидаемая подстрока signature (`gate`→`ci-docker`, `up`→`MODULES`, `backup`→без `[NODE=`, `restore`→`DUMP_FILE`, `down`→без `-v`); манифест против карты. R5 negative: инлайн-запись с устаревшей сигнатурой → RED.

**Acceptance Criteria:**
- AC-T7.1: манифест + AGENTS.md отражают реальные сигнатуры up/backup/restore
- AC-T7.2: `make restore NODE=prod` даёт осмысленную ошибку «DUMP_FILE not set» (не «неизвестная переменная»), `make restore DUMP_FILE=<path>` работает

---

## $PARALLEL_GROUPS

```
Wave 1 (параллельно, файлы не пересекаются): T1 · T2 · T3 · T4 · T5
Wave 2 (один агент — общий entrypoint-manifest.yaml + core/AGENTS.md): T6 + T7
Wave 3 (верификация): make generate-manifests && make check-manifests && make gate MODE=fast
```

T2 и T4 пересекаются только по `hermes-agent/docker-compose.base.yml` (разные строки: env:164 vs image:69) — допустимо параллелить с явной инструкцией не трогать чужой участок; при сомнении — T2→T4 последовательно.

---

## $TEST_SPEC

| Test file | Функция | Scenario | Module under test |
|-----------|---------|----------|-------------------|
| `tests/gates/test_gate_down_no_volumes.py` | `test_down_no_volumes` + R5 negative | рецепт `down:` без `-v`; `down-volumes:` с `-v` | modules.mk (T1) |
| `tests/gates/test_gate_litellm_health_url_parity.py` | `test_litellm_health_url_parity` + R5 negative | 5 источников с одним эндпоинтом `/health/liveliness` | litellm/hermes-agent compose + sync_env_defaults (T2) |
| `tests/gates/test_gate_status_page_port_parity.py` | `test_status_page_port_parity` + R5 negative | compose env + healthcheck ссылаются на `${STATUS_PAGE_PORT}`, дефолт == SoT | status-page compose (T3) |
| `tests/gates/test_gate_manifest_signature_parity.py` | `test_signature_parity` + R5 negative | карта таргет→сигнатура (gate/up/backup/restore/down) против манифеста | entrypoint-manifest (T6+T7) |
| `tests/unit/test_yaml_query.py` | консолидированные кейсы | все edge-cases + CLI JSON repr | yaml_query (T5) |
| `tests/gates/test_gate_image_tag_form.py` | расширенный parity-assert | default compose тег == env_defaults.CONTEXT_IMAGE | hermes-agent base.yml (T4) |

---

## Acceptance Criteria Summary

| Критерий | Проверка |
|----------|----------|
| AC-ALL | `make gate MODE=fast && make check-manifests && ruff check .` зелёные (после коммита 119-H) |
| AC-P-1 | `make down` сохраняет volumes; `down-volumes` — деструктивный явный |
| AC-P-2 | Единый `/health/liveliness` во всех источниках LiteLLM |
| AC-P-3 | STATUS_PAGE_PORT потребляется compose (не no-op) |
| AC-P-4 | Единый тег CONTEXT_IMAGE (default compose == SoT) |
| AC-P-5 | Один канонический файл yaml_query-тестов |
| AC-P-6/P-7 | Манифест + AGENTS.md консистентны с кодом; P-12 (collapse) снят |
| AC-R5 | 4 новых parity-гейта с negative-тестами |

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/122-audit-fixes/01-DevPlan.md, implement Wave 1: T1, T2, T3, T4, T5
```

### Wave 2
```
coder Read .ai/plans/122-audit-fixes/01-DevPlan.md, implement Wave 2: T6, T7 (manifest + AGENTS.md)
```

### Wave 3 (верификация)
```
make generate-manifests && make check-manifests
make gate MODE=fast
make down && docker volume ls  # проверить: volumes сохранены
make down-volumes && docker volume ls  # проверить: volumes удалены
```

⚠️ **Предусловие:** до прогона gate/e2e закоммитить рабочее дерево 119-H (node_yaml-миграция) — `git add core/internal/shared/node_yaml/ ... && git commit`; иначе проверки исполняют незакоммиченное дерево, а не HEAD.

## $END_DEVPLAN
