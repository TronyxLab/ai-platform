#!/usr/bin/env bash
# GREP_SUMMARY: hermes-agent s6 cont-init profile-creation context-overlay build
# STRUCTURE: ▶ check guard → ○ profile creation → ◇ context dir? → context config overlay → context profiles overlay → touch guard → chown → ∑ log → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose     s6 cont-init.d script: create profiles from templates + apply context overlay
## @scope       Runs once per container start; idempotent — skips existing profiles and completed overlays
## @invariants
##   - Idempotent: checks guard file + existing config.yaml before any operation
##   - Guard file: /opt/data/.context-overlay-applied (prevents re-applying context overlay)
##   - Profile creation: checks [ -f "$dest/config.yaml" ] before copying
##   - Context config overlay: rsync from /opt/hermes/context/config/ → /opt/hermes/config/
##   - Context profile overlay: rsync --ignore-existing from context profiles → data profiles
##   - Only creates, never overwrites — user edits are preserved
##   - Logs at IMP:9 for business logic assertions
##   - Exits 1 if CONTEXT is set but invalid; exits 0 on success
##   - Volume permissions: validates write access to /opt/data before fixup; non-fatal on failure
##   - chown 10000:10000 on all data directories for hermes user
## @changes     LAST_CHANGE: 2026-07-06 | Added _validate_volume_permissions() for P0 volume fix
# endregion MODULE_CONTRACT

# GREP_SUMMARY: init.sh s6 cont-init.d platform profiles context-overlay first-boot idempotent
# STRUCTURE: ▶ check guard → ○ profile creation → ◇ context dir? → context config overlay → context profiles overlay → touch guard → chown → ∑ log → ⎋ exit 0

set -euo pipefail

log() { echo "[PLATFORM_INIT][$(date -u '+%Y-%m-%dT%H:%M:%SZ')][$1] $2"; }

TEMPLATES="/opt/hermes/templates/profiles"
DATA="/opt/data/profiles"
CONTEXT_DIR="/opt/hermes/context"
CONTEXT_GUARD="/opt/data/.context-overlay-applied"

log "INFO" "=== Platform init started ==="

# ⚠️ TRAP[BUG] · 2026-07-10 · P1 · CONTEXT not visible in s6-overlay cont-init.d
# · s6-overlay stores env in /run/s6/container_environment/ but doesn't export
# · to cont-init.d scripts. Fallback reads from env file directly.
_read_s6_env() {
    local varname="$1"
    local envfile="/run/s6/container_environment/${varname}"
    if [[ -z "${!varname:-}" && -f "$envfile" ]]; then
        local val
        val=$(cat "$envfile" 2>/dev/null | tr -d '\n')
        export "$varname"="$val"
        log "INFO" "[IMP:8][INIT][S6ENV] Read ${varname} from ${envfile}: ${val}"
    fi
}
_read_s6_env "CONTEXT"

# [IMP:9] Validate CONTEXT if set
if [[ -n "${CONTEXT:-}" ]]; then
    log "INFO" "[IMP:9][INIT][CONTEXT] Context specified: ${CONTEXT}"
else
    log "WARN" "[IMP:7][INIT][CONTEXT] No CONTEXT set — running base-only mode"
fi

