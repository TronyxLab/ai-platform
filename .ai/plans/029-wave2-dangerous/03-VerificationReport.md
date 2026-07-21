# 03-VerificationReport: 029-Wave2-Dangerous DevPlan Pre-Implementation Audit

**Program:** 027-architecture-modernization-program
**Wave:** 2 of 5 (Dangerous)
**Audit scope:** Pre-implementation review of `02-DevPlan.md` (531 строк)
**Audit type:** LARGE (22+ файлов в манифесте, архитектурные изменения SSH-фасада + audit-trail + composite-action)

🔒 Verified against SHA: **BLOCKED** — git commands blocked by permission rules (rule: `git *` allow contradicted by deny `*`). Audit performed against filesystem snapshot via read-only tools (read/grep/glob).
Working tree: не проверено (та же причина). Оператору следует убедиться в чистом working tree перед стартом Wave 2.

$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Pre-implementation аудит DevPlan 029-Wave2-Dangerous перед передачей в dev-pipeline. Цель: выявить фактические расхождения между утверждениями DevPlan и реальной кодовой базой, дефекты самого плана (внутренние противоречия, нереализуемые AC, технические ошибки), и нарушения REQUIRES-условий. Не выполнять runtime-валидацию (реализации ещё нет) — только статический аудит плана + cross-file сверка с существующей кодовой базой.
DESCRIPTION:           Phase 1 (формальный контракт DevPlan) + Phase 2 (cross-file drift между DevPlan-claims и кодовой базой) + Phase 3 (инварианты) + Phase 4 (тест-план AC). Phase 5/6 не применяются (нет реализации). Найдено 11 drift-находок, из них 4 CRITICAL и 5 HIGH. Вердикт: DRIFTED (CRITICAL) — DevPlan требует ревизии перед делегированием в Coder.
RATIONALE:             Wave 2 — Dangerous wave (явно заявленный высокий production-риск). Single point of failure SSH-фасад, 6 файлов-мигрантов, 7 entrypoints с audit-trail. Ошибки в плане на этом этапе =-multiplied cost на production. Архитектор заявил verify-against-codebase «2026-07-21», но фактическая сверка выявила расхождения → verify был неполным или устарел. Pre-implementation gate обязательный для Dangerous-волн.
ACCEPTANCE_CRITERIA:   (1) Все 7 полей $ARTIFACT_CONTRACT DevPlan проверены и соответствуют doc-protocols. (2) Все verify-claims DevPlan сверены с кодовой базой через read-only инструменты. (3) Все 18 AC проверены на реализуемость. (4) Внутренние противоречия DevPlan обнаружены и описаны. (5) REQUIRES-условия проверены. (6) Вердикт определяет готовность плана к делегированию.
IMPLEMENTS:            QA workflow §LARGE (Phases 1-4), §INVARIANT (Pessimistic by Design), §INVARIANT (Cross-File by Default), §OUTPUT (разделы 1-4), artifact-registry R1 (VerificationReport canonical name).
IMPACTS:               Данный отчёт. Делегирование в Architect для ревизии DevPlan перед стартом Coder (см. §Handoff).
REQUIRES:              `.ai/plans/029-wave2-dangerous/02-DevPlan.md`, read-only доступ к `core/`, `.github/`, `tests/`, `.ai/plans/027-*/01-Brief.md`, `.ai/plans/028-*/02-DevPlan.md`, `reports/architecture-analysis-2026-07-21.md`.
$END_ARTIFACT_CONTRACT

---

## Section 1 — Static Audit (Phase 1)

### Compliance matrix формального контракта DevPlan

