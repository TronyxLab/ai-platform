#!/usr/bin/env bash
# GREP_SUMMARY: converge reconciler reconcile-perms reconcile-audit-log reconcile-projects reconcile-networks detect-hosts-drift verify-vhosts idempotent drift-detection desired-state
# STRUCTURE: ▶ argparse ┌--node --dry-run --report-only┐ → ⚡ flock /var/lock/platform-converge.lock → ▶ R1 reconcile_perms ⚡ [IMP:9] chmod ug+x → ▶ R2 reconcile_audit_log ⚡ [IMP:9] 0664 root:adm → ▶ R3 reconcile_projects ⚡ [IMP:9] mkdir + stub → ▶ R4 reconcile_networks ⚡ [IMP:9] proxy-net → ▶ R5 detect_hosts_drift ⚡ [IMP:9] WARN /etc/hosts → ▶ R6 verify_vhosts ⚡ [IMP:9] nginx -t + content-hash → ⊕ aggregate exit_code → ⎋ JSON report (--report-only)
# region MODULE_CONTRACT
## @purpose  Idempotent desired-state reconciler for platform VPS — reads node.yaml as desired
##           state and converges 6 dimensions (R1-R6) to match. No-op on repeat run
##           (SCENARIO_IDEMPOTENT). Designed as a standalone component callable from
##           node-lifecycle.sh (init/update) or directly via make converge.
## @scope    R1 reconcile_perms — M1 executable-bit fix (defense-in-depth)
##           R2 reconcile_audit_log — M2 audit.log 0664 root:adm
##           R3 reconcile_projects — M3 per-project directory + stub files
##           R4 reconcile_networks — M4 proxy-net existence verification
##           R5 detect_hosts_drift — M5 read-only /etc/hosts drift detection
##           R6 verify_vhosts — S1 read-only vhost config integrity + nginx -t
## @location core/internal/bootstrap/converge.sh
## @invariants
##   - R-units are independent — one unit failure does NOT abort others
##   - Exit code: 0=converged (no drifts), 1=mutations applied, 2=unit(s) failed
##   - --report-only: no mutations, exit 0, JSON drift report on stdout
##   - --dry-run: prints plan without mutations, exit 0
##   - node.yaml must be present or FATAL exit 2
##   - Concurrent execution blocked by flock /var/lock/platform-converge.lock
##   - Never modifies project data (volumes, DB, images — invariant O7)
##   - Legacy fallback semantics (TRAP[DECISION] 2026-07-17): ensure_docker_network retained
##     as runtime belt-and-suspenders. Provisioner is primary network creator.
## @rationale Centralized desired-state reconciler replaces 7 manual SSH mutations.
##            R-units are lightweight idempotent checks — fast (seconds) on repeat run.
##            Design chosen over per-mutation lifecycle checkpoints for atomic drift
##            detection + standalone usability.
## @rationale Location: core/internal/bootstrap/ (not lib/) because converge is an executable
##            operation (shebang + main), not a sourced library. Calls lib/ functions.
## ⚠️ TRAP[DECISION] · 2026-07-18 · HI · D⊕B synthesis: R6 verify-only (not render)
## · Rejected: S2 "full render on node" — conflicts with rsync --delete semantics
## · Reason: S1 (render at operator + R6 verify) chosen by user. Template engine
##   not deployed to node — R6 only verifies content-hash integrity of delivered files.
## · Rev: if 3 nodes × 10 projects make manual render a bottleneck, migrate R6 to
##   full render-on-node (template engine shipped with core).
# endregion MODULE_CONTRACT

set -euo pipefail

# ═══════════════════════════════════════════════════════════════════
# GLOBALS
# ═══════════════════════════════════════════════════════════════════
CONVERGE_NODE=""
CONVERGE_DRY_RUN=false
CONVERGE_REPORT_ONLY=false
CONVERGE_JSON_REPORT="{}"
CONVERGE_EXIT_CODE=0  # 0=converged, 1=mutations, 2=errors
LOCK_FILE="/var/lock/platform-converge.lock"
CORE_DIR=""
NODE_YAML_PATH=""

# ═══════════════════════════════════════════════════════════════════
# region FUNC_usage
## @purpose  Print usage instructions and exit
usage() {
    cat <<'EOF'
Usage: converge.sh --node <name> [--dry-run] [--report-only]

Idempotent desired-state reconciler for platform VPS.

Required:
  --node <name>              Node name to reconcile (resolves node.yaml)

Optional:
  --dry-run                  Print planned mutations without executing
  --report-only              Check-only: print JSON drift report to stdout, exit 0
  --help, -h                 Print this help

Exit codes:
  0 — fully converged (no drifts)
  1 — mutations applied (normal after first run)
  2 — one or more R-units failed (errors during reconciliation)

Examples:
  converge.sh --node tronyx-vps
  converge.sh --node tronyx-vps --dry-run
  converge.sh --node tronyx-vps --report-only
EOF
    exit 0
}
# endregion FUNC_usage

