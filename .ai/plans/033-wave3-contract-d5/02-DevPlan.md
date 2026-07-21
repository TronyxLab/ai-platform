# 033-DevPlan: Wave 3 — Contract Strengthening D5

**Wave:** 3 (Contract Strengthening D5) программы `027-architecture-modernization-program`
**Source brief:** `.ai/plans/027-architecture-modernization-program/01-Brief.md` §5 (Wave 3)
**Source analysis:** `reports/architecture-analysis-2026-07-21.md` §4.4 (Option A — D5-контракт), verified 2026-07-21
**Prior waves:** Wave 1 (`.ai/plans/028-wave1-immediate/`) — IMPLEMENTED; Wave 2 (`.ai/plans/029-wave2-dangerous/`) — IMPLEMENTED
**Pre-flight verification (principle 9 — Read before Act):** выполнена 2026-07-21, см. §1.4

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Усилить модульный контракт D4 → D5: типизированные env_requires, enforced обязательные секреты (`${VAR:?}` где применимо), restart-drift детекция между module.yaml и compose, AGE_SECRET_KEY в .env.example. Закрыть проблемы P06, P07, P08 матрицы. Сделать контракт machine-enforced (CI gate red при нарушении), а не только задокументированным.
DESCRIPTION:           Пять эпиков: (W3-E1) создать `core/internal/scripts/validate_module_yaml.py` — typed Python-валидатор D5-контракта поверх jsonschema; (W3-E2) добавить AGE_SECRET_KEY в `.env.example`; (W3-E3) ввести `${VAR:?error}` для критичных секретов с РАЗРЕШЕНИЕМ КОНФЛИКТА DD3 (см. §2); (W3-E4) `restart: no` enforcement во всех 13 test-compose; (W3-E5) Makefile target `validate-modules` + регистрация в `core/entrypoint-manifest.yaml` + CI gate. Подход: Strangler-Fig (Wave 1 §1 брифа), Tier-1 извлечение — новый Python-модуль вместо inline python3. Существующие 14 module.yaml остаются обратно-совместимыми (D5 расширяет D4, не ломает).
RATIONALE:             Бриф Wave 3 §5 устанавливает D5 как целевой контракт. Анализ §4.4 отчёта (Option A, score 8/10) рекомендует typed env_requires + `${VAR:?}` + restart-drift detection.jsonschema уже в deps (principle 8 — расширение существующего, не дублирование). КРИТИЧЕСКОЕ ПРОТИВОРЕЧИЕ: бrief W3-E3 требует `${VAR:?error}` в compose, но `core/modules/AGENTS.md` запрет #6 + DD3 явно запрещает `${VAR:?error}` в `docker-compose.base.yml` (причина: `docker compose config` валидирует ВСЕ include'd файлы, включая неактивные profiles → silent-fail на CI для несобранных модулей). ✅ КОНФЛИКТ КОЛЛАПСИРОВАН ОПЕРАТОРОМ 2026-07-21 → Option A (полная отмена DD3): замена `${VAR}` → `${VAR:?error}` в base.yml + снятие запрета #6 + обновление CI compose-invocations. См. §2.3.
ACCEPTANCE_CRITERIA:
  AC-1 (W3-E1): `core/internal/scripts/validate_module_yaml.py` существует, имеет MODULE_CONTRACT + GREP_SUMMARY + LDD-логи [IMP:7-10]. Запуск `python3 core/internal/scripts/validate_module_yaml.py --all` после фиксов W3-E2/E3/E4 возвращает exit 0. Запуск с `--schema-strict` детектирует D4-нарушения (negative-test). Unit-тесты в `tests/test_validate_module_yaml.py` покрывают: каждый тип env_var (string/secret/int/bool), required:true/false, restart-drift detection, `${VAR:?}` detection в compose (в рамках выбранной опции §2). Покрытие ≥85%.
  AC-2 (W3-E1): `core/schemas/module.schema.json` расширен до D5 (env_requires: array of objects с type+required; backward-compat: bare strings валидны со значением по умолчанию `{type: secret, required: true}`). Документация обновлена в `core/modules/AGENTS.md` §module.yaml — D5 контракт.
  AC-3 (W3-E2): `.env.example` содержит `AGE_SECRET_KEY=` в секции Platform secrets с комментарием о fails-closed boot и TRAP[DECISION] о критичности (P06 закрыт). Синхронизирован в `.env` через существующий gate `env-example-sync`.
  AC-4 (W3-E3): ✅ COLLAPSED → Option A. Все 4 критичных секрета (`POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD`, `LITELLM_MASTER_KEY`, `MINIO_ROOT_PASSWORD`) используют `${VAR:?error message}` в `docker-compose.base.yml` (7 файлов). Запрет #6 в `core/modules/AGENTS.md` снят с TRAP[DECISION]. CI compose-invocations обновлены (экспорт `COMPOSE_PROFILES` перед `docker compose config`; `--skip-check-profiles` НЕ СУЩЕСТВУЕТ). `rg '\$\{(POSTGRES_PASSWORD|CLICKHOUSE_PASSWORD|LITELLM_MASTER_KEY|MINIO_ROOT_PASSWORD)\}' core/modules/*/docker-compose.base.yml` без `:?` = 0. `make gate MODE=fast` зелёный, `docker compose config` exit 0.
  AC-5 (W3-E4): Все 13 `core/modules/*/docker-compose.test.yml` имеют верхнеуровневое `restart: "no"` или per-service `restart: "no"` на каждом сервисе. `rg '^restart:' core/modules/*/docker-compose.test.yml` — 13/13 файлов имеют `restart: "no"`. Test-isolation gate `test_gate_compose_restart_consistency.py` (новый) — проверяет test-compose на `no`, base на `unless-stopped` (P08 закрыт).
  AC-6 (W3-E5): Makefile target `validate-modules` существует и вызывает `python3 core/internal/scripts/validate_module_yaml.py --all`. Зарегистрирован в `core/entrypoint-manifest.yaml` (validate section). Вызывается в CI workflow `platform-test.yml` и `push-gate.yml` после lint-шага. Gate red при нарушении D5 — `tests/gates/test_gate_module_yaml_contract.py` расширен D5-проверками.
  AC-7 (Regression): `make gate MODE=fast` зелёный. Существующие D4-гейты (`test_gate_module_schema_d4.py`, `test_gate_module_yaml_contract.py`) продолжают проходить (D5 — надмножество D4). Все 14 module.yaml валидны против расширенной схемы.
