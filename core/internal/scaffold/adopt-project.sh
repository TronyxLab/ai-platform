#!/usr/bin/env bash
# shellcheck disable=SC2034
# GREP_SUMMARY: adopt-project lifecycle migration existing-project env-sync makefile agents-doc domain register vhost
# STRUCTURE: ▶ init → parse_args → detect project_type → [gen minimal ai-platform.yaml if missing] → simplify_deploy_yml → delete_platform_deploy_yml → gen_env_platform → [gen Makefile/AGENTS.md if missing] → register_in_node_yaml → configure_vhost → print_diff_report
# region MODULE_CONTRACT
## @purpose  Adopt an existing project into the ai-platform lifecycle: generate .env.platform,
##           add Makefile/AGENTS.md if missing, register in node.yaml, configure vhost.
##           Preserves all existing project files (src/, Dockerfile, etc.).
## @scope    Called from scaffold.sh adopt-project.
## @io       stdout: diff report of what was changed
##           stderr: LDD logs via log_imp at IMP:7-10
## @invariants
##   - NEVER modifies src/, Dockerfile, docker-compose.yml (application code)
##   - .env.platform regenerated; existing Makefile/AGENTS.md preserved (without --force)
##   - Supports personal domains (O11) — separate cert path
##   - Idempotent: second call with same project → no-op (exit 0) except .env.platform regeneration
##   - deploy.yml simplified to use reusable workflow (if exists)
##   - platform-deploy.yml deleted if exists
## @rationale Migration tool for existing projects (like dance-site with personal domain).
##            Without this, existing projects cannot adopt the connection-model without
##            manual intervention. --force flag for Makefile/AGENTS.md replaces existing.
## @links    CALLED_BY: scaffold.sh (adopt-project)
##           CALLS: gen-env-platform.sh, add-vhost.sh, yq for node.yaml manipulation
## @changes  2026-07-17 · T11 — full implementation
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")}"
PROJECTS_ROOT="${PROJECTS_ROOT:-$(dirname "$PLATFORM_ROOT")}"
TEMPLATES_DIR="${PLATFORM_ROOT}/templates"

__LOG_PREFIX="adopt-project"
source "${PLATFORM_ROOT}/core/lib/logging.sh"
source "${PLATFORM_ROOT}/core/lib/args.sh"

# ═══════════════════════════════════════════════════════════════════
# GLOBALS
# ═══════════════════════════════════════════════════════════════════
PROJECT_DIR=""
PROJECT_NAME=""
PROJECT_ORG=""
PROJECT_NODE=""
PROJECT_DOMAIN=""
FORCE=0

USAGE_SCRIPT="adopt-project.sh"
USAGE_DESC="Adopt an existing project into the ai-platform lifecycle."
USAGE_OPTIONS=(
    "--dir <dir>        Path to existing project directory"
    "--name <name>      Project name (auto-detected from directory basename)"
    "--org <org>        Organization name (from ai-platform.yaml or auto-detected)"
    "--node <node>      Target node name (from ai-platform.yaml or default)"
    "--domain <domain>  Custom domain"
    "--force            Regenerate Makefile/AGENTS.md even if they exist"
)

# 🧐 TRAP[DECISION] · 2026-07-21 · — · adopt-project.sh keeps local parse_args (env auto-detection)
# · Rejected: full parse_args adoption (auto-detection logic too complex)
# · Reason: minimal W1 scope, complex arg defaults + path auto-detection
# · Rev: Wave 4 — full migration when parse_args supports defaults

