#!/usr/bin/env bash
# GREP_SUMMARY: validate yaml json-schema ajv python-jsonschema pre-commit exit1
# STRUCTURE: detect validator(ajv|python) → for each yaml → convert yaml→json → validate against schema → report
# region MODULE_CONTRACT
## @purpose  Validate YAML declaration files against JSON Schemas (node/module/ai-platform)
## @scope    Used as git pre-commit hook and standalone validator; supports ajv or python-jsonschema
## @invariants
##   - exit 0 only if ALL validated files pass ALL schemas
##   - exit 1 with human-readable error on first schema violation
##   - .yml extension for ai-platform.yaml rejected (must use .yaml per AR, 00 §5)
##   - Works without arguments (validates all known yaml files) or with specific paths
## @rationale Pre-commit validation prevents invalid declarations from entering git history (06 §9)
## ⚠️ TRAP[DECISION] · 2026-07-01 · — · Single manifest format (ai-platform.yaml only)
## ·   validate.sh ONLY validates ai-platform.yaml — legacy project.yaml/declaration.yaml
## ·   are NOT supported (AD-2). Earlier phases used project.yaml; migration completed.
## ·   If a file uses .yml instead of .yaml, it's rejected with an explicit error.
## ·   Rejected: support both project.yaml and ai-platform.yaml — would fragment the config
## ·   Rejected: auto-migrate old format — risk of silent data loss in edge cases
##
## ⚠️ TRAP[DECISION] · 2026-07-01 · — · Port conflict check via --check-ports
## ·   validate.sh has a --check-ports mode that scans all ai-platform.yaml files in
## ·   PROJECTS_BASE for host_port uniqueness. deploy blocked if conflict found.
## ·   Rationale: separate validation step allows CI/CD to check before deploy.
## ·   platform-deploy.sh also checks individually (defense-in-depth).
## ·   Rejected: rely only on per-deploy check — CI can fail faster (before pull)
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMAS_DIR="${SCRIPT_DIR}/../../schemas"

ERRORS=0

# === LDD Logging ===
__LOG_PREFIX="validate"
source "${SCRIPT_DIR}/../../lib/logging.sh"
source "${SCRIPT_DIR}/../../lib/python_deps.sh"

# Local adapters for two-arg (block, message) log calls.
# Library's log_* use one-arg (message) with auto-block — incompatible with existing calls.
vlog_info()  { log_imp 6 "$1" "$2"; }
vlog_ok()    { log_imp 7 "$1" "OK: $2"; }
vlog_fail()  { log_imp 9 "$1" "FAIL: $2"; ERRORS=$(( ERRORS + 1 )); }

# region DETECT_VALIDATOR
# Returns "ajv", "python", or exits with error
detect_validator() {
    if command -v ajv &>/dev/null; then
        echo "ajv"
    elif command -v python3 &>/dev/null && require_python_module jsonschema; then
        echo "python"
    else
        echo "[IMP:10][validate][detect] ERROR: No validator found." \
             "Install: npm install -g ajv-cli ajv-formats  OR  pip3 install jsonschema pyyaml" >&2
        exit 1
    fi
}
# endregion DETECT_VALIDATOR

# region VALIDATE_WITH_AJV
validate_with_ajv() {
    local yaml_file="$1"
    local schema_file="$2"

    # Convert YAML to JSON for ajv
    local tmp_json
    tmp_json="$(mktemp /tmp/platform-validate-XXXXXX.json)"
    trap 'rm -f "${tmp_json}" 2>/dev/null || true' RETURN

    if ! python3 -m core.internal.shared.node_yaml \
        --file "$yaml_file" \
        --json-output > "$tmp_json" 2>/dev/null; then
        vlog_fail "ajv" "Failed to parse YAML: ${yaml_file}"
        return 1
    fi

    local output
    if ! output="$(ajv validate -s "$schema_file" -d "$tmp_json" --errors=text --all-errors 2>&1)"; then
        vlog_fail "ajv" "${yaml_file}: ${output}"
        return 1
    fi

    vlog_ok "ajv" "${yaml_file}"
}
# endregion VALIDATE_WITH_AJV

# region VALIDATE_WITH_PYTHON
validate_with_python() {
    local yaml_file="$1"
    local schema_file="$2"

    local output
    if ! output="$(python3 - "$yaml_file" "$schema_file" <<'PYEOF' 2>&1
import sys
import json
import yaml
import jsonschema

yaml_file = sys.argv[1]
schema_file = sys.argv[2]

with open(yaml_file) as f:
    instance = yaml.safe_load(f)

with open(schema_file) as f:
    schema = json.load(f)

validator = jsonschema.Draft7Validator(schema)
errors = list(validator.iter_errors(instance))

if errors:
    for e in errors:
        path = " > ".join(str(p) for p in e.absolute_path) if e.absolute_path else "(root)"
        print(f"  Error at '{path}': {e.message}", file=sys.stderr)
    sys.exit(1)

sys.exit(0)
PYEOF
)"; then
        vlog_fail "python" "${yaml_file}:
${output}"
        return 1
    fi

    vlog_ok "python" "${yaml_file}"
}
# endregion VALIDATE_WITH_PYTHON

# region CHECK_PROJECT_EXTENSION
check_project_extension() {
    local file="$1"
    local basename
    basename="$(basename "$file")"
    # Only reject .yml for ai-platform.yaml, not other .yml (e.g. docker-compose.yml)
    if [[ "$basename" == "ai-platform.yml" ]]; then
        # [IMP:9][validate][extension] ai-platform.yaml MUST use .yaml, NOT .yml (00 §5)
        vlog_fail "extension" "REJECT: '${file}' uses .yml extension — platform requires .yaml for ai-platform declarations"
        return 1
    fi
    return 0
}
# endregion CHECK_PROJECT_EXTENSION

# region VALIDATE_FILE
validate_file() {
    local yaml_file="$1"
    local schema_file="$2"
    local validator="$3"

    if [[ ! -f "$yaml_file" ]]; then
        vlog_fail "file" "File not found: ${yaml_file}"
        return 1
    fi
    if [[ ! -f "$schema_file" ]]; then
        vlog_fail "schema" "Schema not found: ${schema_file}"
        return 1
    fi

    vlog_info "validate" "Validating: ${yaml_file} against $(basename "${schema_file}")"

    case "$validator" in
        ajv)    validate_with_ajv "$yaml_file" "$schema_file" ;;
        python) validate_with_python "$yaml_file" "$schema_file" ;;
    esac
}
# endregion VALIDATE_FILE

# region CHECK_FQDN_CONFLICT
## @brief  Check FQDN uniqueness across all ai-platform.yaml files on the node (06 §5.4 E1)
## @param  $1  project directory to check
## @return 0 if domain is unique, 1 if conflict found
## @invariants
##   - First project to claim a FQDN owns it
##   - Second project claiming same FQDN → exit 1 with E1 error
##   - Projects without domain declaration are skipped
##   - Only ai-platform.yaml supported (legacy declaration files removed per AD-2)
check_fqdn_conflict() {
    local project_dir="$1"
    local project_yaml=""

    if [[ -f "${project_dir}/ai-platform.yaml" ]]; then
        project_yaml="${project_dir}/ai-platform.yaml"
    else
        vlog_info "fqdn" "No ai-platform.yaml in ${project_dir} — skipping FQDN check"
        return 0
    fi

    # Extract own domain from needs.domain
    local own_domain
    own_domain="$(grep -m1 '^\s*domain:' "$project_yaml" 2>/dev/null | awk '{print $2}' || true)"

    # ⚠️ TRAP[BUG] FQDN false-positive: needs.domain: false matches grep as a real domain
    # grep '^\s*domain:' also catches "domain: false" / "domain: none" / "domain: null" etc.
    # Without this guard, YAML keys like "needs.domain: false" → own_domain="false" → false-positive E1.
    # Skip non-real domain values: false, none, no, null, empty
    if [[ -z "$own_domain" || "$own_domain" == "false" || "$own_domain" == "none" || "$own_domain" == "no" || "$own_domain" == "null" ]]; then
        vlog_info "fqdn" "No domain declared in $(basename "${project_yaml}") — skipping FQDN check"
        return 0
    fi

    vlog_info "fqdn" "Checking FQDN uniqueness: '${own_domain}' claimed by $(basename "${project_dir}")"

    # Scan all ai-platform.yaml files in PROJECTS_BASE (if set) or nearby projects
    local projects_base="${PROJECTS_BASE:-}"
    if [[ -z "$projects_base" ]]; then
        # Fallback: search relative to script dir
        projects_base="$(cd "${SCRIPT_DIR}/../../projects" 2>/dev/null && pwd || true)"
    fi

    if [[ ! -d "$projects_base" ]]; then
        vlog_info "fqdn" "PROJECTS_BASE (${projects_base}) not available — skipping cross-project FQDN check"
        return 0
    fi

    local conflict_found=0
    local other_project_name=""

    while IFS= read -r -d '' other_yaml; do
        # Skip own project file
        local other_dir
        other_dir="$(dirname "$other_yaml")"
        if [[ "$other_dir" == "$project_dir" ]]; then
            continue
        fi

        local other_domain
        other_domain="$(grep -m1 '^\s*domain:' "$other_yaml" 2>/dev/null | awk '{print $2}' || true)"

        if [[ "$other_domain" == "$own_domain" ]]; then
            other_project_name="$(basename "$other_dir")"
            vlog_fail "fqdn" "E1: FQDN '${own_domain}' already claimed by '${other_project_name}' — deploy blocked (06 §5.4)"
            conflict_found=1
        fi
    done < <(find "$projects_base" -maxdepth 2 -name "ai-platform.yaml" -print0 2>/dev/null || true)

    if [[ "$conflict_found" -eq 1 ]]; then
        vlog_fail "fqdn" "FQDN conflict detected for '${own_domain}' — exiting"
        return 1
    fi

    vlog_ok "fqdn" "FQDN '${own_domain}' is unique across projects on this node"
    return 0
}
# endregion CHECK_FQDN_CONFLICT