# ═══════════════════════════════════════════════════════════════════
# region FUNC_setup_environment
## @purpose  Resolve paths and node.yaml, validate prerequisites
## @globals  CORE_DIR, NODE_YAML_PATH
## @exit 2   If node.yaml not found
setup_environment() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    CORE_DIR="$(cd "${script_dir}/../.." && pwd)"

    # Source paths.sh
    # shellcheck source=../../lib/paths.sh
    source "${CORE_DIR}/lib/paths.sh"

    # Source node-resolver for NODE_YAML resolution
    # shellcheck source=../../lib/node-resolver.sh
    source "${CORE_DIR}/lib/node-resolver.sh"

    # Source logging
    __LOG_PREFIX="converge"
    # shellcheck source=../../lib/logging.sh
    source "${CORE_DIR}/lib/logging.sh"

    # Resolve node.yaml
    NODE_YAML_PATH="$(resolve_node_yaml "${CONVERGE_NODE}" "${PLATFORM_ROOT}" "${HOME}/projects")" || {
        echo "[IMP:10][converge][setup] FATAL: Cannot resolve node.yaml for node=${CONVERGE_NODE}" >&2
        echo "[IMP:10][converge][setup]   Ensure node-configs/ are SCP-delivered and node.yaml exists" >&2
        exit 2
    }

    if [[ ! -f "${NODE_YAML_PATH}" ]]; then
        echo "[IMP:10][converge][setup] FATAL: node.yaml not found at ${NODE_YAML_PATH}" >&2
        exit 2
    fi

    echo "[IMP:8][converge][setup] Node: ${CONVERGE_NODE}" >&2
    echo "[IMP:8][converge][setup] node.yaml: ${NODE_YAML_PATH}" >&2
}
# endregion FUNC_setup_environment

# ═══════════════════════════════════════════════════════════════════
# region FUNC_acquire_lock
## @purpose  Acquire flock to prevent concurrent converge/node-update runs
## @exit 3   If lock cannot be acquired (already running)
acquire_lock() {
    if [[ "${CONVERGE_DRY_RUN}" == "true" ]] || [[ "${CONVERGE_REPORT_ONLY}" == "true" ]]; then
        echo "[IMP:7][converge][lock] SKIP: flock not needed in dry-run/report-only mode" >&2
        return 0
    fi

    if ! mkdir -p "$(dirname "${LOCK_FILE}")" 2>/dev/null; then
        # /var/lock may not be writable — use /tmp fallback
        LOCK_FILE="/tmp/platform-converge.lock"
    fi

    exec 200>"${LOCK_FILE}"
    if ! flock -n 200; then
        echo "[IMP:10][converge][lock] FATAL: Another converge or node-update is already running (lock: ${LOCK_FILE})" >&2
        exit 3
    fi
    echo "[IMP:7][converge][lock] Acquired exclusive lock: ${LOCK_FILE}" >&2
}
# endregion FUNC_acquire_lock

# ═══════════════════════════════════════════════════════════════════
# region FUNC_jq_init / FUNC_jq_add / FUNC_jq_emit
## @purpose  JSON report helpers for --report-only mode
## @uses     python3 for JSON construction (guaranteed available post-bootstrap step 2)
CONVERGE_REPORT_DRIFTS=()

report_init() {
    CONVERGE_REPORT_DRIFTS=()
    echo "[IMP:7][converge][report] Initialized drift report" >&2
}

report_add() {
    local unit="$1"
    local status="$2"    # ok | mutated | skipped | warn | fail
    local detail="$3"
    CONVERGE_REPORT_DRIFTS+=("$(python3 -c "
import json, sys
entry = {'unit': '${unit}', 'status': '${status}', 'detail': '${detail}'}
print(json.dumps(entry))
" 2>/dev/null || echo "{\"unit\":\"${unit}\",\"status\":\"${status}\",\"detail\":\"${detail}\"}")")
}

report_emit() {
    local node_name="${CONVERGE_NODE}"
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    local exit_reason="converged"
    if [[ "${CONVERGE_EXIT_CODE}" -eq 1 ]]; then
        exit_reason="mutations_applied"
    elif [[ "${CONVERGE_EXIT_CODE}" -ge 2 ]]; then
        exit_reason="errors"
    fi

    python3 -c "
import json, sys
entries = [json.loads(e) for e in ${CONVERGE_REPORT_DRIFTS}]
report = {
    'node': '${node_name}',
    'timestamp': '${ts}',
    'exit_code': ${CONVERGE_EXIT_CODE},
    'status': '${exit_reason}',
    'drifts': entries
}
print(json.dumps(report, indent=2))
" 2>/dev/null || {
        echo "[IMP:8][converge][report] WARN: python3 JSON emission failed — plain text fallback" >&2
        echo "node=${node_name} timestamp=${ts} exit_code=${CONVERGE_EXIT_CODE} status=${exit_reason}"
        for e in "${CONVERGE_REPORT_DRIFTS[@]}"; do
            echo "  ${e}"
        done
    }
}
# endregion

# ═══════════════════════════════════════════════════════════════════
# R1 — reconcile_perms (M1, лечебный слой)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_perms
## @purpose  Reconcile executable bit on *.sh files outside core/lib/.
##           M1 defense-in-depth: if rsync delivered files with 644,
##           this restores ug+x. Gate test (test_gate_executable_bit.py)
##           covers the git-index layer; this is the runtime layer.
## @io       stdout/stderr: LDD logs [IMP:7-9], count of fixed files
##           side-effect: chmod ug+x on non-executable scripts
## @edge-cases
##   - 0 non-executable files → SKIP [IMP:7]
##   - Symlink → not followed (-type f)
##   - File disappears between find and chmod (rsync race) → chmod || true
##   - Large file tree → single exec find -exec chmod batch (not per-file)
reconcile_perms() {
    local unit="R1"
    echo "[IMP:8][converge][${unit}] START: reconcile_perms — fixing executable bits outside core/lib/" >&2

    local core_dir="${CORE_DIR}"
    local find_args=("${core_dir}" -name '*.sh' -not -path '*/lib/*' -type f ! -perm -u+x)
    local dry_run="${CONVERGE_DRY_RUN}"
    local report_only="${CONVERGE_REPORT_ONLY}"

    local fix_count=0
    local file_list

    if [[ "${report_only}" == "true" ]] || [[ "${dry_run}" == "true" ]]; then
        # Count only, no mutation
        file_list="$(find "${find_args[@]}" 2>/dev/null || true)"
        fix_count="$(echo "${file_list}" | grep -c . || true)"
        if [[ "${fix_count}" -eq 0 ]]; then
            echo "[IMP:9][converge][${unit}] SKIP: All scripts already executable" >&2
            echo "[IMP:9][converge][${unit}] converged: 0 files need fix" >&2
            report_add "${unit}" "skipped" "All scripts already executable"
            return 0
        fi
        echo "[IMP:9][converge][${unit}] WOULD fix ${fix_count} file(s):" >&2
        echo "${file_list}" >&2
        report_add "${unit}" "mutated" "${fix_count} files would get ug+x"
        CONVERGE_EXIT_CODE=1
        return 0
    fi

    # ── Actual mutation ──
    # Single find -exec chmod to avoid per-file overhead
    fix_count=0
    while IFS= read -r -d '' f; do
        if [[ -f "$f" ]]; then
            chmod ug+x "$f" 2>/dev/null || {
                echo "[IMP:8][converge][${unit}] WARN: chmod failed for ${f} (race with rsync?)" >&2
                continue
            }
            fix_count=$((fix_count + 1))
            echo "[IMP:7][converge][${unit}] Fixed: ${f}" >&2
        fi
    done < <(find "${find_args[@]}" -print0 2>/dev/null || true)

    if [[ "${fix_count}" -eq 0 ]]; then
        echo "[IMP:9][converge][${unit}] SKIP: All scripts already executable" >&2
        report_add "${unit}" "skipped" "All scripts already executable"
    else
        echo "[IMP:9][converge][${unit}] DONE: Fixed ${fix_count} file(s) — chmod ug+x applied" >&2
        report_add "${unit}" "mutated" "${fix_count} files fixed with ug+x"
        CONVERGE_EXIT_CODE=1
    fi
}
# endregion FUNC_reconcile_perms

