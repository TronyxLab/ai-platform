$START_VERIFICATION_REPORT

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Верификация acceptance criteria 001-deploy-loop-refactor (TASK-1..TASK-8) — статический аудит, cross-file drift detection, runtime validation |
| **DESCRIPTION** | Полная проверка 8 задач рефакторинга деплой-контура: network alias в шаблонах, variable-based proxy_pass, nginx reload hook, project-compose gate, macOS smoke skip, make verify, CI platform-deliver, TRAP[DEBT] |
| **RATIONALE** | 4 ручные правки в сессии деплоя — symptom systemic drift. Каждый AC проверяет, что автоматизация/детекция работает |
| **ACCEPTANCE_CRITERIA** | Все 8 AC из DevPlan, 4 gate-теста, LDD траектории, bash -n синтаксис, yaml валидация |
| **IMPLEMENTS** | Stage 3 QA для 001-DevPlan.md |
| **IMPACTS** | Все файлы из File Manifest (13 файлов) + entrypoint-manifest.yaml, core/AGENTS.md |
| **REQUIRES** | DevPlan.md, Python 3.10+, bash 4+, Docker (для smoke-тестов) |

---

## Section 1 — Static Audit (Phase 1)

### Compliance Matrix

| File | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | #region pairing | Doxygen tags | LDD logs | No bare except | No secrets |
|------|-------------|-----------|-----------------|-----------------|--------------|----------|----------------|------------|
| `templates/template-frontend/docker-compose.yml` | ✅ | ✅ | N/A (template) | ✅ | N/A | N/A | N/A | ✅ |
| `templates/template-backend/docker-compose.yml` | ✅ | ✅ | N/A (template) | ✅ | N/A | N/A | N/A | ✅ |
| `templates/template-fullstack/docker-compose.yml` | ✅ | ✅ | N/A (template) | ✅ | N/A | N/A | N/A | ✅ |
| `core/internal/scaffold/add-vhost.sh` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | N/A | ✅ |
| `core/modules/nginx/module.yaml` | ✅ | ✅ | ✅ | N/A | ✅ | N/A | N/A | ✅ |
| `core/modules/nginx/nginx_reload_hook.sh` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | N/A | ✅ |
| `core/internal/deploy/deploy-project.sh` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | N/A | ✅ |
| `tests/gates/test_gate_project_compose.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/test_smoke_nginx.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/_conftest/smoke.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `Makefile` (verify target) | ✅ | N/A | ✅ | N/A | ✅ | ✅ | N/A | ✅ |
| `core/entrypoints/verify.sh` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | N/A | ✅ |
| `.github/workflows/deploy-project.yml` | ✅ | ✅ | ✅ | N/A | ✅ | ✅ | N/A | ✅ |
| `.ai/plans/001-deploy-loop-refactor/02-Debt.md` | ✅ | ✅ | ✅ | N/A | ✅ | N/A | N/A | ✅ |

**Findings:** 0 compliance violations. Все файлы соответствуют стандартам разметки.

---

## Section 2 — Drift Analysis (Phase 2)

### Drift Register

| DRIFT-ID | Severity | Files | Expected | Actual | Suggestion |
|----------|----------|-------|----------|--------|------------|
| DRIFT-1 | **CRITICAL** | `core/modules/nginx/nginx_reload_hook.sh` vs `core/entrypoint-manifest.yaml` | nginx_reload_hook.sh зарегистрирован в manifest (delegates_to или exception) | Скрипт существует на диске, но не зарегистрирован в entrypoint-manifest.yaml | Добавить nginx_reload_hook.sh в manifest как delegates_to для make_target или в _SHEBANG_EXCEPTION_PATTERNS |
| DRIFT-2 | **CRITICAL** | `core/entrypoint-manifest.yaml gates:` vs `tests/gates/test_gate_project_compose.py` | test_gate_project_compose.py зарегистрирован в manifest gates: секции | Файл существует, имеет @pytest.mark.gate, но не указан в entrypoint-manifest.yaml | Добавить 4 записи в gates: секцию для test_gate_project_compose |
| DRIFT-3 | **HIGH** | `core/entrypoint-manifest.yaml` vs `core/AGENTS.md` | `verify` verb присутствует в обоих | `verify` есть в manifest (allowed_verbs) но отсутствует в core/AGENTS.md таблице канонических операций | Добавить `verify` target в core/AGENTS.md canonical operations table |
| DRIFT-4 | **HIGH** | `core/entrypoints/verify.sh` vs `tests/gates/test_gate_thin_wrapper.py` | verify.sh ≤150 LOC (thin-wrapper contract) | verify.sh: 256 LOC (превышение на 106 строк) | Рефакторинг: вынести логику в internal/, оставить entrypoint как thin wrapper |
| DRIFT-5 | **INFO** | `tests/report.xml` vs `test_gate_skip_enforcement.py` | JUnit XML: 0 errors | 3 errors (pre-existing, не связаны с этим рефакторингом) | Не блокирует merge, но требует отдельной диагностики |

### Contract Violations

| Contract | Status | Evidence |
|----------|--------|----------|
| Gate registration protocol (tests/gates/AGENTS.md): файл + маркер + manifest | **VIOLATED** | test_gate_project_compose.py имеет файл + @pytest.mark.gate, но пропущен entrypoint-manifest.yaml |
| Manifest bidirectional integrity (core/AGENTS.md): все verbs в manifest должны быть в AGENTS.md | **VIOLATED** | `verify` verb в manifest, отсутствует в core/AGENTS.md таблице |
| Thin-wrapper contract (tests/gates/AGENTS.md): entrypoints ≤150 LOC | **VIOLATED** | verify.sh: 256 LOC |

### Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 2 |
| HIGH | 2 |
| INFO | 1 |

---

## Section 3 — Invariant Status (Phase 3)

Из AGENTS.md (root) — только инварианты, затрагиваемые этим рефакторингом:

| Invariant | Status | Evidence | Risk |
|-----------|--------|----------|------|
| **Inv 1:** Makefile — единый фасад. Все операции через `make <target>` | **HELD** | `make verify` реализован как Makefile target, делегирует в entrypoint | Неблокирующий |
| **Inv 4:** AGENTS.md — 3 канонических файла (root, core/, core/modules/) | **HELD** | Все 3 файла существуют, DRIFT-3 — missing `verify` verb в core/AGENTS.md (minor) | Низкий |
| **Inv 5:** core/entrypoint-manifest.yaml — YAML-реестр канонических операций для CI-gate'ов | **AT_RISK** | DRIFT-1, DRIFT-2: 2 новых скрипта не зарегистрированы | Средний — CI gate не обнаружит дрейф новых компонентов |
| **Inv 7:** Полный локальный стек через `docker compose up` на macOS разработчика | **HELD** | Smoke тесты на macOS: 3 PASS, 2 SKIP (ожидаемо) | Неблокирующий |

### Summary

| Status | Count |
|--------|-------|
| HELD | 3 |
| AT_RISK | 1 |
| VIOLATED | 0 |

---

## Section 4 — Test Quality (Phase 4)

### Coverage Gaps

| Gap | Detail | Severity |
|-----|--------|----------|
| Gate registration gap | test_gate_project_compose.py не зарегистрирован в manifest — CI gate не запускает его в `make gate` | CRITICAL |
| Invariant coverage | Inv 5 (entrypoint-manifest YAML-реестр) — нет gate-теста, верифицирующего, что все hook-скрипты зарегистрированы | WARNING |

### Semantic Assertion Check

| File | Implementation tests | Behavioral tests | Ratio | Verdict |
|------|-------------------|------------------|-------|---------|
| `test_gate_project_compose.py` | 0/4 | 4/4 | 0% | ✅ Behavioral |
| `test_smoke_nginx.py` | 0/5 | 5/5 | 0% | ✅ Behavioral |

### Fragility Index

| Metric | Value |
|--------|-------|
| Skip count (gates suite) | 10 skipped (9 module-hooks: no hooks declared, 1 intentional) |
| Skip rate | 6.0% (10/167) — acceptable, all documented |
| Stale tests (>90d unchanged) | None detected (all files from 2026-07-18) |

### Test Results (gates suite)

| Result | Count |
|--------|-------|
| PASSED | 153 |
| FAILED | 4 (2 caused by this refactor: DRIFT-1, DRIFT-3; 1 related: DRIFT-4; 1 pre-existing: DRIFT-5) |
| SKIPPED | 10 |
| **Total** | **167** |

---

## Section 5 — Runtime Validation (Phase 5)

### Test Results — AC-specific

| Test | Result | Evidence |
|------|--------|----------|
| `test_gate_project_compose.py` (4 tests) | ✅ ALL PASS | `4 passed in 0.04s` — AC-4 полностью зелёный |
| `tests/gates/` (full suite) | ⚠️ 4 FAILED, 153 PASSED | См. Drift Analysis — 2 failures direct (DRIFT-1, DRIFT-3), 1 related (DRIFT-4), 1 pre-existing (DRIFT-5) |
| `tests/test_smoke_nginx.py` (macOS) | ✅ 3 PASS, 2 SKIP | AC-5 — 2 skipped with correct rationale: "macOS: Linux-parity in CI" |
| `tests/test_contract_entrypoints.py` (verify) | ✅ 4 PASS | `test_entrypoint_exists`, `test_entrypoint_has_shebang`, `test_entrypoint_bash_syntax`, `test_entrypoint_help_smoke` — все PASS |

### LDD Trace Analysis

**Ключевые IMP:9 логи (gate project compose):**
```
[IMP:9][validate] Ports published in service(s): web — use proxy-net instead
[IMP:9][validate] PASS: No services expose ports
[IMP:9][validate] No service has 'networks.proxy-net.aliases' — add at least one alias for deterministic hostname
[IMP:9][validate] PASS: proxy-net with alias found
[IMP:9][validate] PASS: env_file .env.platform found
[IMP:9][validate] No service has 'env_file: .env.platform' — platform environment injection required
[IMP:9][test_no_ports_published] PASS: ports violation correctly detected
[IMP:9][test_proxy_net_with_alias] PASS: missing alias correctly detected
[IMP:9][test_env_file_platform_present] PASS: missing env_file correctly detected
[IMP:9][test_valid_project_passes] PASS: valid compose produces no errors
```

**Ключевые IMP:9 логи (verify.sh):**
```
[IMP:7][main] Starting post-deploy verification for node=<name>
[IMP:8][resolve-yaml] Found node.yaml (path 1): ...
[IMP:7][parse-yaml] Parsing projects with expose:true from ...
[IMP:9][verify] No expose:true domains found in node.yaml for node=<name>
[IMP:7][curl] Checking https://<domain>/ (timeout=10s)
[IMP:9][verify] ALL DOMAINS PASS — HTTP 200 for all N domain(s)
[IMP:9][verify] SOME DOMAINS FAILED — review output above
```

**Ключевые IMP:9 логи (nginx reload hook):**
```
[IMP:7][nginx-hook] Starting nginx reload hook (project=..., node=...)
[IMP:10][nginx-hook] nginx -t FAILED — config error detected, NOT reloading
[IMP:9][nginx-hook] nginx reload SKIPPED (config invalid, old config kept running)
[IMP:9][nginx-hook] nginx -t OK — config is valid
[IMP:9][nginx-hook] nginx reloaded successfully (project=..., node=...)
```

**Ключевые IMP:9 логи (deploy CI workflow):**
```
[IMP:9][resolve-node] target_node=tronyx-vps
[IMP:9][resolve-node] ssh_host=...
[IMP:9][deliver] Delivering to <host>: docker-compose.yml ai-platform.yaml ...
[IMP:9][deliver] Delivery complete
[IMP:9][verify] Starting post-deploy verification for <project> on <node>
[IMP:10][verify] Verification PASSED — all endpoints healthy for <project>
```

### Anti-Illusion Verdict

**PASS** — Business logic IMP:9 logs present in all test trajectories. LDD traces confirm actual execution paths match design contracts.

### Acceptance Criteria Verification

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| **AC-1** | proxy-net alias = project name в шаблонах | ✅ PASS | `grep aliases: templates/template-*/docker-compose.yml` → все 3 файла содержат `aliases: ["__PROJECT_NAME__"]` |
| **AC-2** | add-vhost.sh генерирует variable-based proxy_pass | ✅ PASS | `add-vhost.sh` line 249: `set \$upstream_${project_name} ${project_name}:80;`, line 250: `proxy_pass http://\$upstream_${project_name};` |
| **AC-3** | nginx reload вызывается автоматически после project-deploy | ✅ PASS | `module.yaml` hooks: `on_project_deploy: nginx_reload_hook.sh`; `deploy-project.sh` line 1016: `_trigger_deploy_hooks`; `nginx_reload_hook.sh` validates + reloads |
| **AC-4** | `make gate MODE=fast` FAIL при ports в project-compose | ✅ PASS | `test_gate_project_compose.py`: 4/4 PASS (test_no_ports_published, test_proxy_net_with_alias, test_env_file_platform_present, test_valid_project_passes) |
| **AC-5** | macOS SKIP 2 smoke-теста | ✅ PASS | macOS: `test_nginx_tls_cert_san` SKIPPED, `test_nginx_error_page` SKIPPED с `reason="macOS: Linux-parity in CI"` |
| **AC-6** | `make verify NODE=tronyx-vps` HTTP 200 | ⚠️ PARTIAL | Verify.sh exists, `bash -n` ✅, `--help` ✅, contract tests ✅ (4/4 PASS). **Full проверка требует live VPS SSH доступа** |
| **AC-7** | CI workflow: platform-deliver + verify шаги | ✅ PASS | `deploy-project.yml` lines 64-81: "Deliver project payload" (tar→SSH platform-deliver); lines 91-99: "Post-deploy verify" (make verify). YAML validation ✅ |
| **AC-8** | TRAP[DEBT] на Option C | ✅ PASS | `02-Debt.md` exists (324 lines). Содержит TRAP[DEBT], TRAP[DECISION] markers, rationale deferral, 5-phase migration path |

