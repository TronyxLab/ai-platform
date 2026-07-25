$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Consolidate all 14 DevPlans into a single first-release roadmap. Close all 42 systemic drift points (39 unique + 3 cross-domain duplicates: S6=E1, S7=E3, B4=D2) catalogued in Brief 077. Guarantee zero overlap between waves — no file is rewritten twice for the same reason.
DESCRIPTION:           Meta-DevPlan that sequences the complete elimination of systemic drift in ai-platform. Covers 6 domains (secrets, bootstrap, certificates, deploy, config, healthcheck) across 8 sequential waves. Each wave is a self-contained DevPlan that can be handed to a coder independently. Every drift point from the Brief is mapped to exactly one DevPlan. File touch matrix ensures no overlapping modifications.
RATIONALE:             Brief 077 identified 42 drift points (39 unique, 3 cross-domain dual-named IDs) across 5 root causes. The 14 DevPlans aim for 100% coverage. This document defines the complete closure plan, respecting the constraint: "мы готовимся к первому релизу — я хочу чтобы работало все" and "в разных волнах одно и тоже не переписывали".
ACCEPTANCE_CRITERIA:
  1. Every one of the 42 drift IDs from Brief 077 is mapped to exactly one DevPlan (including 3 cross-domain duplicates: S6=E1, S7=E3, B4=D2)
  2. No file is modified by >1 DevPlan for the same business logic (file touch matrix clean)
  3. Wave sequencing respects all dependencies (shared modules before consumers, security fixes first)
  4. Each DevPlan is self-contained: coder can read one DevPlan and implement without cross-referencing others
  5. Gate tests ensure no regression after each wave
  6. Final `make gate MODE=full` passes after all waves complete
  7. DevPlan 070 (FOUNDATION) is restored in working tree and implemented first — all subsequent waves depend on it
IMPLEMENTS:            Brief 077 (all 5 root causes, all 42 drift points)
IMPACTS:               120+ files (see file touch matrix below)
REQUIRES:              Access to repository, Python >= 3.10, Docker, age-key at ~/.ssh/age-key-personal.txt, DevPlan 070 restored to working tree
$END_ARTIFACT_CONTRACT

---

# Final DevPlan: Systemic Drift Unification — First Release Roadmap

**Severity:** CRITICAL (системный дрейф затрагивает все домены платформы)
**Created:** 2026-07-25
**Updated:** 2026-07-25 (audit correction — drift count 41→42, recovery path added)
**Author:** Kilo (orchestrator agent)
**Source:** Brief 077 + 14 архитекторских DevPlans
**Audited:** 2026-07-25 — VerificationReport 03 (score: 87/100, verdict: DRIFTED CRITICAL)
**Total waves:** 8 (1 foundation + 7 implementation)
**Implementation status:** **0% across all 14 DevPlans** (see §0 below)

---

## §0: AUDIT STATUS — 2026-07-25

### 0.1 Implementation Status

Проведён полный аудит всех 14 DevPlans (070-084). **Ни одна волна не реализована.** `core/internal/shared/` не создан. Все 42 drift-точки всё ещё присутствуют в кодовой базе.

| DevPlan | Название | Wave | Код | Тесты | Статус |
|---------|----------|------|-----|-------|--------|
| **070** | Extract Shared Libraries | 1 (FOUNDATION) | 0% | 0% | ❌ УДАЛЁН из working tree |
| **071** | Unify Checkpoints | 1 | 0% | 0% | ⚪ Не начат |
| **072** | Secrets Atomic Write | 1 | 0% | 0% | ⚪ Не начат |
| **073** | Provision → Python | 2b | 0% | 0% | ⚪ Не начат |
| **074** | Monitoring Hooks → Python | 2b | 0% | 0% | ⚪ Не начат |
| **075** | Watchdog → Python | 2b | 0% | 0% | ⚪ Не начат |
| **076** | Reconcile → Python | 2b | 0% | 0% | ⚪ Не начат |
| **078** | Secrets & Tokens Unif. | 2a | 0% | 0% | 🔒 BLOCKED by 070 |
| **079** | Bootstrap Pipeline Unif. | 3 | 0% | 0% | 🔒 BLOCKED by 070 |
| **080** | Certs & SSL Unification | 4 | 0% | 0% | 🔒 BLOCKED by 070 |
| **081** | Deploy Pipeline Unif. | 5 | 0% | 0% | 🔒 BLOCKED by 079 |
| **082** | Config & Env Unification | 6 | 0% | 0% | 🔒 BLOCKED by 078 |
| **083** | Healthcheck Unification | 7 | 0% | 0% | ⚪ Не начат (независимый) |
| **084** | Dead Code Sweep | 7 | 0% | 0% | 🔒 BLOCKED by 080 + 071 |

