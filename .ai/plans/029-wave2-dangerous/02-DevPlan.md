# 029-DevPlan: Wave 2 (Dangerous) — SSH Timeouts, CI Composite, Audit-Trail

**Program:** 027-architecture-modernization-program
**Wave:** 2 of 5 (Dangerous — высокий production-риск, явный профит)
**Source Brief:** `.ai/plans/027-architecture-modernization-program/01-Brief.md` §4 (Wave 2 эпики W2-E1…W2-E3)
**Predecessor:** `.ai/plans/028-wave1-immediate/02-DevPlan.md` (Wave 1 — честные тесты, baseline-метрики, lib/args.sh)
**Verified against codebase:** 2026-07-21 (4 файла-мигранта SSH: scp-deliver.sh=225 LOC, remote-cmd.sh=572 LOC, remove-project.sh=482 LOC, project-list.sh=387 LOC; `core/lib/ssh.sh` не существует; `core/lib/audit_logging.sh`=81 LOC с готовой `audit_log()`; 7 state-modifying entrypoints без wrapper-emit; 9 CI workflows, 10 `actions/checkout@v*` вхождений; composite-action `setup-python-venv` уже существует как паттерн). **VerificationReport 03 audit addendum (2026-07-21):** vps-readiness.sh содержит 4 (не 5) inline ssh, все с `-o ConnectTimeout=10`; reconcile-projects.sh также с ConnectTimeout=10. setup-python-venv используется только в 3 workflow (push-gate, nightly-gate, platform-test), не в 6. baseline-metrics-2026-07.csv не существует — W1-E8 не завершён.

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Реализовать Wave 2 (Dangerous) программы архитектурной модернизации 027: закрыть CRITICAL-проблему P02 (CI hangs из-за SSH-вызовов без timeout), дать явный профит (CI setup −30s/workflow, 7 audit-точек покрытия state-modifying operations), подготовить фундамент для Wave 4 (SSH-фасад как single source for remote-operations, переиспользуемый при Strangler-декомпозиции deploy-modules/node-lifecycle/converge). Все изменения затрагивают production-пути: SSH-фасад = single point of failure для всех remote-операций; CI composite = все workflows; audit-trail = 7 entrypoints. Поэтому каждый эпик имеет обязательный staging-gate перед merge.
DESCRIPTION:           3 эпика (W2-E1…W2-E3), выполняемых последовательно с одним параллельным исключением: (A) lib/ssh.sh — единая SSH-фасадная функция `ssh_exec()` с wrapper'ом `timeout` (600s default для remote-deploy, 60s для read-only), `SSH_OPTS_COMMON` const (-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -o ServerAliveInterval=30 -o ServerAliveCountMax=10), миграция 4 файлов-потребителей (scp-deliver.sh, remote-cmd.sh, remove-project.sh, project-list.sh) + 2 secondary (vps-readiness.sh, reconcile-projects.sh). Staging-тест обязателен перед merge (R-RISK-1, impact H). (B) setup-platform composite — `.github/actions/setup-platform/action.yml`, объединяющий checkout + setup-python-venv + setup-gitleaks + provisioner-call. Минимум 7 workflows мигрированы (включая platform-deploy.yml). Параллелен к E1 (независимая подсистема CI). (C) audit-trail wrapper — расширение `core/lib/audit_logging.sh` wrapper-функцией `audit_step()` (wrapper-style, без trap: START → exec → capture $? → DONE/FAIL emit), применение в 7 entrypoints. После E1 (нужен стабильный SSH-фасад для end-to-end проверки entrypoint'ов). Wave завершается production-релизом: staging-деплой → merge → верификация audit-trail на production-ноде.
RATIONALE:             Анализ архитектуры (`reports/architecture-analysis-2026-07-21.md`), верифицированный против кодовой базы 2026-07-21, показал: (а) SSH-вызовы без общего timeout — CRITICAL trust-киллер, `rg "ssh\b" core/` находит 50+ вхождений в 14 файлах. **VerificationReport 03 audit (2026-07-21) уточняет:** в vps-readiness.sh — 4 inline-вызова (lines 82,96,111,127), а не 5; в reconcile-projects.sh — 2 inline-вызова (lines 219,231). **Все 6 инлайн-вызовов уже содержат `-o ConnectTimeout=10`** — они не висят бесконечно на connection-phase, но не имеют `timeout`-wrapper'а на весь remote-CMD (включая серверное выполнение) и используют прямые ssh-флаги вместо единой фасадной функции. Миграция нужна для **unification** (единый source of truth: `ssh_read()` вместо inline ssh) + timeout-wrapper на весь remote-CMD. Основной risk — scp-deliver.sh + remote-cmd.sh (ConnectTimeout=30 в SSH_OPTS, но без `timeout` wrapper на весь remote-CMD → может висеть при ServerAliveCountMax=10 × 30s = 5 мин на каждый retry-cycle); (б) CI workflows содержат 10 `actions/checkout@v*` + дублированную последовательность setup-python → setup-gitleaks → provisioner в 3 workflows (push-gate, nightly-gate, platform-test); deploy-project, stage-deploy, core-deploy используют checkout+gitleaks/provisioner но **без setup-python-venv** (не используют Python). Итого: 3 workflow с python (composite даёт −30s overhead), 3 workflow без python (composite даёт unification + DRIFT-risk reduction, но не speed gain); (в) 7 entrypoints (`context-promote`, `remove-project`, `provision`, `hermes-build`, `secrets-unlock`, `node-update --mode update`, `core-deploy rsync`) модифицируют state без audit-trail → forensic-невозможность. Оператор выбрал (бриф 027 §4, §9 R-RISK-1): (а) staging-тест обязателен перед merge SSH-фасада — impact повышен до H (single point of failure для deploy/bootstrap/healthcheck/node-update/converge/project-list/remove-project/verify); (б) composite-action как расширение существующего паттерна `setup-python-venv` — не новая абстракция, а композиция проверенных actions; (в) dual-write audit через `logger -t platform-audit` + `printf >> /var/log/platform/audit.log` (R-RISK-8 mitigation, belt-and-suspenders) — уже реализовано в текущей `audit_log()`, нужно только обернуть entrypoints в `audit_step()`.
ACCEPTANCE_CRITERIA:
  **A. lib/ssh.sh — единый SSH-фасад с timeout (W2-E1):**
    1. `core/lib/ssh.sh` создан с PUBLIC API: `ssh_exec(host, user, command, [timeout=600], [mode=deploy|read])`, `ssh_read(host, user, command, [timeout=60])` (алиас для read-only с дефолтом 60s), `SSH_OPTS_COMMON` readonly-const, `ssh_exec_dry_run(...)` для echo-режима. Файл имеет MODULE_CONTRACT region + GREP_SUMMARY + STRUCTURE.
    2. `ssh_exec()` wrapper'ит `timeout "${timeout}" ssh "${SSH_OPTS_COMMON[@]}" "${user}@${host}" "${command}"` с детекцией exit-кода 124 (timeout) → log_imp 1 + return 124, не silent-fail.
    3. Мигрированы 4 primary файла (из брифа): `core/internal/bootstrap/scp-deliver.sh`, `core/internal/bootstrap/remote-cmd.sh`, `core/internal/scaffold/remove-project.sh`, `core/internal/scaffold/project-list.sh`. SSH-вызовы в этих файлах делегируют в `ssh_exec()` / `ssh_read()`.
    4. Мигрированы 2 secondary файла (обнаружены при verify): `core/lib/vps-readiness.sh` (4 inline-ssh на lines 82/96/111/127 — все уже с `-o ConnectTimeout=10`, но без timeout-wrapper на remote-CMD → `ssh_read(host, "ci-deploy", cmd, timeout=30)`), `core/internal/deploy/reconcile-projects.sh` (2 inline-ssh на lines 219/231 — также с ConnectTimeout=10 → `ssh_read()`). Миграция даёт unification (единый фасад вместо inline-дисперсии), а не устранение «бесконечного hang'а» (ConnectTimeout уже ограничивает connection-phase).
    5. `rg "ssh\s+-i\s" core/ --type sh` → 0 вхождений (все inline ssh -i устранены). `rg "ConnectTimeout" core/ --type sh` → все вхождения внутри `core/lib/ssh.sh` (через `SSH_OPTS_COMMON`), либо внутри `${SSH_OPTS[*]:-...}` fallback-branches в `scp-deliver.sh:131` и `remote-cmd.sh` (legacy fallbacks, которые не активируются при source lib/ssh.sh, но literal остаётся в коде — документированное исключение, удаляется в Wave 3).
    6. Staging-тест перед merge: `make converge NODE=<test-node>` + `make project-list NODE=<test-node>` + `make project-status NAME=<test-project> NODE=<test-node>` — все 3 проходят без hang. Документировано в VerificationReport.
    7. Unit-тест `tests/test_lib_ssh.py` покрывает: ssh_exec с timeout=1 на unreachable host → return 124, ssh_exec на reachable host → return 0, ssh_read default timeout=60, dry_run mode → echo без exec, SSH_OPTS_COMMON immutability.
    8. Документация: TRAP[DECISION] в `core/lib/ssh.sh` фиксирует выбор timeout-дефолтов (600s deploy / 60s read) с rationale (remote-deploy = rsync/docker-pull с ServerAliveCountMax=10 × 30s = 5 мин safe-margin; read-only = короткие команды типа docker ps).
  **B. setup-platform composite-action (W2-E2):**
    9. `.github/actions/setup-platform/action.yml` создан как composite-action с inputs: `python-version` (default 3.10), `install-gitleaks` (default true), `run-provisioner` (default false), `provisioner-scope` (default all). Шаги: checkout → setup-python-venv (reuse существующего) → setup-gitleaks (reuse) → [optional] provisioner-call (reuse).
    10. Минимум 7 из 9 workflows мигрированы: приоритет — `push-gate.yml`, `nightly-gate.yml`, `platform-test.yml`, `deploy-project.yml`, `stage-deploy.yml`, `core-deploy.yml`, `platform-deploy.yml`. Каждый workflow заменяет multi-step последовательность одним `uses: ./.github/actions/setup-platform`.
    11. `rg "actions/checkout@v" .github/workflows/` → ≤3 вхождения (whitelist для mirror.yml и build-platform.yml — workflows со специфическим checkout-config, которые остаются на inline checkout; platform-deploy.yml включён в миграцию, его 3 checkout'а заменены composite-action).
    12. CI execution time замерен до и после: `reports/ci-composite-impact-2026-07.csv` с колонками `workflow, before_sec, after_sec, delta_sec, runs_averaged, has_python`. Минимум −15s на каждый workflow с python (push-gate, nightly-gate, platform-test — цель −30s; если меньше — зафиксировать как partial-success с rationale). Workflows без python (deploy-project, stage-deploy, core-deploy, platform-deploy) — delta может быть ~0s, профит в unification (reduced DRIFT-risk).
    13. Cache-key strategy сохранена: composite-action наследует `venv-${{ runner.os }}-${{ hashFiles('Makefile') }}` из setup-python-venv (без инвалидации существующих cache'ей — R-RISK-10 mitigation).
  **C. audit-trail wrapper (W2-E3):**
    14. `core/lib/audit_logging.sh` расширен wrapper-функцией `audit_step()`: signature `audit_step(step_name, command...)` — 1) эмитит `audit_log "${step_name}" "START" "$*"` синхронно перед exec; 2) выполняет команду в текущем shell, capture `$?` в переменную `_audit_rc`; 3) если `_audit_rc -eq 0` → `audit_log "${step_name}" "DONE" "$*"`; иначе `audit_log "${step_name}" "FAIL" "exit=${_audit_rc}" "$*"`. **Дизайн-решение (VerificationReport 03 DRIFT-7 fix): wrapper-style без trap-on-EXIT** — bash EXIT trap fires на всех выходах (включая успех), что делает дедупликацию START+FAIL невозможной без дополнительной машинерии. Wrapper-style: явный capture rc → условный emit DONE/FAIL. Exit-code propagation сохранён (`return ${_audit_rc}`). Никакого subshell, никакого trap.
    15. Применён в 7 entrypoints:
        - `core/entrypoints/context-promote.sh` — audit_step "context-promote:${CONTEXT}" на старте main-flow.
        - `core/internal/scaffold/remove-project.sh` — audit_step "remove-project:${NAME}" (с NODE в сообщении).
        - `core/internal/provision-environment.sh` — audit_step "provision:${SCOPE}".
        - `core/entrypoints/build.sh` — audit_step "hermes-build:${MODE}:${CONTEXT}".
        - `core/internal/secrets/decrypt-secrets.sh` — audit_step "secrets-unlock:${NODE}".
        - `core/internal/bootstrap/node-lifecycle.sh` — audit_step "node-update:${NODE}" в update-mode (init-mode уже имеет audit-summary на step 16).
        - `core/internal/deploy/deploy-project.sh` — audit_step "core-deploy:${NODE}".
    16. `rg "audit_step\b" core/` → ≥7 вхождений (по одному на entrypoint). `rg "audit_log\b" core/` → ≥14 новых вхождений (START + DONE на каждый из 7 entrypoints).
    17. Staging-тест: после запуска 3 entrypoints на тестовой ноде `sudo tail /var/log/platform/audit.log | grep -E "(START|DONE|FAIL)"` показывает ≥6 записей (3 × 2) с ISO8601 timestamps и step-names. `sudo` обязателен — audit.log создаётся с режимом 0664/root:adm, ci-deploy должен быть в group adm или использовать sudo (задокументировать в staging-node инструкции).
    18. Unit-тест `tests/test_audit_step.py` покрывает: audit_step с успешной командой → 2 записи (START + DONE, без FAIL), audit_step с failing командой → 2 записи (START + FAIL, без DONE) + exit-code propagated, audit_step с timeout-командой → START + FAIL с exit=124. Wrapper-style без trap: после exec capture `$?`, условный emit DONE (=0) или FAIL (≠0).
  **Cross-cutting:**
    19. `make gate MODE=fast` — зелёный после всех изменений (с учётом Wave 1 honesty-fix: skip→fail может давать fail на CI без Docker — должен быть уже stabilised в Wave 1 `REQUIRE_HONESTY_MODE=marker`).
    20. Все новые Python-файлы (`tests/test_lib_ssh.py`, `tests/test_audit_step.py`) проходят `ruff check` + `ruff format --check`.
    21. Все новые shell-файлы (`core/lib/ssh.sh`, расширения `audit_logging.sh`) проходят `shellcheck` (Makefile lint-target).
    22. `core/entrypoint-manifest.yaml` обновлён: регистрация `core/lib/ssh.sh` и `core/lib/audit_logging.sh` (audit_step extension) в новой секции `lib:` (создаётся в Wave 2 — ранее manifest не имел lib-секции; pattern: `type: sourced, consumers: [<list>]`), новые gate-тесты в секции gates.
    23. TRAP[DECISION] в root `AGENTS.md` (после языковой политики) фиксирует выбор staging-gate для SSH-фасада: rationale «single point of failure для всех remote-операций — staging-test обязателен перед merge, revert-path документирован».
    24. VerificationReport `03-VerificationReport.md` содержит: staging-test результаты для всех 6 entrypoints (SSH + audit), CI before/after timing, regression test results.
