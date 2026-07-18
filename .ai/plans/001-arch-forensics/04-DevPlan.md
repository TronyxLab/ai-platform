<!-- GREP_SUMMARY: DevPlan, arch-forensics-remediation, collapse-fixes, invariant, boundary, path-prefix, observability, 5-waves, gate-hardening, typed-contract -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Debt Intake → ◇ Architecture Overview → ◇ Draft Code Graph → ◇ Data Flow → ◇ §TASKS (5 waves) → ◇ §PARALLEL_GROUPS → ◇ §TEST_SPEC → ◇ File Manifest → ◇ Design Decisions → ⎋ Next Steps -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** DevPlan устранения 4 архитектурных коллапсов ai-platform, выявленных двумя прогонами `arch-forensics`. План реализует 5-волновую стратегию из Brief (03-Brief.md): гибрид Rules+Runtime, закрывающий INVARIANT, BOUNDARY, PATH-PREFIX и OBSERVABILITY коллапсы.
- **DESCRIPTION:** Детальный план имплементации: Draft Code Graph (модули, data flow, зависимости), атомарные задачи ($TASKS), параллельные группы ($PARALLEL_GROUPS), спецификация тестов ($TEST_SPEC), file manifest и design decisions с `## @rationale`. 5 волн: W1 (Observability Coverage) + W2 (Model Surgery) параллельно → W3 (Gate Hardening) → W4 (Path Remediation) → W5 (Runtime Sentinel).
- **RATIONALE:** 01/02 VerificationReports показали персистирующие коллапсы + 2 новых. ~20 коммитов между отчётами не затронули корневые причины. Ad-hoc фиксы не работают — нужен структурированный план с измеримыми acceptance criteria, покрытый новыми гейтами для предотвращения регресса. Гибридная стратегия Rules+Runtime выбрана потому что статический анализ имеет принципиальный потолок (cron, systemd, генерируемые конфиги) — runtime sentinel (W5) ловит то, что статика не может.
- **ACCEPTANCE_CRITERIA:**
  1. Gate система больше не даёт ложных гарантий — все 4 коллапса закрыты или обнаружены гейтами
  2. INVARIANT COLLAPSE закрыт — cross-layer правило машиночитаемо и enforceable; `_looks_like_path` трекает присвоения переменных
  3. BOUNDARY COLLAPSE закрыт — path-consistency gate ловит `/opt/core/` и modules→internal вызовы через cron/systemd
  4. PATH-PREFIX COLLAPSE закрыт — `rg '/opt/core/' core/` возвращает 0 prod-путей; gate предотвращает регресс
  5. OBSERVABILITY COLLAPSE закрыт — postgres + hermes-agent имеют метрики; observability-coverage gate предотвращает регресс
  6. Doc drift устранён — `verify` в glossary, `static`/`static_audit` консистентны
  7. `make gate MODE=fast` зелёный на финальном состоянии (все новые гейты проходят)
- **IMPLEMENTS:** skill `arch-forensics` (collapse detection), skill `doc-protocols` (DevPlan protocol), Brief `03-Brief.md`
- **IMPACTS:** `core/AGENTS.md` (cross-layer rule + typed contract), `core/modules/AGENTS.md` (module interface schema), 13 `module.yaml` (interfaces field, D4 extension), `core/entrypoints/healthcheck.sh` (контрадикция), `tests/test_cross_layer_imports.py` (variable tracking), `core/modules/backup-cron/scripts/crontab` (path fix), `core/templates/sudo-whitelist.template` (path fix), `core/modules/monitoring/config/prometheus.yml.tmpl` (new scrape jobs), `core/modules/infra-metrics/docker-compose.base.yml` (postgres-exporter), 3 новых gate-файла, `core/internal/verify/verify-node-paths.sh` (новый), `core/entrypoint-manifest.yaml` (новые gate entries + verify-node-paths)
- **REQUIRES:** `03-Brief.md`, `01-VerificationReport.md`, `02-VerificationReport.md` того же плана; `core/AGENTS.md`, `core/modules/AGENTS.md`, `entrypoint-manifest.yaml`

$START_DEVPLAN

# DevPlan: Architecture Collapse Remediation — 5 Waves

---

## §Debt Intake

Перед проектированием выполнен аудит существующих TRAP и DEBT-артефактов в зоне изменений:

| Finding | Location | Classification | Disposition |
|---------|----------|---------------|-------------|
| TRAP[DEBT] Makefile:282-287 (gate MODE=fast swallows failures) | Makefile | OBSOLETE | **DEFER** — код уже исправлен (`\|\| { echo FAIL; exit 1; }`), TRAP кандидат на ARCHIVED. Вне скоупа этого DevPlan — отдельная задача чистки TRAP |
| TRAP[DECISION] Makefile:74-75 (up вызывает provision напрямую) | Makefile | KNOWN | **DEFER** — задокументировано, вне скоупа |
| TRAP[DECISION] langfuse base.yml:117 (дубль redis digest) | langfuse/docker-compose.base.yml | KNOWN | **DEFER** — accepted duplication |
| TRAP[DECISION] backup-cron crontab (backup sequence order) | crontab | KNOWN | **DEFER** — design decision, не дефект |
| Dual mechanism: healthcheck (docker inspect + module healthcheck.sh) | system-wide | ARCHITECTURAL | **IN_SCOPE** — W2 typed contract формализует интерфейс |
| Knowledge duplication: PLATFORM_ROOT vs /opt/core/ hardcodes | 5+ files | DRIFT | **IN_SCOPE** — W4 path remediation |

---

## §1. Architecture Overview

### Current State: Collapse Map

```
                    ┌─── ENFORCED ───────────────────────────────────┐
                    │  entrypoints → internal/lib   ✅ 0 violations  │
                    │  module isolation (D4 contract) ✅ 0 violations│
                    │  dual-delivery (NO git core)   ✅ held         │
                    └────────────────────────────────────────────────┘
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            │                         │                         │
     ⚡ INVARIANT              ⚡ BOUNDARY               ⚡ PATH-PREFIX
     COLLAPSE (CRITICAL)       COLLAPSE (HIGH)          COLLAPSE (HIGH)
     internal→modules          modules→internal          /opt/core/ vs
     фиктивный запрет          cron/systemd/hook         /opt/platform/core/
     6 runtime вызовов         3 невидимых канала        5 stale точек
     Gate #8 слеп              Gate #8 слеп              backup-cron silent fail
            │                         │                         │
            └─────────────────────────┼─────────────────────────┘
                                      │
                              ⚡ OBSERVABILITY
                              COLLAPSE (HIGH)
                              postgres severity=critical
                              0 Prometheus scrape
                              hermes-agent без метрик
                              Нет гейта coverage
```

