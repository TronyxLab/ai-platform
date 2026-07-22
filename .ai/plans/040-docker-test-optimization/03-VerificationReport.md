$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               QA верификация DevPlan 040 перед реализацией — проверка архитектурных инвариантов, кросс-файлового drift, семантической целостности и покрития acceptance criteria.
DESCRIPTION:           Полный аудит Phases 1-6 для LARGE-задачи (28 файлов, CI/Makefile/config изменения). Выявлено 2 HIGH, 3 MEDIUM, 4 WARNING — блокирующих проблем нет, но требуется доработка DevPlan по 3 пунктам перед началом кодирования.
RATIONALE:             DevPlan затрагивает тестовую инфраструктуру (conftest, фикстуры, CI workflow) и вводит многопоточную Wave-Pipeline архитектуру — необходим тщательный пре-имплементационный аудит.
ACCEPTANCE_CRITERIA:   Проверить: (1) семантическую целостность DevPlan, (2) отсутствие критического drift с существующей кодовой базой, (3) реализуемость AC1-AC9, (4) соблюдение инвариантов AGENTS.md.
IMPLEMENTS:            QA workflow Phase 1-6 для LARGE-задачи согласно §QA Behavior.
IMPACTS:               DevPlan 040 Wave 1-5 имплементация.
REQUIRES:              DevPlan 040, доступ к core/modules/*/module.yaml, CI workflow, Makefile.
$END_ARTIFACT_CONTRACT

---

🔒 **Verified against SHA:** `b301609b32e71ea43ff0b16daf06002a6e45a83e`
⚠️ **WARNING:** 4 некоммитченных файла обнаружено — состояние workspace может не соответствовать SHA:
- `.ai/plans/038-fix-smoke-skips/DevPlan.md`
- `core/internal/bootstrap/converge/reconciler.py`
- `core/internal/bootstrap/deploy/orphan_reconciler.py`
- `tests/unit/test_reconciler.py`

---

## 1. Static Audit (Phase 1)

### Compliance Matrix

| # | File | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | Regions | Doxygen | LDD IMP:7-10 | Secrets |
|---|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | `tests/_conftest/smoke.py` (846 LOC) | ✅ | ✅ | ✅ full | ✅ | ✅ | ✅ | ✅ test-only |
| 2 | `tests/_conftest/__init__.py` (103 LOC) | ✅ | ✅ | ✅ full | ✅ | ✅ | N/A | N/A |
| 3 | `tests/conftest.py` (33 LOC) | ✅ | ✅ | ✅ full | ✅ | ✅ | N/A | N/A |
| 4 | `tests/_conftest/infra.py` (208 LOC) | ✅ | ✅ | ✅ full | ✅ | ✅ | ✅ | N/A |
| 5 | `tests/test_smoke_postgres.py` (510 LOC) | ✅ | ✅ | ✅ full | ✅ | ✅ | ✅ | ✅ test-only |
| 6 | `tests/test_component_hermes.py` (939 LOC) | ✅ | ✅ | ✅ full | ✅ | ✅ | ✅ | ✅ test-only |
| 7 | `tests/test_component_pgbouncer.py` (664 LOC) | ✅ | ✅ | ✅ full | ✅ | ✅ | ✅ | ✅ test-only |
| 8 | `tests/test_component_clickhouse.py` (446 LOC) | ✅ | ✅ | ✅ full | ✅ | ✅ | ✅ | ✅ test-only |
| 9 | `tests/test_smoke_nginx.py` (520 LOC) | ✅ | ✅ | ✅ full | ✅ | ✅ | ✅ | N/A |
| 10 | `tests/test_smoke_redis.py` (566 LOC) | ✅ | ✅ | ✅ full | ✅ | ✅ | ✅ | N/A |
| 11 | `pyproject.toml` (65 LOC) | ✅ | ✅ | ✅ full | ✅ | N/A | N/A | N/A |
| 12 | `Makefile` (41 LOC) | ✅ | ✅ | ✅ full | ✅ | N/A | N/A | N/A |
| 13 | `core/entrypoint-manifest.yaml` (613 LOC) | ✅ | ✅ | ✅ full | N/A | N/A | N/A | N/A |
| 14 | `.github/workflows/platform-test.yml` (367 LOC) | ✅ | ✅ | ✅ full | ✅ | N/A | N/A | ⚠️ secrets refs |
| -- | **NEW FILES (not yet created):** | -- | -- | -- | -- | -- | -- | -- |
| N1 | `tests/_conftest/reuse.py` | ❌ TBD | ❌ TBD | ❌ TBD | ❌ TBD | ❌ TBD | ❌ TBD | N/A |
| N2 | `tests/_conftest/state_reset.py` | ❌ TBD | ❌ TBD | ❌ TBD | ❌ TBD | ❌ TBD | ❌ TBD | N/A |
| N3 | `tests/_conftest/wave_pipeline.py` | ❌ TBD | ❌ TBD | ❌ TBD | ❌ TBD | ❌ TBD | ❌ TBD | N/A |

### Findings

**[INFO] F1 · platform-test.yml:345 · Secrets referenced in CI workflow**
`HERMES_DASHBOARD_PASSWORD`, `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `LITELLM_MASTER_KEY` — все используют `${{ secrets.* }}` синтаксис GitHub Actions. Корректно, secrets не раскрыты в коде.

**[WARNING] F2 · smoke.py:716-724 · `_STALE_CONTAINER_NAMES` неполный список**
Подтверждён gap: отсутствуют `clickhouse-test` и `redis-test`. DevPlan корректно идентифицирует эти 2 имени. Однако список также не включает другие потенциально-stale контейнеры (`minio-test`, `litellm-test`, `langfuse-test`, `langfuse-worker-test`, `backup-cron-test`, `infra-metrics-test`, `monitoring-*`, `status-page-*`, `platform-secrets-*`). Рекомендация: рассмотреть динамическое удаление всех `*-test` контейнеров вместо хардкоженного списка.

**[INFO] F3 · Все создаваемые файлы · Требуют полного semantic markup**
`reuse.py`, `state_reset.py`, `wave_pipeline.py` должны получить GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT, #region/#endregion, Doxygen-теги и LDD IMP:7-10 логи при создании. Добавить в acceptance criteria явную проверку.

### Summary
- **PASS:** 14/14 существующих файлов
- **BLOCKED (TBD):** 3/3 новых файла (ожидаемо — будут созданы при реализации)
- **Findings:** 1 WARNING, 2 INFO

---

## 2. Drift Analysis (Phase 2)

### Drift Register

#### DRIFT-1 [WARNING] · CI Pre-pull Duplication
- **Файлы:** `.github/workflows/platform-test.yml:169-181` vs DevPlan §Wave 4 (lines 484-489)
- **Суть:** CI workflow УЖЕ содержит «Pre-pull Docker images (parallel)» шаг, который пуллит все образы в фоне. DevPlan Wave 4 предлагает добавить `make docker-pull-all` target — но его основное применение (CI pre-pull) уже реализовано.
- **Expected:** `make docker-pull-all` добавляется как convenience target для локальной разработки, CI шаг остаётся без изменений.
- **Actual:** DevPlan предлагает модифицировать CI workflow (line 488: «Добавить шаг `make docker-pull-all` перед `make gate MODE=full`»), но этот шаг уже существует в виде shell-цикла.
- **Fix:** Уточнить в DevPlan: `make docker-pull-all` — target для локального использования; в CI — заменить inline shell-цикл на вызов `make docker-pull-all` для устранения дублирования логики.

#### DRIFT-2 [HIGH] · `FIXTURE_WAVE` Mapping — Hardcoded vs Claimed Dynamic
- **Файлы:** DevPlan lines 375-403 vs lines 289-290 (утверждение: «зависимости извлекаются из core/modules/*/module.yaml»)
- **Суть:** DevPlan утверждает, что wave-зависимости извлекаются динамически из `module.yaml#depends_on`, но предлагаемый код `pytest_collection_modifyitems` (lines 380-386) использует **хардкоженный** словарь `FIXTURE_WAVE`:
  ```python
  FIXTURE_WAVE = {
      "redis_compose": 0, "nginx_compose": 0, ...
      "pgbouncer_up": 1, "litellm_up": 1, ...
      "hermes_up": 2, "platform_services": 3,
  }
  ```
