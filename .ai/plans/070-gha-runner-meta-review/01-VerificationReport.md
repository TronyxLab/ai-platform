# VerificationReport 01 — Meta Review: DevPlan 070 (GHA Self-Hosted Runner)

$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               QA verification of DevPlan 070 (meta-review of DevPlan 069 gha-runner) before implementation. Cross-file drift detection, invariant verification, and language policy compliance check.
DESCRIPTION:           Verification performed across 3 phases: static audit of DevPlan 070, cross-file drift detection (secret-definitions.yaml, CI workflows, module contracts, AGENTS.md invariants), and invariant verification against platform architecture. Identified 2 CRITICAL drifts, 3 HIGH issues, and 2 MEDIUM issues requiring plan amendment before implementation.
RATIONALE:             DevPlan 070 corrects 3 critical errors from DevPlan 069 (PAT scope, registration flow, `--once` storm) and integrates 22 accepted proposals from 3 expert reviews. However, the corrected plan introduces new drifts against platform policies (language policy, system-module contract) that must be resolved before Coder begins Wave 0.
ACCEPTANCE_CRITERIA:
  AC-REPORT-1: All 27 proposals from the meta-review verified for correct classification (ACCEPT/MODIFY/REJECT)
  AC-REPORT-2: Cross-file drift between DevPlan 070 and platform invariants documented with file:line evidence
  AC-REPORT-3: Language policy compliance verified for all new shell scripts
  AC-REPORT-4: Module contract compliance verified against core/modules/AGENTS.md
  AC-REPORT-5: Secret-definitions.yaml SSoT completeness verified
  AC-REPORT-6: CI workflow migration plan verified against actual workflow inventory
IMPLEMENTS:            QA Phase 1 (static audit), Phase 2 (cross-file drift), Phase 3 (invariant verification)
IMPACTS:
  - .ai/plans/070-gha-runner-meta-review/01-VerificationReport.md — настоящий отчёт
  - Требуется amendment DevPlan 070 перед реализацией