IMPLEMENTS:            Brief 027 §5 (Wave 3 эпики W3-E1..E5). Report 2026-07-21 §4.4 Option A. AGENTS.md invariant 4 (канонические AGENTS.md). core/modules/AGENTS.md §module.yaml D4 контракт (эволюция → D5). Principle 8 (AI-First Architecture — типизированные публичные контракты). Principle 6 (Small Simple Blocks — расширение существующего валидатора/jsonschema вместо нового фреймворка). Principle 9 (Read before Act — pre-flight §1.4).
IMPACTS:               **New Python:** `core/internal/scripts/validate_module_yaml.py` (~250-350 строк), unit-тесты `tests/test_validate_module_yaml.py`. **Schema:** `core/schemas/module.schema.json` (D4 → D5, backward-compat). **AGENTS.md (core/modules):** §module.yaml — обновлён до D5; запрет #6 — пересмотрен в зависимости от опции §2. **Compose:** 4 файла с критичными секретами (postgres, clickhouse, litellm, minio base.yml) — в зависимости от опции §2; 13 test-compose (restart: no). **.env.example / .env:** добавление AGE_SECRET_KEY. **Makefile:** новый target `validate-modules`. **entrypoint-manifest.yaml:** регистрация validate-modules. **CI workflows:** `platform-test.yml`, `push-gate.yml` — вызов validate-modules. **Tests:** расширение `tests/gates/test_gate_module_yaml_contract.py` (D5-чеки), новый negative-test `test_gate_module_yaml_contract_d5_negative.py` (R5 anti-survivorship). **TRAP[DECISION]:** в `core/modules/AGENTS.md` — фиксация коллапсированной опции §2.
REQUIRES:              Чистый working tree (проверить `git status`). Прочитанные: `reports/architecture-analysis-2026-07-21.md` §4.4, `.ai/plans/027-architecture-modernization-program/01-Brief.md` §5, `core/modules/AGENTS.md` §module.yaml + запрет #6, существующий `core/schemas/module.schema.json` (D4). Зависимости: Wave 1 (честные тесты для regression-валидации), Wave 2 (опционально — не блокирует). ✅ КОЛЛАПС суперпозиции §2 выполнен оператором 2026-07-21 → Option A (полная отмена DD3).
$END_ARTIFACT_CONTRACT

---

## 1. Контекст и pre-flight (Read before Act)

### 1.1. Цель Wave 3

Превратить D4-контракт модулей (декларативный, но не enforced) в D5-контракт (machine-enforced через CI gate + typed schema). Закрыть 3 проблемы матрицы:

| ID  | Категория        | Sev     | Текущее состояние (verified 2026-07-21)                                              |
|-----|------------------|---------|--------------------------------------------------------------------------------------|
| P06 | SECURITY         | 🟠 HIGH | `AGE_SECRET_KEY` отсутствует в `.env.example` (verified: 0 вхождений в `.env.example`, но присутствует в `core/modules/platform-secrets/module.yaml` env_requires) |
| P07 | ERROR_HANDLING   | 🟠 HIGH | `${VAR:?error}` = 0 мест в compose (DD3 explicit). Критичные секреты как raw `${VAR}`: postgres, clickhouse, litellm, minio, backup-cron, infra-metrics, langfuse (7 файлов, verified) |
| P08 | MODULE_CONTRACT  | 🟠 HIGH | `restart:` в test-compose: 7/13 файлов имеют EXPLICIT `restart: unless-stopped` (backup-cron, infra-metrics×5, langfuse×2, litellm, logging×2, monitoring×2, nginx) — НАРУШЕНИЕ (test-isolation требует `no`); 6/13 MISSING — inherit `unless-stopped` от base — тоже НАРУШЕНИЕ; monitoring имеет `restart: "no"` для init service — правильный пример |

### 1.2. Текущая модель D4 (source of truth)

`core/schemas/module.schema.json` (D4):
- `env_requires`: array of strings — bare names без type/required semantics
- `resources`, `spool_dir`, `spool_volume`, `env_shared`, `interfaces`, `depends_on` — без restart-field

`core/modules/AGENTS.md` §module.yaml D4 + запрет #6: `${VAR:?error}` в `docker-compose.base.yml` запрещён (DD3 rationale: `docker compose config` валидирует include'd файлы с неактивными profiles → silent-fail при отсутствующей переменной).

### 1.3. Целевая модель D5 (из брифа + отчёта §4.4)

- `env_requires`: array of objects `{name, type: string|secret|int|bool, required: bool}` (backward-compat: bare string = `{type: secret, required: true}`)
- `${VAR:?error}` enforced для required+secret критичных vars
- `restart` field в module.yaml; cross-check с compose (drift detection)
- AGE_SECRET_KEY в `.env.example`

### 1.4. Pre-flight verification (выполнено 2026-07-21)