| Поле / секция | Статус | Evidence |
|---|---|---|
| `$START_DEVPLAN` / `$END_DEVPLAN` | ✅ PASS | lines 9, 523 |
| `$ARTIFACT_CONTRACT` boundaries | ✅ PASS | lines 11, 54 |
| PURPOSE | ✅ PASS | line 12 — конкретный, verifiable |
| DESCRIPTION | ✅ PASS | line 13 — 3 эпика + порядок + cross-refs |
| RATIONALE | ✅ PASS | line 14 — обоснование с evidence (rg counts, выявленные secondary-файлы) |
| ACCEPTANCE_CRITERIA | ✅ PASS | lines 15-50 — 24 AC, все verifiable |
| IMPLEMENTS | ✅ PASS | line 51 — Brief 027 §4/§9/§10, AGENTS.md invariants, principles, skills |
| IMPACTS | ✅ PASS | line 52 — полный перечень new/modified файлов |
| REQUIRES | ✅ PASS | line 53 — Wave 1 predecessor, staging-нода, чистый working tree |
| `$DOCUMENT_PLAN` | ✅ PASS | lines 58-80 — GOALs + USE_CASEs |
| Draft Code Graph (XML) | ✅ PASS | lines 84-246 — все entity с type/layer/api |
| Step-by-Step Data Flow | ✅ PASS | lines 250-339 |
| File Manifest CREATE/MODIFY | ✅ PASS | lines 345-381 |
| Acceptance Criteria table | ✅ PASS | lines 386-405 |
| Risk Mitigation & Revert | ✅ PASS | lines 411-435 |
| Execution Order | ✅ PASS | lines 441-485 |
| Effort Estimate | ✅ PASS | lines 491-497 |
| Anti-goals | ✅ PASS | lines 501-509 |
| Cross-references | ✅ PASS | lines 515-521 |

**Phase 1 summary:** DevPlan структурно корректен, все обязательные секции присутствуют, формальный $ARTIFACT_CONTRACT соответствует doc-protocols. **0 нарушений формального контракта.** Проблемы обнаружены в Phase 2 (содержание vs кодовая база) и внутри Data Flow / AC (Phase 3).

---

## Section 2 — Drift Analysis (Phase 2)

Сверка verify-claims DevPlan (lines 7, 14, 20, 159-167) с фактической кодовой базой через `rg`.

### Drift Register