### 0.2 Critical Blockers

| # | Блокер | Severity | Следствие |
|---|--------|----------|-----------|
| 1 | **DevPlan 070 удалён из working tree** | CRITICAL | `core/internal/shared/` не создан. Wave 1 невозможна. Waves 2-5 заблокированы. |
| 2 | **11 untracked VerificationReports** в директориях 071-084 | HIGH | QA-результаты не закоммичены — риск потери истории аудита |
| 3 | **3 downstream DevPlans имеют design flaws** (071, 076, 082) | MEDIUM | Требуют ревизии ДО имплементации (см. Brief 077 §AUDIT_UPDATE) |

### 0.3 Drift ID Count: 42 (not 41)

Brief 077 определяет **42** поименованных drift ID: S1-S7 (7), B1-B6 (6), C1-C8 (8), D1-D6 (6), E1-E8 (8), H1-H7 (7) = **42**. Из них 3 являются кросс-доменными дублями:
- **S6 = E1** (POSTGRES_PASSWORD — 6 default-значений, затрагивает и секреты, и конфигурацию)
- **S7 = E3** (NEXTAUTH_SECRET — 4 test-значения, затрагивает и секреты, и конфигурацию)
- **B4 = D2** (Content hash — 3 реализации, затрагивает и bootstrap, и deploy)

Таким образом: **42 поименованных ID = 39 уникальных проблем + 3 кросс-доменных дубля**. Предыдущая версия DevPlan ошибочно указывала 41 — это исправлено.

---

## Резюме

Проект ai-platform накопил 42 точки системного дрейфа (39 уникальных + 3 кросс-доменных дубля) за 15 дней (287 коммитов). Причина: двойные реализации бизнес-логики (Python + Bash), отсутствие shared-библиотек, фрагментированные default-значения и мёртвый код.

**7 существующих DevPlans** (070-076) покрывают 4 drift-точки полностью/частично + 4 inline-python3 миграции.
**7 новых DevPlans** (078-084) закрывают оставшиеся 38 drift-точек.

Этот документ — дорожная карта, определяющая ПОСЛЕДОВАТЕЛЬНОСТЬ волн, зависимости между ними и матрицу покрытия. Передаётся разработчику вместе с конкретным DevPlan-ом для реализации каждой волны.

---

## Глава 1: Карта покрытия — 42 drift-точки → 14 DevPlans

### 1.1 Полная матрица покрытия (42 поименованных ID, 3 кросс-доменных дубля)

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
| Всего поименованных drift ID в Brief 077 | **42** |
| Из них кросс-доменных дублей (один drift в двух доменах) | 3 (S6=E1, S7=E3, B4=D2) |
| Уникальных проблем (после дедупликации) | 39 |
| + Dead-code пункты (ssl-provision.sh, LITELLM_METRICS_TOKEN, .done) | 3 |
| Итого уникальных точек для закрытия | **42** (39 drift + 3 dead-code) |
| Покрыто существующими DevPlans (070-076) | 4 (B1, B5-частично, S3-частично, S5-частично) |
| Покрыто НОВЫМИ DevPlans (078-084) | 38 (включая завершение частичных) |
| Полностью закрыто после всех волн | **42 из 42** (100%) |
| Унаследованных DevPlans (уже готовы) | 7 |
| Новых DevPlans (требуют реализации) | 7 |
| Всего DevPlans | **14** |
| Логических волн (последовательных групп) | **8** (1 foundation + 7 implementation) |
| Текущая имплементация (аудит 2026-07-25) | **0%** (ни одна волна не начата) |