| Проверка                                              | Результат                                                                            |
|-------------------------------------------------------|--------------------------------------------------------------------------------------|
| Wave 1 implemented (yaml_query.py, args.sh, gate_helpers.py, honesty.py, _negative tests) | ✅ verified: `core/internal/scripts/yaml_query.py`, `core/lib/args.sh`, `tests/helpers/gate_helpers.py` существуют |
| Wave 2 implemented (ssh.sh, setup-platform composite, audit_logging.sh)               | ✅ verified: `core/lib/ssh.sh`, `.github/actions/setup-platform/`, `core/lib/audit_logging.sh` существуют |
| `validate_module_yaml.py` отсутствует                                                  | ✅ confirmed: не реализован (W3-E1 — новая работа)                                   |
| D4 schema в `core/schemas/module.schema.json`                                          | ✅ exists, 70 строк, `env_requires: array of strings`                                |
| 14 module.yaml используют D4 string-array env_requires                                 | ✅ verified: все 14 файлов. С непустыми env_requires: postgres, litellm, clickhouse, minio, langfuse, hermes-agent, monitoring, status-page, backup-cron, infra-metrics, platform-secrets. С пустыми env_requires (`env_requires: []`): redis, logging. Без module.yaml (не Docker-модуль): nginx — проверяется отдельно gate'ом D4. |
| `${VAR:?}` count в core/modules/*/docker-compose.base.yml                              | ✅ 0 (соответствует DD3)                                                             |
| `restart:` в test-compose                                                              | ✅ verified: 7/13 имеют `restart: unless-stopped` (backup-cron, infra-metrics×5, langfuse×2, litellm, logging×2, monitoring×2, nginx) — НАРУШАЕТ P08 (test-isolation требует `no`); 6/13 не имеют restart (inherit от base = `unless-stopped`) — тоже нарушение |
| AGE_SECRET_KEY в `.env.example`                                                        | ✅ verified: 0 вхождений (P06 подтверждён)                                           |
| jsonschema в deps                                                                      | ✅ (template_engine.py уже использует)                                               |
| Конфликт DD3 ↔ W3-E3                                                                   | ⚠️ ОБНАРУЖЕН: требует SUPERPOSITION §2 перед реализацией                             |

---

## 2. SUPERPOSITION: разрешение конфликта DD3 ↔ W3-E3 (BLOCKER)

### 2.1. Конфликт

- **W3-E3 бриф** (`.ai/plans/027-.../01-Brief.md` строка 223): «Заменить raw `${POSTGRES_PASSWORD}`, `${CLICKHOUSE_PASSWORD}`, `${LITELLM_MASTER_KEY}`, `${MINIO_ROOT_PASSWORD}` → `${VAR:?error message}` во всех compose-файлах».
- **DD3** (`core/modules/AGENTS.md` строка 167): «Все `${VAR:?error}` заменены на `${VAR:-}` — `docker compose config` валидирует все include'd файлы, даже неактивные profiles».
- **Запрет #6** (`core/modules/AGENTS.md` строка 187): «`${VAR:?error}` в `docker-compose.base.yml` — Блокирует валидацию неактивных profiles (DD3)».

### 2.2. Варианты

```
## SUPERPOSITION: DD3 ↔ W3-E3 conflict resolution

### Option A: "Полная отмена DD3 — `${VAR:?}` в base.yml" [оценка ниже]
Approach: Заменить все raw `${CRITICAL_SECRET}` → `${CRITICAL_SECRET:?error message}` в 
  docker-compose.base.yml. Снять запрет #6 в AGENTS.md. Изменить CI-вызовы `docker compose 
  config` → экспорт `COMPOSE_PROFILES` (флаг `--skip-check-profiles` НЕ СУЩЕСТВУЕТ) 
  (если доступно в установленной версии compose v2.x). Обосновать в TRAP[DECISION] 
  в core/modules/AGENTS.md изменение инварианта.
Trade-offs: +Fail-fast при отсутствии секрета (P07 закрыт полностью, как в брифе). 
  −Требует изменение compose-invocation по всему CI + Makefile (ripple effect). 
  −Возможна несовместимость со старыми compose v1. −Нарушает действующий запрет #6 — 
  требует обоснования reversal. −R-RISK: CI ломается для модулей, чьи секреты 
  осознанно не заданы в dev/CI окружении (например, LITELLM_LICENSE).
Best when: Оператор готов принять ripple-cost и явный reversal запрета #6.

### Option B: "Static validator вместо compose-syntax" [score: 8/10] ★
Approach: НЕ трогать compose-синтаксис (остаётся `${VAR:-}` per DD3). Валидатор 
  validate_module_yaml.py делает static-cross-check: для каждого env_requires{type:secret, 
  required:true} в module.yaml → проверяет (а) наличие в `.env.example`, (б) наличие 
  в `secrets-manifest.yaml`, (в) non-empty default в `.env.example`. Соответствие 
  compose↔module.yaml проверяется grep'ом `${VAR` без `:-` (детект raw-reference = 
  warning, не error). Запрет #6 сохраняется, DD3 остаётся в силе.
Trade-offs: +Zero изменения compose/CI/Makefile — нет ripple. +D5-контракт enforced 
  через валидатор (не через runtime compose-fail). +Сохраняет существующую семантику 
  compose-config validation. −Fail происходит на CI-gate, а не на `docker compose up` 
  (мягче, чем `${VAR:?}`). −Не буквально букве брифа W3-E3, но достигает цели P07 
  (mandatory-arg guard). +Принцип 6 (Small Simple Blocks — статический анализ вместо 
  runtime-side-effect). +Принцип 8 (расширение существующего валидатора вместо нового 
  контракта).
Best when: Хочется закрыть P07 без reversal запрета #6 и без ripple-cost. ★ РЕКОМЕНДУЕТСЯ.

### Option C: "Dual-overlay — `${VAR:?}` только в test-compose" [score: 6/10]
Approach: base-overlay остаётся `${VAR:-}` (DD3 сохраняется). Test-overlay 
  (docker-compose.test.yml) использует `${VAR:?error}` для критичных секретов — 
  test-compose активируется только с явным profile, поэтому compose-config не 
  валидирует неактивные. Запрет #6 уточняется: «`${VAR:?error}` запрещён в base.yml, 
  разрешён в test.yml».
Trade-offs: +Закрывает P08 (test-compose детектирует отсутствующие секреты на CI). 
  −Не закрывает P07 для production-runtime (base-overlay остаётся мягким). 
  −Двойной стандарт (base vs test) увеличивает поверхность дрейфа. 
  −Test-compose может не содержать всех критичных секретов, которые есть в base.
Best when: Если приоритет — fail-fast в CI, но не на production-VPS.
```

### 2.3. Коллапс суперпозиции (зафиксирован оператором 2026-07-21)

**✅ COLLAPSED → Option A: Полная отмена DD3** (выбор оператора через `question` tool, 2026-07-21).

**Обоснование выбора:** закрытие P07 «по букве» брифа W3-E3 — runtime-fail-fast при отсутствии секрета, а не мягкий статический анализ. Принимается ripple-cost (обновление CI compose-invocations + reversal запрета #6).

**План реализации Option A в W3-E3:**

1. Заменить raw `${VAR}` → `${VAR:?error message}` для 4 критичных секретов в 7 файлах `docker-compose.base.yml`:
   - `POSTGRES_PASSWORD` → postgres, backup-cron, infra-metrics, langfuse
   - `CLICKHOUSE_PASSWORD` → clickhouse, langfuse
   - `LITELLM_MASTER_KEY` → litellm
   - `MINIO_ROOT_PASSWORD` → minio
2. Снять запрет #6 в `core/modules/AGENTS.md`; DD3 rationale пометить как superseded с TRAP[DECISION] (дата, rationale reversal, revert-path).
3. Обновить CI compose-invocations: экспортировать `COMPOSE_PROFILES` из `platform-env.yaml` перед каждым вызовом `docker compose config` (CI workflows, Makefile, entrypoint scripts). Механизм: `export COMPOSE_PROFILES="postgres,clickhouse,litellm,minio,backup-cron,infra-metrics,langfuse,logging,monitoring,nginx,redis,status-page,hermes-agent,platform-secrets"` — перечень берётся из `docker compose config --services` на корневом compose. Альтернатива: `docker compose --profile <name> --profile <name> ... config` с явным перечислением всех профилей. N.B.: флаг `--skip-check-profiles` НЕ СУЩЕСТВУЕТ в Docker Compose (verified docker compose v5.3.0 `--help`); использовать нельзя.
4. Regression: `make gate MODE=fast` зелёный; manual `docker compose config` НЕ падает на неактивных profiles (verify после fix CI-invocation).
5. В `.env.example` оставить все 4 секрета с non-empty test-значениями (уже есть, verified 2026-07-21) — чтобы `docker compose config` на CI не падал.

**Revert-path (для TRAP):** при обнаружении несовместимости compose v1 / legacy-VPS — `git revert <merge-commit>` + восстановление raw `${VAR}` + запрет #6 в AGENTS.md + удаление `COMPOSE_PROFILES` экспорта из CI workflows.

**Acceptance AC-4 (Option A):** Все 4 критичных секрета в 7 файлах base.yml используют `${VAR:?error message}`. Запрет #6 в AGENTS.md снят с TRAP[DECISION]. `rg '\$\{(POSTGRES_PASSWORD|CLICKHOUSE_PASSWORD|LITELLM_MASTER_KEY|MINIO_ROOT_PASSWORD)\}' core/modules/*/docker-compose.base.yml` без `:?` = 0. `make gate MODE=fast` зелёный. `COMPOSE_PROFILES="<all-profiles>" docker compose config` (на CI с обновлённым invocation) exit 0.

---

## 3. Draft Code Graph (XML)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<CodeGraph program="033-wave3-contract-d5">

  <!-- ============ W3-E1: validate_module_yaml.py ============ -->
  <Entity name="validate_module_yaml_py" type="FILE">
    <TYPE>Python script, standalone CLI</TYPE>
    <Path>core/internal/scripts/validate_module_yaml.py</Path>
    <keywords>D5-validator, jsonschema, env_requires-typed, restart-drift, static-cross-check</keywords>
    <annotation>
      ## @purpose D5-контракт валидатор для module.yaml (strangler Tier-1 extraction)
      ## @scope CLI + importable functions; consumes core/schemas/module.schema.json (D5)
      ## @invariants
      ##   - Exit 0 = все 14 module.yaml валидны по D5-контракту
      ##   - Exit 1 = обнаружено нарушение, подробности в stderr + LDD [IMP:9]
      ##   - backward-compat: bare-string env_requires = {type: secret, required: true}
      ## @rationale jsonschema уже в deps; template_engine.py демонстрирует pattern
    </annotation>
    <CrossLinks>
      <link target="module_schema_json" relation="validates-against"/>
      <link target="secrets_manifest_yaml" relation="cross-checks-env"/>
      <link target="env_example" relation="cross-checks-presence"/>
      <note>YAML parsing: uses `import yaml` directly (library, not yaml_query.py CLI).
            yaml_query.py is a Wave 1 CLI tool for grep-like YAML queries — not an importable library.
            validate_module_yaml.py imports `yaml` directly (already in pyproject.toml deps).</note>
    </CrossLinks>
  </Entity>

  <Entity name="validate_module_yaml_FUNC_load_module" type="FUNC">
    <TYPE>def load_module(path: Path) -> dict</TYPE>
    <BelongsTo>validate_module_yaml_py</BelongsTo>
    <keywords>load-yaml, normalize</keywords>
    <annotation>Loads module.yaml, normalizes env_requires bare-strings → objects</annotation>
  </Entity>

  <Entity name="validate_module_yaml_FUNC_validate_schema" type="FUNC">
    <TYPE>def validate_schema(module: dict, schema_path: Path) -> list[str]</TYPE>
    <BelongsTo>validate_module_yaml_py</BelongsTo>
    <keywords>jsonschema, structural-check</keywords>
    <annotation>Returns list of violations (empty = OK)</annotation>
  </Entity>

  <Entity name="validate_module_yaml_FUNC_check_env_requires_presence" type="FUNC">
    <TYPE>def check_env_requires_presence(module: dict, env_example: Path, secrets_manifest: Path) -> list[str]</TYPE>
    <BelongsTo>validate_module_yaml_py</BelongsTo>
    <keywords>P07-static-cross-check, required-secret-presence</keywords>
    <annotation>
      Для каждого env_requires{required:true}: (а) проверяет presence в .env.example,
      (б) non-empty значение или marker, (в) если type:secret — presence в secrets-manifest.
      Реализует P07-цель без compose-синтаксис изменения (Option B §2).
    </annotation>
  </Entity>

  <Entity name="validate_module_yaml_FUNC_check_restart_drift" type="FUNC">
    <TYPE>def check_restart_drift(module: dict, compose_base: Path) -> list[str]</TYPE>
    <BelongsTo>validate_module_yaml_py</BelongsTo>
    <keywords>P08-restart-drift, compose-cross-check, base-only</keywords>
    <annotation>
      Проверяет module.yaml `restart` против `docker-compose.base.yml` restart per-service.
      Возвращает violation при расхождении. Охват: base-compose только.
      ⚠️ НЕ проверяет test-compose `restart: "no"` — это ответственность gate-теста
      `test_gate_compose_restart_consistency.py` (H2), не валидатора module.yaml.
      Разделение: валидатор = module.yaml ↔ base-compose drift; gate = test-compose enforcement.
    </annotation>
  </Entity>

  <Entity name="validate_module_yaml_FUNC_main" type="FUNC">
    <TYPE>def main(argv: list[str]) -> int</TYPE>
    <BelongsTo>validate_module_yaml_py</BelongsTo>
    <keywords>CLI-dispatch, argparse, exit-code</keywords>
    <annotation>argparse: --all | --module NAME | --schema-strict; LDD [IMP:9] summary</annotation>
  </Entity>

  <!-- ============ W3-E1: schema D5 extension ============ -->
  <Entity name="module_schema_json" type="FILE">
    <TYPE>JSON Schema draft-07</TYPE>
    <Path>core/schemas/module.schema.json</Path>
    <keywords>module-yaml, D5, schema, env_requires-typed</keywords>
    <annotation>
      ## @purpose D5 schema — typed env_requires + optional restart field
      ## @changes D4 → D5: env_requires items = string OR object{type,required};
      ##   added optional `restart` field; backward-compat via oneOf
    </annotation>
    <CrossLinks>
      <link target="validate_module_yaml_py" relation="consumed-by"/>
      <link target="test_gate_module_schema_d4_py" relation="validated-by-legacy-gate"/>
    </CrossLinks>
  </Entity>

  <!-- ============ W3-E2: AGE_SECRET_KEY ============ -->
  <Entity name="env_example" type="FILE">
    <TYPE>dotenv template</TYPE>
    <Path>.env.example</Path>
    <keywords>AGE_SECRET_KEY, platform-secrets, fails-closed</keywords>
    <annotation>
      ## @changes + AGE_SECRET_KEY= в секции Platform secrets, с CONSTRAINT-комментарием
      ##   и TRAP[DECISION] о fails-closed boot
    </annotation>
    <CrossLinks>
      <link target="env_FILE" relation="mirrored-to"/>
      <link target="test_gate_env_example_sync_py" relation="enforced-by-gate"/>
    </CrossLinks>
  </Entity>

  <!-- ============ W3-E4: restart: no test-compose ============ -->
  <Entity name="test_compose_restart_fix" type="FILESET">
    <TYPE>13 docker-compose.test.yml files</Type>
    <Path>core/modules/{backup-cron,clickhouse,hermes-agent,infra-metrics,langfuse,litellm,logging,minio,monitoring,nginx,postgres,redis,status-page}/docker-compose.test.yml</Path>
    <keywords>restart-no, test-isolation, P08</keywords>
    <annotation>
      ## @purpose Все 13 test-compose имеют верхнеуровневое `restart: "no"`
      ## @rationale test-isolation — zombie-container prevention after test failure
    </annotation>
  </Entity>

  <!-- ============ W3-E5: Makefile + manifest + CI ============ -->
  <Entity name="validate_modules_target" type="MAKEFILE_TARGET">
    <TYPE>.PHONY target</TYPE>
    <Path>Makefile → core/entrypoints/validate.sh --modules (or direct python invocation)</Path>
    <keywords>validate-modules, CI-gate, D5-enforcement</keywords>
    <annotation>
      ## @purpose CI-callable target для D5-валидации
      ## @scope thin wrapper — delegates to validate_module_yaml.py
    </annotation>
    <CrossLinks>
      <link target="validate_module_yaml_py" relation="delegates-to"/>
      <link target="entrypoint_manifest_yaml" relation="registered-in"/>
    </CrossLinks>
  </Entity>

  <Entity name="entrypoint_manifest_yaml" type="FILE">
    <TYPE>YAML registry</TYPE>
    <Path>core/entrypoint-manifest.yaml</Path>
    <annotation>## @changes + validate-modules entry в validate section</annotation>
  </Entity>

  <!-- ============ Tests ============ -->
  <Entity name="test_validate_module_yaml_py" type="FILE">
    <TYPE>pytest unit-tests</TYPE>
    <Path>tests/test_validate_module_yaml.py</Path>
    <annotation>
      ## @scope unit-тесты для каждой функции валидатора; ≥85% coverage
      ## @invariants LDD [IMP:9-10] в каждом test, caplog trajectory printed
    </annotation>
  </Entity>

  <Entity name="test_gate_module_yaml_contract_d5_negative_py" type="FILE">
    <TYPE>pytest gate-test (R5 anti-survivorship)</TYPE>
    <Path>tests/gates/test_gate_module_yaml_contract_d5_negative.py</Path>
    <annotation>
      ## @purpose R5: detect D5 violation (missing required field, wrong type, drift)
      ## @rationale каждый gate-test с bug-ID → имеет _negative companion
    </annotation>
  </Entity>

  <Entity name="test_gate_compose_restart_consistency_py" type="FILE">
    <TYPE>pytest gate-test</TYPE>
    <Path>tests/gates/test_gate_compose_restart_consistency.py</Path>
    <annotation>
      ## @purpose Gate: verify test-compose has restart: "no", base-compose has unless-stopped
      ## @rationale Separate from test_restart_consistency.py (Makefile semantic check)
      ##   — compose-file restart audit is a distinct domain
    </annotation>
  </Entity>

</CodeGraph>
```