### Target State: After 5 Waves

```
                    ┌─── ENFORCED + GATED ──────────────────────────┐
                    │  entrypoints → internal/lib          ✅       │
                    │  internal → modules (typed contract) ✅ GATED │
                    │  modules → internal (path gate)      ✅ GATED │
                    │  module isolation (D4 contract)      ✅       │
                    │  dual-delivery (NO git core)         ✅       │
                    │  path prefix (/opt/platform/core/)   ✅ GATED │
                    │  observability coverage (∀ sev≥high) ✅ GATED │
                    │  doc consistency (verbs, markers)    ✅ GATED │
                    │  runtime sentinel (post-deploy)      ✅       │
                    └────────────────────────────────────────────────┘
```

### Layers and Contracts (Target)

| Layer | Can call | Mechanism | Enforced by |
|-------|----------|-----------|-------------|
| `entrypoints/` | `internal/`, `lib/` | direct sourcing | Gate #8 (existing) |
| `internal/` | `internal/`, `lib/` | direct sourcing | Gate #8 (existing) |
| `internal/` | `modules/<name>/<interface>` | **typed contract** via `module.yaml interfaces:` | Gate #8 (hardened, W2+W3) |
| `modules/` | `lib/`, `templates/` | direct sourcing + module.mk include | Gate #8 (existing) |
| `modules/` | `internal/` via cron/systemd | **path-consistency gate** detects | Gate path-consistency (new, W3) |
| `prometheus.yml` | all severity≥high services | scrape job enforcement | Gate observability-coverage (new, W1) |

---

## §2. Draft Code Graph

```
┌── Makefile (root facade) ──────────────────────────────────────────┐
│  make gate, make test, make healthcheck, make node-update, ...      │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ delegates to
                                   ▼
┌── core/entrypoints/ ────────────────────────────────────────────────┐
│  healthcheck.sh (← FIX: remove contradiction, add typed contract   │
│                  reference)                                         │
│  audit.sh (← EXTEND: integrate verify-node-paths.sh)               │
│  14 other entrypoints (unchanged)                                   │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ delegates to
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
┌── core/internal/ ──────┐ ┌── core/internal/ ──────┐ ┌── core/internal/verify/
│ bootstrap/              │ │ healthcheck/           │ │ verify-node-paths.sh  (NEW)
│  node-lifecycle.sh      │ │  modules-healthcheck.sh│ │  ├─ cron path verify
│   (EXTEND: call         │ │   (EXTEND: --deep      │ │  ├─ systemd ExecStart
│    verify-node-paths    │ │    calls verify-node-  │ │  ├─ prometheus targets
│    post-deploy)         │ │    paths)              │ │  └─ sudoers paths
│  deploy-modules.sh      │ │                        │ │
│   (REFERENCE: typed     │ │                        │ │
│    contract via         │ │                        │ │
│    modules-healthcheck) │ │                        │ │
└─────────────────────────┘ └─────────────────────────┘ └──────────────────────────┘
          │                        │ (typed contract)
          ▼                        ▼
┌── core/modules/ ─────────────────────────────────────────────────────┐
│  module.yaml (all 13 modules)                                        │
│   + interfaces: [healthcheck, install, deploy-hook, remove-hook]     │
│   (NEW field, D4 extension)                                          │
│                                                                      │
│  postgres/          ← NEW: exporter scrape in prometheus.yml        │
│  hermes-agent/      ← NEW: /metrics endpoint + scrape job           │
│  infra-metrics/     ← NEW: postgres-exporter container              │
│  monitoring/        ← EXTEND: prometheus.yml.tmpl scrape jobs       │
│  backup-cron/       ← FIX: crontab paths /opt/core/ → correct       │
│  platform-secrets/  ← REF: systemd path (already correct)           │
│  8 other modules    ← ADD: interfaces field (empty if not called)   │
└──────────────────────────────────────────────────────────────────────┘

┌── tests/ ────────────────────────────────────────────────────────────┐
│  test_cross_layer_imports.py  (HARDEN: variable tracking, W2+W3)     │
│  gates/test_gate_observability_coverage.py  (NEW, W1)                │
│  gates/test_gate_path_consistency.py        (NEW, W3)                │
│  gates/test_gate_doc_consistency.py         (NEW, W3)                │
│  gates/test_gate_cross_layer.py             (EXTEND: typed contract) │
└──────────────────────────────────────────────────────────────────────┘

┌── core/lib/ ─────────────────────────────────────────────────────────┐
│  paths.sh  (SoT: PLATFORM_ROOT=/opt/platform — UNCHANGED)            │
└──────────────────────────────────────────────────────────────────────┘
```

---

## §3. Step-by-Step Data Flow (per wave)

### Wave 1: Observability Coverage

```
1. Coder reads postgres/module.yaml → confirms severity: critical
2. Coder adds postgres-exporter container to infra-metrics/docker-compose.base.yml
   - image: quay.io/prometheuscommunity/postgres-exporter
   - connects: shared-db-net (read postgres) + observability-net (scraped)
   - DATA_SOURCE_NAME=postgresql://pgbouncer:5432/postgres?sslmode=disable
3. Coder adds scrape jobs to prometheus.yml.tmpl:
   - job: postgres-exporter → target: postgres-exporter:9187
   - job: hermes-agent → target: hermes-agent:9119 (if metrics exist)
4. Coder creates tests/gates/test_gate_observability_coverage.py:
   - Parse all module.yaml → collect severity≥high modules
   - Parse prometheus.yml.tmpl → collect all static_config targets
   - For each severity≥high module: assert ∃ scrape job or ∅ interfaces
   - Reverse: for each scrape job: assert target exists in a compose file
5. Coder registers gate in entrypoint-manifest.yaml
6. QA runs make gate MODE=fast → confirms gate passes on current stack
```

### Wave 2: Model Surgery

