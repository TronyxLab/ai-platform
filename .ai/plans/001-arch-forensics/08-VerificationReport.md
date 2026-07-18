<!-- GREP_SUMMARY: VerificationReport, arch-forensics-collapse-audit, cross-DevPlan-consistency, PATH-PREFIX-regression, OBSERVABILITY-gap, INVARIANT-closed, BOUNDARY-partial, typed-contract-complete, template-engine-complete, DataFlow-complete, DRIFTED-CRITICAL -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Executive Summary → ◇ 4 Collapses Matrix → ◇ DevPlan Implementation Status → ◇ Drift Analysis → ◇ Invariant Status → ◇ Test Results → ◇ Issues Register → ◇ Superposition → ◇ Verdict -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** QA-аудит текущего состояния ai-platform (SHA 4e6dbb6) на согласованность между 4 DevPlan'ами (04, 05, 06, 07) и закрытие проблем, задокументированных в 02-VerificationReport.md. Кросс-DevPlan consistency check + delta-анализ относительно 05-VerificationReport.md.
- **DESCRIPTION:** Полный аудит: Phase 1 (static audit ключевых файлов), Phase 2 (cross-file drift — PATH-PREFIX regression), Phase 3 (инварианты), Phase 4 (test quality), Phase 5 (runtime — 57 targeted tests PASS), Phase 6 (config sync). Фокус: согласованность реализаций 4 DevPlan'ов + состояние 4 коллапсов из 02-Report.
- **RATIONALE:** 05-Report (SHA 7d65d9b) был выполнен на dirty tree с 11 незакоммиченными файлами — часть артефактов W3/W4/W5 существовала только в рабочем дереве и была потеряна при последующих коммитах. Требуется аудит текущего HEAD для определения реального состояния и выявления регрессий.
- **ACCEPTANCE_CRITERIA:** 4 коллапса проверены на текущем коде; все 4 DevPlan'а проверены на полноту реализации; кросс-файловый drift обнаружен и задокументирован; evidence file:line для каждого утверждения.
- **IMPLEMENTS:** skill arch-forensics, роль QA, DevPlans 04/05/06/07, 02-VerificationReport.md, 05-VerificationReport.md
- **IMPACTS:** `core/modules/backup-cron/scripts/crontab:44,46` (PATH-PREFIX regression), `core/modules/monitoring/config/prometheus.yml.tmpl` (OBSERVABILITY gap), `.kilo/server-state-vps.json:5` (stale path), `core/entrypoint-manifest.yaml` (gates registration), `AGENTS.md` root (doc drift)
- **REQUIRES:** 02-VerificationReport.md (baseline проблем), 04-DevPlan.md (5-волновой план), 05-DevPlan.md (Typed Contract), 06-DevPlan-Templates.md (Template Unification), 07-DevPlan-DataFlow.md (ShellCheck), 05-VerificationReport.md (предыдущий QA)

$START_VERIFICATION_REPORT

# VerificationReport: DevPlan Cross-Consistency Audit — Arch-Forensics Collapses

🔒 **Verified against SHA** `4e6dbb6b919e484606bd7a3119ffac39df0bc983`
✅ **Working tree clean** — 0 modified files, 0 untracked
📅 **Audit date:** 2026-07-18

---

## §0. Executive Summary

**Verdict: DRIFTED — severity: CRITICAL**

Проведён полный аудит 4 DevPlan'ов (04, 05, 06, 07) на согласованность реализаций и закрытие 4 архитектурных коллапсов, выявленных в 02-VerificationReport.md (`arch-forensics` re-run).

**Ключевой результат:** 2 из 4 коллапсов **закрыты** (INVARIANT — Typed Contract, TEMPLATE — Engine Unification), 1 **частично закрыт** (PATH-PREFIX — sudo fix committed, crontab/systemd REGRESSION), 1 **не закрыт** (OBSERVABILITY — ни одной строки кода не реализовано). Обнаружена **регрессия PATH-PREFIX** относительно 05-Report: crontab:44,46 снова содержит `/opt/core/` (runtime bug — cron падает каждую минуту). Три гейта и `verify-node-paths.sh` создавались в dirty tree во время 05-Report, но **никогда не были закоммичены** и потеряны.

**Позитив:** 05-DevPlan (Typed Contract), 06-DevPlan (Template Engine), и 07-DevPlan (DataFlow) реализованы **полностью**. 57/57 целевых тестов зелёные. 13/13 module.yaml имеют поле `interfaces`. 6 call sites используют `invoke_module_interface`. Template engine с Python-ядром и strict grammar работает.

---

## §1. Four Collapses: Current Status Matrix