---

## 4. Эпики и шаги реализации

### W3-E1: validate_module_yaml.py + D5 schema

**Порядок:**

1. **Расширить `core/schemas/module.schema.json` до D5** (backward-compat):
   - `env_requires.items`: oneOf [string, object{type,required}]
   - добавить optional `restart: string` field (enum: `[always, unless-stopped, no, on-failure]`)
   - title/description → "Module Descriptor (D5)"
   - additionalProperties остаётся `true` для forward-compat
   - **Design decision (`restart` field scope):** `restart` в module.yaml — модуль-уровневый атрибут, применяется ко ВСЕМ сервисам модуля. Для multi-service модулей с разной restart-политикой (например, monitoring: prometheus = `unless-stopped`, init = `no`) — `restart` в module.yaml описывает основной production-сервис, исключения документируются в комментарии module.yaml и проверяются валидатором как carve-out.
   - **Схема:** `restart: string` (enum: `[always, unless-stopped, no, on-failure]`), optional, уровень top-level module.yaml (не per-service — YAML-структура module.yaml не имеет per-service подобъектов, все сервисы делят один docker-compose.base.yml)
   - **Валидатор:** `check_restart_drift(module, compose_base)` проверяет module.yaml `restart` против per-service restart в compose. Если compose содержит несколько разных restart-политик для разных сервисов — валидатор должен либо (a) проверять что `restart` в module.yaml совпадает с restart основного сервиса, либо (b) требовать документирования в комментарии. Реализация: простой cross-check, для сложных multi-service случаев — manual review carve-out.