| DRIFT-ID | Severity | Files | Expected (DevPlan) | Actual (codebase) | Fix suggestion |
|---|---|---|---|---|---|
| DRIFT-1 | **CRITICAL** | DevPlan line 7, 20 vs `core/lib/vps-readiness.sh` | «vps-readiness.sh=5 inline ssh без timeout» | **4 inline** `ssh -i` (lines 81, 95, 110, 126), не 5 | Исправить count в DevPlan. Изменить AC-формулировку «5 inline ssh → ssh_read» на «4 inline ssh → ssh_read». AC3 (`rg "ssh\s+-i\s" core/` → 0) всё ещё достижим. |
| DRIFT-2 | **CRITICAL** | DevPlan line 159-161 vs `core/lib/vps-readiness.sh:82,96,111,127` | «inline `ssh -i ... -o BatchMode=yes -o StrictHostKeyChecking=accept-new` БЕЗ ConnectTimeout, severity HI — без timeout могут hang'ать бесконечно» | Все 4 inline **уже содержат `-o ConnectTimeout=10`** — hang на connection-phase ограничен 10s | Снизить severity vps-readiness миграции с HI на MED в DevPlan. Переформулировать rationale: миграция нужна для **unification** + timeout-wrapper на remote-CMD (commands short: `exit`, `ping`, `docker ps`), а не для устранения «бесконечного hang'а». |
| DRIFT-3 | **CRITICAL** | DevPlan line 165 vs `core/internal/deploy/reconcile-projects.sh:219,231` | «2 inline ssh без timeout на lines 218, 230» | lines 219, 231 **уже содержат `-o ConnectTimeout=10`** | То же что DRIFT-2. |
| DRIFT-4 | **HIGH** | DevPlan line 28 (AC9) | «`rg "actions/checkout@v" .github/workflows/` → ≤3 вхождения» (whitelist: workflows со специфическим checkout-config) | **10 вхождений** в 8 workflows. Из 3 не-мигрируемых (mirror, build-platform, platform-deploy) один `platform-deploy.yml` содержит **3 checkout'а** (lines 81, 135, 161) → после миграции 6 workflows останется 4 checkout'а (3 в platform-deploy + 1 в одном из mirror/build-platform). **AC9 ≤3 недостижим без миграции platform-deploy.yml.** | (а) Включить `platform-deploy.yml` в миграцию (сделать 7 workflows вместо 6), ИЛИ (б) Ослабить AC9 до ≤4 с явным комментарием «platform-deploy содержит matrix-build с 3 checkout'ами — вне scope», ИЛИ (в) Рефакторить platform-deploy.yml — но это Wave 2+ работа. |
| DRIFT-5 | **HIGH** | DevPlan line 14, 29 vs `.github/workflows/*.yml` | «6× дублированную последовательность setup-python → setup-gitleaks → provisioner» / «−30s на каждый мигрированный workflow» | `setup-python-venv` присутствует **только в 3 workflows**: platform-test.yml, nightly-gate.yml, push-gate.yml. deploy-project.yml, stage-deploy.yml, core-deploy.yml **не используют setup-python-venv вообще**. | DevPlan должен явно классифицировать 2 категории workflow: (1) с python — push-gate, nightly-gate, platform-test (composite даёт −30s); (2) без python (deploy-project, stage-deploy, core-deploy) — composite даёт только unification (DRIFT-risk reduction), но не speed gain. Пересмотреть KPI «−30s/workflow» — он достижим только для 3 workflows. |
| DRIFT-6 | **HIGH** | DevPlan line 19, AC1, AC4 vs `core/internal/bootstrap/scp-deliver.sh:131` (и remote-cmd.sh:274,391,464,473,555) | AC4: «`rg "ConnectTimeout" core/` → все вхождения внутри `core/lib/ssh.sh` или через sourced SSH_OPTS_COMMON» | scp-deliver.sh:131 + remote-cmd.sh:274,391,464,473,555 содержат **inline fallback** `${SSH_OPTS[*]:--o StrictHostKeyChecking=accept-new -o ConnectTimeout=30}`. literal `ConnectTimeout=30` присутствует в fallback-branches. После source lib/ssh.sh и заполнения SSH_OPTS — fallback не активируется, но literal остаётся в коде → `rg` найдёт его. | AC4 переформулировать: «`rg "ConnectTimeout" core/` → все вхождения либо внутри `core/lib/ssh.sh`, либо внутри `${SSH_OPTS[*]:-...}` fallback-branches в scp-deliver.sh/remote-cmd.sh (задокументированные legacy-fallbacks, удаляются в Wave 3)». ИЛИ: добавить шаг в Execution Order: «удалить `${SSH_OPTS[*]:-...}` fallback-branches, т.к. lib/ssh.sh гарантирует инициализацию». |
| DRIFT-7 | **CRITICAL** | DevPlan line 32-33 (AC14), line 217 (Data Flow 2), line 297 (self-correction), line 311 (Implementation note) | AC14 описывает `audit_step` с `trap 'audit_log "FAIL" ...' EXIT` в под-оболочке + unit-тест, проверяющий failing→2 записи (START+FAIL). | Сам DevPlan (line 297) признаёт: bash EXIT-trap fires on **ALL exits including success** → trap-on-EXIT гарантированно эмитит FAIL даже на success. AC14+unit-тест невозможны корректно с этой логикой. Line 311 перекладывает выбор на Coder — но AC уже зафиксирован. | Архитектор обязан **до делегирования** выбрать одну из двух опций и зафиксировать в AC14: (Опц. A) `trap ... RETURN` + явная проверка `$?` после exec; (Опц. B) wrapper-style без trap — capture rc, потом условный emit. Удалить «Architect должен выбрать на этапе Code» — это DESIGN-решение, должно быть в плане, не в коде. |
| DRIFT-8 | **CRITICAL** | DevPlan line 14 vs `core/lib/audit_logging.sh:67-79` | «async-write audit через `>>` в `/var/log/platform/audit.log` (R-RISK-8 mitigation) — уже реализовано в текущей `audit_log()`» | Текущая `audit_log()` — **dual-write**: сначала синхронный `logger -t platform-audit` (syscall ~5-10ms), затем `printf >> $PLATFORM_AUDIT_LOG` (belt-and-suspenders). Это НЕ pure-async `>>`. R-RISK-8 mitigation основан на неверном описании. | Исправить DevPlan line 14: «текущая `audit_log()` — dual-write (logger + file-append), уже работает в production. `audit_step` wrapper добавляет 1 extra `audit_log` call (START) + subshell overhead (~1ms). R-RISK-8 mitigation остаётся в силе (dual-write = belt-and-suspenders), но не из-за pure-async, а из-за non-blocking semantics (file_rc не fatal).» |
| DRIFT-9 | **MEDIUM** | DevPlan AC14 (staging-test `tail /var/log/platform/audit.log`) vs `audit_logging.sh:32-49` | AC14 предполагает tail оператором | Файл создаётся с режимом 0664/root:adm. ci-deploy user — не в group adm по умолчанию → `tail` даст permission-denied. | Добавить в AC14: «tail выполняется под root или user в group adm (добавить ci-deploy в adm на staging-ноде — задокументировать в node.yaml или разрешить через ACL)». Или: добавить `chmod +r` для ci-deploy. |
| DRIFT-10 | **HIGH** | DevPlan IMPACTS line 52, File Manifest line 366-372 vs `core/internal/scaffold/remove-project.sh`, `core/entrypoints/build.sh` | Предполагается `audit_step` wrapper в 7 entrypoints | `remove-project.sh` и `build.sh` **не содержат `source audit_logging.sh`** (grep: 0 matches). Только `deploy-project.sh:69` source'ит audit_logging (и то с `2>/dev/null || true`). | Добавить в Execution Order (W2-E3) шаг перед «MODIFY 7 entrypoints»: «Предварительный подшаг для 6 entrypoints (кроме deploy-project.sh): добавить `source "${CORE_DIR}/lib/audit_logging.sh"` вверху main-flow». Иначе audit_step будет fail на undefined function. |
| DRIFT-11 | **HIGH** | DevPlan line 39, File Manifest line 371 vs `core/internal/bootstrap/node-lifecycle.sh:1289` | «node-lifecycle.sh — audit_step "node-update:${NODE}" в update-mode (init-mode уже имеет audit-summary на step 16)» — подразумевает, что update-mode не имеет audit | **update-mode уже вызывает `audit_log "node-update:complete" "DONE" ...`** на line 1289. После внедрения audit_step — дублирование: 1 START + 1 DONE (от audit_step) + 1 дополнительный DONE (от line 1289) на каждый node-update. | В Execution Order добавить: «удалить или интегрировать существующий `audit_log "node-update:complete"` на line 1289 в новый audit_step wrapper — иначе double-emit ломает AC16 (≥6 записей = 3×2)». |