```
1. Coder reads core/modules/AGENTS.md → adds §Module Interfaces section
2. Coder defines interfaces vocabulary: [healthcheck, install, deploy-hook, remove-hook]
3. For each of 13 module.yaml files:
   a. Determine which internal/ scripts call this module
   b. Add interfaces: [...] field reflecting actual call patterns
   c. Modules NOT called from internal/: interfaces: []
4. Modules called from internal/:
   - postgres: interfaces: [healthcheck]
   - redis: interfaces: [healthcheck]
   - nginx: interfaces: [healthcheck]
   - monitoring: interfaces: [healthcheck, deploy-hook, remove-hook]
   - backup-cron: interfaces: [healthcheck]
   - platform-secrets: interfaces: [healthcheck]
   (litellm, langfuse, minio, clickhouse, logging, infra-metrics, hermes-agent — verify if called)
5. Coder updates core/AGENTS.md cross-layer table:
   - internal/ → modules/ разрешено ТОЛЬКО через interfaces: [...]
   - Adds: «Вызов без регистрации в interfaces = violation, gate #8 красный»
6. Coder fixes core/entrypoints/healthcheck.sh:12-13:
   - Replace "internal/ → modules is permitted" with reference to typed contract
7. Coder extends tests/test_cross_layer_imports.py:
   - Add variable tracking: scan `local var="...modules/..."` assignments
   - Before scanning bash calls, build variable→path map
   - When encountering `bash "$var"`, resolve var through map
8. Coder extends test_gate_cross_layer.py:
   - After resolving cross-layer call, check module.yaml interfaces field
   - If internal→modules call but interface not registered → violation
```

### Wave 3: Gate Hardening

```
1. Coder extends tests/test_cross_layer_imports.py _looks_like_path():
   - Add check: if argument is a variable ($var or ${var}), check if var appears
     in assignment map from W2
   - If var was assigned from path-literal → classify as path-bearing
2. Coder creates tests/gates/test_gate_path_consistency.py:
   - Scan all crontab, *.service, *.timer, *.path files in core/modules/
   - Extract all absolute paths (starting with /)
   - Whitelist: /usr/, /bin/, /etc/, /var/, /tmp/, /run/, /opt/platform/
   - Flag: /opt/core/ → violation
   - Flag: any /opt/ path not matching PLATFORM_ROOT pattern → violation
3. Coder creates tests/gates/test_gate_doc_consistency.py:
   - Parse entrypoint-manifest.yaml allowed_verbs
   - Parse root AGENTS.md glossary table (✅-rows)
   - Assert: ∀ allowed_verb ∈ AGENTS.md glossary (no missing entries)
   - Parse pyproject.toml markers
   - Assert: ∀ marker used in at least one test OR in known-unused list
   - Assert: static ↔ static_audit consistency
4. Coder registers both gates in entrypoint-manifest.yaml
5. QA confirms: W3 gates are RED before W4 fixes (path-consistency must flag /opt/core/); doc-consistency flags verify/static
```

### Wave 4: Path Remediation

```
1. Coder fixes core/modules/backup-cron/scripts/crontab:44,48:
   - /opt/core/internal/healthcheck/docker-healthcheck.sh → correct path
   - /opt/core/modules/backup-cron/scripts/disk-monitor.sh → correct path
   - NOTE: backup-cron container has NO mount of /opt/ — these paths are in-container.
           The docker-healthcheck.sh and disk-monitor.sh are COPY'd into the image
           (verify Dockerfile). If they're at /usr/local/bin/, use that path.
2. Coder fixes core/templates/sudo-whitelist.template:
   - /opt/core/modules/{{MODULE_NAME}}/Makefile → /opt/platform/core/modules/...
   - Or parameterize: {{PLATFORM_ROOT}}/core/modules/...
3. Coder fixes core/bootstrap/systemd/README.md:189,192:
   - /opt/core/internal/healthcheck/ → /opt/platform/core/internal/healthcheck/
4. Coder updates .kilo/server-state-vps.json and .kilo/agents/sysadmin.md path references
5. Verification: rg '/opt/core/' core/ → only historical comments remain
6. QA confirms: W3 path-consistency gate turns GREEN after W4 fixes
```

### Wave 5: Runtime Sentinel

```
1. Coder creates core/internal/verify/verify-node-paths.sh:
   a. Cron verification:
      - For each /etc/cron.d/* file: parse script paths
      - Assert each referenced file exists (test -f)
      - Skip /usr/*, /bin/*, /sbin/* (system paths assumed present)
   b. Systemd verification:
      - For each modules/*/*.service: parse ExecStart=/ExecStartPre=
      - Assert each absolute path exists
   c. Prometheus verification:
      - Parse /etc/prometheus/prometheus.yml (runtime-generated)
      - For each static_config target: attempt HTTP GET /metrics
      - Log failures (prometheus may not be running locally)
   d. Sudoers verification:
      - Parse /etc/sudoers.d/platform-*: extract Makefile paths
      - Assert each referenced Makefile exists
2. Coder registers verify-node-paths.sh in entrypoint-manifest.yaml as script:
3. Coder extends node-lifecycle.sh update-mode:
   - After deploy-system step: call verify-node-paths.sh
   - On failure: log warning, do NOT abort (non-fatal sentinel)
4. Coder extends modules-healthcheck.sh:
   - In --deep mode: call verify-node-paths.sh after module checks
5. Coder extends audit.sh:
   - Add verify-node-paths.sh to audit pipeline
```

---

## §4. $TASKS

### Wave 1: Observability Coverage (W1)

| Task ID | Description | Role | Priority | Dependencies | Complexity | Files |
|---------|-------------|------|----------|-------------|------------|-------|
| **TASK-W1-1** | Add postgres-exporter container to `infra-metrics/docker-compose.base.yml` | Coder | HIGH | — | 4 | `core/modules/infra-metrics/docker-compose.base.yml` |
| **TASK-W1-2** | Add postgres + hermes-agent scrape jobs to `prometheus.yml.tmpl` | Coder | HIGH | TASK-W1-1 | 3 | `core/modules/monitoring/config/prometheus.yml.tmpl` |
| **TASK-W1-3** | Create gate `test_gate_observability_coverage.py` | Coder | HIGH | TASK-W1-2 | 6 | `tests/gates/test_gate_observability_coverage.py` (new) |
| **TASK-W1-4** | Register new gate in `entrypoint-manifest.yaml` | Coder | HIGH | TASK-W1-3 | 1 | `core/entrypoint-manifest.yaml` |
| **TASK-W1-5** | Verify W1: run `make gate MODE=fast`, confirm postgres/hermes covered | QA | HIGH | TASK-W1-3, TASK-W1-4 | 2 | — |