---

## Section 6 — Config Sync Audit (Phase 6)

### Env variable propagation chain

Проверка не required — рефакторинг не добавляет новых env-переменных.

### Compose override consistency

| File | Status | Notes |
|------|--------|-------|
| `templates/template-frontend/docker-compose.yml` | ✅ | proxy-net alias: `__PROJECT_NAME__`, env_file: `.env.platform`, NO ports |
| `templates/template-backend/docker-compose.yml` | ✅ | proxy-net alias: `__PROJECT_NAME__`, env_file: `.env.platform`, NO ports |
| `templates/template-fullstack/docker-compose.yml` | ✅ | proxy-net alias: `__PROJECT_NAME__`, env_file: `.env.platform`, NO ports |

### Network consistency

| Network | Status | Notes |
|---------|--------|-------|
| proxy-net | ✅ | Определена как external в шаблонах, aliases присутствуют |

---

## Issues for Fix Loop

| # | Severity | Issue | File | Suggested Fix | Task |
|---|----------|-------|------|---------------|------|
| I-1 | 🔴 CRITICAL | `nginx_reload_hook.sh` not registered in entrypoint-manifest.yaml | `core/modules/nginx/nginx_reload_hook.sh` | Add `core/modules/nginx/nginx_reload_hook.sh` to `core/entrypoint-manifest.yaml` as delegates_to path or add to _SHEBANG_EXCEPTION_PATTERNS | TASK-3 follow-up |
| I-2 | 🔴 CRITICAL | `test_gate_project_compose.py` not registered in entrypoint-manifest.yaml gates: | `tests/gates/test_gate_project_compose.py` + `core/entrypoint-manifest.yaml` | Add 4 entries to `gates:` section for project_compose tests | TASK-4 follow-up |
| I-3 | 🟡 HIGH | `verify` verb missing from core/AGENTS.md canonical operations table | `core/AGENTS.md` | Add row for `make verify` target: operation, signature, delegation path | TASK-6 follow-up |
| I-4 | 🟡 HIGH | `verify.sh` exceeds thin wrapper LOC limit (256 > 150) | `core/entrypoints/verify.sh` | Extract business logic (YAML parsing, curl loop) into `core/internal/verify/verify-domains.sh` and keep entrypoint as thin delegator | TASK-6 follow-up |

