# shellcheck shell=bash
# GREP_SUMMARY: node-resolver yaml library node-config hostname-resolution
# STRUCTURE: ┌node_name┐ → ○ resolve_node_yaml (3-candidate-path search) → ○ extract_node_host → ⊕ return config
# ⚠️ Errexit guard: warn if sourced without `set -e` (fail-fast on errors).
# Uses $- (portable across bash/zsh) instead of [ -o errexit ] (zsh-incompatible).
case $- in *e*) ;; *) echo "[WARN] node-resolver.sh sourced without set -e" >&2 ;; esac
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — Node YAML Resolver Library
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Shell facade for NodeYaml Python CLI. All YAML resolution
##           logic moved to core.internal.shared.node_yaml. resolve_node_yaml()
##           is now a thin wrapper around `python3 -m core.internal.shared.node_yaml --resolve`.
##           Replaces 3+ duplicated inline implementations and shell-based
##           3-candidate-path search with a single Python source of truth.
## @scope    — resolve_node_yaml() → thin facade (delegates to NodeYaml.resolve())
##           — extract_node_host() with YAML host extraction via NodeYaml CLI
##           (resolve_node_from_env УДАЛЁН, волна 118 B6 — 0 callers: NODE_HOST_MAP
##           резолвится через Python vps_readiness.py / yaml_query.py в CI)
##           — zero side-effects on source (pure function definitions only)
## @input    — __LOG_PREFIX (env var, set before source; default: "node-resolver")
##           — resolve_node_yaml: node_name, [platform_root], [projects_dir]
##           — extract_node_host: yaml_path
## @output   — Functions print data to stdout, LDD logs to stderr (via log_imp)
##           — exit code 0: success, exit code 1: not found / parse error
## @links    — USES: core/lib/logging.sh for log_imp() with auto block
##           — USED_BY: context-init.sh, deploy scripts, CI entrypoints (core-deploy.yml)
##           — REPLACES: inline resolve_node_yaml in apply.sh (deleted, former lines 292-330),
##             core-deploy.yml CI workflow (replaced platform-push.sh), context-init.sh (lines 524-571)
##           — SEE_ALSO: core/schemas/node.schema.json (node.yaml validation)
## @invariants — Functions MUST NOT set or modify global variables
##             — Functions MUST write data to stdout, logs to stderr
##             — Library MUST NOT execute any code on source beyond function
##               definitions and SETUP bootstrap (no side-effects)
##             — resolve_node_yaml MUST search exactly 3 paths in order:
##               platform-local → org repos → VPS fallback
##             — extract_node_host MUST use NodeYaml CLI `--get node.host`
##               (python3 -m core.internal.shared.node_yaml) for parsing — DevPlan 116 B6 T8.3
##             — A single quote in yaml_path would break python -c string
##               literal (accepted limitation — paths come from
##               resolve_node_yaml which only returns validated paths)
## @rationale Q: Why a shared library instead of inline function per script?
##            A: Three scripts (context-init.sh, former apply.sh, former platform-push.sh) had
##            subtle differences in error messaging and log blocks.
##            A shared library ensures consistent search order, identical
##            nullglob handling, uniform log format, and single maintenance
##            point for path additions or error messaging changes.
## @changes   LAST_CHANGE: 2026-07-09 · T2 — Updated references (apply.sh, platform-push.sh deleted)
##            Original (2026-07-07 · T1): extracted from apply.sh, platform-push.sh, context-init.sh patterns.
## @modulemap — resolve_node_yaml [W:80] 3-path search → stdout resolved path
##             — extract_node_host  [W:30] NodeYaml CLI host extraction → stdout host
## @usecases  — Developer: source node-resolver.sh; yaml_path="$(resolve_node_yaml
##               "prod-web")" || exit 1
##             — Deploy: host="$(extract_node_host "${yaml_path}")"
##             — CI: if ! yaml_path="$(resolve_node_yaml "${NODE}" "${PLATFORM_ROOT}"
##               "${PROJECTS_DIR}")"; then ...; fi
# endregion MODULE_CONTRACT
# GREP_SUMMARY: node, node.yaml, resolve, resolver, extract, host, yaml, config, node-configs, platform-root
# STRUCTURE: ▶ ┌node,platform_root,projects_dir┐ → ○ resolve_node_yaml → ◇ ┌3 paths┐ ⊕ ┌-f path?┐ → ⊕ echo path | ⎋ exit1
#            ▶ ┌yaml_path┐ → ○ extract_node_host → ⊕NodeYaml CLI --get node.host→ ◇ ┌host∋?┐ → ⊕ echo host | ⎋ empty | ⎋ exit1