2. **Создать `core/internal/scripts/validate_module_yaml.py`**:
   - `load_module(path)` → загружает YAML, нормализует env_requires bare-strings → objects с default `{type: secret, required: true}`
   - `validate_schema(module, schema_path)` → jsonschema.validate, возвращает list ошибок
   - `check_env_requires_presence(module, env_example, secrets_manifest)` → для `{required:true}` проверяет presence в `.env.example` и `secrets-manifest.yaml` (Option B §2)
   - `check_restart_drift(module, compose_base)` → если module.yaml имеет `restart`, cross-check с per-service restart в `docker-compose.base.yml`. Возвращает violation при расхождении. Охват: ТОЛЬКО base-compose. Test-compose `restart: "no"` enforcement — в отдельном gate-тесте `test_gate_compose_restart_consistency.py`.
   - `main(argv)` → argparse `--all` / `--module NAME` / `--schema-strict`; LDD [IMP:7-10] через `print` + stderr; exit 0/1
   - Размер: ~250-350 строк (в пределах Tier-1 extraction)

3. **Unit-тесты `tests/test_validate_module_yaml.py`** (native pytest, no subprocess.run per testing rule):
   - test each env_var type (string/secret/int/bool)
   - test required:true/false
   - test restart-drift detection (positive + negative)
   - test backward-compat (bare-string env_requires)
   - test AGE_SECRET_KEY presence-check
   - caplog trajectory printed, IMP:9 log asserted (per testing rule)

4. **Расширить `core/modules/AGENTS.md` §module.yaml** — D5 контракт документация (type/required semantics, restart field, backward-compat note).

**Acceptance:** AC-1, AC-2.

### W3-E2: AGE_SECRET_KEY в .env.example

1. В `.env.example` секцию Platform secrets добавить:
   ```
   # AGE_SECRET_KEY — master age-key для SOPS-расшифровки platform-secrets.
   # ⚠️ REQUIRED (env_requires of platform-secrets module) — без него systemd oneshot fails-closed.
   # Генерация: age-keygen -o keys.txt → извлечь публичный/приватный ключ
   # ⚠️ NOT for production .env — только SOPS-encrypted secrets на VPS.
   AGE_SECRET_KEY=
   ```
2. Синхронизировать в `.env` через существующий gate `env-example-sync` (или вручную, если gate не покрывает).
3. Проверить `rg "AGE_SECRET_KEY" .env.example` — 1 вхождение.

**Acceptance:** AC-3.

### W3-E3: `${VAR:?}` enforcement (✅ Option A — COLLAPSED 2026-07-21)

✅ Суперпозиция коллапсирована оператором → **Option A: полная отмена DD3** (см. §2.3).

**Порядок реализации:**

1. **Заменить raw `${VAR}` → `${VAR:?error message}`** для 4 критичных секретов в base.yml (точные файлы после grep):
   - `POSTGRES_PASSWORD` → postgres, backup-cron, infra-metrics, langfuse (DATABASE_URL, CLICKHOUSE_MIGRATION_URL с встраиванием)
   - `CLICKHOUSE_PASSWORD` → clickhouse, langfuse
   - `LITELLM_MASTER_KEY` → litellm
   - `MINIO_ROOT_PASSWORD` → minio
   - **⚠️ Внимание на URL-встраивания:** `postgresql://...:${POSTGRES_PASSWORD}@...` и `clickhouse://...?password=${CLICKHOUSE_PASSWORD}&...` — `${VAR:?error message}` внутри URL: символ `?` в error-message конфликтует с query-string delimiter URL. Решение: использовать ТОЛЬКО alphanumeric + underscore в error-message: `${POSTGRES_PASSWORD:?PG_PASSWORD_REQUIRED}` и `${CLICKHOUSE_PASSWORD:?CH_PASSWORD_REQUIRED}`. Избегать пробелов, `?`, `&`, `=`, `#` в тексте ошибки внутри URL.
   - Конкретные места замены (URL-embedded vars):
     * backup-cron, infra-metrics, langfuse: `DATABASE_URL: "postgresql://...:${POSTGRES_PASSWORD}@..."` → `${POSTGRES_PASSWORD:?PG_PASSWORD_REQUIRED}`
     * langfuse: `CLICKHOUSE_MIGRATION_URL: "clickhouse://...?password=${CLICKHOUSE_PASSWORD}&..."` → `${CLICKHOUSE_PASSWORD:?CH_PASSWORD_REQUIRED}`
   - **⚠️ Исключения:** переменные со fallback-by-design (например, `LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY: "${S3_SECRET_KEY:-${MINIO_ROOT_PASSWORD:-dummy}}"`) — НЕ трогать, оставить каскадный fallback.

