# shellcheck shell=bash
# GREP_SUMMARY: vps-readiness pre-flight ssh docker readiness-check remediation
# STRUCTURE: ▶ check_vps_ready(node,[--json|--quick]) → ◆ SSH check (10s timeout) → ◆ forced-command ping → ◆ /opt/projects/ exists + writable → ◆ Docker daemon (skip if --quick) → ⎋ exit 0|1 + diagnostics + remediation
# region MODULE_CONTRACT
## @purpose  Shared VPS readiness pre-flight checks for deploy and CI pipelines.
##           Verifies SSH accessibility, forced-command availability, /opt/projects/
##           existence, and Docker daemon responsiveness. Provides remediation hints
##           for each failure mode. Import via `source core/lib/vps-readiness.sh`.
## @scope    Sourced by Makefile (deploy target), core/entrypoints/deploy-project.sh,
##           and CI workflows (deploy-project.yml). NOT an executable script.
## @invariants
##   - Source-safe: defines only check_vps_ready() function, no side effects at source time
##   - exit 0 = VPS fully ready; exit 1 = any check FAIL with diagnostics on stderr
##   - --quick: skip Docker daemon check (used for deploy pre-flight where Docker not critical)
##   - --json: output JSON diagnostics for CI parsing (all checks still print to stderr)
##   - SSH check: BatchMode=yes, ConnectTimeout=10 — fails fast on unreachable host
##   - Remediation hints are built-in — each FAIL prints a specific actionable message
##   - NODE_HOST_MAP env var used for node→host resolution (K4/K5 pattern)
## @rationale Extracted as shared module to avoid duplicating pre-flight logic across
##            Makefile, entrypoints, and CI workflows (DevPlan 025 W1).
##            Single source of truth for VPS readiness definition.
## @changes 2026-07-21 | Initial implementation (DevPlan 025 W1)
##           2026-07-21 | W2-E1 — Migrated to lib/ssh.sh: source ssh.sh, 4 inline ssh -i → ssh_read(timeout=30)
# endregion MODULE_CONTRACT

# ── Source lib/ssh.sh for SSH_OPTS_COMMON, ssh_read ────────────────
# shellcheck source=./ssh.sh
source "${PATHS_LIB_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}/ssh.sh"

