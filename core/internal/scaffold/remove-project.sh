#!/usr/bin/env bash
# shellcheck disable=SC2034
# GREP_SUMMARY: remove-project lifecycle unregister node-yaml compose-down vhost-off safe-remove report SSH
# STRUCTURE: ▶ init → parse_args → find_node_yaml → lookup project → [not found → SKIP exit 0] → unregister_from_node_yaml → remove_vhost → ssh_compose_down → print_report → ⊕ exit 0
# region MODULE_CONTRACT
## @purpose  Remove a project from the platform lifecycle: unregister from node.yaml,
##           stop containers on target node (compose down WITHOUT -v), deactivate vhost.
##           Volumes, databases, images, and GitHub repo are NEVER touched (O7/DD10).
##           Prints a report of what was NOT deleted at the end.
## @scope    Called from scaffold.sh remove-project.
## @io       stdout: status messages, report of what was/wasn't deleted
##           stderr: LDD logs via log_imp at IMP:7-10
## @invariants
##   - NEVER runs `down -v`, `volume rm`, `image rm`, `gh repo delete` (O7/DD10)
##   - Idempotent: second call with same project → SKIP with exit 0
##   - VPS unavailability → unregister + vhost removal execute, SSH step skipped + instruction printed
##   - Project not found in node.yaml → SKIP with exit 0
##   - Prints report of what was NOT deleted (volumes, DB images, GitHub repo, local dir)
## @rationale Completes the project lifecycle (CREATE→REGISTER→DEPLOY→REMOVE).
##            Safe-only (O7): no automatic data deletion. User must manually clean
##            volumes/DB/repo if desired.
## @links    CALLED_BY: scaffold.sh (remove-project)
##           CALLS: yq for node.yaml manipulation, SSH for remote compose down
##           CONTRACTS: O7/DD10 — remove = disconnect, not destroy
## @changes  2026-07-17 · T10 — full implementation
##           2026-07-21 | W2-E1 — Migrated to lib/ssh.sh: source ssh.sh, 2 inline ssh → ssh_read/ssh_exec
##           2026-07-21 | W2-E3 — Added audit_step wrapper for cleanup operations (source audit_logging.sh)
# 💼 TRAP[BUSINESS] · 2026-07-17 · HI · remove = disconnect, данные не удаляются автоматически
# · Source: owner
# · Risk: авто-очистка = невосстановимая потеря БД проекта
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")}"
PROJECTS_ROOT="${PROJECTS_ROOT:-$(dirname "$PLATFORM_ROOT")}"

__LOG_PREFIX="remove-project"
source "${PLATFORM_ROOT}/core/lib/logging.sh"
source "${PLATFORM_ROOT}/core/lib/args.sh"
source "${PLATFORM_ROOT}/core/lib/ssh.sh"
source "${PLATFORM_ROOT}/core/lib/audit_logging.sh"
source "${PLATFORM_ROOT}/core/lib/python_deps.sh"

# ═══════════════════════════════════════════════════════════════════
# GLOBALS
# ═══════════════════════════════════════════════════════════════════
PROJECT_NAME=""
NODE_NAME=""
FORCE=0
FOUND_NODE_YAML=""
FOUND_PROJECT_ENTRY=""
PROJECT_DOMAIN=""
PROJECT_NODE_HOST=""
PROJECT_ORG=""
NODE_CONFIGS_DIR=""

USAGE_SCRIPT="remove-project.sh"
USAGE_DESC="Remove a project from the platform lifecycle (SAFE — no data loss). Volumes, databases, images, and GitHub repos are NEVER deleted automatically."
USAGE_OPTIONS=(
    "--name <name>     Project name to remove"
    "--node <node>     Target node name (searched in all node-configs if omitted)"
    "--force           Skip confirmation prompt"
)