### Wave 2: Model Surgery (W2)

| Task ID | Description | Role | Priority | Dependencies | Complexity | Files |
|---------|-------------|------|----------|-------------|------------|-------|
| **TASK-W2-1** | Add `## Module Interfaces (typed contract)` section to `core/modules/AGENTS.md` | Coder | HIGH | — | 3 | `core/modules/AGENTS.md` |
| **TASK-W2-2** | Add `interfaces:` field to all 13 `module.yaml` files | Coder | HIGH | TASK-W2-1 | 5 | 13 × `core/modules/*/module.yaml` |
| **TASK-W2-3** | Fix contradiction in `core/entrypoints/healthcheck.sh:12-13` | Coder | HIGH | TASK-W2-1 | 2 | `core/entrypoints/healthcheck.sh` |
| **TASK-W2-4** | Update `core/AGENTS.md` cross-layer table with typed contract rule | Coder | HIGH | TASK-W2-1 | 2 | `core/AGENTS.md` |
| **TASK-W2-5** | Extend `tests/test_cross_layer_imports.py`: add variable assignment tracking | Coder | HIGH | TASK-W2-2 | 7 | `tests/test_cross_layer_imports.py` |
| **TASK-W2-6** | Extend `tests/gates/test_gate_cross_layer.py`: enforce typed contract | Coder | HIGH | TASK-W2-2, TASK-W2-5 | 5 | `tests/gates/test_gate_cross_layer.py` |
| **TASK-W2-7** | Verify W2: run `make gate MODE=fast`, confirm gate flags unregistered interfaces | QA | HIGH | TASK-W2-5, TASK-W2-6 | 2 | — |

### Wave 3: Gate Hardening (W3)

| Task ID | Description | Role | Priority | Dependencies | Complexity | Files |
|---------|-------------|------|----------|-------------|------------|-------|
| **TASK-W3-1** | Extend `_looks_like_path()` with variable-resolution from W2 assignment map | Coder | MEDIUM | TASK-W2-5 | 5 | `tests/test_cross_layer_imports.py` |
| **TASK-W3-2** | Create gate `test_gate_path_consistency.py` | Coder | MEDIUM | W2 complete | 6 | `tests/gates/test_gate_path_consistency.py` (new) |
| **TASK-W3-3** | Create gate `test_gate_doc_consistency.py` | Coder | MEDIUM | — | 5 | `tests/gates/test_gate_doc_consistency.py` (new) |
| **TASK-W3-4** | Register W3 gates in `entrypoint-manifest.yaml` | Coder | MEDIUM | TASK-W3-2, TASK-W3-3 | 1 | `core/entrypoint-manifest.yaml` |
| **TASK-W3-5** | Verify W3: confirm path-consistency gate is RED (flags /opt/core/), doc gate flags verify/static drift | QA | MEDIUM | TASK-W3-2, TASK-W3-3, TASK-W3-4 | 2 | — |

### Wave 4: Path Remediation (W4)

| Task ID | Description | Role | Priority | Dependencies | Complexity | Files |
|---------|-------------|------|----------|-------------|------------|-------|
| **TASK-W4-1** | Fix `crontab:44,48` paths (backup-cron container) | Coder | MEDIUM | TASK-W3-2 (gate RED before fix) | 3 | `core/modules/backup-cron/scripts/crontab` |
| **TASK-W4-2** | Fix `sudo-whitelist.template` paths (parameterize or replace) | Coder | MEDIUM | TASK-W3-2 | 2 | `core/templates/sudo-whitelist.template` |
| **TASK-W4-3** | Fix `systemd/README.md:189,192` paths | Coder | LOW | — | 1 | `core/bootstrap/systemd/README.md` |
| **TASK-W4-4** | Fix `.kilo/server-state-vps.json` and `.kilo/agents/sysadmin.md` paths | Coder | LOW | — | 2 | `.kilo/server-state-vps.json`, `.kilo/agents/sysadmin.md` |
| **TASK-W4-5** | Verify W4: `rg '/opt/core/' core/` → 0 prod paths; path-consistency gate GREEN | QA | MEDIUM | TASK-W4-1..4, TASK-W3-2 | 1 | — |

### Wave 5: Runtime Sentinel (W5)

| Task ID | Description | Role | Priority | Dependencies | Complexity | Files |
|---------|-------------|------|----------|-------------|------------|-------|
| **TASK-W5-1** | Create `core/internal/verify/verify-node-paths.sh` | Coder | LOW | W4 complete | 8 | `core/internal/verify/verify-node-paths.sh` (new) |
| **TASK-W5-2** | Register `verify-node-paths.sh` in `entrypoint-manifest.yaml` as `script:` | Coder | LOW | TASK-W5-1 | 1 | `core/entrypoint-manifest.yaml` |
| **TASK-W5-3** | Integrate call in `node-lifecycle.sh` update-mode (after deploy-system) | Coder | LOW | TASK-W5-1 | 2 | `core/internal/bootstrap/node-lifecycle.sh` |
| **TASK-W5-4** | Integrate call in `modules-healthcheck.sh` --deep mode | Coder | LOW | TASK-W5-1 | 2 | `core/internal/healthcheck/modules-healthcheck.sh` |
| **TASK-W5-5** | Integrate call in `audit.sh` pipeline | Coder | LOW | TASK-W5-1 | 2 | `core/internal/audit/audit.sh`, `core/entrypoints/audit.sh` |
| **TASK-W5-6** | Verify W5: local test of `verify-node-paths.sh`, confirm integration points | QA | LOW | TASK-W5-1..5 | 2 | — |

**Total tasks: 28** | **Critical path: W1 (TASK-W1-1→5) + W2 (TASK-W2-1→7) → W3 (TASK-W3-1→5) → W4 (TASK-W4-1→5) → W5 (TASK-W5-1→6)**

---

## §5. $PARALLEL_GROUPS

### Wave Dependency Graph

```
W1 (indep) ─────────────────────────────┐
                                         ├──▶ W3 ──▶ W4 ──▶ W5
W2 (indep) ─────────────────────────────┘
```

### Wave 1 + Wave 2 — Parallel Launch

W1 and W2 share NO files. They can be implemented in parallel by two Coder agents.

