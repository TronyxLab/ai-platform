#!/usr/bin/env bash
# GREP_SUMMARY: scripts-audit, shebang-registration, pre-commit, gate-exceptions, manifest
# STRUCTURE: ▶ find shebang .sh files under core/ → ◇ exception pattern match → ◇ grep manifest registration → ⊕ report unregistered → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Audit: every shebang file under core/ must be registered in
##           entrypoint-manifest.yaml (delegates_to or module_hooks) OR
##           match an exception pattern in this file. Exit 0 = all registered, 1 = violations.
## @scope    All .sh files with shebang under core/ (excluding __pycache__, .backup, node_modules)
## @io       Reads core/entrypoint-manifest.yaml → exit 0 (clean) | exit 1 + list of unregistered scripts
## @invariants
##   - Reads only first line of each .sh for shebang detection (#! prefix)
##   - Exception patterns use bash glob matching against relative path from project root
##   - Manifest check uses simple grep — false positives possible but acceptable
##     (path may appear in comments/descriptions)
## @rationale Prevents registration drift — gate tests catch missing registrations
##            post-factum on CI; this hook catches them pre-commit.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 026 W3)
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$CORE_DIR/.." && pwd)"
MANIFEST="$CORE_DIR/entrypoint-manifest.yaml"

# ── Exception patterns ──────────────────────────────────────────────────
# Scripts that legitimately don't need manifest registration.
# Patterns use bash glob matching against relative path from project root.
EXCEPTIONS=(
    "core/lib/*"                        # Libraries (sourced, not executed)
    "core/modules/*/healthcheck.sh"     # Module healthchecks
    "core/modules/*/hooks/*.sh"         # Module hooks
    "core/modules/*/install.sh"         # Module installers
    "core/modules/*/ready-check.sh"     # Module readiness checks
    "core/modules/*/scripts/*.sh"       # Module scripts
    "core/modules/*/config/*.sh"        # Module configs
    "core/modules/*/config/*/*.sh"      # Nested module configs
    "core/modules/*/watchdog/*.sh"      # Module watchdogs
    "core/internal/healthcheck/*.sh"    # Internal healthchecks
    "core/modules/hermes-agent/build/scripts/*"  # Hermes build
    "core/modules/hermes-agent/context/scripts/*" # Hermes context
    "core/internal/bootstrap/ssl-provision.sh"   # SSL provisioning (thin wrapper)
    "core/modules/nginx/nginx_reload_hook.sh"    # Nginx hook
    "core/internal/bootstrap/s3-ssl-cache.sh"    # SSL cache (DevPlan 024)
    "core/internal/deploy/reconcile-projects.sh"  # Reconciliation (DevPlan 025)
    "core/internal/hooks/*.sh"                    # Pre-commit hooks (DevPlan 028 W1-E7)
    "core/internal/scripts-audit.sh"              # Self
)

# ── Collect shebang files ───────────────────────────────────────────────
UNREGISTERED=()
while IFS= read -r -d '' file; do
    rel="${file#$PROJECT_ROOT/}"

    # Check: has shebang?
    if ! head -1 "$file" 2>/dev/null | grep -q '^#!/'; then
        continue  # Not a shebang file
    fi

    # Check exceptions (suffix match for directory patterns)
    is_exception=false
    for pattern in "${EXCEPTIONS[@]}"; do
        if [[ "$rel" == $pattern ]]; then
            is_exception=true
            break
        fi
    done
    $is_exception && continue

    # Check manifest registration
    if grep -qF "$rel" "$MANIFEST" 2>/dev/null; then
        continue
    fi

    UNREGISTERED+=("$rel")
done < <(find "$CORE_DIR" -name "*.sh" \
    -not -path "*/.backup/*" \
    -not -path "*/__pycache__/*" \
    -not -path "*/node_modules/*" \
    -print0 2>/dev/null)

# ── Report ──────────────────────────────────────────────────────────────
if [[ ${#UNREGISTERED[@]} -gt 0 ]]; then
    echo "[IMP:10][scripts-audit] UNREGISTERED SCRIPTS FOUND:"
    for f in "${UNREGISTERED[@]}"; do
        echo "  - $f"
    done
    echo ""
    echo "Action required:"
    echo "  1. Register in core/entrypoint-manifest.yaml (delegates_to or module_hooks)"
    echo "  2. OR add to EXCEPTIONS array in core/internal/scripts-audit.sh"
    echo "  3. Retry commit"
    exit 1
fi

echo "[IMP:9][scripts-audit] All shebang scripts registered or in exceptions"
exit 0
