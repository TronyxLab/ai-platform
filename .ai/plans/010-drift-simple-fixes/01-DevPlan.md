# $START_DEVPLAN
# 01-DevPlan.md — Drift Audit: Simple Mechanical Fixes (Wave A)

<!-- GREP_SUMMARY: devplan, drift-fixes, ci-workflows, markup-compliance, ldd-logs, compose-cleanup, mechanical -->
<!-- STRUCTURE: ┌VerificationReport findings┐ → ◇ filter: mechanical/no-arch/no-questions → ⊕ 7 tasks → ∑ 3 parallel groups → ⎋ gate MODE=fast green -->

## $ARTIFACT_CONTRACT
| Field | Value |
|-------|-------|
| PURPOSE | Устранить все механические, не-архитектурные находки из 001-full-drift-audit/01-VerificationReport.md, не требующие проектных решений или уточнений |
| DESCRIPTION | 7 атомарных задач: фиксы CI-workflow (event name, unnamed steps, версии actions, inline docker), очистка compose (orphan volume, clickhouse bind), документация CI-секретов, markup-комплаенс (LDD-логи в shell, @purpose в Python, STRUCTURE в Makefile, @ldd_trajectory в тестах) |
| RATIONALE | Q: почему отдельный план? A: эти находки детерминированы — fix известен построчно из отчёта, ревью решений не требуется; выполняются параллельно без блокировки на решениях плана 011 |
| ACCEPTANCE_CRITERIA | `make gate MODE=fast` зелёный; `python -m pytest tests/ -s -v` — 100% PASS; повторный grep-аудит по каждой категории показывает 0 нарушений из списка ниже |
| IMPLEMENTS | .ai/plans/001-full-drift-audit/01-VerificationReport.md — Top-10 items #2,3,4,6,7,8,9 + Section 1 findings (LDD, Doxygen, STRUCTURE) + Section 4 (6 test files) |
| IMPACTS | 5 CI workflows, docker-compose.yml, clickhouse/test.yml, .env.example, 37 shell-скриптов, 4 Python-файла, 11 Makefile, 6 тест-файлов (~65 файлов, все изменения — markup/config, ноль изменений бизнес-логики) |
| REQUIRES | Coder-делегирование; ноль решений от Architect/пользователя |

---

## Requirements Analysis

Источник: `.ai/plans/001-full-drift-audit/01-VerificationReport.md` (Health Score 45/100).
В этот план включены ТОЛЬКО находки, удовлетворяющие всем трём критериям:
1. Fix полностью определён в отчёте (file:line + конкретная замена);
2. Не затрагивает архитектуру, контракты, схемы, семантику таргетов;
3. Не требует уточнений у пользователя.

**Ключевые критерии успеха:**
1. CI workflows: 0 unnamed steps, корректный `cancel-in-progress` event, единые версии actions.
2. Markup: 100% shell-скриптов с `[IMP:` логами, 100% Python-функций с `## @purpose`, 100% Makefile со `# STRUCTURE`.
3. Compose: 0 orphan volumes, 0 расхождений base→test override (clickhouse).
4. `.env.example` документирует все 12 GitHub Actions секретов (комментарием, не значениями).
5. Регрессий нет: полный тестовый прогон 100% PASS.

**Явное НЕ-включение (ушло в план 011-drift-arch-decisions):** Invariant 1/4, platform-secrets contract, restart/up/backup semantics, phantom/dead env vars, тесты для 4 инвариантов, TRAP[TEST]/TRAP[DECISION] стратегия, hardcoded module list, stale tests.

## @rationale (размер задачи)
Q: файлов >20 — почему не LARGE-протокол с Brief + CONFIRM_BRIEF?
A: пользователь явно заказал готовые DevPlan'ы без итераций («создай 2 девплана … и заверши работу») — CONFIRM_BRIEF пройден самим запросом. Изменения не затрагивают архитектуру/схемы/контракты — по типу это STANDARD-markup-работа, масштабированная по числу файлов.

---

## Architecture Overview

Новых модулей/функций нет. Draft Code Graph — только точки правок:

```
▶ .github/workflows/{platform-test,deploy-project,platform-deploy,build-platform}.yml  ← T1 (event, names, versions, make-target)
▶ docker-compose.yml + core/modules/clickhouse/docker-compose.test.yml                 ← T2 (volume, mount)
▶ .env.example                                                                          ← T3 (CI secrets doc block)
▶ core/modules/backup-cron/scripts/{backup_config,date_parser,retention,s3_client}.py  ← T4 (@purpose/@io/@complexity)
▶ core/modules/*/Makefile (11 шт.)                                                      ← T5 (# STRUCTURE)
▶ core/{entrypoints,internal,lib}/*.sh + modules/*/healthcheck.sh (37 шт.)             ← T6 ([IMP:] логи)
▶ tests/{gate,unit,...}/ 6 файлов                                                       ← T7 ([IMP:] + @ldd_trajectory)
```

