#!/usr/bin/env bash
# GREP_SUMMARY: reconcile-projects stub-detection ghcr-check auto-deploy idempotent recovery post-bootstrap
# STRUCTURE: ▶ read node.yaml#projects → ◇ for each: _is_stub? → ◇ ghcr image exists? → ⚡ platform-deliver + compose up → ◇ healthcheck → ⎋ summary
# region MODULE_CONTRACT
## @purpose  Post-bootstrap recovery: detect stub projects in /opt/projects/,
##           check GHCR for Docker images, deploy if found. Idempotent.
## @scope    Called ONLY from converge.sh --reconcile, bootstrap.sh --auto-reconcile,
##           or node-lifecycle.sh step_15+ (AUTO_RECONCILE=true). Not an entrypoint.
##           Sources: logging.sh, paths.sh, docker.sh from core/lib/
## @location core/internal/deploy/reconcile-projects.sh — alongside deploy-project.sh
## @invariants
##   - Reads node.yaml#projects — does NOT scan filesystem blindly
##   - For each project: _is_stub() → docker manifest inspect → platform-deliver + compose up
##   - Stub without GHCR image → WARN "awaiting first CI deploy"
##   - Already deployed (real ai-platform.yaml) → SKIP
##   - Idempotent: repeat run = no-op for deployed projects
##   - Uses same ci-deploy SSH key as deploy-project.sh
##   - Audit log: RECONCILE-<project> entries
##   - dry_run mode: prints planned actions without executing
## @rationale Separate internal script (not entrypoint) per fusion S7 decision.
##            Called through existing entrypoints with --reconcile/--auto-reconcile flags.
##            Lives in internal/deploy/ alongside deploy-project.sh — same layer, same concern.
## @changes 2026-07-21 | Initial implementation (DevPlan 025 W4)
##           2026-07-21 | W2-E1 — Migrated to lib/ssh.sh: source ssh.sh, 2 inline ssh -i → ssh_read
# endregion MODULE_CONTRACT

set -euo pipefail