### Cross-file mismatches summary

- **Inline `ssh -i` count:** DevPlan = 7 (5+2), actual = 6 (4+2). −1 vs plan.
- **Inline `ssh -i` без ConnectTimeout:** DevPlan = 7, actual = **0** (все имеют ConnectTimeout=10). Драматическое расхождение.
- **setup-python-venv в workflows:** DevPlan = 6 workflows, actual = 3. −3 vs plan.
- **Workflows с `actions/checkout@v*`:** DevPlan = 10 вхождений, actual = 10 ✓ (count совпадает, но распределение отличается: 8 workflows, не 9).
- **Source audit_logging в 7 entrypoints:** DevPlan считает что они готовы к audit_step, actual = 1/7 готов (только deploy-project.sh).
- **audit_log в node-lifecycle update-mode:** DevPlan не учитывает существующий emit, actual = 1 существующий.

**Drift summary:** 11 drifts total — **4 CRITICAL** (DRIFT-1, 2, 3, 7, 8 — формально 5, но DRIFT-7 и DRIFT-8 объединимы по criticality), **5 HIGH** (DRIFT-4, 5, 6, 10, 11), **2 MEDIUM/LOW** (DRIFT-9). Все в категории «DevPlan-claims vs codebase-reality» + «DevPlan internal contradictions».

---

## Section 3 — Invariant Status (Phase 3)

Инварианты из root AGENTS.md (10 шт.) + core/AGENTS.md cross-layer rules. Проверка — какие из них затрагивает Wave 2 и не нарушает ли DevPlan их.

