# 027-Brief: Architecture Modernization Program — Bash→Python Strangler + Honesty-First Test Wave

**Program type:** Multi-wave strategic brief (5 delivery waves + Decision Gate, ~30-35 недель)
**Source analysis:** `reports/architecture-analysis-2026-07-21.md`, verified against codebase 2026-07-21
**Operator decisions (2026-07-21):** Strangler-Fig с двухуровневым триггером; Honesty-First в Wave 1 (Immediate); опасные изменения (SSH/CI/audit) выделены в Wave 2; enforcement только через AGENTS.md (без CI gate); единый бриф без мелкой грануляции. Корректировка 2026-07-21: Wave 1 разбита на Immediate (баги/тесты/фиксация позиции + inline python3 консолидация) и Dangerous (SSH/CI/audit с явным профитом), цифры shell/Python скорректированы по факту.

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Программа архитектурной модернизации ai-platform v1.0.0 → v2.0. Цель: преодолеть три системных напряжения (Bash-scalability ceiling, test-trust deficit, reconciliation gap), зафиксировать языковую политику (новый код — только Python, bash — тонкие обёртки) и получить ощутимый профит уже в первой волне (false-green → real-fail, −400 строк бойлерплейта, консолидация inline python3). Каждая волна — самостоятельный deliverable с production-релизом на выходе, может быть выделена в отдельный DevPlan.
DESCRIPTION:           5 delivery-волн + Decision Gate, каждая закрывает строго определённое подмножество Problem Matrix из отчёта (P01-P15). **Wave 1 (Immediate, ~3-4 нед)** — нулевой риск: AGENTS.md правило, R4/R5-fix, gate_helpers.py, lib/args.sh, консолидация 103 inline `python3 -c` вызовов в библиотечные Python-модули. **Wave 2 (Dangerous, ~3-4 нед)** — затрагивает production-пути: lib/ssh.sh с timeout, CI composite action, audit-trail на 7 entrypoints. **Wave 3** — D5-контракт и module.yaml strengthening. **Wave 4** — Strangler Fig на топ-3 скриптах. **Wave 5** — bootstrap reliability и converge K8s-parity. **Decision Gate** — аналитический артефакт (2 дня, не DevPlan), фиксирует направление 2027+. Применяется принцип Strangler-Fig (Option B из §4.1 отчёта) с двухуровневым триггером: Tier 1 (немедленный) — любой новый `python3 -c` или heredoc → Python-модуль для этой логики; Tier 2 (плановый) — накопление ≥3 Tier-1 экстракций в одном shell-файле → Strangler-декомпозиция всей подсистемы в следующей волне.
RATIONALE:             Анализ архитектуры (arch-forensics skill), верифицированный против кодовой базы 2026-07-21, показал: top-3 shell-scripts (3979 строк) смешивают 3-5 ответственностей каждый; 103 inline `python3 -c` вызова в 20+ файлах сигнализируют о Bash-ceiling (а не единичный heredoc-блок, как предполагалось изначально); 18 R4-нарушений и 3 R5-нарушения делают CI false-green; ~15K строк shell (скорректировано с первоначальной оценки 24.5K). Оператор выбрал: (а) правило Python-only в AGENTS.md вместо CI gate — опирается на code review и роль разработчика; (б) Honesty-First в первой волне — trust-киллер должен быть закрыт до любых архитектурных изменений; (в) разбивка Immediate/Dangerous — безопасные изменения не должны ждать опасных и наоборот; (г) inline python3 консолидация вместо heredoc extraction — реальный масштаб проблемы в 5× больше, чем предполагалось. Big-bang rewrite отклонён (Option A §4.1, score 3/10); hybrid без дисциплины отклонён (Option C §4.1, откладывание решения).
ACCEPTANCE_CRITERIA:
  **Wave 1 (Immediate — Bugs, Tests, Policy Fixation):**
    1. Root `AGENTS.md` содержит раздел "Языковая политика" с правилом: новый код только на Python; bash — тонкие обёртки; inline `python3 -c` и heredoc — сигнал к извлечению в `.py` модуль; двухуровневый Strangler-триггер (Tier 1: немедленное извлечение новой логики; Tier 2: плановая декомпозиция при накоплении).
    2. Все 18 R4-нарушений (`pytest.skip("Docker/... not available")`) заменены на `pytest.fail` через единую фикстуру `require_docker_or_fail` из `tests/_conftest/honesty.py`.
    3. Три gate-теста без `_negative` пар (`test_gate_litellm_pg_enforcement`, `test_gate_module_schema_d4`, `test_gate_env_shared_consistency`) получили `_negative`-компаньоны, детектирующие нарушение.
    4. `tests/helpers/gate_helpers.py` создан; дубликаты `_load_yaml` (6 копий), `PROJECT_ROOT` (70+ объявлений), `assert_ldd_imp9` убраны из gate-тестов через импорт из helper'а.
    5. `core/lib/args.sh` создан: стандартизированный `parse_args` + `usage`-хелпер. Рефактор 14 `usage()` + 8 `parse_args` определений. `rg "^usage\(\)" core/` — 0 определений вне lib/args.sh.
    6. `verify-domains.sh` использует `source lib/logging.sh` вместо локального `log_imp()`. Локальная функция `log_imp()` удалена.
    7. Inline `python3 -c` консолидация: создан `core/internal/scripts/yaml_query.py` (typed Python API для YAML/JSON-запросов — замена 40+ однострочников `import json,sys; print(json.load(sys.stdin)...)`); библиотека `core/lib/yaml_read.sh` переведена на вызов этого модуля; новые inline `python3 -c` блокируются pre-commit проверкой. Все 103 существующих вызова mapped → tracked для консолидации (часть мигрируется в Wave 1, остальные — в Waves 4-5 вместе с декомпозицией родительских скриптов).
  **Wave 2 (Dangerous — SSH, CI, Audit):**
    8. `core/lib/ssh.sh` (новый) содержит единую SSH-фасадную функцию с `timeout`-обёрткой (600s default для remote-deploy, 60s для read). Все вызовы SSH в `scp-deliver.sh`, `remote-cmd.sh`, `remove-project.sh`, `project-list.sh` используют фасад. Нет удалённых команд без общего timeout. Staging-тест перед merge (R-RISK-1 impact ↑ H: single point of failure для всех remote-операций).
    9. `.github/actions/setup-platform/action.yml` (новый composite) объединяет `checkout` + `setup-python-venv` + `setup-gitleaks` + `provisioner-call`. Минимум 6 workflows мигрированы. `make gate MODE=fast` — CI setup −30s на каждый workflow.
    10. `core/lib/audit_logging.sh` расширен wrapper-макросом. 7 entrypoints (`context-promote`, `remove-project`, `provision`, `hermes-build`, `secrets-unlock`, `node-update --mode update`, `core-deploy rsync`) эмитят audit_log на старте/завершении. Async-write через `>>` в `/var/log/platform/audit.log`.
  **Wave 3 (Contract Strengthening D5):**
    11. `core/internal/scripts/validate_module_yaml.py` создан; валидирует все module.yaml по D5-контракту (env_requires типизированы, `${VAR:?}` enforced, restart-drift детектируется).
    12. `AGE_SECRET_KEY` добавлен в `.env.example` для platform-secrets (P06 закрыт).
    13. Критичные секреты (POSTGRES_PASSWORD, CLICKHOUSE_PASSWORD, LITELLM_MASTER_KEY, MINIO_ROOT_PASSWORD) используют `${VAR:?error}` в compose (P07 закрыт).
    14. Все 13 модулей в test-compose имеют `restart: no` (не наследуют `unless-stopped`) (P08 закрыт).
  **Wave 4 (Strangler Fig топ-3):**
    15. `deploy-modules.sh` (1633 строк) декомпозирован по 5 ответственностям в Python-модули: docker-orchestration, sudoers-generation, context-overlay, secrets-validation, orphan-reconciliation. Shell-фасад <100 строк.
    16. `node-lifecycle.sh` (1297 строк) — выделена business-logic (state-machine transitions) в Python. Shell-фасад <200 строк.
    17. `converge.sh` (1149 строк) — reconcile-loop переведён в Python; shell остаётся тонким фасадом <150 строк.
    18. Makefile декомпозирован через `include` (makefiles/{bootstrap,deploy,scaffold,modules,ci,helpers}.mk). Root Makefile <150 строк.
  **Wave 5 (Bootstrap Reliability + Converge K8s-parity):**
    19. `deploy_docker_group` поддерживает transactional atomic-success-or-rollback (P05 закрыт).
    20. converge.sh покрывает 7/10 K8s-style desired-state контракта (добавлены: docker-volumes drift, sudoers drift, runtime-state reconciliation); R5/R6 переведены из detect-only в self-heal (P13 закрыт частично). HARD STOP на 7/10 — без continuous-watch.
  **Decision Gate (post-Wave 5, аналитический артефакт):**
    21. Decision document зафиксирован: анализ метрик Q1-Q4 (test-coverage, change-cost, incident-rate, CI-gate time) → recommendation по дальнейшей миграции. Это validation gate стратегии, а не точка отказа от неё.