---

## Fix Cycle 1 Results

### Summary

| # | Issue | Severity | Status | Result |
|---|-------|----------|--------|--------|
| I-1 | `nginx_reload_hook.sh` not registered in entrypoint-manifest.yaml | CRITICAL | ✅ FIXED | Registered in `module_hooks:` section with path + description |
| I-2 | `test_gate_project_compose.py` not registered in manifest `gates:` | CRITICAL | ✅ FIXED | Registered as `id: project-compose` with `test_file: test_gate_project_compose.py` |
| I-3 | `verify` verb missing from `core/AGENTS.md` canonical operations table | HIGH | ✅ FIXED | Added at line 49: `make verify | Пост-деплойная HTTPS-верификация | make verify NODE=<node> | core/entrypoints/verify.sh → core/internal/verify/verify-domains.sh` |
| I-4 | `verify.sh` exceeds thin wrapper LOC limit (256 > 150) | HIGH | ✅ FIXED | Refactored: business logic (YAML parsing, curl loop) extracted to `core/internal/verify/verify-domains.sh`. verify.sh now 89 LOC (≤ 150), passes thin-wrapper gate test (4/4 PASS) |

### New Issues Introduced by Fix Cycle

| # | Severity | Issue | File | Root Cause |
|---|----------|-------|------|------------|
| I-5 | 🔴 CRITICAL | `core/internal/verify/verify-domains.sh` not referenced in entrypoint-manifest.yaml (delegation chain incomplete) | `core/entrypoint-manifest.yaml` line 176 | `verify` entry has `delegates_to: core/entrypoints/verify.sh` but missing `→ core/internal/verify/verify-domains.sh`. Gate `test_all_shebang_files_in_manifest` FAIL |
| I-6 | 🔴 CRITICAL | `core/internal/verify/verify-domains.sh` detected as dead code | `core/internal/verify/verify-domains.sh` | Dynamic variable call (`"${internal_script}"`) in verify.sh not statically resolvable. Script has no `delegates_to` reference in manifest and no static source/exec reference. Gate `test_all_internal_scripts_reachable` FAIL |