| # | Инвариант | Status | Evidence / Risk |
|---|---|---|---|
| Inv-1 | Makefile — единый фасад | **HELD** | DevPlan не добавляет новых make-глаголов для ssh.sh (line 521: «SSH-фасад не добавляет новый make-глагол — lib-файл»). lib/ssh.sh — sourced, не entrypoint. |
| Inv-2 | Deploy model (git push → CI, SCP/rsync для core) | **HELD** | DevPlan не меняет deploy-model. SSH-фасад используется внутри SCP/rsync-flow. |
| Inv-3 | org = context (определяется из физического пути) | **HELD** | Не затрагивается. |
| Inv-4 | AGENTS.md — канонические файлы | **AT_RISK** | DevPlan line 49, 380: добавление TRAP[DECISION] в root AGENTS.md. Само по себе не нарушение, но требует осторожности — TRAP должен быть в секции «после языковой политики» и не дублировать существующие TRAP'ы. |
| Inv-5 | entrypoint-manifest.yaml — YAML-реестр | **AT_RISK** | DevPlan line 48, 379: «+core/lib/ssh.sh в lib-секции, +2 gate-теста». Фактический manifest **не содержит lib-секцию вообще** (grep: 0 matches для `lib/`). Риск: DevPlan предполагает существующую секцию, которой нет — нужно создать или использовать другую схему регистрации. |
| Inv-6 | bootstrap-node идемпотентный | **HELD** | Не затрагивается напрямую. audit_step wrapper не нарушает идемпотентность. |
| Inv-7 | Полный локальный стек через docker compose | **HELD** | Не затрагивается. |
| Inv-8 | LiteLLM — PostgreSQL | **HELD** | Не затрагивается. |
| Inv-9 | Тестовый сервер пересоздаваем | **HELD** | Используется в staging-gate (AC6, AC14). |
| Inv-10 | Сборка образов hermes | **HELD** | Не затрагивается. |
| core-CrossLayer | entrypoints → internal/lib; internal → internal/lib/modules; modules → lib/templates | **HELD** | lib/ssh.sh в core/lib/ — правильный слой. internal/ source'ит lib/ssh.sh — соответствует правилу. |
| §Language policy | Новый код на Python, bash — тонкая обёртка | **AT_RISK** | lib/ssh.sh — новая shell-lib (~80 строк). Явное исключение в AGENTS.md «Bash остаётся для lib-функций низкого уровня». Но DevPlan не ссылается на это исключение явно → риск future-нарушений «по прецеденту». |

**Invariant summary:** 8 HELD, 3 AT_RISK, 0 VIOLATED. Основные риски — Inv-5 (несуществующая lib-секция в manifest) и §Language policy (нужно явное обоснование исключения).

---

## Section 4 — Test Quality & AC Realizability (Phase 4)

### AC realizability matrix

| AC# | Description | Verdict | Reason |
|---|---|---|---|
| AC1 | `ssh_exec()` в core/lib/ssh.sh | ✅ ACHIEVABLE | Создание нового файла, нет блокеров. |
| AC2 | `timeout "${timeout}"` dynamic | ✅ ACHIEVABLE | — |
| AC3 | `rg "ssh\s+-i\s" core/` → 0 | ✅ ACHIEVABLE | 6 inline `ssh -i` в vps-readiness+reconcile → после миграции 0. |
| AC4 | `rg "ConnectTimeout" core/` → только в lib/ssh.sh | ❌ **NOT ACHIEVABLE** (без правок плана) | DRIFT-6: scp-deliver.sh:131 + remote-cmd.sh:274,391,464,473,555 содержат literal `ConnectTimeout=30` в fallback-branches. |
| AC5 | pytest tests/test_lib_ssh.py green | ✅ ACHIEVABLE | Но зависит от DRIFT-7 — тест dry_runmode → echo без exec требует чёткой семантики. |
| AC6 | Staging-test (converge, project-list, project-status) | ✅ ACHIEVABLE | Требует staging-ноду (REQUIRES выполняется). |
| AC7 | action.yml exists | ✅ ACHIEVABLE | — |
| AC8 | `rg "uses:.*setup-platform" .github/workflows/` → ≥6 | ✅ ACHIEVABLE | Если 6 workflows мигрируют. |
| AC9 | `rg "actions/checkout@v" .github/workflows/` → ≤3 | ❌ **NOT ACHIEVABLE** (без правок плана) | DRIFT-4: platform-deploy.yml один даёт 3 checkout'а. |
| AC10 | CSV с ≥6 workflows | ✅ ACHIEVABLE | — |
| AC11 | `audit_step()` def in audit_logging.sh | ✅ ACHIEVABLE | — |
| AC12 | `rg "audit_step\b" core/` → ≥7 | ✅ ACHIEVABLE | — |
| AC13 | pytest tests/test_audit_step.py green | ⚠️ **AT RISK** | DRIFT-7: тест «failing → 2 записи START+FAIL» невозможен с trap-on-EXIT пока не зафиксирован дизайн. |
| AC14 | Staging `tail /var/log/platform/audit.log` ≥6 | ⚠️ **AT RISK** | DRIFT-9: permission-denied риск + DRIFT-11: double-emit может дать ≥8, но нестандартных. |
| AC15 | `make gate MODE=fast` green | ✅ ACHIEVABLE | — |
| AC16 | ruff check + format | ✅ ACHIEVABLE | — |
| AC17 | shellcheck | ✅ ACHIEVABLE | — |
| AC18 | TRAP[DECISION] в AGENTS.md | ✅ ACHIEVABLE | — |

