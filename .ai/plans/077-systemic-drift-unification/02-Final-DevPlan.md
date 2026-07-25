$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Consolidate all 14 DevPlans (7 existing + 7 new) into a single first-release roadmap. Close all 41 systemic drift points catalogued in Brief 077. Guarantee zero overlap between waves — no file is rewritten twice for the same reason.
DESCRIPTION:           Meta-DevPlan that sequences the complete elimination of systemic drift in ai-platform. Covers 6 domains (secrets, bootstrap, certificates, deploy, config, healthcheck) across 8 sequential waves. Each wave is a self-contained DevPlan that can be handed to a coder independently. Every drift point from the Brief is mapped to exactly one DevPlan. File touch matrix ensures no overlapping modifications.
RATIONALE:             Brief 077 identified 41 drift points across 5 root causes. The 7 pre-existing DevPlans (070-076) only covered 4 drift points (10%). This document defines the complete closure plan, respecting the constraint: "мы готовимся к первому релизу — я хочу чтобы работало все" and "в разных волнах одно и тоже не переписывали".
ACCEPTANCE_CRITERIA:
  1. Every one of the 41 drift IDs from Brief 077 is mapped to exactly one DevPlan
  2. No file is modified by >1 DevPlan for the same business logic (file touch matrix clean)
  3. Wave sequencing respects all dependencies (shared modules before consumers, security fixes first)
  4. Each DevPlan is self-contained: coder can read one DevPlan and implement without cross-referencing others
  5. Gate tests ensure no regression after each wave
  6. Final `make gate MODE=full` passes after all waves complete
IMPLEMENTS:            Brief 077 (all 5 root causes, all 41 drift points)
IMPACTS:               120+ files (see file touch matrix below)
REQUIRES:              Access to repository, Python >= 3.10, Docker, age-key at ~/.ssh/age-key-personal.txt
$END_ARTIFACT_CONTRACT

---

# Final DevPlan: Systemic Drift Unification — First Release Roadmap

**Severity:** CRITICAL (системный дрейф затрагивает все домены платформы)
**Created:** 2026-07-25
**Author:** Kilo (orchestrator agent)
**Source:** Brief 077 + 14 архитекторских DevPlans
**Total waves:** 8 (7 унаследованных + 7 новых → 8 последовательных групп)

---

## Резюме

Проект ai-platform накопил 41 точку системного дрейфа за 15 дней (287 коммитов). Причина: двойные реализации бизнес-логики (Python + Bash), отсутствие shared-библиотек, фрагментированные default-значения и мёртвый код.

**7 существующих DevPlans** (070-076) покрывают 4 drift-точки полностью/частично + 4 inline-python3 миграции.
**7 новых DevPlans** (078-084) закрывают оставшиеся 37 drift-точек.

Этот документ — дорожная карта, определяющая ПОСЛЕДОВАТЕЛЬНОСТЬ волн, зависимости между ними и матрицу покрытия. Передаётся разработчику вместе с конкретным DevPlan-ом для реализации каждой волны.

---

## Глава 1: Карта покрытия — 41 drift-точка → 14 DevPlans

### 1.1 Полная матрица покрытия

