#!/usr/bin/env bash
# shellcheck disable=SC2034
# GREP_SUMMARY: project-list lifecycle status node-yaml table offline ssh-status timeout
# STRUCTURE: ▶ init → parse_args → ◇ --status? → offline: read node.yaml → table output | live: SSH status → human-readable → ⊕ exit
# region MODULE_CONTRACT
## @purpose  List projects registered in node.yaml (offline) or query live status from
##           target node (SSH). Provides the OBSERVE phase of the project lifecycle.
## @scope    Called from scaffold.sh project-list / project-status.
## @io       stdout: formatted table (list) or human-readable status (status)
##           stderr: LDD logs via log_imp at IMP:7-10
## @invariants
##   - `list` subcommand works fully offline (reads local node.yaml only)
##   - `status` subcommand connects via SSH — fails with explicit error on timeout/connectivity
##   - Never uses --purge, never modifies state
##   - SSH timeout ≤10 seconds (prevents hanging on unavailable nodes)
## @rationale Completes the OBSERVE lifecycle phase — owner can see what's registered
##            without SSH. Production monitoring uses healthcheck.sh, not this.
## @links    CALLED_BY: scaffold.sh (project-list, project-status)
##           READS: node.yaml files under PROJECTS_ROOT
##           CALLS: SSH forced-command status verb (K1)
## @changes  2026-07-17 · T12 — full implementation
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")}"
PROJECTS_ROOT="${PROJECTS_ROOT:-$(dirname "$PLATFORM_ROOT")}"

__LOG_PREFIX="project-list"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

# ═══════════════════════════════════════════════════════════════════
# GLOBALS
# ═══════════════════════════════════════════════════════════════════
MODE="list"     # list | status
NODE_NAME=""
PROJECT_NAME=""
FORMAT="table"  # table | json

# ──────────────────────────────────────────────────────────────────
# region FUNC_usage
## @purpose  Print usage guide
## @io       stdout: usage text
usage() {
    cat <<'HELP'
USAGE: project-list.sh [OPTIONS]

List projects or query live status.

SUBCOMMANDS (auto-detected):
  --list              Show project table from local node.yaml (default)
  --status            Query live status from target node via SSH

OPTIONS:
  --node <node>       Node name (required for --status, optional for --list)
  --name <name>       Filter by project name
  --format <format>   Output format: table (default), json
  --help              Show this help

EXAMPLES:
  project-list.sh                          # list all projects
  project-list.sh --status --node my-node  # live status for all projects on node
  project-list.sh --status --name myapp    # live status for one project

NOTE:
  - --list works offline (reads local node.yaml files)
  - --status has a 10-second SSH timeout
  - --status without --node will search for the project's node
HELP
}
# endregion FUNC_usage

# ──────────────────────────────────────────────────────────────────
# region FUNC_parse_args
## @purpose  Parse CLI arguments
## @io       Sets MODE, NODE_NAME, PROJECT_NAME, FORMAT globals
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --list)   MODE="list" ;;
            --status) MODE="status" ;;
            --node)   shift; NODE_NAME="$1" ;;
            --name)   shift; PROJECT_NAME="$1" ;;
            --format) shift; FORMAT="$1" ;;
            --help|-h) usage; exit 0 ;;
            *) log_imp 9 "-" "Unknown arg: $1"; usage >&2; exit 1 ;;
        esac
        shift
    done

    if [[ "$FORMAT" != "table" && "$FORMAT" != "json" ]]; then
        log_imp 9 "-" "Invalid format: ${FORMAT}. Use 'table' or 'json'."
        exit 1
    fi

    log_imp 7 "-" "Args: mode=${MODE} node=${NODE_NAME:-<auto>} name=${PROJECT_NAME:-<all>} format=${FORMAT}"
}
# endregion FUNC_parse_args

# ──────────────────────────────────────────────────────────────────
# region FUNC_find_node_yaml_files
## @purpose  Find all node.yaml files under PROJECTS_ROOT/*/node-configs/*/
## @return   Prints one node.yaml path per line to stdout
## @complexity O(f) where f = number of files found
find_node_yaml_files() {
    local node_filter="${1:-}"

    if [[ -n "$node_filter" ]]; then
        find "$PROJECTS_ROOT" -maxdepth 5 \
            -path "*/node-configs/${node_filter}/node.yaml" \
            -type f -print0 2>/dev/null | xargs -0 -I{} echo "{}" 2>/dev/null || true
    else
        find "$PROJECTS_ROOT" -maxdepth 5 \
            -path "*/node-configs/*/node.yaml" \
            -type f -print0 2>/dev/null | xargs -0 -I{} echo "{}" 2>/dev/null || true
    fi
}
# endregion FUNC_find_node_yaml_files