2. **Снять запрет #6 в `core/modules/AGENTS.md`** + DD3 rationale:
   - заменить строку 187 запрет #6 на: `~~6~~ | ~~${VAR:?error} в docker-compose.base.yml~~ | **REVERSED 2026-07-21 (TRAP[DECISION])** — Option A collapse Wave 3 DevPlan 033; COMPOSE_PROFILES export in CI workflows`
   - обновить DD3 (строка 167): пометить `~~DD3~~ superseded 2026-07-21 — see TRAP[DECISION]`
   - добавить новый TRAP[DECISION] блок:
     ```
     # ⚠️ TRAP[DECISION] · 2026-07-21 · HI · DD3 reversed — `${VAR:?error}` now enforced in base.yml
     # · Rejected: static-validator-only (Option B, score 8/10) — operator chose runtime-fail-fast per W3-E3 brief letter
     # · Reason: P07 closed by compose-runtime enforcement, not CI-gate-only. Acceptable ripple-cost: CI invocation update.
     # · Implementation: 4 critical secrets use ${VAR:?...} in 7 base.yml files; 
     #   CI exports COMPOSE_PROFILES="<all-profiles>" before every `docker compose config` call.
#   ⚠️ --skip-check-profiles does NOT exist in Docker Compose (verified v5.3.0).
     # · Revert-path: git revert <merge-commit> + restore raw ${VAR} + restore запрет #6.
     # · Rev: if CI compose-v2 incompatibility discovered → fall back to Option B (static validator).
     ```

3. **Обновить CI compose-invocations** (НЕ использовать `--skip-check-profiles` — флаг НЕ СУЩЕСТВУЕТ в Docker Compose):
   - ⚠️ `--skip-check-profiles` не существует ни в одной версии Docker Compose (verified v5.3.0 `docker compose --help`). Использовать `COMPOSE_PROFILES=... docker compose config`.
   - Экспортировать `COMPOSE_PROFILES` из `platform-env.yaml` во всех CI workflow (platform-test.yml, push-gate.yml) ПЕРЕД любым вызовом `docker compose config`
   - Полный список всех затронутых `docker compose config` вызовов (ripple effect — 10+ мест):
     * CI: `.github/workflows/push-gate.yml` — compose config reference
     * Tests: `tests/test_smoke_platform.py` (test_all_compose_configs_valid)
     * Tests: `tests/test_predeploy_gate.py` (test_project_compose_configs_valid, `--dry-run`)
     * Tests: `tests/test_hermes_l2_fallback.py` (compose config `--images`, 2 вызова)
     * Tests: `tests/gates/test_gate_local_stack.py` (compose config fallback)
     * Scripts: `core/internal/deploy/deploy-project.sh` line 718 — `docker compose config` для image pattern
     * Scripts: `core/internal/bootstrap/deploy-modules.sh` lines 462, 501, 537 — `config --images`, `config --services`, `config --format json`
     * Scripts: `core/internal/scaffold/adopt-project.sh` line 390 — compose config для network validation
   - **Стратегия:** добавить `export COMPOSE_PROFILES="${COMPOSE_PROFILES:-$(make -s _get_all_profiles)}"` в начало скриптов, использующих `docker compose config`. Для CI — установить `COMPOSE_PROFILES` env var на уровне workflow/job.
   - **Regression:** `make gate MODE=fast` зелёный; `COMPOSE_PROFILES=<all> docker compose config` exit 0
   - **⚠️ IMPORTANT:** если тест/скрипт вызывает `docker compose config` с `--profile` флагом явно — COMPOSE_PROFILES не оверрайдит явные флаги; их поведение остаётся без изменений. Проверить: `deploy-modules.sh` (проверка hermes-agent) — использует `--profile "$module_name" config --images` с явным профилем, не требует COMPOSE_PROFILES.

4. **Проверить `.env.example`** — все 4 критичных секрета имеют non-empty test-значения (verified 2026-07-21: `POSTGRES_PASSWORD=test-pg-pwd`, `CLICKHOUSE_PASSWORD=test-clickhouse-pwd-not-for-prod`, `LITELLM_MASTER_KEY=sk-ci-test-master-key`, `MINIO_ROOT_PASSWORD=minioadmin`). Если CI-окружение не загружает `.env.example` автоматически — verify что `--env-file .env.example` передаётся или что CI имеет эквивалентные vars.

5. **Regression run:** `make gate MODE=fast`. Если падает на compose-config — iterate шаг 3.

**Acceptance:** AC-4.

### W3-E4: restart: no в test-compose

1. Для каждого из 13 файлов `core/modules/*/docker-compose.test.yml`:
   - предпочтительный подход: верхнеуровневое `restart: "no"` под `services:` (compose v2+ поддерживает deploy-level restart default через `x-default-restart` anchor, но test-compose проще: добавить `restart: "no"` в каждую service-секцию ЯВНО — избегает multi-service ambiguity и compose-version несовместимости)
   - **7 модулей с EXPLICIT `unless-stopped`:** backup-cron, infra-metrics (5 services), langfuse (2), litellm (1), logging (2), monitoring (2 — prometheus + grafana; init уже имеет `"no"`), nginx (1) — заменить `restart: unless-stopped` → `restart: "no"`
   - **6 модулей без restart поля:** clickhouse, hermes-agent, minio, postgres, redis, status-page — добавить `restart: "no"` в каждую service-секцию
   - **monitoring init service:** уже имеет `restart: "no"` (line 35) — оставить как есть, пример правильного контракта
   - **ВАЖНО:** не использовать YAML anchors/aliases между разными файлами (cross-file anchors не поддерживаются большинством compose-реализаций). Каждый файл самодостаточен.

2. Создать новый gate `tests/gates/test_gate_compose_restart_consistency.py` (НЕ расширять существующий test_restart_consistency.py — см. finding H2):
   - название файла: `test_gate_compose_restart_consistency.py` (отражает новую ответственность — compose-file restart audit, не Makefile restart)
   - проверка #1 (test-compose): для каждого `core/modules/*/docker-compose.test.yml` → все сервисы имеют `restart: "no"`
   - проверка #2 (base-compose): для каждого `core/modules/*/docker-compose.base.yml` → все сервисы имеют `restart: unless-stopped` ИЛИ `restart: always` для severity:critical (drift detection)
   - использовать `@pytest.mark.gate` декоратор (gate registration protocol)
   - зарегистрировать в `core/entrypoint-manifest.yaml` gates section (триединое соответствие: файл + маркер + manifest)
   - LDD trajectory через `tests/_conftest/ldd.py`
   - rationale: существующий `test_restart_consistency.py` проверяет Makefile restart семантику (soft vs hard), новое требование проверяет compose-file restart — это разные домены, разумно разделить файлы (см. finding H2)

3. Проверить: `rg '^restart:' core/modules/*/docker-compose.test.yml` — 13/13 файлов.

**Acceptance:** AC-5.

### W3-E5: Makefile target + manifest + CI gate

1. **Makefile** — добавить target (в блок validate):
   ```makefile
   .PHONY: validate-modules
   validate-modules:
   ## D5 module contract validator (Wave 3, W3-E5)
   	@python3 core/internal/scripts/validate_module_yaml.py --all
   ```
   (tab-indentation critical)

2. **`core/entrypoint-manifest.yaml`** — добавить в `validate:` section + `allowed_verbs`:
   В секцию `validate:`:
   ```yaml
     - make_target: validate-modules
       mechanism: python-script
       delegates_to: core/internal/scripts/validate_module_yaml.py --all
       description: "D5 module.yaml contract validator (typed env_requires, restart-drift, secret-presence)"
   ```
   В секцию `allowed_verbs` (line 548): добавить `  - validate-modules` (алфавитный порядок: после `validate` и перед `verify`).
   ⚠️ БЕЗ регистрации в `allowed_verbs` gate `test_gate_manifest_integrity.py` будет RED — валидирует bidirectional соответствие targets ↔ allowed_verbs.

