$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Семантическая QA-верификация DevPlan 087 (Bootstrap Phase Consolidation 32→14). Проверка $ARTIFACT_CONTRACT, self-consistency, task coverage, migration completeness, cross-file drift.
DESCRIPTION:           Полный аудит DevPlan 087: статический анализ структуры, верификация 14-фазной модели, проверка migration mapping на полноту (23→14 ключей), анализ precondition/dependency конфликта, валидация File Manifest против файловой системы, проверка отсутствия дублирования функций между phases.py и state_machine.py.
RATIONALE:             DevPlan затрагивает критическую подсистему (bootstrap pipeline) — ошибки в migration mapping приведут к даунтайму production-нод. Dual state machine (state_machine.py + steps.py) требует тщательной верификации разделения ответственности.
ACCEPTANCE_CRITERIA:   Все находки классифицированы по severity (BLOCKER/MAJOR/MINOR), каждая с file:line и рекомендацией. DevPlan признан STABLE только при 0 BLOCKER.
IMPLEMENTS:            QA role — Phase 1 (static audit) + Phase 2 (cross-file drift) + Phase 3 (invariant verification) + user-specified special checks.
IMPACTS:               VerificationReport.md (этот файл). Делегирование: Architect для исправления BLOCKER/MAJOR.
REQUIRES:              DevPlan 087 (.ai/plans/087-bootstrap-phase-consolidation/DevPlan.md), state_machine.py, steps.py, checkpoint_migration.py, node-lifecycle.sh
$END_ARTIFACT_CONTRACT

---

# VerificationReport: DevPlan 087 — Bootstrap Phase Consolidation

🔒 **Verified against SHA:** `5a31ef2bafd10b6bbe59345d35625e3b1c108953`
**Date:** 2026-07-28
**Task size:** LARGE (архитектурная реорганизация state machine, 14+ файлов)
**Verdict:** DRIFTED (CRITICAL)

---

## §1. Static Audit (Phase 1)

### 1.1 $ARTIFACT_CONTRACT Completeness

| Поле | Статус | Линия | Комментарий |
|------|--------|-------|-------------|
| PURPOSE | ✅ PASS | 4 | Консолидация 32→14, dual state machine removal |
| DESCRIPTION | ✅ PASS | 5 | Полное описание: DP-078/DP-079 residual, 8 silent failure точек |
| RATIONALE | ✅ PASS | 6 | Recurring drift причина, complexity reduction на 56% |
| ACCEPTANCE_CRITERIA | ✅ PASS | 7-21 | 14 AC (AC1-AC14), измеримы, с grep-командами |
| IMPLEMENTS | ✅ PASS | 22 | Superposition S3, DP-079 residual, state migration audit |
| IMPACTS | ✅ PASS | 23 | 14+ файлов, отсылка к §5 |
| REQUIRES | ✅ PASS | 24 | DP-078 (done), DP-086 (рекомендация merge first) |

**Verdict:** Все 7 полей $ARTIFACT_CONTRACT присутствуют и содержательны. ✅

### 1.2 Markup Compliance

| Проверка | Статус | Деталь |
|----------|--------|--------|
| $START_DEVPLAN / $END_DEVPLAN | ✅ PASS | L1, L695 |
| Нумерация секций (§1-§8) | ✅ PASS | Последовательная |
| Таблицы | ✅ PASS | §1 dual state machine, §2.5 composite hash, §4 waves, §5 manifest |
| Code blocks (mermaid/json/python) | ✅ PASS | §2.5 сигнатура, §4.1 state.json пример, §4.2 recovery flow |

### 1.3 Task Coverage

| Проверка | Статус | Деталь |
|----------|--------|--------|
| T1-T21 все описаны | ❌ **BLOCKER** | **T17 ОТСУТСТВУЕТ** — пропуск в нумерации. T1-T16 присутствуют, T18-T21 присутствуют, T17 нет ни в одной волне. |
| Каждый task имеет Effort | ✅ PASS | Effort 1-4 для всех |
| Каждый task имеет Описание | ✅ PASS | Все столбцы заполнены |
| Wave ordering | ✅ PASS | Wave 1 (foundation) → Wave 2 (cleanup) → Wave 3 (doc+tests) → Wave 4 (tests+gate) — логичный порядок |

