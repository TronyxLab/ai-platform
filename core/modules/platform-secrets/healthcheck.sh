#!/usr/bin/env bash
# GREP_SUMMARY: platform-secrets healthcheck systemd liveness secrets.env
# STRUCTURE: → systemctl is-active platform-secrets → [[ -s /run/platform/secrets.env ]] → exit 0 | exit 1
# region MODULE_CONTRACT
## @purpose  LIVENESS check for platform-secrets systemd oneshot service
## @scope    Verifies the service has executed and produced a non-empty secrets.env
## @invariants
##   - systemctl is-active confirms the oneshot completed successfully
##   - [[ -s /run/platform/secrets.env ]] confirms the output file exists and is non-empty
##   - exit 0 = service active + secrets file present
##   - exit 1 = service failed or secrets file missing
## @rationale platform-secrets is a systemd oneshot (not Docker) — checked via systemctl + file presence
# endregion MODULE_CONTRACT

set -euo pipefail

echo "[IMP:7][platform-secrets-hc][main] Starting platform-secrets healthcheck" >&2
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Deep check: verify secrets file is non-empty and contains expected keys
    if [[ -s /run/platform/secrets.env ]]; then
        log_imp 8 "deep" "Secrets file exists and is non-empty"
        if grep -q "POSTGRES_PASSWORD" /run/platform/secrets.env 2>/dev/null; then
            log_imp 8 "deep" "Secrets file contains expected keys"
            exit 0
        fi
        log_imp 9 "deep" "Secrets file missing expected keys"
        exit 1
    fi
    log_imp 9 "deep" "Secrets file is missing or empty"
    exit 1
fi

# Default liveness check
if systemctl is-active platform-secrets &>/dev/null; then
    log_imp 8 "liveness" "platform-secrets service is active"
    if [[ -s /run/platform/secrets.env ]]; then
        log_imp 8 "liveness" "Secrets file present"
        exit 0
    fi
    log_imp 9 "liveness" "Secrets file /run/platform/secrets.env is missing or empty"
    exit 1
fi

log_imp 9 "liveness" "platform-secrets service is not active"
exit 1