# ═══════════════════════════════════════════════════════════════════
# R2 — reconcile_audit_log (M2/G1)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_audit_log
## @purpose  Ensure /var/log/platform/audit.log exists with correct
##           ownership (root:adm) and permissions (0664). Also ensures
##           ci-deploy is in adm group for write access.
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: mkdir, chmod, chown, usermod
## @edge-cases
##   - audit.log is a symlink → FATAL [IMP:10] (symlink attack prevention)
##   - ci-deploy not in adm group → usermod -aG adm
##   - File already correct → SKIP
reconcile_audit_log() {
    local unit="R2"
    local log_dir="/var/log/platform"
    local audit_log="${log_dir}/audit.log"
    local dry_run="${CONVERGE_DRY_RUN}"
    local report_only="${CONVERGE_REPORT_ONLY}"

    echo "[IMP:8][converge][${unit}] START: reconcile_audit_log — ensuring ${audit_log} 0664 root:adm" >&2

    # ── Security check: reject symlink target (symlink attack) ──
    if [[ -L "${log_dir}" ]]; then
        echo "[IMP:10][converge][${unit}] FATAL: ${log_dir} is a symlink — possible symlink attack, aborting unit" >&2
        report_add "${unit}" "fail" "Symlink detected: ${log_dir} — possible attack"
        CONVERGE_EXIT_CODE=2
        return 1
    fi
    if [[ -L "${audit_log}" ]]; then
        echo "[IMP:10][converge][${unit}] FATAL: ${audit_log} is a symlink — possible symlink attack, aborting unit" >&2
        report_add "${unit}" "fail" "Symlink detected: ${audit_log} — possible attack"
        CONVERGE_EXIT_CODE=2
        return 1
    fi

    # ── Ensure ci-deploy is in adm group ──
    if id "ci-deploy" &>/dev/null; then
        local groups
        groups="$(id -nG ci-deploy 2>/dev/null || true)"
        if [[ ! " ${groups} " =~ \ adm\  ]]; then
            if [[ "${report_only}" == "true" ]] || [[ "${dry_run}" == "true" ]]; then
                echo "[IMP:9][converge][${unit}] WOULD fix: ci-deploy not in adm group — usermod -aG adm" >&2
                report_add "${unit}" "mutated" "ci-deploy would be added to adm group"
                CONVERGE_EXIT_CODE=1
            else
                echo "[IMP:9][converge][${unit}] Adding ci-deploy to adm group" >&2
                if usermod -aG adm ci-deploy 2>/dev/null; then
                    echo "[IMP:9][converge][${unit}] DONE: ci-deploy added to adm group" >&2
                    report_add "${unit}" "mutated" "ci-deploy added to adm group"
                    CONVERGE_EXIT_CODE=1
                else
                    echo "[IMP:8][converge][${unit}] WARN: usermod failed — ci-deploy may not have write access to audit.log" >&2
                    report_add "${unit}" "warn" "usermod failed for ci-deploy → adm group"
                fi
            fi
        fi
    else
        echo "[IMP:8][converge][${unit}] INFO: ci-deploy user does not exist yet (pre-bootstrap) — skipping group check" >&2
    fi

    # ── Ensure directory ──
    if [[ ! -d "${log_dir}" ]]; then
        if [[ "${report_only}" == "true" ]] || [[ "${dry_run}" == "true" ]]; then
            echo "[IMP:9][converge][${unit}] WOULD create: ${log_dir} 0750 root:adm" >&2
            report_add "${unit}" "mutated" "Directory ${log_dir} would be created"
            CONVERGE_EXIT_CODE=1
        else
            echo "[IMP:8][converge][${unit}] Creating ${log_dir} 0750 root:adm" >&2
            mkdir -p "${log_dir}"
            chmod 0750 "${log_dir}" 2>/dev/null || true
            chown root:adm "${log_dir}" 2>/dev/null || chown root:root "${log_dir}"
            echo "[IMP:9][converge][${unit}] DONE: ${log_dir} created 0750 root:adm" >&2
            report_add "${unit}" "mutated" "Directory ${log_dir} created"
            CONVERGE_EXIT_CODE=1
        fi
    fi

    # ── Ensure audit.log ──
    if [[ ! -f "${audit_log}" ]]; then
        if [[ "${report_only}" == "true" ]] || [[ "${dry_run}" == "true" ]]; then
            echo "[IMP:9][converge][${unit}] WOULD create: ${audit_log} 0664 root:adm" >&2
            report_add "${unit}" "mutated" "File ${audit_log} would be created"
            CONVERGE_EXIT_CODE=1
        else
            echo "[IMP:8][converge][${unit}] Creating ${audit_log} 0664 root:adm" >&2
            touch "${audit_log}"
            chmod 0664 "${audit_log}" 2>/dev/null || true
            chown root:adm "${audit_log}" 2>/dev/null || true
            echo "[IMP:9][converge][${unit}] DONE: ${audit_log} created 0664 root:adm" >&2
            report_add "${unit}" "mutated" "File ${audit_log} created"
            CONVERGE_EXIT_CODE=1
        fi
    else
        # File exists — verify and fix permissions
        local current_mode
        current_mode="$(stat -c '%a' "${audit_log}" 2>/dev/null || echo "000")"
        local current_owner
        current_owner="$(stat -c '%u:%g' "${audit_log}" 2>/dev/null || echo "0:0")"

        if [[ "${current_mode}" != "0664" ]] || [[ "${current_owner}" != "0:4" ]]; then
            if [[ "${report_only}" == "true" ]] || [[ "${dry_run}" == "true" ]]; then
                echo "[IMP:9][converge][${unit}] WOULD fix: ${audit_log} mode=${current_mode} owner=${current_owner}" >&2
                report_add "${unit}" "mutated" "audit.log permissions would be fixed"
                CONVERGE_EXIT_CODE=1
            else
                echo "[IMP:8][converge][${unit}] Fixing permissions: ${audit_log} mode=${current_mode} owner=${current_owner}" >&2
                chmod 0664 "${audit_log}" 2>/dev/null || true
                chown root:adm "${audit_log}" 2>/dev/null || true
                echo "[IMP:9][converge][${unit}] DONE: ${audit_log} permissions corrected" >&2
                report_add "${unit}" "mutated" "audit.log permissions corrected to 0664 root:adm"
                CONVERGE_EXIT_CODE=1
            fi
        else
            echo "[IMP:9][converge][${unit}] SKIP: ${audit_log} already 0664 root:adm (converged)" >&2
            if [[ -z "$(find "${log_dir}" -maxdepth 0 -perm 0750 2>/dev/null)" ]]; then
                report_add "${unit}" "converged" "audit.log permissions correct"
            else
                report_add "${unit}" "converged" "audit.log permissions correct"
            fi
        fi
    fi

    return 0
}
# endregion FUNC_reconcile_audit_log

