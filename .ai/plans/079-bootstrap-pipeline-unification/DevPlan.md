$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Унификация трёх дублирующих подсистем bootstrap pipeline: (1) 4 entrypoint'а для deploy context → один, (2) 3 реализации content hash → одна, (3) 2 независимые реализации docker compose orchestration → одна shared library.
DESCRIPTION:           Закрывает DRIFT-B3, DRIFT-B4, DRIFT-B6 из Brief 077. Создаёт shared Python-модули в core/internal/shared/ для content hash и docker compose. Рефакторит context_deployer.py для включения полного deploy_context() с cert orchestration + vhost render + verify. Заменяет shell content-hash.sh на Python-реализацию. Устраняет 3 копии `_extract_context_from_node_yaml()` (context_deployer, steps, state_machine) и 3 копии `_extract_domains()` (state_machine, steps, + мигрируемая в context_deployer). Четвёртая копия в s3_ssl_cache.py:318 — DRIFT-B5, вне скоупа.
RATIONALE:             Каждая точка дрейфа — независимая реализация одной бизнес-логики. DRIFT-B3: 4 пути делают одно и то же с вариациями. DRIFT-B4: разные хеши на разных файлах → рассинхронизация idempotency. DRIFT-B6: context_deployer.py не имеет retry/rollback/image-check → production риск. Унификация устраняет дублирование и добавляет отсутствующие features. VerificationReport 01-VerificationReport.md обнаружил 3 (не 2) копии `_extract_context_from_node_yaml` и 3 (не 2) копии `_extract_domains` — план обновлён для устранения всех копий.
ACCEPTANCE_CRITERIA:
  - AC1: `core/internal/shared/content_hash.py` содержит `compute_content_hash(files: list[str]) -> str`
  - AC2: `core/internal/shared/docker_compose.py` содержит pull, build, up, healthcheck_poll, retry_pull, check_image_exists
  - AC3: context_deployer.py содержит публичную `deploy_context()` с полным flow (cert + project deploy + vhost + verify)
  - AC4: state_machine.py вызывает `deploy_context()` из context_deployer.py (не из steps.py)
  - AC5: steps.py._step_deploy_context удалён или редуцирован до тонкого фасада
  - AC6: content-hash.sh редуцирован до thin wrapper над Python
  - AC7: docker_orchestrator.py импортирует pull/build/up/healthcheck из shared/docker_compose.py
  - AC8: context_deployer.py импортирует pull/build/up/healthcheck из shared/docker_compose.py (удаляет локальные копии)
  - AC9: Все существуующие тесты проходят; новые unit-тесты для shared модулей
  - AC10: `make gate MODE=fast` — green
IMPLEMENTS:            Brief 077 — Wave B (Bootstrap Pipeline Unification): DRIFT-B3, DRIFT-B4, DRIFT-B6
IMPACTS:
  - NEW: core/internal/shared/content_hash.py
  - NEW: core/internal/shared/docker_compose.py
  - MODIFIED: core/internal/bootstrap/lifecycle/state_machine.py (import из shared, вызов deploy_context)
  - MODIFIED: core/internal/bootstrap/lifecycle/steps.py (удалить/редуцировать _step_deploy_context)
  - MODIFIED: core/internal/bootstrap/deploy/context_deployer.py (deploy_context(), импорт shared)
  - MODIFIED: core/internal/bootstrap/deploy/docker_orchestrator.py (импорт shared)
  - MODIFIED: core/internal/bootstrap/content-hash.sh (thin wrapper)
  - MODIFIED: core/internal/scaffold/add-vhost.sh (вызов Python content_hash)
  - NEW: tests/unit/test_shared_content_hash.py
  - NEW: tests/unit/test_shared_docker_compose.py
REQUIRES:              DevPlan 070 (shared/ directory exists). Shared библиотеки создаются в core/internal/shared/.

---

## Prerequisites & Preconditions

