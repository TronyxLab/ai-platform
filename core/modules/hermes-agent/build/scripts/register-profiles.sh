#!/usr/bin/env bash
# GREP_SUMMARY: hermes-agent s6 cont-init register-profiles profile-registration
# STRUCTURE: ▶ check guard → ○ for each profile → hermes profile create → touch guard → ∑ log → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose     s6 cont-init.d script: register all profiles with hermes CLI
## @scope       Runs after 02-platform-init; idempotent via guard file + hermes profile list check
## @invariants
##   - Guard file: /opt/data/.profiles-registered (prevents re-registration)
##   - Iterates all profiles in /opt/data/profiles/ (both base and context)
##   - Idempotent: checks guard file first, then checks hermes profile list
##   - Non-fatal on hermes CLI failure (logs warning, continues)
##   - Logs at IMP:9 for business logic assertions
## @changes     LAST_CHANGE: 2026-07-03 | Unified registration (base + context profiles)
# endregion MODULE_CONTRACT

# GREP_SUMMARY: register-profiles.sh s6 cont-init.d hermes profile create profiles registration
# STRUCTURE: ▶ check guard → ○ for each profile → hermes profile create → touch guard → ∑ log → ⎋ exit 0

set -euo pipefail

log() { echo "[PLATFORM_REGISTER][$(date -u '+%Y-%m-%dT%H:%M:%SZ')][$1] $2"; }

DATA="/opt/data/profiles"
GUARD_FILE="/opt/data/.profiles-registered"

log "INFO" "=== Profile registration started ==="

# ── Check guard file ────────────────────────────────────────────────────
if [[ -f "$GUARD_FILE" ]]; then
    log "INFO" "[IMP:7][REGISTER][GUARD] Profiles already registered (guard: ${GUARD_FILE}) — idempotent, skipping"
    exit 0
fi

# ── Verify data directory exists ────────────────────────────────────────
if [[ ! -d "$DATA" ]]; then
    log "WARN" "[IMP:7][REGISTER][DATA] No profiles directory at ${DATA} — nothing to register"
    exit 0
fi

# ── Register each profile ───────────────────────────────────────────────
REGISTERED_COUNT=0
for profile_dir in "$DATA"/*/; do
    [ -d "$profile_dir" ] || continue
    profile_name=$(basename "$profile_dir")

    # Check if profile already registered with hermes CLI
    if command -v hermes &>/dev/null; then
        if hermes profile list 2>/dev/null | grep -q "^${profile_name}\b"; then
            log "INFO" "[IMP:7][REGISTER][${profile_name}] Already registered with hermes — skipping"
            continue
        fi

        log "INFO" "[IMP:8][REGISTER][${profile_name}] Registering profile via hermes CLI"
        if hermes profile create --name "$profile_name" --from "$profile_dir" 2>/dev/null; then
            log "INFO" "[IMP:9][REGISTER][${profile_name}] Profile registered successfully"
            REGISTERED_COUNT=$((REGISTERED_COUNT + 1))
        else
            log "WARN" "[IMP:9][REGISTER][${profile_name}] hermes profile create failed — continuing"
        fi
    else
        log "WARN" "[IMP:7][REGISTER][CLI] hermes CLI not found — profiles cannot be registered via CLI"
        log "WARN" "[IMP:7][REGISTER][CLI] Profile data is available at ${profile_dir} for manual registration"
    fi
done

# ── Write guard file ────────────────────────────────────────────────────
touch "$GUARD_FILE"
log "INFO" "[IMP:9][REGISTER][COMPLETE] Profile registration finished: ${REGISTERED_COUNT} profiles registered"