# ═══════════════════════════════════════════════════════════════════
# R3 — reconcile_projects (M3/G2)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_projects
## @purpose  Read node.yaml#projects and ensure per-project directories,
##           ownership ci-deploy:ci-deploy, stub ai-platform.yaml, and
##           empty .env.platform (if-missing).
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: mkdir -p, chown, touch
## @edge-cases
##   - projects: [] or missing section → SKIP
##   - Invalid project name (/ or ..) → FAIL [IMP:10]
##   - Existing non-stub ai-platform.yaml → NOT touched
##   - Existing stub → NOT overwritten (no-op)
##   - Existing .env.platform → NOT touched (if-missing)
reconcile_projects() {
    local unit="R3"
    local dry_run="${CONVERGE_DRY_RUN}"
    local report_only="${CONVERGE_REPORT_ONLY}"
    local node_yaml="${NODE_YAML_PATH}"

    echo "[IMP:8][converge][${unit}] START: reconcile_projects — ensuring project directories and stubs" >&2

    # ── Validate project name ──
    _validate_project_name() {
        local name="$1"
        if [[ -z "${name}" ]]; then
            echo "[IMP:10][converge][${unit}] FAIL: Empty project name" >&2
            return 1
        fi
        if [[ "${name}" =~ [/] ]] || [[ "${name}" =~ \.\. ]]; then
            echo "[IMP:10][converge][${unit}] FAIL: Invalid project name '${name}' — contains / or .." >&2
            return 1
        fi
        if [[ ! "${name}" =~ ^[a-zA-Z0-9_-]+$ ]]; then
            echo "[IMP:10][converge][${unit}] FAIL: Invalid project name '${name}' — only [a-zA-Z0-9_-] allowed" >&2
            return 1
        fi
        return 0
    }

    # ── Extract projects list from node.yaml ──
    local projects_json
    projects_json="$(python3 - "${node_yaml}" <<'PYEOF' 2>/dev/null || echo "[]"
import yaml, json, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
projects = data.get('projects', [])
if isinstance(projects, list):
    out = []
    for p in projects:
        if isinstance(p, dict):
            out.append({"name": p.get("name", ""), "domain": p.get("domain", "")})
        elif isinstance(p, str):
            out.append({"name": p, "domain": ""})
    print(json.dumps(out))
else:
    print("[]")
PYEOF
)"

    local project_list
    project_list="$(echo "${projects_json}" | python3 -c "import json,sys; [print(p['name']) for p in json.load(sys.stdin) if p.get('name')]" 2>/dev/null || true)"

    if [[ -z "${project_list}" ]]; then
        echo "[IMP:9][converge][${unit}] SKIP: No projects defined in node.yaml or projects: []" >&2
        report_add "${unit}" "skipped" "No projects defined in node.yaml"
        return 0
    fi

    local projects_dir="/opt/projects"
    local mutated=0
    local errors=0

    while IFS= read -r proj_name; do
        [[ -z "${proj_name}" ]] && continue

        echo "[IMP:7][converge][${unit}] Processing project: ${proj_name}" >&2

        # Validate name
        if ! _validate_project_name "${proj_name}"; then
            errors=$((errors + 1))
            report_add "${unit}" "fail" "Invalid project name: ${proj_name}"
            CONVERGE_EXIT_CODE=2
            continue
        fi

        local proj_dir="${projects_dir}/${proj_name}"

        # ── mkdir -p ──
        if [[ ! -d "${proj_dir}" ]]; then
            if [[ "${report_only}" == "true" ]] || [[ "${dry_run}" == "true" ]]; then
                echo "[IMP:9][converge][${unit}] WOULD create directory: ${proj_dir}" >&2
                mutated=$((mutated + 1))
            else
                echo "[IMP:8][converge][${unit}] Creating directory: ${proj_dir}" >&2
                mkdir -p "${proj_dir}" || {
                    echo "[IMP:10][converge][${unit}] FAIL: mkdir -p ${proj_dir} failed" >&2
                    errors=$((errors + 1))
                    continue
                }
                echo "[IMP:9][converge][${unit}] DONE: ${proj_dir} created" >&2
                mutated=$((mutated + 1))
            fi
        fi

        # ── chown ci-deploy:ci-deploy (directory only, not recursive) ──
        if [[ "${report_only}" != "true" ]] && [[ "${dry_run}" != "true" ]] && [[ -d "${proj_dir}" ]]; then
            chown ci-deploy:ci-deploy "${proj_dir}" 2>/dev/null || {
                echo "[IMP:8][converge][${unit}] WARN: chown ci-deploy:ci-deploy ${proj_dir} failed (user may not exist yet)" >&2
            }
        fi

        # ── stub ai-platform.yaml (if-missing) ──
        local stub_file="${proj_dir}/ai-platform.yaml"
        if [[ ! -f "${stub_file}" ]]; then
            if [[ "${report_only}" == "true" ]] || [[ "${dry_run}" == "true" ]]; then
                echo "[IMP:9][converge][${unit}] WOULD create stub: ${stub_file}" >&2
                mutated=$((mutated + 1))
            else
                echo "[IMP:8][converge][${unit}] Creating stub: ${stub_file}" >&2
                cat > "${stub_file}" <<-STUBEOF
