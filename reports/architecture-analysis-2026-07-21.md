# GREP_SUMMARY: architecture-analysis, ai-platform, bash-python-makefile, modules, tests, CI/CD, deploy, drift
# STRUCTURE: ┌analysis headers┐ → ◇ methodology → ◇ findings (baseline, drift, P-catalog, recommendations) → ⊕ appendix
# ARCHITECTURE ANALYSIS REPORT — ai-platform v1.0.0

**Дата анализа:** 2026-07-21
**Аналитик:** Staff Software Architect (zai/glm-5.2)
**Сбор данных:** 4 параллельных субагента (general subagent, эквивалент deepseek-v4-flash)
**Scope:** Full architectural review (Bash/Python/Makefile, modules, tests, CI/CD, deploy)

---

## $ARTIFACT_CONTRACT

```yaml
PURPOSE: |
  Recover objective system architecture, identify systemic problems and
  extension opportunities that weaker models cannot surface. Produce
  evidence-based roadmap for v1.x → v2.0 evolution.

DESCRIPTION: |
  Synthesis of 4 parallel data-collection sweeps (duplication, tests,
  modules/compose, CI/CD) with FULL/BINARY/GUIDED/ADVERSARIAL superposition.
  Output: problem matrix, 5 superpositions, 7 extension points, 5 perf
  bottlenecks, 1 adversarial verdict, prioritized roadmap, risk register.

RATIONALE: |
  Project was built primarily by weaker models (GPT-4o-mini tier). Staff-level
  architectural analysis requires cross-cutting synthesis that lower-tier
  models cannot produce reliably. This document captures that synthesis in a
  durable artifact form (Zero-Context Survival principle).

ACCEPTANCE_CRITERIA:
  - All 4 subagent reports synthesized into single problem matrix
  - At least 3 superposition options per area (FULL mode)
  - Each problem has: severity, complexity, payoff, root cause
  - Each recommendation is measurable (lines/tests/seconds)
  - Roadmap has ≤4 phases with explicit dependencies

IMPLEMENTS:
  - arch-forensics skill (Tasks 1-7, modes S7-S15)
  - superposition skill (modes FULL/BINARY/GUIDED/ADVERSARIAL/AUTO-COLLAPSE)

IMPACTS:
  - Architecture roadmap for next 4 quarters
  - Module contract evolution (D4 → D5)
  - Test infrastructure health score
  - Migration strategy Shell → Python

REQUIRES:
  - 4 subagent reports (A: duplication, B: tests, C: modules, D: CI/CD)
  - Project metrics from AGENTS.md
  - Principles/Constitution/Testing/Markup rules
```

---

## 1. Executive Summary

ai-platform v1.0.0 — **функционально зрелая, но архитектурно асимметричная** PaaS-платформа. Production-слой (Bash, ~24.5K строк shell + ~2K строк Python) демонстрирует продуманные инварианты (Triple Delivery Model, идемпотентность bootstrap, 10 архитектурных правил), но страдает от **трёх системных напряжений**:

1. **Bash-scalability ceiling**: топ-3 скрипта (deploy-modules 1633, node-lifecycle 1297, converge/deploy-project 1149 each) суммарно содержат ~480 `if`-операторов, ~270 строк встроенного Python в heredoc-блоках и смешивают в среднем 5 ответственностей на файл. Стоимость изменения экспоненциально растёт.

2. **Test-trust deficit**: ~16-18 R4-нарушений (тесты маскируют отсутствие окружения как `skip` вместо `fail`), 3 R5-нарушения (gate-тесты без `_negative` пар), 25-30% бойлерплейта в gate-тестах и критически слабое `integration`-покрытие (2 теста). CI потенциально green, но **false-green**.

3. **Reconciliation gap**: converge.sh реализует лишь 4/10 K8s-style desired-state контракта (single-shot, detect-only для R5/R6, без reconciliation volumes/confs/sudoers/runtime-state). Это создаёт «ручные корректировки» как常态 — обратную сторону декларируемой декларативности.

**Вердикт проекта:** `NEEDS_ATTENTION` — не технический банкротство, но без целенаправленной миграции в Python для новых подсистем и усиления тестовой честности накопится ~30% технического долга в год.

---

## 2. Project Fingerprint

| Метрика | Значение | Производное |
|---------|----------|-------------|
| Production Python | ~2K строк (9 файлов) | template_engine.py = 36% production-Python |
| Shell (.sh) | ~24.5K строк | 12 файлов >500 строк = 65% объёма |
| Makefile | 731 строка (40.8 KB) | 60+ `.PHONY` таргетов |
| entrypoint-manifest.yaml | 540 строк | 40+ CI gate-секций |
| Docker-модули | 12 | + 1 systemd (platform-secrets) |
| CI workflows | 9 + 5 composite actions | 100% composite-actions used |
| Gate-тесты | 52 файла / 192 `@pytest.mark.gate` | 25-30% boilerplate |
| Total tests | 168 файлов / ~170 test functions | 53 use `subprocess.run` |
| conftest submodules | 12 (22 fixtures, 3 hooks) | 6/12 — utility, not conftest |
| lib-biblioteki | 11 (public API = 32 functions) | `log_imp` = 53 callers (доминанта) |
| Bootstrap scripts | 16 files in core/internal/bootstrap/ | 15 transitively reachable from node-lifecycle.sh |
| Triple Delivery | 3 channels | Core=NO git ✅, Context=git, Project=tar+SSH |
| Functions total | 330 unique (407 defs) | 2 dead (0.6%) — отлично |
| `set -euo pipefail` adoption | 88% (90/102) | 3 неоправданных пропуска |