IMPLEMENTS:            Анализ архитектуры `reports/architecture-analysis-2026-07-21.md` (15 проблем матрицы, 5 superpositions), верифицированный против кодовой базы 2026-07-21. Skills: arch-forensics (Tasks 1-7), superposition (FULL/BINARY/GUIDED/ADVERSARIAL/AUTO-COLLAPSE), doc-protocols (этот бриф). AGENTS.md invariants 1 (Makefile-фасад), 4 (канонические AGENTS.md), 8 (AI-First Architecture). Principles 6 (Small Simple Blocks через Strangler), 8 (модульные границы), 9 (Read before Act — отчёт прочитан и верифицирован).
IMPACTS:               **AGENTS.md** (root) — новый раздел "Языковая политика" (Wave 1). **New Python:** `core/internal/scripts/yaml_query.py` (Wave 1 — замена 40+ inline python3 вызовов), `core/internal/scripts/validate_module_yaml.py` (Wave 3), Python-модули разложения топ-3 скриптов (Wave 4). **New lib:** `core/lib/args.sh` (Wave 1), `core/lib/ssh.sh` (Wave 2). **Tests:** `tests/_conftest/honesty.py`, `tests/helpers/gate_helpers.py`, R4-фиксы в 18 файлах, R5-новые `_negative` пары (Wave 1). **CI:** `.github/actions/setup-platform/action.yml`, 7 entrypoints с audit-trail (Wave 2). **Bash→Python:** `deploy-modules.sh`, `converge.sh`, `node-lifecycle.sh` — декомпозиция (Wave 4). **Modules:** `.env.example`, compose-файлы 13 модулей, module.yaml D5-контракт (Wave 3). **Makefile:** include-декомпозиция (Wave 4).
REQUIRES:              Чистый working tree. Источник: `reports/architecture-analysis-2026-07-21.md` (должен быть прочитан архитектором каждой волны перед генерацией DevPlan). Перед стартом каждой волны: verify problem matrix против текущего состояния кодовой базы (принцип 9 — Read before Act). Оператор подтвердил: enforcement через AGENTS.md (без CI gate на .sh файлы), разбивка Immediate/Dangerous, inline python3 консолидация в Wave 1, двухуровневый Strangler-триггер. Каждая delivery-волна генерирует собственный DevPlan через dev-pipeline skill (Brief → Architect → Coder → QA → Fix) и завершается production-релизом. Decision Gate — аналитический артефакт, не DevPlan.
$END_ARTIFACT_CONTRACT

---

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Зафиксировать языковую политику с двухуровневым Strangler-триггером => G1
- GOAL Дать карту Problem Matrix → Wave mapping (скорректированную) => G2
- GOAL Описать Wave 1 (Immediate): bugs, tests, policy, inline python3 консолидация => G3
- GOAL Описать Wave 2 (Dangerous): SSH timeouts, CI composite, audit-trail => G4
- GOAL Описать Wave 3 (Contract D5): env_requires типизация, `${VAR:?}`, restart enforcement => G5
- GOAL Описать Wave 4 (Strangler Fig): декомпозиция топ-3 скриптов, Makefile include-split => G6
- GOAL Описать Wave 5 (Bootstrap Reliability): transactional deploy, converge K8s-parity => G7
- GOAL Описать Decision Gate: post-delivery evaluation, валидация стратегии => G8
- GOAL Зафиксировать risk register, метрики успеха, pre-commitment => G9