- **Expected:** `FIXTURE_WAVE` вычисляется из `module_graph` (который строится из `module.yaml#depends_on`). Формула: wave(module) = max(wave(dep) for dep in module.depends_on) + 1.
- **Actual:** Хардкоженный словарь с 12 записями — drift-risk при добавлении/удалении модулей.
- **Fix:** Заменить хардкоженный `FIXTURE_WAVE` на вычисление из `module_graph`. `conftest.py` уже импортирует `module_graph` из `_conftest.audit`. Код должен выглядеть:
  ```python
  # Build fixture→wave mapping from module_graph (from module.yaml depends_on)
  _module_wave = {}
  for module_name, deps in module_graph.items():
      _module_wave[module_name] = max((_module_wave.get(d, -1) for d in deps), default=-1) + 1
  # Map module names to fixture names
  FIXTURE_WAVE = {
      f"{name}_up": wave for name, wave in _module_wave.items()
  }
  ```

#### DRIFT-3 [MEDIUM] · `make docker-pull-all` Not Registered in Manifest
- **Файлы:** DevPlan line 487 vs `core/entrypoint-manifest.yaml`
- **Суть:** Новый Makefile target `docker-pull-all` не упомянут в плане регистрации в `entrypoint-manifest.yaml`. Согласно инварианту Makefile §1 и core/AGENTS.md, каждый `.PHONY` target должен быть зарегистрирован.
- **Expected:** `docker-pull-all` добавлен в `allowed_verbs` и имеет запись в manifest.
- **Actual:** DevPlan не упоминает регистрацию в manifest.
- **Fix:** Добавить в DevPlan Wave 4: регистрация `docker-pull-all` в `entrypoint-manifest.yaml` (секция `allowed_verbs` + описание).