**Finding T17-GAP [BLOCKER]:** T17 отсутствует в нумерации задач. Схема нумерации: T1-T16, T18-T21. Это либо опечатка (T17 пропущен), либо одна из задач была удалена без ренумерации. Рекомендация: добавить T17 или перенумеровать T18-T21 → T17-T20.

### 1.4 Wave Task Distribution

| Wave | Tasks | Issues |
|------|-------|--------|
| Wave 1 (Foundation) | T1-T4 | ✅ |
| Wave 2 (Cleanup) | T5, T6, T7, T8, T12, T15, T16, T18, T20 | ⚠️ Непоследовательная нумерация (T12 после T8, T9-T11 в Wave 4) |
| Wave 3 (Doc+Tests) | T13, T14, T19, T21 | ✅ |
| Wave 4 (Tests+Gate) | T9, T10, T11 | ✅ |

**Finding T-NONSEQ [MINOR]:** Нумерация задач не следует порядку волн. T9-T11 (Wave 4) идут ПОСЛЕ T12 (Wave 2) в нумерации. Это усложняет grep/tracking. Рекомендация: перенумеровать задачи последовательно по волнам.

---

## §2. Drift Analysis (Phase 2)

### 2.1 File Manifest vs Filesystem

| Файл | Статус в DevPlan | На диске | |
|------|-----------------|----------|-|
| `core/internal/bootstrap/lifecycle/phases.py` | CREATE | ❌ Не существует | ✅ OK (CREATE) |
| `core/internal/bootstrap/lifecycle/state_migration.py` | CREATE | ❌ Не существует | ✅ OK (CREATE) |
| `tests/unit/test_bootstrap_phases.py` | CREATE | ❌ Не существует | ✅ OK (CREATE) |
| `core/internal/bootstrap/lifecycle/state_machine.py` | MODIFY | ✅ Существует (2115 LOC) | ✅ OK |
| `core/internal/bootstrap/lifecycle/steps.py` | MODIFY | ✅ Существует (776 LOC) | ✅ OK |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | ✅ Существует | ✅ OK |
| `core/lib/checkpoint.sh` | MODIFY | ✅ Существует | ✅ OK |
| `core/internal/bootstrap/AGENTS.md` | MODIFY | ✅ Существует | ✅ OK |
| `core/entrypoints/bootstrap.sh` | MODIFY | ✅ Существует | ✅ OK |
| `core/entrypoints/node-update.sh` | MODIFY | ✅ Существует | ✅ OK |
| `tests/unit/test_state_machine.py` | MODIFY | ✅ Существует | ✅ OK |
| `tests/test_node_lifecycle_static.py` | MODIFY | ✅ Существует | ✅ OK |
| `core/internal/checkpoint_migration.py` | DELETE | ✅ Существует | ✅ OK |
| Shell .done-файлы | DELETE | — | ✅ OK |

**Verdict:** File Manifest корректен — все CREATE-файлы отсутствуют (ожидаемо), все MODIFY/DELETE-файлы существуют.

### 2.2 Integration Test Path Drift

**Finding IT-PATH [MAJOR]:** §8 Implementation Commands (lines 658-662) ссылается на:
```
python3 -m pytest tests/integration/test_bootstrap_dry_run.py -v
```
Этот файл НЕ указан в §5 File Manifest (ни как CREATE, ни как MODIFY). T14 создаёт интеграционный тест, но путь `tests/integration/test_bootstrap_dry_run.py` не фигурирует в манифесте. Рекомендация: добавить в §5 CREATE или уточнить путь в T14.

### 2.3 Cross-File Value Mismatch — _step_deploy_context

**Finding STEP-CTX [MAJOR — подтверждено]:** DevPlan утверждает (L101), что `_step_deploy_context()` в steps.py:751 — «дубликат логики из state_machine.py._step_deploy_modules()». Фактически:
- `_step_deploy_context()` существует **только** в steps.py:751 (подтверждено grep).
- В state_machine.py **нет** функции с именем `_step_deploy_context()`. Вместо неё `_import_deploy_context()` на L1188 вызывает `context_deployer.deploy_context()`.
- В steps.py:751 `_step_deploy_context()` — это thin facade (прокси к context_deployer.deploy_context()).

Дублирование НЕ в прямом копировании кода, а в двух точках вызова одной и той же логики (`context_deployer.deploy_context()`): через state_machine.py напрямую и через steps.py как прокси. T5 правильно требует удаления из steps.py.