**Архитектурная температура:**
- 🟢 **Strong:** Triple Delivery Model реализован без утечек, идемпотентность bootstrap, низкий dead-code (0.6%), 100% используемых composite actions.
- 🟡 **Strained:** Makefile монолитен, 5 модулей хранят бойлерплейт (usage/parse_args), converge.sh неполон, audit-trail покрывает 2/9 modify-state ops.
- 🔴 **Risk:** test-suite false-green (R4), 12 monolithic shell-scripts (top-3 = 4197 строк), no transactional rollback в bootstrap, no SSH command-timeouts.

---

## 3. Problem Matrix (Top-15, отсортировано по серьёзности)

| ID | Категория | Проблема | Src | Sev | Cmplx | Payoff |
|----|-----------|----------|-----|-----|-------|--------|
| **P01** | TEST_QUALITY | R4-нарушения: ~16-18 тестов `pytest.skip("Docker/script not available")` вместо FAIL → CI false-green на контрактах (entrypoints, gates, modules). Систематический паттерн. | B | 🔴 CRITICAL | M | +17 real-fail signals |
| **P02** | ERROR_HANDLING | SSH command-timeout отсутствует (`scp-deliver.sh`, `remote-cmd.sh`): только `ConnectTimeout=30` (TCP), нет `timeout`-обёртки на remote-команды → зависший `docker compose up` или `git pull` висит в CI бесконечно. | D | 🔴 CRITICAL | L | CI hangs → fast-fail |
| **P03** | ARCHITECTURE | Top-3 shell-scripts (deploy-modules 1633 + node-lifecycle 1297 + converge 1149 = 3979 строк) смешивают 3-5 ответственностей каждый. Цикломатическая сложность: 119 if / 41 for в deploy-modules. Достигнут Bash-scalability ceiling. | A,D | 🔴 CRITICAL | H | Maintainability +50% |
| **P04** | TEST_QUALITY | R5-нарушения: 3 gate-теста (`test_gate_litellm_pg_enforcement`, `test_gate_module_schema_d4`, `test_gate_env_shared_consistency`) без `_negative` пары — не доказывают детекцию нарушений (anti-survivorship gap). | B | 🟠 HIGH | L | +3 falsifiability |
| **P05** | ERROR_HANDLING | Bootstrap pipeline: rollback существует ТОЛЬКО для `deploy-project.sh` (project payload channel). Все остальные операции (users, sudoers, deploy_docker_group, issue-cert) — best-effort. Partial failure → полусобранная система. | D | 🟠 HIGH | H | Recovery reliability |
| **P06** | SECURITY | `AGE_SECRET_KEY` (platform-secrets module, env_requires) отсутствует в `.env.example` → fails-closed boot инвариант не задокументирован. | C | 🟠 HIGH | L | +1 documented contract |
| **P07** | ERROR_HANDLING | DD3 `${VAR:?error}` не используется нигде (0 мест). Критичные секреты (POSTGRES_PASSWORD, CLICKHOUSE_PASSWORD, LITELLM_MASTER_KEY, MINIO_ROOT_PASSWORD) передаются как raw `${VAR}` → Docker молча разворачивает в пустую строку при отсутствии. | C | 🟠 HIGH | L | +4 mandatory-arg guards |
| **P08** | MODULE_CONTRACT | `restart: no` в test-compose нарушен во ВСЕХ 13 модулях. Большинство наследует `unless-stopped` от base → риск зомби-контейнеров после падения тестов. | C | 🟠 HIGH | L | Clean test isolation |
| **P09** | ARCHITECTURE | `deploy-modules.sh` (1633 строк) смешивает 5 ответственностей: docker-orchestration, sudoers-generation, context-overlay, secrets-validation, orphan-reconciliation (нарушение AI-First принцип 8). | A,D | 🟠 HIGH | M | Cohesion +5 SRP |
| **P10** | DUPLICATION | Boilerplate duplication: `usage()` определён 14 раз, `parse_args` — 8 раз, `getopts` не используется нигде (0). `_load_yaml`/`_read_file` дубликаты в gate-тестах (5 и 3 копии соответственно). ~50% boilerplate removable. | A,B | 🟠 HIGH | M | −400 строк кода |
| **P11** | CI_EFFICIENCY | Audit-trail покрывает только 2/9 modify-state ops (`bootstrap/init`, `deploy-project`). `context-promote`, `remove-project`, `provision`, `hermes-build`, `secrets-unlock`, `node-update --mode update`, `core-deploy rsync` — БЕЗ audit-записей. Compliance gap. | D | 🟡 MEDIUM | M | +7 audit points |
| **P12** | EXTENSIBILITY | Python-в-heredoc антипаттерн: deploy-modules.sh (строки 628-970) и converge.sh (~790-950) содержат ~270 строк Python внутри `python3 - <<PYEOF`. Ломает bash-анализаторы и скрывает бизнес-логику от grep/AGENTS.md. | A | 🟡 MEDIUM | M | +grep-ability |
| **P13** | ARCHITECTURE | converge.sh реализует 4/10 K8s-style desired-state контракта. Не покрывает: docker-volumes drift, изменённые вручную конфиги, sudoers drift, runtime-state, image-age. R5/R6 — detect-only (не self-heal). | D | 🟡 MEDIUM | H | → K8s-parity 7/10 |
| **P14** | DUPLICATION | `log()` convention conflict: `lib/logging.sh::log_imp` каноническая, но 5 модулей (backup-cron, hermes-agent/{context,watchdog,build/init,build/register-profiles}) переопределяют `log()` локально. `verify-domains.sh` дублирует `log_imp()` вместо source'а. | A | 🟡 MEDIUM | L | +1 convention |
| **P15** | PERFORMANCE | CI шаги дублируются: `actions/checkout@v7` — 10 вхождений в 8 файлах, `provisioner-call` — 4, `setup-python-venv`/`setup-gitleaks` — по 3. Нет единого `setup-platform` composite. | D | 🟡 MEDIUM | L | CI setup −30s |