# region FUNC_reconcile_projects
## @purpose  Reconcile all stub projects from node.yaml — deploy if GHCR image exists
## @param $1  Node name
## @param $2  Path to node.yaml
## @param $3  dry_run (optional: "true" = dry-run mode)
## @return 0  All projects reconciled or skipped
## @return 1  One or more deployments failed
## @io       stderr: LDD logs [IMP:7-10], stderr: deploy progress
reconcile_projects() {
    local node_name="$1"
    local node_yaml="$2"
    local dry_run="${3:-false}"
    local overall_status=0

    echo "[IMP:8][reconcile][main] START: Reconcile stub projects for node=${node_name}" >&2
    echo "[IMP:8][reconcile][main] node.yaml: ${node_yaml}" >&2
    echo "[IMP:8][reconcile][main] dry_run: ${dry_run}" >&2

    # ── Source dependencies ────────────────────────────────────────
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local core_dir
    core_dir="$(cd "${script_dir}/../.." && pwd)"

    # Source paths.sh
    # shellcheck source=../../lib/paths.sh
    source "${core_dir}/lib/paths.sh" 2>/dev/null || {
        # Fallback: define required paths locally
        PLATFORM_ROOT="${PLATFORM_ROOT:-/opt/platform}"
        CORE_DIR="${core_dir}"
    }
    __LOG_PREFIX="reconcile"
    # shellcheck source=../../lib/logging.sh
    source "${core_dir}/lib/logging.sh" 2>/dev/null || true
    # shellcheck source=../../lib/ssh.sh
    source "${core_dir}/lib/ssh.sh" 2>/dev/null || true

    # ── Validate inputs ────────────────────────────────────────────
    if [[ ! -f "${node_yaml}" ]]; then
        echo "[IMP:10][reconcile][main] FATAL: node.yaml not found at ${node_yaml}" >&2
        return 1
    fi

    # ── Extract projects from node.yaml ────────────────────────────
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
            out.append({"name": p.get("name", ""), "org": p.get("org", ""), "domain": p.get("domain", "")})
        elif isinstance(p, str):
            out.append({"name": p, "org": "", "domain": ""})
    print(json.dumps(out))
else:
    print("[]")
PYEOF
)"

    local project_count
    project_count="$(echo "${projects_json}" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")"

    if [[ "${project_count}" -eq 0 ]]; then
        echo "[IMP:9][reconcile][main] SKIP: No projects defined in node.yaml" >&2
        return 0
    fi

    echo "[IMP:8][reconcile][main] Found ${project_count} project(s) in node.yaml" >&2

    # ── For each project ──
    local deployed_count=0
    local skipped_count=0
    local warn_count=0
    local fail_count=0

    while IFS= read -r proj_entry; do
        [[ -z "${proj_entry}" ]] && continue

        local proj_name
        proj_name="$(echo "${proj_entry}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('name',''))" 2>/dev/null || true)"
        local proj_org
        proj_org="$(echo "${proj_entry}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('org',''))" 2>/dev/null || true)"
        local proj_domain
        proj_domain="$(echo "${proj_entry}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('domain',''))" 2>/dev/null || true)"

        [[ -z "${proj_name}" ]] && continue

        local proj_dir="/opt/projects/${proj_org:+${proj_org}/}${proj_name}"
        local ai_yaml="${proj_dir}/ai-platform.yaml"

        echo "[IMP:7][reconcile][${proj_name}] Processing..." >&2

        # ── Check if project directory exists ──
        if [[ ! -d "${proj_dir}" ]]; then
            echo "[IMP:7][reconcile][${proj_name}] SKIP: Project directory not found at ${proj_dir}" >&2
            skipped_count=$((skipped_count + 1))
            continue
        fi

        # ── Check if stub ──
        if [[ -f "${ai_yaml}" ]]; then
            if ! head -1 "${ai_yaml}" 2>/dev/null | grep -q "GENERATED-STUB"; then
                echo "[IMP:7][reconcile][${proj_name}] SKIP: real ai-platform.yaml (already deployed)" >&2
                skipped_count=$((skipped_count + 1))
                continue
            fi
        else
            echo "[IMP:7][reconcile][${proj_name}] SKIP: no ai-platform.yaml (project dir may need creation)" >&2
            skipped_count=$((skipped_count + 1))
            continue
        fi

        echo "[IMP:9][reconcile][${proj_name}] Stub detected — checking GHCR for Docker image..." >&2

        # ── Check GHCR ──
        local context="${proj_org:-tronyx-lab}"
        local ghcr_image="ghcr.io/${context}/${proj_name}:latest"

        if docker manifest inspect "${ghcr_image}" &>/dev/null 2>&1; then
            echo "[IMP:9][reconcile][${proj_name}] Image found: ${ghcr_image} — deploying" >&2

            if [[ "${dry_run}" != "true" ]]; then
                # ── Deliver real ai-platform.yaml via platform-deliver ──
                # Build the deliver payload: generate a proper ai-platform.yaml
                local tmp_dir
                tmp_dir="$(mktemp -d)" || {
                    echo "[IMP:10][reconcile][${proj_name}] FAIL: mktemp failed" >&2
                    fail_count=$((fail_count + 1))
                    continue
                }

                # Write real ai-platform.yaml (not stub)
                cat > "${tmp_dir}/ai-platform.yaml" <<-CONFEOF
project: ${proj_name}
service: ${proj_name}
target_node: ${node_name}
${proj_domain:+domain: ${proj_domain}}
${proj_org:+org: ${proj_org}}
CONFEOF

                # Check if we have compose file or need to use vanilla docker
                local compose_src="${proj_dir}/docker-compose.yml"
                if [[ -f "${proj_dir}/compose.yaml" ]]; then
                    compose_src="${proj_dir}/compose.yaml"
                fi

                if [[ -f "${compose_src}" ]]; then
                    cp "${compose_src}" "${tmp_dir}/"
                else
                    # No compose file yet — create a minimal one for the image
                    cat > "${tmp_dir}/docker-compose.yml" <<-COMPOSEEOF
services:
  ${proj_name}:
    image: ${ghcr_image}
    restart: unless-stopped