### Root Cause Analysis

Both new failures (I-5, I-6) stem from the same root: the `delegates_to` field for the `verify` target in `entrypoint-manifest.yaml` (line 174-177) lists only `core/entrypoints/verify.sh` but not the full delegation chain to `core/internal/verify/verify-domains.sh`.

Compare with other entries that correctly document the full chain:
- `deploy`: `core/entrypoints/deploy.sh → ... → core/internal/deploy/deploy-project.sh → ...`
- `healthcheck`: `core/entrypoints/healthcheck.sh → Module healthcheck.sh scripts + core/internal/healthcheck/tor-proxy-healthcheck.sh`

**Fix:** Update line 176 from:
```
delegates_to: core/entrypoints/verify.sh
```
to:
```
delegates_to: core/entrypoints/verify.sh → core/internal/verify/verify-domains.sh
```

### Pre-existing Failures

| Test | Status | Bug |
|------|--------|-----|
| `test_executed_tests_greater_than_zero` | ❌ STILL FAILS | JUnit XML shows 3 test errors. Pre-existing (DRIFT-5 in original report). Not related to this refactor or fix cycle. |

### Gate Test Suite Comparison

| Metric | Before Fix Cycle | After Fix Cycle | Δ |
|--------|-----------------|-----------------|---|
| PASSED | 153 | **154** | +1 (verify.sh thin-wrapper now passes) |
| FAILED | 4 | **3** | -1 (I-1+I-2+I-3+I-4 fixed, but I-5+I-6 introduced) |
| SKIPPED | 10 | **10** | 0 |