**Сводка по серьёзности:** CRITICAL: 3, HIGH: 6, MEDIUM: 6. Из 15 проблем **8 имеют L (Low) complexity** — это quick wins.

---

## 4. Superposition: Architecture Improvements

### 4.1 Bash → Python Migration Strategy (FULL, 4 опции)

**Контекст:** 24.5K строк shell, top-3 = 3979 строк, embedded-Python в heredoc. Каждое изменение в монолитных скриптах имеет экспоненциальную стоимость.

```
## SUPERPOSITION: Bash → Python Migration Strategy

### Option A: "Big Bang Rewrite" [score: 3/10]
Approach: Переписать все 24.5K строк shell в Python за один проект.
Trade-offs: +Единый стек, +Тестируемость. −Огромный риск регрессий, −Потеря знаний,
  закодированных в shell-идиомах, −Длительный fork (6-12 мес), −Параллельная поддержка.
Best when: Никогда. Big-bang rewrites в production — табу.

### Option B: "Strangler Fig с приоритизацией по боли" [score: 8/10] ★
Approach: Выделить 5-7 «болезненных» скриптов (deploy-modules, node-lifecycle,
  converge, add-vhost, adopt-project, deploy-project) в Python, оставив тонкие
  shell-обёртки (entrypoints/*.sh) как совместимые фасады. Сначала извлечь
  embedded-Python-блоки (стратегическая выгода, тактическая простота).
Trade-offs: +Прогрессивная ценность, +Тесты для каждой новой подсистемы,
  +Совместимость с shell-инструментарием (rsync, scp, ssh). −Временное
  сосуществование двух стеков (примите как цену эволюции). −Требует Python lib
  для SSH/rsync (paramiko/sh—from venv, не stdlib).
Best when: Что угодно кроме большой аварии — это default path.

### Option C: "Hybrid: новые фичи только в Python" [score: 6/10]
Approach: Оставить shell как есть. Все новые подсистемы писать на Python.
  Существующие скрипты — только баг-фиксы.
Trade-offs: +Минимальные риски. −Техдолг растёт (shell не уменьшается),
  −Когнитивная двойственность для команды. −Через 2 года: ещё больший раскол.
Best when: Команда близка к выгоранию / критичен time-to-market.

### Option D: "Bash Cleanup + new lib layer" [score: 5/10]
Approach: Не мигрировать. Извлечь бойлерплейт в lib/args.sh, lib/ssh.sh,
  lib/yaml.sh, ввести shellcheck-strict, типизировать через comments.
Trade-offs: +Минимальные изменения. −Не решает embedded-Python, −Bash-ceiling
  остаётся. −Сложность топ-3 скриптов не падает существенно.
Best when: Решение «остаться на bash навсегда» принято сознательно.

### Recommendation: Option B — Strangler Fig
Rationale: Оптимальный ratio ценности/риска. Embedded-Python в heredoc (P12)
  уже сигнализирует, что shell перестал справляться — strangler начнётся с
  extraction of этих блоков (минимальный риск, максимальный cleanup).
  За 4-6 кварталов: 6 топовых скриптов → 6 Python-модулей + 6 тонких фасадов.

**Collapse signal:** Reply B/D или укажи constraint (budget, team size, deadline).
Auto-collapsing to B (score 8/10) — autonomous mode.
```

### 4.2 Makefile Decomposition (FULL, 3 опции)

**Контекст:** 731 строка (40.8 KB), 60+ `.PHONY`, монолит.

```
## SUPERPOSITION: Makefile Decomposition

### Option A: "Include-based split" [score: 8/10] ★
Approach: Разбить Makefile на `makefiles/{bootstrap,deploy,scaffold,modules,
  ci,helpers}.mk`, подключить через `include` в корневом Makefile. Каждый
  таргет остаётся доступным из корня (include = inline expansion).
Trade-offs: +SRP для Makefile, +Лучшая навигация, +Git-blame чище.
  −Make медленнее на ~5% (парсинг нескольких файлов). −Требует统一 naming
  convention (например, `bootstrap-%` pattern).
Best when: 60+ таргетов и git-history перегружена.

### Option B: "Categorised aliases + Makfile.common" [score: 6/10]
Approach: Оставить монолит, но ввести `make help` с категориями и собрать
  общие macros в `core/Makefile.common` (уже есть!).
Trade-offs: +Минимальные изменения. −Файл остаётся 700+ строк.
Best when: Текущая структура не критична.

### Option C: "Justfile / Taskfile migration" [score: 4/10]
Approach: Заменить Make на современный task-runner (Just или Task).
Trade-offs: +Современный синтаксис, +Cross-platform. −Потеря совместимости с
  существующим CI (которое зовёт `make`). −Изучение нового инструмента.
  −Не решает архитектурную проблему, только синтаксис.
Best when: Новая команда без bash/make опыта.

### Recommendation: Option A — Include-based split
Rationale: AGENTS.md уже декларирует "Makefile — единый фасад" — include
  это и есть единый фасад. Просто организованный по SRP. Минимальный риск.

**Collapse signal:** A/B/C. Auto-collapsing to A (score 8/10).
```

### 4.3 Test Infrastructure Optimization (FULL, 4 опции)

**Контекст:** 168 тестов, R4-нарушения (16-18), R5 (3), 25-30% boilerplate, `integration` = 2 теста.