# ═══════════════════════════════════════════════════════════════════
# DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════
# region SETUP

## @purpose  Resolve script directory, set default __LOG_PREFIX, and
##           source logging.sh for log_imp().
##
##           __LOG_PREFIX defaults to "node-resolver" if not already set
##           by the calling script. This allows callers to override the
##           prefix before sourcing (e.g. __LOG_PREFIX="deploy").
##
## @rationale Q: Why override __LOG_PREFIX?
##            A: Calling scripts (apply.sh, platform-push.sh) each have
##            their own identity. Setting __LOG_PREFIX before sourcing
##            lets log lines carry the caller's prefix instead of
##            "node-resolver". This is consistent with how logging.sh handles __LOG_PREFIX.
##
## @invariants — __LOG_PREFIX set at source-time, read at call-time
##             — _NODE_RESOLVER_LIB_DIR always resolves to core/lib/ regardless of
##               how the script is invoked
##             — sourcing logging.sh is idempotent (pure function defs)
# ⚠️ TRAP[BUG] · 2026-07-07 · P1 · SCRIPT_DIR collision with readonly from caller scripts
# · Root: library files are sourced by caller scripts that may declare SCRIPT_DIR
# ·   as readonly (via declare -r). Reassigning SCRIPT_DIR in the library fails.
# · Fix: use _NODE_RESOLVER_LIB_DIR to avoid readonly variable collision
# · Prevention: library files must use unique variable names; avoid SCRIPT_DIR
_NODE_RESOLVER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${__LOG_PREFIX:=node-resolver}"
# shellcheck source=core/lib/logging.sh
source "${_NODE_RESOLVER_LIB_DIR}/logging.sh"

# endregion SETUP

# ═══════════════════════════════════════════════════════════════════
# FUNCTION — resolve_node_yaml
# ═══════════════════════════════════════════════════════════════════
# region FUNC_resolve_node_yaml
## @purpose  Search for a node.yaml file across 3 candidate paths in
##           order: platform-local → org repos → VPS fallback. Prints
##           the first found path to stdout. Exits with code 1 if not
##           found after all paths are exhausted.
## @param $1  node_name — name of the node (e.g. "prod-web", "staging-db")
## @param $2  platform_root — base directory for platform configs
##            (default: ${PLATFORM_ROOT:-/opt/platform})
## @param $3  projects_dir — base directory for org repo projects
##            (default: $HOME/projects)
## @io       stdout: found absolute path (e.g.
##           "${PLATFORM_ROOT}/node-configs/prod-web/node.yaml")
##           stderr: LDD logs via log_imp at IMP:7-10
##           exit 0: found, exit 1: not found in any candidate path
## @complexity O(N) where N = number of dirs found at
##             projects_dir/*/node-configs (typically 0-5)
## @invariants — Searches exactly 3 candidate paths in specified order;
##               first match wins
##             — nullglob state is saved before glob expansion and
##               restored to original value after
##             — Never writes non-log output to stderr
##             — node_name is required; empty value returns 1
## @example   yaml_path="$(resolve_node_yaml "prod-web")" || exit 1
## @example   yaml_path="$(resolve_node_yaml "staging-db" "${PLATFORM_ROOT:-/opt/platform}" "${HOME}/projects")"
resolve_node_yaml() {
    local node_name="${1:-}"
    local platform_root="${2:-${PLATFORM_ROOT:-/opt/platform}}"
    local projects_dir="${3:-${HOME}/projects}"

    if [[ -z "${node_name}" ]]; then
        log_imp 10 "-" "Missing required argument: node_name"
        return 1
    fi

    log_imp 8 "-" "Resolving node.yaml for node=${node_name} via NodeYaml Python CLI"

    local result
    result="$(python3 -m core.internal.shared.node_yaml --resolve --resolve-node "$node_name" 2>/dev/null)" || {
        log_imp 10 "-" "node.yaml not found for node=${node_name}"
        log_imp 10 "-" "  Ensure node-configs/${node_name}/node.yaml exists"
        return 1
    }

    echo "$result"
    log_imp 9 "-" "Resolved node.yaml: ${result}"
}
# endregion FUNC_resolve_node_yaml