---

## Глава 2: Последовательность волн — Dependency Graph

### 2.0 Ключевое правило: 070 — ФУНДАМЕНТ ВСЕХ ВОЛН

**DevPlan 070 (Extract Shared Libraries) создаёт директорию `core/internal/shared/` и модули `node_yaml.py`, `project_registry.py`. Без него невозможна ни одна последующая волна.** Все DevPlans 078-081 добавляют модули в `shared/` или импортируют из него. Wave 1 = ТОЛЬКО 070. После успешного merge 070 → Waves 2a/2b/3 могут идти параллельно.

```
                         ┌──────────────────────────────────────────┐
                         │    WAVE 1: FOUNDATION (CRITICAL PATH)    │
                         │               только 070                  │
                         │   Создаёт core/internal/shared/           │
                         │   Удаляет 3 копии _extract_context        │
                         │   MUST merge first — unblocks ALL waves   │
                         └──────────────┬───────────────────────────┘
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              │                         │                         │
              ▼                         ▼                         ▼
   ┌──────────────────┐    ┌──────────────────────┐    ┌──────────────────┐
   │  WAVE 2a: 078    │    │  WAVE 2b:            │    │  WAVE 2c: 071-072│
   │  Secrets & Tokens│    │  073 Provision Py    │    │  Checkpoints +   │
   │  Unification     │    │  074 Monitoring Py   │    │  Secrets Write   │
   │  (S1-S7, CRIT)   │    │  075 Watchdog Py     │    │  (внутри Wave 1  │
   │                  │    │  076 Reconcile Py    │    │   но независимы  │
   │  Зависит: 070    │    │  Зависят: 070        │    │   от shared/)    │
   └────────┬─────────┘    └──────────┬───────────┘    └────────┬─────────┘
            │                         │                          │
            └─────────────────────────┼──────────────────────────┘
                                      │ (все три группы параллельны после 070)
                                      │
                    ┌─────────────────┴─────────────────┐
                    ▼                                   ▼
         ┌──────────────────────┐          ┌──────────────────────┐
         │  WAVE 3: 079         │          │  WAVE 4: 080         │
         │  Bootstrap Pipeline  │          │  Certs & SSL         │
         │  (B3, B4, B6)        │          │  (B2, C1-C8)         │
         │  + shared/ модули    │          │                      │
         │  Зависит: 070+071+078│          │  Зависит: 070+078+079│
         └──────────┬───────────┘          └──────────┬───────────┘
                    │                                  │
                    │  080 может стартовать             │
                    │  параллельно с 081                │
                    │  после завершения 079             │
                    │                                  │
         ┌──────────┴───────────┐          ┌──────────┴───────────┐
         │  WAVE 5: 081         │          │  WAVE 6: 082         │
         │  Deploy Pipeline     │          │  Config & Env        │
         │  (D1, D3-D6)         │          │  (E1-E8)             │
         │  Зависит: 070+079    │          │  Зависит: 078        │
         │  (использует         │          │  (может стартовать   │
         │   docker_compose.py) │          │   после 078,         │
         └──────────┬───────────┘          │   независимо от      │
                    │                      │   079-081)            │
                    │                      └──────────┬───────────┘
                    │                                  │
                    └─────────────────┬────────────────┘
                                      │
                                      ▼
                           ┌──────────────────────┐
                           │  WAVE 7: 083 + 084  │
                           │  Healthcheck +       │
                           │  Dead Code Sweep     │
                           │  083: независимый    │
                           │  084: зависит от 080 │
                           │       и 071          │
                           └──────────────────────┘
```

### 2.1 Детализация каждой волны

#### WAVE 1: FOUNDATION — 070 (неделя 1, день 1)

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **070** | Extract Shared Libraries | +150 / −90 | Создаёт `core/internal/shared/` с `node_yaml.py` + `project_registry.py`. Удаляет 3 копии `_extract_context_from_node_yaml()` и 3 Python heredoc блока. |