### P0: shared/ directory must exist (required for Wave 1)
**Check:** `[ -d core/internal/shared/ ] && [ -f core/internal/shared/__init__.py ]`
**Blocked tasks:** TASK-1, TASK-6 (Wave 1 — создают новые файлы в shared/)
**Resolution path:**
- **(A) Implement DevPlan 070 first** — создаёт `core/internal/shared/` с `__init__.py`, модульными конвенциями, test-инфраструктурой.
- **(B) Bootstrap shared/ inline** — добавить создание `__init__.py` в TASK-1/TASK-6, убрать зависимость от DevPlan 070.
- Рекомендация VerificationReport: вариант (B) — shared/ содержит всего 2 модуля, отдельный DevPlan 070 — over-engineering.
**Если выбран вариант (B):** обновить REQUIRES (удалить упоминание DevPlan 070), добавить AC в TASK-1/TASK-6: «создать `__init__.py` если отсутствует».

### P1: Wave 2+ не требует DevPlan 070
Wave 1 (TASK-1 + TASK-6) — единственная волна, строго требующая существования `core/internal/shared/`. Как только shared/ создан, все остальные волны (TASK-2–TASK-11) могут выполняться независимо от наличия DevPlan 070.

### P2: Все исходные файлы существуют (проверено VerificationReport)
10/10 файлов из File Manifest верифицированы на диске. Все ссылки на строки актуальны.

---

## Requirements Analysis

### Success Criteria
1. **SC1: Единая функция content hash.** Все 3 реализации (shell content-hash.sh, state_machine._step_hash, add-vhost.sh compute_body_hash) заменены на вызов `compute_content_hash(files)` из `core/internal/shared/content_hash.py`.
2. **SC2: Единый deploy_context() flow.** 4 entrypoint'а (state_machine init/update, steps._step_deploy_context, deploy-context.sh, context_deployer.main) делегируют к одной публичной функции `deploy_context()`.
3. **SC3: Shared docker compose library.** pull, build, up, healthcheck_poll, retry_pull, check_image_exists в одном модуле — оба оркестратора импортируют из него.
4. **SC4: Обратная совместимость.** Все существующие CLI-интерфейсы (`make deploy-context`, bootstrap init/update) работают без изменений сигнатуры вызова.
5. **SC5: Тестовое покрытие.** Unit-тесты для каждого shared модуля; существующие тесты не сломаны.

---

## Architecture Overview

### Draft Code Graph (AFTER unification)

```
┌─ entrypoints/deploy-context.sh ─┐        ┌─ state_machine.py ────────────┐
│  python3 context_deployer.py    │        │  importlib context_deployer   │
│  --node-yaml ... --context ...   │        │  deploy_context(...)          │
└────────────┬────────────────────┘        └────────────┬──────────────────┘
             │                                          │
             ▼                                          ▼
    ┌─────────────────────────────────────────────────────────┐
    │  context_deployer.deploy_context()   [PUBLIC API]        │
    │  ┌──────────────────────────────────────────────────┐   │
    │  │ 1. extract_context_from_node_yaml(node_yaml)     │   │
    │  │ 2. cert_orchestrator.orchestrate_certs(domains)   │   │
    │  │ 3. deploy_context_projects(node_yaml, context)    │   │
    │  │ 4. render_vhosts(node_name, node_yaml)            │   │
    │  │ 5. verify_domains(node_name)                      │   │
    │  └──────────────────────────────────────────────────┘   │
    └──────────┬──────────────────────┬───────────────────────┘
               │                      │
    ┌──────────▼──────────┐  ┌───────▼──────────────────────┐
    │ shared/docker_      │  │ shared/content_hash.py       │
    │ compose.py          │  │ compute_content_hash(files)  │
    │ · pull              │  └──────────────────────────────┘
    │ · build             │
    │ · up                │
    │ · healthcheck_poll  │
    │ · retry_pull        │
    │ · check_image_exists│
    └─────────────────────┘
              ▲                      ▲
    ┌─────────┴──────────┐  ┌───────┴──────────────┐
    │ docker_            │  │ content-hash.sh       │
    │ orchestrator.py    │  │ (thin wrapper →       │
    │ (import shared)    │  │  python3 shared/...)  │
    └────────────────────┘  └──────────────────────┘
```

### Data Flow

**DRIFT-B3 (deploy context unification):**
```
state_machine.py init step 23 / update step 9
   → importlib: from context_deployer import deploy_context
   → deploy_context(core_dir, node_name, node_yaml)
   └─► cert orchestration → project deploy → vhost render → verify

entrypoints/deploy-context.sh
   → python3 context_deployer.py --node-yaml ... --context ...
   → main() → deploy_context(...)
```