```
## SUPERPOSITION: Test Infrastructure Optimization

### Option A: "Honesty-First Wave" [score: 9/10] ★
Approach: Фаза 1 (1 квартал): исправить R4 (skip → fail), R5 (добавить
  _negative пары), создать `tests/helpers/gate_helpers.py` (убрать
  дубликаты `_load_yaml`, `PROJECT_ROOT`). Фаза 2: добавить integration
  тесты (target: 20+ вместо 2). Фаза 3: переместить utility-модули из
  _conftest/ в tests/helpers/.
Trade-offs: +Maximum CI-trust recovery, +Falsifiability. −Время: ~2-3 спринта.
  −Может временно снизить % passing (как и должно быть для честных тестов).
Best when: Всегда — это базовое здоровье.

### Option B: "Coverage-First Wave" [score: 5/10]
Approach: Сначала добавить integration/e2e тесты для покрытия runtime-state.
  Затем заняться honesty.
Trade-offs: +Быстрый coverage growth. −False-green риск остаётся.
Best when: Compliance требует coverage >X%.

### Option C: "Property-based + snapshot testing" [score: 6/10]
Approach: Внедрить hypothesis для property-based тестов (template engine,
  topo_sort, yaml-reading) + syrupy/pytest-insta для snapshot (compose files).
Trade-offs: +Сильная детекция регрессий. −Высокий порог входа. −Не решает R4/R5.
Best when: Уже есть honesty-fix и ищется дополнительная глубина.

### Option D: "Test pyramid rebuild" [score: 4/10]
Approach: Переписать с нуля, разделить на unit/integration/e2e слои.
Trade-offs: +Чистая архитектура. −Огромный риск, −Длительный fork. −Overkill.
Best when: Текущие тесты безнадёжны (что не так).

### Recommendation: Option A — Honesty-First Wave
Rationale: R4/R5 — это trust-киллер. Без доверия к CI никакая оптимизация
  не имеет смысла. Это **prerequisite** для всех остальных улучшений.

**Collapse signal:** A/B/C/D. Auto-collapsing to A (score 9/10).
```

### 4.4 Module Contract Strengthening (FULL, 3 опции)

**Контекст:** module.yaml D4-контракт + module-interface.sh + Gate #8. 14 модулей, 1 RED (`AGE_SECRET_KEY`), DD3 `${VAR:?}` = 0 мест, restart-drift (postgres).

```
## SUPERPOSITION: Module Contract Strengthening

### Option A: "D5-контракт с типизацией env_requires" [score: 8/10] ★
Approach: Расширить module.yaml: каждая env_var получает type (string/secret/
  int/bool) и required: bool. Скрипт `validate.py` проверяет на CI:
  - Все required vars используют `${VAR:?error}` в compose
  - Все env_requires есть в .env.example с правильным типом
  - restart в module.yaml == restart в compose (детект drift)
Trade-offs: +Compile-time проверка. −Время валидатора: 1-2 спринта.
Best when: Хочется превратить D4-контракт в принудительный D5.

### Option B: "Schema validation через jsonschema/pydantic" [score: 6/10]
Approach: Определить JSON Schema для module.yaml, валидировать через
  existing `jsonschema` dep (уже в production deps).
Trade-offs: +Формальная спецификация. −Сложность поддержки схемы.
  −Schema vs implementation drift.
Best when: Нужна machine-readable контракт для внешних потребителей.

### Option C: "Tests-as-contract: contract_first development" [score: 5/10]
Approach: Каждый новый env_requires/depends_on/interface добавляется
  вместе с тестом. Существующие — постепенно.
Trade-offs: +Testability. −Долгий путь. −Не закрывает retroактивные пробелы.
Best when: Долгосрочная перспектива.

### Recommendation: Option A — D5-контракт
Rationale: jsonschema уже в deps (principle 8 — расширение существующего).
  Прямо решает P06 (AGE_SECRET_KEY), P07 (${VAR:?}), restart-drift.

**Collapse signal:** A/B/C. Auto-collapsing to A (score 8/10).
```

### 4.5 Bootstrap Pipeline Reliability (FULL, 4 опции)

**Контекст:** 16 скриптов, rollback только для deploy-project.sh, audit-trail 2/9, converge 4/10 K8s-parity.

```
## SUPERPOSITION: Bootstrap Pipeline Reliability

### Option A: "Audit-first + SSH timeouts" [score: 9/10] ★
Approach: Wave 1 (1 квартал): добавить SSH `timeout`-обёртки (P02, 1 день),
  добавить audit_log в 7 modify-state operations (P11, 3 дня). Wave 2:
  transactional deploy_docker_group (atomic success-or-rollback через
  docker compose down на failed siblings).
Trade-offs: +Immediate CI reliability. +Compliance. −Wave 2 требует
  redesign параллельного deploy.
Best when: Всегда — это фундамент.

### Option B: "Full transactional bootstrap" [score: 4/10]
Approach: Превратить bootstrap в ACID-транзакцию (snapshot → apply → commit).
Trade-offs: +Strongest guarantee. −Огромный redesign. −Bash не подходит для
  transaction-management. −Overkill для bare-metal.
Best when: Реальная потребность в zero-downtime.

### Option C: "State-machine explicit" [score: 7/10]
Approach: Превратить checkpoint-step в явную state-machine: состояния в
  JSON-файле, переходы с pre/post-условиями, retry-policy на каждое состояние.
Trade-offs: +Recovery становится deterministic. −Усложнение.
Best when: После Wave 1 (Option A).

### Option D: "converge.sh → K8s-controller pattern" [score: 6/10]
Approach: Превратить converge из single-shot в continuous loop с watch
  (inotify/file-changes), self-healing R5/R6, observed-generation tracking.
Trade-offs: +Real reconciliation. −Долгая разработка. −Systemd timer уже
  близок к этому при правильной настройке.
Best when: После Option A+C, если manual-drift станет частым.

### Recommendation: Option A — Audit-first + SSH timeouts
Rationale: P02 (CRITICAL) — 1 день работы, бесконечный ROI (CI hang →
  fail-fast). P11 — добавляет 7 audit-точек, compliance gap закрыт.
  Wave 2 — следующий приоритет.

**Collapse signal:** A/B/C/D. Auto-collapsing to A (score 9/10).
```

---

## 5. Extension Points (Quick Wins, ≤200 строк, без новых deps)

