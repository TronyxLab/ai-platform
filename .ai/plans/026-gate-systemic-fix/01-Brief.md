# 026-Brief: Gate failures systemic fix — exception list unification, test-data/schema coherence, allowlist governance, registration friction reduction

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Устранить 4 системные причины дрейфа, порождающие 6 из 8 failures в `make gate MODE=full`: рассинхрон exception-списков между gate-тестами (S1), расхождение тестовых фикстур со схемами (S2), деградация allowlist-механизма до dump-места (S3), и registration friction — отсутствие автоматической проверки полноты регистрации скриптов (S5). Цель: gate-тесты должны падать только на реальные нарушения, а не на дрейф собственной конфигурации.
DESCRIPTION:           Полный gate (1359 тестов, `make gate MODE=full`) выявил 8 failures. Суперпозиционный анализ (S1–S5) показал, что только 2 из них — реальные проблемы (smoke-тесты, macOS overlays — вынесены в отдельный бриф 027). Остальные 6 — false positives или дрейф тестовых данных, порождённые 4 системными причинами:
  S1 — Exception-списки в `test_gate_no_unregistered_entrypoint.py` и `test_gate_dead_code.py` не синхронизированы: `s3-ssl-cache.sh` и `reconcile-projects.sh` добавлены в shebang-exceptions (DevPlan 024/025), но dead-code gate не обновлён.
  S2 — Тестовые фикстуры (`tests/test_data/node.yaml`) не проходят автовалидацию против схем (`core/schemas/node.schema.json`): schema эволюционировала (добавлены `required: [context, modules]`, `owner_key`), test data остались старыми.
  S3 — ALLOWLIST в `test_gate_thin_wrapper.py` стал dump-местом: `converge.sh` (151 LOC, +1) и `context-promote.sh` (135 LOC — проходит лимит) allowlisted без `until`/`reason`. Нет периодического ревью.
  S5 — Добавление скрипта требует ручной регистрации в 4 местах (manifest, Makefile, AGENTS.md, тесты). Нет pre-commit проверки, детектирующей пропущенную регистрацию.
Бриф предлагает 3 волны, закрывающие все 4 системные причины.
RATIONALE:             Gate-тесты — последняя линия обороны перед CI и production. Если gate красный из-за дрейфа собственных exception-списков или устаревших тестовых данных, оператор теряет доверие к gate и начинает игнорировать красные сигналы. Ситуация усугубляется с каждым DevPlan'ом: новые скрипты добавляются в exception-списки, test data не обновляются под schema, allowlist растёт. Без системного решения gate деградирует до «always red — ignore». Текущий момент (8 failures после волны активной разработки 024-025) — оптимальная точка для санации: failures свежие, root causes точно идентифицированы суперпозицией, изменения в коде минимальны.
ACCEPTANCE_CRITERIA:   1. Единый `core/gate-config.yaml` — все три gate-теста (shebang, dead-code, thin-wrapper) читают exception/allowlist из одного источника.  2. `s3-ssl-cache.sh` и `reconcile-projects.sh` больше не детектятся как dead code (exception прозрачно применяется в обоих тестах).  3. `tests/test_data/node.yaml` проходит валидацию против `core/schemas/node.schema.json` на этапе сбора тестов (`pytest_sessionstart`).  4. Тесты `test_extract_node_host_from_yaml`, `test_node_yaml_domain_extraction`, `test_node_yaml_validation[valid-node-yaml]` — зелёные.  5. ALLOWLIST содержит `until`/`reason` для каждой записи; gate-тест падает на просроченных allowlist-записях.  6. `converge.sh` (151 LOC) — либо рефакторен до ≤150, либо имеет явный `until` в allowlist с обоснованием.  7. `make scripts-audit` — проверяет, что каждый shebang-файл зарегистрирован в manifest ИЛИ в exceptions; возвращает ненулевой код при нарушениях.  8. `make gate MODE=fast` — зелёный (6 из 8 failures устранены; оставшиеся 2 smoke-test failures — в брифе 027).  9. Pre-commit hook `scripts-audit` предотвращает коммит незарегистрированных скриптов.
IMPLEMENTS:            Инварианты 1 (Makefile-фасад), 2 (модель деплоя), 4 (AGENTS.md канонические файлы), 7 (полный локальный стек через docker compose). Суперпозиционный анализ S1-S5 (superposition skill, FULL mode, 2026-07-21).
IMPACTS:               core/gate-config.yaml (CREATE: единый конфиг exceptions/allowlist), tests/gates/test_gate_no_unregistered_entrypoint.py (MODIFY: чтение из gate-config.yaml), tests/gates/test_gate_dead_code.py (MODIFY: чтение из gate-config.yaml), tests/gates/test_gate_thin_wrapper.py (MODIFY: чтение из gate-config.yaml, проверка until), tests/conftest.py (MODIFY: pytest_sessionstart autovalidation фикстур), tests/test_data/node.yaml (MODIFY: актуализация под schema), tests/test_bootstrap_auto.py (MODIFY: assert host), tests/test_node_yaml_domains.py (MODIFY: assert domain/email), tests/test_validate.py (MODIFY: valid_node_data fixture), core/entrypoints/converge.sh (MODIFY: опционально — рефакторинг до ≤150 LOC), Makefile (MODIFY: target scripts-audit), core/internal/scripts-audit.sh (CREATE: аудит регистрации скриптов), .pre-commit-config.yaml (MODIFY: hook scripts-audit).
REQUIRES:              Ветка от origin/main, `make gate MODE=fast` зелёный до начала (текущий fast-gate должен быть зелёным; full-gate красный — это ожидаемо и является предметом данного брифа), working tree чистый. Бриф 027 (macOS overlays для litellm/status-page) — отдельно, не блокирует данный бриф.
$END_ARTIFACT_CONTRACT

