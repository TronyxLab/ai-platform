#!/usr/bin/env bash
# GREP_SUMMARY: hermes-agent s6 cont-init context-overlay context-validation L2
# STRUCTURE: ▶ guard $CONTEXT? → ◇ empty → exit 1 || log context → ○ apply overlays → ∑ done → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose     s6 cont-init.d script: validate CONTEXT env and apply context overlay for L2 image
## @scope       Runs once per container start after L1 platform init (02-platform-init). Guards on $CONTEXT
## @invariants
##   - CONTEXT env var is REQUIRED for L2 image — exits 1 if empty
##   - Logs context name at IMP:9 for business logic assertion
##   - Runs after 02-platform-init (L1) and 03-register-profiles (L2 profiles)
##   - Always exits 0 if CONTEXT is set (non-fatal even if overlay directories are empty)
##   - Context overlay dirs: /opt/hermes/context/config/, /opt/hermes/context/skills/
## @rationale  L2 (context) image must always have a CONTEXT label to differentiate deployments.
##             Without it, the container cannot determine which config/skills/profiles to apply.
##             The guard prevents silent misconfiguration (container runs with wrong context).
# endregion MODULE_CONTRACT

# GREP_SUMMARY: init-context.sh s6 cont-init.d context CONTEXT guard validate L2 overlay
# STRUCTURE: ▶ guard $CONTEXT? → ◇ empty → exit 1 || log context → ○ apply overlays → ∑ done → ⎋ exit 0

set -euo pipefail

log() { echo "[CONTEXT_INIT][$(date -u '+%Y-%m-%dT%H:%M:%SZ')][$1] $2"; }

log "INFO" "=== Context init started ==="

# ══════════════════════════════════════════════════════════════════════════
# 🧐 TRAP[DECISION] · 2026-07-10 · P1 · s6-overlay cont-init.d env var visibility
# ·   s6-overlay (upstream hermes-agent v2026.7.1) does NOT pass Docker env vars
# ·   to cont-init.d scripts even though they are stored in
# ·   Rev: If upstream fixes this in a later s6-overlay version, this fallback becomes a no-op
# ·   /run/s6/container_environment/. The env is only applied to the main
# ·   service (main-wrapper.sh via with-contenv).
# ·   Fix: fallback to reading CONTEXT from /run/s6/container_environment/CONTEXT
# ·   if $CONTEXT is empty.
# ·   Rejected: Changing shebang to #!/command/with-contenv bash — makes the script
# ·   dependent on s6-overlay internals and breaks direct execution during tests.
# ══════════════════════════════════════════════════════════════════════════
# ⚠️ TRAP[BUG] · 2026-07-10 · P1 · L2 container starts without CONTEXT in cont-init.d
# · Symptom: L2 container crashes because CONTEXT env var not visible in s6 cont-init.d
# · Root: s6-overlay stores env in /run/s6/container_environment/ but doesn't export
# ·       it to cont-init.d scripts
# · Fix: Read from s6 env file if env var is empty
# · Prevention: Use read_s6_env() fallback pattern
_read_s6_env() {
    local varname="$1"
    local envfile="/run/s6/container_environment/${varname}"
    if [[ -z "${!varname:-}" && -f "$envfile" ]]; then
        local val
        val=$(cat "$envfile" 2>/dev/null | tr -d '\n')
        export "$varname"="$val"
        log "INFO" "[IMP:8][CONTEXT_INIT][S6ENV] Read ${varname} from ${envfile}: ${val}"
    fi
}

_read_s6_env "CONTEXT"

# ══════════════════════════════════════════════════════════════════════════
# region FUNCTION_guard_context
## @purpose     Validate CONTEXT env var is set; exit 1 if empty
## @io          In: $CONTEXT env var; Out: exit 1 on empty, 0 otherwise
## @complexity  O(1) — single string check
## @rationale   L2 image MUST have CONTEXT set, otherwise it would run as base-only
##              (same as L1), defeating the purpose of the L2 layer separation.
##              This guard enforces the architecture invariant at container startup.
_guard_context() {
    if [[ -z "${CONTEXT:-}" ]]; then
        echo "[IMP:10][CONTEXT_INIT][FATAL] CONTEXT env is required for L2 image"
        echo "[IMP:10][CONTEXT_INIT][FATAL] Usage: docker run -e CONTEXT=mycontext hermes-agent-context"
        exit 1
    fi
    log "INFO" "[IMP:9][CONTEXT_INIT][GUARD] CONTEXT validated: ${CONTEXT}"
}
# endregion FUNCTION_guard_context

# ── Guard: require CONTEXT ──────────────────────────────────────────────
_guard_context

# ── Apply context config overlay (if present) ───────────────────────────
if [[ -d "/opt/hermes/context/config" ]]; then
    log "INFO" "[IMP:8][CONTEXT_INIT][CONFIG] Applying context config overlay from /opt/hermes/context/config/"
    rsync -a "/opt/hermes/context/config/" /opt/hermes/config/ 2>/dev/null || true
    log "INFO" "[IMP:9][CONTEXT_INIT][CONFIG] Context config overlay applied"
else
    log "INFO" "[IMP:7][CONTEXT_INIT][CONFIG] No context config overlay — skipping"
fi

# ── Apply context skills overlay (if present) ───────────────────────────
if [[ -d "/opt/hermes/context/skills" ]]; then
    log "INFO" "[IMP:8][CONTEXT_INIT][SKILLS] Applying context skills overlay from /opt/hermes/context/skills/"
    rsync -a "/opt/hermes/context/skills/" /opt/hermes/skills/ 2>/dev/null || true
    log "INFO" "[IMP:9][CONTEXT_INIT][SKILLS] Context skills overlay applied"
else
    log "INFO" "[IMP:7][CONTEXT_INIT][SKILLS] No context skills overlay — skipping"
fi

log "INFO" "[IMP:9][CONTEXT_INIT][COMPLETE] Context init finished for CONTEXT=${CONTEXT}"
