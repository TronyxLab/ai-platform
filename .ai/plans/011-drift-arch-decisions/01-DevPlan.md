# $START_DEVPLAN
# $STATUS: ARCHIVED
# 01-DevPlan.md — Drift Audit: Architectural Decisions & Elaboration (Wave B)
# $STATUS: ARCHIVED

<!-- GREP_SUMMARY: devplan, drift-fixes, invariants, platform-secrets, verb-semantics, env-drift, invariant-tests, trap-strategy, decisions-required -->
<!-- STRUCTURE: ┌VerificationReport findings┐ → ◇ filter: needs-decision/elaboration → ⊕ 8 tasks × open questions → ∑ 3 waves (D-решения → реализация → тесты) → ⎋ health score ≥85 -->

## $ARTIFACT_CONTRACT
| Field | Value |
|-------|-------|
| PURPOSE | Закрыть все находки аудита 001-full-drift-audit, требующие архитектурных решений, проработки или уточнений у владельца |
| DESCRIPTION | 8 задач: нарушение Invariant 1 (Makefile-фасад в core-deploy.yml), AT_RISK Invariant 4 (AGENTS.md), контракт platform-secrets для systemd-модулей, конвергенция семантики глаголов restart/up/backup/down, phantom/dead env vars, тестовое покрытие 4 инвариантов, TRAP-стратегия, единый источник module-list |
| RATIONALE | Q: почему отдельный план от 010? A: каждая задача здесь содержит развилку (2+ валидных исхода) либо меняет контракт/инвариант — требуется collapse решений ДО кодирования; смешивание с механическими фиксами заблокировало бы Wave A |
| ACCEPTANCE_CRITERIA | Все Open Questions закрыты (ответ владельца или зафиксированный default); инварианты 1 и 4 в статусе HELD; 4 инварианта покрыты тестами; `make gate MODE=full` зелёный; повторный drift-аудит: 0 HIGH-находок |
| IMPLEMENTS | .ai/plans/001-full-drift-audit/01-VerificationReport.md — Top-10 items #1,5,10 + Section 2 (manifest parity, phantom/dead vars, module list) + Section 3 (Invariant 1, 4) + Section 4 (invariant gaps, TRAP[TEST], stale tests) + оба TRAP[DEBT] |
| IMPACTS | core-deploy.yml, AGENTS.md (root + core/modules/), core/templates/ (новый systemd-шаблон), core/Makefile.common, core/templates/module.mk, entrypoint-manifest.yaml, .env/.env.example/compose, tests/ (4+ новых теста), 2 CI workflows |
| REQUIRES | Решения D1-D6 (question tool → владелец) ДО Wave 2; результаты Wave A (010) не блокируют, но merge-конфликты по workflows возможны — выполнять после 010 |

---

## Requirements Analysis

Источник: `.ai/plans/001-full-drift-audit/01-VerificationReport.md`. Критерий включения: находка требует выбора между валидными альтернативами, меняет инвариант/контракт, или fix не определён однозначно.

**Ключевые критерии успеха:**
1. Инварианты: 14/14 HELD (сейчас 12 HELD, 1 VIOLATED, 1 AT_RISK).
2. Покрытие инвариантов тестами: 14/14 (сейчас 10/14).
3. Глоссарий глаголов: одно имя = одна семантика во всех Makefile (сейчас 4 конфликта).
4. Env-цепочка: 0 phantom, 0 dead vars.
5. Health Score повторного аудита ≥ 85/100.

---

## Open Questions (решения до реализации)

Каждое D-решение collapse'ится через `question` tool в сессии исполняющего Architect'а (Mode 3 GUIDED). Рекомендации даны; при недоступности владельца применяется Recommended как default с фиксацией TRAP[DECISION].

**D1 · Invariant 1 · core-deploy.yml:188 вызывает `provision-environment.sh` напрямую**
- Вариант A (Recommended): заменить на `make provision SCOPE=networks,volumes` — Makefile уже в rsync-манифесте, инвариант восстанавливается буквально. Отчёт подтверждает готовность.
- Вариант B: легализовать прямой вызов для CI-контекста, обновив инвариант. Отвергнуть: размывает фасад, открывает дрейф Makefile↔CI (см. TRAP[DEBT] в отчёте).
- Риск A: на VPS в момент выполнения шага должен быть доступен make + Makefile — проверить порядок шагов rsync→provision в core-deploy.yml.