**SECTION_USE_CASES:**
- USE_CASE Оператор берёт одну волну и генерирует DevPlan через dev-pipeline => SC1
- USE_CASE Архитектор проверяет dependencies между волнами перед стартом новой => SC2
- USE_CASE QA верифицирует acceptance criteria конкретной волны => SC3
- USE_CASE Ревьюер проверяет, что изменение соответствует языковой политике (двухуровневый триггер) => SC4
- USE_CASE После каждой волны — прикрепление DevPlan к брифу для tracking'а => SC5
$END_DOCUMENT_PLAN

---

## 1. Языковая политика (внести в root `AGENTS.md`, Wave 1)

### 1.1. Текст для добавления в AGENTS.md

Новый раздел размещается после §Глоссарий глаголов, перед §Правило.

```markdown
## Языковая политика

**Главное правило:** новый код платформы пишется только на Python. Bash допустим исключительно как тонкая обёртка над Python-модулями и системными утилитами (`rsync`, `scp`, `ssh`, `docker`, `systemctl`).

**Принципы применения:**

1. **Новый код = Python.** Любая новая подсистема, бизнес-логика, валидатор, парсер, state-machine — пишется на Python (предпочтительно в `core/internal/scripts/` или `core/internal/<domain>/`). Shell-обёртка в `core/entrypoints/` только вызывает `python3 script.py` и пробрасывает exit code.

2. **Bash остаётся для:** entrypoints (тонкие фасады, вызываемые из Makefile), чистой оркестрации (последовательность subprocess-вызовов без логики), lib-функций низкого уровня (`lib/logging.sh`, `lib/paths.sh`, `lib/ssh.sh`). Существующие стабильные shell-библиотеки (`logging.sh`, `paths.sh`, `healthcheck.sh`, `module-interface.sh`) НЕ мигрируются на Python — их API стабилен и замена не даст прироста.

3. **Inline Python и heredoc — сигнал к извлечению.** Любой `python3 -c "..."` или `python3 - <<PYEOF ... PYEOF` в bash-скрипте — это сигнал: логику нужно вынести в отдельный `.py` файл и вызывать через `python3 script.py`. Никаких исключений для нового кода. Для существующего кода: миграция через двухуровневый Strangler-триггер (см. п.4).

4. **Strangler-триггер (двухуровневый порог переписывания на Python):**
   - **Tier 1 (немедленный — при ЛЮБОМ изменении скрипта):**
     - Добавление нового `python3 -c "..."` или heredoc-блока → вынести эту конкретную логику в отдельный `.py` модуль. Не переписывать всю подсистему.
     - Добавление >3 новых `if`-веток с бизнес-логикой (не оркестрацией) → вынести business-logic в Python-модуль.
   - **Tier 2 (плановый — при накоплении):**
     - В одном shell-файле накоплено ≥3 Tier-1 экстракций → плановая Strangler-декомпозиция всей подсистемы в Python. Shell остаётся фасадом <150 строк.
     - Баг-фикс затрагивает >2 ответственностей одного скрипта → регистрируется в debt-tracker, включается в план следующей волны.

   **Важно:** Tier 1 НЕ требует переписывать весь скрипт — только извлечь новую/изменяемую логику в Python-модуль. Это устраняет извращённый стимул «не чини баг, потому что придётся переписывать всё».

5. **Несовпадение с hybrid-без-дисциплины:** это не откладывание решения, а Strangler-Fig discipline — каждая переписанная подсистема становится тестируемой, типизированной и grep-able. Двухуровневый триггер обеспечивает непрерывное движение к Python без блокировки баг-фиксов.
```

### 1.2. Обоснование (для архитектора DevPlan)

- Текущее состояние: ~15K строк shell, top-3 = 3979 строк смешивают 3-5 ответственностей каждый, **103 inline `python3 -c` вызова** в 20+ файлах (не единичный heredoc — реальный масштаб проблемы). Единственный `<<PYEOF` heredoc-блок — 21 строка в `deploy-modules.sh:628-650`.
- shellcheck не анализирует inline Python; тестирование требует `subprocess.run(["bash", ...])` — медленно и хрупко.
- 80+ тестов уже на Python; `template_engine.py`, `discover_modules.py` — стабильный production-Python; jsonschema, pyyaml — уже в deps.
- `core/lib/yaml_read.sh` уже консолидирует часть inline-вызовов — это демонстрирует, что направление верное.
- Strangler-Fig (Option B §4.1 отчёта, score 8/10) минимизирует риски: каждая подсистема мигрируется отдельно, тесты пишутся перед миграцией.
- Двухуровневый триггер решает проблему извращённых стимулов: баг-фикс не блокируется необходимостью переписывать весь скрипт.

---

## 2. Problem Matrix → Wave Mapping

