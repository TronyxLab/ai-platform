$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Verification of DevPlan 075 — platform-agent-watchdog.sh → Python Daemon
DESCRIPTION:           Plan self-consistency audit, implementation status check, cross-reference integrity, and prerequisite validation
RATIONALE:             Ensure DevPlan is actionable, complete, and free of drift before delegating to Coder for Wave 1 implementation
ACCEPTANCE_CRITERIA:   All referenced existing files exist; ACs are specific and measurable; no internal contradictions; prerequisites satisfied; plan ready for implementation
IMPLEMENTS:            DevPlan:.ai/plans/075-watchdog-python/
IMPACTS:               core/modules/hermes-agent/watchdog/ (4 files: 1 new, 2 modified, 1 unchanged)
REQUIRES:              None — can run parallel to 070–076
$END_ARTIFACT_CONTRACT

---

# Verification Report: DevPlan 075 — Watchdog → Python Daemon

**Date:** 2026-07-25
**SHA:** d37326afc64e505bb69f230465e83f9f5bef0d8a

---

## Final Verdict: **STABLE** — Plan ready for implementation, no blockers

Plan is self-consistent, all referenced existing files verified, acceptance criteria are specific and measurable. Implementation has NOT started — all tasks remain open. One minor finding (inline code in DevPlan) is informational only.

---

## 1. Plan Self-Consistency Audit

### 1.1 File inventory

| File | Status | Role |
|------|--------|------|
| `01-DevPlan.md` (58 LOC) | ✅ Exists | Brief DevPlan — overview, 6 ACs, 6 tasks, 5 test cases |
| `02-DevPlan.md` (1483 LOC) | ✅ Exists | Expanded DevPlan (authoritative per R1) — full code graph, 13 test cases, 6 tasks in 3 waves |

**Note:** The user referenced "DevPlan-expanded.md" which does not exist. The authoritative expanded plan is `02-DevPlan.md`.

### 1.2 Internal consistency (01 ↔ 02)

| Aspect | 01-DevPlan | 02-DevPlan | Consistent? |
|--------|-----------|-----------|-------------|
| Source file LOC | 549 | 549 | ✅ |
| Inline python3 count | 5 | 5 | ✅ |
| Shell launcher target | <30 LOC | 25 LOC exact | ✅ |
| New Python file | `agent_watchdog.py` | `agent_watchdog.py` (~500 LOC) | ✅ |
| Test file | `tests/unit/test_agent_watchdog.py` | `tests/unit/test_agent_watchdog.py` (~300 LOC) | ✅ |
| AC: zero inline python3 | ✅ | ✅ | ✅ |
| AC: circuit breaker 3-state | ✅ (3 states) | ✅ (CLOSED/OPEN/HALF_OPEN) | ✅ |
| AC: systemd update | ✅ (update ExecStart) | ✅ (ExecStart + WatchdogSec=0) | ✅ |
| AC: gate green | ✅ | ✅ | ✅ |
| Test count (T5) | 5 test cases | 13 test cases | ⚠️ 01 underspecified — 02 is authoritative |
| AC count | 7 | 8 (adds Telegram, Docker cleanup, expanded CB) | ⚠️ 02 adds 1 AC — intentional expansion |

**Conclusion:** No contradictions. 02-DevPlan expands on 01-DevPlan with greater detail. The 01→02 delta is intentional per §R1 (02 is authoritative).

### 1.3 Acceptance Criteria measurability

| # | AC (from 02-DevPlan) | Measurable? | Metric |
|---|----------------------|-------------|--------|
| 1 | `agent_watchdog.py` exists with all business logic | ✅ | File exists, `python3 -c "import agent_watchdog"` succeeds |
| 2 | `platform-agent-watchdog.sh` <30 LOC, zero inline python3 | ✅ | `wc -l < 30`, `grep "python3 -c"` returns nothing |
| 3 | Circuit breaker: 3-state FSM with JSON persistence | ✅ | 13 unit tests covering state transitions |
| 4 | Per-service health check with failure window tracking | ✅ | Configurable via env vars |
| 5 | Self-update: poll /ready → success/rollback flow | ✅ | 2 test cases (poll success, poll timeout) |
| 6 | Telegram notifications via direct HTTP | ✅ | 1 test case (no secrets file → graceful) |
| 7 | Docker image cleanup (keep KEEP_IMAGES) | ✅ | Part of T1 implementation |
| 8 | systemd unit updated (Type=oneshot) | ✅ | `grep "ExecStart" → agent_watchdog.py` |
| 9 | 10+ unit tests | ✅ | $TEST_SPEC lists 13 test functions |
| 10 | `make gate MODE=fast` green | ✅ | Binary pass/fail |

**Conclusion:** All 10 ACs are measurable with clear pass/fail criteria.

### 1.4 DevPlan structural compliance