### 2.4 _step_secrets_init Duplicate Confirmed

**Finding SEC-DUP [MAJOR — подтверждено]:** `_step_secrets_init()` существует в ДВУХ файлах:
- `state_machine.py:1846` — функция с sourcing secrets.env + вызов secrets-init.sh
- `steps.py:106` — функция с вызовом secrets-init.sh (без sourcing)

Это разные реализации одной ответственности. T20 правильно требует удаления дубликата.

### 2.5 Migration Mapping — Missing Keys Audit

**Критическая проверка:** Полнота маппинга `migrate_state_to_phases()` (DevPlan L301-310) против фактических ключей `INIT_STEPS` (state_machine.py L88-112) и `UPDATE_STEPS` (L114-124) + `SHELL_TO_PYTHON_STEP` (checkpoint_migration.py L46-63).

#### 2.5.1 Полный инвентарь старых ключей

| Источник | Ключи | Количество |
|----------|-------|-----------|
| INIT_STEPS (state_machine.py:88-112) | ssh_access, apt_deps, tor_proxy, install_docker, docker_auth, create_platform_user, create_ci_deploy_user, create_projects_base, firewall, verify_core, verify_node_configs, decrypt_secrets, ensure_secrets, **secrets_init**, read_node_yaml, ghcr_auth, **sudoers**, install_acme, node_update, converge, audit_log, telegram, deploy_context | 23 |
| UPDATE_STEPS (state_machine.py:114-124) | verify_core, provision, deliver_overlays, ssl_provision, deploy_modules, provision_llm_keys, healthcheck, converge, deploy_context | 9 |
| SHELL_TO_PYTHON_STEP (checkpoint_migration.py:46-63) | metrics_cron (shell-only) | 1 дополнительный |

**Всего уникальных ключей:** 23 (init) + 6 (update, без дублей verify_core/converge/deploy_context) + 1 (metrics_cron) = **30 уникальных ключей**

Дубли имён между INIT и UPDATE: `verify_core`, `converge`, `deploy_context` — по 3 ключа.

#### 2.5.2 Таблица покрытия миграции

| Старый ключ | Источник | Присутствует в MIGRATION_MAP (L301-310) | Статус |
|------------|----------|----------------------------------------|--------|
| ssh_access | INIT | φ2 user_accounts | ✅ |
| apt_deps | INIT | φ1 system_bootstrap | ✅ |
| tor_proxy | INIT | φ1 system_bootstrap | ✅ |
| install_docker | INIT | φ1 system_bootstrap | ✅ |
| docker_auth | INIT | φ1 system_bootstrap | ⚠️ См. 2.5.3 |
| create_platform_user | INIT | φ2 user_accounts | ✅ |
| create_ci_deploy_user | INIT | φ2 user_accounts | ✅ |
| create_projects_base | INIT | φ2 user_accounts | ✅ |
| firewall | INIT | φ1 system_bootstrap | ✅ |
| verify_core | INIT | φ5 node_configuration | ✅ |
| verify_node_configs | INIT | φ5 node_configuration | ✅ |
| decrypt_secrets | INIT | φ4 secrets_provision | ✅ |
| ensure_secrets | INIT | φ4 secrets_provision | ✅ |
| **secrets_init** | INIT | **НЕТ** (ensure_secrets используется, но это отдельный ключ!) | ❌ **BLOCKER** |
| read_node_yaml | INIT | φ5 node_configuration | ✅ |
| ghcr_auth | INIT | φ6 registry_auth | ✅ |
| **sudoers** | INIT | **НЕТ** | ❌ **BLOCKER** |
| install_acme | INIT | φ7 certificates | ✅ |
| node_update | INIT | Нет (отдельный трек) | ⚠️ WARNING |
| converge | INIT | φ8.5 converge_services | ✅ |
| audit_log | INIT | Нет (отдельный трек) | ⚠️ WARNING |
| telegram | INIT | Нет (отдельный трек) | ⚠️ WARNING |
| deploy_context | INIT | φ8 deploy_services | ✅ |
| metrics_cron | SHELL | φ3 platform_setup | ✅ |
| **platform_dirs** | **НЕ СУЩЕСТВУЕТ** | φ3 platform_setup (в маппинге) | ❌ **BLOCKER** |
| **docker_config** | **НЕ СУЩЕСТВУЕТ** | φ3 platform_setup (в маппинге) | ❌ **BLOCKER** |
| verify_core (update) | UPDATE | **НЕТ** (см. 2.5.4) | ❌ **BLOCKER** |
| provision | UPDATE | **НЕТ** | ❌ **BLOCKER** |
| deliver_overlays | UPDATE | **НЕТ** | ❌ **BLOCKER** |
| ssl_provision (update) | UPDATE | **НЕТ** (в маппинге φ7 — init контекст) | ❌ **BLOCKER** |
| deploy_modules (update) | UPDATE | **НЕТ** (в маппинге φ8 — init контекст) | ❌ **BLOCKER** |
| provision_llm_keys | UPDATE | **НЕТ** | ❌ **BLOCKER** |
| healthcheck | UPDATE | **НЕТ** | ❌ **BLOCKER** |
| converge (update) | UPDATE | **НЕТ** (в маппинге φ8.5 — init контекст) | ❌ **BLOCKER** |
| deploy_context (update) | UPDATE | **НЕТ** (в маппинге φ8 — init контекст) | ❌ **BLOCKER** |