3. **CI workflows** (`platform-test.yml`, `push-gate.yml`):
   - добавить шаг `make validate-modules` после lint, перед gate-tests
   - использовать `setup-platform` composite (Wave 2) для окружения

4. **Расширить gate `tests/gates/test_gate_module_yaml_contract.py`**:
   - существующие D4-чеки сохраняются
   - добавить D5-чеки: presence of validate_module_yaml.py, schema-version check, calling validator on all modules
   - LDD trajectory

5. **Создать `tests/gates/test_gate_module_yaml_contract_d5_negative.py`** (R5 anti-survivorship):
   - ⚠️ Gate registration protocol (tests/gates/AGENTS.md): файл в tests/gates/ + `@pytest.mark.gate` декоратор + manifest-запись. Пропуск любого из трёх = gate не запускается в `make gate`.
   - подаёт валидатору intentionally-broken module.yaml (missing type, wrong type, restart-drift)
   - assert: валидатор возвращает violations (не empty)
   - использует декоратор `@pytest.mark.gate` на каждой test-функции
   - issue: "033-wave3-contract-d5"
   - LDD trajectory через `tests/_conftest/ldd.py`

6. **Регистрация в `core/entrypoint-manifest.yaml` gates section**:
   ```yaml
     - id: module-yaml-contract-d5-negative
       description: "R5 companion: detect D5 violations (missing type, wrong type, restart-drift)"
       test_file: test_gate_module_yaml_contract_d5_negative.py
       issue: "033-wave3-contract-d5"
   ```

**Acceptance:** AC-6.

### Regression & Production Gate

1. `make gate MODE=fast` — зелёный (все существующие D4-гейты проходят + новые D5-чеки).
2. `make test MARKER=static` — зелёный.
3. `make validate-modules` — exit 0 после фиксов W3-E2/E3/E4.
4. Manual smoke: `docker compose config` не падает на неактивных profiles (для Option B — обязательно).
5. Pre-push gate (`make gate MODE=fast`) зелёный перед коммитом.

**Acceptance:** AC-7.

---

## 5. Step-by-Step Data Flow

```
[Operator подтверждает §2 опцию]
        │
        ▼
[W3-E1.1: расширить module.schema.json D5]
        │
        ▼
[W3-E1.2: создать validate_module_yaml.py] ──► imports `yaml` directly (stdlib-like);
        │                                       module.schema.json via `json.load()`;
        │                                       jsonschema via `import jsonschema`
        ▼
[W3-E1.3: unit-тесты tests/test_validate_module_yaml.py]
        │
        ├──► caplog trajectory printed (testing rule)
        ├──► IMP:9 log asserted
        │
        ▼
[W3-E1.4: документация core/modules/AGENTS.md §module.yaml D5]
        │
        ▼
[W3-E2: AGE_SECRET_KEY в .env.example + .env sync]
        │
        ├──► проверка rg "AGE_SECRET_KEY" .env.example
        │
        ▼
[W3-E3: enforcement — В ЗАВИСИМОСТИ ОТ ОПЦИИ §2]
        │
        ├──► Option B: встроено в W3-E1 + TRAP[DECISION] в AGENTS.md
        ├──► Option A: compose-syntax fix + DD3 reversal
        └──► Option C: test-overlay ${VAR:?}
        │
        ▼
[W3-E4: restart: "no" в 13 test-compose]
        │
        ├──► создание test_gate_compose_restart_consistency.py gate
        │
        ▼
[W3-E5.1: Makefile validate-modules target]
        │
        ▼
[W3-E5.2: регистрация в entrypoint-manifest.yaml]
        │
        ▼
[W3-E5.3: CI workflow integration (platform-test.yml, push-gate.yml)]
        │
        ▼
[W3-E5.4: расширение test_gate_module_yaml_contract.py + D5 _negative companion]
        │
        ▼
[Regression: make gate MODE=fast зелёный]
        │
        ▼
[Production release: commit + push → CI verify → merge]
```

---

## 6. File Manifest

### New files
| Path | Type | Purpose |
|------|------|---------|
| `core/internal/scripts/validate_module_yaml.py` | Python script | D5-валидатор (W3-E1) |
| `tests/test_validate_module_yaml.py` | pytest unit | Unit-тесты валидатора (W3-E1) |
| `tests/gates/test_gate_module_yaml_contract_d5_negative.py` | pytest gate | R5 anti-survivorship (W3-E5) |
| `tests/gates/test_gate_compose_restart_consistency.py` | pytest gate | Compose restart consistency gate (W3-E4) |

### Modified files
| Path | Type of change | Purpose |
|------|----------------|---------|
| `core/schemas/module.schema.json` | extend (D4 → D5, backward-compat) | Typed env_requires + restart field (W3-E1) |
| `core/modules/AGENTS.md` | extend §module.yaml + (опционально) запрет #6 | D5 документация + TRAP[DECISION] (W3-E1, W3-E3) |
| `.env.example` | add `AGE_SECRET_KEY=` | P06 fix (W3-E2) |
| `.env` | mirror `.env.example` | env-example-sync gate (W3-E2) |
| `core/modules/*/docker-compose.test.yml` (13 files) | add `restart: "no"` | P08 fix (W3-E4) |
| `Makefile` | add `validate-modules` target | W3-E5 |
| `core/entrypoint-manifest.yaml` | add validate-modules + new gate | W3-E5 |
| `.github/workflows/platform-test.yml` | add `make validate-modules` step | W3-E5 |
| `.github/workflows/push-gate.yml` | add `make validate-modules` step | W3-E5 |
| `tests/gates/test_gate_module_yaml_contract.py` | extend D5 checks | W3-E5 |
| `tests/gates/test_gate_compose_restart_consistency.py` | NEW — compose-file restart audit (test-compose: no, base: unless-stopped) | W3-E4 |
| `tests/test_inventory.yaml` | add new test entries | sync after test addition |

### Conditionally modified (depends on §2 collapse)
| Path | Option A | Option B | Option C |
|------|----------|----------|----------|
| `core/modules/postgres/docker-compose.base.yml` | `${VAR:?}` | unchanged | unchanged |
| `core/modules/clickhouse/docker-compose.base.yml` | `${VAR:?}` | unchanged | unchanged |
| `core/modules/litellm/docker-compose.base.yml` | `${VAR:?}` | unchanged | unchanged |
| `core/modules/minio/docker-compose.base.yml` | `${VAR:?}` | unchanged | unchanged |
| `core/modules/backup-cron/docker-compose.base.yml` | `${VAR:?}` | unchanged | unchanged |
| `core/modules/infra-metrics/docker-compose.base.yml` | `${VAR:?}` | unchanged | unchanged |
| `core/modules/langfuse/docker-compose.base.yml` | `${VAR:?}` | unchanged | unchanged |
| `core/modules/*/docker-compose.test.yml` (4 crit secrets) | unchanged | unchanged | `${VAR:?}` |

**Additionally modified under Option A (compose config invocation update, NOT compose content):**