**DRIFT-B4 (content hash unification):**
```
content-hash.sh:  python3 shared/content_hash.py compute --files "$@"
state_machine:    from core.internal.shared.content_hash import compute_content_hash
add-vhost.sh:     python3 shared/content_hash.py compute --files "$vhost_file"
```

**DRIFT-B6 (docker compose unification):**
```
docker_orchestrator.py:  from core.internal.shared.docker_compose import docker_compose_pull, ...
context_deployer.py:     from core.internal.shared.docker_compose import docker_compose_pull, ...
```

---

## Design Decisions

### ## @rationale D1: deploy_context() как публичная функция в context_deployer.py
Q: Почему context_deployer.py, а не steps.py?
A: context_deployer.py уже содержит `deploy_context_projects()` и имеет публичный CLI. Добавление `deploy_context()` (полный flow: certs + projects + vhost + verify) делает его единым entrypoint для deploy context. state_machine вызывает его через importlib (как сейчас вызывает cert_orchestrator), deploy-context.sh вызывает через subprocess. steps.py._step_deploy_context удаляется — логика мигрирует в context_deployer.deploy_context().

### ## @rationale D2: shared/docker_compose.py — подмножество, не замена docker_orchestrator.py
Q: Почему не объединить docker_orchestrator.py и context_deployer.py в один файл?
A: У них разная ответственность: docker_orchestrator.py работает с платформенными модулями (compose file resolution, hermes-agent special case, parallel deploy, topo-sort), context_deployer.py — с проектами контекста (node.yaml projects, ghcr.io pull, bootstrap compose stub). Общие только низкоуровневые docker-операции: pull, build, up, healthcheck poll. Именно их выносим в shared. Это слой инфраструктуры (по DDD), не бизнес-логики.

### ## @rationale D3: compute_content_hash(files: list[str]) — явный список файлов
Q: Почему не унаследовать от content_hash.py (deploy/) с dockerignore?
A: content_hash.py в deploy/ специализирован для build-context (учитывает .dockerignore, ходит по директории). Для bootstrap idempotency нужен явный список файлов — никаких фильтров. Это разные use cases, разные функции. Новая shared/версия — простая, без зависимостей от docker.

---

## $TASKS

### TASK-1: Создать `core/internal/shared/content_hash.py`
**Owner:** Coder
**Output:** `core/internal/shared/content_hash.py` (~60 LOC)
**Acceptance Criteria:**
- Функция `compute_content_hash(files: list[str]) -> str` возвращает SHA256 hexdigest
- Файлы читаются в порядке, указанном в списке
- Отсутствующие файлы логируются с WARNING и пропускаются (не фатально)
- CLI: `python3 -m core.internal.shared.content_hash compute --files f1 f2`
- Module contract с GREP_SUMMARY, STRUCTURE, @purpose, @invariants
**Dependencies:** None
**Complexity:** 2

### TASK-2: Создать `tests/unit/test_shared_content_hash.py`
**Owner:** Coder
**Output:** `tests/unit/test_shared_content_hash.py` (~80 LOC)
**Acceptance Criteria:**
- test_compute_hash_two_files: два временных файла → хеш консистентен
- test_compute_hash_order_matters: разный порядок → разный хеш
- test_compute_hash_missing_file: отсутствующий файл → warning, не фатально
- test_compute_hash_empty_list: пустой список → пустой хеш (sha256(""))
- test_cli_compute: `python3 -m core.internal.shared.content_hash compute --files ...` работает
- LDD: минимум один IMP:9 лог в каждом успешном сценарии
**Dependencies:** TASK-1
**Complexity:** 3

### TASK-3: Рефакторить content-hash.sh → thin wrapper
**Owner:** Coder
**Output:** `core/internal/bootstrap/content-hash.sh` (~40 LOC, было 127)
**Acceptance Criteria:**
- `compute_step_hash()` делегирует к `python3 -m core.internal.shared.content_hash compute --files "$@"` или аналогичному Python вызову
- `step_hash_changed()` остаётся в shell (требует доступ к CHECKPOINT_DIR и файловой системе .hash)
- Сохраняется обратная совместимость: все caller'ы (checkpoint.sh, node-lifecycle.sh) работают без изменений
- Shell-скрипт проверяет наличие Python-модуля, при отсутствии — fallback на старый алгоритм
**Dependencies:** TASK-1
**Complexity:** 2