**D2 · Invariant 4 · 8 файлов AGENTS.md при инварианте «три файла»**
- Вариант A (Recommended): обновить формулировку инварианта — «3 канонических (root, core/, core/modules/) + вспомогательные, перечисленные в навигации root-AGENTS.md»; добавить все 8 в навигацию.
- Вариант B: удалить/слить 5 лишних AGENTS.md до трёх. Требует ревизии их содержимого — дороже, возможна потеря локального контекста (§INVARIANT Local Context говорит в пользу локальных файлов).
- Требуется инвентаризация: `find . -name AGENTS.md` + решение по каждому из 5 недокументированных.

**D3 · platform-secrets · Docker-шаблон module.mk при `install_type: system`**
- Вариант A (Recommended): создать `core/templates/module-system.mk` (таргеты: install, status, restart, logs через systemd; БЕЗ build/up/backup docker-семантики), перевести platform-secrets на него, задокументировать альтернативный контракт в `core/modules/AGENTS.md`. Закрывает и missing-files-находку (base/test compose не нужны systemd-модулю — контракт для system-модулей другой), и 4 dangling-таргета.
- Вариант B: добавить 3 недостающих файла-заглушки под Docker-контракт. Отвергнуть: заглушки = ложь контракта, dangling-таргеты остаются.
- Cascade (Step 1.8): module-system.mk + core/modules/AGENTS.md + platform-secrets/Makefile + entrypoint-manifest.yaml + gate-тест module-contract (научить различать install_type).

**D4 · Семантика глаголов · restart (2 реализации), up/backup (root vs module), down отсутствует**
- Вариант A (Recommended): зафиксировать в глоссарии AGENTS.md двухуровневую семантику: root-глагол = оркестрация всего стека, module-глагол = операция одного модуля; унифицировать `restart` до одной реализации (выбрать soft из Makefile.common; hard-вариант переименовать в `restart-hard`), добавить `down` в module.mk как алиас `stop` для discoverability.
- Вариант B: полное разведение имён (module-up, module-backup). Отвергнуть: ломает привычный вызов `make -C core/modules/X up`, массовый churn.
- Cascade: Makefile.common, module.mk, entrypoint-manifest.yaml, глоссарий AGENTS.md, gate manifest-integrity.

**D5 · Env drift · phantom NGINX_HTTP_PORT/NGINX_HTTPS_PORT; dead HERMES_DASHBOARD_BASIC_AUTH_*, LITELLM_METRICS_TOKEN**
- Phantom (Recommended): добавить в `.env`/`.env.example` с дефолтами 80/443 — переменные реально используются compose'ом (platform-dev:129), цепочка должна быть полной.
- Dead vars — нужно уточнение у владельца: это заготовка будущей фичи (тогда — TRAP[DECISION] `Reason: deferred` + оставить) или мусор (удалить из .env/.env.example)? Без ответа: оставить + TRAP[DECISION] deferred (безопасный default — удаление может сломать невидимый потребитель).