**AC summary:** 13 ACHIEVABLE, 2 NOT ACHIEVABLE (AC4, AC9), 3 AT RISK (AC13, AC14 + косвенно AC5). **2 AC требуют явной ревизии DevPlan до старта Coder.**

### Test plan quality

- **`tests/test_lib_ssh.py`** — 5 заявленных кейсов (ssh_exec timeout=1 unreachable → 124, reachable → 0, ssh_read default 60, dry_run echo, SSH_OPTS_COMMON immutability). Пробел: нет кейса «ssh_exec с mode=deploy default timeout=600» (просто проверить default-value). Пробел: нет кейса «ssh_exec с timeout=124 от ssh native exit → return 124» (различение timeout-exit от ssh native exit).
- **`tests/test_audit_step.py`** — 3 кейса. Пробел: нет кейса «audit_step с command, бросающей исключение (exit ≥ 128)». Пробел: нет кейса «audit_step nested в другой audit_step» (корректность trap-scope).
- **Test Honesty R5 (ANTI-SURVIVORSHIP):** Для staging-test (AC6) нет negative-test — нужен тест «ssh_exec на несуществующем host → return non-zero, staging-gate блокирует merge». DevPlan не указывает negative-test для SSH-фасада.

---

## Section 5 — Runtime Validation (Phase 5)

**SKIPPED** — pre-implementation audit. Реализация ещё не существует, runtime-валидация неприменима.

---

## Section 6 — Config Sync (Phase 6)

**SKIPPED** — Wave 2 не затрагивает env-vars propagation, compose-overrides, network/volume config. Composite-action переиспользует существующий cache-key из setup-python-venv без изменений — проверено (DRIFT-review setup-python-venv/action.yml line 43: `venv-${{ runner.os }}-${{ hashFiles('Makefile') }}`).

Единственная config-sync-проверка: `core/entrypoint-manifest.yaml` — DevPlan предполагает регистрацию `core/lib/ssh.sh` в lib-секции, но **lib-секция в manifest не существует** (Inv-5 AT_RISK). Требуется уточнение схемы регистрации.

---

## REQUIRES-условия проверка

| REQUIRES-условие (DevPlan line 53) | Status | Evidence |
|---|---|---|
| Завершённая Wave 1 + production-релиз | ⚠️ **PARTIAL** | `core/lib/args.sh`, `tests/_conftest/honesty.py`, `tests/helpers/gate_helpers.py`, `core/internal/scripts/yaml_query.py` существуют → часть Wave 1 сделана. **Но `03-VerificationReport.md` для Wave 1 в `.ai/plans/028-wave1-immediate/` НЕ существует** (DevPlan 028 line 1811 явно требует). Статус завершения Wave 1 не задокументирован. |
| Baseline-метрики из W1-E8 для замера CI composite effect | ❌ **NOT MET** | `reports/baseline-metrics-2026-07.csv` **не существует** (glob: No files found). DevPlan 029 line 520 явно cross-ref'ит этот файл. Без baseline — AC10 (CI before/after timing) невозможен. |
| lib/args.sh как precedent | ✅ MET | Файл существует, корректно оформлен. |
| Чистый working tree | 🔒 UNVERIFIED | git-команды заблокированы permission-rules. |
| Staging-нода | ✅ ASSUMED | Оператор предоставляет. |
| Доступ к GitHub Actions API | ✅ ASSUMED | — |
| Чтение reports/architecture-analysis-2026-07-21.md §P02/P10/P11/P15 | ✅ MET | Файл существует. |

**REQUIRES summary:** 2 из 7 условий не выполнены или частично. **Baseline-metrics — критический блокер для AC10.**