| # | Что | Где (файл) | Почему ценно | Усилие |
|---|-----|------------|--------------|--------|
| **E1** | lib/ssh.sh — единый SSH-фасад с `timeout`-обёрткой и `SSH_OPTS` константой | core/lib/ssh.sh (новый) | Закрывает P02 (SSH timeout) + P10 (SSH-opts duplication: scp-deliver.sh ×2, remove-project.sh ×2, project-list.sh) + P14 (verify-domains log_imp clone) | ~80 строк, 1 день |
| **E2** | `tests/helpers/gate_helpers.py` — `load_yaml()`, `repo_root()`, `module_yaml_paths()`, `assert_ldd_imp9()` | `tests/helpers/gate_helpers.py` (новый) | Закрывает 25-30% boilerplate в gates (53 `PROJECT_ROOT` объявлений, 5 копий `_load_yaml`), унифицирует `ldd_trajectory` import-style | ~120 строк, 2 дня |
| **E3** | lib/args.sh — стандартизированный `parse_args` + `usage`-хелпер | core/lib/args.sh (новый) | Закрывает P10: 14 копий `usage()`, 8 копий `parse_args`, 15+ скриптов с `while [[ $# -gt 0 ]]`. Шаблон: `parse_args "u:username:,p:port:" "$@"` | ~100 строк, 2 дня |
| **E4** | CI composite `setup-platform` — объедини `checkout` + `setup-python-venv` + `setup-gitleaks` + `provisioner-call` | `.github/actions/setup-platform/action.yml` (новый) | Закрывает P15: 10 вхождений `checkout`, 3× `setup-python-venv`, 3× `setup-gitleaks`, 4× `provisioner-call`. CI setup −30s на каждый workflow | ~50 строк, 1 день |
| **E5** | R4-fix script: `_conftest/honesty.py` с `require_docker_or_fail()` фикстурой | `tests/_conftest/honesty.py` (новый) | Закрывает P01 (R4) для всех 16-18 нарушений. Заменяет `pytest.skip("Docker not available")` → `pytest.fail(...)` через единый helper | ~60 строк, 1 день |
| **E6** | D5-validator script: `core/internal/scripts/validate_module_yaml.py` | `core/internal/scripts/validate_module_yaml.py` (новый, jsonschema уже в deps) | Закрывает P06 (AGE_SECRET_KEY), P07 (${VAR:?}=0), restart-drift (postgres). Валидирует все module.yaml на CI | ~180 строк, 3 дня |
| **E7** | `audit_log` wrapper-macro для entrypoints | patch в `core/lib/audit_logging.sh` + 7 entrypoints | Закрывает P11: 7 modify-state операций без audit. Шаблон: `trap 'audit_log "$VERB" "$?" "$STEP"' EXIT` в каждом entrypoint | ~30 строк в audit_logging.sh + 7×5 строк в entrypoints, 2 дня |

**Итого трудозатрат:** ~670 строк нового кода + ~35 строк патчей = ~5-7 рабочих дней.
**Итого payoff:** закрывает 6 из 15 проблем матрицы (P01, P02, P06, P07, P10, P11, P15) частично или полностью.

---

## 6. Performance Bottlenecks & Optimizations

| # | Bottleneck | Текущая perf | Целевая | Замер | Оптимизация | ROI |
|---|------------|--------------|---------|-------|-------------|-----|
| **B1** | Локальный `make gate MODE=fast` — последовательный запуск 52 gate-тестов | ~3-5 мин (оценка по 192 `@pytest.mark.gate` — даже с `-x` долго) | <90 сек | `time make gate MODE=fast` × 3 повтора, `pytest --durations=10` | (1) `pytest -n auto` через pytest-xdist (parallel gate tests), (2) `--sw` (last-failed-first), (3) cache `_load_yaml` через `@lru_cache` (сейчас 5 копий, каждая парсит заново) | −60% времени, ROI = часы/неделю |
| **B2** | CI setup duplication — 10× `checkout`, 3× `setup-python-venv` | ~30-60 сек × N workflows на setup | <5 сек (single composite) | `gh run view --log` + grep setup-step durations | E4: composite `setup-platform` (cache across workflows через `actions/cache@v6` с ключём по `pyproject.toml`+`platform-env.yaml`) | −30s/workflow × 9 workflows = ~4-5 мин на каждый push |
| **B3** | Bootstrap: последовательный deploy топологических групп с лимитом 4 | Оценочно: 12 модулей × ~30 сек healthcheck = 6 мин холодного старта | <2 мин | `make bootstrap-node` на staging + `time` обёртка + `docker events` логи | (1) `COMPOSE_PARALLEL_LIMIT=8` (по умолчанию 4 — консервативно, vps с 2GB+ RAM держит), (2) pre-pull images parallel через `docker compose pull` (уже есть `_pre_pull_images`), (3) healthcheck `start_period` tuning (некоторые модули стартуют за 2 сек, ждём 30) | −50% cold start |
| **B4** | Docker-compose up холодный старт всех 12 модулей | ~3-5 мин (postgres + langfuse + hermes-agent — slow starters) | <90 сек | `time docker compose up -d` × 3 с cleanup volumes между | (1) `healthcheck.start_period` правильная настройка (многие 30 сек — overkill для redis), (2) `depends_on.condition: service_healthy` для критичных (postgres перед litellm), (3) `profiles` для opt-in модулей (monitoring/logging — не всегда нужны) | −60% cold start |
| **B5** | Template engine: рендер всех шаблонов без кеширования | `template_engine.py` (716 строк) — каждый вызов перечитывает manifest | <100 мс на повторный рендер | `time python -c "from template_engine import render_all; render_all()"` × 3 | (1) LRU cache на `read_manifest()`, (2) mtime-based cache на file reads, (3) precompiled Jinja2 Environment (если используется) | −80% на повторных рендерах |