**D6 · Hardcoded 12-module list в platform-test.yml:176 и nightly-gate.yml:109-124**
- Вариант A (Recommended): генерировать список из существующего механизма `make discover-modules` / `discover_modules.py` (single source of truth уже есть — Knowledge-Dedup Step 1.11), workflows читают вывод шага.
- Вариант B: оставить хардкод + gate-тест на синхронность списка с core/modules/*. Дешевле, но копия знания остаётся.

---

## Architecture Overview (Draft Code Graph)

```
▶ D1: .github/workflows/core-deploy.yml:188 ──→ make provision SCOPE=networks,volumes
▶ D2: AGENTS.md §invariant-4 + §Навигация ──→ 8 AGENTS.md inventory
▶ D3: core/templates/module-system.mk (NEW) ←─ core/modules/platform-secrets/Makefile
      └→ core/modules/AGENTS.md §system-module-contract + entrypoint-manifest.yaml
▶ D4: core/Makefile.common §restart ⊕ core/templates/module.mk §restart/down + AGENTS.md глоссарий
▶ D5: .env ⊕ .env.example ⊕ docker-compose.platform-dev.yml (env-цепочка)
▶ D6: platform-test.yml ⊕ nightly-gate.yml ←─ discover_modules.py output
▶ T-inv: tests/gate/test_gate_local_stack.py (NEW, Inv 7)
         tests/gate/test_gate_context_overlay_git.py (NEW, D2/D3-инварианты доставки)
         tests/unit/test_no_backward_compat_markers.py (NEW, Inv 9)
```

## Data Flow
1. Architect-сессия: collapse D1-D6 (question tool, Mode 3 GUIDED) → фиксация в этом DevPlan §Decisions Log.
2. Wave 2: Coder реализует D1, D5, D6 (после collapse — механические).
3. Wave 3: Coder реализует D3, D4 (контрактные изменения + manifest + gate-тесты) и D2 (документация).
4. Wave 4: Coder пишет тесты 4 инвариантов ($TEST_SPEC) + TRAP-волна.
5. QA: `make gate MODE=full` + повторный drift-скан → VerificationReport.

---

## $TASKS

### T1 — Invariant 1: core-deploy.yml → make provision · complexity 4 · deps: D1 · files: 2-3
- **[D1=A1]** Расширить таргет `provision` в root Makefile: `SCOPE=a,b` → повторяемые `--scope a --scope b` (сейчас передаётся одно значение, Makefile:60). Заменить прямой вызов `provision-environment.sh` (core-deploy.yml:188) на `make provision SCOPE=networks,volumes`; порядок шагов корректен (Makefile rsync'ится в Step 5c:150 ДО provision-шага). Закрыть TRAP[DECISION] core-deploy.yml:164 (Rev выполнен) → TRAP[ARCHIVED]; снять TRAP[DEBT] №1 из отчёта.
- AC: `grep 'provision-environment.sh' .github/workflows/` → только через make; `make provision SCOPE=networks,volumes` локально прогоняет оба scope; инвариант 1 = HELD.

### T2 — Invariant 4: AGENTS.md инвентаризация и навигация · complexity 2 · deps: D2 · files: 1-2
- **[D2=A′]** Инвентаризация закрыта (8 файлов). Обновить формулировку инварианта 4 в root AGENTS.md: «3 канонических + вспомогательные, перечисленные в навигации; templates/template-*/AGENTS.md — payload шаблонов, вне скоупа инварианта». Добавить в §Навигация: core/internal/bootstrap/AGENTS.md, tests/gates/AGENTS.md.
- AC: инвариант 4 = HELD; каждый не-template AGENTS.md либо каноничен, либо в навигации; скоуп-исключение зафиксировано в формулировке.

### T3 — platform-secrets: system-module контракт · complexity 7 · deps: D3, D3b · files: 6-7
- **[D3=A]** Создать `core/templates/module-system.mk`; перевести `platform-secrets/Makefile`; документировать `install_type: system` контракт в `core/modules/AGENTS.md`; зарегистрировать таргеты в `entrypoint-manifest.yaml`; адаптировать gate-тест module-contract под два типа модулей. Снять TRAP[DEBT] №2 из отчёта.
- **[D3b=включить]** Добавить `RequiredBy=docker.service` в `platform-secrets.service` (или drop-in для docker.service) — enforce задекларированного «fails closed»; закрыть TRAP[DEBT] в module.yaml → TRAP[BUG]; проверка поведения в predeploy-контуре.
- AC: `make -C core/modules/platform-secrets <target>` — нет dangling docker-таргетов; gate module-contract PASS для обоих типов; `systemctl show docker -p Requires` содержит platform-secrets (predeploy).

### T4 — Конвергенция глаголов restart/up/backup/down · complexity 5 · deps: D4 · files: 4-5
- Единая реализация `restart`; документировать двухуровневую семантику up/backup в глоссарии; добавить `down` в module.mk; синхронизировать entrypoint-manifest.yaml.
- AC: `grep -rn '^restart:' core/` → одна семантика; глоссарий обновлён; manifest-integrity gate PASS.