# ═══════════════════════════════════════════════════════════════════
# FUNCTION — extract_node_host
# ═══════════════════════════════════════════════════════════════════
# region FUNC_extract_node_host
## @purpose  Extract the host field from a node.yaml file using the NodeYaml CLI
##           (`python3 -m core.internal.shared.node_yaml --get node.host`).
##           Expected YAML structure: { node: { host: "1.2.3.4" } }. Prints the host value
##           to stdout. Returns empty string if host field is absent.
## @param $1  yaml_path — absolute path to an existing node.yaml file
## @io       stdout: host string (IP or domain), empty if field missing
##           stderr: LDD logs via log_imp at IMP:7-10
##           exit 0: success (host may be empty — not an error),
##           exit 1: file not found, unparseable YAML, or missing arg
## @complexity O(1) — single NodeYaml CLI --get call
## @invariants — YAML structure expected: node.host (nested dict)
##             — Empty or absent host produces empty stdout (not an error)
##             — File not found or YAML parse error → exit code 1 + IMP:10 log
##             — Uses NodeYaml CLI --get node.host (DevPlan 116 B6 T8.3) — python3 must have
##               core.internal.shared.node_yaml importable (repo root on PYTHONPATH)
##             — Result is captured via command substitution; trailing
##               newlines are stripped by bash
## @example   host="$(extract_node_host "${PLATFORM_ROOT:-/opt/platform}/node-configs/prod-web/node.yaml")"
## @example   if [[ -z "$(extract_node_host "${yaml_path}")" ]]; then
##               echo "WARN: no host defined for this node"
##           fi

# ═══════════════════════════════════════════════════════════════════
# FUNCTION — extract_node_host (fallback, file-based)
# ═══════════════════════════════════════════════════════════════════
extract_node_host() {
    local yaml_path="${1:-}"

    # ── Validate input ─────────────────────────────────────────────
    if [[ -z "${yaml_path}" ]]; then
        log_imp 10 "-" "Missing required argument: yaml_path"
        return 1
    fi

    if [[ ! -f "${yaml_path}" ]]; then
        log_imp 10 "-" "File not found: ${yaml_path}"
        return 1
    fi

    # [IMP:8][extract_node_host] Begin extraction
    log_imp 8 "-" "Extracting host from: ${yaml_path}"

    # ── Parse YAML via NodeYaml CLI ────────────────────────────────
    # Uses python3 -m core.internal.shared.node_yaml (DevPlan 038c)
    # Replaces inline python3 -c "import yaml..." block.
    local host
    host="$(python3 -m core.internal.shared.node_yaml \
        --file "${yaml_path}" \
        --get node.host \
        --default "" 2>/dev/null)" || {
        log_imp 10 "-" "Failed to parse YAML or extract host: ${yaml_path}"
        return 1
    }

    # ── Result ─────────────────────────────────────────────────────
    if [[ -n "${host}" ]]; then
        log_imp 9 "-" "Extracted host: ${host}"
    else
        log_imp 9 "-" "No host field in node.yaml: ${yaml_path} (empty output)"
    fi

    echo "${host}"
}
# endregion FUNC_extract_node_host