### TASK-4: Обновить state_machine._step_hash() → импорт из shared
**Owner:** Coder
**Output:** `core/internal/bootstrap/lifecycle/state_machine.py` (изменено ~20 строк)
**Acceptance Criteria:**
- `_step_hash()` (строка 418-429) заменён на вызов `compute_content_hash(files)` из shared/content_hash.py
- `_compute_step_hash()` (строка 1248-1296) обновлён: формирует список файлов, вызывает `compute_content_hash()`
- Удалён метод `_safe_update_hash` (больше не нужен, shared обрабатывает missing files)
- Лог-префикс сохраняется: `[IMP:6][StateMachine][_step_hash]`
**Dependencies:** TASK-1
**Complexity:** 2

### TASK-5: Обновить add-vhost.sh compute_body_hash() → Python content_hash
**Owner:** Coder
**Output:** `core/internal/scaffold/add-vhost.sh` (изменено ~30 строк в compute_body_hash)
**Acceptance Criteria:**
- `compute_body_hash()` (строка 89-114) делегирует к `python3 -m core.internal.shared.content_hash compute --files "$vhost_file"` или прямому импорту
- Fallback: если Python-модуль недоступен, использовать if-elif-else с sha256sum/shasum (существующий код)
- TRAP[BUG] (строка 99-103) сохраняется как историческая документация
**Dependencies:** TASK-1
**Complexity:** 2

### TASK-6: Создать `core/internal/shared/docker_compose.py`
**Owner:** Coder
**Output:** `core/internal/shared/docker_compose.py` (~200 LOC)
**Acceptance Criteria:**
- `docker_compose_pull(compose_dir: str, timeout: int = 120) -> bool`
- `docker_compose_build(compose_dir: str, timeout: int = 300) -> bool`
- `docker_compose_up(compose_dir: str, timeout: int = 120) -> bool`
- `healthcheck_poll(project_name: str, timeout: int, interval: int = 3, use_inspect: bool = True) -> str` ("healthy"/"unhealthy")
- `retry_pull(compose_dir: str, max_attempts: int = 3, backoff_seconds: list[int] = [5, 10, 20]) -> bool`
- `check_image_exists(image_ref: str, timeout: int = 60) -> bool`
- Module contract с GREP_SUMMARY, STRUCTURE, @purpose, @invariants
- Все функции логируют через стандартный `logging.getLogger("docker_compose")`
**Dependencies:** None (standalone shared module)
**Complexity:** 4

### TASK-7: Создать `tests/unit/test_shared_docker_compose.py`
**Owner:** Coder
**Output:** `tests/unit/test_shared_docker_compose.py` (~120 LOC)
**Acceptance Criteria:**
- test_pull_success: mock subprocess.run → возвращает True
- test_pull_failure: mock subprocess.run returncode=1 → возвращает False
- test_healthcheck_poll_healthy: mock docker ps → "healthy"
- test_healthcheck_poll_timeout: mock всегда unhealthy → возвращает "unhealthy" после N попыток
- test_retry_pull_success_second_attempt: первый раз fail, второй success
- test_check_image_exists_found: mock manifest inspect returncode=0 → True
- test_check_image_exists_not_found: mock returncode=1 → False
- LDD: минимум один IMP:9 лог в каждом успешном сценарии
**Dependencies:** TASK-6
**Complexity:** 3

### TASK-8: Рефакторить context_deployer.py — импорт из docker_compose.py
**Owner:** Coder
**Output:** `core/internal/bootstrap/deploy/context_deployer.py` (изменено ~100 LOC)
**Acceptance Criteria:**
- `_docker_compose_pull()` (строка 504-518) → вызов `docker_compose_pull()` из shared
- `_docker_compose_build()` (строка 531-545) → вызов `docker_compose_build()` из shared
- `_docker_compose_up()` (строка 558-572) → вызов `docker_compose_up()` из shared
- `_wait_until_healthy()` (строка 585-594) → вызов `healthcheck_poll()` из shared
- `_is_project_healthy()` (строка 470-491) — логика перенесена в shared, вызывается через `healthcheck_poll`
- Локальные приватные функции удалены (заменены на shared)
- Добавлен `retry_pull()` в `_deploy_single_project()`: 3 попытки с backoff 5/10/20s перед fallback build
**Dependencies:** TASK-6
**Complexity:** 4