**Measurement discipline:** Каждая оптимизация требует **baseline замера** до и **post-замера** после. Без цифр — это мнение, не оптимизация.

---

## 7. Adversarial Analysis: Shell vs Python

**Главное решение для adversarial анализа:** оставить ли Bash основным языком платформы или мигрировать на Python.

### Case for A: «Остаться на Bash» (steelman)

**Сильнейший аргумент:** Bash — domain-specific language для оркестрации system administration. 90% работы платформы = вызовы `rsync`, `scp`, `ssh`, `docker`, `systemctl`, `ufw`. На Python это будет `subprocess.run(["rsync", ...])` — это **Bash, написанный на Python**, только с бо́льшим boilerplate. Git-history, AGENTS.md, all TRAP-annotations, all team knowledge — закодированы в shell-идиомах. Переписывание теряет 90% institutional knowledge.

**Дополнительные аргументы:**
- Zero-dependency deployment (bash есть везде, Python требует venv management)
- Faster iteration (no compile, no import, edit-and-run)
- 88% adoption `set -euo pipefail` уже делает bash достаточно безопасным
- Топ-3 скрипта — сложные, но **не buggy** (тесты `test_bootstrap_auto`, `test_deploy_modules` покрывают)
- Triple Delivery Model построена вокруг shell-инструментов (rsync/ssh/tar) — миграция не упрощает

**Сильнейшее опровержение:** «Domain-specific» — миф. Платформа уже содержит **~270 строк embedded-Python в heredoc** (deploy-modules.sh:628-970, converge.sh:790-950). Это означает, что **bash перестал справляться** и разработчики уже пишут на Python — но не тестируемом, не типизированном, не импортируемом. Argument от «знание закодировано в shell» — это sunk-cost fallacy: знание закодировано в *логике*, а не в синтаксисе.

### Case for B: «Мигрировать на Python» (steelman)

**Сильнейший аргумент:** Цикломатическая сложность top-3 скриптов (4197 строк, 480 if-операторов) делает их **недоступными для статического анализа**. shellcheck не может проанализировать embedded-Python. Тестирование требует subprocess (медленно, хрупко). Property-based тестирование — невозможно. На Python: pytest-native, hypothesis, mypy, ruff (уже в pre-commit). Документирование через docstrings + Doxygen (skill `doxygen-python` уже загружен в проект). Цена изменения одного правила в deploy-modules.sh сейчас = полдня тестирования.

**Дополнительные аргументы:**
- 80+ тестов уже на Python — expertise есть в команде
- Production Python (template_engine.py 716 строк, discover_modules.py 120) работает стабильно
- Module-contract валидация, jsonschema, pyyaml — всё в production deps
- Strangler-Fig (Option B из §4.1) минимизирует риски

**Сильнейшее опровержение:** «Pytest-native» — иллюзия. Большинство тестов — `subprocess.run(["bash", "deploy-modules.sh"])` (Subagent B: 53 теста с `requires_docker`, 290 subprocess-вызовов в 66 файлах). Переписывание на Python сделает эти тесты *настоящими* unit-тестами, но только после того, как **сама логика** будет переписана — это chicken-and-egg. Плюс: paramiko для SSH существенно медленнее и хрупче native `ssh` (нет поддержки SSH-agent прозрачно, нет ControlMaster). Python не заменит shell для system-orchestration — он заменит его только для *business logic*, а бизнес-логики в платформе немного.

### Case for C: «Hybrid (новое на Python, существующее на Bash)» (steelman)

**Сильнейший аргумент:** Принцип «Small Simple Blocks» (principle 6) и «AI-First Architecture» (principle 8) не предписывают язык — они предписывают **границы ответственности**. Текущая архитектура *уже* hybrid: shell для orchestration (rsync/ssh/docker), Python для logic (template_engine, discover_modules, _topo_sort). Нужно сделать эту границу **явной**: new business logic → Python, new orchestration glue → shell thin wrappers. Это требует 0 миграции, 0 риска, и постепенно сдвигает баланс.

**Дополнительные аргументы:**
- Совместимо со Strangler-Fig (Option B из §4.1)
- Сохраняет git-history
- Не требует единомоментных решений
- соответствует AGENTS.md invariant «Makefile — единый фасад» (entrypoints остаются shell, internals могут быть Python)

**Сильнейшее опровержение:** «Гибрид» — это не решение, а **откладывание решения**. Через 2 года будет 50% Python + 50% shell, и **граница** между ними будет размыта (кто-то напишет embedded-shell-in-Python через subprocess — и мы вернёмся к началу). Плюс: «отсутствие необходимости мигрировать» = отсутствие принуждения к дисциплине → тренд ухудшения сохранится.

### Вердикт

**Применяется COLLAPSE signal: BOUNDARY COLLAPSE** (S7 ∩ S8 ∩ S13):
- S7 (Boundary): заявленная граница «shell for orchestration» — fractured (embedded-Python её нарушает)
- S8 (Coupling): structural coupling shell↔Python внутри одного файла (deploy-modules.sh:628-970)
- S13 (Dependency): CONVENTION-type hidden dependency (bash-анализатор не видит Python-логику)

**Коллапс → Решение:** Гибрид (C) — но **не как откладывание**, а как **.Strangler-Fig discipline**. Конкретно:

1. **Q1-Q2 2026:** Извлечь embedded-Python блоки (deploy-modules.sh:628-970 → `core/internal/scripts/parse_modules.py`, converge.sh:790-950 → `core/internal/scripts/reconcile_logic.py`). Это чистый выигрыш: −270 строк из shell, +2 тестируемых Python-модуля.
2. **Q3-Q4 2026:** Новые подсистемы — только Python (scaffold generation, D5-validator, audit-aggregator).
3. **Q1-Q2 2027:** Решение по top-3 скриптам на основе опыта Q1-Q4 2026. Если embedded-Python-extraction успешен — продолжить Strangler. Если команда сочтёт shell-нужным для orchestration — зафиксировать явный контракт «orchestration = shell forever».