**Зависимости:** Нет. Это абсолютный фундамент.
**Критичность:** CRITICAL. 070 создаёт директорию `shared/`, которую используют ВСЕ последующие волны (078, 079, 080, 081). DevPlan 070 удалён из working tree — требует `git checkout` перед стартом.
**❗ BLOCKER:** Файлы DevPlan 070 помечены как ` D` в git status. Необходимо восстановить: `git checkout HEAD -- .ai/plans/070-extract-shared-libs/`

#### WAVE 2a: Secrets & Tokens Unification — 078 (неделя 1, день 2-4)

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **078** | Secrets & Tokens Complete Unification | +150 / −170 | `age_key.py` + `crypto.py` в shared/. S4 security fix. 5 naming conflicts resolved. POSTGRES_PASSWORD + NEXTAUTH_SECRET unified. |

**Зависимости:** 070 (shared/__init__.py), 072 (merge order).
**Критичность:** CRITICAL. S4 — уязвимость (токен в /proc/cmdline). S1 — 5 копий = 5 баг-векторов.
**Может идти параллельно с Wave 2b и Wave 2c после завершения 070.**

#### WAVE 2b: Shell → Python Migrations — 073, 074, 075, 076 (неделя 1-2)

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **073** | Provision → Python | +250 / −400 | 13 inline python3 → `provisioner.py`. Shell wrapper <50 LOC. |
| **074** | Monitoring Hooks → Python | +400 / −370 | 19 inline python3 → `monitoring_config_renderer.py`. 3-level JSON merge unit-tested. |
| **075** | Watchdog → Python Daemon | +500 / −530 | Circuit breaker FSM в Python. systemd unit обновлён. |
| **076** | Reconcile → Python | +250 / −280 | 6 inline python3 → `reconciler_projects.py`. Shell wrapper сохранён для converge.sh. |

**Зависимости:** 070 (shared/). Могут идти параллельно с Wave 2a (078) и Wave 2c (071-072).
**Примечание:** Это языковая политика (Tier 1 inline python3 → Python модули). Не закрывают drift ID напрямую, но устраняют структурную причину дрейфа.

#### WAVE 2c: Checkpoints + Secrets Atomic Write — 071, 072 (неделя 1, день 1-2)

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **071** | Unify Checkpoints | +120 / −80 | `checkpoint.sh` переписан на `state.json`. Старые `.done` файлы мигрированы. Единая система чекпоинтов. |
| **072** | Secrets Atomic Write | +30 / −15 | `secrets_manager.py`: append → atomic overwrite. `LITELLM_METRICS_TOKEN` удалён из `.env.example`. |

**Зависимости:** Нет (не зависят от shared/). Могут идти параллельно с Wave 2a и Wave 2b.
**Критичность:** MEDIUM. Эти два плана не зависят от 070 (не используют shared/), поэтому могут стартовать немедленно, даже до восстановления 070.

#### WAVE 3: Bootstrap Pipeline Unification (079) — неделя 2, день 1-3

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **079** | Bootstrap Pipeline Unification | +300 / −200 | `shared/content_hash.py`, `shared/docker_compose.py`. 4 deploy-context entrypoints → 1. Content hash unified. Docker compose shared library. |

**Зависимости:** 070, 071, 078.
**Ключевой результат:** `shared/docker_compose.py` с `retry_pull()` — фундамент для Wave 5 (081) DRIFT-D3.

#### WAVE 4: Certificates & SSL Unification (080) — неделя 2, день 2-4

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **080** | Certificates & SSL Complete Unification | +200 / −1200 | DELETE nginx/install.sh (1107 LOC). `cert_orchestrator.py` — единая точка входа. Все vhost'ы на wildcard. Dev cert имена гармонизированы. |

**Зависимости:** 070, 078, 079.
**Может идти параллельно с Wave 5 (081) после завершения Wave 3 (079).**

#### WAVE 5: Deploy Pipeline Unification (081) — неделя 2, день 3-5

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **081** | Deploy Pipeline Unification | +350 / −150 | Deploy Path Registry + gate test. `shared/ssh_command_parser.py`, `shared/platform_deliver.py`, `shared/audit_logger.py`. Retry/rollback в Python deploy путях. |

**Зависимости:** 070, 079 (docker_compose.py).
**Может идти параллельно с Wave 4 (080) после завершения Wave 3 (079).**