```
### Wave 1-2 (parallel, independent, no shared files)
- Wave 1: TASK-W1-1, TASK-W1-2, TASK-W1-3, TASK-W1-4
- Wave 2: TASK-W2-1, TASK-W2-2, TASK-W2-3, TASK-W2-4, TASK-W2-5, TASK-W2-6
- Command:
  Agent 1: `coder Read 04-DevPlan.md, implement Wave 1: TASK-W1-1 through TASK-W1-4`
  Agent 2: `coder Read 04-DevPlan.md, implement Wave 2: TASK-W2-1 through TASK-W2-6`
```

### Wave 3 — Sequential (depends on W2)

```
### Wave 3 (depends on W2 complete)
- Tasks: TASK-W3-1, TASK-W3-2, TASK-W3-3, TASK-W3-4
- W3-1 extends W2-5 file (test_cross_layer_imports.py) — MUST run after W2
- W3-2 and W3-3 are independent new files, but both need W2 complete for context
- Command: `coder Read 04-DevPlan.md, implement Wave 3: TASK-W3-1 through TASK-W3-4`
```

### Wave 4 — Sequential (depends on W3 for gate)

```
### Wave 4 (depends on W3 complete)
- Tasks: TASK-W4-1, TASK-W4-2, TASK-W4-3, TASK-W4-4
- Gate from W3 must be RED before W4 fixes (verification gate)
- All W4 tasks modify different files → can be done in single Coder session
- Command: `coder Read 04-DevPlan.md, implement Wave 4: TASK-W4-1 through TASK-W4-4`
```

### Wave 5 — Sequential (depends on W4)

```
### Wave 5 (depends on W4 complete)
- Tasks: TASK-W5-1, TASK-W5-2, TASK-W5-3, TASK-W5-4, TASK-W5-5
- All depend on corrected paths from W4
- TASK-W5-2 through TASK-W5-5 depend on TASK-W5-1 (script must exist first)
- Command: `coder Read 04-DevPlan.md, implement Wave 5: TASK-W5-1 through TASK-W5-5`
```

### QA Verification Waves

```
### QA-W1 (after Wave 1)
- Task: TASK-W1-5 — verify observability gate passes on current stack
- Command: `qa Read 04-DevPlan.md, verify Wave 1 per TASK-W1-5 acceptance criteria`

### QA-W2 (after Wave 2)
- Task: TASK-W2-7 — verify typed contract gate detects unregistered interfaces
- Command: `qa Read 04-DevPlan.md, verify Wave 2 per TASK-W2-7 acceptance criteria`

### QA-W3 (after Wave 3)
- Task: TASK-W3-5 — verify path-consistency gate is RED, doc gate flags drift
- Command: `qa Read 04-DevPlan.md, verify Wave 3 per TASK-W3-5 acceptance criteria`

### QA-W4 (after Wave 4)
- Task: TASK-W4-5 — verify 0 /opt/core/ in prod paths, path-consistency gate GREEN
- Command: `qa Read 04-DevPlan.md, verify Wave 4 per TASK-W4-5 acceptance criteria`

### QA-W5 (after Wave 5)
- Task: TASK-W5-6 — verify-node-paths.sh works locally, integration points correct
- Command: `qa Read 04-DevPlan.md, verify Wave 5 per TASK-W5-6 acceptance criteria`

### QA-FINAL (after all waves)
- Run `make gate MODE=fast` — must be GREEN
- Run `make test MARKER=gate` — must be GREEN
- Command: `qa Read 04-DevPlan.md, run final gate verification`
```

---

## §6. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/gates/test_gate_observability_coverage.py` | `test_severity_high_modules_have_scrape_job` | Парсит все module.yaml → для severity≥high проверяет наличие scrape job в prometheus.yml.tmpl | `core/modules/monitoring/config/prometheus.yml.tmpl`, все `module.yaml` |
| `tests/gates/test_gate_observability_coverage.py` | `test_scrape_targets_exist_in_compose` | Парсит prometheus.yml.tmpl → для каждого static_config target проверяет существование сервиса в compose-файлах | `prometheus.yml.tmpl`, все `docker-compose.base.yml` |
| `tests/gates/test_gate_observability_coverage.py` | `test_postgres_has_exporter` | Проверяет что postgres-exporter контейнер существует в infra-metrics compose | `core/modules/infra-metrics/docker-compose.base.yml` |
| `tests/test_cross_layer_imports.py` | `test_variable_tracking_assignment_map` | Unit-тест: проверяет что `_looks_like_path` распознаёт `bash "$var"` когда var присвоена из path-литерала | `tests/test_cross_layer_imports.py::_looks_like_path` |
| `tests/test_cross_layer_imports.py` | `test_typed_contract_violation_flagged` | Unit-тест: internal→modules вызов без interfaces регистрации → violation | `tests/test_cross_layer_imports.py::check_violation` |
| `tests/gates/test_gate_cross_layer.py` | `test_cross_layer_typed_contract` | Интеграционный: запускает полный линтер, проверяет что нарушения через переменные найдены | `tests/test_cross_layer_imports.py::lint_core` |
| `tests/gates/test_gate_path_consistency.py` | `test_no_opt_core_hardcodes` | Сканирует crontab, *.service, *.timer → флагит `/opt/core/` как violation | Все `crontab`, `*.service`, `*.timer` в `core/modules/` |
| `tests/gates/test_gate_path_consistency.py` | `test_paths_match_platform_root` | Все prod-пути начинаются с `/opt/platform/` или разрешённых системных префиксов | Все файлы с абсолютными путями в `core/modules/` |
| `tests/gates/test_gate_path_consistency.py` | `test_cron_targets_exist_in_container` | Для crontab-путей внутри backup-cron: проверяет что пути соответствуют Dockerfile COPY | `core/modules/backup-cron/scripts/crontab`, `Dockerfile` |
| `tests/gates/test_gate_doc_consistency.py` | `test_all_allowed_verbs_in_glossary` | ∀ allowed_verb из manifest → ∃ в AGENTS.md ✅-таблице | `entrypoint-manifest.yaml`, `AGENTS.md` |
| `tests/gates/test_gate_doc_consistency.py` | `test_marker_static_consistency` | Проверяет что `static`/`static_audit` маркеры консистентны между pyproject.toml и AGENTS.md | `pyproject.toml`, `AGENTS.md` |
| `tests/gates/test_gate_doc_consistency.py` | `test_all_pytest_markers_used` | ∀ marker из pyproject.toml → используется хотя бы в одном тесте ИЛИ в known-unused | `pyproject.toml`, все `tests/**/test_*.py` |
| `tests/test_module_yaml_schema.py` (существующий, расширить) | `test_interfaces_field_schema` | Валидирует что `interfaces` поле содержит только разрешённые значения | `core/modules/*/module.yaml` |