### TASK-9: Рефакторить docker_orchestrator.py — импорт из docker_compose.py
**Owner:** Coder
**Output:** `core/internal/bootstrap/deploy/docker_orchestrator.py` (изменено ~30 LOC)
**Acceptance Criteria:**
- `_pull_module_images()` (строка 767-812) → использует `docker_compose_pull()` из shared для базового вызова (оставляет логику skip build:-modules)
- `_check_image_exists()` (строка 113-133) → заменён на `check_image_exists()` из shared
- `wait_for_readiness()` и `run_healthcheck()` — используют invoke_module_interface (НЕ трогаем, это платформенный healthcheck, не проектный)
- Импорт: `from core.internal.shared.docker_compose import docker_compose_pull, check_image_exists`
**Dependencies:** TASK-6
**Complexity:** 2

### TASK-10: Рефакторить context_deployer.py — объединить deploy_context() + устранить ВСЕ дубликаты extract-функций
**Owner:** Coder
**Output:** `context_deployer.py` + `steps.py` + `state_machine.py` (изменено ~220 LOC)
**Acceptance Criteria:**
- Новая публичная функция `deploy_context(core_dir: str, node_name: str, node_yaml: str, context: str = "") -> DeployResult` в context_deployer.py
- Включает полный flow: extract context → cert orchestration → project deploy → vhost render → nginx reload → verify
- Cert orchestration: importlib cert_orchestrator.orchestrate_certs() (как сейчас в steps.py:852-865)
- Vhost render: subprocess add-vhost.sh --render-all (как сейчас в steps.py:888-895)
- Verify: subprocess verify-domains.sh (как сейчас в steps.py:905-913)
- `main()` обновлён: вызывает `deploy_context()` вместо `deploy_context_projects()`
- `steps.py._step_deploy_context()` (строка 828-916) удалён — заменён на вызов `deploy_context()` из context_deployer.py
- `state_machine.py` (строка 1136-1139 и 1237-1239): вместо `_steps._step_deploy_context(...)` → importlib вызов `deploy_context()` из context_deployer