| Коллапс | Severity (02-Report) | SHA 7d65d9b (05-Report, dirty tree) | SHA 4e6dbb6 (HEAD, clean) | Δ |
|---------|---------------------|--------------------------------------|---------------------------|---|
| **INVARIANT** (internal→modules) | CRITICAL | ⚠️ AT_RISK (gate works, interfaces missing) | ✅ **CLOSED** | Typed Contract committed |
| **BOUNDARY** (modules→internal) | HIGH | ✅ CLOSED (path-consistency gate GREEN) | ⚠️ **REGRESSION** (crontab /opt/core/ вернулся) | Gate lost, paths reverted |
| **PATH-PREFIX** (/opt/core/ vs /opt/platform/core/) | HIGH | ✅ CLOSED (0 prod paths) | 🔴 **REGRESSION** (crontab:44,46, systemd README, .kilo) | Same — dirty tree fix discarded |
| **OBSERVABILITY** (postgres no metrics) | HIGH | 🔴 STILL OPEN (implementation missing) | 🔴 **STILL OPEN** | No change |

### INVARIANT COLLAPSE — CLOSED ✅

**Что было** (02-Report): `core/AGENTS.md` декларировал запрет `internal/ → modules/`, но 6 runtime вызовов (`bash "$hc_script"` etc.) нарушали его. Gate #8 был слеп (требовал `/` в строковом литерале). `healthcheck.sh:12` противоречил AGENTS.md.

**Что сделано** (05-DevPlan Typed Contract, 2026-07-18):
- `core/lib/module-interface.sh` создан — `invoke_module_interface(module, interface, args...)` с dispatch и graceful degradation
- 6 call sites рефакторены с `bash "$variable"` → `invoke_module_interface` (evidence: 0 результатов `rg 'bash "\$(hc_script|install_script|healthcheck_script|hook_script)"' core/internal/`)
- `interfaces:` поле добавлено во все 13 `module.yaml` (evidence: `rg '^interfaces:' core/modules/*/module.yaml` → 13 matches)
- `core/AGENTS.md` cross-layer таблица: `internal/ → modules/ (через invoke_module_interface + interfaces)`
- `core/modules/AGENTS.md` D4 контракт: документировано поле `interfaces` с closed vocabulary `[healthcheck, install, deploy-hook, remove-hook]`
- `core/entrypoints/healthcheck.sh:12` — контрадикция заменена на "internal/ → modules is permitted through typed contract (invoke_module_interface + module.yaml.interfaces)"
- Gate #8 v3: `_detect_direct_module_calls`, `_detect_invoke_calls`, `_validate_interfaces`, `_trace_variable_assignment`, `_collect_path_variables`, ShellCheck SC2154 integration
- **Тесты:** 57/57 PASS включая `test_all_call_sites_use_invoke`, `test_invoke_registered_interface_passes`, `test_invoke_unregistered_interface_fails`

**Verdict: ЗАКРЫТ.** Gate #8 v3 enforce'ит typed contract на уровне кода, документации и CI.

### BOUNDARY COLLAPSE — REGRESSION ⚠️

**Что было** (02-Report): modules→internal через cron (`crontab:44` → `/opt/core/internal/healthcheck/docker-healthcheck.sh`), systemd (`platform-secrets.service:13`), и hook-цепочку (`monitoring/hooks/on-project-deploy.sh:321` → `generate-catalog.sh`).

**Что сделано:**
- `platform-secrets.service:13` использует `/opt/platform/core/internal/secrets/decrypt-secrets.sh` — КОРРЕКТНЫЙ путь ✅
- `monitoring/hooks/on-project-deploy.sh:321` — hook-цепочка не была в скоупе ни одного DevPlan (non-scope Brief). Путь `${PLATFORM_ROOT}/core/internal/catalog/generate-catalog.sh` — зависит от переменной окружения, статически `/opt/platform/...` ✅
- `crontab:44,46` — `/opt/core/internal/healthcheck/docker-healthcheck.sh` и `/opt/core/modules/backup-cron/scripts/disk-monitor.sh` — **НЕ ИСПРАВЛЕНЫ** ❌

**Что произошло:** Во время 05-Report (SHA 7d65d9b) crontab был исправлен в dirty tree. Path-consistency gate был зелёный (3/3 PASS). Но коммит `4e6dbb6` (HEAD) показывает исходные `/opt/core/` пути — dirty tree исправления были потеряны при последующих коммитах (security hardening, template-engine).

**Косвенная защита:** Gate `test_gate_path_consistency.py` НЕ существует в репозитории — был создан в dirty tree и потерян. Без него регресс не обнаруживается CI.