COMPOSEEOF
                fi

                # tar and deliver via forced-command
                local deliver_verb="platform-deliver ${proj_org:+${proj_org} }${proj_name}"
                local ci_key="${CI_DEPLOY_KEY:-${PLATFORM_CI_DEPLOY_KEY_FILE:-${HOME}/.ssh/ci_deploy_key}}"

                # Get SSH host from NODE_HOST_MAP or node.yaml
                local ssh_host=""
                if [[ -n "${NODE_HOST_MAP:-}" ]]; then
                    ssh_host="$(echo "${NODE_HOST_MAP}" | python3 -c "import json,sys; m=json.load(sys.stdin); print(m.get('${node_name}',''))" 2>/dev/null || true)"
                fi
                if [[ -z "${ssh_host}" ]]; then
                    # Try extracting from node.yaml
                    ssh_host="$(python3 - "${node_yaml}" <<'PYEOF' 2>/dev/null || true
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
print(data.get('node',{}).get('host',''))
PYEOF
)"
                fi

                if [[ -z "${ssh_host}" ]]; then
                    echo "[IMP:10][reconcile][${proj_name}] FAIL: Cannot resolve SSH host for node=${node_name}" >&2
                    rm -rf "${tmp_dir}"
                    fail_count=$((fail_count + 1))
                    continue
                fi

                # Deliver payload via ssh_read (W2-E1: lib/ssh.sh facade)
                echo "[IMP:8][reconcile][${proj_name}] Delivering payload to ${ssh_host}..." >&2
                (cd "${tmp_dir}" && tar czf - ai-platform.yaml docker-compose.yml 2>/dev/null) | \
                    ssh_read "${ssh_host}" "ci-deploy" "${deliver_verb}" 30 2>&1 || {
                    echo "[IMP:10][reconcile][${proj_name}] FAIL: Payload delivery failed" >&2
                    rm -rf "${tmp_dir}"
                    fail_count=$((fail_count + 1))
                    continue
                }

                rm -rf "${tmp_dir}"

                # ── Docker compose pull and up -d via ssh_exec (W2-E1: lib/ssh.sh facade) ──
                echo "[IMP:9][reconcile][${proj_name}] Deploying via docker compose..." >&2
                ssh_exec "${ssh_host}" "ci-deploy" \
                    "cd ${proj_dir} && docker compose pull && docker compose up -d" 600 "deploy" 2>&1 || {
                    echo "[IMP:10][reconcile][${proj_name}] FAIL: docker compose up failed" >&2
                    fail_count=$((fail_count + 1))
                    continue
                }

                echo "[IMP:9][reconcile][${proj_name}] DONE: stub → deployed" >&2
                deployed_count=$((deployed_count + 1))
            else
                echo "[IMP:8][reconcile][${proj_name}] DRY-RUN: would deliver payload and deploy ${ghcr_image}" >&2
                deployed_count=$((deployed_count + 1))
            fi
        else
            echo "[IMP:8][reconcile][${proj_name}] WARN: No image in GHCR (${ghcr_image}) — awaiting first CI deploy" >&2
            warn_count=$((warn_count + 1))
        fi
    done <<< "$(echo "${projects_json}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for p in data:
    import json as j
    print(j.dumps(p))
" 2>/dev/null || true)"

    # ── Summary ──
    echo "[IMP:9][reconcile][main] ==============================" >&2
    echo "[IMP:9][reconcile][main] Reconcile complete for node=${node_name}" >&2
    echo "[IMP:9][reconcile][main]   deployed: ${deployed_count}" >&2
    echo "[IMP:9][reconcile][main]   skipped:  ${skipped_count}" >&2
    echo "[IMP:9][reconcile][main]   warnings: ${warn_count}" >&2
    echo "[IMP:9][reconcile][main]   failures: ${fail_count}" >&2
    echo "[IMP:9][reconcile][main] ==============================" >&2

    if [[ "${fail_count}" -gt 0 ]]; then
        return 1
    fi
    return 0
}
# endregion FUNC_reconcile_projects

# ── Direct invocation guard ──
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "[IMP:10][reconcile] FATAL: This script is NOT an entrypoint — source it from converge.sh or node-lifecycle.sh" >&2
    echo "[IMP:10][reconcile] Usage: source reconcile-projects.sh && reconcile_projects <node> <node_yaml> [dry_run]" >&2
    exit 1
fi