**Провалы после fix cycle:**
1. `test_all_shebang_files_in_manifest` — I-5 (NEW, CRITICAL)
2. `test_all_internal_scripts_reachable` — I-6 (NEW, CRITICAL)
3. `test_executed_tests_greater_than_zero` — pre-existing JUnit XML error

### LDD Trajectory

```
--- LDD TRAJECTORY (IMP:7-10) ---
[IMP:7][conftest][_load_test_env] Loaded .env: .../core/modules/hermes-agent/.env
[IMP:9][conftest][pytest_collection_modifyitems] Gate marker required for gate tests — 167 items
[IMP:8][test_all_shebang_files_in_manifest] Found 91 .sh files under core/
[IMP:9][test_all_shebang_files_in_manifest] FAIL: Unregistered script 'core/internal/verify/verify-domains.sh'
[IMP:9][test_all_internal_scripts_reachable] DEAD_CODE: 1 internal script(s) without live caller
[IMP:10][test_all_internal_scripts_reachable] FAIL: no caller found for core/internal/verify/verify-domains.sh
[IMP:9][test_entrypoint_no_direct_binary_calls] PASS — all 13 entrypoints clean
[IMP:9][test_executed_tests_greater_than_zero] JUnit XML: collected=1109, executed=1081, errors=3, failures=2, skipped=28
[IMP:10][test_executed_tests_greater_than_zero] AssertionError: JUnit XML shows 3 test errors
[IMP:9][conftest][sessionfinish] FAILURES DETECTED — attempt #0
--- END LDD TRAJECTORY ---
```