---

## §7. File Manifest

### Files to CREATE

| # | File | Wave | Purpose |
|---|------|------|---------|
| 1 | `tests/gates/test_gate_observability_coverage.py` | W1 | Gate: severity≥high → scrape job coverage |
| 2 | `tests/gates/test_gate_path_consistency.py` | W3 | Gate: no /opt/core/ hardcodes, paths match PLATFORM_ROOT |
| 3 | `tests/gates/test_gate_doc_consistency.py` | W3 | Gate: verb glossary completeness, marker consistency |
| 4 | `core/internal/verify/verify-node-paths.sh` | W5 | Runtime sentinel: cron/systemd/prometheus/sudoers path verification |

### Files to MODIFY

| # | File | Wave | Change |
|---|------|------|--------|
| 5 | `core/modules/infra-metrics/docker-compose.base.yml` | W1 | Add postgres-exporter container + networks |
| 6 | `core/modules/monitoring/config/prometheus.yml.tmpl` | W1 | Add postgres-exporter + hermes-agent scrape jobs |
| 7 | `core/entrypoint-manifest.yaml` | W1, W3, W5 | Register 3 new gates + verify-node-paths script |
| 8 | `core/modules/AGENTS.md` | W2 | Add §Module Interfaces (typed contract) |
| 9 | `core/modules/postgres/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 10 | `core/modules/redis/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 11 | `core/modules/nginx/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 12 | `core/modules/monitoring/module.yaml` | W2 | Add `interfaces: [healthcheck, deploy-hook, remove-hook]` |
| 13 | `core/modules/backup-cron/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 14 | `core/modules/platform-secrets/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 15 | `core/modules/hermes-agent/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 16 | `core/modules/litellm/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 17 | `core/modules/langfuse/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 18 | `core/modules/minio/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 19 | `core/modules/clickhouse/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 20 | `core/modules/logging/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 21 | `core/modules/infra-metrics/module.yaml` | W2 | Add `interfaces: [healthcheck]` |
| 22 | `core/entrypoints/healthcheck.sh` | W2 | Fix lines 12-13: remove contradiction, reference typed contract |
| 23 | `core/AGENTS.md` | W2 | Update cross-layer table: internal→modules via interfaces |
| 24 | `tests/test_cross_layer_imports.py` | W2, W3 | Add variable assignment tracking + extend `_looks_like_path` |
| 25 | `tests/gates/test_gate_cross_layer.py` | W2 | Extend: enforce typed contract (interfaces field) |
| 26 | `core/modules/backup-cron/scripts/crontab` | W4 | Fix lines 44,48: /opt/core/ → correct paths |
| 27 | `core/templates/sudo-whitelist.template` | W4 | Fix lines 12,36-41: /opt/core/ → /opt/platform/core/ |
| 28 | `core/bootstrap/systemd/README.md` | W4 | Fix lines 189,192: /opt/core/ → /opt/platform/core/ |
| 29 | `.kilo/server-state-vps.json` | W4 | Fix workdir path if applicable |
| 30 | `.kilo/agents/sysadmin.md` | W4 | Fix line 469 example path |
| 31 | `core/internal/bootstrap/node-lifecycle.sh` | W5 | Add verify-node-paths.sh call after deploy-system |
| 32 | `core/internal/healthcheck/modules-healthcheck.sh` | W5 | Add verify-node-paths.sh call in --deep mode |
| 33 | `core/internal/audit/audit.sh` | W5 | Integrate verify-node-paths.sh |
| 34 | `core/entrypoints/audit.sh` | W5 | Integrate verify-node-paths.sh (if delegation needed) |

### Files to DELETE — NONE

---

## §8. Design Decisions

### D1: Typed Contract over Blanket Prohibition

**## @rationale**
**Q:** Почему не оставить правило «internal/ → modules/ запрещено» и починить гейт чтобы реально его проверял?
**A:** Потому что правило **уже не работает** в runtime — 6 вызовов из internal в modules существуют и необходимы (healthcheck, install, deploy hooks). Запрет фиктивен. Два варианта:
- **(A) Ужесточить запрет:** переписать internal скрипты чтобы они НЕ вызывали modules. Это потребует перемещения healthcheck-оркестрации в entrypoints (нарушая current architecture) или дублирования логики.
- **(B) Typed contract (выбран):** заменить фиктивный запрет на enforceable contract — internal МОЖЕТ вызывать modules, но только через зарегистрированные interfaces. Это честно отражает реальность и делает границу проверяемой.

**Rejected:** Option A — требует architectural refactoring за пределами scope этого плана (non-scope Brief).

### D2: Variable Tracking — MVP, not Full Data-Flow

**## @rationale**
**Q:** Почему только `local var=...` присвоения, а не полный data-flow анализ?
**A:** Полный статический анализ bash (flow-sensitive, path-sensitive) — это research-grade задача. MVP покрывает 95% случаев: все 6 текущих нарушений используют `local hc_script="${CORE_DIR}/modules/..."`. Полный анализ требует построения CFG (Control Flow Graph), что неоправданно для gate-теста. Non-scope Brief явно указывает: «MVP (только `local var=...` присвоения), не полный data-flow analysis».

**📝 TRAP[DEBT] · 2026-07-18 · MED · Variable tracking beyond `local var=...` is not implemented**
- Observed: bash variable tracking covers only `local var="value"` assignments — misses `var=$(...)`, `export var=...`, multi-line assignments, indirect references `${!var}`
- Suspected: edge cases exist but probability <5% for current codebase patterns
- Impact: future complex bash patterns may evade gate #8 detection
- When: during W2/W3 implementation — deferred by design per Brief non-scope

### D3: Gate Must Be RED Before Fix (W3→W4 handshake)

**## @rationale**
**Q:** Почему path-consistency gate (W3) должен быть красным перед path fixes (W4)?
**A:** TDD-approach: gate validates that the fix actually works. If we fix paths AND add gate in same wave, we can't distinguish «gate doesn't work» from «paths are already correct». W3 gate красный на `/opt/core/` → W4 fixes paths → gate зеленый = доказательство что gate работает.

### D4: postgres-exporter via pgbouncer, not direct postgres

**## @rationale**
**Q:** Почему postgres-exporter подключается к pgbouncer, а не напрямую к postgres?
**A:** postgres-exporter использует `pg_stat_database`, `pg_locks`, `pg_stat_replication` которые доступны через pgbouncer (простые SELECT). Прямое подключение к postgres требует отдельного пользователя/пароля и создаёт дополнительную connection-нагрузку. Pgbouncer уже обслуживает все модули — exporter становится ещё одним клиентом. DATA_SOURCE_NAME: `postgresql://pgbouncer:5432/postgres?sslmode=disable`.