# region CHECK_PORTS
## @brief  Check host_port uniqueness across all ai-platform.yaml files on the node
## @param  $1  projects base directory
## @return 0 if all ports unique, 1 if conflict
## @invariants
##   - Only checks host_port in monitoring section
##   - Projects without host_port are skipped
check_port_conflict() {
    local projects_base="$1"

    if [[ ! -d "$projects_base" ]]; then
        vlog_info "ports" "PROJECTS_BASE (${projects_base}) not available — skipping port check"
        return 0
    fi

    vlog_info "ports" "Checking port uniqueness across ${projects_base}..."

    local -A port_map=()
    local conflict_found=0

    while IFS= read -r -d '' yaml_file; do
        local project_name
        project_name="$(basename "$(dirname "$yaml_file")")"

        local host_port
        host_port="$(python3 -m core.internal.shared.node_yaml \
            --file "${yaml_file}" \
            --get monitoring.host_port \
            --default "0" 2>/dev/null || echo "0")"

        if [[ "$host_port" -eq 0 ]]; then
            continue
        fi

        if [[ -n "${port_map[$host_port]:-}" ]]; then
            vlog_fail "ports" "Port conflict: ${host_port} claimed by '${project_name}' and '${port_map[$host_port]}'"
            conflict_found=1
        else
            port_map[$host_port]="$project_name"
            vlog_info "ports" "  Port ${host_port} → ${project_name}"
        fi
    done < <(find "$projects_base" -maxdepth 3 -name "ai-platform.yaml" -print0 2>/dev/null || true)

    if [[ "$conflict_found" -eq 1 ]]; then
        return 1
    fi

    vlog_ok "ports" "All host ports are unique across projects"
    return 0
}
# endregion CHECK_PORTS

# region MAIN
main() {
    # Handle special flags
    if [[ "${1:-}" == "--check-fqdn" ]]; then
        if [[ -z "${2:-}" ]]; then
            echo "[IMP:10][validate][fqdn] ERROR: --check-fqdn requires a project directory argument" >&2
            exit 1
        fi
        check_fqdn_conflict "$2"
        exit $?
    fi

    if [[ "${1:-}" == "--check-ports" ]]; then
        local base="${2:-}"
        if [[ -z "$base" ]]; then
            # Default: use PROJECTS_BASE or search relative to script
            base="${PROJECTS_BASE:-}"
            if [[ -z "$base" ]]; then
                base="$(cd "${SCRIPT_DIR}/../../projects" 2>/dev/null && pwd || true)"
            fi
        fi
        check_port_conflict "$base"
        exit $?
    fi

    vlog_info "start" "Detecting schema validator"
    local validator
    validator="$(detect_validator)"
    vlog_info "start" "Using validator: ${validator}"

    # Files to validate: from args or auto-discover
    local -a targets=("$@")

    if [[ ${#targets[@]} -eq 0 ]]; then
        # Auto-discover: find all yaml files in core tree
        local root_dir="${SCRIPT_DIR}/.."
        while IFS= read -r -d '' f; do
            targets+=("$f")
        done < <(find "$root_dir" -name "*.yaml" -o -name "*.yml" 2>/dev/null | sort -z || true)
    fi

    if [[ ${#targets[@]} -eq 0 ]]; then
        vlog_info "main" "No YAML files found to validate"
        exit 0
    fi

    for file in "${targets[@]}"; do
        # Skip flag arguments (e.g. --lint) that are not file paths
        [[ "$file" == --* ]] && continue
        local basename schema_file
        basename="$(basename "$file")"

        case "$basename" in
            node.yaml | node.yml)
                validate_file "$file" "${SCHEMAS_DIR}/node.schema.json" "$validator"
                ;;
            module.yaml | module.yml)
                validate_file "$file" "${SCHEMAS_DIR}/module.schema.json" "$validator"
                ;;
            ai-platform.yaml | ai-platform.yml)
                check_project_extension "$file"
                vlog_info "migration" "INFO: '${file}' — единый формат манифеста (AD-2)"
                validate_file "$file" "${SCHEMAS_DIR}/ai-platform.schema.json" "$validator"
                ;;
            *)
                vlog_info "skip" "Skipping non-declaration file: ${file}"
                ;;
        esac
    done

    if [[ "$ERRORS" -gt 0 ]]; then
        echo "[IMP:9][validate][result] FAIL: ${ERRORS} validation error(s) found" >&2
        exit 1
    fi

    echo "[IMP:8][validate][result] OK: All files valid" >&2
    exit 0
}
# endregion MAIN

main "$@"