# ──────────────────────────────────────────────────────────────────
# region FUNC_list_projects_offline
## @purpose  Read all node.yaml files and print a table of registered projects.
##           Works entirely offline — no network or SSH required.
## @io       stdout: formatted table or JSON
## @complexity O(p+f) where p = total projects, f = node.yaml files
list_projects_offline() {
    log_imp 7 "-" "Listing projects from local node.yaml files (offline)"

    local yaml_files
    yaml_files="$(find_node_yaml_files "$NODE_NAME")"

    if [[ -z "$yaml_files" ]]; then
        log_imp 8 "-" "No node.yaml files found under ${PROJECTS_ROOT}"
        echo "No projects found (no node.yaml files)"
        return 0
    fi

    local all_projects=()
    local header_printed=false

    if [[ "$FORMAT" == "json" ]]; then
        echo "["
    fi

    local first_entry=true
    local ny
    while IFS= read -r ny; do
        [[ -z "$ny" ]] && continue

        local node_name
        node_name="$(basename "$(dirname "$ny")")"  # .../node-configs/<node>/node.yaml → <node>
        local node_host=""
        node_host="$(yq eval ".node.host // \"\"" "$ny" 2>/dev/null || true)"

        if [[ "$FORMAT" == "json" ]]; then
            # JSON output mode
            local projects_json
            projects_json="$(yq eval ".projects[] | {name, node: \"${node_name}\", host: \"${node_host}\", domain: (.domain // \"\"), type: (.type // \"\"), repo: (.repo // \"\")}" "$ny" 2>/dev/null || true)"
            if [[ -n "$projects_json" && "$projects_json" != "null" ]]; then
                while IFS= read -r pj; do
                    # Filter by name if specified
                    if [[ -n "$PROJECT_NAME" ]]; then
                        local pn
                        pn="$(echo "$pj" | yq eval ".name // \"\"" - 2>/dev/null || true)"
                        [[ "$pn" != "$PROJECT_NAME" ]] && continue
                    fi
                    if [[ "$first_entry" == "true" ]]; then
                        first_entry=false
                    else
                        echo ","
                    fi
                    echo "  ${pj}"
                done <<< "$projects_json"
            fi
        else
            # Table output mode
            if [[ "$header_printed" == "false" ]]; then
                printf "%-25s %-20s %-30s %-15s %s\n" "NAME" "NODE" "DOMAIN" "TYPE" "REPO"
                printf "%-25s %-20s %-30s %-15s %s\n" "─" "─" "─" "─" "─"
                header_printed=true
            fi

            # Read projects via yq
            if command -v yq &>/dev/null; then
                local project_count
                project_count="$(yq eval ".projects | length" "$ny" 2>/dev/null || echo 0)"
                for ((i=0; i<project_count; i++)); do
                    local pname pdomain ptype prepo
                    pname="$(yq eval ".projects[$i].name // \"\"" "$ny" 2>/dev/null || true)"
                    pdomain="$(yq eval ".projects[$i].domain // \"\"" "$ny" 2>/dev/null || true)"
                    ptype="$(yq eval ".projects[$i].type // \"\"" "$ny" 2>/dev/null || true)"
                    prepo="$(yq eval ".projects[$i].repo // \"\"" "$ny" 2>/dev/null || true)"

                    # Filter by name
                    if [[ -n "$PROJECT_NAME" && "$pname" != "$PROJECT_NAME" ]]; then
                        continue
                    fi

                    if [[ -n "$pname" ]]; then
                        printf "%-25s %-20s %-30s %-15s %s\n" "$pname" "$node_name" "${pdomain:--}" "${ptype:--}" "$prepo"
                    fi
                done
            fi
        fi
    done <<< "$yaml_files"

    if [[ "$FORMAT" == "json" ]]; then
        echo ""
        echo "]"
    fi

    log_imp 9 "-" "Offline project listing complete"
}
# endregion FUNC_list_projects_offline

# ──────────────────────────────────────────────────────────────────
# region FUNC_find_project_node_yaml
## @purpose  Find the node.yaml file containing a specific project, and extract SSH host.
## @param $1  Project name
## @param $2  Optional node filter
## @io       Prints SSH host to stdout, returns 0 on success.
##           Sets PROJECT_NODE_YAML and PROJECT_SSH_HOST globals.
## @return   0 if found, 1 if not found
find_project_node_yaml() {
    local name="$1"
    local node_filter="${2:-}"

    local yaml_files
    yaml_files="$(find_node_yaml_files "$node_filter")"

    while IFS= read -r ny; do
        [[ -z "$ny" ]] && continue

        local found
        if command -v yq &>/dev/null; then
            found="$(yq eval ".projects[] | select(.name == \"${name}\") | .name" "$ny" 2>/dev/null || true)"
        else
            found="$(grep -E "^\s*-\s*name:\s*${name}\s*$" "$ny" 2>/dev/null || true)"
        fi

        if [[ -n "$found" && "$found" != "null" ]]; then
            PROJECT_NODE_YAML="$ny"
            if command -v yq &>/dev/null; then
                PROJECT_SSH_HOST="$(yq eval ".node.host // \"\"" "$ny" 2>/dev/null || true)"
            else
                PROJECT_SSH_HOST="$(grep -E '^\s*host:\s*' "$ny" 2>/dev/null | head -1 | awk '{print $2}' || true)"
            fi
            log_imp 7 "-" "Found project '${name}' in: ${ny} host=${PROJECT_SSH_HOST:-<unknown>}"
            return 0
        fi
    done <<< "$yaml_files"

    log_imp 8 "-" "Project '${name}' not found in any node.yaml"
    return 1
}
# endregion FUNC_find_project_node_yaml