---

## 1. Диагноз: системные причины 6 из 8 gate failures

### 1.1. Карта failures → системные причины

```
Failure                                          Системная причина
──────────────────────────────────────────────   ──────────────────────
s3-ssl-cache.sh → dead code (false positive)  ──► S1: рассинхрон exception-списков
reconcile-projects.sh → dead code (false +)   ──► S1: рассинхрон exception-списков
s3-ssl-cache.sh → not in manifest             ──► S1: рассинхрон exception-списков
──────────────────────────────────────────────   ──────────────────────
node.yaml → host mismatch (192.168.1.100)     ──► S2: test-data/schema расхождение
node.yaml → domain mismatch (test.local)      ──► S2: test-data/schema расхождение
node.yaml → schema validation fail            ──► S2: test-data/schema расхождение
──────────────────────────────────────────────   ──────────────────────
converge.sh → 151 LOC > 150                   ──► S3: allowlist governance
──────────────────────────────────────────────   ──────────────────────
(будущие failures)                            ──► S5: registration friction
```

Оставшиеся 2 failures (smoke: `status-page-test` не стартует, `litellm` не стартует) — S4 (macOS overlay pattern), отдельный бриф 027.

---

### 1.2. S1: Рассинхрон exception-списков между gate-тестами

**Механизм деградации:**

1. `test_gate_no_unregistered_entrypoint.py` содержит `_SHEBANG_EXCEPTION_PATTERNS` (16 glob-паттернов) — определяет, какие shebang-файлы НЕ требуют регистрации в `entrypoint-manifest.yaml`.
2. `test_gate_dead_code.py` содержит `_EXCEPTION_PREFIXES`, `_EXCEPTION_SUFFIXES`, `_EXCEPTION_PATHS` — определяет, какие internal-скрипты НЕ требуют живого caller'а в call graph.
3. Эти два списка **независимы** и имеют **разную структуру** (glob vs prefix/suffix/exact).
4. Когда `s3-ssl-cache.sh` был добавлен в shebang-exceptions (DevPlan 024, W1 — SSL cache), dead-code gate **не был обновлён**.
5. Когда `reconcile-projects.sh` был добавлен в shebang-exceptions (DevPlan 025, W4 — reconciliation), dead-code gate **не был обновлён**.

**Результат:** dead-code gate детектит оба скрипта как «мёртвый код», хотя они оба живые:
- `s3-ssl-cache.sh` → source-ится из `node-lifecycle.sh` (строка 898) и `issue-cert.sh` (строка 491)
- `reconcile-projects.sh` → source-ится из `converge.sh` (строка 1107) и `node-lifecycle.sh` (строка 664)

**Почему call-graph не находит их:** оба скрипта вызываются через переменные (`local s3_cache="${CORE_DIR}/internal/bootstrap/s3-ssl-cache.sh"`), и парсер `_VAR_SOURCE_RE` в `_build_call_graph` должен их ловить. Но dead-code gate имеет собственный список исключений, в который они не попали.

**Существующие исключения в двух gate-файлах:**