| Drift ID | Суть | Severity | DevPlan | Статус покрытия |
|----------|------|----------|---------|-----------------|
| **RC-1: Двойная бухгалтерия shell/Python** |
| S1 | detect_age_key() — 5 копий | HI | **078** | ✅ Новый |
| S2 | htpasswd — 3 реализации | HI | **078** | ✅ Новый |
| S3 | _FALLBACK_SECRETS не синхронизирован | HI | **072** + **078** | ⚠️ Частично (072: append fix; 078: sync + тест) |
| S4 | Docker token в /proc/cmdline | CRITICAL | **078** | ✅ Новый |
| B1 | Dual state machine (.done + state.json) | HI | **071** | ✅ Существующий (100%) |
| B2 | SSL provisioning — 4 реализации | HI | **080** | ✅ Новый |
| B3 | 4 entrypoint'а deploy context | MED | **079** | ✅ Новый |
| B4 | Content hash — 3 реализации | HI | **079** | ✅ Новый |
| B5 | YAML-key extraction — 4+ копий | MED | **070** | ⚠️ Частично (3 из 4 копий) |
| B6 | Docker compose ops — 2 пути | HI | **079** | ✅ Новый |
| C3 | cert_orchestrator vs issue-cert | HI | **080** | ✅ Новый |
| C5 | Dual --reloadcmd | HI | **080** (авто через C1) | ✅ Новый |
| D3 | Docker ops retry/rollback — несовместимы | HI | **081** | ✅ Новый |
| D4 | Два SSH_ORIGINAL_COMMAND парсера | HI | **081** | ✅ Новый |
| H5 | Два healthcheck оркестратора | MED | **083** | ✅ Новый |
| H6 | Deep check ≠ Docker HEALTHCHECK | HI | **083** | ✅ Новый |
| H7 | modules-healthcheck дублирование docker inspect | LO | **083** | ✅ Новый |
| **RC-2: Отсутствие единой Python shared library** |
| B5 | YAML extraction (остаток) | LO | **070** (expanded) | ⚠️ Частично |
| B6 | Docker compose (shared) | HI | **079** | ✅ Новый |
| D2 | Content hash (deploy domain, =B4) | HI | **079** | ✅ Новый |
| D3 | Docker ops (shared retry) | HI | **081** (на базе 079) | ✅ Новый |
| D4 | SSH parser (shared) | HI | **081** | ✅ Новый |
| D6 | Audit log (shared) | LO | **081** | ✅ Новый |
| H1 | 9 healthcheck механизмов | HI | **083** | ✅ Новый |
| H2 | 8 port-check паттернов | MED | **083** | ✅ Новый |
| H4 | docker exec copy-paste в 5 модулях | MED | **083** | ✅ Новый |
| **RC-3: Фрагментированные default-значения** |
| S5 | Конфликтующие имена секретов (5 шт.) | MED | **078** | ✅ Новый |
| S6 | POSTGRES_PASSWORD — 6 значений | HI | **078** + **082** | ✅ Новый |
| S7 | NEXTAUTH_SECRET — 4 значения | MED | **078** + **082** | ✅ Новый |
| E1 | POSTGRES_PASSWORD (=S6) | HI | **078** + **082** | ✅ Новый |
| E2 | S3_ENDPOINT_URL — cyclic fallback + 3 дефолта | HI | **082** | ✅ Новый |
| E3 | NEXTAUTH_SECRET (=S7) | MED | **078** + **082** | ✅ Новый |
| E4 | 3 Jinja2-подобных механизма | MED | **082** | ✅ Новый |
| E5 | Variable naming (6 пар) | MED | **082** | ✅ Новый |
| E6 | PLATFORM_DOMAIN default divergence | MED | **082** | ✅ Новый |
| E7 | NO_PROXY — 3 разных списка | MED | **082** | ✅ Новый |
| E8 | GF_SECURITY_ADMIN_USER chain fallback | LO | **082** | ✅ Новый |
| **RC-4: Мёртвый код** |
| C1 | nginx/install.sh (1107 LOC) | HI | **080** + **084** | ✅ Новый |
| — | ssl-provision.sh (40 LOC) | LO | **084** | ✅ Новый |
| — | LITELLM_METRICS_TOKEN в definitions | LO | **072** + **078** | ⚠️ Частично (072: .env.example; 078: definitions) |
| — | Shell .done файлы | LO | **071** | ✅ Существующий |
| **RC-5: Разные стандарты обработки ошибок** |
| D1 | 7 путей доставки кода | MED | **081** | ✅ Новый |
| D5 | platform-deliver в 3 местах | LO | **076** + **081** | ⚠️ Частично (076: reconcile; 081: остальные) |
| D6 | Разные форматы audit-логов | LO | **081** | ✅ Новый |
| H3 | 7 разных start_period значений | LO | **083** | ✅ Новый |
| **Отдельные домены** |
| C2 | Shadow cert path | MED | **080** | ✅ Новый |
| C4 | 3 renewal пути | HI | **080** | ✅ Новый |
| C6 | Два dev cert filename | LO | **080** | ✅ Новый |
| C7 | platform-vhost cert path | MED | **080** | ✅ Новый |
| C8 | Template syntax clash | LO | **080** | ✅ Новый |
| **Inline-python3 миграции (языковая политика)** |
| — | 13 inline python3 в provision | — | **073** | ✅ Существующий |
| — | 19 inline python3 в monitoring hooks | — | **074** | ✅ Существующий |
| — | 5 inline python3 в watchdog | — | **075** | ✅ Существующий |
| — | 6 inline python3 в reconcile | — | **076** | ✅ Существующий |