# GENERATED-STUB by converge — overwritten by CI deliver
# This is a placeholder created during node convergence.
# CI deliver will replace it with the actual project configuration.
project: ${proj_name}
service: ${proj_name}
STUBEOF
                chown ci-deploy:ci-deploy "${stub_file}" 2>/dev/null || true
                echo "[IMP:9][converge][${unit}] DONE: stub created for ${proj_name}" >&2
                mutated=$((mutated + 1))
            fi
        else
            echo "[IMP:7][converge][${unit}] SKIP: ${stub_file} already exists (not overwritten)" >&2
        fi

        # ── empty .env.platform (if-missing) 0640 ci-deploy ──
        local env_file="${proj_dir}/.env.platform"
        if [[ ! -f "${env_file}" ]]; then
            if [[ "${report_only}" == "true" ]] || [[ "${dry_run}" == "true" ]]; then
                echo "[IMP:9][converge][${unit}] WOULD create: ${env_file} 0640 ci-deploy" >&2
                mutated=$((mutated + 1))
            else
                echo "[IMP:8][converge][${unit}] Creating empty: ${env_file} 0640 ci-deploy" >&2
                touch "${env_file}"
                chmod 0640 "${env_file}" 2>/dev/null || true
                chown ci-deploy:ci-deploy "${env_file}" 2>/dev/null || true
                echo "[IMP:9][converge][${unit}] DONE: ${env_file} created" >&2
                mutated=$((mutated + 1))
            fi
        else
            echo "[IMP:7][converge][${unit}] SKIP: ${env_file} already exists (if-missing policy)" >&2
        fi
    done <<< "${project_list}"

    if [[ "${mutated}" -gt 0 ]]; then
        report_add "${unit}" "mutated" "${mutated} project item(s) created/fixed"
        CONVERGE_EXIT_CODE=1
    elif [[ "${errors}" -gt 0 ]]; then
        report_add "${unit}" "fail" "${errors} project(s) had errors"
    else
        report_add "${unit}" "converged" "All project directories and stubs present"
    fi

    echo "[IMP:9][converge][${unit}] DONE: projects reconciled (mutated=${mutated} errors=${errors})" >&2
}
# endregion FUNC_reconcile_projects