#### DRIFT-4 [MEDIUM] · Marker Registration in pyproject.toml
- **Файлы:** DevPlan lines 470-471, 504 vs `pyproject.toml:51-63`
- **Суть:** DevPlan корректно указывает регистрацию markers `requires_fresh_state` и `wave` в pyproject.toml (Wave 3 #18, Wave 5 #27). Текущий pyproject.toml (line 51-63) не содержит этих markers.
- **Expected:** Добавлены строки:
  ```
  "requires_fresh_state: test requires clean container state (restart before test)",
  "wave: wave-pipeline test ordering marker (0|1|2|3)",
  ```
- **Status:** ✅ CORRECT — DevPlan предусматривает эту регистрацию. Закрывается при реализации.

#### DRIFT-5 [INFO] · Module Dependencies Confirmed
- **Файлы:** DevPlan §2.3 (lines 124-129) vs `core/modules/*/module.yaml`
- **Проверка:** Граф зависимостей в DevPlan соответствует реальным `depends_on` в module.yaml:
  - `hermes-agent`: [nginx, postgres, redis, litellm] ✅
  - `litellm`: [postgres] ✅
  - `postgres`: [] (no depends_on) ✅
- **Status:** ✅ CONFIRMED — граф зависимостей корректен.

### Cross-File Value Mismatches

#### MISMATCH-1 [LOW] · COMPOSE_PROFILES: 13 Modules
- `Makefile:30`: `postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page`
- `.github/workflows/platform-test.yml:71`: идентичный список
- **Status:** ✅ CONSISTENT — оба содержат 13 модулей в одинаковом порядке.

### Contract Violations

**Module contract checks (core/modules/AGENTS.md):**
- Все 14 Docker-модулей имеют `module.yaml`, `docker-compose.base.yml`, `healthcheck.sh`, `Makefile`, `.dockerignore` → ✅
- `platform-secrets` — system-модуль, имеет `module.yaml` с `install_type: system` → ✅

### Summary
| Severity | Count | IDs |
|----------|-------|-----|
| CRITICAL | 0 | -- |
| HIGH | 1 | DRIFT-2 |
| MEDIUM | 2 | DRIFT-3, DRIFT-4 |
| WARNING | 1 | DRIFT-1 |
| INFO | 2 | DRIFT-5, MISMATCH-1 |

---

## 3. Invariant Verification (Phase 3)

### Invariant Table

| # | Invariant (from AGENTS.md) | Status | Evidence | Risk if Violated |
|---|---------------------------|--------|----------|------------------|
| 1 | **Makefile — единый фасад.** Все операции через `make <target>`. | ✅ HELD | `Makefile:1-41` — delegates to `makefiles/*.mk` | -- |
| 2 | **Модель деплоя: git push → CI.** | ✅ HELD | Не затрагивается DevPlan | -- |
| 3 | **org = context.** | ✅ HELD | Не затрагивается | -- |
| 4 | **AGENTS.md — 3 канонических файла.** | ✅ HELD | Не затрагивается | -- |
| 5 | **core/entrypoint-manifest.yaml — YAML-реестр.** | ⚠️ AT_RISK | DevPlan добавляет `docker-pull-all` target без регистрации в manifest (DRIFT-3) | Нарушение manifest-integrity gate |
| 6 | **make bootstrap-node — строго идемпотентный.** | ✅ HELD | Не затрагивается | -- |
| 7 | **Полный локальный стек через `docker compose up` на macOS.** | ⚠️ AT_RISK | Wave-Pipeline вводит `threading.Thread` для фонового старта контейнеров — поведение на macOS Docker Desktop (QEMU, resource contention) не валидировано. `platform_services` уже использует `ThreadPoolExecutor` (line 751), но Wave-Pipeline добавляет долгоживущий фоновый поток за пределами `with ThreadPoolExecutor`. | macOS-специфичные hang/deadlock в тестах |
| 8 | **LiteLLM — PostgreSQL во всех окружениях.** | ✅ HELD | Не затрагивается | -- |
| 9 | **Тестовый сервер может быть пересоздан заново.** | ✅ HELD | Не затрагивается | -- |
| 10 | **Сборка образов hermes.** | ✅ HELD | Не затрагивается | -- |

### Additional Invariant Checks (core/modules/AGENTS.md)

| # | Invariant | Status | Evidence |
|---|----------|--------|----------|
| M1 | module.yaml — source of truth метаданных | ✅ HELD | `module_graph` fixture уже читает `depends_on` из module.yaml |
| M2 | docker-compose.test.yml: `container_name: <c>-test` | ⚠️ AT_RISK | Wave-Pipeline переиспользует контейнеры через foreign guard — `container_name` должен оставаться `<c>-test` во всех compose-файлах |
| M3 | `restart: "no"` в test-compose | ✅ HELD | Не затрагивается — stop/start (Фаза 3) не меняет restart policy |

### Summary
| Status | Count |
|--------|-------|
| HELD | 10 |
| VIOLATED | 0 |
| AT_RISK | 2 (Invariant 5 — manifest registration, Invariant 7 — macOS concurrency) |
| UNVERIFIABLE | 0 |

---

## 4. Test Quality (Phase 4)

### Coverage Gaps

#### GAP-1 [HIGH] · No Gate Test for Wave-Pipeline Infrastructure
- **Суть:** Phase 5 вводит новую архитектурную подсистему (`wave_pipeline.py`, `threading.Event`, background thread, `pytest_collection_modifyitems`), но ни одного gate-теста для неё не предусмотрено.
- **Риск:** Регрессия Wave-Pipeline logic (event never set, thread hang, ordering violation) не будет поймана CI.
- **Рекомендация:** Добавить `test_gate_wave_pipeline.py`:
  - Тест: `_init_wave_events(3)` создаёт 4 Event (waves 0-3)
  - Тест: `_ensure_wave_ready` блокируется на `_wave_ready[N].wait()` и разблокируется после `signal_wave_ready(N)`
  - Тест: `pytest_collection_modifyitems` assigns correct wave based on fixture dependencies
  - Тест: timeout срабатывает (event.wait(timeout=1) → fail с диагностикой)

#### GAP-2 [MEDIUM] · No Test for `reuse_or_start()` Idempotency
- **Суть:** `tests/_conftest/reuse.py` — cornerstone модуль Фазы 1+2, но unit-тесты отсутствуют.
- **Рекомендация:** Добавить `tests/unit/test_reuse.py`:
  - Тест: `check_foreign_containers()` с мокнутым `docker inspect` output
  - Тест: `reuse_or_start()` — reuse path (skip compose up/down)
  - Тест: `reuse_or_start()` — fresh path (compose up → yield → compose down)

#### GAP-3 [MEDIUM] · No Test for `@requires_fresh_state` Marker Semantics
- **Суть:** Wave 3 добавляет marker `requires_fresh_state` и autouse fixture `_reset_fresh_state`. Семантика marker → restart service не покрыта тестом.
- **Рекомендация:** Добавить тест в `tests/unit/test_state_reset.py`:
  - Тест: `restart_service("postgres")` вызывает `docker compose restart postgres`

### Fragile Tests

- `test_nginx_error_page` — маркирован как macOS skip (bind-mount permissions). Статус: валидный skip, не stale.
- `test_nginx_tls_cert_san` — macOS skip (mkcert paths). Статус: валидный skip.
- `test_hermes_api_completions` — 60s timeout на LLM-вызов. Статус: documented in DevPlan §6 (Wave 2 tests).

### Skip Rate
- 2 известных macOS skip (nginx_error_page, nginx_tls_cert) + 1 langfuse_ingestion (HTTP 500) = **3 skip из 100 тестов → 3%**
- Все skip обоснованы и не stale (<90 дней).
- **Статус:** ✅ Приемлемо.

### Summary
| Метрика | Значение |
|---------|----------|
| Invariant coverage | 10/10 invariants HELD, 0 explicitly tested (gap) |
| Gate test presence (новые модули) | 0/3 (reuse, state_reset, wave_pipeline) |
| Skip rate | 3% (все обоснованные) |
| Fragility index | 0 stale skips |
| Test health score | **72/100** (-10 GAP-1, -8 GAP-2, -8 GAP-3, -2 misc) |

---

## 5. Runtime Validation (Phase 5)

**Пропущен** — пре-имплементационный QA. Runtime validation выполняется после реализации каждой Wave.

### Acceptance Criteria Pre-Verification

| # | Критерий | Pre-Status | Оценка реализуемости |
|---|----------|-----------|---------------------|
| AC1 | 0 ERRORS в едином прогоне | ❓ TBD | ✅ Реализуемо — Фаза 1 (foreign guard) + расширение `_STALE_CONTAINER_NAMES` |
| AC2 | 97 pass + 2 skip + 1 macOS skip | ❓ TBD | ✅ Реализуемо — при условии корректного foreign guard |
| AC3 | Время прогона ≤200s | ⚠️ AT_RISK | ⚠️ Зависит от неспецифицированной оптимизации `test_platform_starts_all_containers` (122s→30s). См. SEM-1. |
| AC4 | Тестовые функции не изменены | ⚠️ AT_RISK | ⚠️ Wave 3 добавляет `@pytest.mark.requires_fresh_state` на тестовые функции (файлы 20-21). Технически это изменение декоратора, не тела функции — но валидировать нужно. |
| AC5 | `make gate MODE=full` зелёный | ❓ TBD | ✅ Реализуемо |
| AC6 | `make docker-pull-all` работает | ❓ TBD | ✅ Реализуемо |
| AC7 | Foreign guard на всех 7 фикстурах | ❓ TBD | ✅ Реализуемо |
| AC8 | Тесты упорядочены по волнам | ❓ TBD | ✅ Реализуемо |
| AC9 | Fault isolation | ❓ TBD | ⚠️ Требует ручного тестирования — не автоматизировано. Риск: regressions не ловятся CI. |

---

## 6. Config Sync Audit (Phase 6)

### Env Variable Propagation Chain

| Variable | .env.example | CI (platform-test.yml) | conftest.py (SMOKE_ENV) | Status |
|----------|:---:|:---:|:---:|:---:|
| COMPOSE_PROFILES | ✅ | ✅ (hardcoded) | N/A (Makefile export) | ✅ SYNCED |
| HERMES_DASHBOARD_PASSWORD | ✅ | ✅ (secrets) | ✅ (testpass) | ✅ SYNCED |
| LITELLM_MASTER_KEY | ✅ | ✅ (secrets) | ✅ (sk-test-key) | ✅ SYNCED |
| CONTEXT_IMAGE | N/A | ✅ (override) | N/A (ENV_HERMES) | ✅ CORRECT |

### Compose Override Consistency

- `docker-compose.base.yml` → `docker-compose.test.yml` → `docker-compose.macos.yml`: override chain корректен.
- Wave-Pipeline добавляет compose stop/start вместо down/up — не затрагивает override chain.

### Docker Network Consistency

- Platform networks: `shared-db-net`, `proxy-net`, `shared-cache-net`, `hermes-agent-net`, `observability-net`
- Test networks: `test-shared-db-net`, `test-shared-cache-net`
- Все определены в test fixtures и pre-created через `ensure_external_networks` → ✅

### Manifest Parity

- `docker-pull-all` — новый target, **не зарегистрирован** в `entrypoint-manifest.yaml#allowed_verbs` → ❌ DRIFT-3
- Все остальные целевые модификации Makefile — валидны.

---

## Семантический анализ (Semantic Issues)

### SEM-1 [HIGH] · Неспецифицированная оптимизация `test_platform_starts_all_containers`

- **Локация:** DevPlan line 656: «При оптимизации test_platform_starts_all_containers (122s → 30s): 240s → ~210s ≤ 200s.»
- **Суть:** AC3 (≤200s) **зависит** от сокращения этого теста с 122s до 30s. Но DevPlan **не содержит** описания КАК эта оптимизация будет выполнена. Без неё прогнозное время — 240s, что на 20% выше target.
- **Impact:** Если оптимизация невозможна или сложнее ожидаемого, AC3 недостижим.
- **Fix:** Добавить в DevPlan конкретный план оптимизации `test_platform_starts_all_containers`:
  - Либо: исключить из метрики (вынести в отдельный маркер `@pytest.mark.slow`), переопределив AC3 как «≤200s для тестов без `slow` маркера»
  - Либо: описать механизм оптимизации (например, reuse platform_services вместо перезапуска полного стека)

### SEM-2 [WARNING] · Wave-Pipeline timing зависит от macOS vs Linux

- **Локация:** DevPlan §6 (Data Flow, lines 642-655)
- **Суть:** Pipeline gain (120s) предполагает, что тесты выполняются пока контейнеры стартуют. Но тайминги валидированы только на macOS (baseline 500s). На Linux CI контейнеры стартуют быстрее → меньше overlap → меньше gain.
- **Impact:** Pipeline gain на Linux может быть меньше ожидаемого. AC3 всё ещё достижим (240s vs 200s target), но margin меньше.
- **Рекомендация:** При верификации Wave 5 измерить время на обоих платформах.

### SEM-3 [LOW] · DOCUMENT_PLAN говорит «3 фазы», DevPlan описывает 5

- **Локация:** DevPlan line 20 (`$START_DOCUMENT_PLAN` → «Оптимизационный дизайн (3 фазы)») vs lines 136-409 (5 фаз)
- **Суть:** `DOCUMENT_PLAN` устарел — план расширился до 5 фаз, но метаданные не обновлены.
- **Fix:** Обновить `DOCUMENT_PLAN` до «Оптимизационный дизайн (5 фаз)».

### SEM-4 [INFO] · Risk Register покрывает основные риски

- 8 рисков с митигациями — хорошее покрытие.
- Дополнительный риск: CI timeout (сейчас 40 мин = 2400s, тесты ≤200s — margin достаточный). Не добавлять.

### SEM-5 [INFO] · Rollback Plan корректен

- Каждая wave независима, откат через `git revert <wave-commit>`.
- Wave 5 additive — удаление `wave_pipeline.py` + `pytest_collection_modifyitems` возвращает к post-Wave-4 поведению.

---

## TRAP Verification

### Active TRAPs in Scope

| TRAP | File:Line | Type | Relevance |
|------|-----------|------|-----------|
| `TRAP[BUG]` smoke.py:694 | stale containers safety net | BUG | ✅ — Wave 1 расширяет список |
| `TRAP[BUG]` test_smoke_postgres.py:70 | test compose network override | BUG | ✅ — Не затрагивается |
| `TRAP[BUG]` test_component_pgbouncer.py:76 | COMPOSE_PROFILES missing | BUG | ✅ — Уже исправлен |
| `TRAP[BUG]` test_smoke_redis.py:113 | test compose network override | BUG | ✅ — Не затрагивается |
| `TRAP[DECISION]` test_component_hermes.py:317 | macOS wait-timeout 120s | DECISION | ℹ️ — Wave-Pipeline может изменить тайминги |
| `TRAP[BUG]` test_component_hermes.py:795 | shifted port 19119 | BUG | ℹ️ — Foreign guard должен учитывать shifted ports |

### TRAP[DEBT] Candidates

Рекомендую создать при реализации:

- **[DEBT] `_STALE_CONTAINER_NAMES` — hardcoded list** → заменить на `docker ps -a --filter name=-test --format '{{.Names}}'` для динамического обнаружения stale-контейнеров. См. F2.

---

## Семантический вердикт

| Критерий | Оценка |
|----------|--------|
| Статический аудит | ✅ 14/14 PASS |
| Drift анализ | ⚠️ 1 HIGH, 2 MEDIUM |
| Инварианты | ⚠️ 2 AT_RISK |
| Test Quality | ⚠️ 3 coverage gaps (score 72/100) |
| Config Sync | ⚠️ 1 manifest registration gap |
| Acceptance Criteria | ⚠️ AC3 — зависит от неспецифицированной оптимизации |

### Итоговый вердикт: **DRIFTED (WARNING)**

**DevPlan 040 НЕ СОДЕРЖИТ блокирующих проблем и может быть реализован.** Однако перед началом кодирования требуется:

1. **[HIGH] DRIFT-2:** Заменить хардкоженный `FIXTURE_WAVE` на динамическое вычисление из `module_graph` (module.yaml `depends_on`).
2. **[HIGH] SEM-1:** Специфицировать механизм оптимизации `test_platform_starts_all_containers` (122s → 30s) — без этого AC3 математически недостижим.
3. **[MEDIUM] DRIFT-3:** Добавить регистрацию `docker-pull-all` в `entrypoint-manifest.yaml`.

Рекомендации (опционально, не блокируют):
- **[MEDIUM] GAP-1:** Добавить gate-тест для Wave-Pipeline инфраструктуры
- **[WARNING] F2:** Рассмотреть динамическое удаление `*-test` контейнеров вместо хардкоженного списка

### Project Health Score: **79/100**

```
100 - 5 (DRIFT-2 HIGH) - 3 (DRIFT-3 MEDIUM) - 3 (DRIFT-4 MEDIUM)
    - 5 (Inv 5 AT_RISK) - 5 (Inv 7 AT_RISK) = 79
```

---

## Handoff Protocol

Для исправления DRIFT-2, SEM-1 и DRIFT-3 рекомендуется делегировать доработку DevPlan архитектору:

```
task(subagent_type="Architect", description="Fix DevPlan 040 gaps",
  prompt="Review VerificationReport at .ai/plans/040-docker-test-optimization/03-VerificationReport.md.
  Fix 3 issues in DevPlan 040:
  1. DRIFT-2: Replace hardcoded FIXTURE_WAVE with dynamic computation from module_graph
  2. SEM-1: Specify test_platform_starts_all_containers optimization mechanism (122s→30s)
  3. DRIFT-3: Add docker-pull-all to entrypoint-manifest.yaml
  Update DevPlan.md in place, preserving existing structure.")
```

$END_VERIFICATION_REPORT