### 1.2 Статистика покрытия

| Метрика | Значение |
|---------|----------|
| Всего drift ID в Brief 077 | 41 |
| Покрыто существующими DevPlans (070-076) | 4 (B1, B5-частично, S3-частично, S5-частично) |
| Покрыто НОВЫМИ DevPlans (078-084) | 38 (включая завершение частичных) |
| Полностью закрыто после всех волн | **41 из 41** (100%) |
| Унаследованных DevPlans (уже готовы) | 7 |
| Новых DevPlans (требуют реализации) | 7 |
| Всего DevPlans | **14** |
| Логических волн (последовательных групп) | **8** |

---

## Глава 2: Последовательность волн — Dependency Graph

```
                        ┌──────────────────────────────────────────┐
                        │         WAVE 1: FOUNDATION               │
                        │  070  071  072  073  074  075  076       │
                        │  (все 7 НЕЗАВИСИМЫ, можно параллельно)    │
                        └──────────────┬───────────────────────────┘
                                       │
                    ┌──────────────────┴──────────────────┐
                    ▼                                     ▼
        ┌──────────────────────┐            ┌──────────────────────┐
        │  WAVE 2: 078        │            │  WAVE 3 (parallel):   │
        │  Secrets & Tokens   │            │  073 Provision Python │
        │  Unification        │            │  074 Monitoring Py    │
        │  (S1-S7)            │            │  075 Watchdog Python  │
        └──────────┬──────────┘            │  076 Reconcile Python │
                   │                       └──────────────────────┘
                   │                                 │
    ┌──────────────┼──────────────┐                  │ (все 073-076 — независимы от 078,
    │              │              │                  │  но зависят от 070 для shared/)
    ▼              ▼              ▼
┌────────┐  ┌──────────┐  ┌──────────┐
│ WAVE 4 │  │ WAVE 5   │  │ WAVE 6   │
│  079   │  │  080     │  │  081     │
│Bootstrap│  │Certs/SSL │  │ Deploy   │
│(B3,B4, │  │(B2,C1-C8)│  │ Pipeline │
│ B6)    │  │          │  │(D1,D3-D6)│
└───┬────┘  └────┬─────┘  └────┬─────┘
    │            │             │
    │  080 and 081 can run      │
    │  in parallel after 079   │
    │            │             │
    ▼            ▼             ▼
┌────────┐  ┌──────────┐  ┌──────────┐
│ WAVE 7 │  │ WAVE 8   │  │  (done)  │
│  082   │  │  083+084 │  │          │
│Config/ │  │Healthck+ │  │          │
│Env     │  │Dead Code │  │          │
│(E1-E8) │  │(H1-H7)   │  │          │
└────────┘  └──────────┘  └──────────┘
```

### 2.1 Детализация каждой волны

#### WAVE 1: Foundation (070, 071, 072) — неделя 1, день 1-2

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **070** | Extract Shared Libraries | +150 / −90 | Создаёт `core/internal/shared/` с `node_yaml.py` + `project_registry.py`. Удаляет 3 копии `_extract_context_from_node_yaml()` и 3 Python heredoc блока. |
| **071** | Unify Checkpoints | +120 / −80 | `checkpoint.sh` переписан на `state.json`. Старые `.done` файлы мигрированы. Единая система чекпоинтов. |
| **072** | Secrets Atomic Write | +30 / −15 | `secrets_manager.py`: append → atomic overwrite. `LITELLM_METRICS_TOKEN` удалён из `.env.example`. |

