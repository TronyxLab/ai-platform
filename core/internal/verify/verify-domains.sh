#!/usr/bin/env bash
# GREP_SUMMARY: verify-domains, internal, curl, http-check, node-yaml, domains, expose, post-deploy
# STRUCTURE: ▶ ┌NODE┐ → ○ resolve node.yaml (3-path search) → ○ python3 yaml parse ┌projects[expose=true]┐ → ○ foreach domain: curl --max-time 10 → ◇ HTTP 200? → ⊕ pass|warn → ∑ results → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Post-deploy HTTP(S) verification business logic — resolve node.yaml, parse expose:true domains, curl each
## @scope    Called from core/entrypoints/verify.sh (thin wrapper). Never called directly.
## @invariants
##   - Reads node.yaml via 3-path search: platform-local → org repos → VPS fallback
##   - Only checks projects with `expose: true` (boolean) in YAML
##   - curl timeout defaults to 10 seconds
##   - Exit code 0: ALL domains respond HTTP 200
##   - Exit code 1: at least one domain is unreachable or returns non-200
##   - HTTPS is always used (https://${domain})
## @rationale Extracted from verify.sh (TASK-6) to satisfy thin-wrapper contract (≤150 LOC).
##            Separates YAML parsing + curl logic from arg parsing/delegation.
## @changes  CREATED: 2026-07-18 · I-4 fix · Extracted from core/entrypoints/verify.sh
# endregion MODULE_CONTRACT
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════
# Source logging from lib (replaces local log_imp)
# ═══════════════════════════════════════════════════════════════════
__VERIFY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
__CORE_DIR="$(cd "${__VERIFY_DIR}/../.." && pwd)"
source "${__CORE_DIR}/lib/logging.sh"

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════
CURL_TIMEOUT="${CURL_TIMEOUT:-10}"
__LOG_PREFIX="${__LOG_PREFIX:-verify}"

# ═══════════════════════════════════════════════════════════════════
# FUNC: resolve_yaml — 3-path search for node.yaml
# ═══════════════════════════════════════════════════════════════════
# region FUNC_resolve_yaml
## @purpose  Locate node.yaml via 3-path search (platform-local → org repos → VPS fallback)
## @param $1  Node name
## @param $2  Platform root path (from PLATFORM_ROOT or /opt/platform)
## @stdout   Absolute path to node.yaml
## @exitcode 0 if found, 1 if not found
resolve_yaml() {
    local node_name="$1" platform_root="$2"
    local yaml_path=""

    # Path 1: platform-local
    local candidate="${platform_root}/node-configs/${node_name}/node.yaml"
    if [[ -f "${candidate}" ]]; then
        echo "${candidate}"
        log_imp 8 "resolve-yaml" "Found node.yaml (path 1): ${candidate}"
        return 0
    fi

    # Path 2: org repos (projects dir)
    local projects_dir="${HOME}/projects"
    local shopt_nullglob=false
    if [[ ! -o nullglob ]]; then
        shopt -s nullglob
        shopt_nullglob=true
    fi
    for dir in "${projects_dir}"/*/node-configs; do
        candidate="${dir}/${node_name}/node.yaml"
        if [[ -f "${candidate}" ]]; then
            yaml_path="${candidate}"
            log_imp 8 "resolve-yaml" "Found node.yaml (path 2): ${yaml_path}"
            break
        fi
    done
    if [[ "${shopt_nullglob}" == true ]]; then
        shopt -u nullglob
    fi

    if [[ -n "${yaml_path}" ]]; then
        echo "${yaml_path}"
        return 0
    fi

    # Path 3: VPS fallback
    candidate="/opt/node-configs/${node_name}/node.yaml"
    if [[ -f "${candidate}" ]]; then
        echo "${candidate}"
        log_imp 8 "resolve-yaml" "Found node.yaml (path 3): ${candidate}"
        return 0
    fi

    log_imp 10 "resolve-yaml" "node.yaml not found for node=${node_name}"
    log_imp 10 "resolve-yaml" "  Searched:"
    log_imp 10 "resolve-yaml" "    1. ${platform_root}/node-configs/${node_name}/node.yaml"
    log_imp 10 "resolve-yaml" "    2. ${HOME}/projects/*/node-configs/${node_name}/node.yaml"
    log_imp 10 "resolve-yaml" "    3. /opt/node-configs/${node_name}/node.yaml"
    return 1
}
# endregion FUNC_resolve_yaml

# ═══════════════════════════════════════════════════════════════════
# FUNC: get_expose_domains — parse node.yaml, return expose:true domains as JSON array
# ═══════════════════════════════════════════════════════════════════
# region FUNC_get_expose_domains
## @purpose  Parse node.yaml via python3+yaml, extract domains with expose:true
## @param $1  Path to node.yaml
## @stdout   JSON array of domain strings (e.g. ["a.example.com","b.example.com"])
## @exitcode 0 on success, 1 on parse failure
get_expose_domains() {
    local yaml_path="$1"
    python3 -c "
import yaml, sys, json
try:
    with open('${yaml_path}') as f:
        data = yaml.safe_load(f)
    projects = data.get('projects', []) if data else []
    domains = []
    for p in projects:
        if p.get('expose', False) is True:
            if p.get('domain'):
                domains.append(p['domain'])
    print(json.dumps(domains))
except Exception as e:
    print(json.dumps({'error': str(e)}), file=sys.stderr)
    sys.exit(1)
" 2>/dev/null
}
# endregion FUNC_get_expose_domains

