#!/usr/bin/env bash
# GREP_SUMMARY: postgres readiness ready-check migrations warm-up nginx-upstream
# STRUCTURE: pg_isready check → psql test_query → exit 0 | exit 1
# region MODULE_CONTRACT
## @purpose  READINESS check — verifies postgres is ready to serve queries (not just accepting TCP)
## @scope    Called by nginx upstream health probe; failure removes postgres from upstream pool
## @invariants
##   - Performs both pg_isready (TCP) AND a simple query (SELECT 1) to confirm query readiness
##   - Does NOT trigger Docker restart (readiness failure ≠ liveness failure)
##   - exit 0 = ready to accept traffic; exit 1 = not ready (nginx skips upstream)
##   - Distinguishes from liveness: handles migration/warmup period (06 §12)
## @rationale nginx must not route to postgres before it's query-ready (06 §12 readiness vs liveness)
# endregion MODULE_CONTRACT

set -euo pipefail

POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-postgres}"

# [IMP:7][postgres-ready-check][step1] Step 1: TCP liveness (fast fail)
if ! pg_isready -U "${POSTGRES_USER}" -h 127.0.0.1 -t 3 &>/dev/null; then
    echo "[IMP:9][postgres-ready-check] READINESS FAIL: pg_isready returned non-zero (TCP not ready)" >&2
    exit 1
fi

# [IMP:8][postgres-ready-check][step2] Step 2: Execute simple query to confirm query readiness
if psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -h 127.0.0.1 -c "SELECT 1;" &>/dev/null; then
    echo "[IMP:8][postgres-ready-check] READINESS PASS: postgres accepting queries"
    exit 0
else
    echo "[IMP:9][postgres-ready-check] READINESS FAIL: psql SELECT 1 failed — not query-ready" >&2
    exit 1
fi