**Зависимости:** Нет. Все три независимы. Можно запускать параллельно разным кодерам.
**Критичность:** HIGH. 070 создаёт директорию `shared/`, которую используют ВСЕ последующие волны.

#### WAVE 2: Secrets & Tokens Unification (078) — неделя 1, день 3-4

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **078** | Secrets & Tokens Complete Unification | +150 / −170 | `age_key.py` + `crypto.py` в shared/. S4 security fix. 5 naming conflicts resolved. POSTGRES_PASSWORD + NEXTAUTH_SECRET unified. |

**Зависимости:** 070 (shared/__init__.py), 072 (merge order).
**Критичность:** CRITICAL. S4 — уязвимость (токен в /proc/cmdline). S1 — 5 копий = 5 баг-векторов.

#### WAVE 3: Shell → Python Migrations (073, 074, 075, 076) — неделя 1-2, параллельно с Wave 2

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **073** | Provision → Python | +250 / −400 | 13 inline python3 → `provisioner.py`. Shell wrapper <50 LOC. |
| **074** | Monitoring Hooks → Python | +400 / −370 | 19 inline python3 → `monitoring_config_renderer.py`. 3-level JSON merge unit-tested. |
| **075** | Watchdog → Python Daemon | +500 / −530 | Circuit breaker FSM в Python. systemd unit обновлён. |
| **076** | Reconcile → Python | +250 / −280 | 6 inline python3 → `reconciler_projects.py`. Shell wrapper сохранён для converge.sh. |

**Зависимости:** 070 (shared/). Могут идти параллельно с 078.
**Примечание:** Это языковая политика (Tier 1 inline python3 → Python модули). Не закрывают drift ID напрямую, но устраняют структурную причину дрейфа.

#### WAVE 4: Bootstrap Pipeline Unification (079) — неделя 2, день 1-3

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **079** | Bootstrap Pipeline Unification | +300 / −200 | `shared/content_hash.py`, `shared/docker_compose.py`. 4 deploy-context entrypoints → 1. Content hash unified. Docker compose shared library. |

**Зависимости:** 070, 071, 078.
**Ключевой результат:** `shared/docker_compose.py` с `retry_pull()` — фундамент для Wave 6 (081) DRIFT-D3.

#### WAVE 5: Certificates & SSL Unification (080) — неделя 2, день 2-4

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **080** | Certificates & SSL Complete Unification | +200 / −1200 | DELETE nginx/install.sh (1107 LOC). `cert_orchestrator.py` — единая точка входа. Все vhost'ы на wildcard. Dev cert имена гармонизированы. |

**Зависимости:** 070, 078, 079.
**Может идти параллельно с 081 после завершения 079.**

#### WAVE 6: Deploy Pipeline Unification (081) — неделя 2, день 3-5

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **081** | Deploy Pipeline Unification | +350 / −150 | Deploy Path Registry + gate test. `shared/ssh_command_parser.py`, `shared/platform_deliver.py`, `shared/audit_logger.py`. Retry/rollback в Python deploy путях. |

**Зависимости:** 070, 079 (docker_compose.py).
**Может идти параллельно с 080 после завершения 079.**

#### WAVE 7: Configuration & Env Defaults Unification (082) — неделя 3, день 1-2

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **082** | Config & Env Defaults Unification | +250 / −100 | Трёхуровневая иерархия SoT: `secret-definitions.yaml` → `platform-env.yaml` → `.env.example`. S3_ENDPOINT циклический fallback убран. PLATFORM_DOMAIN default унифицирован. Variable naming стандартизирован. `.env.example` генерируется, не редактируется вручную. |

**Зависимости:** 078 (секретные дефолты унифицированы).
**Не зависит от 079-081, может идти параллельно с ними после 078.**