# ═══════════════════════════════════════════════════════════════════
# FUNC: verify_domains — curl each domain, report pass/fail
# ═══════════════════════════════════════════════════════════════════
# region FUNC_verify_domains
## @purpose  Curl each domain in JSON array, print status, return true if all HTTP 200
## @param $1  JSON array of domain strings
## @stdout   Per-domain status lines
## @exitcode 0 if all HTTP 200, 1 otherwise
verify_domains() {
    local domains_json="$1"
    local all_ok=true

    # Convert JSON array to bash array
    local domains=()
    while IFS= read -r -d '' dom; do
        domains+=("$dom")
    done < <(python3 -c "
import json, sys
doms = json.loads('${domains_json}')
for d in doms:
    print(d, end='\\0')
")

    if [[ ${#domains[@]} -eq 0 ]]; then
        log_imp 9 "verify" "No expose:true domains found — nothing to verify"
        echo ""
        log_imp 9 "verify" "ALL DOMAINS PASS — 0 domain(s) to check"
        return 0
    fi

    log_imp 7 "verify" "Found ${#domains[@]} expose:true domain(s) to check"
    for d in "${domains[@]}"; do
        log_imp 8 "verify" "  - ${d}"
    done

    for domain in "${domains[@]}"; do
        log_imp 7 "curl" "Checking https://${domain} (timeout=${CURL_TIMEOUT}s)"

        local http_code=""
        set +e
        http_code="$(curl -sS -o /dev/null -w '%{http_code}' \
            --max-time "${CURL_TIMEOUT}" \
            "https://${domain}" 2>/dev/null)"
        local curl_exit=$?
        set -e

        if [[ $curl_exit -ne 0 ]]; then
            echo "  ${domain} -> CONNECTION FAILED (curl exit ${curl_exit})"
            log_imp 8 "curl" "Connection failed for ${domain}: curl exit ${curl_exit}"
            all_ok=false
        elif [[ "${http_code}" == "200" ]]; then
            echo "  ${domain} -> HTTP ${http_code} ✓"
            log_imp 7 "curl" "${domain} OK (HTTP ${http_code})"
        else
            echo "  ${domain} -> HTTP ${http_code} ⚠️  WARN: expected 200"
            log_imp 8 "curl" "${domain} returned HTTP ${http_code} (expected 200)"
            all_ok=false
        fi
    done

    echo ""
    if [[ "${all_ok}" == true ]]; then
        log_imp 9 "verify" "ALL DOMAINS PASS — HTTP 200 for all ${#domains[@]} domain(s)"
    else
        log_imp 9 "verify" "SOME DOMAINS FAILED — review output above"
    fi

    # ── Status-page health check (016) — CI post-deploy gate ──
    # Checks /health endpoint on the main domain with Basic Auth.
    # Verifies the status-page service is operational and all internal checks pass.
    local status_page_ok=true
    local main_domain="${PLATFORM_DOMAIN:-}"
    local master_email="${PLATFORM_MASTER_EMAIL:-}"
    local master_password="${PLATFORM_MASTER_PASSWORD:-}"

    if [[ -n "$main_domain" && -n "$master_email" && -n "$master_password" ]]; then
        log_imp 7 "status-page" "Checking status-page /health on https://${main_domain}/health"
        local health_http_code=""
        set +e
        health_http_code="$(curl -sS -o /dev/null -w '%{http_code}' \
            --max-time 30 \
            -u "${master_email}:${master_password}" \
            "https://${main_domain}/health" 2>/dev/null)"
        local health_exit=$?
        set -e

        if [[ $health_exit -ne 0 ]]; then
            echo "  status-page /health -> CONNECTION FAILED (curl exit ${health_exit})"
            log_imp 9 "status-page" "Status-page health check FAILED — connection error"
            status_page_ok=false
            all_ok=false
        elif [[ "${health_http_code}" == "200" ]]; then
            echo "  status-page /health -> HTTP 200 PASS ✓"
            log_imp 7 "status-page" "Status-page health check PASSED"
        else
            echo "  status-page /health -> HTTP ${health_http_code} FAIL ⚠️"
            log_imp 9 "status-page" "Status-page health check FAILED (HTTP ${health_http_code})"
            status_page_ok=false
            all_ok=false
        fi
    else
        log_imp 8 "status-page" "Skipping status-page health check — missing PLATFORM_DOMAIN or credentials"
    fi
    echo ""

    if [[ "${all_ok}" == true ]]; then
        log_imp 9 "verify" "ALL CHECKS PASS — domains + status-page health"
        return 0
    else
        log_imp 9 "verify" "SOME CHECKS FAILED — review output above"
        return 1
    fi
}
# endregion FUNC_verify_domains

# ═══════════════════════════════════════════════════════════════════
# FUNC: main — orchestrate resolve, parse, verify
# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Orchestrate full verification pipeline
## @param $1  Node name
## @param $2  Platform root (from PLATFORM_ROOT or /opt/platform)
## @exitcode 0 all pass, 1 any fail
main() {
    local node_name="${1:-}" platform_root="${2:-${PLATFORM_ROOT:-/opt/platform}}"

    log_imp 7 "main" "Starting post-deploy verification for node=${node_name}"

    # Step 1: Resolve node.yaml
    local yaml_path
    yaml_path="$(resolve_yaml "${node_name}" "${platform_root}")" || {
        exit 1
    }
    log_imp 7 "resolve-yaml" "Resolved node.yaml: ${yaml_path}"

    # Step 2: Parse expose:true domains
    log_imp 7 "parse-yaml" "Parsing projects with expose:true from ${yaml_path}"
    local domains_json
    domains_json="$(get_expose_domains "${yaml_path}")" || {
        log_imp 10 "parse-yaml" "Failed to parse YAML: ${yaml_path}"
        exit 1
    }

    # Step 3: Verify each domain
    verify_domains "${domains_json}"
    local verify_exit=$?
    exit ${verify_exit}
}
# endregion FUNC_main

main "$@"