| Check | 01-DevPlan | 02-DevPlan |
|-------|-----------|-----------|
| $ARTIFACT_CONTRACT (7 fields) | ✅ | ✅ |
| PURPOSE, DESCRIPTION, RATIONALE | ✅ | ✅ |
| ACCEPTANCE_CRITERIA (numbered) | ✅ (7 items) | ✅ (10 items) |
| IMPLEMENTS section | ✅ (Wave 6B) | ✅ (Wave 6B) |
| IMPACTS (file list) | ✅ (4 files) | ✅ (5 files incl. timer) |
| REQUIRES | ✅ (None) | ✅ (None) |
| Tasks with dependencies | ✅ (T1–T6, sequential) | ✅ (T1–T6, 3 waves) |
| $TASKS section | ❌ (missing) | ✅ |
| $PARALLEL_GROUPS | ❌ (missing) | ✅ |
| $TEST_SPEC table | ❌ (missing) | ✅ (13 rows) |

**Conclusion:** 02-DevPlan has complete artifact structure. 01-DevPlan is a valid brief but lacks expanded sections — this is acceptable as 02 is the authoritative artifact.

---

## 2. Implementation Status

### 2.1 File status matrix

| File | Expected State | Current State | Delta |
|------|---------------|---------------|-------|
| `agent_watchdog.py` | NEW (~500 LOC) | ❌ **DOES NOT EXIST** | T1 not executed |
| `platform-agent-watchdog.sh` | REDUCE (549→25 LOC) | 549 LOC, 5 inline `python3 -c` calls | T2 not executed |
| `platform-agent-watchdog.service` | UPDATE (ExecStart) | `ExecStart=/usr/local/bin/platform-agent-watchdog.sh` | T3 not executed |
| `platform-agent-watchdog.timer` | NO CHANGE | Unchanged | ✅ (correct) |
| `tests/unit/test_agent_watchdog.py` | NEW (~300 LOC) | ❌ **DOES NOT EXIST** | T5 not executed |

### 2.2 Evidence: current state

- **`.sh` line count:** 549 lines (matches DevPlan source analysis)
- **`.sh` inline python3:** 5 calls at lines 298, 304, 317-327, 332, 336 (all in `increment_failure_counter()`)
- **`.service` ExecStart:** `/usr/local/bin/platform-agent-watchdog.sh` (old path, not updated to Python daemon)
- **`.timer`:** Unchanged, correct
- **`tests/`:** No file matching `*watchdog*` or `*agent_watchdog*` exists

### 2.3 Test results

```
python -m pytest tests/ -s -v -k "watchdog or daemon or agent"
→ 20 selected, 18 passed, 1 skipped, 1 error

The 1 error is a pre-existing environment issue:
  tests/test_component_hermes.py::test_hermes_agent_starts → container unhealthy
  (NOT related to DevPlan 075 — langfuse container exits during test setup)

Zero watchdog-specific tests exist — test_agent_watchdog.py not yet created.
```

### 2.4 Git status

- Working tree has uncommitted changes in `.ai/plans/` (other DevPlans 045–070) but **none in `core/modules/hermes-agent/watchdog/`**.
- Last commit touching watchdog dir: `f2a7511 feat: bootstrap lifecycle...` — predates this DevPlan.
- No implementation work has been committed or staged for DevPlan 075.

---

## 3. Prerequisites Check

| Prerequisite | Status | Evidence |
|-------------|--------|----------|
| Source file `.sh` exists | ✅ | `platform-agent-watchdog.sh` — 549 LOC |
| Source file `.service` exists | ✅ | `platform-agent-watchdog.service` — 32 LOC |
| Source file `.timer` exists | ✅ | `platform-agent-watchdog.timer` — 31 LOC |
| Python 3.10+ available | ✅ | Python 3.14.6 on macOS (dev), Ubuntu 22.04 on VPS |
| Python stdlib sufficiency | ✅ | All imports (json, subprocess, signal, logging, dataclasses, time, os, pathlib, sys, argparse, urllib) are stdlib |
| Docker available (dev) | ✅ | Docker daemon running |
| No hard deps on 070–076 | ✅ | 02-DevPlan §REQUIRES: None |
| TRAP annotations preserved | ✅ | watchdog.sh:243 TRAP[DECISION] mapped to list[str] commands |
| systemd timer unchanged | ✅ | No changes needed per DevPlan |

**Conclusion:** All prerequisites satisfied. Implementation can begin immediately.

---

## 4. Cross-Reference Integrity

### 4.1 File references

| DevPlan reference | Exists? | Path |
|------------------|---------|------|
| `platform-agent-watchdog.sh` (source) | ✅ | `core/modules/hermes-agent/watchdog/platform-agent-watchdog.sh` |
| `platform-agent-watchdog.service` (source) | ✅ | `core/modules/hermes-agent/watchdog/platform-agent-watchdog.service` |
| `platform-agent-watchdog.timer` (source) | ✅ | `core/modules/hermes-agent/watchdog/platform-agent-watchdog.timer` |
| `agent_watchdog.py` (new) | ❌ | To be created by T1 |
| `tests/unit/test_agent_watchdog.py` (new) | ❌ | To be created by T5 |