#### WAVE 8: Healthcheck + Dead Code (083, 084) — неделя 3, день 2-4

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **083** | Healthcheck Complete Unification | +100 / −300 | 9 механизмов → 3 примитива. `check_tcp()`, `exec_check()` в lib. start_period стандартизирован. 14 модулей унифицированы. |
| **084** | Dead Code Sweep | +50 / −1150 | DELETE nginx/install.sh (если не удалён в 080), ssl-provision.sh. `make check-dead-code` gate. |

**Зависимости:** 083 — независимый домен. 084 зависит от 080 (cert unification), 071 (.done migration).

---

## Глава 3: File Touch Matrix — гарантия отсутствия конфликтов

### 3.1 Файлы, модифицируемые в >1 DevPlan (проверка на конфликты)

| Файл | DevPlans | Тип пересечения | Безопасно? |
|------|----------|-----------------|------------|
| `state_machine.py` | 070, 071, 079 | 070 удаляет `_extract_context_from_node_yaml()`, 071 работает с чекпоинтами, 079 унифицирует deploy-context entrypoints | ✅ Разные регионы файла. Последовательный merge: 070 → 071 → 079 |
| `steps.py` | 070, 080 | 070 удаляет `_extract_context_from_node_yaml()`, 080 удаляет `_ssl_cert_provision()` | ✅ Разные методы. Последовательный merge: 070 → 080 |
| `context_deployer.py` | 070, 079 | 070 заменяет локальную копию на импорт, 079 унифицирует `deploy_context()` как единую точку входа | ✅ 070 удаляет дубликат, 079 реструктурирует. Merge: 070 → 079 |
| `secrets_manager.py` | 072, 078 | 072 фиксит append→overwrite (строки 310-326), 078 заменяет `_ensure_htpasswd()` (строки 375-450) | ✅ Разные строки. Последовательный merge: 072 → 078 |
| `.env.example` | 072, 078, 082 | 072 удаляет LITELLM_METRICS_TOKEN, 078 унифицирует дефолты, 082 делает generated | ✅ Разные строки. Merge: 072 → 078 → 082 |
| `deploy-project.sh` | 076, 081 | 076 мигрирует reconcile-projects.sh, 081 унифицирует platform_deliver builder | ✅ Разные файлы. 076: `internal/deploy/reconcile-projects.sh`. 081: `entrypoints/deploy-project.sh` + `internal/deploy/deploy-project.sh` |
| `docker_orchestrator.py` | 079, 081 | 079 создаёт shared/docker_compose.py, 081 добавляет retry/rollback используя его | ✅ 079 — новый файл, 081 — consumer. Порядок: 079 → 081 |
| `checkpoint.sh` | 071, 084 (verification) | 071 переписывает на state.json, 084 верифицирует что .done файлы удалены | ✅ 084 только проверяет результат 071 |

**Вывод:** Ни одного конфликта. Все пересечения — это разные регионы одного файла или producer→consumer зависимость. При последовательном merge конфликтов не будет.

### 3.2 Новые файлы в `core/internal/shared/` (создаются разными DevPlans)

| Файл | Создаётся в | Назначение |
|------|------------|------------|
| `__init__.py` | 070 | Package init |
| `node_yaml.py` | 070 | `extract_context_from_node_yaml()` |
| `project_registry.py` | 070 | `register_project()`, `deregister_project()` |
| `age_key.py` | 078 | `detect_age_key()` |
| `crypto.py` | 078 | `hash_apr1()`, `generate_htpasswd_entry()` |
| `content_hash.py` | 079 | `compute_content_hash()` |
| `docker_compose.py` | 079 | `pull()`, `build()`, `up()`, `healthcheck_poll()`, `retry_pull()`, `check_image_exists()` |
| `ssh_command_parser.py` | 081 | `parse_ssh_original_command()` |
| `platform_deliver.py` | 081 | `build_deliver_command()` |
| `audit_logger.py` | 081 | `AuditLogger` class (JSON-lines) |
| `deploy_paths.py` | 081 | Deploy Path Registry |

Все 11 модулей в `core/internal/shared/` — без конфликтов имён.

