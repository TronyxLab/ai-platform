# Brief 075 — Watchdog Python Migration

## $ARTIFACT_CONTRACT
- **PURPOSE:** Migrate platform-agent-watchdog.sh (549 LOC, 5 inline python3 calls, circuit breaker state machine) to production-grade Python daemon agent_watchdog.py.
- **DESCRIPTION:** Proper signal handling, structured logging, typed state machine. Shell launcher <30 LOC. Circuit breaker for 5 stateful services, self-update readiness polling with auto-rollback, Telegram notifications, Docker image cleanup. systemd service updated to call Python daemon directly.
- **RATIONALE:** Shell circuit breaker state machine is fragile; Python provides proper signal handling and structured logging.
- **ACCEPTANCE_CRITERIA:** 10 ACs from DevPlan-expanded.md.
- **IMPLEMENTS:** DevPlan 075 (01-DevPlan.md + 02-DevPlan.md, authoritative: expanded 1483 LOC).
- **IMPACTS:** platform-agent-watchdog.sh, agent_watchdog.py (NEW), systemd service, 13 test files.
- **REQUIRES:** Nothing (independent).

## Current Status (Audit 2026-07-25)
- **Verdict:** STABLE — plan ready for implementation, no blockers.
- **Implementation:** 0% (не начата). agent_watchdog.py не существует.

## Key Findings (from VerificationReport.md)
- All 10 ACs measurable. All prerequisites satisfied. Cross-references verified.
- Shell injection TRAP preserved in Python via list[str] commands.
- Pre-existing: test_hermes_agent_starts fails (unrelated, langfuse dependency).
- 13 unit tests planned, zero implemented.

## Required Actions
1. Implement agent_watchdog.py per expanded DevPlan.
2. Create 13 unit tests.
3. Update systemd service to call Python daemon directly.