### T5 — Env-цепочка: phantom + D5a/D5b · complexity 5 · deps: D5 · files: 6-7
- Phantom: NGINX_HTTP_PORT/NGINX_HTTPS_PORT → добавить в .env + .env.example (дефолты 80/443; реальный потребитель — `core/modules/nginx/docker-compose.base.yml`, не platform-dev).
- **[D5a=удалить]** HERMES_DASHBOARD_BASIC_AUTH_* удалить из .env/.env.example; переписать stale-комментарий .env.example:130 на фактическую цепочку (`HERMES_DASHBOARD_USERNAME/PASSWORD` → compose → контейнерные BASIC_AUTH_*-имена). Перед удалением — grep secrets.env-цепочки (platform-secrets) на эти имена.
- **[D5b=чинить, переквалифицирован в БАГ]** LITELLM_METRICS_TOKEN НЕ удалять: prometheus.yml:58 ссылается на него, но подстановка не работает (mount `:ro`, envsubst нет). Реализовать генерацию prometheus.yml через envsubst (init-контейнер или entrypoint monitoring); TRAP[BUG] у fix-точки; gate-тест: в итоговом конфиге нет литерала `${LITELLM_METRICS_TOKEN}`.
- AC: env-drift скан (.env ↔ .env.example ↔ compose ↔ CI ↔ config-потребители) → 0 phantom, 0 dead; scrape LiteLLM-метрик с реальным токеном (predeploy).