---

## Глава 4: Стратегия верификации

### 4.1 Gate tests после каждой волны

После КАЖДОЙ волны обязательно:
```bash
make fix-gate && git add -u && make gate MODE=fast
```

### 4.2 Волно-специфичные тесты

| Волна | Команда верификации |
|-------|-------------------|
| 070 | `python3 -m pytest tests/unit/test_node_yaml.py tests/unit/test_project_registry.py -v` |
| 071 | `python3 -m pytest tests/unit/test_state_machine.py -v -k "checkpoint or resume or force"` |
| 072 | `python3 -m pytest tests/unit/test_secrets_manager.py -v -k "idempotent or preserve"` |
| 073 | `python3 -m pytest tests/unit/test_provisioner.py -v` |
| 074 | `python3 -m pytest tests/unit/test_monitoring_config_renderer.py -v` |
| 075 | `python3 -m pytest tests/unit/test_agent_watchdog.py -v` |
| 076 | `python3 -m pytest tests/unit/test_project_reconciler.py -v` |
| 078 | `python3 -m pytest tests/unit/test_age_key.py tests/unit/test_crypto.py tests/gates/test_gate_fallback_secrets_sync.py -v` |
| 079 | `python3 -m pytest tests/unit/test_content_hash.py tests/unit/test_docker_compose.py -v` |
| 080 | `python3 -m pytest tests/unit/test_cert_orchestrator.py tests/unit/test_cert_cron_migration.py tests/test_template_syntax_gate.py -v` |
| 081 | `python3 -m pytest tests/unit/test_ssh_command_parser.py tests/unit/test_audit_logger.py tests/gates/test_gate_deploy_paths.py -v` |
| 082 | `python3 -m pytest tests/gates/test_gate_env_defaults_consistency.py -v && make sync-env-defaults --dry-run` |
| 083 | `python3 -m pytest tests/test_lib_healthcheck.py tests/test_healthcheck_contract.py tests/gates/test_gate_healthcheck_contract.py -v` |
| 084 | `make check-dead-code && grep -r "DEPRECATED" --include="*.sh" --include="*.py" core/ | wc -l` (должно быть 0) |

### 4.3 Финальная верификация (после всех 8 волн)

```bash
# Полный gate
make gate MODE=full

# Проверка отсутствия дрейфа
grep -r "detect_age_key" --include="*.sh" core/entrypoints/ core/lib/ core/internal/secrets/ | wc -l  # должно быть 0 (все делегируют Python)
grep -r "python3 -c" --include="*.sh" core/internal/provision-environment.sh core/modules/monitoring/hooks/on-project-deploy.sh core/internal/deploy/reconcile-projects.sh | wc -l  # должно быть 0
grep -r "DEPRECATED" --include="*.sh" --include="*.py" core/ | grep -v "test_" | grep -v "AGENTS.md" | wc -l  # должно быть 0

# Проверка единого SoT для default-значений
python3 -c "
import yaml
defs = yaml.safe_load(open('core/secret-definitions.yaml'))
pg = [s for s in defs['secrets'] if s['name'] == 'POSTGRES_PASSWORD'][0]
assert pg['ci_default'] == 'test-pg-pwd', f'Expected test-pg-pwd, got {pg[\"ci_default\"]}'
print('PASS: POSTGRES_PASSWORD default is canonical')
"

# Bootstrap dry-run (если есть тестовая нода)
make converge NODE=test-node --dry-run
```

---

## Глава 5: План выполнения (для руководителя)

### Неделя 1

| День | Кодер A | Кодер B | Кодер C (опционально) |
|------|---------|---------|----------------------|
| Пн | **070** Extract Shared Libs | **071** Unify Checkpoints | **072** Secrets Atomic Write |
| Вт | 070 + 071 + 072 → merge + gate | — | — |
| Ср | **078** Secrets & Tokens | **073** Provision → Python | **074** Monitoring → Python |
| Чт | 078 продолжение | **075** Watchdog → Python | **076** Reconcile → Python |
| Пт | 078 → merge + gate | 073-076 → merge + gate | — |