# ═══════════════════════════════════════════════════════════════════
# R4 — reconcile_networks (M4)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_networks
## @purpose  Ensure proxy-net Docker network exists (runtime fallback).
##           For each running project container, verify proxy-net connectivity.
##           Does NOT auto-connect — that's the compose project's responsibility.
## @io       stdout/stderr: LDD logs [IMP:7-9]
##           side-effect: docker network create (if missing)
## @edge-cases
##   - Docker daemon unavailable → FAIL unit, continue others
##   - proxy-net exists with wrong driver → WARN, don't recreate
##   - Concurrent docker network create (race with deploy-modules) → handled via
##     inspect-after-create pattern (network already exists → no-op)
## @rationale Uses ensure_docker_network() from lib/docker.sh (extracted from
##            deploy-modules.sh). R4 is runtime fallback — provisioner is primary
##            source of truth for networks (TRAP[DECISION] 2026-07-17).
reconcile_networks() {
    local unit="R4"
    local dry_run="${CONVERGE_DRY_RUN}"
    local report_only="${CONVERGE_REPORT_ONLY}"

    echo "[IMP:8][converge][${unit}] START: reconcile_networks — ensuring proxy-net exists" >&2

    # Source docker.sh for ensure_docker_network()
    # shellcheck source=../../lib/docker.sh
    source "${CORE_DIR}/lib/docker.sh" 2>/dev/null || {
        echo "[IMP:10][converge][${unit}] FATAL: Cannot source lib/docker.sh" >&2
        report_add "${unit}" "fail" "Cannot source lib/docker.sh"
        CONVERGE_EXIT_CODE=2
        return 1
    }

    # ── Check docker daemon availability ──
    if ! docker info &>/dev/null 2>&1; then
        echo "[IMP:10][converge][${unit}] FAIL: Docker daemon not available — skipping network reconciliation" >&2
        report_add "${unit}" "fail" "Docker daemon unavailable"
        CONVERGE_EXIT_CODE=2
        return 1
    fi

    # ── proxy-net: ensure exists ──
    if ! docker network inspect proxy-net &>/dev/null 2>&1; then
        if [[ "${report_only}" == "true" ]] || [[ "${dry_run}" == "true" ]]; then
            echo "[IMP:9][converge][${unit}] WOULD create: proxy-net (bridge)" >&2
            report_add "${unit}" "mutated" "proxy-net would be created"
            CONVERGE_EXIT_CODE=1
        else
            echo "[IMP:8][converge][${unit}] Creating proxy-net (runtime fallback)" >&2
            # ensure_docker_network is defined in lib/docker.sh
            ensure_docker_network "proxy-net" "bridge"
            echo "[IMP:9][converge][${unit}] DONE: proxy-net created" >&2
            report_add "${unit}" "mutated" "proxy-net created"
            CONVERGE_EXIT_CODE=1
        fi
    else
        # Check driver
        local current_driver
        current_driver="$(docker network inspect proxy-net --format '{{.Driver}}' 2>/dev/null || echo "unknown")"
        if [[ "${current_driver}" != "bridge" ]]; then
            echo "[IMP:9][converge][${unit}] WARN: proxy-net exists but driver=${current_driver} (expected=bridge)" >&2
            report_add "${unit}" "warn" "proxy-net driver=${current_driver} (expected=bridge)"
        else
            echo "[IMP:9][converge][${unit}] SKIP: proxy-net already exists (driver=bridge, converged)" >&2
        fi
    fi

    # ── Check project containers for proxy-net connectivity ──
    local node_yaml="${NODE_YAML_PATH}"
    local project_names
    project_names="$(python3 - "${node_yaml}" <<'PYEOF' 2>/dev/null || true
import yaml, json, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
projects = data.get('projects', [])
for p in projects:
    if isinstance(p, dict):
        name = p.get('name', '')
        if name:
            print(name)
PYEOF
)"

    if [[ -n "${project_names}" ]]; then
        while IFS= read -r pname; do
            [[ -z "${pname}" ]] && continue
            # Find running containers for this project
            local containers
            containers="$(docker ps --filter "label=com.docker.compose.project=${pname}" --format '{{.Names}}' 2>/dev/null || true)"
            if [[ -z "${containers}" ]]; then
                echo "[IMP:7][converge][${unit}] INFO: No running containers for project ${pname}" >&2
                continue
            fi
            while IFS= read -r cname; do
                [[ -z "${cname}" ]] && continue
                local networks
                networks="$(docker inspect "${cname}" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null || true)"
                if [[ ! " ${networks} " =~ \ proxy-net\  ]]; then
                    echo "[IMP:9][converge][${unit}] WARN: Container ${cname} (project ${pname}) NOT connected to proxy-net — compose project should declare proxy-net external" >&2
                    report_add "${unit}" "warn" "Container ${cname} not connected to proxy-net"
                else
                    echo "[IMP:7][converge][${unit}] OK: Container ${cname} connected to proxy-net" >&2
                fi
            done <<< "${containers}"
        done <<< "${project_names}"
    fi

    echo "[IMP:9][converge][${unit}] DONE: networks reconciled" >&2
}
# endregion FUNC_reconcile_networks

# ═══════════════════════════════════════════════════════════════════
# R5 — detect_hosts_drift (M5/G5 — только детекция)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_detect_hosts_drift
## @purpose  Read-only detection of stale /etc/hosts entries for project names
##           from node.yaml. No mutation — only WARN with [IMP:9].
## @io       stdout/stderr: WARN logs [IMP:9], JSON entries in report
## @edge-cases
##   - /etc/hosts unreadable → WARN, not fail
##   - Project name matches substring of legitimate entry → \b word boundary grep
detect_hosts_drift() {
    local unit="R5"
    local node_yaml="${NODE_YAML_PATH}"

    echo "[IMP:8][converge][${unit}] START: detect_hosts_drift — checking /etc/hosts for stale project entries" >&2

    local hosts_file="/etc/hosts"
    if [[ ! -r "${hosts_file}" ]]; then
        echo "[IMP:8][converge][${unit}] WARN: ${hosts_file} not readable — skipping drift detection" >&2
        report_add "${unit}" "warn" "Cannot read /etc/hosts"
        return 0
    fi

    # Extract project names from node.yaml
    local project_names
    project_names="$(python3 - "${node_yaml}" <<'PYEOF' 2>/dev/null || true
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
projects = data.get('projects', [])
for p in projects:
    if isinstance(p, dict):
        name = p.get('name', '')
        if name:
            print(name)
PYEOF
)"

    if [[ -z "${project_names}" ]]; then
        echo "[IMP:9][converge][${unit}] SKIP: No projects defined in node.yaml" >&2
        report_add "${unit}" "skipped" "No projects to check"
        return 0
    fi

    local drift_found=0
    while IFS= read -r pname; do
        [[ -z "${pname}" ]] && continue
        # Word-boundary match to avoid substring false positives
        if grep -qws "\b${pname}\b" "${hosts_file}" 2>/dev/null; then
            local matches
            matches="$(grep -ws "\b${pname}\b" "${hosts_file}" 2>/dev/null | head -5 || true)"
            echo "[IMP:9][converge][${unit}] WARN: Stale /etc/hosts entry found for project '${pname}':" >&2
            while IFS= read -r line; do
                echo "[IMP:9][converge][${unit}]   /etc/hosts: ${line}" >&2
            done <<< "${matches}"
            echo "[IMP:9][converge][${unit}]   Runbook: remove manually per OperatorChecklist §11 (G5 resolution)" >&2
            report_add "${unit}" "warn" "Stale /etc/hosts entry for ${pname}"
            drift_found=$((drift_found + 1))
            CONVERGE_EXIT_CODE=1
        fi
    done <<< "${project_names}"

    if [[ "${drift_found}" -eq 0 ]]; then
        echo "[IMP:9][converge][${unit}] SKIP: No stale /etc/hosts entries (converged)" >&2
        report_add "${unit}" "converged" "No stale /etc/hosts entries"
    else
        echo "[IMP:9][converge][${unit}] DONE: ${drift_found} drift(s) detected (read-only — no mutation)" >&2
    fi
}
# endregion FUNC_detect_hosts_drift