### D5: Hermes-agent metrics — graceful degradation

**## @rationale**
**Q:** Что если hermes-agent не имеет `/metrics` endpoint?
**A:** Prometheus scrape job для hermes-agent добавляется условно:
- Если hermes-agent предоставляет `/metrics` (проверить код/конфигурацию) → scrape job с таргетом `hermes-agent:9119`
- Если нет → scrape job НЕ добавляется; observability-coverage gate проверяет severity≥high, но hermes-agent не имеет `severity: critical` в module.yaml → gate пропускает
- Добавление `/metrics` endpoint в hermes-agent — отдельная задача, вне скоупа этого DevPlan

### D6: sudo-whitelist.template — замена хардкода, не параметризация

**## @rationale**
**Q:** Почему не сделать полную параметризацию от `{{PLATFORM_ROOT}}`?
**A:** Brief non-scope: «sudo-whitelist.template полная параметризация от PLATFORM_ROOT — только замена хардкода, не рефакторинг шаблонизатора». Текущий sed-based templating (`sed "s/{{MODULE_NAME}}/..."`) не поддерживает вложенные переменные. Добавление `{{PLATFORM_ROOT}}` требует изменения `generate_sudoers_for_module()` в `setup-node.sh` — это architectural change за пределами scope.

### D7: verify-node-paths.sh — non-fatal sentinel

**## @rationale**
**Q:** Почему sentinel не блокирует деплой при ошибке?
**A:** verify-node-paths.sh — это детектор, не enforcer. Он запускается **после** деплоя (post-deploy) и сигнализирует о проблемах, но не откатывает деплой. Причина: на момент запуска некоторые проверки могут давать ложные срабатывания (prometheus не запущен → scrape target HTTP check fails). Блокировка деплоя на основе sentinel создаст ложные блокировки. Интеграция: WARNING-уровень в логах + Telegram alert.

---

## §9. Configuration DRY

### Configuration consistency map

| Variable/Value | SoT | Consumers (must stay synced) | Drift risk |
|----------------|-----|------------------------------|------------|
| `PLATFORM_ROOT=/opt/platform` | `core/lib/paths.sh:33` | crontab, sudo-whitelist, systemd units, README, CI workflows, .kilo configs, deploy-modules.sh, node-lifecycle.sh | **HIGH** — W4 fixes |
| `postgres-exporter:9187` | `infra-metrics/docker-compose.base.yml` | `prometheus.yml.tmpl` scrape job | MEDIUM — W1 adds |
| `interfaces: [healthcheck, ...]` | `module.yaml` per module | `test_cross_layer_imports.py`, `test_gate_cross_layer.py` | LOW — enforced by gate |
| Gate IDs | `entrypoint-manifest.yaml` gates section | test files, pytest markers | LOW — enforced by manifest-integrity gate |

### Change Impact Cascade (W1 postgres-exporter)

```
postgres-exporter container added to infra-metrics/docker-compose.base.yml
  ├─ prometheus.yml.tmpl: add scrape job postgres-exporter:9187         [CASCADE +1]
  ├─ tests/gates/test_gate_observability_coverage.py: validate coverage [CASCADE +1]
  └─ entrypoint-manifest.yaml: register new gate                        [CASCADE +1]
  Total cascade: 3 files beyond the primary change
```

### Change Impact Cascade (W2 interfaces field)

```
interfaces field added to module.yaml D4 schema
  ├─ core/modules/AGENTS.md: document new field                         [CASCADE +1]
  ├─ core/AGENTS.md: update cross-layer table                           [CASCADE +1]
  ├─ core/modules/*/module.yaml (13 files): add field                   [CASCADE +13]
  ├─ tests/test_cross_layer_imports.py: enforce typed contract           [CASCADE +1]
  ├─ tests/gates/test_gate_cross_layer.py: gate-level enforcement        [CASCADE +1]
  └─ tests/test_module_yaml_schema.py: schema validation                 [CASCADE +1]
  Total cascade: 18 files
```

---

## §10. Contracts (Inter-Layer)

### Contract: Module Interface Registration

```yaml
# module.yaml D4 extension
interfaces:                          # NEW — typed contract for cross-layer calls
  - healthcheck                      # internal/healthcheck/modules-healthcheck.sh
  - install                          # internal/bootstrap/deploy-modules.sh
  - deploy-hook                      # internal/deploy/deploy-project.sh
  - remove-hook                      # internal/deploy/deploy-project.sh
```

**Vocabulary (closed):** `healthcheck`, `install`, `deploy-hook`, `remove-hook`

**Invariant:** `internal/ → modules/<name>/<script>` call is valid IFF `<name>/module.yaml` declares the corresponding interface.

**Enforcement:** Gate #8 (`test_cross_layer_imports.py`) resolves module name from path, reads module.yaml, checks interfaces field.

### Contract: verify-node-paths.sh Exit Codes

```
0 — all paths verified, no issues
1 — path mismatch(es) found (non-fatal, warning-level)
2 — script internal error (cannot read config, missing deps)
```

**Caller contract:** Callers MUST NOT abort on exit code 1. Callers MAY abort on exit code 2 (environmental error).

---

## §11. Edge Cases & TRAP Annotations

### TRAP Annotations to Add During Implementation

| Location | TRAP Type | Content |
|----------|-----------|---------|
| `tests/test_cross_layer_imports.py::_looks_like_path` | TRAP[DECISION] | Variable tracking is MVP — only `local var=...` assignments. Full data-flow analysis is deferred. See D2. |
| `tests/gates/test_gate_path_consistency.py` | TRAP[BUSINESS] | Path consistency gate prevents `/opt/core/` regression — business accent: reliability > convenience. Source: Brief root cause B. |
| `core/modules/infra-metrics/docker-compose.base.yml` (postgres-exporter section) | TRAP[DECISION] | Exporter connects via pgbouncer, not direct postgres. See D4. Rev: if pgbouncer connection pooling blocks stats queries → switch to direct. |