# ──────────────────────────────────────────────────────────────────
# region FUNC_get_status_via_ssh
## @purpose  SSH to the target node and query project status.
##           Uses SSH timeout ≤10 seconds to prevent hanging.
## @param $1  SSH host
## @param $2  Project name
## @io       stdout: human-readable status
## @return   0 on success, 1 on SSH/connectivity failure
## @complexity O(t) where t = SSH round-trip time (≤10s timeout)
get_status_via_ssh() {
    local host="$1"
    local project="$2"

    if [[ -z "$host" ]]; then
        log_imp 10 "-" "FAIL-FAST: No SSH host available for project '${project}'"
        echo "ERROR: Cannot determine SSH host for project '${project}'"
        return 1
    fi

    log_imp 7 "-" "Connecting to ${host} for project '${project}' status..."

    local ssh_output=""
    local ssh_rc=0

    # Try ci-deploy user first, then current user
    local ssh_target=""
    for try_user in "ci-deploy" ""; do
        local try_target
        if [[ -n "$try_user" ]]; then
            try_target="${try_user}@${host}"
        else
            try_target="${host}"
        fi

        log_imp 6 "-" "  Trying SSH as: ${try_target}"
        if ssh_output="$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes \
            "$try_target" \
            "cd /opt/projects/${project} 2>/dev/null && docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}' 2>&1 || docker compose -p ${project} ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}' 2>&1" 2>&1)"; then
            ssh_target="$try_target"
            ssh_rc=0
            break
        else
            ssh_rc=$?
        fi
    done

    if [[ $ssh_rc -ne 0 ]]; then
        log_imp 10 "-" "SSH connection failed to ${host} for project '${project}'"
        echo "ERROR: Cannot connect to node ${host} (timeout/connectivity)"
        echo "  Check: node reachability, SSH keys, ci-deploy user"
        return 1
    fi

    echo ""
    echo "──────────────────────────────────────────────"
    echo "  Status: ${project} on ${host}"
    echo "──────────────────────────────────────────────"
    echo ""
    echo "$ssh_output"
    echo ""
    echo "──────────────────────────────────────────────"

    log_imp 9 "-" "Status retrieved for project '${project}' from ${host}"
}
# endregion FUNC_get_status_via_ssh

# ──────────────────────────────────────────────────────────────────
# region FUNC_list_status
## @purpose  Full --status workflow: find project node, SSH, print status.
## @io       stdout: human-readable status
list_status() {
    log_imp 7 "-" "Querying live status for project '${PROJECT_NAME}'"

    if [[ -z "$PROJECT_NAME" ]]; then
        log_imp 10 "-" "FAIL-FAST: --status requires --name <project>"
        echo "ERROR: --status requires --name <project>"
        echo "Usage: project-list.sh --status --name <project> [--node <node>]"
        exit 1
    fi

    # Find the project in node.yaml
    PROJECT_NODE_YAML=""
    PROJECT_SSH_HOST=""
    if ! find_project_node_yaml "$PROJECT_NAME" "$NODE_NAME"; then
        echo "ERROR: Project '${PROJECT_NAME}' not found in node.yaml"
        echo "  Register it first or check --name spelling"
        exit 1
    fi

    # Get SSH host from node.yaml, or use provided host
    if [[ -z "$PROJECT_SSH_HOST" ]]; then
        echo "ERROR: No SSH host found for project '${PROJECT_NAME}' in node.yaml"
        echo "  Check node.host in: ${PROJECT_NODE_YAML}"
        exit 1
    fi

    get_status_via_ssh "$PROJECT_SSH_HOST" "$PROJECT_NAME"
}
# endregion FUNC_list_status

# ──────────────────────────────────────────────────────────────────
# region FUNC_main
## @purpose  Dispatch to list or status based on MODE
main() {
    log_imp 6 "-" "Starting project-list.sh (T12 full implementation)"

    parse_args "$@"

    case "$MODE" in
        list)
            log_imp 7 "-" "Mode: list — offline project listing"
            list_projects_offline
            ;;
        status)
            log_imp 7 "-" "Mode: status — live SSH status query"
            list_status
            ;;
        *)
            log_imp 10 "-" "Unknown mode: ${MODE}"
            exit 1
            ;;
    esac

    log_imp 9 "-" "project-list DONE (mode=${MODE})"
}
# endregion FUNC_main

main "$@"
