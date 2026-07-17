#!/usr/bin/env bash
# GREP_SUMMARY: backup-cron readiness ready-check cron-running last-run-marker
# STRUCTURE: pgrep cron → check crontab installed → exit 0 | exit 1
# region MODULE_CONTRACT
## @purpose  READINESS check — cron daemon running AND crontab is installed and valid
## @scope    Called by monitoring probes; does NOT trigger Docker restart
## @invariants
##   - Checks pgrep cron (daemon alive) AND /etc/cron.d/platform-backup exists
##   - Does NOT check last backup success (that's in backup logs, not liveness)
##   - exit 0 = ready; exit 1 = not ready
## @rationale Readiness requires both daemon + schedule; liveness only needs daemon (06 §12)
# endregion MODULE_CONTRACT

set -euo pipefail

CRONTAB_FILE="/etc/cron.d/platform-backup"

# [IMP:7][backup-cron-ready][step1] Step 1: cron daemon liveness
if ! pgrep -x cron > /dev/null 2>&1; then
    echo "[IMP:9][backup-cron-ready] READINESS FAIL: cron daemon not running" >&2
    exit 1
fi

# [IMP:8][backup-cron-ready][step2] Step 2: crontab installed
if [[ ! -f "${CRONTAB_FILE}" ]]; then
    echo "[IMP:9][backup-cron-ready] READINESS FAIL: crontab not installed: ${CRONTAB_FILE}" >&2
    exit 1
fi

# [IMP:8][backup-cron-ready][step3] Step 3: crontab has expected schedule entries
SCHEDULE_COUNT=$(grep -cE "^[0-9]" "${CRONTAB_FILE}" 2>/dev/null || echo "0")
if [[ "${SCHEDULE_COUNT}" -lt 3 ]]; then
    echo "[IMP:9][backup-cron-ready] READINESS FAIL: expected ≥3 cron entries, found ${SCHEDULE_COUNT}" >&2
    exit 1
fi

echo "[IMP:8][backup-cron-ready] READINESS PASS: cron running, crontab has ${SCHEDULE_COUNT} entries"
exit 0