IMPLEMENTS:            Brief 027 §4 (Wave 2 эпики W2-E1…W2-E3), §9 Risk Register (R-RISK-1 staging-gate, R-RISK-8 dual-write audit, R-RISK-10 cache-strategy), §10 KPI (SSH-вызовов без timeout → 0, Audit-trail покрытие 2/9 → 9/9, Duplicated CI steps 10 → ≤3). AGENTS.md invariants 1 (Makefile-фасад — entrypoints вызывают ssh.sh через source lib/), 2 (deploy-model — SSH как часть core-delivery), 8 (AI-First Architecture — единый SSH-контракт вместо 6 разбросанных). Principles 6 (Small Simple Blocks — facade ≤80 строк), 8 (модульные границы — lib/ssh.sh как domain-boundary для remote-operations), 9 (Read before Act — codebase верифицирован 2026-07-21, обнаружены 2 secondary-файла сверх брифа). Skills: doc-protocols (этот DevPlan), arch-forensics (исходный анализ).
IMPACTS:               **New lib:** `core/lib/ssh.sh` (~80 строк: SSH_OPTS_COMMON + ssh_exec + ssh_read + ssh_exec_dry_run + TRAP[DECISION]). **Extended lib:** `core/lib/audit_logging.sh` (+30 строк: audit_step wrapper — wrapper-style, без trap). **New composite-action:** `.github/actions/setup-platform/action.yml` (~40 строк: 4 step-reuse). **New Python tests:** `tests/test_lib_ssh.py` (unit-тесты ssh_exec/ssh_read/dry_run), `tests/test_audit_step.py` (unit-тесты audit_step wrapper). **Modified shell:** `core/internal/bootstrap/scp-deliver.sh` (source lib/ssh.sh, ssh_exec вместо inline ssh), `core/internal/bootstrap/remote-cmd.sh` (4 source-points → единый через ssh_exec), `core/internal/scaffold/remove-project.sh` (ssh_exec в cleanup-flow + source audit_logging.sh), `core/internal/scaffold/project-list.sh` (ssh_read вместо inline ssh), `core/lib/vps-readiness.sh` (4 inline ssh → ssh_read timeout=30; severity MED — ConnectTimeout=10 уже присутствует), `core/internal/deploy/reconcile-projects.sh` (2 inline ssh → ssh_read), `core/internal/provision-environment.sh` (audit_step + source audit_logging.sh), `core/internal/secrets/decrypt-secrets.sh` (audit_step + source audit_logging.sh), `core/internal/deploy/deploy-project.sh` (audit_step — уже имеет source), `core/entrypoints/context-promote.sh` (audit_step + source audit_logging.sh), `core/entrypoints/build.sh` (audit_step + source audit_logging.sh), `core/internal/bootstrap/node-lifecycle.sh` (audit_step в update-mode + source audit_logging.sh + удаление существующего `audit_log "node-update:complete"` на line 1289). **Modified CI:** `.github/workflows/push-gate.yml`, `nightly-gate.yml`, `platform-test.yml`, `deploy-project.yml`, `stage-deploy.yml`, `core-deploy.yml`, `platform-deploy.yml` (7 миграций на composite-action). **AGENTS.md:** TRAP[DECISION] о staging-gate для SSH-фасада (после языковой политики). **core/entrypoint-manifest.yaml:** +lib-секция (создаётся; содержит `core/lib/ssh.sh` и `core/lib/audit_logging.sh`), +2 gate-теста в секции gates. **Reports:** `reports/ci-composite-impact-2026-07.csv` (CI before/after timing).
REQUIRES:              Завершённая Wave 1 + её production-релиз (нужны: честные тесты для staging-валидации, lib/args.sh как precedent для нового lib-файла). **VerificationReport 03 (2026-07-21) отмечает:** `reports/baseline-metrics-2026-07.csv` из W1-E8 не существует — baseline-замеры CI должны быть выполнены ДО старта Wave 2 (W1-E8 prerequisite) или AC10 (CI before/after timing) явно descope'ирован с rationale. Чистый working tree на момент старта. Staging-нода для SSH-фасад testing (тестовый сервер, пересоздаваемый — инвариант 9). Доступ к GitHub Actions для замера CI execution time (или last-10-runs averages из API). Перед стартом: архитектор ОБЯЗАН прочитать `reports/architecture-analysis-2026-07-21.md` §P02/P10/P11/P15, Brief 027 §4, VerificationReport Wave 1 (если есть), и **настоящий VerificationReport 03** (аудит выявил 11 drift-находок, все исправлены в этой версии DevPlan). Порядок выполнения: E1 (SSH-фасад) → staging-test → merge → E2 (CI composite, параллельно с E1 возможен) → E3 (audit-trail, после E1 — нужен стабильный SSH для end-to-end entrypoint проверки) → production-релиз.
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Зафиксировать эпики и порядок выполнения (E1 → E2 ∥ E1 → E3 после E1) => GOAL_ORDER
- GOAL Описать Draft Code Graph для lib/ssh.sh, audit_step wrapper, composite-action => GOAL_GRAPH
- GOAL Описать step-by-step data flow для ssh_exec (timeout-wrapping, dry-run, exit-124 detection) => GOAL_SSH
- GOAL Описать audit_step wrapper-семантику (wrapper-style без trap: START → exec → capture $? → DONE/FAIL emit) => GOAL_AUDIT
- GOAL Зафиксировать File Manifest (CREATE/MODIFY) с приоритетами миграции => GOAL_MANIFEST
- GOAL Определить Acceptance Criteria с verifiable commands (rg, staging-tests) => GOAL_AC
- GOAL Зафиксировать risks (R-RISK-1, R-RISK-8, R-RISK-10) и staging-gate mitigation => GOAL_RISK
- GOAL Оценить effort по эпикам с учётом staging-test overhead => GOAL_EFFORT
- GOAL Определить revert-strategy для каждого эпика => GOAL_REVERT
**SECTION_USE_CASES:**
- USE_CASE CI runner запускает deploy → ssh_exec(timeout=600) → если remote зависает, через 600s return 124, CI red (вместо бесконечного hang) => UC_SSH_TIMEOUT
- USE_CASE Разработчик добавляет новый entrypoint с remote-CMD → source lib/ssh.sh → ssh_exec вместо ssh -i ... inline → единый timeout + audit => UC_SSH_FACADE
- USE_CASE Оператор investigate post-mortem после failed deploy → tail /var/log/platform/audit.log → видит START без DONE/FAIL на конкретном entrypoint → локализует hang => UC_AUDIT_FORENSIC
- USE_CASE CI workflow использует setup-platform composite → −30s на каждый run + единый cache-key => UC_CI_COMPOSITE
- USE_CASE Staging-test перед merge SSH-фасада → make converge + project-list + project-status на test-node → green → merge (R-RISK-1 mitigation) => UC_STAGING_GATE
$END_DOCUMENT_PLAN
```

---

## Draft Code Graph (XML)

```xml
<graph>
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <!-- A. lib/ssh.sh — единый SSH-фасад                                      -->
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <entity id="lib_ssh_sh" type="SHELL_LIB" layer="core/lib/ssh.sh">
    <purpose>Единая SSH-фасадная функция с timeout-wrapper — single source of truth для всех remote-operations</purpose>
    <public_api>
      SSH_OPTS_COMMON — readonly const array (-o BatchMode=yes -o StrictHostKeyChecking=accept-new
                                              -o ConnectTimeout=30 -o ServerAliveInterval=30
                                              -o ServerAliveCountMax=10)
      ssh_exec(host, user, command, [timeout=600], [mode=deploy]) → int  # remote-cmd execution
      ssh_read(host, user, command, [timeout=60]) → int                  # read-only short cmds
      ssh_exec_dry_run(host, user, command, [timeout]) → int             # echo-mode для --dry-run
    </public_api>
    <deps>core/lib/logging.sh (log_imp), core/lib/paths.sh (PLATFORM_ROOT)</deps>
    <exit_codes>
      0   = success
      124 = timeout (от timeout-wrapper)
      1-3 = ssh native exit codes (propagated)
    </exit_codes>
    <invariants>
      - Каждый ssh_exec/ssh_read вызов обёрнут в `timeout`
      - exit=124 детектируется явно → log_imp 1 "SSH timeout"
      - SSH_OPTS_COMMON — readonly (защита от случайной мутации)
      - mode=deploy default 600s, mode=read default 60s
    </invariants>
  </entity>

  <entity id="trap_decision_ssh_timeouts" type="TRAP" layer="core/lib/ssh.sh">
    <annotation>⚠️ TRAP[DECISION] · 2026-07-21 · HI · Timeout-дефолты 600s deploy / 60s read</annotation>
    <rationale>
      remote-deploy (rsync/docker-pull/converge): ServerAliveCountMax=10 × ServerAliveInterval=30s = 5 мин safe-margin.
      600s = 10 мин ≈ 2× safe-margin для длинных docker-pull на медленных каналах.
      read-only (docker ps, project-list): короткие команды, 60s = 2× типичного времени ответа.
    </rationale>
    <rejected>Единый timeout=300 (риск: прерывание длинных rsync на медленных каналах)</rejected>
    <rev>если CI-deploy стабильно < 300s → снизить deploy-default до 400s</rev>
  </entity>

  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <!-- B. Миграция 4 primary + 2 secondary потребителей                      -->
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <entity id="scp_deliver_migration" type="SHELL_REFACTOR" layer="core/internal/bootstrap/scp-deliver.sh">
    <before>prepare_ssh_opts() строит SSH_OPTS array; ssh "${SSH_OPTS[@]}" inline в 3 местах</before>
    <after>source lib/ssh.sh; prepare_ssh_opts() ДЕПРЕЦИРУЕТСЯ (алиас на SSH_OPTS_COMMON); rsync через ssh_exec с mode=deploy</after>
    <crosslinks>
      <link target="lib_ssh_sh" relation="source" />
      <link target="remote_cmd_migration" relation="sibling" />
    </crosslinks>
    <note>rsync использует `-e "ssh ${SSH_OPTS_COMMON[*]}"` — ssh_exec неприменим к rsync напрямую, нужен ssh-opts-passing</note>
  </entity>

  <entity id="remote_cmd_migration" type="SHELL_REFACTOR" layer="core/internal/bootstrap/remote-cmd.sh">
    <before>4 source-points scp-deliver.sh; SSH_OPTS array; inline ssh "${SSH_OPTS[@]}" в 4 местах</before>
    <after>source lib/ssh.sh; 4 inline-ssh → ssh_exec; dry-run mode → ssh_exec_dry_run</after>
    <crosslinks>
      <link target="lib_ssh_sh" relation="source" />
      <link target="scp_deliver_migration" relation="parent" />
    </crosslinks>
  </entity>

  <entity id="remove_project_migration" type="SHELL_REFACTOR" layer="core/internal/scaffold/remove-project.sh">
    <before>SSH_OPTS inline; ssh "${SSH_OPTS[@]}" в cleanup-flow (manual-step hints)</before>
    <after>source lib/ssh.sh; ssh_exec для actual remote-cleanup</after>
  </entity>

  <entity id="project_list_migration" type="SHELL_REFACTOR" layer="core/internal/scaffold/project-list.sh">
    <before>inline ssh для project-list + --status</before>
    <after>ssh_read (timeout=60, read-only)</after>
  </entity>

  <entity id="vps_readiness_migration" type="SHELL_REFACTOR" layer="core/lib/vps-readiness.sh">
    <before>4 inline `ssh -i ${ci_key} -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10` на lines 82, 96, 111, 127 — connection-phase ограничен 10s, но без timeout-wrapper на remote-CMD и без единого фасада</before>
    <after>ssh_read(host, "ci-deploy", cmd, timeout=30) — короткие read-only пробы, единый фасад</after>
    <severity>MED — ConnectTimeout=10 уже защищает от бесконечного connection-hang; миграция даёт unification (единый source of truth вместо inline-дисперсии) + timeout-wrapper на весь remote-CMD</severity>
  </entity>

  <entity id="reconcile_projects_migration" type="SHELL_REFACTOR" layer="core/internal/deploy/reconcile-projects.sh">
    <before>2 inline ssh на lines 219, 231 — с `-o ConnectTimeout=10`, но без timeout-wrapper на remote-CMD и без единого фасада</before>
    <after>ssh_read (read-only probe, единый фасад)</after>
  </entity>

  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <!-- C. setup-platform composite-action                                     -->
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <entity id="setup_platform_composite" type="GH_COMPOSITE_ACTION" layer=".github/actions/setup-platform/action.yml">
    <purpose>Объединяет checkout + setup-python-venv + setup-gitleaks + [optional] provisioner-call</purpose>
    <inputs>
      python-version (default 3.10) → проброс в setup-python-venv
      install-gitleaks (default true) → условный step setup-gitleaks
      run-provisioner (default false) → условный step provisioner-call
      provisioner-scope (default all) → проброс в provisioner-call
    </inputs>
    <reuses>
      <link target="setup_python_venv" relation="include" />
      <link target="setup_gitleaks" relation="include" />
      <link target="provisioner_call" relation="include" />
    </reuses>
    <invariants>
      - cache-key наследуется из setup-python-venv (без инвалидации — R-RISK-10)
      - gitleaks/provisioner опциональны (не всем workflows нужны)
    </invariants>
  </entity>

  <entity id="workflow_migration_target" type="GH_WORKFLOW_REFACTOR" layer=".github/workflows/*.yml">
    <pattern>
      ЗАМЕНА:
        - uses: actions/checkout@v7
        - uses: ./.github/actions/setup-python-venv
        - uses: ./.github/actions/setup-gitleaks
        - uses: ./.github/actions/provisioner-call
      НА:
        - uses: ./.github/actions/setup-platform
          with:
            python-version: '3.10'
            install-gitleaks: true
            run-provisioner: false
    </pattern>
    <priority_order>push-gate, nightly-gate, platform-test, deploy-project, stage-deploy, core-deploy, platform-deploy</priority_order>
    <whitelist>workflows с специфическим checkout-config (fetch-depth: 0, path-filtering) остаются на inline checkout, но python+gitleaks через composite</whitelist>
  </entity>

  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <!-- D. audit_step wrapper                                                  -->
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <entity id="audit_step_wrapper" type="SHELL_FUNC" layer="core/lib/audit_logging.sh">
    <purpose>Wrapper для обёртки entrypoint-команд с START/DONE/FAIL emit в audit.log</purpose>
    <signature>audit_step(step_name, command...) → int (propagated command exit code)</signature>
    <semantics>
      1. Emit audit_log(step_name, "START", command-preview) — синхронно до exec
      2. Execute command in current shell, capture $? в _audit_rc
      3. if _audit_rc -eq 0 → audit_log(step_name, "DONE", command-preview)
      4. if _audit_rc -ne 0 → audit_log(step_name, "FAIL", "exit=${_audit_rc}", command-preview)
      5. return ${_audit_rc} (propagate original exit code)
    </semantics>
    <design_rationale>Wrapper-style без trap-on-EXIT. Bash EXIT trap fires на всех выходах (включая успех), что делает дедупликацию START+FAIL невозможной. Wrapper-style: явный capture rc → условный emit. Проще, без subshell overhead, без trap-scope проблем.</design_rationale>
    <invariants>
      - Никакого subshell, никакого trap — чистый wrapper
      - START emit синхронный (до exec), DONE/FAIL — после exec через existing audit_log() (dual-write: logger + printf >>)
      - command-preview truncated до 200 chars (избежать огромных audit entries)
      - Exit-code propagation: return code команды = return code wrapper'а
    </invariants>
    <crosslinks>
      <link target="audit_log_func" relation="depends-on" />
    </crosslinks>
  </entity>

  <entity id="audit_entrypoint_integration" type="SHELL_REFACTOR" layer="7 entrypoints">
    <targets>
      context-promote.sh, remove-project.sh, provision-environment.sh,
      build.sh, decrypt-secrets.sh, node-lifecycle.sh (update-mode),
      deploy-project.sh (core-deploy path)
    </targets>
    <prerequisite>6 из 7 entrypoints НЕ имеют `source audit_logging.sh` (только deploy-project.sh:69 source'ит). Перед audit_step integration: добавить `source "${CORE_DIR}/lib/audit_logging.sh"` в каждом entrypoint (кроме deploy-project.sh). node-lifecycle.sh требует также удаления существующего `audit_log "node-update:complete"` на line 1289 (избежать double-emit).</prerequisite>
    <pattern>
      ДО: main-flow() { ...команды... }
      ПОСЛЕ: main-flow() {
        source "${CORE_DIR}/lib/audit_logging.sh"
        audit_step "entrypoint-name:${param}" actual_command arg1 arg2
      }
    </pattern>
  </entity>
</graph>
```

---

## Step-by-Step Data Flow

### Data Flow 1: `ssh_exec` execution path (W2-E1)

```
[Caller] deploy-project.sh needs remote `docker compose -p X down`
    │
    ▼
[ssh_exec(host, "ci-deploy", cmd, timeout=600, mode=deploy)]
    │
    ├─ if [[ "${DRY_RUN:-}" == "1" ]] → delegate to ssh_exec_dry_run()
    │       │
    │       └─ echo "[IMP:8][ssh][dry-run] ssh ci-deploy@host 'cmd'" → return 0
    │
    ├─ Validate inputs (host non-empty, cmd non-empty, timeout is int)
    │       │
    │       └─ fail-fast: log_imp 1 + return 2 if invalid
    │
    ├─ Construct: timeout "${timeout}" ssh "${SSH_OPTS_COMMON[@]}" "${user}@${host}" "${cmd}"
    │
    ├─ Execute (capture exit code)
    │       │
    │       ├─ exit=0   → log_imp 9 "[ssh][ok]" → return 0
    │       ├─ exit=124 → log_imp 1 "[ssh][timeout] host=${host} timeout=${timeout}s" → return 124
    │       └─ exit=*   → log_imp 7 "[ssh][fail] rc=${rc}" → return ${rc}
    │
    ▼
[Caller] receives exit code, propagates to make-target
```

### Data Flow 2: `audit_step` wrapper (W2-E3) — wrapper-style, NO trap

```
[Entrypoint] context-promote.sh main flow
    │
    ▼
[audit_step("context-promote:prod", promote_to_context, "prod")]
    │
    ├─ Emit audit_log("context-promote:prod", "START", "promote_to_context prod")
    │       │
    │       └─ logger -t platform-audit "ISO | context-promote:prod | START | ..."
    │       └─ printf >> /var/log/platform/audit.log
    │
    ├─ Execute: promote_to_context "prod"  (in current shell, NO subshell)
    │       │
    │       └─ Capture: _audit_rc=$?
    │
    ├─ if [[ ${_audit_rc} -eq 0 ]]
    │       └─ audit_log("context-promote:prod", "DONE", preview)  → 1 запись
    │
    ├─ if [[ ${_audit_rc} -ne 0 ]]
    │       └─ audit_log("context-promote:prod", "FAIL", "exit=${_audit_rc}", preview)  → 1 запись
    │
    └─ return ${_audit_rc}
```

**⚠️ Design rationale (VerificationReport 03 DRIFT-7 fix):** bash EXIT-trap fires on ALL exits including success — trap-based подход требует дедупликации (trap эмитит FAIL даже при exit 0) и даёт ложные срабатывания. Wrapper-style проще: явный capture `$?` → условный emit DONE (=0) или FAIL (≠0). Никакого subshell-overhead (~1ms), никакого trap-scope leaking. Итого: успешная команда генерирует 2 записи (START + DONE), failing — 2 записи (START + FAIL).

### Data Flow 3: CI composite-action substitution (W2-E2)

```
[Workflow] push-gate.yml (BEFORE)
    │
    ├─ - uses: actions/checkout@v7
    ├─ - uses: ./.github/actions/setup-python-venv
    ├─ - uses: ./.github/actions/setup-gitleaks
    ├─ - run: make gate MODE=fast
    │   (4 setup steps, ~30s overhead)
    │
    ▼
[AFTER]
    │
    ├─ - uses: ./.github/actions/setup-platform
    │     with:
    │       python-version: '3.10'
    │       install-gitleaks: true
    │       run-provisioner: false
    ├─ - run: make gate MODE=fast
    │   (1 setup step, composite internally reuses 3 actions with shared cache)
    │
    ▼
[Cache behavior]
    venv-cache key: venv-${{ runner.os }}-${{ hashFiles('Makefile') }}
    → НЕ инвалидируется (R-RISK-10 mitigation) — composite наследует тот же key
```

---

## File Manifest

### CREATE (новые файлы)

| Файл | Назначение | LOC | Эпик |
|------|-----------|-----|------|
| `core/lib/ssh.sh` | Единый SSH-фасад с timeout-wrapper | ~80 | W2-E1 |
| `.github/actions/setup-platform/action.yml` | Composite-action (checkout + python + gitleaks + provisioner) | ~40 | W2-E2 |
| `tests/test_lib_ssh.py` | Unit-тесты ssh_exec/ssh_read/dry_run | ~120 | W2-E1 |
| `tests/test_audit_step.py` | Unit-тесты audit_step wrapper | ~100 | W2-E3 |
| `reports/ci-composite-impact-2026-07.csv` | CI before/after timing | — | W2-E2 |

### MODIFY (изменяемые файлы)

| Файл | Изменение | Эпик |
|------|-----------|------|
| `core/lib/audit_logging.sh` | +`audit_step()` wrapper (~30 строк) | W2-E3 |
| `core/internal/bootstrap/scp-deliver.sh` | source lib/ssh.sh; ssh_exec вместо inline ssh; deprecate prepare_ssh_opts | W2-E1 |
| `core/internal/bootstrap/remote-cmd.sh` | source lib/ssh.sh; 4 inline-ssh → ssh_exec | W2-E1 |
| `core/internal/scaffold/remove-project.sh` | ssh_exec для remote-cleanup | W2-E1 |
| `core/internal/scaffold/project-list.sh` | ssh_read (timeout=60) | W2-E1 |
| `core/lib/vps-readiness.sh` | 4 inline-ssh → ssh_read(timeout=30); severity MED (ConnectTimeout=10 уже присутствует) | W2-E1 |
| `core/internal/deploy/reconcile-projects.sh` | 2 inline-ssh → ssh_read | W2-E1 |
| `core/entrypoints/context-promote.sh` | audit_step wrapper в main-flow | W2-E3 |
| `core/internal/scaffold/remove-project.sh` | audit_step wrapper (дополнительно к ssh_exec) | W2-E3 |
| `core/internal/provision-environment.sh` | audit_step wrapper | W2-E3 |
| `core/entrypoints/build.sh` | audit_step wrapper | W2-E3 |
| `core/internal/secrets/decrypt-secrets.sh` | audit_step wrapper | W2-E3 |
| `core/internal/bootstrap/node-lifecycle.sh` | audit_step в update-mode + source audit_logging.sh + удаление существующего `audit_log "node-update:complete"` line 1289 | W2-E3 |
| `core/internal/deploy/deploy-project.sh` | audit_step в core-deploy path | W2-E3 |
| `.github/workflows/push-gate.yml` | → uses: setup-platform | W2-E2 |
| `.github/workflows/nightly-gate.yml` | → uses: setup-platform | W2-E2 |
| `.github/workflows/platform-test.yml` | → uses: setup-platform | W2-E2 |
| `.github/workflows/deploy-project.yml` | → uses: setup-platform | W2-E2 |
| `.github/workflows/stage-deploy.yml` | → uses: setup-platform | W2-E2 |
| `.github/workflows/core-deploy.yml` | → uses: setup-platform | W2-E2 |
| `.github/workflows/platform-deploy.yml` | → uses: setup-platform (3 checkout'а заменены composite) | W2-E2 |
| `core/entrypoint-manifest.yaml` | +lib-секция (создаётся; ssh.sh + audit_logging.sh), +2 gate-теста | Cross |
| `AGENTS.md` | TRAP[DECISION] о staging-gate для SSH-фасада | Cross |

---

## Acceptance Criteria (verifiable commands)

| AC# | Verification command | Expected |
|-----|---------------------|----------|
| AC1 | `test -f core/lib/ssh.sh && rg "ssh_exec\(\)" core/lib/ssh.sh` | 1 match |
| AC2 | `rg "timeout\s+\"\$\{" core/lib/ssh.sh` | ≥1 (dynamic timeout in ssh_exec) |
| AC3 | `rg "ssh\s+-i\s" core/ --type sh` | 0 matches |
| AC4 | `rg "ConnectTimeout" core/ --type sh` | все вхождения внутри `core/lib/ssh.sh` (SSH_OPTS_COMMON), либо в `${SSH_OPTS[*]:-...}` fallback-branches (scp-deliver.sh:131, remote-cmd.sh; legacy, не активируются при source lib/ssh.sh — документированное исключение, удаляются в Wave 3) |
| AC5 | `python3 -m pytest tests/test_lib_ssh.py -v` | all green |
| AC6 | `make converge NODE=<test-node> && make project-list NODE=<test-node> && make project-status NAME=<test-project> NODE=<test-node>` | staging-test green, no hang |
| AC7 | `test -f .github/actions/setup-platform/action.yml` | exists |
| AC8 | `rg "uses:.*setup-platform" .github/workflows/` | ≥7 matches |
| AC9 | `rg "actions/checkout@v" .github/workflows/` | ≤3 matches |
| AC10 | `test -f reports/ci-composite-impact-2026-07.csv && head -8 reports/ci-composite-impact-2026-07.csv` | ≥7 workflows with delta_sec + has_python column |
| AC11 | `rg "audit_step\b" core/lib/audit_logging.sh` | 1 function definition |
| AC12 | `rg "audit_step\b" core/` | ≥7 matches (entrypoint usage) |
| AC13 | `python3 -m pytest tests/test_audit_step.py -v` | all green |
| AC14 | Staging: `sudo tail /var/log/platform/audit.log \| grep -cE "(START\|DONE\|FAIL)"` после 3 entrypoints | ≥6 |
| AC15 | `make gate MODE=fast` | green |
| AC16 | `ruff check tests/test_lib_ssh.py tests/test_audit_step.py && ruff format --check tests/test_lib_ssh.py tests/test_audit_step.py` | no errors |
| AC17 | `shellcheck core/lib/ssh.sh core/lib/audit_logging.sh` | no errors |
| AC18 | `rg "TRAP\[DECISION\]" AGENTS.md` | содержит SSH staging-gate decision |

---

## Risk Mitigation & Revert Strategy

### R-RISK-1 (impact H): SSH-фасад ломает remote-CMD

**Single point of failure для ВСЕХ remote-операций** (deploy, bootstrap, healthcheck, node-update, converge, project-list/status, remove-project, verify).

**Mitigation:**
1. **Staging-gate обязателен перед merge.** Тестовая нода (пересоздаваемая — инвариант 9): полный цикл `make converge` + `make project-list` + `make project-status` + `make bootstrap-node` (init) + `make node-update`.
2. **Feature-branch с PR review.** Не fast-forward merge — explicit merge-commit для audit-trail.
3. **Backward-compat shim:** `prepare_ssh_opts()` в scp-deliver.sh сохраняется как deprecated-алиас на 1 release-cycle (делегирует в SSH_OPTS_COMMON). Удаляется в Wave 3.
4. **Быстрый revert-path:** git revert merge-commit + redeploy через `make bootstrap-node` (SCP/rsync доставит старую версию lib/).

**Detection signal:** staging-test red → блокирует merge. CI red после merge → immediate revert + post-mortem.

### R-RISK-8 (impact L): audit-overhead

**Mitigation:** dual-write через `logger -t platform-audit` + `printf >> /var/log/platform/audit.log` уже реализован в существующей `audit_log()` (belt-and-suspenders: logger даёт syslog-интеграцию, file-append — гарантированную доставку). `audit_step` wrapper добавляет только 1 extra `audit_log` call (START) + conditional emit (DONE или FAIL). На 7 entrypoints × ~3 runs/day = 21 audit writes/day — negligible. Wrapper-style без subshell — overhead ~1ms на вызов.

### R-RISK-10 (impact L): CI composite ломает кеширование

**Mitigation:**
1. Cache-key наследуется из setup-python-venv без изменений: `venv-${{ runner.os }}-${{ hashFiles('Makefile') }}`.
2. Baseline-замер ДО миграции (из GitHub Actions API last-10-runs; если W1-E8 baseline-metrics.csv существует — использовать его; иначе — собрать fresh baseline перед миграцией).
3. Post-migration замер через 10 runs → если degradation > 10s — investigate cache-miss.
4. Whitelist для workflows со специфическим checkout-config — они остаются на inline steps (избегают сюрпризов с path-filtering).

**Detection signal:** `reports/ci-composite-impact-2026-07.csv` показывает negative delta (CI стал медленнее) → rollback конкретного workflow на inline steps.

---

## Execution Order

```
W2-E1 (lib/ssh.sh + миграция 6 файлов)
    │
    ├─ Step 1: CREATE core/lib/ssh.sh (SSH_OPTS_COMMON + ssh_exec + ssh_read + dry_run + TRAP)
    ├─ Step 2: CREATE tests/test_lib_ssh.py (unit-тесты)
    ├─ Step 3: MODIFY scp-deliver.sh (source + deprecate prepare_ssh_opts)
    ├─ Step 4: MODIFY remote-cmd.sh (4 inline-ssh → ssh_exec)
    ├─ Step 5: MODIFY remove-project.sh (ssh_exec)
    ├─ Step 6: MODIFY project-list.sh (ssh_read)
    ├─ Step 7: MODIFY vps-readiness.sh (4 inline → ssh_read timeout=30)
    ├─ Step 8: MODIFY reconcile-projects.sh (2 inline → ssh_read)
    ├─ Step 9: unit-тесты green, shellcheck clean
    ├─ Step 10: STAGING-GATE — make converge + project-list + project-status + bootstrap + node-update на test-node
    └─ Step 11: merge в feature-branch → PR review → merge в main

W2-E2 (CI composite) — ПАРАЛЛЕЛЬНО с E1 (после Step 2)
    │
    ├─ Step 1: CREATE .github/actions/setup-platform/action.yml
    ├─ Step 2: Baseline-замер CI (last-10-runs per workflow)
    ├─ Step 3: MODIFY push-gate.yml → composite
    ├─ Step 4: MODIFY nightly-gate.yml → composite
    ├─ Step 5: MODIFY platform-test.yml → composite
    ├─ Step 6: MODIFY deploy-project.yml → composite
    ├─ Step 7: MODIFY stage-deploy.yml → composite
    ├─ Step 8: MODIFY core-deploy.yml → composite
    ├─ Step 9: MODIFY platform-deploy.yml → composite (3 checkout'а заменены)
    ├─ Step 10: Post-migration замер (10 runs)
    └─ Step 11: CREATE reports/ci-composite-impact-2026-07.csv

W2-E3 (audit-trail) — ПОСЛЕ E1 (нужен стабильный SSH для end-to-end)
    │
    ├─ Step 1: MODIFY core/lib/audit_logging.sh (+audit_step wrapper — wrapper-style, без trap, ~30 строк)
    ├─ Step 2: CREATE tests/test_audit_step.py
    ├─ Step 3: Предварительный подшаг — добавить `source "${CORE_DIR}/lib/audit_logging.sh"` в 6 entrypoints, которые его не имеют (context-promote.sh, remove-project.sh, provision-environment.sh, build.sh, decrypt-secrets.sh, node-lifecycle.sh; deploy-project.sh уже имеет source на line 69)
    ├─ Step 4: MODIFY 7 entrypoints (audit_step integration)
    ├─ Step 5: В node-lifecycle.sh — удалить существующий `audit_log "node-update:complete" "DONE"` на line 1289 (заменяется audit_step wrapper'ом, избежать double-emit)
    ├─ Step 6: unit-тесты green
    ├─ Step 7: STAGING-TEST — 3 entrypoints на test-node, verify audit.log с sudo
    └─ Step 8: merge

PRODUCTION-RELEASE (post-W2-E3)
    │
    ├─ Step 1: merge feature-branch в main
    ├─ Step 2: deploy на production-ноду через `make bootstrap-node NODE=<prod>`
    ├─ Step 3: verify audit.log на production (tail после первого remote-deploy)
    ├─ Step 4: VerificationReport 03-VerificationReport.md
    └─ Step 5: обновить Brief 027 (link на DevPlan + VerificationReport)
```

---

## Effort Estimate

| Эпик | Оценка (dev-days) | Включая |
|------|------------------|---------|
| W2-E1 lib/ssh.sh + миграция | 5-7 | unit-тесты, staging-test overhead, 6 файлов миграции |
| W2-E2 CI composite | 2-3 | action.yml + 7 workflow миграций + baseline/post замеры |
| W2-E3 audit-trail | 3-4 | wrapper + 7 entrypoints + source-audit_logging + node-lifecycle cleanup + unit-тесты + staging-verify |
| Production-release + VerificationReport | 1-2 | merge, deploy, post-deploy verify, report |
| **Итого** | **11-16 dev-days** | ~3-4 недели при 4 dev-days/week |

---

## Anti-goals (что НЕ делается в Wave 2)

- ❌ Миграция всех 14 файлов с ssh-вхождениями — только 6 primary/secondary (остальные — setup-node.sh, node-lifecycle.sh, deploy-project.sh entrypoint — обрабатываются в Wave 4 при Strangler-декомпозиции).
- ❌ Замена `prepare_ssh_opts()` в scp-deliver.sh на немедленное удаление — сохраняем как deprecated-алиас на 1 release-cycle (backward-compat для external consumers, если есть).
- ❌ CI gate на наличие ssh_exec в новых entrypoints — enforcement через code review + AGENTS.md (как в Wave 1 для языковой политики).
- ❌ Migration всех 9 workflows на composite — минимум 6, остальные по мере touch.
- ❌ Synchronous audit-write (через logger с ожиданием) — только dual-write через existing `audit_log()` (logger -t platform-audit + printf >>, R-RISK-8 mitigation).
- ❌ Audit-trail на read-only operations (healthcheck, verify, project-list, project-status) — только state-modifying (7 entrypoints из брифа).
- ❌ Замена `audit_log()` существующей — только расширение через `audit_step()` wrapper.
- ❌ Использование trap-on-EXIT в `audit_step()` — только wrapper-style с явным capture `$?` (дизайн-решение VerificationReport 03 DRIFT-7).

---

## Cross-references

| Артефакт | Назначение |
|----------|-----------|
| [Brief 027](../027-architecture-modernization-program/01-Brief.md) §4 | Wave 2 спецификация |
| [DevPlan 028](../028-wave1-immediate/02-DevPlan.md) | Wave 1 predecessor (нужны честные тесты, baseline, lib/args.sh precedent) |
| `reports/architecture-analysis-2026-07-21.md` §P02, §P10, §P11, §P15 | Исходные проблемы |
| `reports/baseline-metrics-2026-07.csv` (из W1-E8) | Baseline для CI composite замера **(status: не существует — W1-E8 не завершён; fresh baseline собирается перед W2-E2)** |
| `core/AGENTS.md` §Канонические операции | SSH-фасад не добавляет новый make-глагол — lib-файл |

$END_DEVPLAN

---

## Заключение

Wave 2 (Dangerous) — первая волна с реальным production-риском. Все 3 эпика затрагивают production-пути, но дают измеримый профит: CI hangs устранены (P02 CRITICAL), CI setup −30s/workflow для Python-workflows + unification для non-Python (P15), 7 audit-точек (P11). Staging-gate для SSH-фасада — обязательный (R-RISK-1, impact H), revert-path документирован. Wave завершается production-релизом с VerificationReport.

**Аудит VerificationReport 03 (2026-07-21) выявил 11 drift-находок (4 CRITICAL, 5 HIGH, 2 MEDIUM).** Настоящая версия DevPlan содержит все исправления из §Audit Addendum ниже. После ревизии архитектора — готов к повторному QA-аудиту перед делегированием в dev-pipeline (Coder → QA → Fix).

---

## Audit Addendum — VerificationReport 03 Fixes Applied

Перечень drift-находок из `.ai/plans/029-wave2-dangerous/03-VerificationReport.md` и применённых исправлений:

| DRIFT-ID | Severity | Описание | Применённое исправление |
|---|---|---|---|
| DRIFT-1 | CRITICAL | vps-readiness.sh: 5 inline ssh → фактически 4 | Count исправлен на 4 во всех упоминаниях (lines 7, 20, 159-161, 364, 450) |
| DRIFT-2 | CRITICAL | vps-readiness.sh: «без ConnectTimeout» → все 4 имеют `-o ConnectTimeout=10` | Severity снижен с HI до MED; rationale переписан: миграция для unification, а не устранения hang'а |
| DRIFT-3 | CRITICAL | reconcile-projects.sh: «без timeout» → оба вызова имеют ConnectTimeout=10 | Описание исправлено в Code Graph entity + rationale |
| DRIFT-4 | HIGH | AC9 ≤3 недостижим: platform-deploy.yml содержит 3 checkout'а | platform-deploy.yml включён в миграцию (7 workflows вместо 6); AC9 оставлен ≤3 (остаются mirror.yml + build-platform.yml = 2) |
| DRIFT-5 | HIGH | «6× дублированную setup-python» → только 3 workflow используют setup-python-venv | RATIONALE переписан: 2 категории workflow (3 с python, 3 без python); KPI −30s только для Python-воркфлоу |
| DRIFT-6 | HIGH | AC4 «ConnectTimeout только в lib/ssh.sh» недостижим из-за fallback-branches | AC4 переформулирован: документированное исключение для `${SSH_OPTS[*]:-...}` legacy-fallbacks (удаляются в Wave 3) |
| DRIFT-7 | CRITICAL | audit_step trap-on-EXIT fires на success И failure → unit-тест AC13 невозможен | **Дизайн-решение:** wrapper-style без trap. Явный capture `$?` → условный emit DONE (=0) или FAIL (≠0). Обновлены: AC14, Data Flow 2, Code Graph audit_step_wrapper entity, Implementation note удалён |
| DRIFT-8 | CRITICAL | R-RISK-8 mitigation: «pure-async `>>`» → фактически dual-write (logger + printf) | Описание исправлено во всех секциях: dual-write, belt-and-suspenders |
| DRIFT-9 | MEDIUM | AC14 tail permission-denied для ci-deploy | AC14 + staging-test: `sudo tail`. Добавлено требование ci-deploy ∈ group adm или sudo |
| DRIFT-10 | HIGH | 6 из 7 entrypoints не имеют `source audit_logging.sh` | Добавлен Step 3 (предварительный) в Execution Order W2-E3 + prerequisite в Code Graph entity |
| DRIFT-11 | HIGH | node-lifecycle.sh:1289 уже вызывает `audit_log "node-update:complete"` → double-emit | Добавлен Step 5 в Execution Order W2-E3: удалить существующий audit_log на line 1289 |
| Inv-5 | AT_RISK | entrypoint-manifest.yaml не имеет lib-секции | AC22 + File Manifest + IMPACTS: lib-секция создаётся в Wave 2 |
| REQUIRES | CRITICAL | baseline-metrics-2026-07.csv не существует → W1-E8 не завершён | REQUIRES обновлён: W1-E8 prerequisite или descope AC10; Cross-references обновлён со статусом missing |