# ──────────────────────────────────────────────────────────────────
# region FUNC_parse_args
## @purpose  Parse CLI arguments
## @io       Sets PROJECT_DIR, PROJECT_NAME, PROJECT_ORG, PROJECT_NODE,
##           PROJECT_DOMAIN, FORCE globals
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dir)    shift; PROJECT_DIR="$1" ;;
            --name)   shift; PROJECT_NAME="$1" ;;
            --org)    shift; PROJECT_ORG="$1" ;;
            --node)   shift; PROJECT_NODE="$1" ;;
            --domain) shift; PROJECT_DOMAIN="$1" ;;
            --force)  FORCE=1 ;;
            --help|-h) usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}"; exit 0 ;;
            *) log_imp 9 "-" "Unknown arg: $1"; usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" >&2; exit 1 ;;
        esac
        shift
    done

    if [[ -z "$PROJECT_DIR" ]]; then
        log_imp 10 "-" "FAIL-FAST: --dir is required"
        usage "$USAGE_SCRIPT" "${USAGE_DESC:-}" "${USAGE_OPTIONS[@]:-}" >&2
        exit 1
    fi

    if [[ ! -d "$PROJECT_DIR" ]]; then
        log_imp 10 "-" "FAIL-FAST: project directory not found: ${PROJECT_DIR}"
        exit 1
    fi

    # Auto-detect name from dir basename if not given
    if [[ -z "$PROJECT_NAME" ]]; then
        PROJECT_NAME="$(basename "$PROJECT_DIR")"
        log_imp 7 "-" "Auto-detected project name: ${PROJECT_NAME}"
    fi

    # Read existing ai-platform.yaml for defaults
    local yaml_file="${PROJECT_DIR}/ai-platform.yaml"
    if [[ -f "$yaml_file" ]]; then
        if [[ -z "$PROJECT_NODE" ]]; then
            PROJECT_NODE="$(grep -E '^\s*target_node:\s*' "$yaml_file" 2>/dev/null | head -1 | awk '{print $2}' || true)"
            [[ -n "$PROJECT_NODE" ]] && log_imp 6 "-" "Node from ai-platform.yaml: ${PROJECT_NODE}"
        fi
        if [[ -z "$PROJECT_DOMAIN" ]]; then
            local dm
            dm="$(grep -E '^\s*domain:\s*' "$yaml_file" 2>/dev/null | head -1 | awk '{sub(/^[[:space:]]*domain:[[:space:]]*/, ""); gsub(/["'"'"']/, ""); print $1}' || true)"
            if [[ -n "$dm" && "$dm" != "false" ]]; then
                PROJECT_DOMAIN="$dm"
                log_imp 6 "-" "Domain from ai-platform.yaml: ${PROJECT_DOMAIN}"
            fi
        fi
    fi

    # Derive org from directory path if not set by --org or PLATFORM_ORG
    # projects/<org>/<project>/ → org = basename(dirname(project_dir_abs))
    if [[ -z "${PROJECT_ORG:-}" ]]; then
        local _project_dir_abs
        _project_dir_abs="$(cd "$PROJECT_DIR" && pwd -P 2>/dev/null || echo "$PROJECT_DIR")"
        local _derived_org
        _derived_org="$(basename "$(dirname "$_project_dir_abs")")"
        if [[ -n "$_derived_org" ]]; then
            PROJECT_ORG="$_derived_org"
            log_imp 7 "-" "Derived org from path: ${PROJECT_ORG}"
        fi
    fi

    # Apply env defaults (org requires explicit setting, no silent default)
    if [[ -z "${PROJECT_ORG:-}" ]]; then
        PROJECT_ORG="${PLATFORM_ORG:-}"
    fi
    PROJECT_NODE="${PROJECT_NODE:-${PLATFORM_DEFAULT_NODE:-tronyx-vps}}"

    # ⚠️ TRAP[BUG] · 2026-07-17 · D3 — молчаливый дефолт "personal" + отсутствие casing-нормализации
    # породили конфиг-drift dance-site (см. .ai/plans/007-dance-site-launch/02-Debt.md D3)
    # Root: дефолт "personal" скрывал отсутствие --org → org резолвилась в неверное имя,
    #       а отсутствие lowercase-нормализации ghcr.io приводило к несовпадению registry paths
    # Fix: fail-fast exit 1 с подсказкой + lowercase для ghcr + exact-case для uses: + сверка с node.yaml
    # Prevention: org всегда явный — отказ вместо молчания
    if [[ -z "${PROJECT_ORG:-}" ]]; then
        log_imp 10 "-" "FAIL-FAST: PROJECT_ORG is not set. Use --org <github-org> or set PLATFORM_ORG env."
        usage >&2
        exit 1
    fi
    log_imp 7 "-" "Args: dir=${PROJECT_DIR} name=${PROJECT_NAME} org=${PROJECT_ORG} node=${PROJECT_NODE} domain=${PROJECT_DOMAIN:-<none>} force=${FORCE}"
}
# endregion FUNC_parse_args