**Итого:** 12 BLOCKER-пробелов в migration mapping из 30 ключей.

#### 2.5.3 DRIFT-001: docker_auth в двух группах

`docker_auth` (INIT_STEPS index 5) замаплен в φ1 (system_bootstrap), НО логически относится к Docker-конфигурации, которая в φ3 (platform_setup). При этом `docker_config` (который не существует как ключ!) указан в φ3. Фактическая реализация `docker_auth` в state_machine.py (L1089-1104) — это Docker Hub registry auth, а не system packages. Рекомендация: перенести `docker_auth` из φ1 в φ3 ИЛИ переименовать в маппинге.

#### 2.5.4 DRIFT-002: UPDATE keys не имеют маппинга

DevPlan §2.5 (L277-281) описывает маппинг для UPDATE-фаз φ9-φ13, НО ссылается на несуществующие ключи:
- φ9 secrets-update → "decrypt_secrets (update context)" — **ключа decrypt_secrets в UPDATE_STEPS нет!**
- φ10 node-config-update → "read_node_yaml (update context)" — **ключа read_node_yaml в UPDATE_STEPS нет!**
- φ11 registry-update → "ghcr_auth (update context)" — **ключа ghcr_auth в UPDATE_STEPS нет!**

Фактические UPDATE_STEPS ключи:
```
verify_core, provision, deliver_overlays, ssl_provision, deploy_modules,
provision_llm_keys, healthcheck, converge, deploy_context
```

Ни один из них не замаплен в MIGRATION_MAP. Это означает, что при миграции state.json все UPDATE-ключи будут потеряны, и update-фазы φ9-φ13 запустятся с чистого листа на production-нодах.

**Рекомендация:** Полностью переработать маппинг UPDATE-ключей:

| Старый UPDATE-ключ | Новая фаза |
|-------------------|-----------|
| verify_core (update) | φ10 node_config_update |
| provision | φ9 secrets_update (или отдельная фаза) |
| deliver_overlays | φ9 secrets_update |
| ssl_provision (update) | φ12 deploy_update |
| deploy_modules (update) | φ12 deploy_update |
| provision_llm_keys | φ12 deploy_update |
| healthcheck | φ12 deploy_update |
| converge (update) | φ13 converge_update |
| deploy_context (update) | φ12 deploy_update |

Или — альтернативно — признать, что φ9-φ11 в UPDATE-режиме — это НОВЫЕ фазы без старых ключей (pending на первом запуске), и задокументировать это явно.

#### 2.5.5 DRIFT-003: secrets_init vs ensure_secrets

В INIT_STEPS (state_machine.py L100-102):
```
"decrypt_secrets",  # 12
"ensure_secrets",   # 13
"secrets_init",     # 14
```

MIGRATION_MAP (L305):
```python
"secrets_provision": ["decrypt_secrets", "ensure_secrets"],
```

Ключ `secrets_init` (INIT_STEPS[13]) отсутствует в маппинге! Функции разные:
- `ensure_secrets`: генерирует отсутствующие autogen-пароли через secrets_manager
- `secrets_init`: инициализирует сервисные пароли через secrets-init.sh