| ID  | Категория        | Sev     | Wave закрытия | Примечание |
|-----|------------------|---------|---------------|------------|
| P01 | TEST_QUALITY (R4) | 🔴 CRIT | Wave 1 | skip → fail через `require_docker_or_fail` |
| P02 | ERROR_HANDLING (SSH timeout) | 🔴 CRIT | Wave 2 | `lib/ssh.sh`, staging-тест обязателен |
| P03 | ARCHITECTURE (top-3 monolith) | 🔴 CRIT | Wave 4 | Strangler декомпозиция |
| P04 | TEST_QUALITY (R5) | 🟠 HIGH | Wave 1 | 3 `_negative` пары |
| P05 | ERROR_HANDLING (rollback) | 🟠 HIGH | Wave 5 | transactional deploy_docker_group |
| P06 | SECURITY (AGE_SECRET_KEY) | 🟠 HIGH | Wave 3 | D5-validator |
| P07 | ERROR_HANDLING (`${VAR:?}`) | 🟠 HIGH | Wave 3 | D5-validator |
| P08 | MODULE_CONTRACT (restart drift) | 🟠 HIGH | Wave 3 | test-compose enforcement |
| P09 | ARCHITECTURE (deploy-modules SRP) | 🟠 HIGH | Wave 4 | декомпозиция по 5 ответственностям |
| P10 | DUPLICATION (boilerplate) | 🟠 HIGH | Wave 1 (args.sh, gate_helpers) + Wave 2 (ssh.sh) | lib/args.sh + gate_helpers.py + lib/ssh.sh |
| P11 | CI_EFFICIENCY (audit-trail) | 🟡 MED | Wave 2 | 7 audit_log wrapper'ов |
| P12 | EXTENSIBILITY (inline python3) | 🟡 MED | Wave 1 (консолидация) + Wave 4 (декомпозиция) | 103 inline `python3 -c` → yaml_query.py + модули |
| P13 | ARCHITECTURE (converge K8s-parity) | 🟡 MED | Wave 5 | 4/10 → 7/10 |
| P14 | DUPLICATION (`log()` convention) | 🟡 MED | Wave 1 | verify-domains fix |
| P15 | CI_EFFICIENCY (composite setup) | 🟡 MED | Wave 2 | `setup-platform` action |

**Итог по волнам:** Wave 1 = 7 проблем (P01, P04, P10 частично, P12 частично, P14) + AGENTS.md policy + inline python3 консолидация. Wave 2 = 4 (P02, P10 частично, P11, P15). Wave 3 = 3 (P06, P07, P08). Wave 4 = 3 (P03, P09, P12 завершение). Wave 5 = 2 (P05, P13). Decision Gate = evaluation. Все 15 закрыты к концу Wave 5.

---

## 3. Wave 1 — Immediate: Bugs, Tests, Policy Fixation

**Длительность:** ~3-4 недели.
**Зависимости:** нет. Стартовая волна.
**Риск:** нулевой. Все изменения — либо документация, либо тесты, либо новые файлы (не затрагивают production-пути).
**Профит здесь и сейчас:** false-green → real-fail (P01), тесты получают `_negative`-компаньоны (P04), −400 строк бойлерплейта (P10), консолидация inline python3 (P12 старт), AGENTS.md фиксирует правила игры.
**Production-релиз:** обновлённый AGENTS.md, зелёный `make gate MODE=fast` с честными тестами.

### 3.1. Эпики Wave 1

| Эпик | Проблема | Что делаем | Acceptance |
|------|----------|------------|------------|
| **W1-E1** AGENTS.md policy | — | Внести раздел "Языковая политика" (§1.1 этого брифа) с двухуровневым Strangler-триггером | Раздел в root AGENTS.md, ссылка из core/AGENTS.md |
| **W1-E2** Honesty R4-fix | P01 | Создать `tests/_conftest/honesty.py` с `require_docker_or_fail()`. Заменить 18 `pytest.skip(...)` на fail. Поэтапно: marker → xfail(strict=False) → fail (R-RISK-2). | `rg "pytest\.skip\(" tests/` — 0 skip с reason "Docker/script/env not available" |
| **W1-E3** Honesty R5-fix | P04 | Добавить `_negative` пары для 3 gate-тестов | 3 новых файла `test_*_negative.py`, каждый детектирует нарушение |
| **W1-E4** gate_helpers.py | P10 | Создать `tests/helpers/gate_helpers.py`: `load_yaml()`, `repo_root()`, `module_yaml_paths()`, `assert_ldd_imp9()`. Рефактор 52 gate-тестов. | −25-30% строк в gate-тестах; `rg "_load_yaml" tests/` — 0 определений вне helper'а; `PROJECT_ROOT` — единый источник |
| **W1-E5** lib/args.sh | P10 | Создать `core/lib/args.sh`: стандартизированный `parse_args` + `usage`-хелпер. Рефактор 14 `usage()` + 8 `parse_args`. | `rg "^usage\(\)" core/` — 0 определений вне lib/args.sh |
| **W1-E6** verify-domains log_imp fix | P14 | `verify-domains.sh` использует `source lib/logging.sh` вместо локального `log_imp()` | Локальная функция `log_imp()` удалена |
| **W1-E7** Inline python3 консолидация | P12 (start) | Создать `core/internal/scripts/yaml_query.py` — typed Python API для YAML/JSON-запросов, замена 40+ однострочников `import json,sys; print(json.load(sys.stdin)...)`. Обновить `core/lib/yaml_read.sh` на вызов этого модуля. Добавить pre-commit проверку: новые `python3 -c` / heredoc блокируются. Составить map всех 103 существующих inline-вызовов → tracked для консолидации (часть мигрируется в Wave 1, остальные в Waves 4-5). | `yaml_query.py` с unit-тестами; `rg "python3 -c.*import json" core/` — оставшиеся вхождения имеют tracking-issue; pre-commit hook блокирует новые inline python3 |
| **W1-E8** Baseline measurement | — | Замерить baseline для всех незамеренных метрик KPI (§9): `make gate MODE=fast` time (3 повтора), SSH-вызовов без timeout (точный count), CI execution time per workflow | Файл `reports/baseline-metrics-2026-07.csv` с цифрами ДО старта Wave 2 |

### 3.2. Порядок выполнения внутри Wave 1

```
W1-E1 (AGENTS.md)  ──► даёт policy-рамку для всех остальных
W1-E7 (inline python3 консолидация)  ──► демонстрирует policy в действии, даёт первые Python-модули
W1-E8 (baseline measurement) ──► параллельно, не блокирует
W1-E2 + W1-E3 (Honesty R4/R5) ──► параллельно, закрывают false-green
W1-E4 (gate_helpers) ──► после R4/R5, чтобы не плодить дубли при фиксах
W1-E5 (lib/args.sh) ──► после E4 (единый lib-layer)
W1-E6 (verify-domains) ──► в любом порядке, мелкий
```

### 3.3. Риски Wave 1