**Verdict: РЕГРЕССИЯ.** Без path-consistency gate BOUNDARY collapse может регрессировать в любой момент. Текущее состояние — идентично 02-Report для crontab и systemd/README.md.

### PATH-PREFIX COLLAPSE — REGRESSION 🔴

| Файл | Статус | Evidence |
|------|--------|----------|
| `core/lib/paths.sh:33` | SoT: `/opt/platform` | `PLATFORM_ROOT="/opt/platform"` ✅ |
| `core-deploy.yml:130` | rsync dest | `/opt/platform/core/` ✅ |
| `core/templates/sudo-whitelist.template` | **FIXED** | `{{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile` ✅ |
| `core/modules/backup-cron/scripts/crontab:44` | **BROKEN** | `/opt/core/internal/healthcheck/docker-healthcheck.sh` ❌ |
| `core/modules/backup-cron/scripts/crontab:46` | **BROKEN** | `/opt/core/modules/backup-cron/scripts/disk-monitor.sh` ❌ |
| `core/bootstrap/systemd/README.md:189,192` | **BROKEN** | `/opt/core/internal/healthcheck/` ❌ |
| `.kilo/server-state-vps.json:5` | **STALE** | `"workdir": "/opt/core"` ❌ |
| `.kilo/agents/sysadmin.md:469` | **STALE** | `/opt/core/bootstrap/bootstrap.sh` ❌ |
| `core/internal/bootstrap/install-tor-proxy.sh:340` | **COMMENT** | Исторический комментарий — допустимо |