Это ДВА РАЗНЫХ старых ключа, но маппинг предполагает только один. Рекомендация: добавить `secrets_init` в MIGRATION_MAP для φ4 или объединить с `ensure_secrets` с учётом того, что T20 удаляет дубликат `_step_secrets_init`.

#### 2.5.6 DRIFT-004: platform_dirs и docker_config не существуют

MIGRATION_MAP (L304):
```python
"platform_setup": ["platform_dirs", "docker_config", "metrics_cron"],
```

`platform_dirs` и `docker_config` **не являются ключами** в текущем state.json. `metrics_cron` существует (shell-only step). Рекомендация: заменить на фактические старые ключи для φ3, например: `docker_auth` (перенести из φ1).

---

## §3. Специальные проверки (по запросу пользователя)

### 3.1 Число фаз (14) — консистентность

| Источник | Значение | Статус |
|----------|---------|--------|
| Title: "32→14" | 14 | ✅ |
| §2 INIT: φ1-φ8.5 | 9 init фаз | ✅ |
| §2 UPDATE: φ9-φ13 | 5 update фаз | ✅ |
| §3 phases.py функции | 14 функций (phase_system_bootstrap...phase_converge_update) | ✅ |
| AC1: "14 значений в BootstrapPhase enum (φ1-φ13 + φ8.5)" | 14 | ✅ |
| AC7: "_phase_dependency_graph содержит все 14 фаз" | 14 | ✅ |
| AC12: "14 фаз (не 23) выполняются корректно" | 14 | ✅ |
| T1: "BootstrapPhase enum: 14 значений" | 14 | ✅ |
| DD1: "14 — это минимальное число" | 14 | ✅ |

**Verdict:** Число 14 консистентно во всём DevPlan. ✅

**Finding PHI-NAMING [MINOR]:** φ8.5 — нецелочисленный идентификатор фазы. В enum, JSON-ключах и state.json это создаёт неудобства (`.` в имени ключа). Рекомендация: использовать φ9 для converge-services (init) и сдвинуть update-фазы на φ10-φ14, получив строго последовательную нумерацию 1-14.

### 3.2 precondition_check() vs _phase_dependency_graph

**Finding PRECON-CONFLICT [MAJOR]:** Два механизма проверки зависимостей пересекаются без чёткого разделения:

| Механизм | T# | Описание в DevPlan | Тип проверок |
|----------|----|--------------------|--------------|
| `BootstrapState.precondition_check()` | T3 | «для каждой группы проверяет prerequisites. BLOCK если precondition не satisfied» | **Не уточнён** — может быть и environmental (root access), и inter-phase (φ4 done → φ6) |
| `_phase_dependency_graph` | T4 | «dict с явными зависимостями для всех 14 фаз» | Inter-phase: φ2→φ1, φ8→φ4/φ6/φ7 |

**Конфликт:** В §2 precondition φ6 гласит: "Precondition: φ4 OK (needs secrets for docker login)". Это inter-phase зависимость, которая должна быть в `_phase_dependency_graph`, но описана как precondition. DD3 (L576-577): "precondition BLOCKS, а не WARN" — относится к inter-phase блокировкам, которые дублируются с графом зависимостей.

**Риск:** Если `precondition_check()` и `_phase_dependency_graph` реализуют одну и ту же проверку (φ4 done? → BLOCK φ6) с разной логикой, возникнет divergence. При изменении одной проверки другая останется старой → silent inconsistency.

**Рекомендация:** Чётко разделить:
- `precondition_check()` — **только** environmental проверки (root, age-key exists, node.yaml readable)
- `_phase_dependency_graph` — **только** inter-phase зависимости (φN требует φM done)
- Никакого дублирования проверок между ними.

### 3.3 migrate_state_to_phases() — покрытие старых ключей

**Verdict:** ❌ **BLOCKER** — 12 старых ключей из 30 не покрыты маппингом. Детали в §2.5.2.

**Дополнительная находка — duplicate key handling:** Ключи `verify_core`, `converge`, `deploy_context` присутствуют и в INIT, и в UPDATE с ОДИНАКОВЫМ именем. Текущий state.json НЕ различает init/update контекст для этих ключей (state_machine.py использует один и тот же `step_name`). MIGRATION_MAP маппит:
- `verify_core` → φ5 node_configuration (init)
- `converge` → φ8.5 converge_services (init)
- `deploy_context` → φ8 deploy_services (init)