- **R-RISK-2** (R4-fix → временно красный CI на staging без Docker): митигировать поэтапно — `requires_docker` marker → `xfail(strict=False)` → `fail`.
- **PGM-R1** (языковая политика не соблюдается без CI gate): code review checklist. В этой волне — только документирование правила, enforcement начинается после принятия.
- **Новый: Inline python3 false-positives** (pre-commit hook блокирует легитимные однострочники вроде `python3 -c "import yaml; print(...)"`): whitelist для вызовов через `yaml_read.sh` / `yaml_query.py` facade.

---

## 4. Wave 2 — Dangerous: SSH, CI, Audit (явный профит)

**Длительность:** ~3-4 недели.
**Зависимости:** Wave 1 (нужны честные тесты для staging-валидации; baseline E8 для замера эффекта CI composite).
**Риск:** ВЫСОКИЙ. Изменения затрагивают production-пути: SSH-фасад — single point of failure для всех remote-операций; CI composite — все workflows; audit-trail — 7 entrypoints.
**Профит:** CI hangs устранены (P02, CRITICAL), CI setup −30s на каждый workflow (P15), 7 audit-точек (P11).
**Production-релиз:** staging-тест SSH-фасада → merge → деплой на production-ноду → верификация audit-trail.

### 4.1. Эпики Wave 2

| Эпик | Проблема | Что делаем | Acceptance |
|------|----------|------------|------------|
| **W2-E1** lib/ssh.sh + timeouts | P02, P10 | Создать `core/lib/ssh.sh`: `SSH_OPTS` const, `timeout`-обёртка (600s default remote-deploy, 60s read). Мигрировать 4 файла: scp-deliver, remote-cmd, remove-project, project-list. | `rg "ConnectTimeout" core/` — все через фасад; нет SSH-вызовов без timeout; staging-тест: `make bootstrap-node` + `make project-list` на тестовой ноде |
| **W2-E2** setup-platform composite | P15 | `.github/actions/setup-platform/action.yml`: checkout + setup-python-venv + setup-gitleaks + provisioner-call. Мигрировать ≥6 workflows. | `rg "actions/checkout@v7" .github/workflows/` — ≤3 вхождения (whitelist); CI execution time baseline → post composite |
| **W2-E3** audit-trail wrapper | P11 | Расширить `core/lib/audit_logging.sh` wrapper-макросом. Применить в 7 entrypoints. Async-write через `>>`. | 7 entrypoints эмитят `audit_log` на start + exit; `tail -f /var/log/platform/audit.log` показывает записи |

### 4.2. Порядок выполнения внутри Wave 2

```
W2-E1 (lib/ssh.sh) ──► CRITICAL, staging-тест обязателен перед merge
W2-E2 (CI composite) ──► параллельно, независимая подсистема
W2-E3 (audit-trail) ──► после E1 (нужен стабильный SSH-фасад для тестирования entrypoints)
```

### 4.3. Риски Wave 2

- **R-RISK-1** (`lib/ssh.sh` ломает remote-CMD): **impact повышен до H** — SSH-фасад является single point of failure для ВСЕХ remote-операций (deploy, bootstrap, healthcheck, node-update, converge, project-list, project-status, remove-project, verify). Митигация: unit-test на staging-ноде перед merge; отдельная feature-ветка; возможность быстрого revert.
- **R-RISK-8** (audit-overhead): async-write через `>>` в `/var/log/platform/audit.log`.
- **Новый: CI composite ломает кеширование** (изменение структуры action сбрасывает cache → CI медленнее до стабилизации): принять как временную цену, замерить baseline до и после.

---

## 5. Wave 3 — Contract Strengthening D5

**Длительность:** ~5 недель.
**Зависимости:** Wave 1 (нужны честные тесты для валидации D5-контракта). Wave 2 — желательно, но не блокирует (SSH/CI не нужны для валидации module.yaml).
**Профит:** module contract D4 → D5; 3 проблемы закрыты (P06, P07, P08).

### 5.1. Эпики Wave 3

| Эпик | Проблема | Что делаем | Acceptance |
|------|----------|------------|------------|
| **W3-E1** validate_module_yaml.py | P06, P07, P08 | Создать `core/internal/scripts/validate_module_yaml.py` (jsonschema уже в deps). Schema: каждая env_var имеет type (string/secret/int/bool) + required: bool; `${VAR:?}` enforced в compose; restart в module.yaml == restart в compose. | `python3 validate_module_yaml.py --all` exit 0 после фиксов; CI gate вызывается через Makefile target `validate-modules` |
| **W3-E2** AGE_SECRET_KEY в .env.example | P06 | Добавить `AGE_SECRET_KEY=` в `.env.example` для platform-secrets, с комментарием о fails-closed boot | `.env.example` содержит AGE_SECRET_KEY |
| **W3-E3** `${VAR:?}` для критичных секретов | P07 | Заменить raw `${POSTGRES_PASSWORD}`, `${CLICKHOUSE_PASSWORD}`, `${LITELLM_MASTER_KEY}`, `${MINIO_ROOT_PASSWORD}` → `${VAR:?error message}` во всех compose-файлах | `rg '\$\{(POSTGRES_PASSWORD|CLICKHOUSE_PASSWORD|LITELLM_MASTER_KEY|MINIO_ROOT_PASSWORD)\}' core/` — без `:?` = 0 |
| **W3-E4** restart: no enforcement | P08 | Override `restart: no` во всех 13 test-compose файлах (не наследовать unless-stopped) | Все 13 test-compose имеют `restart: no` на верхнем уровне сервисов |
| **W3-E5** CI gate `validate-modules` | — | Makefile target `validate-modules` + регистрация в `core/entrypoint-manifest.yaml` + вызов в CI после lint | Gate red при нарушении D5-контракта |

### 5.2. Риски Wave 3

- **R-RISK-3** (D5-validator находит 10+ нарушений в существующих module.yaml): зафиксировать как technical-debt-tracking, не блокирующий merge валидатора. Фиксы накатываются параллельно.

---

## 6. Wave 4 — Strangler Fig на топ-3 скриптах

**Длительность:** ~8 недель.
**Зависимости:** Wave 1 (gate_helpers, honest tests для regression), Wave 3 (D5-контракт — целевая архитектура модулей). Wave 2 — желательно (SSH timeout снижает риск при тестировании).
**Профит:** top-3 monolith → тестируемые Python-модули; maintainability +50%.