# ──────────────────────────────────────────────────────────────────
# region FUNC_is_ignored_path
## @purpose  Check if a path should be excluded from modification checks
## @param $1  Relative path from project root
## @return   0 if ignored, 1 if should be checked
is_ignored_path() {
    local relpath="$1"
    case "$relpath" in
        .git/*|.gitignore|.env|.env.platform|_SETUP_CHECKLIST.md|README.md)
            return 0 ;;
        src/*|Dockerfile*|docker-compose.yml|compose.yaml|nginx/*)
            return 0 ;;
        node_modules/*|__pycache__/*|*.pyc|.deploy-snapshots/*)
            return 0 ;;
        *)
            return 1 ;;
    esac
}
# endregion FUNC_is_ignored_path

# ──────────────────────────────────────────────────────────────────
# region FUNC_generate_minimal_ai_platform_yaml
## @purpose  Generate a minimal ai-platform.yaml if not present.
##           Prompts user for values, uses sensible defaults.
## @io       Creates <project_dir>/ai-platform.yaml if missing
generate_minimal_ai_platform_yaml() {
    local yaml_file="${PROJECT_DIR}/ai-platform.yaml"

    if [[ -f "$yaml_file" ]]; then
        log_imp 6 "-" "ai-platform.yaml exists: ${yaml_file} — preserving"
        return 0
    fi

    log_imp 7 "-" "No ai-platform.yaml found — generating minimal"

    local type_guess="backend"
    if [[ -f "${PROJECT_DIR}/src/index.html" ]] || [[ -d "${PROJECT_DIR}/frontend" ]]; then
        type_guess="frontend"
    fi
    if [[ -d "${PROJECT_DIR}/frontend" && -d "${PROJECT_DIR}/backend" ]]; then
        type_guess="fullstack"
    fi

    log_imp 7 "-" "  Guessed project type: ${type_guess}"

    cat > "$yaml_file" <<YAML
# =============================================================================
# ai-platform.yaml — единый манифест проекта AI Platform
# =============================================================================
# GENERATED by adopt-project.sh — PLEASE REVIEW
# Project: ${PROJECT_NAME}
# Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
# =============================================================================

name: ${PROJECT_NAME}
type: ${type_guess}
target_node: ${PROJECT_NODE}

needs:
  domain: ${PROJECT_DOMAIN:-false}
  expose: $( [[ -n "$PROJECT_DOMAIN" ]] && echo "true" || echo "false" )

monitoring:
  metrics: false
  logs_retention: 7d
  alerting: false
  dashboard: false
YAML

    log_imp 7 "-" "Minimal ai-platform.yaml generated: ${yaml_file}"
    log_imp 8 "-" "  ⚠️  REVIEW and adjust type/domain/monitoring values"
}
# endregion FUNC_generate_minimal_ai_platform_yaml

# ──────────────────────────────────────────────────────────────────
# region FUNC_simplify_deploy_yml
## @purpose  Simplify deploy.yml to use the reusable workflow pattern (K4).
##           Rewrites .github/workflows/deploy.yml to call
##           __ORG_NAME__/ai-platform/.github/workflows/deploy-project.yml@main.
##           Does NOT modify the file if it already uses the new pattern.
## @io       Rewrites <project_dir>/.github/workflows/deploy.yml
## @rationale Existing projects have old deploy.yml with resolve-node action and
##            inline build/deploy. New template uses reusable workflow only.
simplify_deploy_yml() {
    local deploy_yml="${PROJECT_DIR}/.github/workflows/deploy.yml"
    local deploy_yml_new="${PROJECT_DIR}/.github/workflows/platform-deploy.yml"

    if [[ ! -f "$deploy_yml" ]]; then
        log_imp 6 "-" "No deploy.yml found — nothing to simplify"
        return 0
    fi

    # Check if it already uses the new reusable workflow pattern
    if grep -q "uses:.*/ai-platform/.github/workflows/deploy-project.yml" "$deploy_yml" 2>/dev/null; then
        log_imp 6 "-" "deploy.yml already uses reusable workflow — preserving"
        return 0
    fi

    log_imp 7 "-" "Simplifying deploy.yml to use reusable workflow (K4)"

    if [[ "$FORCE" -ne 1 ]]; then
        read -r -p "  Rewrite deploy.yml to use reusable workflow? [y/N] " response
        case "$response" in
            [yY][eE][sS]|[yY]) ;;
            *) log_imp 7 "-" "  deploy.yml simplification skipped"; return 0 ;;
        esac
    fi

    # Backup original
    cp "$deploy_yml" "${deploy_yml}.bak" 2>/dev/null || true

    # Determine org for the uses: path
    local workflow_org="${PROJECT_ORG:-__ORG_NAME__}"

    cat > "$deploy_yml" <<YAMLDEPLOY
# GENERATED by adopt-project.sh — simplified to reusable workflow
# Original backed up at deploy.yml.bak

name: Deploy ${PROJECT_NAME}

on:
  push:
    branches: [main, staging]
  workflow_dispatch:

env:
  IMAGE_NAME: ghcr.io/${workflow_org,,}/${PROJECT_NAME}

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v7

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: \${{ github.actor }}
          password: \${{ secrets.GITHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v7
        with:
          context: .
          push: true
          tags: |
            \${{ env.IMAGE_NAME }}:\${{ github.sha }}
            \${{ env.IMAGE_NAME }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    needs: [build-and-push]
    if: github.ref_name == 'main'
    uses: ${workflow_org}/ai-platform/.github/workflows/deploy-project.yml@main
    with:
      project_name: ${PROJECT_NAME}
      image_tag: \${{ github.sha }}
    secrets: inherit

  deploy-staging:
    needs: [build-and-push]
    if: github.ref_name == 'staging'
    uses: ${workflow_org}/ai-platform/.github/workflows/deploy-project.yml@main
    with:
      project_name: ${PROJECT_NAME}
      image_tag: \${{ github.sha }}
    secrets: inherit
YAMLDEPLOY

    log_imp 9 "-" "deploy.yml simplified: ${deploy_yml}"
    log_imp 6 "-" "  Original backed up: ${deploy_yml}.bak"
}
# endregion FUNC_simplify_deploy_yml

# ──────────────────────────────────────────────────────────────────
# region FUNC_delete_platform_deploy_yml
## @purpose  Delete platform-deploy.yml if it exists (deprecated).
## @io       Removes <project_dir>/.github/workflows/platform-deploy.yml
## @rationale platform-deploy.yml is replaced by the reusable workflow pattern.
delete_platform_deploy_yml() {
    local pd_yml="${PROJECT_DIR}/.github/workflows/platform-deploy.yml"

    if [[ ! -f "$pd_yml" ]]; then
        log_imp 6 "-" "platform-deploy.yml not found — nothing to delete"
        return 0
    fi

    log_imp 7 "-" "Removing deprecated platform-deploy.yml: ${pd_yml}"
    rm -f "$pd_yml"
    log_imp 9 "-" "platform-deploy.yml deleted"
}
# endregion FUNC_delete_platform_deploy_yml

# ──────────────────────────────────────────────────────────────────
# region FUNC_validate_compose_networks
## @purpose  Validate project docker-compose declares proxy-net (external).
##           If the project has a domain, at least one service MUST be connected to
##           proxy-net with external:true. FAILs with clear instructions otherwise.
##           Does NOT mutate the compose file — validation only.
## @param $1  compose_path — path to project compose file (compose.yaml or docker-compose.yml)
## @return   0 if valid, 1 if validation fails (caller decides action)
## @complexity O(S × N) where S = services, N = networks per service
## @invariants — Validation only: no mutation of compose files
##             - Handles both compose.yaml and docker-compose.yml names
##             - Uses `docker compose config` if docker available (resolves anchors/aliases)
##             - Falls back to python3 yaml parser
##             - If neither docker nor python3+yaml available → WARN + return 0 (best-effort)
## @rationale Предотвращает регрессию M4: adopted project without proxy-net будет недоступен
##            через nginx. Валидация до регистрации — fail-fast, не «надеемся на CI».
validate_compose_networks() {
    local compose_path="$1"

    # If no domain configured, project doesn't need proxy-net (backend-only)
    if [[ -z "$PROJECT_DOMAIN" ]]; then
        log_imp 7 "validate_net" "No domain configured — skipping proxy-net validation"
        return 0
    fi

    log_imp 7 "validate_net" "Validating proxy-net in compose: ${compose_path}"

    local resolved_content=""
    local parse_ok=false

    # Method 1: docker compose config (resolves anchors, aliases, extends)
    if command -v docker &>/dev/null; then
        local docker_result
        # COMPOSE_PROFILES — required for ${VAR:?error} enforcement (DevPlan 033 W3-E3 Option A).
        export COMPOSE_PROFILES="${COMPOSE_PROFILES:-postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page}"
        docker_result="$(docker compose -f "$compose_path" config 2>/dev/null)" || true
        if [[ -n "$docker_result" ]]; then
            resolved_content="$docker_result"
            parse_ok=true
            log_imp 7 "validate_net" "Compose parsed via docker compose config"
        fi
    fi

    # Method 2: python3 yaml fallback
    if [[ "$parse_ok" == false ]] && command -v python3 &>/dev/null && python3 -c "import yaml" 2>/dev/null; then
        resolved_content="$(python3 -c "
import sys, yaml
with open('${compose_path}') as f:
    data = yaml.safe_load(f)
if not data or not isinstance(data, dict):
    sys.exit(1)
import json
print(json.dumps(data))
" 2>/dev/null)" || true
        if [[ -n "$resolved_content" ]]; then
            parse_ok=true
            log_imp 7 "validate_net" "Compose parsed via python3 yaml"
        fi
    fi

    if [[ "$parse_ok" == false ]]; then
        log_imp 8 "validate_net" "Cannot parse compose — neither docker nor python3+yaml available"
        log_imp 8 "validate_net" "  WARN: skipping proxy-net validation (best-effort)"
        return 0
    fi

    # Check for proxy-net in networks section
    local has_proxy_net=false
    local has_proxy_external=false
    local services_on_proxy_net=0

    # Use python3 for structured JSON/YAML analysis
    local analysis
    analysis="$(echo "$resolved_content" | python3 -c "
import sys, json

try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, Exception):
    print('PARSE_ERROR')
    sys.exit(0)

networks = data.get('networks', {})
if not isinstance(networks, dict):
    print('NO_NETWORKS')
    sys.exit(0)

proxy_net = networks.get('proxy-net', {})
if not isinstance(proxy_net, dict):
    print('PROXY_NOT_MAP')
    sys.exit(0)

external = proxy_net.get('external', False)
if isinstance(external, dict):
    # external: true (docker compose config resolves to bool)
    external = True
has_external = bool(external)

services = data.get('services', {})
if not isinstance(services, dict):
    services = {}

svc_count = 0
for svc_name, svc_config in services.items():
    if not isinstance(svc_config, dict):
        continue
    svc_networks = svc_config.get('networks', {})
    if isinstance(svc_networks, dict) and 'proxy-net' in svc_networks:
        svc_count += 1
    elif isinstance(svc_networks, list) and 'proxy-net' in svc_networks:
        svc_count += 1

print(f'HAS_PROXY_NET={has_external}')
print(f'SVC_COUNT={svc_count}')
" 2>/dev/null || echo "PARSE_ERROR")"

    if [[ "$analysis" == "PARSE_ERROR" ]]; then
        log_imp 8 "validate_net" "Could not analyze compose structure — skipping validation"
        return 0
    fi

    # Parse analysis results
    while IFS= read -r line; do
        if [[ "$line" == "HAS_PROXY_NET=True" ]]; then
            has_proxy_external=true
        elif [[ "$line" =~ ^SVC_COUNT=([0-9]+)$ ]]; then
            services_on_proxy_net="${BASH_REMATCH[1]}"
        fi
    done <<< "$analysis"

    if [[ "$has_proxy_external" == false ]]; then
        log_imp 10 "validate_net" "FAIL: compose does not declare networks.proxy-net with external:true"
        log_imp 10 "validate_net" "  Add to ${compose_path}:"
        log_imp 10 "validate_net" "    networks:"
        log_imp 10 "validate_net" "      proxy-net:"
        log_imp 10 "validate_net" "        name: proxy-net"
        log_imp 10 "validate_net" "        external: true"
        log_imp 10 "validate_net" "  And connect at least one service:"
        log_imp 10 "validate_net" "    services:"
        log_imp 10 "validate_net" "      <name>:"
        log_imp 10 "validate_net" "        networks:"
        log_imp 10 "validate_net" "          proxy-net:"
        log_imp 10 "validate_net" "            aliases:"
        log_imp 10 "validate_net" "              - <name>"
        return 1
    fi

    if [[ "$services_on_proxy_net" -eq 0 ]]; then
        log_imp 10 "validate_net" "FAIL: compose has proxy-net external but no service is connected to it"
        log_imp 10 "validate_net" "  Connect at least one service to proxy-net with an alias"
        return 1
    fi

    log_imp 9 "validate_net" "PASS: compose declares proxy-net (external) with ${services_on_proxy_net} service(s) connected"
    return 0
}
# endregion FUNC_validate_compose_networks

# ──────────────────────────────────────────────────────────────────
# region FUNC_gen_env_platform
## @purpose  Generate .env.platform in the project directory via gen-env-platform.sh.
## @io       Calls gen-env-platform.sh --name <NAME> --domain <DOMAIN> --output <.env.platform>
## @rationale Regenerate .env.platform for the existing project. Always regenerated.
gen_env_platform() {
    local gen_script="${SCRIPT_DIR}/gen-env-platform.sh"
    local env_file="${PROJECT_DIR}/.env.platform"

    if [[ ! -x "$gen_script" ]]; then
        log_imp 8 "-" "gen-env-platform.sh not found at ${gen_script} — skipping .env.platform generation"
        return 0
    fi

    log_imp 7 "-" "Generating .env.platform from platform-env.yaml"

    if "$gen_script" \
        --name "$PROJECT_NAME" \
        --domain "${PROJECT_DOMAIN:-}" \
        --output "$env_file"; then
        log_imp 9 "-" ".env.platform generated: ${env_file}"
    else
        log_imp 8 "-" "gen-env-platform.sh returned non-zero — .env.platform might be incomplete"
    fi
}
# endregion FUNC_gen_env_platform

# ──────────────────────────────────────────────────────────────────
# region FUNC_gen_project_makefile
## @purpose  Generate a minimal Makefile in the project directory (K3 contract).
##           Preserves existing Makefile unless --force is set.
## @io       Creates <project_dir>/Makefile if not present or --force
gen_project_makefile() {
    local makefile="${PROJECT_DIR}/Makefile"

    if [[ -f "$makefile" ]]; then
        if [[ "$FORCE" -ne 1 ]]; then
            log_imp 6 "-" "Makefile exists — SKIP (use --force to regenerate)"
            return 0
        fi
        log_imp 7 "-" "Force mode: overwriting existing Makefile"
    fi

    log_imp 7 "-" "Generating project Makefile: ${makefile}"

    cat > "$makefile" <<MAKEFILE
# GENERATED by adopt-project.sh — DO NOT EDIT manually
# Project: ${PROJECT_NAME}
# ai-platform project Makefile (K3 contract)

PLATFORM_DIR ?= \$(HOME)/projects/ai-platform

## sync-env: Re-generate .env.platform from platform-env.yaml
sync-env:
\t@echo "[IMP:7][project] Syncing .env.platform..."
\t@\$(MAKE) -C \$(PLATFORM_DIR) project-sync-env NAME=${PROJECT_NAME} DOMAIN=${PROJECT_DOMAIN:-}
\t@echo "[IMP:9][project] .env.platform sync complete"

## status: Show live project status from target node
status:
\t@echo "[IMP:7][project] Querying project status..."
\t@\$(MAKE) -C \$(PLATFORM_DIR) project-status NAME=${PROJECT_NAME}
\t@echo "[IMP:9][project] Status query complete"

## help: Show all available commands
help:
\t@grep -E '^## ' \$(MAKEFILE_LIST) | column -t -s ':'
MAKEFILE

    log_imp 7 "-" "Project Makefile generated: ${makefile}"
}
# endregion FUNC_gen_project_makefile

# ──────────────────────────────────────────────────────────────────
# region FUNC_gen_project_agents
## @purpose  Generate AGENTS.md in the project directory (DD13 contract, ≤60 lines).
##           Preserves existing AGENTS.md unless --force is set.
## @io       Creates <project_dir>/AGENTS.md if not present or --force
gen_project_agents() {
    local agents_file="${PROJECT_DIR}/AGENTS.md"

    if [[ -f "$agents_file" ]]; then
        if [[ "$FORCE" -ne 1 ]]; then
            log_imp 6 "-" "AGENTS.md exists — SKIP (use --force to regenerate)"
            return 0
        fi
        log_imp 7 "-" "Force mode: overwriting existing AGENTS.md"
    fi

    log_imp 7 "-" "Generating project AGENTS.md: ${agents_file}"

    cat > "$agents_file" <<AGENTS
# AGENTS.md — ${PROJECT_NAME} (ai-platform project)

## Platform provides
Template-based services: postgres, redis, litellm, langfuse, minio, clickhouse, nginx
See \`.env.platform\` for exact host/port/DSN/URL.

Domain: ${PROJECT_DOMAIN:-<not set>}
Node: ${PROJECT_NODE}
Target node: ${PROJECT_NODE}

## DO NOT
- Edit \`.env.platform\` manually (regenerate with \`make sync-env\`)
- Store secrets, tokens, or API keys in project files
- Delete this file or Makefile (project platform contract)

## Commands from this directory
- \`make sync-env\` — regenerate .env.platform from platform-env.yaml
- \`make status\` — show live container status from target node
- \`make help\` — show all available commands

## Configuration
\`\`\`
name: ${PROJECT_NAME}
org: ${PROJECT_ORG}
node: ${PROJECT_NODE}
AGENTS

    log_imp 7 "-" "Project AGENTS.md generated: ${agents_file}"
}
# endregion FUNC_gen_project_agents

# ──────────────────────────────────────────────────────────────────
# region FUNC_register_in_node_yaml
## @purpose  Register the project in node.yaml (idempotent — SKIP if already registered).
## @io       Modifies node.yaml via yq (append to projects[] if not present)
## @complexity O(1) — single yq append
register_in_node_yaml() {
    local node_yaml="${PROJECTS_ROOT}/${PROJECT_ORG}/node-configs/${PROJECT_NODE}/node.yaml"

    log_imp 7 "-" "Registering project in node.yaml: ${node_yaml}"

    if [[ ! -f "$node_yaml" ]]; then
        log_imp 8 "-" "node.yaml not found: ${node_yaml}"
        log_imp 8 "-" "  Create it or register manually:"
        log_imp 8 "-" "    yq eval -i '.projects += [{\"name\": \"${PROJECT_NAME}\", \"repo\": \"${PROJECT_ORG}/${PROJECT_NAME}\", \"type\": \"project\"}]' ${node_yaml}"
        return 0
    fi

    # Idempotent check
    if command -v yq &>/dev/null; then
        local existing
        existing=$(yq eval ".projects[] | select(.name == \"${PROJECT_NAME}\") | .name" "$node_yaml" 2>/dev/null || true)
        if [[ -n "$existing" && "$existing" != "null" ]]; then
            log_imp 9 "-" "Project already registered in node.yaml: ${PROJECT_NAME} — SKIP (idempotent)"
            return 0
        fi

        local entry="{\"name\": \"${PROJECT_NAME}\", \"repo\": \"${PROJECT_ORG}/${PROJECT_NAME}\", \"type\": \"adopted\""
        if [[ -n "$PROJECT_DOMAIN" ]]; then
            entry+=", \"domain\": \"${PROJECT_DOMAIN}\""
        fi
        entry+="}"

        yq eval -i ".projects += [${entry}]" "$node_yaml"
        log_imp 9 "-" "Project registered in node.yaml: ${PROJECT_NAME}"
    elif command -v python3 &>/dev/null && python3 -c "import yaml" 2>/dev/null; then
        log_imp 7 "-" "yq not available — using python3+yaml fallback"
        python3 "${SCRIPT_DIR}/../shared/project_registry.py" register \
            --name "$PROJECT_NAME" \
            --repo "${PROJECT_ORG}/${PROJECT_NAME}" \
            --type "adopted" \
            ${PROJECT_DOMAIN:+--domain "$PROJECT_DOMAIN"} \
            --node-yaml "$node_yaml" \
            --log-prefix "adopt" \
            || log_imp 8 "-" "Python registration failed — register manually"
    else
        log_imp 8 "-" "Neither yq nor python3+yaml available — cannot auto-register"
    fi
}
# endregion FUNC_register_in_node_yaml

# ──────────────────────────────────────────────────────────────────
# region FUNC_configure_vhost
## @purpose  Configure nginx vhost for the project if domain is set.
##           Calls add-vhost.sh for standard domains.
## @io       Delegates to add-vhost.sh if available
configure_vhost() {
    if [[ -z "$PROJECT_DOMAIN" ]]; then
        log_imp 6 "-" "No domain configured — skipping vhost"
        return 0
    fi

    local add_vhost_script="${SCRIPT_DIR}/add-vhost.sh"

    if [[ ! -x "$add_vhost_script" ]]; then
        log_imp 8 "-" "add-vhost.sh not found at ${add_vhost_script} — skipping vhost generation"
        log_imp 8 "-" "  Manual: cp <template>/nginx/default.conf to node-configs overlays"
        return 0
    fi

    # Ensure ai-platform.yaml has the domain set
    local yaml_file="${PROJECT_DIR}/ai-platform.yaml"
    if [[ -f "$yaml_file" ]]; then
        # Update needs.domain in yaml if present
        if grep -qE '^\s*domain:' "$yaml_file" 2>/dev/null; then
            sed -i.bak "s/^\(\s*domain:\s*\).*$/\1${PROJECT_DOMAIN}/" "$yaml_file" && rm -f "${yaml_file}.bak" 2>/dev/null || true
        fi
        # Ensure expose: true
        if grep -qE '^\s*expose:' "$yaml_file" 2>/dev/null; then
            sed -i.bak "s/^\(\s*expose:\s*\).*$/\1true/" "$yaml_file" && rm -f "${yaml_file}.bak" 2>/dev/null || true
        fi
    fi

    local node_configs_dir="${PROJECTS_ROOT}/${PROJECT_ORG}/node-configs"

    if [[ ! -d "$node_configs_dir" ]]; then
        log_imp 8 "-" "node-configs dir not found: ${node_configs_dir}"
        log_imp 8 "-" "  Manual: create vhost manually in overlays/nginx/"
        return 0
    fi

    log_imp 7 "-" "Configuring nginx vhost via add-vhost.sh for domain: ${PROJECT_DOMAIN}"

    "$add_vhost_script" \
        --project-dir "$PROJECT_DIR" \
        --node-configs-dir "$node_configs_dir" || {
        log_imp 8 "-" "add-vhost.sh returned non-zero — check vhost manually"
    }

    log_imp 9 "-" "Vhost configured for: ${PROJECT_DOMAIN}"
}
# endregion FUNC_configure_vhost

# ──────────────────────────────────────────────────────────────────
# region FUNC_print_diff_report
## @purpose  Print a human-readable diff report of what was changed
## @io       stdout: formatted report
print_diff_report() {
    local changes=("$@")

    echo ""
    echo "────────────────────────────────────────────────────────────"
    echo "  ✅ adopt-project: ${PROJECT_NAME}"
    echo "────────────────────────────────────────────────────────────"
    echo ""
    if [[ ${#changes[@]} -eq 0 ]]; then
        echo "  No changes made (everything up to date)"
    else
        echo "  Changes:"
        for c in "${changes[@]}"; do
            echo "    ${c}"
        done
    fi
    echo ""
    echo "  ❗ NOT modified (preserved): src/, Dockerfile, application code"
    echo ""
    echo "────────────────────────────────────────────────────────────"
    log_imp 9 "-" "adopt-project DONE: ${PROJECT_NAME}"
}
# endregion FUNC_print_diff_report

# ──────────────────────────────────────────────────────────────────
# region FUNC_validate_org_against_node_yaml
## @purpose  Validate PROJECT_ORG against node.yaml context (case-insensitive).
##           If node.yaml has context with different casing → WARN + adopt node.yaml variant.
##           If node.yaml has different org name → exit 1.
## @io       Updates PROJECT_ORG if casing differs; exits 1 on name mismatch
## @invariants PROJECT_ORG must be non-empty at entry
## @rationale Предотвращает конфиг-drift между adopt-project.sh и node.yaml (Debt D3).
##            Без этой проверки расхождение casing между --org и node.yaml.context
##            приводит к неработающим ghcr-путям при деплое.
validate_org_against_node_yaml() {
    local node_yaml="${PROJECTS_ROOT}/${PROJECT_ORG}/node-configs/${PROJECT_NODE}/node.yaml"

    if [[ ! -f "$node_yaml" ]]; then
        log_imp 6 "-" "node.yaml not found at ${node_yaml} — skipping context validation"
        return 0
    fi

    local node_context
    node_context="$(grep -E '^\s*context:\s*' "$node_yaml" 2>/dev/null | head -1 | awk '{print $2}' || true)"

    if [[ -z "$node_context" ]]; then
        log_imp 6 "-" "node.yaml has no context field — skipping validation"
        return 0
    fi

    # Case-insensitive comparison
    local org_lower="${PROJECT_ORG,,}"
    local node_ctx_lower="${node_context,,}"

    if [[ "$org_lower" != "$node_ctx_lower" ]]; then
        log_imp 10 "-" "FAIL-FAST: PROJECT_ORG='${PROJECT_ORG}' does not match node.yaml context='${node_context}'"
        log_imp 10 "-" "  Either use --org ${node_context} or update node.yaml context"
        exit 1
    fi

    # Casing mismatch only → WARN and adopt node.yaml casing for consistency
    if [[ "$PROJECT_ORG" != "$node_context" ]]; then
        log_imp 8 "-" "Casing mismatch: PROJECT_ORG='${PROJECT_ORG}' vs node.yaml context='${node_context}' — using node.yaml variant"
        PROJECT_ORG="$node_context"
    fi

    log_imp 7 "-" "node.yaml context validated: ${PROJECT_ORG}"
}
# endregion FUNC_validate_org_against_node_yaml

# ──────────────────────────────────────────────────────────────────
# region FUNC_main
## @purpose  Main entry point — orchestrate project adoption
main() {
    log_imp 6 "-" "Starting adopt-project.sh (T11 full implementation)"

    parse_args "$@"

    # ── Org validation against node.yaml (Contract 4.3) ──
    validate_org_against_node_yaml
    local changes=()

    log_imp 7 "-" "Adopting project: ${PROJECT_DIR}"

    # ── Step 1: Generate or verify ai-platform.yaml ──
    log_imp 7 "-" "Step 1/7: Ensure ai-platform.yaml exists"
    generate_minimal_ai_platform_yaml
    changes+=("✔ ai-platform.yaml checked/generated")

    # ── Step 2: Simplify deploy.yml ──
    log_imp 7 "-" "Step 2/7: Simplify deploy.yml to reusable workflow"
    local deploy_yml="${PROJECT_DIR}/.github/workflows/deploy.yml"
    local deploy_was_simplified=false
    if [[ -f "$deploy_yml" ]] && ! grep -q "uses:.*/ai-platform/.github/workflows/deploy-project.yml" "$deploy_yml" 2>/dev/null; then
        simplify_deploy_yml
        if grep -q "uses:.*/ai-platform/.github/workflows/deploy-project.yml" "$deploy_yml" 2>/dev/null; then
            deploy_was_simplified=true
        fi
    else
        log_imp 6 "-" "deploy.yml already uses reusable workflow or not present"
    fi
    if [[ "$deploy_was_simplified" == "true" ]]; then
        changes+=("✔ deploy.yml simplified (uses: org/ai-platform/...)")
    else
        changes+=("- deploy.yml unchanged or already simplified")
    fi

    # ── Step 3: Delete platform-deploy.yml ──
    log_imp 7 "-" "Step 3/7: Remove deprecated platform-deploy.yml"
    delete_platform_deploy_yml
    changes+=("✔ platform-deploy.yml removed (if existed)")

    # ── Step 4: Generate .env.platform ──
    log_imp 7 "-" "Step 4/7: Generate .env.platform"
    gen_env_platform
    changes+=("✔ .env.platform regenerated")

    # ── Step 5: Generate Makefile and AGENTS.md ──
    log_imp 7 "-" "Step 5/7: Generate project Makefile and AGENTS.md"
    gen_project_makefile
    gen_project_agents
    changes+=("✔ Makefile/AGENTS.md ensured")

    # ── Step 6: Validate compose networks (proxy-net) ──
    log_imp 7 "-" "Step 6/8: Validate compose proxy-net (M4 gate)"
    local compose_validated=true
    local compose_candidate=""
    if [[ -f "${PROJECT_DIR}/compose.yaml" ]]; then
        compose_candidate="${PROJECT_DIR}/compose.yaml"
    elif [[ -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
        compose_candidate="${PROJECT_DIR}/docker-compose.yml"
    fi

    if [[ -n "$compose_candidate" ]]; then
        if validate_compose_networks "$compose_candidate"; then
            changes+=("✔ Compose proxy-net validated")
        else
            log_imp 8 "-" "  ⚠️  proxy-net validation FAILED — adopt continues, but fix before deploy"
            changes+=("⚠️  Compose proxy-net VALIDATION FAILED — must fix before deploy")
            compose_validated=false
        fi
    else
        log_imp 6 "-" "No compose file found — skipping proxy-net validation"
        changes+=("- No compose file — proxy-net validation skipped")
    fi

    # ── Step 7: Register in node.yaml ──
    log_imp 7 "-" "Step 7/8: Register in node.yaml (idempotent)"
    register_in_node_yaml
    changes+=("✔ node.yaml registration checked")

    # ── Step 8: Configure vhost ──
    log_imp 7 "-" "Step 8/8: Configure nginx vhost"
    configure_vhost
    if [[ -n "$PROJECT_DOMAIN" ]]; then
        changes+=("✔ Vhost configured for: ${PROJECT_DOMAIN}")
    else
        changes+=("- No domain — vhost skipped")
    fi

    # ── Print report ──
    print_diff_report "${changes[@]}"
}
# endregion FUNC_main

main "$@"