# ═══════════════════════════════════════════════════════════════════
# region FUNC_check_vps_ready
## @purpose  Run all pre-flight checks for a given node. Exit 0 if ready, exit 1 with diagnostics.
## @param $1  Node name (e.g., "tronyx-vps") — resolved via NODE_HOST_MAP env
## @param $2  Optional: --json (JSON output), --quick (skip Docker check)
## @return 0  VPS ready for deployment
## @return 1  One or more checks failed
## @sideeffect stderr: diagnostic messages with remediation hints
## @complexity O(1) — 3-4 sequential SSH calls with 10s timeout each
## @invariants
##   - Checks ordered by failure probability: SSH → forced-command → /opt/projects → Docker
##   - First failure stops all subsequent checks (fail-fast)
##   - NODE_HOST_MAP is required; exit 1 with clear message if unset
##   - --json: final JSON object printed to stdout after all checks
check_vps_ready() {
    local node_name="${1:-}"
    local output_mode="text"
    local quick_mode=false

    # Parse optional flags
    shift 1 2>/dev/null || true
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --json) output_mode="json"; shift ;;
            --quick) quick_mode=true; shift ;;
            *) shift ;;
        esac
    done

    # ── Internal state ──
    local all_ok=true
    local -a diag_messages=()
    local -a remediation_hints=()
    local ssh_host=""
    local ci_deploy_key=""

    echo "[IMP:8][vps-readiness] Starting pre-flight checks for node=${node_name}" >&2

    # ── Resolve SSH host ───────────────────────────────────────
    if [[ -z "${NODE_HOST_MAP:-}" ]]; then
        all_ok=false
        diag_messages+=("NODE_HOST_MAP not set — cannot resolve node to SSH host")
        remediation_hints+=("Set NODE_HOST_MAP env var: export NODE_HOST_MAP='{\"node\":\"host\"}'")
    else
        _VPS_READINESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        ssh_host="$(echo "${NODE_HOST_MAP}" | python3 "${_VPS_READINESS_DIR}/../internal/scripts/yaml_query.py" \
            --stdin --get "${node_name}" --default "" 2>/dev/null || true)"
        if [[ -z "${ssh_host}" ]]; then
            all_ok=false
            diag_messages+=("Node '${node_name}' not found in NODE_HOST_MAP")
            # ⚡ TRAP[DEBT] · 2026-07-26 · uses yaml_query.py --keys (added in 038c)
            # Replaced inline python3 -c "import json..." with yaml_query.py --keys
            local _keys
            _keys="$(echo "${NODE_HOST_MAP}" | python3 "${_VPS_READINESS_DIR}/../internal/scripts/yaml_query.py" \
                --stdin --keys 2>/dev/null | tr '\n' ' ' || echo "unparseable")"
            remediation_hints+=("Check NODE_HOST_MAP for node '${node_name}'. Current keys: ${_keys}")
        fi
    fi

    # ── Check 1: SSH accessibility ─────────────────────────────
    if $all_ok; then
        local ci_key="${CI_DEPLOY_KEY:-${PLATFORM_CI_DEPLOY_KEY_FILE:-${HOME}/.ssh/ci_deploy_key}}"
        echo "[IMP:8][vps-readiness] Check 1/4: SSH connectivity to ci-deploy@${ssh_host} (timeout=30s via ssh_read)" >&2
        if ssh_read "${ssh_host}" "ci-deploy" "exit" 30 2>/dev/null; then
            echo "[IMP:9][vps-readiness] SSH OK: ci-deploy@${ssh_host}" >&2
        else
            all_ok=false
            diag_messages+=("SSH to ci-deploy@${ssh_host} failed (timeout=30s)")
            remediation_hints+=("VPS unreachable. Check: ssh ci-deploy@${ssh_host} — verify network, SSH key, and ci-deploy user existence")
        fi
    fi

    # ── Check 2: Forced-command responds ───────────────────────
    if $all_ok; then
        echo "[IMP:8][vps-readiness] Check 2/4: Forced-command ping (core delivered?)" >&2
        local ping_result
        ping_result="$(ssh_read "${ssh_host}" "ci-deploy" "ping" 30 2>&1)" || true
        if echo "${ping_result}" | grep -q "pong"; then
            echo "[IMP:9][vps-readiness] Forced-command OK: ping responds with pong" >&2
        else
            all_ok=false
            diag_messages+=("Forced-command 'platform-deliver --ping' did not respond with pong")
            remediation_hints+=("Core not delivered. Run: make bootstrap-node NODE=${node_name} first")
        fi
    fi

    # ── Check 3: /opt/projects/ exists and writable ────────────
    if $all_ok; then
        echo "[IMP:8][vps-readiness] Check 3/4: /opt/projects/ exists and writable" >&2
        local projects_check
        projects_check="$(ssh_read "${ssh_host}" "ci-deploy" \
            "test -d /opt/projects && test -w /opt/projects && echo 'OK' || echo 'FAIL'" 30 2>&1)" || true
        if echo "${projects_check}" | grep -q "OK"; then
            echo "[IMP:9][vps-readiness] /opt/projects/ OK: exists and writable" >&2
        else
            all_ok=false
            diag_messages+=("/opt/projects/ missing or not writable by ci-deploy")
            remediation_hints+=("Project base missing. Run: make bootstrap-node NODE=${node_name}")
        fi
    fi

    # ── Check 4: Docker daemon (skip if --quick) ───────────────
    if $all_ok && ! $quick_mode; then
        echo "[IMP:8][vps-readiness] Check 4/4: Docker daemon responsiveness" >&2
        local docker_check
        docker_check="$(ssh_read "${ssh_host}" "ci-deploy" \
            "docker info --format '{{.ServerVersion}}' 2>/dev/null || echo 'FAIL'" 30 2>&1)" || true
        if echo "${docker_check}" | grep -qv "FAIL"; then
            echo "[IMP:9][vps-readiness] Docker OK: version ${docker_check}" >&2
        else
            all_ok=false
            diag_messages+=("Docker daemon not reachable on ${ssh_host}")
            remediation_hints+=("Docker not running. Run: systemctl start docker on VPS (or check Docker socket permissions)")
        fi
    elif $quick_mode; then
        echo "[IMP:7][vps-readiness] Check 4/4: SKIP (--quick mode — Docker check skipped)" >&2
    fi

    # ── Result ─────────────────────────────────────────────────
    if $all_ok; then
        echo "[IMP:9][vps-readiness] ALL CHECKS PASSED — VPS ready for deployment" >&2
        if [[ "${output_mode}" == "json" ]]; then
            echo '{"status":"ready","node":"'"${node_name}"'","host":"'"${ssh_host}"'","checks":["ssh","forced-command","projects","docker"]}'
        fi
        return 0
    else
        echo "[IMP:10][vps-readiness] VPS NOT READY — ${#diag_messages[@]} check(s) failed" >&2
        local idx=0
        for msg in "${diag_messages[@]}"; do
            echo "[IMP:10][vps-readiness]   FAIL: ${msg}" >&2
            echo "[IMP:10][vps-readiness]   FIX:  ${remediation_hints[$idx]}" >&2
            idx=$((idx + 1))
        done

        if [[ "${output_mode}" == "json" ]]; then
            # Build JSON diagnostics
            local json_diag="["
            local first=true
            idx=0
            for msg in "${diag_messages[@]}"; do
                $first || json_diag+=","
                first=false
                json_diag+='{"check":"'"$(echo "${msg}" | sed 's/"/\\"/g')"'","remediation":"'"$(echo "${remediation_hints[$idx]}" | sed 's/"/\\"/g')"'"}'
                idx=$((idx + 1))
            done
            json_diag+="]"
            echo '{"status":"not_ready","node":"'"${node_name}"'","host":"'"${ssh_host}"'","failures":'"${json_diag}"'}'
        fi
        return 1
    fi
}
# endregion FUNC_check_vps_ready