#### WAVE 6: Configuration & Env Defaults Unification (082) — неделя 3, день 1-2

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **082** | Config & Env Defaults Unification | +250 / −100 | Трёхуровневая иерархия SoT: `secret-definitions.yaml` → `platform-env.yaml` → `.env.example`. S3_ENDPOINT циклический fallback убран. PLATFORM_DOMAIN default унифицирован. Variable naming стандартизирован. `.env.example` генерируется, не редактируется вручную. |

**Зависимости:** 078 (секретные дефолты унифицированы).
**Не зависит от 079-081, может стартовать параллельно с Wave 3-5 после завершения Wave 2a (078).**

#### WAVE 7: Healthcheck + Dead Code (083, 084) — неделя 3, день 2-4

| DevPlan | Название | LOC изменений | Ключевой результат |
|---------|----------|---------------|-------------------|
| **083** | Healthcheck Complete Unification | +100 / −300 | 9 механизмов → 3 примитива. `check_tcp()`, `exec_check()` в lib. start_period стандартизирован. 14 модулей унифицированы. |
| **084** | Dead Code Sweep | +50 / −1150 | DELETE nginx/install.sh (если не удалён в 080), ssl-provision.sh. `make check-dead-code` gate. |

**Зависимости:** 083 — независимый домен (может стартовать в любой момент). 084 зависит от 080 (cert unification), 071 (.done migration).

---

## Глава 2b: Recovery Path — как запустить после аудита 2026-07-25

### Шаг 1: Восстановить DevPlan 070

```bash
git checkout HEAD -- .ai/plans/070-extract-shared-libs/
```

DevPlan 070 удалён из working tree (`git status` показывает ` D`). Это фундамент всех волн. Без восстановления невозможен старт.

### Шаг 2: Закоммитить untracked VerificationReports

```bash
git add .ai/plans/07*/0*-VerificationReport.md
git commit -m "audit: commit untracked VerificationReports for DevPlans 071-084"
```

11 untracked QA-отчётов в директориях 071-084. QA-история должна быть сохранена в репозитории.

### Шаг 3: Ревизия DevPlans с design flaws

Три downstream DevPlans имеют design flaws, выявленные аудитом. Эти правки ДОЛЖНЫ быть сделаны ДО старта соответствующих планов:

| DevPlan | Проблема | Что сделать |
|---------|----------|-------------|
| **071** | F1: Step-name misalignment (shell:16 keys vs Python:23). Утверждение «numeric keys will align» — FALSE | Добавить mapping table shell↔Python step names в DevPlan |
| **076** | CRITICAL: `exec python3` в wrapper убьёт converge.sh; NODE_HOST_MAP не forwarded | Исправить wrapper — использовать прямой import вместо subprocess |
| **082** | Scope gaps: 5 Python S3_ENDPOINT файлов, hermes-agent/.env.example не упомянуты | Расширить scope до всех 5 Python-файлов + hermes-agent |

Эти правки не блокируют Wave 1 (070), но блокируют соответствующие downstream DevPlans.

### Шаг 4: Запуск Wave 1 — только 070 (FOUNDATION)

```
Кодер A: DevPlan 070 (Extract Shared Libraries)
         → создаёт core/internal/shared/
         → merge + gate MODE=fast
```

**Gate check после Wave 1:**
```bash
python3 -c "from core.internal.shared.node_yaml import extract_context_from_node_yaml; print('shared/ OK')"
make gate MODE=fast
```

### Шаг 5: Параллельный запуск Waves 2a, 2b, 2c (после merge 070)

```
Кодер A: DevPlan 078 (Secrets & Tokens, CRITICAL — S4 security fix first)
Кодер B: DevPlans 073+074 (Provision + Monitoring → Python)
Кодер C: DevPlans 071+072 (Checkpoints + Atomic Write, не зависят от shared/)
```

### Шаг 6: Последовательные Waves 3-7