| Файл | Структура | Кол-во записей |
|------|-----------|:---:|
| `test_gate_no_unregistered_entrypoint.py` | `_SHEBANG_EXCEPTION_PATTERNS: list[str]` (glob) | 18 |
| `test_gate_dead_code.py` | `_EXCEPTION_PREFIXES: set`, `_EXCEPTION_SUFFIXES: set`, `_EXCEPTION_PATHS: set` | 9 + 4 + 1 |

**Пересечение:** ~60% паттернов имеют эквиваленты в обоих списках, но не все. Ручная синхронизация при каждом DevPlan'е неизбежно приводит к расхождению.

---

### 1.3. S2: Test-data/schema расхождение

**Механизм деградации:**

1. `core/schemas/node.schema.json` эволюционировал: добавлены `required: ["node", "modules", "context"]`, `node.required: ["name", "host", "owner_key"]`, убран `branch` из разрешённых полей (`additionalProperties: false`).
2. `tests/test_data/node.yaml` — единственный файл `node.yaml` во всём проекте, используемый как тестовая фикстура — **не был обновлён**.
3. Три теста используют эту фикстуру и имеют захардкоженные assertions под старые данные.

**Конкретные расхождения:**

| Поле | Фикстура (tests/test_data/node.yaml) | Требование schema | Тесты, которые падают |
|------|--------------------------------------|-------------------|----------------------|
| `host` | `127.0.0.1` | любое валидное | `test_extract_node_host_from_yaml` (assert `192.168.1.100`) |
| `domain` | `test.example.com` | любое валидное | `test_node_yaml_domain_extraction` (assert `test.local`) |
| `email` | `test@example.com` | любое валидное | `test_node_yaml_domain_extraction` (assert `admin@test.local`) |
| `projects[0].domain` | `test-site.example.com` | любое валидное | `test_node_yaml_domain_extraction` (assert `app.test.local`, `independent-project.com`) |
| `context` | **отсутствует** | **required** | `test_node_yaml_validation[valid-node-yaml]` |
| `modules` | **отсутствует** | **required** | `test_node_yaml_validation[valid-node-yaml]` |
| `node.owner_key` | **отсутствует** | **required** | `test_node_yaml_validation[valid-node-yaml]` |
| `branch` | **присутствует** | **запрещено** (`additionalProperties: false`) | `test_node_yaml_validation[valid-node-yaml]` |

**Почему это не детектилось раньше:** schema эволюционировала в одном из предыдущих DevPlan'ов, но:
- Тесты не запускались на full-gate (только fast-gate с deselected тестами)
- Нет механизма автовалидации фикстур против схем при старте тестов
- `test_node_yaml_validation[valid-node-yaml]` — единственный тест, который должен был бы упасть при любом запуске, но он использует parametrize с `valid_node_data` fixture, и вероятно не запускался

---

### 1.4. S3: Allowlist governance — деградация до dump-места

**Механизм деградации:**

1. `test_gate_thin_wrapper.py` содержит `ALLOWLIST: frozenset[str]` — 6 файлов, исключённых из проверок LOC, function count, binary calls.
2. Путь наименьшего сопротивления: когда файл не проходит gate → добавить в ALLOWLIST вместо рефакторинга.
3. Нет полей `reason` и `until` — нельзя отличить легитимное исключение от технического долга.
4. Нет периодической проверки — allowlist только растёт, никогда не сокращается.

**Текущий ALLOWLIST:**

| Файл | LOC | Лимит | Причина в allowlist | Проблема |
|------|:---:|:---:|---------------------|----------|
| `bootstrap.sh` | 192 | 150 | «T15 refactoring planned» | Нет `until`, план T15 не существует |
| `lint.sh` | 228 | 150 | «external tool orchestrator» | Легитимно — оркестратор |
| `check-doc-headers.sh` | 218 | 150 | «documentation audit utility» | Легитимно — утилита аудита |
| `context-promote.sh` | **135** | 150 | «uses ssh -T for SSH auth detection» | **Проходит лимит!** Allowlisted из-за binary-call проверки, но исключён из всех трёх проверок |
| `deploy-project.sh` | 392 | 150 | «orchestrator entrypoint» | Легитимно — оркестратор деплоя |
| `converge.sh` | **151** | 150 | «1 over limit due to --reconcile + markup» | +1 строка. Allowlisted вместо рефакторинга |