REQUIRES:
  - DevPlan 070 (meta-review plan)
  - DevPlan 069 (original plan, 689 lines)
  - AGENTS.md root (architectural invariants, language policy)
  - core/modules/AGENTS.md (system-module contract)
  - core/secret-definitions.yaml (SSoT)
  - .github/workflows/*.yml (9 files)
  - core/templates/module-system.mk (systemd template)
  - core/lib/module-interface.sh (cross-layer dispatch)
$END_ARTIFACT_CONTRACT

---

🔒 Verified against SHA `99fdfe50752473acc557615d65492e6e3e6358a2`
⚠️ Dirty state: 2 files modified outside plan scope (`core/internal/scripts/generate_entrypoint_manifest.py`, `core/modules/status-page/docker-compose.base.yml`) — не влияет на верификацию.

---

## 1. Static Audit (Phase 1)

### 1.1 DevPlan 070 Structure Compliance

| Check | Status | Evidence |
|-------|--------|----------|
| $START_DEVPLAN / $END_DEVPLAN | ✅ PASS | Lines 3, 1210 |
| $ARTIFACT_CONTRACT (7 fields) | ✅ PASS | Lines 5-32: PURPOSE, DESCRIPTION, RATIONALE, ACCEPTANCE_CRITERIA, IMPLEMENTS, IMPACTS, REQUIRES |
| Section completeness | ✅ PASS | Sections 0-11 present, logically ordered |
| TRAP annotations format | ✅ PASS | 9 TRAP annotations with date, severity, rationale |
| Code blocks syntax | ✅ PASS | bash/ini/yaml/prometheus blocks properly fenced |
| Cross-references to DevPlan 069 | ⚠️ WARNING | References "см. DevPlan 069 §3.2" (line 1130) — DevPlan 069 exists (verified), reference is valid |

### 1.2 DevPlan 070 Internal Consistency

| Check | Status | Evidence |
|-------|--------|----------|
| S1-S3 critical errors: all 3 reviews agree | ✅ PASS | Correctly identified and fixed |
| S4-S8 bugs: all accepted with fixes | ✅ PASS | 5/5 bugs addressed |
| S9-S17 operational improvements | ✅ PASS | 9/9 accepted with platform adaptation |
| S18-S20 security | ✅ PASS | 3/3 accepted |
| S21-S22 monitoring | ✅ PASS | 2/2 accepted |
| S23-S24 MODIFY decisions | ✅ PASS | Valid rationale for partial acceptance |
| S25-S27 REJECT decisions | ✅ PASS | Each with TRAP and rev condition |
| Summary statistics match | ✅ PASS | ACCEPT:22, MODIFY:2, REJECT:3 = 27 total |
| Delta table (section 11) | ✅ PASS | 16 rows, accurate reflection of changes |
| **Makefile include vs S7 resolution** | 🔴 **FAIL** | Section 1 says `Makefile # SERVICE_NAME=gha-runner → include module-system.mk` but Section 0.2 S7 says "module-system.mk НЕ используется (install.sh переопределяет install-таргет)" — contradiction |

### 1.3 Summary

| Severity | Count | Findings |
|----------|-------|----------|
| CRITICAL | 2 | DRIFT-1, DRIFT-3 (see Phase 2) |
| HIGH | 3 | DRIFT-2, DRIFT-4, DRIFT-5 |
| MEDIUM | 2 | DRIFT-6, DRIFT-7 |
| WARNING | 1 | Cross-reference to DevPlan 069 §3.2 — valid but requires verification |

---

## 2. Drift Analysis (Phase 2)

### DRIFT-1 [CRITICAL] Language Policy Violation — inline `python3 -c` in new shell scripts

- **Files involved:**
  - DevPlan 070 lines 114, 229, 301, 334, 938, 1017 — 6 occurrences of `python3 -c "..."`
  - `AGENTS.md` root lines 147, 151 — language policy: "Inline Python и heredoc — сигнал к извлечению"
- **Expected:** Each inline python3 block extracted into a `.py` module per Strangler-Trigger Tier 1: "Добавление нового `python3 -c '...'` или heredoc-блока → вынести эту конкретную логику в отдельный `.py` модуль. Не переписывать всю подсистему."
- **Actual:** 4 new shell scripts (register.sh, unregister.sh, runner-drain.sh, healthcheck.sh) contain inline python3 for JSON parsing of GitHub API responses.
- **Impact:** Direct policy violation. During implementation, Coder will either (a) violate policy and create technical debt, or (b) need to extract Python modules mid-implementation, causing rework.
- **Fix:** Replace all `python3 -c` blocks with calls to a shared Python module. Proposed structure:
  ```
  core/modules/gha-runner/gha_api.py  # Python module with:
    - get_registration_token(pat, org) -> str
    - get_removal_token(pat, org) -> str
    - get_runner_id(pat, org, runner_name) -> str
    - get_runner_status(pat, org, runner_name) -> str
  ```
  Shell scripts call: `reg_token=$(python3 "${SCRIPT_DIR}/gha_api.py" registration-token "${PAT}" "${ORG}")`
- **Severity justification:** AGENTS.md line 163 explicitly documents the enforcement mechanism: "pre-commit hook на новые inline python3". A CI pre-commit hook would block these files from being committed, making the plan unimplementable as written.

### DRIFT-2 [HIGH] Template unit + module-system.mk incompatibility — unresolved contradiction

- **Files involved:**
  - DevPlan 070 §1 (line 706): `Makefile # SERVICE_NAME=gha-runner → include module-system.mk`
  - DevPlan 070 §0.2 S7 (line 248): "module-system.mk НЕ используется (install.sh переопределяет install-таргет)"
  - `core/templates/module-system.mk` line 33-36: `cp *.service → systemctl enable $(SERVICE_NAME) → systemctl restart $(SERVICE_NAME)`
- **Expected:** Clear resolution: either Makefile includes module-system.mk with valid template unit handling, or it doesn't include it and provides all targets manually.
- **Actual:** The DevPlan says both things in different sections.
- **Impact:** During implementation, Coder will be confused about which approach to take. If module-system.mk is included with `SERVICE_NAME=gha-runner`, then `systemctl enable gha-runner` will fail because there is no `gha-runner.service` (only `gha-runner@.service` template).
- **Fix:** Choose and document one approach:
  - **Option A (recommended):** Do NOT include module-system.mk. Provide custom Makefile with `install`, `status`, `restart`, `logs` targets that handle template units correctly (e.g., iterate over enabled orgs from node.yaml, or accept `ORG=` parameter).
  - **Option B:** Include module-system.mk but override the `install` target only. Define `SERVICE_NAME` as a variable, not a fixed value, and adapt `status`/`restart`/`logs` targets to accept `ORG=` parameter.

### DRIFT-3 [CRITICAL] Internal contradiction — module-system.mk `install` vs custom `install.sh`

- **Files involved:**
  - DevPlan 070 §1 (line 706): structure shows Makefile including module-system.mk
  - DevPlan 070 §7 Wave 1 E1.2 (line 1096): "install.sh (с preflight checks)"
  - DevPlan 070 §0.2 S7 (line 248): "install.sh модуля сам управляет systemctl enable"
  - `core/templates/module-system.mk` lines 28,31-37: `.PHONY: install` with `systemctl enable` + `systemctl restart`
- **Expected:** Single source of truth for installation procedure.
- **Actual:** Three competing installation mechanisms: (a) module-system.mk `install` target (broken for template units), (b) install.sh called via `invoke_module_interface` from deploy-modules.sh, (c) custom Makefile target. The plan doesn't reconcile them.
- **Impact:** If module-system.mk is included AND install.sh exists, `make install` (within the module directory) would use module-system.mk's target, while `make deploy-modules` would call install.sh via invoke_module_interface. Different behaviors in different contexts = operator confusion.
- **Fix:** Follow the platform-secrets pattern:
  - Makefile includes module-system.mk for `status`/`restart`/`logs` targets only
  - `install` target is **overridden** in the module's Makefile (after the include) to call install.sh
  - Document that `make install` from root dispatches through deploy-modules.sh → invoke_module_interface → install.sh, NOT through module-system.mk

### DRIFT-4 [HIGH] Healthcheck.sh for system module — contract violation

- **Files involved:**
  - DevPlan 070 §1 (line 708): `healthcheck.sh` in gha-runner module structure
  - DevPlan 070 §2 module.yaml (line 745): `interfaces: [install, healthcheck, unregister]`
  - `core/modules/AGENTS.md` line 70: "System-модули НЕ содержат: docker-compose.base.yml, healthcheck.sh, .dockerignore."
  - `core/modules/AGENTS.md` line 124: healthcheck contract assumes Docker — `check_docker_health "$CONTAINER"` is the liveness default
  - `core/lib/module-interface.sh` lines 137-143: dispatches `healthcheck` interface by looking for `healthcheck.sh`
- **Expected:** Either (a) system-module contract is updated to allow healthcheck.sh for modules that need API-based deep health checks, or (b) gha-runner uses a differently-named script.
- **Actual:** DevPlan 070 includes healthcheck.sh in a system-type module, violating the explicit contract.
- **Impact:** Contract violation creates precedent drift. Future system modules may copy this pattern without updating the contract, leading to fragmented conventions.
- **Fix:** Add a carve-out to `core/modules/AGENTS.md` system-module contract:
  ```
  **Исключение:** Системные модули с внешними зависимостями (API-based health) МОГУТ
  предоставлять healthcheck.sh. Liveness default НЕ использует docker inspect —
  вместо этого systemctl is-active. Deep mode — произвольные проверки.
  ```
  Or alternatively: rename to `runner-health.sh` and register a different interface in module.yaml.

### DRIFT-5 [HIGH] Missing secret-definitions.yaml entries specification

- **Files involved:**
  - DevPlan 070 §2 module.yaml (lines 747-757): `env_requires` declares `GHA_RUNNER_PAT`, `GHA_RUNNER_PAT_TRONYX161`, `GHA_RUNNER_PAT_TRONYX_LAB`
  - `core/secret-definitions.yaml` — SSoT: no GHA_RUNNER_PAT* entries exist
  - DevPlan 070 §7 Wave 2 E2.1 (lines 1105-1113): mentions adding to `secrets.enc.yaml` and `core/secret-definitions.yaml`
- **Expected:** Each new secret in SSoT has: `name`, `tier`, `source`, `charset`, `ci_default`, `note`.
- **Actual:** DevPlan 070 provides only the name. Missing: tier classification, charset regex (Classic PAT format: `^ghp_[A-Za-z0-9]+$`), ci_default test value, source (`sops`).
- **Impact:** During implementation (Wave 2), Sysadmin must reverse-engineer the correct SSoT format from existing entries, risking format drift.
- **Fix:** Add explicit entries to the DevPlan:
  ```yaml
  - name: GHA_RUNNER_PAT
    tier: required
    source: sops
    charset: "^ghp_[A-Za-z0-9]+$"
    ci_default: "ghp_test-gha-runner-pat-for-ci"
    note: "Classic PAT с admin:org scope. Используется только register.sh для получения registration token (не живёт в процессе раннера). Fallback если per-org PAT отсутствует."
  - name: GHA_RUNNER_PAT_TRONYX161
    tier: required
    source: sops
    charset: "^ghp_[A-Za-z0-9]+$"
    ci_default: "ghp_test-gha-runner-pat-tronyx161"
    note: "Classic PAT для source-org tronyx161."
  - name: GHA_RUNNER_PAT_TRONYX_LAB
    tier: required
    source: sops
    charset: "^ghp_[A-Za-z0-9]+$"
    ci_default: "ghp_test-gha-runner-pat-tronyx-lab"
    note: "Classic PAT для контекстной org tronyx-lab."
  ```

### DRIFT-6 [MEDIUM] CI workflow migration count mismatch — 6 vs 7

- **Files involved:**
  - DevPlan 070 §7 Wave 3 (line 1129): "Миграция 6 workflow на runs-on: self-hosted"
  - DevPlan 070 §0.8 summary table (line 693): ACCEPT:22, but doesn't specify migration count
  - DevPlan 069 §3.2 (lines 492-504): table lists 7 workflows as `self-hosted` candidates (push-gate, platform-test, build-platform, nightly-gate, mirror, + 2 optional deploy workflows)
  - `.github/workflows/` directory: 9 workflow files exist (platform-deploy.yml, stage-deploy.yml are explicitly excluded)
- **Expected:** Consistent count between plans, or explicit justification for discrepancy.
- **Actual:** DevPlan 070 says "6 workflow" in Wave 3 but DevPlan 069 lists 7 (or 5 definite + 2 optional). The exact set is underspecified.
- **Fix:** Explicitly list the 6 workflows in DevPlan 070 §7 Wave 3, with rationale for any excluded ones.

### DRIFT-7 [MEDIUM] Fragile error handling in API calls

- **Files involved:**
  - DevPlan 070 line 114: `reg_token=$(curl ... | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")` — no HTTP status check
  - DevPlan 070 line 334: `api_status=$(curl ... | python3 -c "..." 2>/dev/null)` — error suppression
- **Expected:** Robust error handling: check HTTP status code, handle API error responses (rate limits, auth failures, network errors).
- **Actual:** `curl` pipes directly to `python3 -c` which will crash with `KeyError: 'token'` on any non-200 response. `2>/dev/null` suppresses the traceback, leaving an empty string that's caught by `[ -z "${reg_token}" ]` — but the error message is generic ("Failed to obtain registration token") without the actual HTTP error.
- **Impact:** Production debugging difficulty. When PAT expires or GitHub API is rate-limited, the operator sees a generic error without HTTP status code or response body.
- **Fix:** The extracted Python module (see DRIFT-1 fix) should handle HTTP errors explicitly: check `response.status_code`, parse error JSON, log `[IMP:9] GitHub API error: HTTP {status} — {message}`.

---

## 3. Invariant Status (Phase 3)

Architectural invariants from `AGENTS.md` root §MODULE_CONTRACT verified against DevPlan 070:

| # | Invariant | Status | Evidence | Risk |
|---|-----------|--------|----------|------|
| 1 | Makefile — единый фасад. Все операции через `make <target>`. | ✅ HELD | DevPlan proposes `make runner-register`, `make runner-drain`, `make unregister` — новые канонические таргеты через entrypoints | Низкий — нужна регистрация в entrypoint-manifest.yaml |
| 2 | Модель деплоя: git push → CI → forced-command. | ✅ HELD | gha-runner — system-модуль, доставляется через core-канал (SCP/rsync), не через git | Низкий |
| 3 | org = context. | ✅ HELD | S25 REJECT явного GITHUB_ORG в node.yaml — сохранён инвариант | Низкий |
| 4 | AGENTS.md — канонические файлы. | ✅ HELD | Не затрагивает | Низкий |
| 5 | entrypoint-manifest.yaml — реестр операций. | ⚠️ AT_RISK | Новые таргеты (`runner-register`, `runner-drain`, `runner-enable`, `unregister`) должны быть зарегистрированы в манифесте и core/AGENTS.md. DevPlan это не упоминает. | Средний — CI gate `check-manifests` заблокирует divergence |
| 6 | make bootstrap-node — идемпотентный. | ✅ HELD | Модуль opt-in (modules: в node.yaml), не затрагивает bootstrap | Низкий |
| 7 | Полный локальный стек через docker compose up. | ✅ HELD | System-модуль, не входит в docker compose стек | Низкий |
| 8 | LiteLLM — PostgreSQL. | ✅ HELD | Не затрагивает | Низкий |
| 9 | Тестовый сервер может быть пересоздан. | ✅ HELD | Не затрагивает | Низкий |
| 10 | Сборка образов hermes. | ✅ HELD | Не затрагивает | Низкий |
| 11 | Manifest Generation Contract. | ⚠️ AT_RISK | Новый модуль gha-runner должен быть discoverable через `make discover-modules` (system-модули не auto-discover через docker-compose include). Секреты должны быть в secret-definitions.yaml. Новые таргеты — в entrypoint-manifest.yaml. | Средний — без регистрации `make check-manifests` будет красным |

### Additional Policy Verification

| Policy | Status | Evidence |
|--------|--------|----------|
| **Языковая политика — Python-first** | 🔴 VIOLATED | 6 inline `python3 -c` в 4 новых bash-скриптах. Tier 1 Strangler-Trigger: "Добавление нового python3 -c → вынести в отдельный .py модуль." |
| **System-module contract** | 🔴 VIOLATED | healthcheck.sh в system-модуле (запрещено core/modules/AGENTS.md line 70) |
| **module.yaml D5 schema** | ✅ HELD | Предложенный module.yaml соответствует D5 (install_type: system, interfaces, env_requires typed) |
| **Secrets flow — SOPS/age** | ✅ HELD | PAT'ы хранятся в secrets.enc.yaml, расшифровываются через platform-secrets |
| **Forbidden verbs** | ✅ HELD | Новые глаголы (`runner-register`, `runner-drain`, `runner-enable`, `unregister`) не входят в forbidden-список |
| **Cross-layer import rules** | ✅ HELD | System-модуль не импортирует из internal/ |

---

## 4. Test Quality (Phase 4)

⚠️ Фаза 4 не выполнялась полностью — модуль ещё не реализован, тестов нет. Выполнена оценка тестовых потребностей:

### 4.1 Anticipated Test Coverage Gaps

| Gap | Description | Risk |
|-----|-------------|------|
| GAP-1 | No test for registration token flow (PAT → API → reg_token → config.sh) | HIGH — критический путь, ошибка ломает деплой |
| GAP-2 | No test for checksum verification failure path | MEDIUM — supply chain security |
| GAP-3 | No test for version-aware update (`.version` file logic) | MEDIUM — regression risk |
| GAP-4 | No test for unregister flow (token retrieval, config.sh remove) | MEDIUM — мёртвые раннеры в GitHub UI |
| GAP-5 | No test for drain/enable mode transitions | LOW — операционное удобство |
| GAP-6 | No negative test: invalid PAT → graceful error | HIGH — отказоустойчивость |

### 4.2 Test Strategy Recommendation

Новые тесты должны быть размещены в `tests/gates/` как gate-тесты (инварианты модуля) и в `tests/modules/` как модульные тесты:
- `test_gate_gha_runner_install.py` — registration flow, checksum, preflight
- `test_gate_gha_runner_healthcheck.py` — deep healthcheck через GitHub API
- `test_gate_gha_runner_secrets.py` — PAT доступность, env var propagation
- `test_module_gha_runner_api.py` — unit-тесты Python модуля для GitHub API

---

## 5. Runtime Validation (Phase 5)

⛔ Пропущена — модуль gha-runner не реализован. Файлы `core/modules/gha-runner/` не существуют.

Валидация будет выполнена после реализации (Waves 0-3).

---

## 6. Config Sync Audit (Phase 6)

### 6.1 Env Variable Propagation Chain

| Variable | .env | .env.example | secret-definitions.yaml | compose | CI workflows | SMOKE_ENV (conftest.py) |
|----------|-----|-------------|------------------------|---------|-------------|------------------------|
| GHA_RUNNER_PAT | ✗ | ✗ | ✗ (missing) | N/A (system module) | ✗ | ✗ |
| GHA_RUNNER_PAT_TRONYX161 | ✗ | ✗ | ✗ (missing) | N/A | ✗ | ✗ |
| GHA_RUNNER_PAT_TRONYX_LAB | ✗ | ✗ | ✗ (missing) | N/A | ✗ | ✗ |

**Finding:** Все три переменные отсутствуют во всей propagation chain. Это ожидаемо (модуль не создан), но подтверждает DRIFT-5 (missing SSoT entries).

### 6.2 CI Workflow Audit

**Current state:** 9 workflow files, все используют `runs-on: ubuntu-latest` (12 occurrences).

**Migration plan (DevPlan 069 §3.2):**

| Workflow | Current | Planned | Definite/Optional |
|----------|---------|---------|-------------------|
| push-gate.yml | ubuntu-latest | self-hosted | Definite |
| platform-test.yml | ubuntu-latest | self-hosted | Definite |
| build-platform.yml | ubuntu-latest | self-hosted | Definite |
| nightly-gate.yml | ubuntu-latest | self-hosted | Definite |
| mirror.yml | ubuntu-latest | self-hosted | Definite |
| core-deploy.yml | ubuntu-latest | self-hosted or ubuntu-latest | Optional |
| deploy-project.yml | ubuntu-latest | self-hosted or ubuntu-latest | Optional |
| platform-deploy.yml | ubuntu-latest | ubuntu-latest | NOT migrated |
| stage-deploy.yml | ubuntu-latest | ubuntu-latest | NOT migrated |

**Finding:** DevPlan 070 says 6 workflows. DevPlan 069 lists 5 definite + 2 optional = 7 potential. Count mismatch. Platform-deploy.yml has 3 jobs; stage-deploy.yml has 2 jobs — these remain on GitHub-hosted.

### 6.3 Node Configuration Audit

**node.yaml (tronyx-vps):**
- `context: tronyx-lab` → Org = tronyx-lab ✓ (соответствует GHA_RUNNER_PAT_TRONYX_LAB)
- Current `modules:` list: 14 modules, НЕ включает gha-runner (ожидаемо)
- После реализации: добавить `{name: gha-runner, enabled: true}`

**secrets/tronyx-vps.enc.yaml:**
- Файл существует (2424 bytes)
- Не содержит GHA_RUNNER_PAT* (ожидаемо, будет добавлено в Wave 2)

---

## 7. Semantic Verdict

```
╔══════════════════════════════════════════════════════════════╗
║                 VERDICT: DRIFTED (CRITICAL)                  ║
╚══════════════════════════════════════════════════════════════╝
```

**Verdict priority chain:** DRIFTED > DEGRADED > STABLE. DRIFTED because 2 CRITICAL drifts found.

**Rationale:**

DevPlan 070 выполняет свою основную задачу — мета-анализ 27 предложений из 3 рецензий проведён безупречно. Все критические ошибки DevPlan 069 (PAT scope, registration flow, `--once` storm) исправлены. 24 из 27 предложений обработаны корректно (22 ACCEPT + 2 MODIFY), 3 REJECT с обоснованными TRAP'ами.

Однако в процессе исправления план приобрёл **2 CRITICAL дрифта**, которые должны быть устранены до начала реализации:

| # | Drift | Severity | Fix complexity |
|---|-------|----------|----------------|
| DRIFT-1 | 6 inline `python3 -c` в новых bash-скриптах → нарушение языковой политики | CRITICAL | Low (extract Python module) |
| DRIFT-2 | Противоречие: Makefile includes module-system.mk vs "НЕ используется" | CRITICAL | Low (clarify in DevPlan) |
| DRIFT-3 | module-system.mk `install` target несовместим с template unit `gha-runner@.service` | CRITICAL | Medium (design decision) |
| DRIFT-4 | healthcheck.sh в system-модуле нарушает контракт | HIGH | Low (contract amendment) |
| DRIFT-5 | Отсутствуют SSoT-записи в secret-definitions.yaml | HIGH | Low (add 3 entries) |
| DRIFT-6 | Несоответствие количества мигрируемых workflow (6 vs 7) | MEDIUM | Low (list explicitly) |
| DRIFT-7 | Хрупкая обработка ошибок API (curl pipe) | MEDIUM | Medium (Python module fix) |

**Суммарная оценка:**
- Качество мета-анализа: ⭐⭐⭐⭐⭐ (5/5) — все 27 предложений обработаны, суперпозиция полная
- Качество исправленного плана: ⭐⭐⭐ (3/5) — критические ошибки DevPlan 069 исправлены, но внесены новые дрифты
- Готовность к реализации: ⭐⭐ (2/5) — требуется amendment для DRIFT-1, DRIFT-2, DRIFT-4

---

## 8. Рекомендации

### Перед реализацией (amendment DevPlan 070):

1. **[CRITICAL] Исправить DRIFT-1**: Заменить все 6 inline `python3 -c` на вызовы Python-модуля `core/modules/gha-runner/gha_api.py`. Обновить секции 4-6 DevPlan 070 с новыми code blocks.

2. **[CRITICAL] Исправить DRIFT-2**: Разрешить противоречие module-system.mk — выбрать один подход (рекомендуется Option A: не включать module-system.mk, предоставить кастомный Makefile) и обновить секцию 1 (структура) и секцию 0.2 S7.

3. **[HIGH] Исправить DRIFT-4**: Добавить carve-out в `core/modules/AGENTS.md` system-module контракт для healthcheck.sh в system-модулях с внешними API-зависимостями.

4. **[HIGH] Исправить DRIFT-5**: Добавить 3 записи в secret-definitions.yaml формата в секцию 2 (module.yaml) или отдельную секцию DevPlan 070.

5. **[MEDIUM] Исправить DRIFT-6**: Явно перечислить 6 workflow в Wave 3 (line 1129-1130).

6. **[MEDIUM] Инвариант 5**: Добавить секцию о регистрации новых таргетов в `entrypoint-manifest.yaml` и `core/AGENTS.md` (canonical operations table).

### После реализации (Waves 0-3):

7. Создать gate-тесты для GAP-1, GAP-2, GAP-6 (registration flow, checksum, error handling).
8. Выполнить `make check-manifests` после добавления модуля для верификации manifest generation.
9. Обновить `make discover-modules` если необходимо (system-модули не auto-discover).

---

$END_VERIFICATION_REPORT