### T6 — Единый источник module-list в CI · complexity 4 · deps: D6 · files: 2-3
- **[D6=A]** Шаг генерации списка из `discover_modules.py` в platform-test.yml (pull-список) и nightly-gate.yml (cleanup-список); оба списка читают вывод шага.
- AC: изменение состава core/modules/* не требует ручной правки workflows.

### T7 — Тесты 4 непокрытых инвариантов · complexity 7 · deps: T3, T4 · files: 3-4 (NEW)
- См. $TEST_SPEC. Инв. 7 (полный локальный стек), Инв. 9 (no backward-compat), D2/D3 доставки (context-overlay git exclusivity).
- Проработка: Инв. 7 тестировать статически (compose config: 12 модулей, 6 сетей, 11 volumes резолвятся) — НЕ запуском стека в юнит-контуре; полный запуск остаётся в e2e/predeploy-маркере.
- AC: 4 инварианта в матрице покрытия = ✅; тесты с @ldd_trajectory и IMP:9.

### T8 — TRAP-стратегия и stale-tests триаж · complexity 5 · deps: none · files: ~20 (первая волна)
- TRAP[DECISION] в 13 module Makefiles + 2 template Python (требует изучения git-истории/rationale каждого — потому здесь, не в 010). TRAP[TEST] — НЕ во все 101 файла: только волна приоритетных (gate-тесты + тесты инвариантов, ~15-20 файлов); остальное — фоновый долг с фиксацией в {NN}-Debt.md. Stale-триаж: пересмотреть smoke-тесты рефакторенных модулей, список кандидатов на актуализацию → Debt.md.
- AC: 100% gate-тестов с TRAP[TEST]; 13 Makefiles с ≥1 TRAP; Debt.md с реестром остатка.

**Критический путь:** D3-collapse → T3 → T7 (контракт system-модулей блокирует gate-тест и тест инвариантов доставки).

---

## $PARALLEL_GROUPS

### Wave 1 (решения — Architect, question tool) — ✅ ВЫПОЛНЕНО 2026-07-18
- Collapse D1-D6 + D3b (обнаружен при верификации) → §Decisions Log заполнен. Все рекомендации приняты владельцем.

### Wave 2 (после D-collapse; независимы, файлы не пересекаются)
- Tasks: T1, T5, T6, T8
- Command: `coder Read .ai/plans/011-drift-arch-decisions/01-DevPlan.md §Decisions Log, implement Wave 2: T1, T5, T6, T8`

### Wave 3 (контракты; T3 и T4 пересекаются по entrypoint-manifest.yaml — последовательно)
- Tasks: T3 → T4, параллельно T2
- Command: `coder Read .ai/plans/011-drift-arch-decisions/01-DevPlan.md, implement Wave 3: T3 then T4; parallel T2`

### Wave 4 (тесты)
- Tasks: T7
- Command: `coder Read .ai/plans/011-drift-arch-decisions/01-DevPlan.md, implement Wave 4: T7`

### Wave 5 (верификация)
- `qa Re-run drift audit scope from .ai/plans/001-full-drift-audit, verify invariants 1,4 HELD and coverage 14/14, write VerificationReport`

---

## Acceptance Criteria (summary)

| # | Критерий | Проверка |
|---|----------|----------|
| 1 | Инварианты 14/14 HELD | повторный Phase-3 скан |
| 2 | Покрытие инвариантов тестами 14/14 | матрица Section 4 |
| 3 | Глоссарий: 1 имя = 1 семантика | grep restart/up/backup/down + manifest gate |
| 4 | 0 phantom / 0 dead env vars | env-drift скан |
| 5 | platform-secrets без dangling-таргетов | make -C … + gate |
| 6 | Все D1-D6 закрыты записью в Decisions Log | этот файл |
| 7 | `make gate MODE=full` зелёный | CI |
| 8 | Health Score повторного аудита ≥ 85 | Wave 5 VerificationReport |

## File Manifest
- `Makefile` (root — provision multi-SCOPE, D1)
- `.github/workflows/core-deploy.yml`, `platform-test.yml`, `nightly-gate.yml`
- `AGENTS.md` (root), `core/AGENTS.md`, `core/modules/AGENTS.md`
- `core/templates/module-system.mk` (NEW), `core/templates/module.mk`, `core/Makefile.common`
- `core/modules/platform-secrets/Makefile`, `platform-secrets.service` (D3b), `module.yaml`, `core/entrypoint-manifest.yaml`
- `.env`, `.env.example`, `core/modules/monitoring/docker-compose.base.yml` (+ envsubst-механизм, D5b)
- `tests/gate/test_gate_local_stack.py` (NEW), `tests/gate/test_gate_context_overlay_git.py` (NEW), `tests/unit/test_no_backward_compat_markers.py` (NEW), gate module-contract тест (edit), gate env-chain тест (new/edit)
- ~35 файлов TRAP-волны (Makefiles, templates, gate-тесты)

## $TEST_SPEC
| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/gate/test_gate_local_stack.py | test_compose_config_resolves_full_stack | `docker compose config` резолвит 12 модулей, 6 сетей, 11 volumes без ошибок (Инвариант 7, статически) | docker-compose.yml + includes |
| tests/gate/test_gate_local_stack.py | test_all_modules_included | include-секция ↔ core/modules/* синхронны | discover_modules контракт |
| tests/gate/test_gate_context_overlay_git.py | test_git_only_in_ensure_context_repo | grep-скан bootstrap/deploy скриптов: git-вызовы только внутри ensure_context_repo() (D3 доставки) | core/internal/bootstrap/deploy-modules.sh |
| tests/gate/test_gate_context_overlay_git.py | test_core_rsync_excludes_git | rsync-вызовы core-доставки содержат `--exclude '.git/'` (D1/D2 доставки) | core-deploy chain |
| tests/unit/test_no_backward_compat_markers.py | test_no_backward_compat_shims | скан test-server инфраструктуры на legacy/compat-маркеры (Инвариант 9) | node-configs + core |
| tests/gate/test_gate_module_contract*.py (edit) | test_system_module_contract | platform-secrets валиден по system-контракту, docker-контракт не применяется | module-system.mk (T3) |
| tests/gate/test_gate_env_chain*.py (new/edit) | test_prometheus_config_no_unexpanded_vars | итоговый prometheus-конфиг не содержит литеральных `${...}` плейсхолдеров (D5b) | monitoring envsubst-генерация (T5) |

## Design Decisions
## @rationale (два DevPlan вместо одного)
Q: почему не единый план? A: владелец явно запросил разделение по критерию «механическое vs требующее решений»; Wave A (010) выполняется немедленно и параллельно, Wave B заблокирована D-collapse — разные жизненные циклы.
## @rationale (T7 — статическая проверка Инварианта 7)
Q: почему не запускать стек в тесте? A: §TESTING запрещает запуск серверов в тестах; `docker compose config` проверяет инвариант структурно, рантайм остаётся predeploy/e2e-маркерам.
## @rationale (T8 — частичный TRAP[TEST])
Q: почему не все 101 файл? A: ценность TRAP[TEST] — в регрессионном rationale; массовая генерация без анализа даст шум. Приоритет — gate/инвариантные тесты; остаток — реестр в Debt.md.

## §Decisions Log
Collapse выполнен 2026-07-18, все решения подтверждены владельцем (question tool, Mode 3 GUIDED). Верификация кода перед collapse выявила 3 факта, изменивших постановку (см. пометки).

**D1 → A1: `make provision` + multi-SCOPE.**
Верификация: Makefile:60 передаёт `--scope $(or $(SCOPE),all)` как ОДНО значение — вариант плана `SCOPE=networks,volumes` без доработки не работает; скрипт поддерживает повторяемые `--scope` (массив `scopes=()`). Rev-условие существующего TRAP[DECISION] в core-deploy.yml:164 выполнено (Makefile в rsync-манифесте, Step 5c:150).
Решение: расширить таргет `provision` (запятая в SCOPE → повторяемые `--scope`), заменить raw-вызов в core-deploy.yml:188 на `make provision SCOPE=networks,volumes`, закрыть TRAP[DECISION] по выполненному Rev.

**D2 → A′: 3 канонических + вспомогательные в навигации; templates/* вне скоупа.**
Инвентаризация: 8 файлов = root, core/, core/modules/ (канонические) + core/internal/bootstrap/, tests/gates/ (вспомогательные → в §Навигация root) + 3× templates/template-*/AGENTS.md (payload `make new-project` — исключены из инварианта 4 явной оговоркой). Инвариант тестируем glob-исключением templates/*.

**D3 → A: `core/templates/module-system.mk`.**
Честный контракт system-модулей (install/status/restart/logs через systemd; без build/up/backup/down docker-семантики). Каскад T3 без изменений.

**D3b (новое, из верификации) → включить в T3: `RequiredBy=docker.service`.**
TRAP[DEBT] (MED) в module.yaml platform-secrets: unit без RequiredBy — задекларированный инвариант «fails closed» не enforced. Решение: добавить RequiredBy (или drop-in docker.service) в рамках T3 + проверка в predeploy. Blast radius принят: остановка boot при сбое секретов — декларированное поведение (Invariant 9: тестовый сервер пересоздаваем).

**D4 → A: двухуровневая семантика + `restart` = soft.**
root-глагол = оркестрация стека, module-глагол = один модуль (фиксируется в глоссарии AGENTS.md). `restart` унифицируется до soft (stop+start, Makefile.common:14); hard-вариант module.mk:78 (`--force-recreate`) переименовывается в `restart-hard`; `down` добавляется в module.mk как алиас `stop`.

**D5a → удалить + исправить комментарий.**
Верификация: host-переменные `HERMES_DASHBOARD_BASIC_AUTH_*` мертвы — compose (hermes-agent:110-111) потребляет `HERMES_DASHBOARD_USERNAME/PASSWORD` и реэкспортирует в контейнер под именем BASIC_AUTH_*. Комментарий .env.example:130 про nginx-переопределение — stale (в nginx-модуле потребителя нет; htpasswd там только для Prometheus/Loki). Решение: удалить BASIC_AUTH_* из .env/.env.example, переписать комментарий на фактическую цепочку. Перед удалением — grep secrets.env-цепочки на VPS-имена.

**D5b → починить подстановку (переквалифицировано из «dead var» в латентный БАГ).**
Верификация: prometheus.yml:58 использует `bearer_token: "${LITELLM_METRICS_TOKEN}"`, но конфиг монтируется `:ro` без envsubst, а Prometheus не разворачивает env в конфиге → литеральная строка уходит как токен, scrape LiteLLM-метрик молча сломан. Решение: генерация prometheus.yml через envsubst (init-контейнер/entrypoint) + TRAP[BUG] + gate-тест реальной подстановки. Аудитный env-drift скан дополнить config-потребителями (prometheus.yml и аналоги).

**D6 → A: генерация списка из `discover_modules.py`.**
Оба workflow-списка (platform-test.yml pull, nightly-gate.yml cleanup) читают вывод шага генерации; single source of truth уже существует (`make discover-modules`).

## Next Steps
```
1. [DONE 2026-07-18] architect: collapse D1-D6 + D3b → §Decisions Log заполнен
2. coder Read .ai/plans/011-drift-arch-decisions/01-DevPlan.md §Decisions Log, implement Wave 2: T1, T5, T6, T8
3. coder Read .ai/plans/011-drift-arch-decisions/01-DevPlan.md, implement Wave 3: T3 then T4; parallel T2
4. coder Read .ai/plans/011-drift-arch-decisions/01-DevPlan.md, implement Wave 4: T7
5. qa Re-run drift audit, verify AC #1-8, write VerificationReport
```
Порядок: выполнять ПОСЛЕ merge плана 010 (пересечение по workflows минимизирует конфликты).

# $END_DEVPLAN