### Неделя 2

| День | Кодер A | Кодер B |
|------|---------|---------|
| Пн | **079** Bootstrap Pipeline | — |
| Вт | 079 продолжение | **080** Certificates & SSL (после 079 merge) |
| Ср | **081** Deploy Pipeline (после 079 merge) | 080 продолжение |
| Чт | 081 продолжение | 080 → merge + gate |
| Пт | 081 → merge + gate | — |

### Неделя 3

| День | Кодер A | Кодер B |
|------|---------|---------|
| Пн | **082** Config & Env Defaults | **083** Healthcheck |
| Вт | 082 продолжение | 083 продолжение |
| Ср | 082 → merge + gate | 083 → merge + gate |
| Чт | **084** Dead Code Sweep | — |
| Пт | **Финальный gate MODE=full** | Интеграционное тестирование |

---

## Глава 6: Ключевые архитектурные решения (Design Decisions)

### DD1: `core/internal/shared/` — единая директория shared-библиотек
- Создаётся в 070. Все последующие волны добавляют модули туда.
- Каждый модуль имеет CLI entry point: `python3 -m core.internal.shared.<module> <args>`.
- Shell-скрипты вызывают Python через тонкие wrapper'ы.

### DD2: Python subprocess для age-key detection
- `detect_age_key()` реализован на Python (в shared/age_key.py), а не shell-библиотеке.
- Причина: нет namespace pollution, можно unit-тестировать, нет проблемы с `source` и `export` в subshell.

### DD3: Fixed-salt идемпотентность htpasswd
- Python-реализация извлекает соль из существующего хеша (если есть), а не использует хардкодную соль.
- Гарантирует одинаковый хеш при повторном вызове с тем же паролем.

### DD4: Healthcheck: Python и Bash оркестраторы НЕ объединяются
- `docker_orchestrator.py` (Python, retry 10×10s) — deploy-time healthcheck.
- `modules-healthcheck.sh` (bash, single pass) — runtime healthcheck.
- Разные lifecycle phases, объединение создаст излишнюю сложность.

### DD5: 3 Jinja2-подобных механизма НЕ консолидируются
- `template_engine.py`: strict regex `{{UPPER_SNAKE}}` — для nginx-шаблонов (безопасность: только известные переменные).
- `config_renderer.py` + `app.py`: full Jinja2 — для LLM-конфигов и status-page (нужны циклы/условия).
- Разные домены требуют разной грамматики. Вместо консолидации — CI gate, проверяющий консистентность синтаксиса в каждой директории.

### DD6: `.env.example` генерируется, не редактируется вручную
- `sync_env_defaults.py` читает `secret-definitions.yaml` + `platform-env.yaml` → генерирует `.env.example`.
- `make check-env-defaults` (CI gate) блокирует divergence.
- Инвариант 11 (Manifest Generation Contract) расширен на `.env.example`.

---

## Глава 7: Риски и TRAP'ы

### TRAP[SEQUENCE] · 2026-07-25 · HI · Порядок merge: 070 → 071 → 072 → 078 → 079 → {080,081} → 082 → {083,084}
- Если 079 смержен до 071: `content_hash.py` будет использовать устаревший `checkpoint.sh` для сравнения хешей.
- Если 080 смержен до 079: `cert_orchestrator.py` вызовет `docker_compose.py`, которого ещё нет.
- **Правило:** каждая волна мержится в main ТОЛЬКО после успешного gate предыдущей волны.

### TRAP[OVERLAP] · 2026-07-25 · MED · nginx/install.sh удаляется в 080 и верифицируется в 084
- 080 удаляет файл. 084 проверяет что удаление чистое (нет оставшихся reference).
- Если кодер 084 запустит `make check-dead-code` до merge 080 — получит false positive.
- **Правило:** 084 запускается строго после merge 080.