---

## Cross-references проверка

| Cross-ref в DevPlan | Status |
|---|---|
| Brief 027 §4 (line 517) | ✅ EXISTS: `.ai/plans/027-architecture-modernization-program/01-Brief.md` |
| DevPlan 028 (line 518) | ✅ EXISTS: `.ai/plans/028-wave1-immediate/02-DevPlan.md` |
| reports/architecture-analysis-2026-07-21.md §P02/P10/P11/P15 (line 519) | ✅ EXISTS |
| reports/baseline-metrics-2026-07.csv из W1-E8 (line 520) | ❌ **MISSING** |
| core/AGENTS.md §Канонические операции (line 521) | ✅ EXISTS |

---

## Semantic Verdict

# **DRIFTED (CRITICAL)**

DevPlan 029-Wave2-Dangerous **не готов к делегированию в Coder в текущем виде**. Структурно корректен (Phase 1 — 0 нарушений формального контракта), но содержит **11 drift-находок** (4 CRITICAL + 5 HIGH) и **2 AC нереализуемы** без правок плана.

### Блокирующие проблемы (требуют ревизии Architect перед стартом):

1. **DRIFT-7 (CRITICAL, design gap):** `audit_step` trap-on-EXIT-логика противоречит собственному unit-тесту (AC13). Архитектор должен выбрать дизайн (trap-RETURN vs wrapper-style без trap) и зафиксировать в DevPlan, а не делегировать выбор на Coder-фазу.
2. **DRIFT-8 (CRITICAL, false premise):** R-RISK-8 mitigation основано на неверном описании текущей `audit_log()` как «pure-async `>>`». Фактически — dual-write (logger + append). Обоснование mitigation нужно скорректировать.
3. **DRIFT-1/2/3 (CRITICAL, false verify):** Утверждения «5 inline ssh без timeout» и «severity HI — бесконечный hang» неверны. Фактически 4 inline (не 5), все имеют ConnectTimeout=10. Severity для vps-readiness/reconcile-projects миграции завышена.
4. **AC4 NOT ACHIEVABLE:** scp-deliver.sh:131 + remote-cmd.sh:274,391,464,473,555 содержат literal `ConnectTimeout=30` в `${SSH_OPTS[*]:-...}` fallback-branches. Нужно: либо ослабить AC4, либо добавить шаг удаления fallback-branches.
5. **AC9 NOT ACHIEVABLE:** platform-deploy.yml один даёт 3 `actions/checkout@` вхождения. Нужно: либо включить platform-deploy.yml в миграцию (→ 7 workflows), либо ослабить AC9 до ≤4.
6. **REQUIRES NOT MET:** `reports/baseline-metrics-2026-07.csv` не существует → AC10 (CI before/after timing) невозможен. Требуется завершить W1-E8 сначала или явно исключить AC10 из Wave 2 scope.
7. **Inv-5 AT_RISK:** DevPlan предполагает lib-секцию в `entrypoint-manifest.yaml`, которой нет. Требуется уточнить схему регистрации `core/lib/ssh.sh`.
8. **DRIFT-10 (HIGH, missing step):** 6 из 7 entrypoints не имеют `source audit_logging.sh`. Execution Order W2-E3 не содержит шага добавления source-инструкции.
9. **DRIFT-11 (HIGH, double-emit):** node-lifecycle.sh:1289 уже вызывает `audit_log "node-update:complete"` — после audit_step будет double-emit.

### Рекомендация оператору

**Не делегировать в dev-pipeline** до ревизии Architect. Рекомендуется делегирование Architect'у с этим VerificationReport'ом для:
- исправления drift-утверждений (DRIFT-1/2/3/8) и AC-формулировок (AC4, AC9, AC13, AC14);
- фиксации design-решения по audit_step trap-логике (DRIFT-7);
- добавления недостающего шага source audit_logging (DRIFT-10) и обработки существующего audit-emit в node-lifecycle update-mode (DRIFT-11);
- явного подтверждения статуса Wave 1 (создание VerificationReport 028 или явное исключение) и завершения W1-E8 (baseline-metrics.csv) либо descoping AC10.

После ревизии Architect'а — повторный QA-аудит обновлённого DevPlan перед делегированием в Coder.

$END_VERIFICATION_REPORT