**Устранение 3 копий `_extract_context_from_node_yaml()` → 1 каноническая:**
- ✅ `context_deployer.py:214` — каноническая (public), сохраняется
- ❌ `steps.py:925-953` — удалить (дубликат)
- ❌ `state_machine.py:2002-2030` — удалить (DEAD CODE: grep подтверждает — ни одного caller'а в state_machine.py; функция не импортируется извне)

**Устранение 3 копий `_extract_domains()` → 1 каноническая:**
- ✅ `context_deployer.py` — принять мигрированную `_extract_domains_for_context(node_yaml_path, context)` из steps.py (перенести как есть)
- ❌ `steps.py:960-993` — удалить после миграции в context_deployer.py
- ❌ `state_machine.py:2038-2074` — удалить; заменить вызов на L1797:
  ```
  # Было:  domains = _extract_domains(node_yaml, context)
  # Стало: domains = context_deployer._extract_domains_for_context(node_yaml, context)
  ```
  Использовать importlib (state_machine уже использует importlib для context_deployer/cert_orchestrator). Функция `_extract_domains()` в state_machine идентична `_extract_domains_for_context()` — обе принимают `(node_yaml_path, context)`, одинаковая логика platform domain + project domains.
- ⚠️ `s3_ssl_cache.py:318` `_extract_domains_from_yaml()` — **НЕ ТРОГАТЬ**. Это DRIFT-B5 (другая ответственность: извлечение доменов для S3 SSL cache restore). Отличается сигнатурой (принимает только `node_yaml_path`, без `context`), используется в `s3_ssl_cache.py:709`. Будет устранена отдельным планом DRIFT-B5.

**Dependencies:** TASK-6, TASK-8
**Complexity:** 6 (было 5, +1 за state_machine dead-code cleanup + importlib refactoring)

### TASK-11: Gate + интеграционная верификация
**Owner:** Coder
**Output:** Все тесты зелёные, `make gate MODE=fast` проходит
**Acceptance Criteria:**
- `python3 -m pytest tests/unit/test_shared_content_hash.py tests/unit/test_shared_docker_compose.py -v` — все тесты проходят
- `python3 -m pytest tests/unit/test_state_machine.py tests/unit/test_docker_orchestrator.py -v` — все тесты проходят (без регрессии)
- `make fix-gate && git add -u && make gate MODE=fast` — green
**Dependencies:** TASK-1 through TASK-10
**Complexity:** 2

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- Tasks: TASK-1, TASK-6
- Command: `coder Read .ai/plans/079-bootstrap-pipeline-unification/01-DevPlan.md, implement Wave 1: TASK-1, TASK-6`

### Wave 2 (depends on Wave 1, independent within wave)
- Tasks: TASK-2, TASK-3, TASK-4, TASK-5, TASK-7
- Command: `coder Read .ai/plans/079-bootstrap-pipeline-unification/01-DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-4, TASK-5, TASK-7`

### Wave 3 (depends on Wave 2)
- Tasks: TASK-8, TASK-9
- Command: `coder Read .ai/plans/079-bootstrap-pipeline-unification/01-DevPlan.md, implement Wave 3: TASK-8, TASK-9`

### Wave 4 (depends on Wave 3)
- Tasks: TASK-10
- Command: `coder Read .ai/plans/079-bootstrap-pipeline-unification/01-DevPlan.md, implement Wave 4: TASK-10`

### Wave 5 (depends on all previous)
- Tasks: TASK-11
- Command: `coder Read .ai/plans/079-bootstrap-pipeline-unification/01-DevPlan.md, implement Wave 5: TASK-11`

---

## Acceptance Criteria Summary

| # | Criteria | Verification |
|---|----------|-------------|
| AC1 | `compute_content_hash(files)` в shared/content_hash.py | test_shared_content_hash.py |
| AC2 | `docker_compose_pull/build/up/healthcheck_poll/retry_pull/check_image_exists` в shared/docker_compose.py | test_shared_docker_compose.py |
| AC3 | `deploy_context()` в context_deployer.py | unit test (через тесты других модулей) |
| AC4 | state_machine вызывает deploy_context() | test_state_machine.py passes |
| AC5 | steps._step_deploy_context удалён | grep не находит функцию |
| AC6 | content-hash.sh → thin wrapper | ручная проверка + gate |
| AC7 | docker_orchestrator импортирует из shared | test_docker_orchestrator.py passes |
| AC8 | context_deployer импортирует из shared | ручная проверка |
| AC9 | Новые unit-тесты проходят | pytest passes |
| AC10 | `make gate MODE=fast` green | CI |

---

## File Manifest

| File | Action | LOC change |
|------|--------|------------|
| `core/internal/shared/content_hash.py` | NEW | +60 |
| `core/internal/shared/docker_compose.py` | NEW | +200 |
| `tests/unit/test_shared_content_hash.py` | NEW | +80 |
| `tests/unit/test_shared_docker_compose.py` | NEW | +120 |
| `core/internal/bootstrap/content-hash.sh` | MODIFY | -87 (127→40) |
| `core/internal/bootstrap/lifecycle/state_machine.py` | MODIFY | ~60 lines changed (было ~20; +40 за удаление dead code L2002-2030 + замена L1797 вызова + удаление L2038-2074) |
| `core/internal/bootstrap/lifecycle/steps.py` | MODIFY | -200 (удаление _step_deploy_context + _extract_context + _extract_domains_for_context; было -160) |
| `core/internal/bootstrap/deploy/context_deployer.py` | MODIFY | ~290 lines changed (было ~250; +40 за добавление _extract_domains_for_context из steps.py) |
| `core/internal/bootstrap/deploy/docker_orchestrator.py` | MODIFY | ~30 lines changed |
| `core/internal/scaffold/add-vhost.sh` | MODIFY | ~30 lines changed |

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/unit/test_shared_content_hash.py | test_compute_hash_two_files | Два файла → консистентный хеш | shared/content_hash.py |
| tests/unit/test_shared_content_hash.py | test_compute_hash_order_matters | Разный порядок файлов → разный хеш | shared/content_hash.py |
| tests/unit/test_shared_content_hash.py | test_compute_hash_missing_file | Отсутствующий файл → WARNING, не фатально | shared/content_hash.py |
| tests/unit/test_shared_content_hash.py | test_compute_hash_empty_list | Пустой список → sha256("") | shared/content_hash.py |
| tests/unit/test_shared_content_hash.py | test_cli_compute | CLI compute --files работает | shared/content_hash.py |
| tests/unit/test_shared_docker_compose.py | test_pull_success | docker compose pull success | shared/docker_compose.py |
| tests/unit/test_shared_docker_compose.py | test_pull_failure | docker compose pull failure | shared/docker_compose.py |
| tests/unit/test_shared_docker_compose.py | test_build_success | docker compose build success | shared/docker_compose.py |
| tests/unit/test_shared_docker_compose.py | test_up_success | docker compose up -d success | shared/docker_compose.py |
| tests/unit/test_shared_docker_compose.py | test_healthcheck_poll_healthy | docker ps → healthy | shared/docker_compose.py |
| tests/unit/test_shared_docker_compose.py | test_healthcheck_poll_timeout | timeout → unhealthy | shared/docker_compose.py |
| tests/unit/test_shared_docker_compose.py | test_retry_pull_success_second_attempt | 1-й fail, 2-й success | shared/docker_compose.py |
| tests/unit/test_shared_docker_compose.py | test_retry_pull_all_fail | Все 3 попытки fail | shared/docker_compose.py |
| tests/unit/test_shared_docker_compose.py | test_check_image_exists_found | manifest inspect found | shared/docker_compose.py |
| tests/unit/test_shared_docker_compose.py | test_check_image_exists_not_found | manifest inspect not found | shared/docker_compose.py |
| tests/unit/test_state_machine.py | exist_test__step_hash | Хеш через shared content_hash | state_machine.py |
| tests/unit/test_docker_orchestrator.py | exist_test_check_image | Импорт из shared | docker_orchestrator.py |

---

## Verification Commands

```bash
# After Wave 1
python3 -c "from core.internal.shared.content_hash import compute_content_hash; print(compute_content_hash([]))"
python3 -c "from core.internal.shared.docker_compose import docker_compose_pull, check_image_exists; print('OK')"

# After Wave 2
python3 -m pytest tests/unit/test_shared_content_hash.py -v
python3 -m pytest tests/unit/test_shared_docker_compose.py -v

# After Wave 4
python3 -m pytest tests/unit/test_state_machine.py tests/unit/test_docker_orchestrator.py -v

# After Wave 5 (final)
make fix-gate && git add -u && make gate MODE=fast
```

---

## Next Steps

### Prerequisite check (before Wave 1)
```bash
# Verify shared/ directory exists. If not, bootstrap it:
[ -d core/internal/shared/ ] || mkdir -p core/internal/shared/
[ -f core/internal/shared/__init__.py ] || touch core/internal/shared/__init__.py
```

### Wave 1 (требует shared/ — единственная волна с жёсткой зависимостью)
```
coder Read .ai/plans/079-bootstrap-pipeline-unification/01-DevPlan.md, implement Wave 1: TASK-1 (shared/content_hash.py), TASK-6 (shared/docker_compose.py)
```

### Wave 2 (может выполняться сразу после Wave 1, не требует DevPlan 070)
```
coder Read .ai/plans/079-bootstrap-pipeline-unification/01-DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-4, TASK-5, TASK-7
```

### Wave 3
```
coder Read .ai/plans/079-bootstrap-pipeline-unification/01-DevPlan.md, implement Wave 3: TASK-8, TASK-9
```

### Wave 4
```
coder Read .ai/plans/079-bootstrap-pipeline-unification/01-DevPlan.md, implement Wave 4: TASK-10
```

### Wave 5
```
coder Read .ai/plans/079-bootstrap-pipeline-unification/01-DevPlan.md, implement Wave 5: TASK-11
```

$END_DEVPLAN