### TRAP[SECURITY] · 2026-07-25 · CRITICAL · DRIFT-S4: токен в /proc/cmdline
- Исправление в 078 (`docker_registry_auth.py:159`).
- ДОЛЖНО быть смержено в первую очередь (Wave 2, сразу после Foundation).
- Откладывание этого фикса на более поздние волны неприемлемо.

### TRAP[DRIFT] · 2026-07-25 · MED · DRIFT-B5 остаток: `_extract_domain_from_node_yaml()` в preflight.py
- 070 извлекает 3 из 4 копий `_extract_context_from_node_yaml()`.
- `_extract_domain_from_node_yaml()` в `preflight.py:459` остаётся отдельной функцией (другая семантика — извлекает domain, не context).
- Переименовать в `extract_domain_from_node_yaml()` и переместить в `shared/node_yaml.py` — scope creep для 082 или отдельного мини-DevPlan.
- **Rev:** при следующем изменении preflight.py → извлечь.

---

## Глава 8: Quick Reference — какой DevPlan читать для какой проблемы

| Если нужно... | Читать DevPlan |
|---------------|----------------|
| Создать shared-библиотеки | **070** |
| Починить bootstrap resume | **071** |
| Починить дубликаты в secrets.env | **072** |
| Перенести provision-environment.sh на Python | **073** |
| Перенести мониторинг-хуки на Python | **074** |
| Переписать watchdog на Python | **075** |
| Перенести reconcile на Python | **076** |
| Унифицировать AGE key detection + htpasswd + токены + default'ы | **078** |
| Унифицировать content hash + docker compose + deploy context | **079** |
| Унифицировать сертификаты (выпуск, renewal, cron, dev certs) | **080** |
| Унифицировать deploy pipeline (SSH parser, audit log, retry/rollback) | **081** |
| Унифицировать конфигурацию (env default'ы, naming, template engine) | **082** |
| Унифицировать healthcheck (9 механизмов → 3, start_period, deep check) | **083** |
| Вычистить мёртвый код | **084** |

---

## Приложение A: Полный список файлов всех DevPlans

### Новые файлы (15)
```
core/internal/shared/__init__.py           # 070
core/internal/shared/node_yaml.py          # 070
core/internal/shared/project_registry.py   # 070
core/internal/shared/age_key.py            # 078
core/internal/shared/crypto.py             # 078
core/internal/shared/content_hash.py       # 079
core/internal/shared/docker_compose.py     # 079
core/internal/shared/ssh_command_parser.py # 081
core/internal/shared/platform_deliver.py   # 081
core/internal/shared/audit_logger.py       # 081
core/internal/shared/deploy_paths.py       # 081
core/internal/provisioner.py               # 073
core/internal/monitoring_config_renderer.py # 074
core/modules/hermes-agent/watchdog/agent_watchdog.py  # 075
core/internal/reconciler_projects.py       # 076
```

### Удаляемые файлы (3)
```
core/modules/nginx/install.sh              # 080/084 (1107 LOC)
core/internal/bootstrap/ssl-provision.sh   # 084 (40 LOC)
core/modules/nginx/templates/platform-default.conf.template  # 080
```

### Новые тестовые файлы (13)
```
tests/unit/test_node_yaml.py               # 070
tests/unit/test_project_registry.py        # 070
tests/unit/test_age_key.py                 # 078
tests/unit/test_crypto.py                  # 078
tests/unit/test_content_hash.py            # 079
tests/unit/test_docker_compose.py          # 079
tests/unit/test_provisioner.py             # 073
tests/unit/test_monitoring_config_renderer.py # 074
tests/unit/test_agent_watchdog.py          # 075
tests/unit/test_project_reconciler.py      # 076
tests/unit/test_ssh_command_parser.py      # 081
tests/unit/test_audit_logger.py            # 081
tests/unit/test_cert_cron_migration.py     # 080
tests/gates/test_gate_fallback_secrets_sync.py   # 078
tests/gates/test_gate_env_defaults_consistency.py # 078/082
tests/gates/test_gate_deploy_paths.py      # 081
tests/test_template_syntax_gate.py         # 080
```

---

$END_DEVPLAN