## Data Flow
1. Coder читает этот DevPlan → берёт свою волну.
2. Для каждой задачи: read файла → точечный edit по спецификации ниже → verification grep (Fail-Fast batch-level).
3. После волны: `make gate MODE=fast` → `ruff format . && ruff check --fix .` (для T4/T7) → `python -m pytest tests/ -s -v`.

---

## $TASKS

### T1 — CI workflow fixes · complexity 3 · deps: none · files: 4
Владелец: Coder. Артефакт: исправленные workflows.
- `platform-test.yml:60` — `cancel-in-progress`: событие `pull_request` → `pull_request_target` (соответствие триггеру).
- `platform-test.yml:153-155` — удалить избыточный step-level override `HERMES_DASHBOARD_PASSWORD`.
- `deploy-project.yml:35` — добавить `name: Checkout repository`, bump `actions/checkout@v4` → `@v7`.
- `deploy-project.yml:75` — `appleboy/ssh-action@v1.0.3` → `@v1.2.5` (выравнивание с platform-deploy.yml:108).
- `platform-deploy.yml:158` — добавить `name: Checkout repository`.
- `platform-deploy.yml` — выровнять `timeout-minutes`: добавить его джобам, где отсутствует (значение по образцу job `e2e-smoke`, L155-163; не менять существующие).
- `build-platform.yml:130-134` — заменить inline `docker tag` + `docker push` на `make hermes-push-l1` (канонический глагол из глоссария; таргет существует).
- AC: `grep -n 'uses:' .github/workflows/*.yml` — все шаги именованы; checkout только `@v7`; ssh-action только `@v1.2.5`; `grep 'docker push' .github/workflows/build-platform.yml` → 0 вне make.

### T2 — Compose cleanup · complexity 2 · deps: none · files: 2 · @keep_separate
(≤2 файлов/≤20 строк, но родителя нет — standalone по merge-rule.)
- `docker-compose.yml:43` — удалить объявление orphan-volume `redis-data:` (redis теперь cache-only, ни один сервис не монтирует).
- `core/modules/clickhouse/docker-compose.test.yml:42` — заменить directory-bind `users.d/` на per-file ro-mount, зеркалирующий `docker-compose.base.yml`.
- AC: `docker compose config` валиден; diff test.yml vs base.yml по volumes — только осознанные override; `grep redis-data docker-compose.yml` → 0.

### T3 — CI secrets documentation · complexity 2 · deps: none · files: 1
- `.env.example` — добавить закомментированный блок `# --- GitHub Actions secrets (NOT .env vars — create in repo Settings → Secrets) ---` с перечислением 12 имён: VPS_HOST, VPS_SSH_KEY, CI_DEPLOY_KEY, GHCR_TOKEN, DOCKER_HUB_USERNAME, DOCKER_HUB_TOKEN, SSH_HOST, SSH_KEY, E2E_BASE_URL, E2E_GRAFANA_URL, GIT_MIRROR_TOKEN, NODE_HOST_MAP — каждое с однострочным описанием и workflow-потребителем (из Section 6 отчёта).
- ЗАПРЕЩЕНО: значения секретов (только имена + описания). §CONSTITUTION-2.
- AC: все 12 имён присутствуют в `.env.example`; значений нет.

### T4 — Doxygen @purpose для backup-cron · complexity 3 · deps: none · files: 4
- `core/modules/backup-cron/scripts/{backup_config,date_parser,retention,s3_client}.py` — каждой функции добавить `## @purpose`, `## @io`, `## @complexity` (skill `doxygen-python`). Мини-блок-диаграмма первой строкой docstring для нетривиальных функций.
- AC: `grep -L '@purpose' core/modules/backup-cron/scripts/*.py` → пусто; `python -m pytest tests/ -s -v -k backup` — PASS.

### T5 — STRUCTURE в 11 module Makefiles · complexity 2 · deps: none · files: 11
- Все `core/modules/*/Makefile`, кроме minio, nginx, platform-secrets — добавить строку вида `# STRUCTURE: ┌module targets┐ → ◇ module.mk include → ⊕ compose lifecycle` (адаптировать под специфику модуля, если она есть).
- AC: `grep -L '# STRUCTURE' core/modules/*/Makefile` → пусто.

### T6 — LDD [IMP:] логи в 37 shell-скриптах · complexity 5 · deps: none · files: 37
Список — Section 1 отчёта: `core/entrypoints/*.sh` (7), `core/internal/*.sh` (8), `core/lib/*.sh` (3: healthcheck.sh, paths.sh, yaml_read.sh), module healthcheck-скрипты (12) + остальные из grep до полного списка 37.
- Паттерн: `echo "[IMP:7][<script>][main] start ..."` на входе, `[IMP:9]` на бизнес-критичных ветках (deploy/secrets/validate), `[IMP:7]` на выходе. Для `core/lib/*.sh` (библиотеки) — логи в функциях, не в top-level (не ломать sourcing: stdout-чистота для yaml_read.sh — логи только в stderr `>&2`).
- ⚠️ Осторожно: скрипты, чей stdout парсится (yaml_read.sh, paths.sh) — ВСЕ логи строго в stderr. Проверить вызовы через `grep -rn 'yaml_read\|paths.sh' core/ Makefile*`.
- AC: `grep -rL '\[IMP:' core/entrypoints/ core/internal/ core/lib/ core/modules/*/healthcheck.sh` → пусто; `make healthcheck` и `make gate MODE=fast` — без регрессий.