Но эти же ключи используются в UPDATE-режиме и должны маппиться в другие фазы (φ10, φ13, φ12 соответственно). **Текущий маппинг потеряет update-статус для этих ключей.**

### 3.4 Дублирование функций между phases.py и state_machine.py

**Анализ:** В целевом дизайне (§3, DD2):
- `phases.py`: 14 функций `phase_*()` — бизнес-логика фаз
- `state_machine.py`: `BootstrapPhase` enum, `BootstrapState`, `_execute_phase()`, `_execute_grouped_phase()`, `_resume_phase()`, `_phase_dependency_graph` — оркестрация

**Verdict:** Разделение ответственности спроектировано корректно. ✅

**Однако** есть риск остаточного дублирования после миграции:
- Текущий `state_machine.py` (2115 LOC) содержит ВСЮ бизнес-логику в `_execute_init_step()` (L1042-1191) и `_execute_update_step()`. При извлечении в `phases.py` нужно гарантировать, что оригинальные `elif step_name == "..."` блоки УДАЛЕНЫ из state_machine.py, а не закомментированы.
- T2 говорит «извлечь бизнес-логику», но явно не требует удаления оригиналов. AC2 удаляет `_step_deploy_context` из steps.py, но нет явного AC для удаления старых `elif`-блоков из state_machine.py после извлечения.

**Рекомендация:** Добавить AC: `grep "elif step_name ==" core/internal/bootstrap/lifecycle/state_machine.py` → empty (после извлечения всей бизнес-логики в phases.py).

---

## §4. Дополнительные находки

### 4.1 Отсутствующие секции

| Секция | Статус | Риск |
|--------|--------|------|
| §Rollback | ❌ ОТСУТСТВУЕТ | **MAJOR** — нет процедуры отката для production-нод при неудачном деплое 14-фазной версии |
| §Risk | ❌ ОТСУТСТВУЕТ | MINOR — риски не формализованы (хотя DD1-DD7 частично покрывают) |
| §Migration (plan-level) | ⚠️ ЧАСТИЧНО | §2.5 покрывает только state.json миграцию, но не миграцию кода/инфраструктуры |
| §Dependencies | ⚠️ ЧАСТИЧНО | Упомянуто в $ARTIFACT_CONTRACT REQUIRES, но без дерева зависимостей |

**Finding ROLLBACK [MAJOR]:** Отсутствует процедура отката. Production-ноды с ~23 старыми ключами в state.json. При деплое новой версии: (1) новый код задеплоен через SCP, (2) migrate_state_to_phases() конвертирует state.json, (3) новый bootstrap запускается с 14 фазами. Если шаг 3 падает — нужна процедура восстановления старого state.json и старого кода. DevPlan упоминает «сохраняет для rollback» (L297), но не описывает КАК откатить. Рекомендация: добавить §9 Rollback Plan с конкретными командами.

### 4.2 Некорректные данные в §1 Current State

**Finding CONVERGE-IDX [MINOR]:** L63: «converge (20 UPDATE)» — но в UPDATE_STEPS (L114-124) converge — это индекс 8 (не 20). Индекс 20 — это INIT_STEPS индекс для converge.

### 4.3 AC — измеримость и ложные срабатывания

| AC | Статус | Комментарий |
|----|--------|-------------|
| AC1 | ✅ | grep на 14 значений enum |
| AC2 | ✅ | grep _step_deploy_context в steps.py → empty |
| AC3 | ✅ | grep SHELL_TO_PYTHON_STEP → empty |
| AC4 | ✅ | grep _step_* в steps.py → empty |
| AC5 | ⚠️ MINOR | `grep "\.done"` — матчит `.done` в комментариях, именах переменных, etc. Рекомендация: `grep "touch.*\.done\|\.done.*touch\|mark.*\.done"` |
| AC6 | ✅ | precondition_check unit-тесты |
| AC7 | ✅ | _phase_dependency_graph покрытие |
| AC8 | ✅ | python3 -c import check |
| AC9 | ✅ | grep step_1_\|checkpoint_step → empty |
| AC10-14 | ✅ | Измеримы |

### 4.4 DevPlan Filename Convention

**Finding FILENAME [MINOR]:** DevPlan называется `DevPlan.md` без NN-префикса. По грамматике именования артефактов (`{NN}-{Type}.md`), ожидается `01-DevPlan.md`. Это не блокирует, но нарушает конвенцию.