**Проблема `context-promote.sh`:** файл в 135 LOC (проходит лимит 150!) был allowlisted потому что вызывает `ssh -T` (binary-call gate). Но `ALLOWLIST` исключает файл из **всех трёх** проверок (LOC, function count, binary calls). Это over-exclusion: файл должен проверяться на LOC и function count, исключение должно быть только для binary-call проверки.

---

### 1.5. S5: Registration friction — 4 ручных шага без авто-проверки

**Механизм деградации:**

1. Добавление нового скрипта требует ручной регистрации в 4 местах:
   - `core/entrypoint-manifest.yaml` (поле `delegates_to`)
   - `Makefile` (target)
   - `AGENTS.md` (глагол в глоссарии)
   - gate-тесты (исключение или регистрация)
2. Ни один из этих шагов не проверяется pre-commit хуком.
3. Gate-тесты ловят несоответствия post-factum на CI, а не предотвращают на этапе коммита.
4. Результат: каждый DevPlan, добавляющий скрипты, с высокой вероятностью порождает gate failures из-за пропущенной регистрации.

**Существующие pre-commit хуки (из `.pre-commit-config.yaml`):**

| Хук | Что проверяет |
|-----|---------------|
| `check-manifest-parity` | bidirectional sync manifest ↔ Makefile ↔ AGENTS.md |
| `grepsummary` | наличие GREP_SUMMARY в .sh файлах |
| `namelint` | валидация имён target'ов |

**Что отсутствует:** хук `scripts-audit`, который проверяет: каждый shebang-файл → или в manifest, или в exceptions.

---

## 2. Решения (3 волны)

### Волна 1 (P0): Унификация exception/allowlist конфигурации [S1 + S3]

**Проблема:** Два gate-файла с независимыми exception-списками разной структуры. ALLOWLIST без `until`/`reason`. Добавление исключения требует правки в 2-3 местах.

**Решение:**
1. Создать `core/gate-config.yaml` — единый YAML-конфиг исключений и allowlist:

```yaml
# GREP_SUMMARY: gate-config exceptions allowlist unified-config S1 S3
# MODULE_CONTRACT:
#   @purpose: Single source of truth for all gate-test exceptions and allowlist entries
#   @scope: Consumed by test_gate_no_unregistered_entrypoint.py, test_gate_dead_code.py, test_gate_thin_wrapper.py
#   @invariants: All three gate tests read from this file. Adding an exception here
#                automatically applies to all relevant gate checks. No per-test hardcoded lists.

shebang_exceptions:
  - "core/lib/*.sh"
  - "core/modules/*/healthcheck.sh"
  - "core/modules/*/hooks/*.sh"
  - "core/modules/*/install.sh"
  - "core/modules/*/ready-check.sh"
  - "core/modules/*/scripts/*.sh"
  - "core/modules/*/config/*.sh"
  - "core/modules/*/config/*/*.sh"
  - "core/modules/*/watchdog/*.sh"
  - "core/bootstrap/systemd/*.sh"
  - "core/internal/healthcheck/*.sh"
  - "core/modules/hermes-agent/build/scripts/*.sh"
  - "core/modules/hermes-agent/context/scripts/*.sh"
  - "core/internal/bootstrap/ssl-provision.sh"
  - "core/modules/nginx/nginx_reload_hook.sh"
  - "core/entrypoints/deploy.sh"
  - "core/internal/bootstrap/s3-ssl-cache.sh"
  - "core/internal/deploy/reconcile-projects.sh"

dead_code_exceptions:
  prefixes:
    - "core/lib/"
    - "core/bootstrap/systemd/"
    - "core/internal/healthcheck/"
  suffixes:
    - "healthcheck.sh"
    - "install.sh"
    - "ready-check.sh"
  exact:
    - "core/internal/catalog/generate-catalog.sh"
    - "core/internal/bootstrap/ssl-provision.sh"
    - "core/internal/bootstrap/s3-ssl-cache.sh"
    - "core/internal/deploy/reconcile-projects.sh"

thin_wrapper_allowlist:
  - file: "bootstrap.sh"
    reason: "Bootstrap orchestrator — planned refactoring to ~150 LOC"
    until: "2026-09-01"
  - file: "lint.sh"
    reason: "External tool orchestrator (shellcheck, hadolint, yamllint, ruff)"
  - file: "check-doc-headers.sh"
    reason: "Documentation audit utility — structural analysis, not thin wrapper"
  - file: "context-promote.sh"
    reason: "Uses ssh -T for SSH auth detection"
    checks: ["binary_calls"]       # исключение только для binary-call проверки
  - file: "deploy-project.sh"
    reason: "Deploy orchestrator — tar assembly, SSH forced-command, CI interaction"
  - file: "converge.sh"
    reason: "1 LOC over limit due to --reconcile flag documentation + MODULE_CONTRACT markup"
    until: "2026-08-15"
```