| Path | Change | Purpose |
|------|--------|---------|
| `.github/workflows/push-gate.yml` | add `COMPOSE_PROFILES` env | compose config без silent-fail |
| `.github/workflows/platform-test.yml` | add `COMPOSE_PROFILES` env | compose config без silent-fail |
| `core/internal/deploy/deploy-project.sh` | add `COMPOSE_PROFILES` before line 718 | image pattern extraction |
| `core/internal/bootstrap/deploy-modules.sh` | review lines 462, 501, 537 | verify `--profile` flag takes precedence over env |
| `core/internal/scaffold/adopt-project.sh` | add `COMPOSE_PROFILES` before line 390 | network validation |
| `tests/test_smoke_platform.py` | add `COMPOSE_PROFILES` in test setup | test_all_compose_configs_valid |
| `tests/test_predeploy_gate.py` | add `COMPOSE_PROFILES` in test setup | test_project_compose_configs_valid |
| `tests/gates/test_gate_local_stack.py` | add `COMPOSE_PROFILES` in test setup | compose config fallback |
| `tests/test_hermes_l2_fallback.py` | review — uses explicit `--profile` flag | may NOT need COMPOSE_PROFILES |
| `Makefile` | add `COMPOSE_PROFILES` export in gate target | local parity with CI |

---

## 7. Risk Register (Wave 3-specific)

| ID | Risk | L | I | Mitigation |
|----|------|---|---|------------|
| **W3-R1** | Конфликт DD3 ↔ W3-E3 не коллапсирован → блокирует реализацию | H | H | §2 SUPERPOSITION с auto-collapse → Option B; `question` tool для подтверждения оператора ДО старта W3-E3 |
| **W3-R2** | D5 schema ломает backward-compat с bare-string env_requires | M | H | oneOf в JSON Schema: `string OR object`; unit-test на backward-compat (W3-E1.3) |
| **W3-R3** | Валидатор находит 10+ нарушений в существующих module.yaml | H | L | (из брифа R-RISK-3) Зафиксировать как technical-debt-tracking, не блокирующий merge валидатора. `--strict` flag опционален |
| **W3-R4** | Изменение CI workflows ломает push-gate | M | H | Тестировать локально через `make gate MODE=fast` перед push; feature-branch с explicit review |
| **W3-R5** | AGENTS.md запрет #6 reversal (Option A) ломает существующий test-compose-config gate | M | H | Regression: `make gate MODE=fast`; при Option B — mitigation не нужна |
| **W3-R5a** | COMPOSE_PROFILES riot — compose config вызывается в >10 местах (tests + scripts + CI), пропуск одного = gate green но production fail | H | H | Список всех вызовов документирован в §4 W3-E3 шаг 3. Добавить `echo "COMPOSE_PROFILES=${COMPOSE_PROFILES:-UNSET}"` в начало каждого скрипта/теста. Локально: `COMPOSE_PROFILES=<all> make gate MODE=fast` перед push |
| **W3-R7** | restart-drift detection даёт false-positive на модулях с разумным расхождением (severity:critical → always vs unless-stopped) | M | M | Валидатор должен учитывать `severity` field module.yaml — `severity: critical` → `restart: always` OK даже если compose говорит `unless-stopped` (documented carve-out) |
| **W3-R8** | AGE_SECRET_KEY добавлен в .env.example, но SOPS-encrypted файл на VPS не имеет — deploy fails | L | M | комментарий в .env.example явно отмечает «NOT for production .env» |

---

## 8. Порядок выполнения

```
[OPERATOR: collapse §2 ✅ ВЫПОЛНЕНО 2026-07-21 → Option A]
            │
            ▼
W3-E1 (schema D5 + validator + unit-tests + AGENTS.md doc) ──► ~5-7 дней
            │
            ├──► W3-E2 (AGE_SECRET_KEY) ──► параллельно, ~1 день
            │
            ▼
W3-E3 (Option A: ${VAR:?} в 7 base.yml + снятие запрета #6 + CI update + TRAP[DECISION]) ──► ~3-5 дней
            │
            ▼
W3-E4 (restart: "no" в 13 test-compose + gate extension) ──► ~2 дня
            │
            ▼
W3-E5 (Makefile + manifest + CI + gate + _negative) ──► ~2-3 дня
            │
            ▼
[Regression: make gate MODE=fast зелёный]
            │
            ▼
[VerificationReport 033 — делегирование в QA через dev-pipeline]
```

**Оценка длительности:** ~5 недель (по брифу §5). Breakdown: W3-E1 = 1.5 нед, W3-E2 = 0.5 нед, W3-E3 (Option A) = 1 нед (compose + CI + AGENTS.md reversal), W3-E4 = 0.5 нед, W3-E5 = 1 нед, regression+verify = 0.5 нед.

---

## 9. Dependencies и валидация зависимостей

| Dependency | Status | Verification |
|------------|--------|--------------|
| Wave 1 (honest tests, gate_helpers, yaml_query.py) | ✅ IMPLEMENTED | `.ai/plans/028-wave1-immediate/03-VerificationReport.md` |
| Wave 2 (ssh.sh, audit, setup-platform) | ✅ IMPLEMENTED | `.ai/plans/029-wave2-dangerous/04-VerificationReport.md` |
| `jsonschema` Python lib | ✅ in deps | template_engine.py уже использует |
| `core/schemas/module.schema.json` (D4) | ✅ exists | 70 строк, D4-контракт |
| Чистый working tree | ☐ CHECK | `git status` перед стартом |

---

## 10. Делегирование в dev-pipeline

После утверждения этого DevPlan:

1. **Coder phase** — реализация W3-E1 → W3-E2 → W3-E3 → W3-E4 → W3-E5 последовательно (см. §8).
2. **QA phase** — `tests/test_validate_module_yaml.py` + `tests/gates/test_gate_module_yaml_contract_d5_negative.py`; regression `make gate MODE=fast`; `03-VerificationReport.md`.
3. **Fix phase** — если QA находит нарушения (W3-R3 — ожидаемо), debt-tracking + iteration.

**Mandatory pre-flight перед каждым эпиком:** `git status` clean, прочитан этот DevPlan, прочитан relevant section брифа 027 §5.

$END_DEVPLAN

---

## Заключение

Wave 3 — Contract Strengthening D5. Закрывает P06 (AGE_SECRET_KEY в .env.example), P07 (mandatory-arg guard через `${VAR:?error}` в base.yml — Option A, по букве брифа), P08 (restart: no в 13 test-compose).

**Ключевое решение зафиксировано:** конфликт DD3 ↔ W3-E3 коллапсирован оператором 2026-07-21 → **Option A: полная отмена DD3**. Замена `${VAR}` → `${VAR:?error message}` в 7 base.yml файлах, снятие запрета #6 в `core/modules/AGENTS.md` с TRAP[DECISION], обновление CI compose-invocations (экспорт `COMPOSE_PROFILES` перед `docker compose config`; `--skip-check-profiles` НЕ СУЩЕСТВУЕТ в Docker Compose).

**Готов к делегированию в dev-pipeline** (Coder → QA → Fix) по порядку выполнения §8: W3-E1 → W3-E2 → W3-E3 (Option A) → W3-E4 → W3-E5 → Regression → VerificationReport 033.

**Pre-flight перед стартом:** `git status` clean; оператор подтверждает «начинай реализацию Wave 3».