### 4.2 External references

| Reference | Verified? | Note |
|-----------|-----------|------|
| Wave 6B — Strangler-Fig Tier 1 | ✅ | Referenced in root AGENTS.md "Strangler-триггер (двухуровневый)" |
| Shell injection TRAP (line 243) | ✅ | Present in current .sh, preserved in Python via list[str] commands |
| `core/AGENTS.md` cross-layer rules | ✅ | Python daemon imports only stdlib — no `core.*` imports (contract honored) |
| `platform-agent-watchdog.timer` unchanged | ✅ | Timer file not listed in IMPACTS for modification |

### 4.3 TRAP annotation inheritance

| Source TRAP | Location | Preservation in Python |
|-------------|----------|----------------------|
| `TRAP[DECISION]` — eval→array-based execution (shell injection prevention) | watchdog.sh:243 | `CircuitBreakerService.check_command` is `list[str]`, `subprocess.run` uses list form — no shell injection vector |

**Conclusion:** All cross-references verified. The migration preserves the security invariant (array-based command execution).

---

## 5. Findings

| # | Severity | Finding | Recommendation |
|---|----------|---------|----------------|
| 1 | INFO | 02-DevPlan.md contains ~1050 lines of inline Python code (complete class definitions). A DevPlan should describe WHAT to build; inline implementation code blurs the specification/implementation boundary. | Not blocking — the code serves as a detailed reference specification. Coder may deviate from exact code if tests pass and invariants hold. |
| 2 | INFO | 01-DevPlan.md lists 5 test cases (T5), while 02-DevPlan.md $TEST_SPEC lists 13 test cases. No contradiction — 02 is authoritative per §R1. | Ensure Coder reads 02-DevPlan.md (authoritative), not 01-DevPlan.md. |
| 3 | INFO | Shell launcher path mismatch: current `.service` uses `/usr/local/bin/platform-agent-watchdog.sh`; DevPlan 02 specifies `ExecStart=/usr/bin/python3 /opt/platform/core/modules/hermes-agent/watchdog/agent_watchdog.py` directly. The shell launcher at `/usr/local/bin/` and the source at `core/modules/.../watchdog/` need to be reconciled during deployment. | T2 (shell launcher) + T3 (service update) resolve this — ensure CI delivery copies the new `.sh` to `/usr/local/bin/`. |
| 4 | INFO | `module.yaml` for hermes-agent does not reference the watchdog — this is architecturally correct (watchdog is OS-level, external to agent, per invariant). | No action needed. |
| 5 | INFO | `test_hermes_agent_starts` fails in CI-like environment (container unhealthy — langfuse dependency). This is a pre-existing issue unrelated to DevPlan 075. | No action needed for this DevPlan. |

---

## 6. Task Readiness Assessment

| Wave | Tasks | Ready? | Blocker? |
|------|-------|--------|----------|
| Wave 1 | T1: Create agent_watchdog.py | ✅ Ready | None |
| Wave 2 | T2: Shell launcher, T3: systemd unit, T4: chmod, T5: Unit tests | ⏳ Depends on T1 | None beyond T1 |
| Wave 3 | T6: `make gate MODE=fast` | ⏳ Depends on T1–T5 | None |

**Recommendation:** Delegate Wave 1 to Coder immediately. Waves 2 and 3 follow after T1 verification.

---

## 7. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Python daemon has bug vs battle-tested shell | Low — Python is more testable; 13 unit tests planned | MEDIUM (watchdog failure = no auto-rollback) | Backward-compatible: shell launcher preserves old invocation path; rollback = `git revert` + restore old .sh from git history |
| systemd unit syntax error | Low — simple ExecStart change | LOW (timer continues, just fails one tick) | T3 trivially reviewable |
| Environment variable drift between shell and Python | Low — DRY config table in DevPlan verifies 1:1 mapping | MEDIUM (silent config divergence) | T5 tests verify config parsing; DRY table serves as contract |

---

## 8. Summary

| Dimension | Status |
|-----------|--------|
| Plan self-consistency | ✅ No contradictions; 02 authoritative |
| Acceptance criteria measurability | ✅ All 10 ACs measurable |
| Referenced file existence | ✅ All existing source files verified; new files correctly marked as NEW |
| Implementation status | 🔴 **NOT STARTED** (0/6 tasks complete) |
| Prerequisites | ✅ All satisfied |
| TRAP preservation | ✅ shell injection TRAP inherited correctly |
| Structural compliance | ✅ 02-DevPlan has all required sections |
| Test coverage | 🔴 0 watchdog tests (13 planned, none implemented) |
| Gate readiness | ⏳ Blocked by T1–T5 |

---

**Decision:** ✅ **STABLE** — Plan is complete, self-consistent, and ready for Wave 1 delegation to Coder.

$END_VERIFICATION_REPORT