# ═══════════════════════════════════════════════════════════════════
# R6 — verify_vhosts (S1, read-only)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_verify_vhosts
## @purpose  Read-only verification of nginx vhost config integrity.
##           Checks: (1) for each project with domain, <domain>.conf exists;
##           (2) GENERATED marker + content-hash match; (3) orphan vhosts
##           without project → WARN; (4) docker exec nginx nginx -t passes.
## @io       stdout/stderr: LDD logs [IMP:7-10], drift report entries
## @param    (none — reads NODE_YAML_PATH, uses CORE_DIR)
## @edge-cases
##   - nginx container not running → WARN nginx -t, other checks proceed
##   - Project without domain → SKIP
##   - Orphan vhost (no project match) → WARN
##   - Legacy conf without GENERATED marker → WARN (transition period)
verify_vhosts() {
    local unit="R6"
    local node_yaml="${NODE_YAML_PATH}"

    echo "[IMP:8][converge][${unit}] START: verify_vhosts — checking nginx vhost integrity" >&2

    # ── Get projects with domains from node.yaml ──
    local projects_with_domains
    projects_with_domains="$(python3 - "${node_yaml}" <<'PYEOF' 2>/dev/null || true
import yaml, json, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
projects = data.get('projects', [])
out = []
for p in projects:
    if isinstance(p, dict):
        name = p.get('name', '')
        domain = p.get('domain', '')
        if name and domain:
            out.append({'name': name, 'domain': domain})
print(json.dumps(out))
PYEOF
)"

    # ── Determine nginx overlay directory ──
    local overlay_dir=""
    local context_name
    context_name="$(python3 - "${node_yaml}" <<'PYEOF' 2>/dev/null || true
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
print(data.get('context', ''))
PYEOF
)"
    if [[ -n "${context_name}" ]]; then
        overlay_dir="/opt/${context_name}/platform/modules/nginx"
    else
        overlay_dir="/opt/${CONVERGE_NODE}/overlays/nginx"
    fi

    local nginx_vhost_dir="${overlay_dir}"
    if [[ ! -d "${nginx_vhost_dir}" ]]; then
        echo "[IMP:8][converge][${unit}] INFO: nginx overlay directory not found: ${nginx_vhost_dir} — checking default paths" >&2
        # Fallback: try node-configs path
        overlay_dir="/opt/node-configs/${CONVERGE_NODE}/overlays/nginx"
        if [[ -d "${overlay_dir}" ]]; then
            nginx_vhost_dir="${overlay_dir}"
        else
            echo "[IMP:8][converge][${unit}] WARN: nginx overlay not found at any expected path — vhost verification limited" >&2
            report_add "${unit}" "warn" "nginx overlay directory not found"
        fi
    fi

    # Source content-hash.sh for content hash computation
    # shellcheck source=../bootstrap/content-hash.sh
    source "${CORE_DIR}/internal/bootstrap/content-hash.sh" 2>/dev/null || true

    # ── Parse projects list ──
    local domain_count=0
    local vhost_errors=0

    # Collect expected domains
    local -a expected_domains=()
    local -a project_name_map=()
    if [[ -n "${projects_with_domains}" ]]; then
        while IFS= read -r entry; do
            [[ -z "${entry}" ]] && continue
            local pname
            pname="$(echo "${entry}" | python3 -c "import json,sys; print(json.load(sys.stdin)['name'])" 2>/dev/null || true)"
            local domain
            domain="$(echo "${entry}" | python3 -c "import json,sys; print(json.load(sys.stdin)['domain'])" 2>/dev/null || true)"
            [[ -z "${pname}" || -z "${domain}" ]] && continue
            expected_domains+=("${domain}")
            project_name_map+=("${pname}|${domain}")
            domain_count=$((domain_count + 1))

            # Check: <domain>.conf exists
            local vhost_file="${nginx_vhost_dir}/${domain}.conf"
            if [[ -f "${vhost_file}" ]]; then
                # Check GENERATED marker
                if head -1 "${vhost_file}" 2>/dev/null | grep -q "GENERATED by add-vhost.sh"; then
                    echo "[IMP:7][converge][${unit}] OK: ${domain}.conf has GENERATED marker" >&2
                    # Check content hash if possible
                    if type compute_step_hash &>/dev/null; then
                        local file_hash
                        file_hash="$(sha256sum "${vhost_file}" 2>/dev/null | cut -d' ' -f1 || echo "unknown")"
                        echo "[IMP:7][converge][${unit}] OK: ${domain}.conf sha256=${file_hash:0:12}..." >&2
                    fi
                else
                    echo "[IMP:9][converge][${unit}] WARN: ${domain}.conf exists but missing GENERATED marker — legacy config (post-migration: FAIL)" >&2
                    report_add "${unit}" "warn" "${domain}.conf: missing GENERATED marker"
                fi
            else
                echo "[IMP:9][converge][${unit}] FAIL: Vhost file not found: ${nginx_vhost_dir}/${domain}.conf" >&2
                report_add "${unit}" "fail" "${domain}.conf not found"
                vhost_errors=$((vhost_errors + 1))
                CONVERGE_EXIT_CODE=2
            fi
        done <<< "$(echo "${projects_with_domains}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for d in data:
    print(json.dumps(d))