**Принципиальное правило (внести в AGENTS.md):** «Никакого embedded-Python в bash-heredoc. Если нужна Python-логика — отдельный `.py` файл, вызываемый через `python3 script.py`.»

---

## 8. Prioritized Roadmap

| Фаза | Что | Усилия | Выигрыш | Зависимости |
|------|-----|--------|---------|-------------|
| **Q1 2026 (Foundation)** | E1 (lib/ssh.sh) + E4 (`setup-platform` composite) + E5 (`honesty.py`) + E7 (audit_log wrapper) + P02 (SSH timeouts) + P11 (audit-trail) | ~3 недели | Закрывает P01, P02, P10 (частично), P11, P15. ROI: CI hangs устранены, false-green → real-fail | Нет |
| **Q1-Q2 2026 (Honesty Wave)** | E2 (`gate_helpers.py`) + R4-fix (16-18 тестов) + R5-fix (3 `_negative` пары) + E3 (lib/args.sh) | ~4 недели | Закрывает P01 (полностью), P04, P10 (полностью). −400 строк бойлерплейта | Q1 Foundation |
| **Q2-Q3 2026 (Contract Strengthening)** | E6 (`D5-validator`) + P06 (AGE_SECRET_KEY в .env.example) + P07 (`${VAR:?}` для критичных vars) + P08 (`restart: no` в test compose) | ~5 недель | Закрывает P06, P07, P08. Module contract D4 → D5 | Q1-Q2 Honesty |
| **Q3-Q4 2026 (Strangler Wave 1)** | P12 (извлечение embedded-Python из deploy-modules.sh + converge.sh) + P03 (декомпозиция deploy-modules.sh по 5 ответственностям) + Option A (Makefile include-split) | ~8 недель | Закрывает P03 (частично), P09, P12. −270 строк embedded-Python → тестируемые модули | Q2-Q3 Contract |
| **Q1-Q2 2027 (Strangler Wave 2)** | P05 (transactional deploy_docker_group) + P13 (converge → K8s-parity 7/10) + Q3-Q4 2026 evaluation по top-3 скриптам | ~10 недель | Закрывает P05, P13. Decision point для дальнейшей миграции | Q3-Q4 Strangler 1 |

**Итого:** 30 недель работы = ~7 месяцев (с учётом параллельности волн Q1/Q2). Все 15 проблем закрыты или значительно смягчены.

---

## 9. Risk Register

| ID | Риск Proposed Change | Likelihood | Impact | Mitigation |
|----|---------------------|------------|--------|------------|
| **R-RISK-1** | E1 (lib/ssh.sh) ломает существующие SSH-вызовы в remote-CMD | M | H | Ввести в _test_ssh.sh unit-test, прогнать на staging-ноде перед merge |
| **R-RISK-2** | R4-fix (skip → fail) временно ломает CI на staging (отсутствие Docker) | H | M | Поэтапно: сначала `pytest.mark.requires_docker` → потом `xfail(strict=False)` → потом `fail` |
| **R-RISK-3** | D5-validator (E6) находит 10+ новых нарушений в существующих module.yaml | H | L (это хорошо!) | Зафиксировать как technical-debt-tracking, не блокирующий merge валидатора |
| **R-RISK-4** | Makefile include-split ломает tab-sensitive parsing | M | H | CI gate на `make -n <target>` для каждого `.PHONY` до/после split |
| **R-RISK-5** | Strangler-Wave 1 (извлечение embedded-Python) ломает deploy-modules.sh runtime | M | H | Subagent D установил, что test_bootstrap_auto + test_deploy_modules покрывают main path — добавить regression-тесты для edge-cases (parallel deploy, orphan-reconciliation) до extraction |
| **R-RISK-6** | transactional deploy_docker_group (P05) замедляет cold-start | L | M | Сохранить parallel-within-group, добавить только atomic-rollback на failure |
| **R-RISK-7** | converge K8s-parity (P13) слишком сложен для bare-metal без реальной потребности | M | M | Останавливаться на 7/10 — не пытаться в continuous-watch (это systemd-timer territory) |
| **R-RISK-8** | Audit-wrapper (E7) добавляет overhead на каждый entrypoint | L | L | Использовать async-write в `/var/log/platform/audit.log` через `>>` (append-only, не блокирующий) |

---

## 10. Appendix: Raw Data from Subagents

### A.1 Subagent A — Duplication & Coupling (key tables)

**Entrypoint→internal matrix (excerpt):** `bootstrap.sh` → `node-lifecycle.sh` (центральный хаб, 15 зависимостей). `scaffold.sh` → 7 internal-скриптов. `deploy.sh` → `deploy-project.sh` + `verify-domains.sh`.

**Source-imports usage:**

| lib-файл | Использований |
|---|---|
| logging.sh | 39 |
| paths.sh | 37 |
| healthcheck.sh | 26 |
| node-resolver.sh | 10 |
| audit_logging.sh | 7 |
| module-interface.sh | 2 (но `invoke_module_interface` = 8 callers) |

**Function clones (top):**

| Имя | Определений | Файлы |
|---|---|---|
| `usage()` | 14 | template-engine, gen-env-platform, project-list, add-project, adopt-project, add-vhost, remove-project, converge (×2), bootstrap, node-update, s3-ssl-cache, pg-archive-cleanup |
| `parse_args` | 8 | deploy-project, pg-archive-cleanup, install-tor-proxy, project-list, add-vhost, add-project, adopt-project, remove-project |
| `log()` | 5 | backup-cron, hermes-agent/{context,watchdog,build/init,build/register-profiles} |

**Complexity scores:**