---

## §5. Итоговая таблица находок

| ID | Severity | Категория | Описание | Локация |
|----|----------|-----------|----------|---------|
| T17-GAP | **BLOCKER** | Task Coverage | T17 отсутствует в нумерации (T1-T16, T18-T21) | §4 |
| DRIFT-MIG-001 | **BLOCKER** | Migration | `secrets_init` (INIT_STEPS[13]) не замаплен — старый ключ потерян | §2.5.2 |
| DRIFT-MIG-002 | **BLOCKER** | Migration | `sudoers` (INIT_STEPS[16]) не замаплен — старый ключ потерян | §2.5.2 |
| DRIFT-MIG-003 | **BLOCKER** | Migration | `platform_dirs` и `docker_config` в MIGRATION_MAP — несуществующие ключи! | §2.5.2, L304 |
| DRIFT-MIG-004 | **BLOCKER** | Migration | 9 UPDATE_STEPS ключей не замаплены — update-статус потерян | §2.5.2, §2.5.4 |
| DRIFT-MIG-005 | **BLOCKER** | Migration | Duplicate keys (verify_core, converge, deploy_context) в init И update — маппятся только в init-фазы | §3.3 |
| DRIFT-MIG-006 | **BLOCKER** | Migration | docker_auth замаплен в φ1 (system), но семантически это Docker-конфигурация (φ3) | §2.5.3 |
| PRECON-CONFLICT | **MAJOR** | Design | precondition_check() и _phase_dependency_graph перекрываются без разделения | §3.2, T3/T4 |
| ROLLBACK | **MAJOR** | Plan Completeness | Отсутствует §Rollback — нет процедуры отката для production | §4.1 |
| IT-PATH | **MAJOR** | File Manifest | test_bootstrap_dry_run.py упомянут в §8, но не в §5 File Manifest | §2.2 |
| SEC-DUP | **MAJOR** | Code | _step_secrets_init() дублирован в state_machine.py:1846 и steps.py:106 | §2.4 |
| MIGR-SEC-INIT | **MAJOR** | Migration | ensure_secrets и secrets_init — два разных ключа, маппятся в один слот φ4 | §2.5.5 |
| T-NONSEQ | MINOR | Task Numbering | Нумерация задач не соответствует порядку волн (T9 после T12) | §1.4 |
| PHI-NAMING | MINOR | Naming | φ8.5 — нецелочисленный идентификатор фазы | §3.1 |
| CONVERGE-IDX | MINOR | Data Error | L63: «converge (20 UPDATE)» — неверный индекс (должен быть 8) | §4.2 |
| AC5-GREP | MINOR | AC Precision | grep "\.done" слишком широкий — ложные срабатывания | §4.3 |
| FILENAME | MINOR | Convention | DevPlan.md без NN-префикса | §4.4 |

---

## §6. Semantic Verdict

**Verdict: DRIFTED (CRITICAL)**

**Обоснование:**
- **7 BLOCKER:** Migration mapping содержит критические пробелы — 12 из 30 старых ключей не покрыты. При деплое на production-ноду `migrate_state_to_phases()` потеряет состояние для `sudoers`, `secrets_init`, всех UPDATE-ключей, и будет ссылаться на несуществующие ключи `platform_dirs`/`docker_config`. Это прямой риск даунтайма.
- **T17-GAP:** Отсутствующая задача — план неполон.
- **5 MAJOR:** Отсутствие rollback-плана, конфликт precondition/dependency, незадекларированный integration test path, дубликат _step_secrets_init.
- **6 MINOR:** Косметические и конвенциональные проблемы.

**Health Score:** 100 − (7×5 + 5×3 + 6×1) = 100 − (35 + 15 + 6) = **44/100**

**Рекомендация:** DevPlan НЕ готов к реализации. Требуется:
1. Полная переработка MIGRATION_MAP с учётом всех 30 фактических ключей (BLOCKER)
2. Добавление T17 или перенумерация (BLOCKER)
3. Разделение precondition_check() и _phase_dependency_graph (MAJOR)
4. Добавление §Rollback с конкретными командами отката (MAJOR)
5. Фикс AC5 grep-паттерна, индекса converge, naming φ8.5 (MINOR)

**Делегирование:** Architect — переработка DevPlan с учётом находок.

$END_VERIFICATION_REPORT