### Edge Cases per Wave

**W1:**
- postgres-exporter fails to connect to pgbouncer → container restarts (Docker restart policy), Prometheus sees `up=0`
- hermes-agent has no `/metrics` → scrape job not added; gate skips (no severity≥high on hermes-agent)
- Multiple postgres instances (future) → need per-instance exporter; current single-node assumes 1 postgres

**W2:**
- Module has `interfaces: [healthcheck]` but internal calls `modules/<name>/install.sh` → gate flags violation
- Module has empty `interfaces: []` but internal calls it → gate flags violation
- Variable assignment `local var=$(echo "${CORE_DIR}/modules/...")` → MVP tracking misses it (known limitation, TRAP[DEBT])
- Module module.yaml missing `interfaces` field → gate treats as `interfaces: []` (strict: fail)

**W3:**
- Path `/opt/platform/core/...` contains symlink → path-consistency gate resolves realpath before checking
- cron uses `/usr/local/bin/script` (COPY'd in Dockerfile) → whitelist allows `/usr/local/`
- Prometheus `.tmpl` contains `${VAR}` → path gate skips template variables

**W4:**
- crontab paths inside container → Dockerfile COPY determines actual path; verify Dockerfile before fixing
- `sudo-whitelist.template` — параметризация sed-based; new path must survive `sed "s/{{MODULE_NAME}}/..."`
- `.kilo/` files — may be regenerated; fix is best-effort, non-blocking

**W5:**
- verify-node-paths.sh runs on host without Docker → prometheus check skipped gracefully
- verify-node-paths.sh runs in container → host paths not accessible; skip cron/systemd/sudoers checks
- Prometheus not running → HTTP check skipped with warning, not error

---

## §12. Acceptance Criteria (Summary)

| # | Criterion | Measured by | Wave |
|---|-----------|-------------|------|
| AC1 | postgres-exporter стартует в observability-net + shared-db-net | `docker compose ps` after `make up MODULES=infra-metrics` | W1 |
| AC2 | Prometheus скрейпит `postgres-exporter:9187` | `curl prometheus:9090/api/v1/targets` shows UP | W1 |
| AC3 | Gate observability-coverage passes on current stack | `python -m pytest tests/gates/test_gate_observability_coverage.py -v` | W1 |
| AC4 | `core/AGENTS.md` и `healthcheck.sh` не противоречат | `grep "internal.*modules.*permitted" core/entrypoints/healthcheck.sh` → removed/reworded | W2 |
| AC5 | Все 13 module.yaml содержат поле `interfaces` | `python -c "import yaml; ..."` validates all | W2 |
| AC6 | Gate #8 флагит вызов internal→modules без interfaces | Unit test: mock module.yaml without interfaces → violation | W2 |
| AC7 | `_looks_like_path` распознаёт `bash "$hc_script"` при наличии присвоения | Unit test in test_cross_layer_imports.py | W3 |
| AC8 | Gate path-consistency красный на `/opt/core/` (до W4 fixes) | `python -m pytest tests/gates/test_gate_path_consistency.py` → FAIL before W4 | W3 |
| AC9 | Gate doc-consistency флагит `verify` missing, `static`/`static_audit` mismatch | `python -m pytest tests/gates/test_gate_doc_consistency.py` → FAIL (or PASS if pre-fixed) | W3 |
| AC10 | `rg '/opt/core/' core/` → 0 prod-путей (исключая исторические комментарии) | grep + human review | W4 |
| AC11 | Gate path-consistency зелёный после W4 | `python -m pytest tests/gates/test_gate_path_consistency.py` → PASS | W4 |
| AC12 | `verify-node-paths.sh` обнаруживает несуществующие cron-пути | Создать временный crontab с битым путём → скрипт возвращает 1 | W5 |
| AC13 | `make gate MODE=fast` зелёный (все гейты проходят) | CI run on final commit | FINAL |

---

## §13. Non-Scope (from Brief, preserved)

- `sudo-whitelist.template` полная параметризация от PLATFORM_ROOT — только замена хардкода
- `.kilo/` файлы — только path-фиксы, не аудит всей конфигурации агентов
- `core/bootstrap/systemd/README.md` — только path-фикс, не актуализация всей документации
- Рефакторинг 6 runtime call sites (замена на typed contract) — только контракт в module.yaml + gate, не переписывание существующих вызовов
- Gate #8 variable tracking — MVP (только `local var=...` присвоения), не полный data-flow анализ
- hermes-agent `/metrics` endpoint — отдельная задача; только scrape job если endpoint уже есть
- TRAP[DEBT] cleanup в Makefile — отдельная задача чистки TRAP

---

## Next Steps

### Wave 1 + Wave 2 (parallel launch)
```
Agent 1: coder Read /Users/tronyx/projects/ai-platform/.ai/plans/001-arch-forensics/04-DevPlan.md, implement Wave 1: TASK-W1-1, TASK-W1-2, TASK-W1-3, TASK-W1-4
```
```
Agent 2: coder Read /Users/tronyx/projects/ai-platform/.ai/plans/001-arch-forensics/04-DevPlan.md, implement Wave 2: TASK-W2-1, TASK-W2-2, TASK-W2-3, TASK-W2-4, TASK-W2-5, TASK-W2-6
```

### QA Wave 1 + Wave 2
```
Agent 3: qa Read /Users/tronyx/projects/ai-platform/.ai/plans/001-arch-forensics/04-DevPlan.md, verify Waves 1-2 per TASK-W1-5 and TASK-W2-7
```

### Wave 3 (after W2 verified)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/001-arch-forensics/04-DevPlan.md, implement Wave 3: TASK-W3-1, TASK-W3-2, TASK-W3-3, TASK-W3-4
```

### Wave 4 (after W3 verified, gate RED)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/001-arch-forensics/04-DevPlan.md, implement Wave 4: TASK-W4-1, TASK-W4-2, TASK-W4-3, TASK-W4-4
```

### Wave 5 (after W4 verified, gate GREEN)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/001-arch-forensics/04-DevPlan.md, implement Wave 5: TASK-W5-1, TASK-W5-2, TASK-W5-3, TASK-W5-4, TASK-W5-5
```

### Final QA
```
qa Read /Users/tronyx/projects/ai-platform/.ai/plans/001-arch-forensics/04-DevPlan.md, run make gate MODE=fast, verify all 13 acceptance criteria
```

$END_DEVPLAN