**Runtime impact (подтверждено):** `crontab:44` выполняется **каждую минуту** в контейнере backup-cron. Файл `/opt/core/internal/healthcheck/docker-healthcheck.sh` не существует ни в контейнере (Dockerfile:74 копирует `scripts/crontab` как `/etc/cron.d/platform-backup`, но контейнер НЕ имеет mount'а `/opt/core/`), ни на хосте (rsync идёт в `/opt/platform/core/`). Cron-демон жив (`pgrep cron` passes liveness), но задача молча падает. Ошибки пишутся в `/var/log/platform/backup/docker-healthcheck.log`, который никто не читает.

**Verdict: РЕГРЕССИЯ — CONFIRMED RUNTIME BUG.** Проблема из 02-Report (§4 Violation: PATH-PREFIX SPLIT) полностью воспроизводится на текущем HEAD.

### OBSERVABILITY COLLAPSE — NOT RESOLVED 🔴

| Требование | Статус | Evidence |
|-----------|--------|----------|
| postgres-exporter в `infra-metrics/docker-compose.base.yml` | ❌ MISSING | `rg postgres-exporter core/modules/infra-metrics/docker-compose.base.yml` → 0 matches |
| postgres scrape job в `prometheus.yml.tmpl` | ❌ MISSING | 8 jobs exist (prometheus, litellm, cadvisor, node-exporter, nginx-exporter, clickhouse, redis-exporter, platform-projects). No postgres/postgres-exporter job. |
| hermes-agent metrics endpoint | ❌ NOT ADDRESSED | Вне скоупа (D5: если нет `/metrics` → scrape job не добавляется) |
| Gate `test_gate_observability_coverage.py` | ❌ MISSING | Файл создавался в dirty tree (05-Report), но НЕ существует в репозитории |
| `test_severity_high_modules_have_scrape_job` | ❌ NOT RUNNING | Gate файл отсутствует → проверка не выполняется |

**Verdict: НЕ ЗАКРЫТ.** Ни TASK-W1-1 (postgres-exporter контейнер), ни TASK-W1-2 (scrape job) не реализованы. Гейт, защищающий от регресса, отсутствует. Postgres (severity=critical, blast radius 4+ модулей) остаётся без метрик — отказ невидим до хард-аутейта.

---

## §2. DevPlan Implementation Status

| DevPlan | Wave(s) | Status | Tests | Key Evidence |
|---------|---------|--------|-------|-------------|
| **04-DevPlan W1** (Observability) | W1 | ❌ NOT IMPLEMENTED | Gate file missing | No exporter, no scrape job, no gate |
| **04-DevPlan W2** (Model Surgery) | W2 | ✅ **COMPLETE via 05-DevPlan** | 57/57 PASS | All call sites + interfaces |
| **04-DevPlan W3** (Gate Hardening) | W3 | ⚠️ PARTIAL | `_looks_like_path` fixed; path/doc gates LOST | Only `_looks_like_path` surviving |
| **04-DevPlan W4** (Path Remediation) | W4 | ⚠️ PARTIAL | sudo only | crontab/systemd/.kilo NOT fixed |
| **04-DevPlan W5** (Runtime Sentinel) | W5 | ❌ LOST | `verify-node-paths.sh` absent | File never committed |
| **05-DevPlan** (Typed Contract) | W1-W3 | ✅ **COMPLETE** | 57/57 PASS | `module-interface.sh` + 13 interfaces + 6 call sites + Gate #8 v3 |
| **06-DevPlan** (Template Engine) | W1-W3 | ✅ **COMPLETE** | 19/19 unit tests PASS | `template_engine.py` + `template-engine.sh` + `template-manifest.yaml` + 2 gates |
| **07-DevPlan** (DataFlow) | W1-W6 | ✅ **COMPLETE** | All functions + ShellCheck integrated | `_collect_path_variables` + `_trace_variable_assignment` + `_substitute_variables` + `shellcheck.py` |

### 05-DevPlan (Typed Contract) — COMPLETE ✅

| Artifact | Status | Evidence |
|----------|--------|----------|
| `core/lib/module-interface.sh` | EXISTS | ~90 строк, `invoke_module_interface()`, `_invoke_validate_interface()`, `_invoke_dispatch_*` |
| `core/lib/paths.sh` source of module-interface.sh | PRESENT | `paths.sh:31` — `source "${PATHS_LIB_DIR}/module-interface.sh"` |
| `interfaces:` in 13 module.yaml | **13/13** | Все модули имеют поле (minio: `[]`, platform-secrets: `[install]`, остальные: `[healthcheck, ...]`) |
| 6 call sites → `invoke_module_interface` | **6/6** | `node-lifecycle.sh:845`, `deploy-modules.sh:357,558,584`, `deploy-project.sh:728,751` |
| `healthcheck.sh:12` contradiction fix | **FIXED** | "internal/ → modules is permitted through typed contract" |
| `core/AGENTS.md` cross-layer table | **UPDATED** | `internal/ → modules/ (через invoke_module_interface + interfaces)` |
| `core/modules/AGENTS.md` D4 contract | **UPDATED** | `interfaces:` field documented with closed vocabulary |
| Gate #8 v3 (`_detect_invoke_calls` etc.) | **PRESENT** | `tests/test_cross_layer_imports.py:638-830` |
| Gate #8 test — `test_all_call_sites_use_invoke` | **PASS** | 0 violations on current state |
| Gate registration in manifest | **PRESENT** | `entrypoint-manifest.yaml:307` — `id: cross-layer` |

### 06-DevPlan (Template Engine) — COMPLETE ✅

| Artifact | Status | Evidence |
|----------|--------|----------|
| `core/internal/template_engine.py` | EXISTS | Python-ядро: `render_template()`, `parse_vars()`, `TemplateError`, `render_all()`, `check_all()` |
| `core/internal/template-engine.sh` | EXISTS | Bash CLI: `render`, `render-all`, `check` |
| `core/templates/template-manifest.yaml` | EXISTS | 8 template entries, `standard_vars`, `version: 1` |
| `tests/test_template_engine.py` | EXISTS | 19 atomic tests, все PASS |
| `tests/gates/test_gate_template_syntax.py` | EXISTS | Gate: unified `{{UPPER_SNAKE}}` syntax |
| `tests/gates/test_gate_template_drift.py` | EXISTS | Gate: template resolvability |
| `Makefile` targets `templates-check`, `templates-render` | PRESENT | `Makefile:37,43` — `.PHONY` targets |
| `entrypoint-manifest.yaml` registration | PRESENT | `template-syntax`, `template-drift`, `template-metrics` + `allowed_verbs` |
| `core/AGENTS.md` operations table | UPDATED | `make templates-check`, `make templates-render` |
| `core/modules/AGENTS.md` contract | UPDATED | `docker-compose.test.yml contract` section |
| `docker-compose.test.template` | **DELETED** | Файл удалён, знания мигрированы в AGENTS.md |
| `sudo-whitelist.conf` symlinks (×6) | **DELETED** | `glob core/modules/*/sudo-whitelist.conf` → 0 results |
| `core/modules/nginx/config/platform-default.conf.template` | **RENAMED** | `.conf` → `.conf.template` ✅ |
| Template syntax `__VAR__` → `{{VAR}}` | **MIGRATED** | Scaffold templates: `__PROJECT_NAME__` → `{{PROJECT_NAME}}` |

### 07-DevPlan (DataFlow) — COMPLETE ✅

| Artifact | Status | Evidence |
|----------|--------|----------|
| `_collect_path_variables()` | PRESENT | `test_cross_layer_imports.py:124` — парсит `paths.sh`, возвращает dict |
| `_trace_variable_assignment()` | PRESENT | `test_cross_layer_imports.py:332` — локальный трекинг `local VAR=...` |
| `_substitute_variables()` | PRESENT | `test_cross_layer_imports.py:296` — замена 9 хардкоженных блоков на auto-collected |
| `_looks_like_path` bare variable detection | PRESENT | `test_cross_layer_imports.py:121` — `$hc_script` → True |
| `_NON_IMPORT_ARGS` extended | PRESENT | Спец-переменные `$?`, `$$`, `$!`, `$@`, etc. |
| `resolve_import` variable tracking integration | PRESENT | Шаг 2: `_trace_variable_assignment` для bare `$variable` |
| ShellCheck `_check_shellcheck_available()` | PRESENT | `tests/_conftest/shellcheck.py` — проверка версии ≥0.9.0 |
| ShellCheck `get_shellcheck_bash_calls()` | PRESENT | `shellcheck.py` — SC2154 data-flow detection |
| ShellCheck integration in `scan_sh_file` | PRESENT | `test_cross_layer_imports.py` — Graceful degradation |
| Regex patterns: `make -C` | PRESENT | Pattern 5 |
| Regex patterns: `docker compose -f` | PRESENT | Pattern 6 |
| Unit tests: `TestLooksLikePath` (10) | **PASS** | 10/10 |
| Unit tests: `TestResolveImport` (6) | **PASS** | 6/6 |
| Unit tests: `TestCollectPathVariables` (4) | **PASS** | 4/4 |
| Unit tests: `TestTraceVariableAssignment` (6) | **PASS** | 6/6 |
| Unit tests: `TestShellCheckIntegration` (4) | **PASS** | 4/4 |

---

## §3. Drift Analysis (Phase 2)

### DRIFT-1: PATH-PREFIX REGRESSION — CRITICAL 🔴

**Файлы:** `core/modules/backup-cron/scripts/crontab:44,46` vs `core/lib/paths.sh:33` vs `core-deploy.yml:130`

**Суть:** SoT (`paths.sh`) declares `PLATFORM_ROOT=/opt/platform`. `core-deploy.yml:130` rsync's to `/opt/platform/core/`. But crontab still uses `/opt/core/` — the path that DOES NOT EXIST on the host or in the container.

**Impact:** Backup-cron silently fails **every minute** (crontab:44 docker-healthcheck.sh) and **every hour** (crontab:46 disk-monitor.sh). Liveness check (`pgrep cron`) passes — cron daemon is alive, cron jobs fail.

**Root cause:** W4 path fixes (05-Report, SHA 7d65d9b) existed only in dirty tree and were discarded when subsequent feature branches merged. The `crontab` file was last committed on 2026-07-17 (SHA `f2a7511` — bootstrap lifecycle), before W4 was implemented.

**Fix:** Restore W4 crontab fix: `/opt/core/` → `/opt/platform/core/` (lines 44, 46). Re-create `test_gate_path_consistency.py` gate to prevent regression.

### DRIFT-2: .kilo Path Staleness — HIGH 🟠

**Файлы:** `.kilo/server-state-vps.json:5` vs `core/lib/paths.sh:33`

**Суть:** `.kilo/server-state-vps.json` declares `"workdir": "/opt/core"` — path does not exist after rsync.

**Fix:** Update to `/opt/platform` per W4 spec.

### DRIFT-3: Doc Glossary Inconsistency — HIGH 🟠

**Файлы:** `AGENTS.md` (root) glossary vs `core/AGENTS.md` operations table vs `entrypoint-manifest.yaml`

**Суть:**
- `verify` — присутствует в `core/AGENTS.md` operations table и `entrypoint-manifest.yaml:182`, но **отсутствует** в root `AGENTS.md` glossary
- `adopt-project` — в `entrypoint-manifest.yaml:120`; `project-adopt` — в root `AGENTS.md` glossary. **Разные имена** для одной операции.

**Fix:** Add `verify` to root AGENTS.md glossary. Unify naming: either `adopt-project` everywhere or `project-adopt` everywhere.

### DRIFT-4: Observability Coverage Gap — CRITICAL 🔴

**Файлы:** `core/modules/postgres/module.yaml:24` (severity: critical) vs `core/modules/monitoring/config/prometheus.yml.tmpl` (no postgres job)

**Суть:** Модуль с `severity: critical` не имеет Prometheus scrape job. Gate, защищающий этот контракт (`test_gate_observability_coverage.py`), отсутствует.

**Fix:** Implement TASK-W1-1 (postgres-exporter) + TASK-W1-2 (scrape job) + re-create gate.

### DRIFT-5: Gate Artifact Loss — HIGH 🟠

**Суть:** 3 gate-файла + 1 скрипт, зафиксированные в 05-Report как «работающие», отсутствуют в репозитории:

| File | 05-Report Status | HEAD Status |
|------|-----------------|-------------|
| `tests/gates/test_gate_path_consistency.py` | 3/3 PASS | ❌ MISSING |
| `tests/gates/test_gate_doc_consistency.py` | 2/3 PASS, 1 FAIL | ❌ MISSING |
| `tests/gates/test_gate_observability_coverage.py` | 1/3 PASS, 2 FAIL | ❌ MISSING |
| `core/internal/verify/verify-node-paths.sh` | Syntax PASS | ❌ MISSING |

**Root cause:** 05-Report выполнен на dirty tree (11 modified files). Последующие коммиты (security hardening, template-engine, gate8-v2) пришли из других веток, и dirty tree был перезаписан без коммита этих файлов.

### DRIFT-6: Gate Registration Mismatch — MEDIUM 🟡

**Суть:** `entrypoint-manifest.yaml` содержит gate entry для `template-metrics` (line 355), но соответствующий gate-файл `test_gate_template_metrics.py` действительно существует. Однако сам gate FAIL (3/3 tests fail — `test_templates_metrics_port_present`, `test_templates_metrics_endpoint_in_main`, `test_templates_metrics_port_consistency`). Инфраструктура metrics endpoint не готова (отдельная задача).

---

## §4. Invariant Status (Phase 3) — Key Invariants

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Makefile — единый фасад | ✅ HELD | Все операции через make, 34 allowed_verbs |
| 4 | AGENTS.md — 3 канонических файла | ⚠️ AT_RISK | `verify` not in root glossary; `adopt-project`/`project-adopt` mismatch |
| 5 | entrypoint-manifest.yaml — YAML-реестр | ✅ HELD | 43 gate entries, 34 allowed_verbs |
| 6 | make bootstrap-node — строго идемпотентный | ✅ HELD | Content-hash checkpoint system |
| 7 | Полный локальный стек через docker compose up | ⚠️ AT_RISK | pre-existing smoke test failures |
| 8 | LiteLLM — PostgreSQL во всех окружениях | ✅ HELD | Enforced by gate |
| — | Cross-layer: typed contract | ✅ HELD | `invoke_module_interface` + 13 interfaces + Gate #8 v3 |
| — | Cross-layer: DataFlow enforcement | ✅ HELD | ShellCheck + Extended Variable Registry + Variable Tracking |

---

## §5. Test Results (Phase 5)

### Targeted Tests (scope DevPlans 05/06/07)

```
tests/gates/test_gate_cross_layer.py::test_gate_cross_layer PASSED
tests/test_cross_layer_imports.py — 37 tests PASSED (включая TestLooksLikePath×10, TestResolveImport×6, TestCollectPathVariables×4, TestTraceVariableAssignment×6, TestShellCheckIntegration×4)
tests/test_template_engine.py — 19 tests PASSED
============================================================================================================
57 passed in 24.84s — 100% PASS
```

**Anti-Illusion Verdict: PASS** — все тесты DevPlan-скоупа имеют IMP:9 логи. Gate #8 v3 логирует `[IMP:9][lint][result] PASS — 0 cross-layer import violations`. Template engine тесты логируют успешный рендеринг.

### Full Gate Suite (pre-existing failures вне скоупа)

```
152 passed, 11 skipped, 6 failed, 2 errors in 15.32s
```

Failures **вне скоупа** arch-forensics DevPlans:
- `test_gate_lint_quality.py::test_linter_parity` — 2 linter disagreements (pre-existing)
- `test_gate_nginx_domain_contract.py` ×2 — pre-existing
- `test_gate_template_metrics.py` ×3 — metrics infrastructure not ready

---

## §6. Issues Register

### CRITICAL (blocks merge / production incident)

| ID | Description | File | Fix |
|----|-------------|------|-----|
| **ISS-R1** | 🔴 CRITICAL — Runtime bug: crontab:44,46 silently fails every minute/hour. `/opt/core/` path does not exist in container or on host. | `core/modules/backup-cron/scripts/crontab:44,46` | Restore W4 fix: `/opt/core/` → `/opt/platform/core/` |
| **ISS-R2** | 🔴 CRITICAL — Observability collapse not closed. postgres (severity=critical) has no metrics. Gate missing. | `core/modules/infra-metrics/docker-compose.base.yml`, `prometheus.yml.tmpl` | Implement TASK-W1-1 + TASK-W1-2 from 04-DevPlan |
| **ISS-R3** | 🔴 CRITICAL — path-consistency, doc-consistency, observability-coverage gates created in dirty tree (05-Report) were lost. No regression protection. | `tests/gates/test_gate_path_consistency.py` et al. | Re-create gates per 04-DevPlan §W3 |

### HIGH

| ID | Description | File | Fix |
|----|-------------|------|-----|
| **ISS-R4** | 🟠 HIGH — `.kilo/server-state-vps.json:5` path `/opt/core` is stale | `.kilo/server-state-vps.json:5` | Update to `/opt/platform` |
| **ISS-R5** | 🟠 HIGH — Root AGENTS.md glossary missing `verify` + naming mismatch `adopt-project`/`project-adopt` | `AGENTS.md:78-100` | Add `verify`, unify naming |
| **ISS-R6** | 🟠 HIGH — `verify-node-paths.sh` lost. W5 runtime sentinel absent. | `core/internal/verify/verify-node-paths.sh` | Re-create per 04-DevPlan §W5 |

### MEDIUM

| ID | Description | File | Fix |
|----|-------------|------|-----|
| **ISS-R7** | 🟡 MEDIUM — `core/bootstrap/systemd/README.md:189,192` references `/opt/core/` | `core/bootstrap/systemd/README.md` | Replace with `/opt/platform/core/` |
| **ISS-R8** | 🟡 MEDIUM — `.kilo/agents/sysadmin.md:469` references `/opt/core/` | `.kilo/agents/sysadmin.md` | Replace with `/opt/platform/core/` |
| **ISS-R9** | 🟡 MEDIUM — `test_gate_template_metrics.py` 3/3 FAIL | `tests/gates/test_gate_template_metrics.py` | Metrics infrastructure separate task |

---

## §7. Superposition — Root Cause Analysis

## SUPERPOSITION: Why are W1/W4/W5 artifacts missing from HEAD?

### Option A: Never Committed — Dirty Tree [score: 9/10]
**Approach:** 05-Report was run on dirty tree (11 modified files). The artifacts existed in the working directory but were never staged/committed. Subsequent `git checkout` of feature branches (security-hardening, template-engine, gate8-v2) overwrote the dirty tree. The artifacts are **lost** — they existed only in the working directory during the QA session.
**Evidence:** 05-Report explicitly notes "Working tree dirty: 11 modified files." The current HEAD shows zero of those 11 files as changed relative to pre-W4 state. The `crontab` git log shows last commit `f2a7511` (2026-07-17, bootstrap lifecycle) — predates W4 implementation.
**Trade-offs:** Most likely explanation. Consistent with git workflow: uncommitted changes are volatile.

### Option B: Committed Then Reverted [score: 2/10]
**Approach:** Artifacts were committed in a branch that was later force-pushed or rebased away. The merge commits between 7d65d9b and 4e6dbb6 (`6d781e7`, `a0c4962`, `3250dd7`, `4e6dbb6`) came from different work streams (security hardening, template engine, gate8-v2) that did not include W4/W5 artifacts.
**Evidence against:** `git log --all -- core/modules/backup-cron/scripts/crontab` shows only commits from bootstrap lifecycle and template-engine — no W4 fix commit exists in any branch.
**Trade-offs:** Requires explicit revert or force-push — less likely than Option A.

### Option C: Superseded by 06/07-DevPlans [score: 3/10]
**Approach:** The template engine (06-DevPlan) re-implemented sudo-whitelist fix with `{{PLATFORM_ROOT}}` — superseding W4-2. The DataFlow (07-DevPlan) implemented variable tracking — superseding W3-1. But crontab fix (W4-1) and verify-node-paths.sh (W5) have no successor in 06/07.
**Evidence:** `sudo-whitelist.template` was fixed via template engine, not W4. But crontab and systemd README were not in scope of 06-DevPlan.
**Trade-offs:** Partial explanation — only covers sudo, not crontab/systemd/verify.

### Recommendation: Option A — Never Committed (9/10)

**Collapse signal:** Evidence from git log confirms: crontab fix was never committed. The 05-Report correctly assessed the dirty tree, but the fixes were lost when the working directory was replaced by subsequent feature branches. The clean tree at HEAD reflects the pre-W4 state for crontab/systemd artifacts.

**Immediate action:** Re-implement W4 crontab/systemd fixes + re-create path-consistency gate. W1 (observability) was never implemented at all — requires new implementation.

---

## §8. Recommendations

### Immediate (BLOCKER — before any merge)

1. **Fix ISS-R1:** Restore crontab path fix — `/opt/core/` → `/opt/platform/core/` in `crontab:44,46`
2. **Fix ISS-R2:** Implement W1: postgres-exporter container + scrape job per 04-DevPlan §W1, D4
3. **Fix ISS-R3:** Re-create `test_gate_path_consistency.py` gate (детектирует `/opt/core/` в crontab/systemd/sudoers)

### High Priority

4. **Fix ISS-R5:** Add `verify` to root AGENTS.md glossary; resolve `adopt-project`/`project-adopt` naming
5. **Fix ISS-R6:** Re-create `verify-node-paths.sh` per 04-DevPlan §W5 — runtime sentinel
6. **Fix ISS-R4:** Update `.kilo/server-state-vps.json` path

### Medium Priority

7. **Fix ISS-R7, ISS-R8:** Fix documentation paths in systemd/README.md and sysadmin.md

---

## §9. Comparison Matrix — All Verification Reports

| Измерение | 01-Report (Jul 16) | 02-Report (Jul 18) | 05-Report (Jul 18, dirty) | **08-Report (Jul 18, HEAD)** |
|-----------|-------------------|-------------------|--------------------------|------------------------------|
| INVARIANT COLLAPSE | CRITICAL | CRITICAL | ⚠️ AT_RISK | ✅ **CLOSED** |
| BOUNDARY COLLAPSE | HIGH | HIGH | ✅ CLOSED | ⚠️ **REGRESSION** |
| PATH-PREFIX COLLAPSE | не обнаружен | HIGH | ✅ CLOSED | 🔴 **REGRESSION** |
| OBSERVABILITY COLLAPSE | не обнаружен | HIGH | 🔴 STILL OPEN | 🔴 **STILL OPEN** |
| DOC DRIFT | не измерялся | не измерялся | ⚠️ DETECTED | ⚠️ **DETECTED** |
| Test count | ~822 | 874 | 909 | 909 |
| Gate entries | 36 | 41 | 41+ | 43 |
| Typed Contract | ❌ | ❌ | Gate works, no data | ✅ **COMPLETE** |
| Template Engine | ❌ | ❌ | ❌ | ✅ **COMPLETE** |
| DataFlow (ShellCheck) | ❌ | ❌ | ❌ | ✅ **COMPLETE** |
| Working tree | — | clean | dirty (11 files) | **clean** |

---

## §10. Verdict

**DRIFTED — severity: CRITICAL**

**Основание:**
1. **DRIFT-1 (PATH-PREFIX REGRESSION — CRITICAL):** `crontab:44,46` содержит `/opt/core/` — runtime bug (cron fails every minute). W4 fix был в dirty tree 05-Report и потерян.
2. **DRIFT-4 (OBSERVABILITY GAP — CRITICAL):** postgres (severity=critical) не имеет метрик. W1 никогда не реализовывалась.
3. **DRIFT-3 (DOC GLOSSARY — HIGH):** `verify` not in root AGENTS.md glossary; `adopt-project` vs `project-adopt` naming mismatch.
4. **DRIFT-5 (GATE LOSS — HIGH):** 3 gate-файла + verify-node-paths.sh созданы в dirty tree, потеряны, не закоммичены.
5. **Cross-file inconsistency:** Root AGENTS.md glossary ≠ core/AGENTS.md operations ⊕ entrypoint-manifest.yaml (verify, naming).

**Позитивные результаты:**
- **05-DevPlan (Typed Contract) — COMPLETE:** INVARIANT COLLAPSE закрыт. 13/13 interfaces, 6/6 call sites, Gate #8 v3.
- **06-DevPlan (Template Engine) — COMPLETE:** 4 механизма → 1 синтаксис `{{UPPER_SNAKE}}`. 19/19 unit tests PASS.
- **07-DevPlan (DataFlow) — COMPLETE:** ShellCheck + Extended Registry + Variable Tracking. 57/57 target tests PASS.
- **04-DevPlan W2 (Model Surgery) — COMPLETE:** via 05-DevPlan.
- **04-DevPlan W3 (`_looks_like_path`) — COMPLETE:** variable tracking работает.

**Корневая причина регрессии:** 05-VerificationReport был выполнен на dirty tree. W4/W5 артефакты существовали только в рабочем дереве и были утеряны при переключении на feature-ветки. Механизма сохранения dirty tree между QA-сессиями не существует — артефакты должны быть закоммичены до завершения QA.

**Рекомендация:** Делегировать Coder'у восстановление W4 crontab fix + re-create path-consistency gate + реализацию W1 (postgres-exporter). После коммита — повторный QA.

$END_VERIFICATION_REPORT