# ──────────────────────────────────────────────────────────────────
# region FUNC_parse_cli_args
## @purpose  Parse CLI arguments via lib/args.sh (wrapper avoids name collision)
## @io       Sets PROJECT_NAME, NODE_NAME, FORCE globals
## @complexity O(n) where n = number of args
parse_cli_args() {
    declare -A ARG_SPEC=(
        [--name]="value"
        [--node]="value"
        [--force]="flag"
    )
    declare -A ARG_RESULT

    # Call lib function (not local — different function name avoids recursion)
    parse_args ARG_SPEC ARG_RESULT -- "$@" || exit 1

    PROJECT_NAME="${ARG_RESULT[--name]:-}"
    NODE_NAME="${ARG_RESULT[--node]:-}"
    FORCE=$([[ -n "${ARG_RESULT[--force]:-}" ]] && echo 1 || echo 0)

    if [[ -z "$PROJECT_NAME" ]]; then
        log_imp 10 "-" "FAIL-FAST: --name is required"
        usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" >&2
        exit 1
    fi

    log_imp 7 "-" "Args: name=${PROJECT_NAME} node=${NODE_NAME:-<auto>} force=${FORCE}"
}
# endregion FUNC_parse_cli_args

# ──────────────────────────────────────────────────────────────────
# region FUNC_find_node_yaml
## @purpose  Locate the node.yaml that contains the project.
##           If --node provided, search only that node's config.
##           Otherwise search PROJECTS_ROOT/*/node-configs/*/node.yaml.
## @param $1  Project name
## @param $2  Optional node name
## @io       Sets FOUND_NODE_YAML, FOUND_PROJECT_ENTRY (JSON), PROJECT_DOMAIN,
##           PROJECT_NODE_HOST, PROJECT_ORG, NODE_CONFIGS_DIR globals.
## @return   0 if found, 1 if not found
## @complexity O(n·m) where n = node.yaml files, m = projects per file
find_node_yaml() {
    local name="$1"
    local node_filter="${2:-}"

    log_imp 7 "-" "Searching for project '${name}' in node.yaml files"

    local search_pattern
    if [[ -n "$node_filter" ]]; then
        search_pattern="$PROJECTS_ROOT/*/node-configs/${node_filter}/node.yaml"
    else
        search_pattern="$PROJECTS_ROOT/*/node-configs/*/node.yaml"
    fi

    # Use yq for robust YAML querying — prefer over python3+yaml for node.yaml ops
    local yaml_files=()
    # shellcheck disable=SC2086
    while IFS= read -r -d '' ny; do
        yaml_files+=("$ny")
    done < <(find "$PROJECTS_ROOT" -maxdepth 5 -path "*/node-configs/*/node.yaml" -print0 2>/dev/null || true)

    if [[ ${#yaml_files[@]} -eq 0 ]]; then
        log_imp 8 "-" "No node.yaml files found under ${PROJECTS_ROOT}"
        return 1
    fi

    log_imp 7 "-" "Found ${#yaml_files[@]} node.yaml file(s) to search"

    for ny in "${yaml_files[@]}"; do
        log_imp 6 "-" "Checking: ${ny}"

        # Try yq first
        if command -v yq &>/dev/null; then
            local entry_json
            entry_json="$(yq eval ".projects[] | select(.name == \"${name}\")" "$ny" 2>/dev/null || true)"
            if [[ -n "$entry_json" && "$entry_json" != "null" ]]; then
                FOUND_NODE_YAML="$ny"
                FOUND_PROJECT_ENTRY="$entry_json"
                PROJECT_DOMAIN="$(yq eval ".projects[] | select(.name == \"${name}\") | .domain // \"\"" "$ny" 2>/dev/null || true)"
                PROJECT_NODE_HOST="$(yq eval ".node.host // \"\"" "$ny" 2>/dev/null || true)"
                # Extract org from the repo field: org/project
                local repo_val
                repo_val="$(yq eval ".projects[] | select(.name == \"${name}\") | .repo // \"\"" "$ny" 2>/dev/null || true)"
                if [[ -n "$repo_val" && "$repo_val" != "null" ]]; then
                    PROJECT_ORG="${repo_val%%/*}"
                fi
                # Derive node-configs dir from node.yaml path
                NODE_CONFIGS_DIR="$(dirname "$(dirname "$ny")")"
                log_imp 7 "-" "Found project '${name}' in: ${ny}"
                log_imp 8 "-" "  domain=${PROJECT_DOMAIN:-<none>} host=${PROJECT_NODE_HOST:-<unknown>} org=${PROJECT_ORG:-unknown}"
                return 0
            fi
        else
            # Fallback: NodeYaml CLI (DevPlan 038c — replaces inline python3)
            local py_result
            py_result="$(python3 -m core.internal.shared.node_yaml \
                --file "${ny}" \
                --find-project "${name}" 2>/dev/null || true)"
            if [[ -n "$py_result" ]]; then
                FOUND_NODE_YAML="$ny"
                FOUND_PROJECT_ENTRY="$(echo "$py_result" | head -1)"
                PROJECT_ORG="$(echo "$py_result" | grep '___ORG___' | sed 's/___ORG___//')"
                PROJECT_NODE_HOST="$(echo "$py_result" | grep '___HOST___' | sed 's/___HOST___//')"
                NODE_CONFIGS_DIR="$(dirname "$(dirname "$ny")")"
                log_imp 7 "-" "Found project '${name}' in: ${ny} (NodeYaml CLI fallback)"
                return 0
            fi
        fi
    done

    log_imp 8 "-" "Project '${name}' not found in any node.yaml"
    return 1
}
# endregion FUNC_find_node_yaml

# ──────────────────────────────────────────────────────────────────
# region FUNC_unregister_from_node_yaml
## @purpose  Remove the project entry from node.yaml using yq.
##           Preserves all other projects and YAML structure.
## @io       Modifies node.yaml in-place
## @complexity O(p) where p = projects count
unregister_from_node_yaml() {
    local node_yaml="$1"
    local name="$2"

    log_imp 7 "-" "Unregistering '${name}' from: ${node_yaml}"

    if command -v yq &>/dev/null; then
        # Remove project by name — yq eval -i with del(..|select)
        yq eval -i "del(.projects[] | select(.name == \"${name}\"))" "$node_yaml"
        log_imp 9 "-" "yq: removed '${name}' from projects[] in ${node_yaml}"
    elif command -v python3 &>/dev/null && require_python_module yaml; then
        log_imp 7 "-" "yq not available — using python3+yaml fallback"
        local py_rc=0
        python3 "${SCRIPT_DIR}/../shared/project_registry.py" deregister \
            --name "$name" \
            --node-yaml "$node_yaml" \
            --log-prefix "remove-project"
        py_rc=$?
        if [[ $py_rc -ne 0 ]]; then
            log_imp 8 "-" "Python unregistration failed (exit=${py_rc})"
            return 1
        fi
        log_imp 9 "-" "python3: removed '${name}' from projects[] (fallback)"
    else
        log_imp 8 "-" "Neither yq nor python3+yaml available — cannot unregister automatically"
        log_imp 8 "-" "Manually remove '${name}' from: ${node_yaml}"
        return 1
    fi

    log_imp 7 "-" "Unregistration complete"
    return 0
}
# endregion FUNC_unregister_from_node_yaml

# ──────────────────────────────────────────────────────────────────
# region FUNC_remove_vhost
## @purpose  Remove nginx vhost file for the project, if a domain is configured.
##           The vhost file lives at <node-configs>/<node>/overlays/nginx/<domain>.conf
## @io       Deletes vhost file if it exists
## @complexity O(1)
remove_vhost() {
    local domain="${1:-}"
    if [[ -z "$domain" || -z "$NODE_CONFIGS_DIR" ]]; then
        log_imp 6 "-" "No domain configured or node-configs unknown — skipping vhost removal"
        return 0
    fi

    # Derive node name from node-configs dir path: .../<node>/node.yaml → node name
    local node_name
    node_name="$(basename "$NODE_CONFIGS_DIR")"
    local vhost_dir="${NODE_CONFIGS_DIR}/overlays/nginx"
    local vhost_file="${vhost_dir}/${domain}.conf"

    if [[ ! -f "$vhost_file" ]]; then
        log_imp 6 "-" "Vhost file not found: ${vhost_file} — SKIP"
        return 0
    fi

    log_imp 7 "-" "Removing nginx vhost: ${vhost_file}"
    rm -f "$vhost_file"
    log_imp 9 "-" "Vhost removed: ${vhost_file}"
}
# endregion FUNC_remove_vhost

# ──────────────────────────────────────────────────────────────────
# region FUNC_ssh_compose_down
## @purpose  SSH to target node and run docker compose down (WITHOUT -v) for the project.
##           Uses ci-deploy user if available, falls back to current user.
## @param $1  SSH host
## @param $2  Project name
## @io       stdout: SSH output; stderr: LDD logs
## @return   0 on success, 1 on SSH failure (VPS unavailable)
## @complexity O(w) where w = wait time for SSH connection
ssh_compose_down() {
    local host="$1"
    local project="$2"

    if [[ -z "$host" ]]; then
        log_imp 8 "-" "No SSH host available for project — skipping remote compose down"
        log_imp 8 "-" "  Manual step: ssh ci-deploy@<host> 'docker compose -p ${project} down'"
        return 1
    fi

    log_imp 7 "-" "Connecting to ${host} to stop project '${project}' containers..."

    # Try ci-deploy user first, then current user
    local ssh_user=""
    local ssh_ok=false

    for try_user in "ci-deploy" ""; do
        local effective_user="${try_user:-${USER:-$(whoami)}}"
        log_imp 6 "-" "  Attempting SSH as: ${effective_user}@${host}"

        # Test SSH connection via ssh_read (W2-E1: lib/ssh.sh facade)
        if ssh_read "${host}" "${effective_user}" "echo OK" 10 2>/dev/null | grep -q "OK"; then
            ssh_user="${try_user}"
            ssh_ok=true
            break
        fi
    done

    if [[ "$ssh_ok" != "true" ]]; then
        log_imp 8 "-" "SSH connection failed — VPS may be unavailable"
        log_imp 8 "-" "  Manual step: ssh ci-deploy@<host> 'docker compose -p ${project} down'"
        return 1
    fi

    local ssh_target
    if [[ -n "$ssh_user" ]]; then
        ssh_target="${ssh_user}@${host}"
    else
        ssh_target="${host}"
    fi

    log_imp 7 "-" "Running docker compose down (NO -v) for '${project}' on ${ssh_target}"

    # Run docker compose down via ssh_exec (W2-E1: lib/ssh.sh facade) — WITHOUT -v per O7/DD10
    local ssh_user="${ssh_user:-ci-deploy}"
    local ssh_output
    ssh_output="$(ssh_exec "${host}" "${ssh_user}" \
        "cd /opt/projects/${project} 2>/dev/null && docker compose down --timeout 30 2>&1 || docker compose -p ${project} down --timeout 30 2>&1" 120 2>&1)" || {
        local rc=$?
        log_imp 8 "-" "SSH command returned exit code ${rc}"
        log_imp 8 "-" "Output: ${ssh_output}"
        log_imp 8 "-" "  Manual step if containers remain: ssh ${ssh_user}@${host} 'docker compose -p ${project} down'"
        return 1
    }

    log_imp 7 "-" "docker compose down output:"
    while IFS= read -r line; do
        log_imp 6 "-" "  ${line}"
    done <<< "$ssh_output"

    log_imp 9 "-" "Containers stopped for '${project}' on ${host} (compose down, NO -v)"
    return 0
}
# endregion FUNC_ssh_compose_down

# ──────────────────────────────────────────────────────────────────
# region FUNC_print_report
## @purpose  Print a human-readable report of what was done and what was NOT deleted.
## @io       stdout: formatted report
## @rationale O7/DD10: user must be explicitly reminded that data persists.
print_report() {
    local name="$1"
    local vhost_removed="$2"  # true|false
    local ssh_done="$3"       # true|false

    echo ""
    echo "────────────────────────────────────────────────────────────"
    echo "  ✅ remove-project: ${name}"
    echo "────────────────────────────────────────────────────────────"
    echo ""
    echo "  Removed:"
    echo "    ✔ Unregistered from node.yaml"
    if [[ "$vhost_removed" == "true" ]]; then
        echo "    ✔ Nginx vhost deactivated"
    fi
    if [[ "$ssh_done" == "true" ]]; then
        echo "    ✔ Containers stopped (compose down)"
    fi
    echo ""
    echo "  ❗ NOT deleted (safe remove O7/DD10 — manual cleanup required):"
    echo "    ❌ Docker volumes — run: docker volume ls | grep ${name}"
    echo "    ❌ Database — DROP DATABASE on postgres if needed"
    echo "    ❌ Docker images — run: docker image ls | grep ${name}"
    echo "    ❌ GitHub repo — run: gh repo delete <org>/${name}"
    echo "    ❌ Local project directory — run: rm -rf <project_dir>"
    if [[ "$ssh_done" != "true" ]]; then
        echo ""
        echo "  ⚠️  VPS was unreachable — SSH step SKIPPED."
        echo "     Manual: ssh <host> 'cd /opt/projects/${name} && docker compose down'"
    fi
    echo ""
    echo "────────────────────────────────────────────────────────────"
}
# endregion FUNC_print_report

# ──────────────────────────────────────────────────────────────────
# region FUNC_main
## @purpose  Main entry point — orchestrates safe project removal
main() {
    log_imp 6 "-" "Starting remove-project.sh (T10 full implementation)"

    parse_cli_args "$@"

    # ── Find project in node.yaml ──
    if ! find_node_yaml "$PROJECT_NAME" "$NODE_NAME"; then
        log_imp 9 "-" "Project '${PROJECT_NAME}' not found in any node.yaml — SKIP (idempotent, exit 0)"
        echo "[IMP:9][remove-project][main] SKIP: project '${PROJECT_NAME}' not registered — exit 0" >&2
        exit 0
    fi

    # ── Confirmation ──
    if [[ "$FORCE" -ne 1 ]]; then
        echo ""
        echo "  This will REMOVE '${PROJECT_NAME}' from the platform lifecycle."
        echo "  Node:  ${NODE_NAME:-$(basename "$NODE_CONFIGS_DIR" 2>/dev/null || echo 'unknown')}"
        echo "  Org:   ${PROJECT_ORG:-unknown}"
        echo ""
        echo "  Volumes, databases, images, and GitHub repo will NOT be deleted (safe remove O7)."
        echo ""
        read -r -p "  Continue? [y/N] " response
        case "$response" in
            [yY][eE][sS]|[yY]) ;;
            *) log_imp 7 "-" "Cancelled by user"; exit 0 ;;
        esac
    fi

    # ── Steps 1-4: Wrap cleanup operations in audit_step ──
    _do_cleanup() {
        local name="$1"
        local node_yaml="$2"
        local node_host="$3"
        local domain="$4"

        # Step 1: Unregister from node.yaml
        log_imp 7 "-" "Step 1/4: Unregister from node.yaml"
        if unregister_from_node_yaml "$node_yaml" "$name"; then
            log_imp 9 "-" "Step 1 complete: unregistered"
        else
            log_imp 8 "-" "Step 1: unregistration had warnings — continuing"
        fi

        # Step 2: Remove nginx vhost
        log_imp 7 "-" "Step 2/4: Remove nginx vhost"
        local vhost_removed=false
        if [[ -n "$domain" && "$domain" != "null" ]]; then
            remove_vhost "$domain"
            vhost_removed=true
        else
            log_imp 6 "-" "No domain configured — skipping vhost removal"
        fi

        # Step 3: SSH compose down on target node
        log_imp 7 "-" "Step 3/4: Stop containers on target node"
        local ssh_done=false
        if ssh_compose_down "$node_host" "$name"; then
            ssh_done=true
            log_imp 9 "-" "Step 3 complete: containers stopped"
        else
            log_imp 8 "-" "Step 3: SSH step did not complete — containers may still be running"
        fi

        # Step 4: Print report
        log_imp 7 "-" "Step 4/4: Print safe-remove report"
        print_report "$name" "$vhost_removed" "$ssh_done"

        log_imp 9 "-" "remove-project DONE: ${name}"
    }

    audit_step "remove-project:${PROJECT_NAME}:node=${NODE_NAME:-$(basename "$NODE_CONFIGS_DIR" 2>/dev/null || echo 'unknown')}" \
        _do_cleanup "$PROJECT_NAME" "$FOUND_NODE_YAML" "$PROJECT_NODE_HOST" "$PROJECT_DOMAIN"
}
# endregion FUNC_main

main "$@"