### 6.1. Эпики Wave 4

| Эпик | Проблема | Что делаем | Acceptance |
|------|----------|------------|------------|
| **W4-E1** deploy-modules.sh декомпозиция | P03, P09 | Разбить 1633 строк на 5 Python-модулей в `core/internal/deploy/`: `docker_orchestrator.py`, `sudoers_generator.py`, `context_overlay.py`, `secrets_validator.py`, `orphan_reconciler.py`. Shell остаётся фасадом <100 строк. | `wc -l core/internal/bootstrap/deploy-modules.sh` <100; каждый Python-модуль имеет unit-тесты; `test_deploy_modules.py` regression green |
| **W4-E2** node-lifecycle.sh декомпозиция | P03 | Выделить state-machine transitions (checkpoint-step logic) в `core/internal/bootstrap/node_lifecycle.py`. Shell — фасад. | `wc -l core/internal/bootstrap/node-lifecycle.sh` <200; state-machine unit-тестирована; `test_bootstrap_auto.py` green |
| **W4-E3** converge.sh reconcile-loop → Python | P03 | Перенести reconcile-loop в Python (`core/internal/converge/reconciler.py`). Shell вызывает Python, получает report, логирует. | `wc -l core/internal/converge/converge.sh` <150; reconcile unit-тесты; existing converge-тесты green |
| **W4-E4** Makefile include-split | (§4.2 отчёта) | Разбить root Makefile на `makefiles/{bootstrap,deploy,scaffold,modules,ci,helpers}.mk` через `include`. | `make -n <target>` работает для каждого `.PHONY`; `wc -l Makefile` <150 (только include'ы и общие macros) |
| **W4-E5** Regression test suite | R-RISK-5 | Перед extraction: добавить regression-тесты для edge-cases (parallel deploy, orphan-reconciliation, checkpoint-resume). | Новые тесты покрывают edge-cases; тесты запускаются до и после extraction |
| **W4-E6** Inline python3 завершение | P12 | Оставшиеся inline `python3 -c` вызовы в топ-3 скриптах (те, что не покрыты W1-E7) мигрируются в Python-модули в ходе декомпозиции | `rg "python3 -c" core/internal/bootstrap/` — 0 вхождений в deploy-modules, node-lifecycle, converge |

### 6.2. Риски Wave 4

- **R-RISK-4** (Makefile include-split ломает tab-parsing): CI gate `make -n <target>` для каждого `.PHONY` до/после split.
- **R-RISK-5** (extraction ломает runtime): regression-тесты ДО extraction (W4-E5 первым).

### 6.3. Принципы Strangler для Wave 4

1. **Сначала тесты, потом extraction** — каждый Python-модуль получает unit-тесты ДО того, как shell-вызов переключается на `python3 script.py`.
2. **Тонкий shell-фасад** — shell сохраняет совместимость с Makefile/CI; внутренности — Python.
3. **Один скрипт за раз** — нет параллельной миграции deploy-modules + node-lifecycle + converge. Последовательно: deploy-modules → converge → node-lifecycle.

---

## 7. Wave 5 — Bootstrap Reliability + Converge K8s-parity

**Длительность:** ~10 недель.
**Зависимости:** Wave 4 (converge.sh уже мигрирован в Python, легче расширять).
**Профит:** recovery reliability; converge K8s-parity 4/10 → 7/10.

### 7.1. Эпики Wave 5

| Эпик | Проблема | Что делаем | Acceptance |
|------|----------|------------|------------|
| **W5-E1** Transactional deploy_docker_group | P05 | Реализовать atomic success-or-rollback: при failure в одном из параллельных контейнеров → `docker compose down` на failed siblings + rollback-state-record. Параллельность внутри группы сохраняется. | Тест: симуляция failure 1 контейнера в группе → atomic rollback всех в группе; audit-trail фиксирует rollback |
| **W5-E2** Converge: docker-volumes drift | P13 | Добавить детекцию + reconciliation дрейфа docker-volumes между desired state и actual | Тест: ручное изменение volume → converge обнаруживает и восстанавливает |
| **W5-E3** Converge: sudoers drift | P13 | Сравнение sudoers с desired state, self-heal | Тест: ручное изменение sudoers → converge восстанавливает |
| **W5-E4** Converge: runtime-state reconciliation | P13 | Container runtime-state (running/restarting/unhealthy) → self-heal без ручного `restart` | Тест: `docker stop <service>` → converge поднимает |
| **W5-E5** R5/R6 self-heal | P13 | Перевести R5 (orphan reconciliation) и R6 (image-age) из detect-only в self-heal | Тест: orphan-контейнер → converge удаляет; aged image → converge обновляет |
| **W5-E6** State-machine explicit | (§4.5 Option C отчёта) | Checkpoint-step → явная state-machine: состояния в JSON, переходы с pre/post-условиями, retry-policy. | JSON state-file существует; transitions детерминированы; unit-тестированы |

### 7.2. Риски Wave 5

- **R-RISK-6** (transactional deploy замедляет cold-start): сохранить parallel-within-group, только atomic-rollback на failure.
- **R-RISK-7** (converge K8s-parity overkill для bare-metal): **HARD STOP на 7/10** — не реализуем continuous-watch (это systemd-timer territory). Self-heal R5/R6 — последний шаг.

---

## 8. Decision Gate — Post-Wave 5 Evaluation

**Тип:** Аналитический артефакт (не DevPlan, не delivery-волна).
**Длительность:** ~2 дня (анализ + документ).
**Зависимости:** завершение Wave 5.
**Профит:** зафиксированное архитектурное направление на 2027+.

### 8.1. Что делаем

| Задача | Что делаем | Acceptance |
|---------|------------|------------|
| **DG-1** Metrics collection | Собрать метрики за период программы: test-coverage до/после, change-cost (время на типичное изменение в бывших топ-3 скриптах), incident-rate, CI-gate execution time, shell→Python ratio, inline python3 count | Дашборд / отчёт с baseline → current → trend |
| **DG-2** Decision document | TRAP[DECISION] в root AGENTS.md: валидация стратегии (метрики подтверждают/опровергают курс на Python?). **Это validation gate, а не точка отказа** — программа нацелена на Outcome A. Если метрики против — анализируем причины (недостаточный объём миграции? внешние факторы?), а не откатываем стратегию. | TRAP[DECISION] с rationale, analysis, recommendation на 2027 |

### 8.2. Критерии для продолжения Strangler (Outcome A)

- change-cost на бывших топ-3 скриптах снизился >40%;
- test-coverage на migrate-областях >80%;
- инцидентов regressions <2 за квартал;
- CI-gate execution time <90 сек (после pytest-xdist).

### 8.3. Критерии для анализа причин (если метрики не достигнуты)

- change-cost не снизился значимо (<20%) → проверить: достаточен ли объём миграции? какие скрипты остались узким местом?
- возникли сложности с SSH-оркестрацией из Python (ControlMaster, SSH-agent transparency) → зафиксировать границу «orchestration = shell, logic = Python» явным контрактом.
- команда предпочитает shell для imperative orchestration → провести ретроспективу: что именно в Python-подходе создало трение?

**Pre-commitment (зафиксирован здесь):** Программа нацелена на Outcome A (Python для всей business-logic, shell для orchestration). Decision Gate — это validation того, что метрики движутся в правильном направлении, а не бинарный выбор «Python или shell».

---

## 9. Risk Register программы

| ID | Risk | Likelihood | Impact | Mitigation | Wave |
|----|------|-----------|--------|------------|------|
| **PGM-R1** | Языковая политика не соблюдается из-за отсутствия CI gate | M | M | Code review checklist + AGENTS.md enforcement + pre-commit hook на новые inline python3 (W1-E7). Если через квартал нарушений >3 → поднять вопрос о CI gate (Whitelist .sh) |
| **PGM-R2** | Strangler-extraction ломает production-deploy | M | H | Regression-тесты ДО extraction; staging-деплой перед production; audit-trail для отката |
| **PGM-R3** | Волны растягиваются по времени, программа теряет momentum | H | M | Каждая волна — отдельный DevPlan с явным deadline + production-релиз на выходе; завершение волны = ретроспектива + демонстрация профита. Delivery каждые 3-10 недель поддерживает momentum |
| **PGM-R4** | D5-контракт выявляет 10+ нарушений в существующих module.yaml → блокирует Wave 3 | H | L | Debt-tracking, не блокирующий merge валидатора |
| **PGM-R5** | K8s-parity converge (Wave 5) превращается в overkill | M | M | HARD STOP на 7/10; continuous-watch = явный non-goal |
| **PGM-R6** | Команда устаёт от миграции, Wave 4-5 забрасываются | M | H | Decision Gate = evaluation checkpoint; если метрики показывают ROI — продолжаем; если нет — фиксируем hybrid с явным контрактом |
| **PGM-R7** | Feature freeze на время программы — новые фичи конфликтуют с миграцией | M | M | Языковая политика (новый код = Python) предотвращает конфликт: новые фичи и так будут на Python. Приоритет: критичные фичи > миграция. Bug-fixes не блокируются (двухуровневый триггер) |
| **PGM-R8** | Десинхронизация Brief ↔ Reality — метрики и проблемы меняются между волнами | H | L | Перед стартом каждой волны: verify problem matrix против текущего состояния кодовой базы (принцип 9 — Read before Act). Актуализировать цифры в Brief при существенных расхождениях |
| **R-RISK-1** | `lib/ssh.sh` ломает remote-CMD (single point of failure для ВСЕХ remote-операций) | M | **H** (повышено) | Unit-test на staging-ноде перед merge; отдельная feature-ветка; возможность быстрого revert. Impact H: затрагивает deploy, bootstrap, healthcheck, node-update, converge, project-list/status, remove-project, verify |
| **R-RISK-2** | R4-fix (skip → fail) временно ломает CI на staging (отсутствие Docker) | H | M | Поэтапно: `pytest.mark.requires_docker` → `xfail(strict=False)` → `fail` |
| **R-RISK-3** | D5-validator находит 10+ нарушений в существующих module.yaml | H | L | Зафиксировать как technical-debt-tracking, не блокирующий merge валидатора |
| **R-RISK-4** | Makefile include-split ломает tab-sensitive parsing | M | H | CI gate на `make -n <target>` для каждого `.PHONY` до/после split |
| **R-RISK-5** | Strangler-extraction ломает runtime скриптов | M | H | Regression-тесты ДО extraction (W4-E5 первым) |
| **R-RISK-6** | Transactional deploy_docker_group замедляет cold-start | L | M | Сохранить parallel-within-group, добавить только atomic-rollback на failure |
| **R-RISK-7** | Converge K8s-parity слишком сложен для bare-metal | M | M | HARD STOP на 7/10 — не пытаться в continuous-watch |
| **R-RISK-8** | Audit-wrapper добавляет overhead на каждый entrypoint | L | L | Async-write через `>>` в `/var/log/platform/audit.log` |
| **R-RISK-9** | Inline python3 pre-commit hook даёт false-positives на легитимные однострочники | M | L | Whitelist для вызовов через `yaml_read.sh` / `yaml_query.py` facade; ignore-pattern для `python3 -c "import yaml; ..."` внутри одобренных lib-файлов |
| **R-RISK-10** | CI composite ломает кеширование → CI медленнее до стабилизации | M | L | Замерить baseline до и после; принять временное замедление как цену унификации |

---

## 10. Метрики успеха программы

### 10.1. Количественные KPI

| Метрика | Baseline (2026-07-21) | Цель (конец Wave 5) |
|---------|----------------------|---------------------|
| Production Python строк | ~2K | ~8-10K |
| Shell строк (excl. entrypoints) | ~15K (скорректировано) | ~8-10K |
| Inline `python3 -c` вызовов | 103 в 20+ файлах | 0 (все вынесены в Python-модули или используют facade) |
| `<<PYEOF` heredoc блоков | 1 (21 строка в deploy-modules.sh) | 0 |
| `pytest.skip("... not available")` | 18 | 0 |
| gate-тесты без `_negative` | 3 | 0 |
| R4/R5 нарушений | ~21 | 0 |
| SSH-вызовов без timeout | TBD (замерить в W1-E8) | 0 |
| Audit-trail покрытие modify-state ops | 2/9 | 9/9 |
| converge K8s-parity score | 4/10 | 7/10 |
| `make gate MODE=fast` time | TBD (замерить в W1-E8, оценка ~3-5 мин) | <90 сек (после pytest-xdist) |
| `PROJECT_ROOT` дубликатов в tests/ | 70+ | 1 (единый source в gate_helpers.py) |
| `_load_yaml` дубликатов в tests/ | 6 | 1 (в gate_helpers.py) |
| `usage()` дубликатов в core/ | 14 | 1 (в lib/args.sh) |
| Duplicated CI steps (checkout) | 10 вхождений | ≤3 (whitelist) |
| Dead code (functions) | 2 из 330 (0.6%) | 0 |

### 10.2. Качественные KPI

- AGENTS.md содержит явную языковую политику (двухуровневый Strangler-триггер), соблюдаемую командой.
- Каждая переписанная подсистема имеет unit-тесты (zero inline python3).
- CI green = доверие (нет false-green).
- Decision-документ зафиксировал направление 2027+ на основе метрик.
- После каждой волны — production-релиз с измеримым профитом.

---

## 11. Порядок делегирования в dev-pipeline

Каждая delivery-волна делегируется через dev-pipeline skill. Decision Gate — аналитический артефакт, не требует DevPlan.

```
027-Brief.md (этот файл)
    │
    ├─► Wave 1 DevPlan: .ai/plans/028-wave1-immediate/01-Brief.md → 02-DevPlan.md → Code → 03-VerificationReport.md
    ├─► Wave 2 DevPlan: .ai/plans/029-wave2-dangerous/...
    ├─► Wave 3 DevPlan: .ai/plans/030-wave3-contract-d5/...
    ├─► Wave 4 DevPlan: .ai/plans/031-wave4-strangler-top3/...
    ├─► Wave 5 DevPlan: .ai/plans/032-wave5-bootstrap-reliability/...
    └─► Decision Gate (аналитический артефакт, ~2 дня, не DevPlan)
```

**Принципы делегирования:**
1. Одна delivery-волна = один DevPlan = одна сессия dev-pipeline (Brief → Architect → Coder → QA → Fix).
2. Каждая волна завершается **production-релизом** — это поддерживает momentum и даёт измеримый профит.
3. Перед стартом новой волны: **verify problem matrix** против текущего состояния кодовой базы (принцип 9 — Read before Act). Если состояние существенно изменилось — актуализировать метрики в этом Brief.
4. После завершения каждой волны: **прикрепить ссылку на DevPlan и VerificationReport** к этому Brief для tracking'а прогресса.
5. Архитектор каждой волны ОБЯЗАН прочитать `reports/architecture-analysis-2026-07-21.md` и актуальную версию этого Brief.
6. Decision Gate — не DevPlan, а аналитическая сессия (~2 дня): сбор метрик + TRAP[DECISION] документ.

---

## 12. Anti-goals (что мы НЕ делаем)

- ❌ Big-bang rewrite всех shell-строк (Option A §4.1, score 3/10).
- ❌ Миграция стабильных shell-библиотек (`logging.sh`, `paths.sh`, `healthcheck.sh`, `module-interface.sh`) — их API стабилен, замена не даст прироста.
- ❌ Миграция на Just/Task (Option C §4.2) — Makefile остаётся единым фасадом.
- ❌ Coverage-first вместо honesty-first (Option B §4.3) — без доверия к тестам coverage бесполезен.
- ❌ Continuous-watch в converge (Option D §4.5) — systemd-timer territory.
- ❌ ACID-транзакции для bootstrap (Option B §4.5) — overkill для bare-metal.
- ❌ CI gate на создание .sh файлов (по решению оператора — enforcement через AGENTS.md + pre-commit hook).
- ❌ Property-based testing (Option C §4.3) — откладывается до post-Wave 5.

---

## 13. Pre-commitment: Стратегическое направление

Программа нацелена на **Outcome A: Python для всей business-logic, shell для orchestration**. Это не «попробуем и посмотрим» — это architectural direction, зафиксированный здесь. Decision Gate (после Wave 5) — это validation того, что метрики движутся в правильном направлении. Если метрики против — мы анализируем **причины** (недостаточный объём миграции? внешние факторы? неправильный триггер?), а не откатываем стратегию.

**Почему это важно:** без pre-commitment команда работает в режиме двойного сознания («а может, оставим shell?»), что снижает качество миграции и создаёт self-fulfilling prophecy (мало мигрировали → метрики не улучшились → «доказано, что Python не нужен»).

---

$END_BRIEF

---

## Заключение

Этот бриф — стратегическая рамка на 5 delivery-волн (~30-35 недель) + Decision Gate. Каждая волна может быть выделена в отдельный DevPlan через dev-pipeline skill и завершается production-релизом.

**Wave 1 (~3-4 нед)** — нулевой риск, немедленный профит: честные тесты, языковая политика, консолидация inline python3, −400 строк бойлерплейта.
**Wave 2 (~3-4 нед)** — высокий риск, явный профит: SSH timeout (CI hangs → fast-fail), CI composite (−30s/workflow), 7 audit-точек.
**Wave 3 (~5 нед)** — D5-контракт, типизированные env_requires, `${VAR:?}` enforcement.
**Wave 4 (~8 нед)** — Strangler-декомпозиция топ-3 скриптов, Makefile include-split.
**Wave 5 (~10 нед)** — transactional deploy, converge K8s-parity 7/10.
**Decision Gate (~2 дня)** — аналитический артефакт, фиксирует направление 2027+.

**После реализации каждого DevPlan** — прикрепить ссылку на DevPlan и VerificationReport к этому Brief для tracking'а прогресса и планирования следующей волны.

**Готов к делегированию Wave 1 в dev-pipeline по команде оператора.**