### T7 — LDD + @ldd_trajectory в 6 тест-файлах · complexity 3 · deps: none · files: 6
- `test_gate_container_name_consistency`, `test_gate_module_schema_d4`, `test_gate_platform_env_schema`, `test_restart_consistency`, `test_smoke_test_isolation`, `unit/test_discover_modules` — добавить `[IMP:` логи в тестируемый поток и декоратор `@ldd_trajectory` (центральный, из `tests/_conftest/ldd.py`).
- AC: `python -m pytest tests/ -s -v` для этих 6 файлов — PASS с видимой LDD-траекторией IMP:9.

**Критический путь:** отсутствует — все 7 задач независимы. Самая тяжёлая — T6 (37 файлов).

---

## $PARALLEL_GROUPS

### Wave 1 (все задачи независимы, файлы не пересекаются)
- Sub-group A (CI/config): T1, T2, T3
- Sub-group B (Python markup): T4, T7
- Sub-group C (Shell/Make markup): T5, T6
- Command A: `coder Read .ai/plans/010-drift-simple-fixes/01-DevPlan.md, implement T1, T2, T3`
- Command B: `coder Read .ai/plans/010-drift-simple-fixes/01-DevPlan.md, implement T4, T7`
- Command C: `coder Read .ai/plans/010-drift-simple-fixes/01-DevPlan.md, implement T5, T6`

---

## Acceptance Criteria (summary)

| # | Критерий | Проверка |
|---|----------|----------|
| 1 | CI workflows: named steps, единые версии, верный event | grep по workflows (T1-AC) |
| 2 | 0 orphan volumes, clickhouse override зеркалирует base | `docker compose config` + diff |
| 3 | 12 CI-секретов задокументированы, значений нет | grep `.env.example` |
| 4 | 100% @purpose в backup-cron scripts | `grep -L '@purpose'` → пусто |
| 5 | 100% STRUCTURE в module Makefiles | `grep -L '# STRUCTURE'` → пусто |
| 6 | 100% `[IMP:` в целевых shell-скриптах, stdout-чистота библиотек | `grep -rL '\[IMP:'` → пусто + healthcheck green |
| 7 | 6 тест-файлов с LDD+trajectory | pytest PASS с IMP:9 траекторией |
| 8 | Нет регрессий | `make gate MODE=fast` green; `python -m pytest tests/ -s -v` 100% PASS |

## File Manifest
- `.github/workflows/platform-test.yml`, `deploy-project.yml`, `platform-deploy.yml`, `build-platform.yml`
- `docker-compose.yml`, `core/modules/clickhouse/docker-compose.test.yml`
- `.env.example`
- `core/modules/backup-cron/scripts/backup_config.py`, `date_parser.py`, `retention.py`, `s3_client.py`
- `core/modules/*/Makefile` (11 шт., кроме minio/nginx/platform-secrets)
- 37 shell-скриптов: `core/entrypoints/*.sh`, `core/internal/**/*.sh`, `core/lib/*.sh`, `core/modules/*/healthcheck.sh`
- 6 тест-файлов (перечислены в T7)

## $TEST_SPEC
`$TEST_SPEC: NONE — @rationale:` все изменения — markup/config без новой бизнес-логики; корректность подтверждается существующим gate-набором (`make gate MODE=fast`), полным прогоном `python -m pytest tests/ -s -v` и verification-grep'ами из AC каждой задачи. T7 модифицирует существующие тесты (телеметрия), не меняя их assertions.

## Design Decisions
## @rationale (T1 → make hermes-push-l1)
Q: почему замена inline docker на make-таргет — «простая», а не архитектурная? A: таргет уже существует и зарегистрирован в глоссарии AGENTS.md; замена — приведение к уже принятой архитектуре, а не новое решение.
## @rationale (T6 stderr-инвариант)
Q: почему логи библиотек только в stderr? A: stdout `yaml_read.sh`/`paths.sh` парсится вызывающими скриптами — лог в stdout сломал бы контракт. Fail-Fast: проверить каждым verification-grep вызовов.

## Next Steps
### Wave 1 (три параллельных Coder-сессии)
```
Use coder role and read .ai/plans/010-drift-simple-fixes/01-DevPlan.md, implement T1, T2, T3
Use coder role and read .ai/plans/010-drift-simple-fixes/01-DevPlan.md, implement T4, T7
Use coder role and read .ai/plans/010-drift-simple-fixes/01-DevPlan.md, implement T5, T6
```
### Finalize
```
make gate MODE=fast && ruff format . && ruff check --fix . && python -m pytest tests/ -s -v
```

# $END_DEVPLAN