| скрипт | строк | if | for/while | case | max глубина |
|---|---|---|---|---|---|
| deploy-modules.sh | 1633 | 119 | 41 | 4 | 5-6 (bash, не Python) |
| node-lifecycle.sh | 1297 | 106 | 6 | 2 | 7 |
| converge.sh | 1149 | 86 | 8 | 1 | 4 (bash) |
| adopt-project.sh | 940 | 68 | 3 | 3 | **9** |

**Dead code:** 2 функции из 330 (0.6%): `yellow()` (lint.sh:26), `is_ignored_path()` (adopt-project.sh:166).

### A.2 Subagent B — Test Infrastructure (key tables)

**Coverage heatmap:**

| Маркер | Кол-во | Покрытие слоёв |
|---|---|---|
| `gate` | 192 | CI-gates cross-cutting |
| `static_audit` | 169 | YAML/schema validation |
| `contract` | 97 | entrypoints, deploy delivery |
| `smoke` | 53 | Docker-стек |
| `predeploy` | 42 | container/network validation |
| `component` | 20 | hermes, pgbouncer, clickhouse |
| `e2e` | 9 | *.tronyx.ru external |
| `integration` | **2** ⚠️ | hermes LLM (критически мало) |
| `backup` | **0** ⚠️ | маркер объявлен, нет тестов (zombie) |

**R4 violations (top):**

| Файл | Skip-причина |
|---|---|
| `_conftest/smoke.py:637` | Docker daemon not available |
| `test_component_hermes.py:111,236` | docker-compose.base.yml not found |
| `test_component_pgbouncer.py:160,184,651` | Docker daemon not available |
| `test_contract_entrypoints.py:198,228,274,340` | Script not found (контракт!) |
| `test_tls_wildcard.py:819` | acme.sh not found (bootstrap artifact) |
| `gates/test_gate_ci_env_vars.py:68` | platform-env.yaml not available (gate!) |

**Subprocess antipattern:** 290 вызовов в 66 файлах. ~40-50 нарушают §TESTING (Python-business-logic через subprocess вместо import).

### A.3 Subagent C — Module Architecture (key tables)

**D4 contract compliance (RED/AMBER):**

| Модуль | Проблема |
|---|---|
| platform-secrets | `AGE_SECRET_KEY` отсутствует в `.env.example` 🔴 |
| postgres | restart drift: module.yaml=always, compose=unless-stopped 🔴 |
| minio | `interfaces: []`, но healthcheck.sh есть (semantically incomplete) 🟡 |
| hermes-agent | `env_shared` (HTTP_PROXY/NO_PROXY) не в `env_requires` 🟡 |

**Dependency graph:**
- Глубина: 2
- Циклы: нет
- Root nodes: 7 (clickhouse, logging, minio, nginx, postgres, redis, platform-secrets)
- Leaf nodes: 9
- **Suspicious:** minio — orphan (backup-cron/langfuse используют MinIO через S3_ENDPOINT, но не декларируют depends_on)

**Test compose violations:** 13/13 модулей нарушают `restart: no` контракт (наследуют `unless-stopped`).

**Hermes L1/L2 duplication:** HEALTHCHECK полностью дублирован (15s/10s/3/20s). `_read_s6_env()` функция копипастится в build/scripts/init.sh + context/scripts/init-context.sh.

### A.4 Subagent D — CI/CD & Deploy Pipeline (key tables)

**CI duplication (top):**

| Шаг | Workflows |
|---|---|
| `actions/checkout@v7` | 10 вхождений в 8 файлах |
| `provisioner-call` | 4 |
| `setup-python-venv`/`setup-gitleaks`/`docker-build-cache`/`sha-resolve` | 2-3 each |

**Workflow staleness:** все 9 workflows модифицировались <30 дней. ✅

**Bootstrap rollback gaps:**

| Шаг | Rollback |
|---|---|
| SCP Phase 1-4 | ❌ (только checkpoint-resume) |
| step_5/6 create users | ❌ (полагается на useradd idempotency) |
| step_13 sudoers | ❌ |
| deploy_docker_group parallel | ❌ (severity-aggregate exit, no rollback) |
| issue-cert.sh acme.sh | ⚠️ (acme native retry, no nginx-reload coord) |
| **deploy-project.sh (atomic_up)** | ✅ ЕДИНСТВЕННЫЙ с rollback |

**Audit trail coverage:**

| Операция | audit_log |
|---|---|
| bootstrap/init (step_16) | ✅ |
| deploy-project (8 точек) | ✅ |
| converge (reconcile_audit_log) | ✅ |
| context-promote, remove-project, provision, hermes-build, secrets-unlock, node-update --mode update, core-deploy rsync | ❌ (7 gap) |

**converge K8s-parity score:** 4/10. Single-shot, detect-only для R5/R6, no continuous-loop, no runtime-state reconciliation.

**Triple Delivery integrity:** ✅ Core-канал соблюдает NO-git (git только для context-overlay и external upstream deps). ⚠️ Дублирующая логика acme.sh clone в install-acme.sh + nginx/install.sh — drift-point.

---

## Заключение

ai-platform v1.0.0 — крепкая инженерная работа с продуманными инвариантами (Triple Delivery, идемпотентность, 10 правил AGENTS.md), но на пороге архитектурной бифуркации. Три системных напряжения (Bash-scalability, test-trust, reconciliation-gap) не критичны по отдельности, но в сумме создают риск **постепенной энтропии**.

**Главный recommend:** выполнить Q1 Foundation wave (3 недели) — это закроет 5 из 15 проблем с минимальным риском и даст команде опыт для последующих волн. Каждая последующая волна — это **эволюция, а не революция** (Strangler-Fig discipline).

**Готов к делегированию волн в dev-pipeline skill** по мере готовности команды.

---

*Конец Architecture Analysis Report. Артефакт создан по протоколу `arch-forensics` + `superposition`. Все находки основаны на evidence из отчётов субагентов A/B/C/D. Не содержит кода, не модифицирует проект.*