2. Модифицировать три gate-теста:
   - `test_gate_no_unregistered_entrypoint.py`: читать `shebang_exceptions` из `gate-config.yaml`
   - `test_gate_dead_code.py`: читать `dead_code_exceptions` из `gate-config.yaml`
   - `test_gate_thin_wrapper.py`: читать `thin_wrapper_allowlist` из `gate-config.yaml`

3. Для thin-wrapper: поддержать поле `checks` — если указано, allowlist применяется только к перечисленным проверкам (напр. `["binary_calls"]`), а не ко всем трём.

4. Для thin-wrapper: проверка `until` — если дата в прошлом, тест падает с сообщением «allowlist entry expired: <file> (until: <date>)».

5. Добавить gate-тест `test_gate_config_schema` — валидирует структуру `gate-config.yaml` (все обязательные поля, формат дат, отсутствие дубликатов).

**Файлы:** `core/gate-config.yaml` (CREATE), `tests/gates/test_gate_no_unregistered_entrypoint.py` (MODIFY), `tests/gates/test_gate_dead_code.py` (MODIFY), `tests/gates/test_gate_thin_wrapper.py` (MODIFY), `tests/gates/test_gate_config_integrity.py` (CREATE).

**Эффект:** S1 и S3 закрыты. Добавление исключения в одном месте — автоматически применяется ко всем gate-тестам. Allowlist имеет прозрачные `reason` и `until`.

---

### Волна 2 (P0): Test-data/schema coherence [S2]

**Проблема:** Тестовые фикстуры не валидируются против схем. После эволюции schema тесты падают из-за устаревших test data, а не из-за багов в коде.

**Решение:**
1. Добавить `pytest_sessionstart` hook в `tests/conftest.py`:

```python
def pytest_sessionstart(session):
    """Validate all test fixtures against their schemas before any test runs."""
    _validate_test_fixtures()
```

`_validate_test_fixtures()`:
- Для каждого `.yaml` файла в `tests/test_data/` определяет соответствующую schema:
  - `node.yaml` → `core/schemas/node.schema.json`
  - (будущие фикстуры — mapping расширяем)
- Валидирует через `jsonschema.validate()`
- При несовпадении: `pytest.exit("Test fixture <path> does not match schema <schema>: <errors>")` — жёсткий fail до запуска тестов, с читаемым сообщением.

2. Обновить `tests/test_data/node.yaml` под актуальную schema:

```yaml
# GREP_SUMMARY: test-data node-yaml predeploy-gate test-site
# Test node.yaml for predeploy gate local testing
# Validated against core/schemas/node.schema.json at pytest_sessionstart

context: test

node:
  name: test-node
  host: 127.0.0.1
  owner_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA test-key-for-gate"
  timezone: UTC

domain: test.example.com
email: test@example.com

modules: []

projects:
  - name: test-site
    domain: test-site.example.com
    repo: https://github.com/example/test-site.git
```

3. Обновить assertions в трёх тестах:

**`test_extract_node_host_from_yaml`** → assert `127.0.0.1` (было `192.168.1.100`)
**`test_node_yaml_domain_extraction`** → assert `test.example.com`, `test@example.com`, `test-site.example.com` (было `test.local`, `admin@test.local`, `app.test.local`/`independent-project.com`)
**`test_node_yaml_validation[valid-node-yaml]`** → fixture теперь содержит `context`, `modules`, `owner_key` и не содержит `branch` → тест должен проходить.

4. Добавить gate-тест `test_fixtures_match_schema` в `tests/gates/` — дублирует логику `pytest_sessionstart` для явного gate-покрытия.

**Файлы:** `tests/conftest.py` (MODIFY: pytest_sessionstart), `tests/test_data/node.yaml` (MODIFY), `tests/test_bootstrap_auto.py` (MODIFY: assert), `tests/test_node_yaml_domains.py` (MODIFY: asserts), `tests/gates/test_gate_fixture_schema.py` (CREATE).

**Эффект:** S2 закрыт. Любое будущее изменение schema, ломающее test data, будет детектироваться на `pytest_sessionstart` (до запуска тестов) и на gate.

---

### Волна 3 (P1): Registration friction reduction [S5]