### Anti-Illusion Verdict

**PASS** — IMP:9-10 logs present in all failing tests. Business logic confirms the specific failures and their root causes. No silent passes.

---

## Дополнительные проверки

| Check | Result |
|-------|--------|
| `bash -n core/internal/scaffold/add-vhost.sh` | ✅ exit 0 |
| `bash -n core/modules/nginx/nginx_reload_hook.sh` | ✅ exit 0 |
| `bash -n core/internal/deploy/deploy-project.sh` | ✅ exit 0 |
| `bash core/entrypoints/verify.sh --help` | ✅ exit 0 (usage printed to stdout) |
| `python3 -c 'import yaml; yaml.safe_load(open("core/modules/nginx/module.yaml"))'` | ✅ OK |
| `python3 -c 'import yaml; yaml.safe_load(open(".github/workflows/deploy-project.yml"))'` | ✅ OK |

---

## Semantic Verdict

```
VERDICT: DRIFTED (CRITICAL)
```

**Rationale:** Fix Cycle 1 успешно исправил 4 из 4 исходных issues (I-1..I-4 ✅). Однако рефакторинг verify.sh (I-4) создал новый internal-скрипт `core/internal/verify/verify-domains.sh`, который не зарегистрирован в delegation chain entrypoint-manifest.yaml. Это привело к 2 новым CRITICAL failures:

1. **I-5 (CRITICAL):** `core/internal/verify/verify-domains.sh` не указан в `delegates_to:` для `verify` таргета в entrypoint-manifest.yaml — CI gate `test_all_shebang_files_in_manifest` FAIL
2. **I-6 (CRITICAL):** `core/internal/verify/verify-domains.sh` не имеет живого caller'а — динамический вызов через переменную `${internal_script}` не детектируется статическим анализатором, и manifest не содержит ссылки на скрипт
3. **Pre-existing:** `test_executed_tests_greater_than_zero` FAIL — JUnit XML 3 errors (DRIFT-5, не связан с этим рефакторингом)

**Verdict priority:** BROKEN > **DRIFTED** > DEGRADED > STABLE

**Recommendation (Fix Cycle 2):** Исправить один байт в `core/entrypoint-manifest.yaml` — строка 176: добавить полную цепочку делегирования.

```yaml
# Было:
delegates_to: core/entrypoints/verify.sh
# Стало:
delegates_to: core/entrypoints/verify.sh → core/internal/verify/verify-domains.sh
```

После фикса ожидается: 156 PASS, 1 FAIL (pre-existing JUnit XML error).

$END_VERIFICATION_REPORT