После завершения Waves 2a+2b+2c:
- **Wave 3 (079)** — зависит от 070+071+078 → sequential, single coder
- **Wave 4 (080) и Wave 5 (081)** — могут идти параллельно после Wave 3
- **Wave 6 (082)** — может стартовать сразу после Wave 2a (078), не ждёт 079-081
- **Wave 7 (083)** — независимый домен, может стартовать в любой момент
- **Wave 7 (084)** — ждёт завершения Waves 4 (080) и 2c (071)

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

### 4.0 VerificationReport Tracking

**Аудит 2026-07-25 обнаружил 11 untracked VerificationReport-файлов** в директориях 071-084. Эти файлы содержат результаты QA-сессий для downstream DevPlans. Они должны быть закоммичены перед стартом любой волны:

```bash
git add .ai/plans/07*/0*-VerificationReport.md
git commit -m "audit: commit untracked VerificationReports for DevPlans 071-084"
```

После каждой реализованной волны — новый VerificationReport в соответствующей директории DevPlan. Все VerificationReports коммитятся в репозиторий.

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

## Глава 5: План выполнения (для руководителя) — обновлён 2026-07-25

### Pre-flight: Восстановление (1 час)

| Действие | Исполнитель | Команда |
|----------|-------------|---------|
| Восстановить DevPlan 070 | Любой | `git checkout HEAD -- .ai/plans/070-extract-shared-libs/` |
| Закоммитить VerificationReports | Любой | `git add .ai/plans/07*/0*-VerificationReport.md && git commit -m "audit: commit untracked QA reports"` |
| Ревизия DevPlans 071, 076, 082 | Architect | Исправить design flaws (см. Brief §AUDIT_UPDATE) |

### Неделя 1

| День | Кодер A (CRITICAL) | Кодер B | Кодер C |
|------|-------------------|---------|---------|
| Пн | **070** Extract Shared Libs ← FOUNDATION | **071** Unify Checkpoints (не зависит от shared/) | **072** Secrets Atomic Write (не зависит от shared/) |
| Вт | 070 → merge + gate | 071 продолжение | 072 продолжение |
| Ср | **078** Secrets & Tokens (S4 CRITICAL) | **073** Provision → Python | **074** Monitoring → Python |
| Чт | 078 продолжение | **075** Watchdog → Python | **076** Reconcile → Python |
| Пт | 078 → merge + gate | 073-076 → merge + gate | 071-072 → merge + gate |

### Неделя 2

| День | Кодер A | Кодер B |
|------|---------|---------|
| Пн | **079** Bootstrap Pipeline | **082** Config & Env (может стартовать после 078!) |
| Вт | 079 продолжение | 082 продолжение |
| Ср | **080** Certificates & SSL | **081** Deploy Pipeline (080 и 081 параллельны!) |
| Чт | 080 продолжение | 081 продолжение |
| Пт | 080/081 → merge + gate | 082 → merge + gate |

### Неделя 3

| День | Кодер A | Кодер B |
|------|---------|---------|
| Пн | **083** Healthcheck | **084** Dead Code (ждёт 080 и 071) |
| Вт | 083 продолжение | 084 продолжение |
| Ср | 083 → merge + gate | 084 → merge + gate |
| Чт | **Финальный `make gate MODE=full`** | Интеграционное тестирование |
| Пт | Bug fixes по результатам gate | Подготовка к релизу |

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

### TRAP[SEQUENCE] · 2026-07-25 · HI · Порядок merge: 070 (FOUNDATION) → {071,072,078,073-076 parallel} → 079 → {080,081 parallel} → 082 → {083,084}
- **Критическое правило:** 070 merge — шлюз для ВСЕХ последующих волн. Без `core/internal/shared/` ни одна downstream волна не компилируется.
- Если 079 смержен до 071: `content_hash.py` будет использовать устаревший `checkpoint.sh` для сравнения хешей.
- Если 080 смержен до 079: `cert_orchestrator.py` вызовет `docker_compose.py`, которого ещё нет.
- **Правило:** каждая волна мержится в main ТОЛЬКО после успешного gate предыдущей волны.
- **Аудит 2026-07-25:** DevPlan 070 удалён из working tree. Требует `git checkout` перед стартом.
- **Исключение:** 071+072 не зависят от 070 (не используют shared/) — могут стартовать немедленно, даже до восстановления 070.

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