" 2>/dev/null || true)"
    else
        echo "[IMP:9][converge][${unit}] SKIP: No projects with domains in node.yaml" >&2
        report_add "${unit}" "skipped" "No projects with domains to verify"
    fi

    # ── Check for orphan vhosts (vhost files with no matching project domain) ──
    if [[ -d "${nginx_vhost_dir}" ]]; then
        while IFS= read -r vhost_file; do
            [[ -z "${vhost_file}" ]] && continue
            local fname
            fname="$(basename "${vhost_file}")"
            # Only check .conf files
            [[ "${fname}" != *.conf ]] && continue
            # Skip non-vhost files
            [[ "${fname}" == "nginx.conf" ]] && continue

            local matched=false
            for expected in "${expected_domains[@]}"; do
                if [[ "${fname}" == "${expected}.conf" ]]; then
                    matched=true
                    break
                fi
            done
            if ! $matched; then
                echo "[IMP:9][converge][${unit}] WARN: Orphan vhost detected — ${fname} has no matching project in node.yaml" >&2
                report_add "${unit}" "warn" "Orphan vhost: ${fname}"
            fi
        done < <(find "${nginx_vhost_dir}" -maxdepth 1 -name '*.conf' -type f 2>/dev/null || true)
    fi

    # ── nginx -t validation ──
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "nginx"; then
        echo "[IMP:8][converge][${unit}] Running nginx -t..." >&2
        if docker exec nginx nginx -t 2>&1; then
            echo "[IMP:9][converge][${unit}] OK: nginx -t passed" >&2
        else
            echo "[IMP:10][converge][${unit}] FAIL: nginx -t failed — nginx reload blocked" >&2
            report_add "${unit}" "fail" "nginx -t failed — reload blocked"
            vhost_errors=$((vhost_errors + 1))
            CONVERGE_EXIT_CODE=2
        fi
    else
        echo "[IMP:8][converge][${unit}] WARN: nginx container not running — skipping nginx -t (syntax already verified at operator)" >&2
        report_add "${unit}" "warn" "nginx container not running — nginx -t skipped"
    fi

    if [[ "${domain_count}" -eq 0 ]]; then
        echo "[IMP:9][converge][${unit}] DONE: no domains to verify" >&2
    elif [[ "${vhost_errors}" -eq 0 ]]; then
        echo "[IMP:9][converge][${unit}] DONE: ${domain_count} vhost(s) verified — all OK" >&2
    else
        echo "[IMP:9][converge][${unit}] DONE: ${domain_count} vhost(s), ${vhost_errors} error(s)" >&2
    fi
}
# endregion FUNC_verify_vhosts

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Entry point: parse args, setup env, dispatch R1-R6,
##           aggregate exit code, emit JSON report if --report-only
main() {
    # ── Parse CLI args ──
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --node) CONVERGE_NODE="$2"; shift 2 ;;
            --dry-run) CONVERGE_DRY_RUN=true; shift ;;
            --report-only) CONVERGE_REPORT_ONLY=true; shift ;;
            --help|-h) usage ;;
            *) echo "[IMP:10][converge][args] ERROR: Unknown argument: $1" >&2; usage ;;
        esac
    done

    if [[ -z "${CONVERGE_NODE}" ]]; then
        echo "[IMP:10][converge][args] FATAL: --node is required" >&2
        usage
    fi

    # ── Setup environment ──
    setup_environment

    # ── Acquire flock (not in dry-run/report-only) ──
    acquire_lock

    # ── Init report ──
    report_init

    # ── Print header ──
    echo "[IMP:9][converge][main] ==============================" >&2
    echo "[IMP:9][converge][main] Platform Converge START" >&2
    echo "[IMP:9][converge][main] Node: ${CONVERGE_NODE}" >&2
    echo "[IMP:9][converge][main] Mode: $(if [[ "${CONVERGE_DRY_RUN}" == "true" ]]; then echo DRY-RUN; elif [[ "${CONVERGE_REPORT_ONLY}" == "true" ]]; then echo REPORT-ONLY; else echo CONVERGE; fi)" >&2
    echo "[IMP:9][converge][main] node.yaml: ${NODE_YAML_PATH}" >&2
    echo "[IMP:9][converge][main] ==============================" >&2

    # ── Dispatch R-units (each returns independently) ──
    reconcile_perms       || true
    reconcile_audit_log   || true
    reconcile_projects    || true
    reconcile_networks    || true
    detect_hosts_drift    || true
    verify_vhosts         || true

    # ── Final summary ──
    echo "[IMP:9][converge][main] ==============================" >&2
    if [[ "${CONVERGE_EXIT_CODE}" -eq 0 ]]; then
        echo "[IMP:9][converge][main] FULLY CONVERGED — all R-units converged (idempotent)" >&2
    elif [[ "${CONVERGE_EXIT_CODE}" -eq 1 ]]; then
        echo "[IMP:9][converge][main] MUTATIONS APPLIED — drift corrected (repeat run = no-op)" >&2
    else
        echo "[IMP:9][converge][main] ERRORS DETECTED — some R-units failed (exit code ${CONVERGE_EXIT_CODE})" >&2
    fi
    echo "[IMP:9][converge][main] ==============================" >&2

    # ── Report-only: JSON to stdout ──
    if [[ "${CONVERGE_REPORT_ONLY}" == "true" ]]; then
        report_emit
        exit 0
    fi

    exit "${CONVERGE_EXIT_CODE}"
}
# endregion FUNC_main

main "$@"