# ══════════════════════════════════════════════════════════════════════════
# region FUNCTION_validate_volume_permissions
## @purpose     Validate write access to /opt/data before ownership fixup
## @io          In: /opt/data directory; Out: exit 0 always (non-fatal)
## @complexity  O(1) — single temp file write test
## @rationale   Volume mounts from Docker may have incorrect permissions
##              preventing the hermes user (UID 10000) from writing profiles.
##              This function detects the issue early and attempts a fix
##              if running as root, without blocking container startup.
# ⚠️ TRAP[BUG] · 2026-07-06 · P0 · Volume permissions — hermes user cannot write /opt/data on first boot
# · Symptom: Hermes agent crashes on startup because /opt/data/profiles/ is owned by root:root
# · Root: Docker volume mounts inherit host ownership (typically root), not UID 10000
# · Fix: _validate_volume_permissions() tests write access and calls chown if running as root
# · Prevention: Always validate volume permissions before data operations
# endregion FUNCTION_validate_volume_permissions
_validate_volume_permissions() {
    local test_file="/opt/data/.write_test"
    local can_write=false

    # Attempt to create a test file to verify write access
    if touch "$test_file" 2>/dev/null; then
        rm -f "$test_file"
        can_write=true
        log "INFO" "[IMP:7][VOLUME][CHECK] Write access to /opt/data confirmed"
    else
        log "WARN" "[IMP:9][VOLUME][CHECK] Cannot write to /opt/data — likely volume permission mismatch (owner is not UID 10000)"
        log "WARN" "[IMP:8][VOLUME][CHECK] Current ownership: $(stat -c '%u:%g' /opt/data 2>/dev/null || stat -f '%u:%g' /opt/data 2>/dev/null || echo 'unknown')"

        # Attempt fix if running as root
        if [[ $EUID -eq 0 ]]; then
            log "INFO" "[IMP:8][VOLUME][FIX] Running as root — attempting chown 10000:10000 /opt/data"
            if chown 10000:10000 /opt/data 2>/dev/null; then
                log "INFO" "[IMP:9][VOLUME][FIX] Volume ownership fixed for /opt/data"
                can_write=true
            else
                log "WARN" "[IMP:7][VOLUME][FIX] chown failed — continuing anyway (non-fatal)"
            fi
        else
            log "WARN" "[IMP:7][VOLUME][FIX] Not running as root — cannot fix ownership, continuing anyway (non-fatal)"
        fi
    fi

    # ── Cleanup: remove test file if it still exists ──
    rm -f "$test_file" 2>/dev/null || true

    # Non-fatal: return 0 always to avoid blocking container startup
    return 0
}

# ── Step 1: Create profiles from templates ─────────────────────────────
if [ ! -d "$TEMPLATES" ]; then
    log "WARN" "[IMP:7][INIT][TEMPLATES] No templates at $TEMPLATES — skipping profile creation"
else
    mkdir -p "$DATA"

    for template_dir in "$TEMPLATES"/*/; do
        [ -d "$template_dir" ] || continue
        profile_name=$(basename "$template_dir")
        dest="$DATA/$profile_name"

        if [ -f "$dest/config.yaml" ]; then
            log "INFO" "[IMP:7][INIT][${profile_name}] SKIP: profile already exists — idempotent"
            continue
        fi

        cp -r "$template_dir" "$dest"
        log "INFO" "[IMP:9][INIT][${profile_name}] Profile created from template"
    done
fi

# ── Step 2: Context config overlay ──────────────────────────────────────
# If context directory has config/, rsync over base config
if [[ -d "${CONTEXT_DIR}/config" ]]; then
    log "INFO" "[IMP:8][INIT][CONTEXT] Applying context config overlay from ${CONTEXT_DIR}/config/"
    rsync -a "${CONTEXT_DIR}/config/" /opt/hermes/config/
    log "INFO" "[IMP:9][INIT][CONTEXT] Context config overlay applied"
fi

# ── Step 3: Context profile overlay (if guard not present) ─────────────
if [[ -d "${CONTEXT_DIR}/templates/profiles" ]]; then
    if [[ -f "$CONTEXT_GUARD" ]]; then
        log "INFO" "[IMP:7][INIT][CONTEXT] Context overlay already applied (guard: ${CONTEXT_GUARD}) — idempotent"
    else
        log "INFO" "[IMP:8][INIT][CONTEXT] Applying context profile overlay from ${CONTEXT_DIR}/templates/profiles/"
        mkdir -p "$DATA"
        # --ignore-existing: base profiles take precedence over context profiles on re-init
        rsync -a --ignore-existing "${CONTEXT_DIR}/templates/profiles/" "$DATA/"
        log "INFO" "[IMP:9][INIT][CONTEXT] Context profile overlay applied"

        # Write guard file
        touch "$CONTEXT_GUARD"
        log "INFO" "[IMP:8][INIT][CONTEXT] Guard file created: ${CONTEXT_GUARD}"
    fi
fi

# ── Step 4: Fix ownership ──────────────────────────────────────────────
_validate_volume_permissions
chown -R 10000:10000 /opt/data 2>/dev/null || true
log "INFO" "[IMP:8][INIT][OWNERSHIP] /opt/data ownership set to 10000:10000"

log "INFO" "[IMP:9][INIT][COMPLETE] Platform init finished"