**Проблема:** Добавление скрипта требует ручной регистрации в 4 местах. Нет pre-commit проверки, которая детектирует пропущенную регистрацию до коммита.

**Решение:**
1. Создать `core/internal/scripts-audit.sh` — скрипт аудита регистрации:

```bash
#!/usr/bin/env bash
# scripts-audit.sh — проверяет, что каждый shebang-файл зарегистрирован или в exceptions
# Использует gate-config.yaml как источник exceptions
# Exit 0: все скрипты зарегистрированы. Exit 1: есть незарегистрированные.

# Алгоритм:
# 1. Загрузить shebang_exceptions из core/gate-config.yaml
# 2. Найти все shebang-файлы под core/ (исключая .backup, .bak, __pycache__)
# 3. Для каждого: проверить match с exception-паттернами
# 4. Для не-exception: проверить наличие в entrypoint-manifest.yaml (delegates_to или module_hooks)
# 5. Вывести список незарегистрированных + инструкцию:
#    "Unregistered scripts found. Add to core/entrypoint-manifest.yaml or core/gate-config.yaml"
```

2. Добавить `make scripts-audit` target в Makefile:

```makefile
scripts-audit:
	@bash core/internal/scripts-audit.sh
```

3. Добавить pre-commit хук в `.pre-commit-config.yaml`:

```yaml
- id: scripts-audit
  name: Audit script registration
  entry: make scripts-audit
  language: system
  files: '^core/.*\.sh$'
  pass_filenames: false
```

4. Интегрировать `scripts-audit` в `make gate MODE=fast` (как один из шагов) или в `make audit`.

**Файлы:** `core/internal/scripts-audit.sh` (CREATE), `Makefile` (MODIFY: target), `.pre-commit-config.yaml` (MODIFY: hook).

**Эффект:** S5 закрыт. При попытке закоммитить новый скрипт без регистрации — pre-commit блокирует коммит с читаемым сообщением. Gate-тесты больше не ловят registration drift post-factum.

---

## 3. Приоритеты и оценки

| Приоритет | Волна | Эффект | Усилие | Зависимости |
|:---------:|-------|--------|:------:|-------------|
| **P0** | W1 (унификация конфига) | S1 + S3: 3 failures исправлены, allowlist прозрачен | Среднее | Нет |
| **P0** | W2 (test-data/schema) | S2: 3 failures исправлены, будущий дрейф предотвращён | Низкое | Нет |
| **P1** | W3 (registration audit) | S5: будущий дрейф предотвращён на pre-commit | Низкое | W1 (нужен gate-config.yaml) |

**Суммарный эффект:** 6 из 8 failures → green. Gate-тесты защищены от собственного дрейфа.

---

## 4. Definition of Done

1. `core/gate-config.yaml` существует, валидируется gate-тестом `test_gate_config_schema`.
2. `test_all_internal_scripts_reachable` — зелёный: `s3-ssl-cache.sh` и `reconcile-projects.sh` не детектятся как dead code.
3. `test_all_shebang_files_in_manifest` — зелёный: оба скрипта в exceptions через gate-config.yaml.
4. `test_entrypoint_loc` — зелёный: `converge.sh` либо рефакторен до ≤150 LOC, либо имеет валидный `until` в allowlist.
5. `test_extract_node_host_from_yaml` — зелёный.
6. `test_node_yaml_domain_extraction` — зелёный.
7. `test_node_yaml_validation[valid-node-yaml]` — зелёный.
8. `make scripts-audit` — exit 0 на чистом working tree, exit 1 при наличии незарегистрированных скриптов.
9. Pre-commit hook `scripts-audit` блокирует коммит незарегистрированных скриптов.
10. `make gate MODE=full` — не более 2 failures (оставшиеся smoke-test — в брифе 027).
11. `make gate MODE=fast` — зелёный.

---

## 5. Не входит в этот бриф

- **S4 (macOS overlays для litellm/status-page)** — вынесен в бриф 027. Требует отдельного тестирования Docker на macOS, свой цикл Coder→Sysadmin→QA.
- Рефакторинг `bootstrap.sh` (192 → 150 LOC) — существующий технический долг, не порождённый текущими failures. Allowlist с `until: 2026-09-01`.
- Рефакторинг `converge.sh` если решено оставить в allowlist (а не рефакторить) — допустимо в рамках W1.
- Добавление новых скриптов или изменение логики существующих — только конфигурационные изменения (исключения, тестовые данные).

$END_BRIEF
