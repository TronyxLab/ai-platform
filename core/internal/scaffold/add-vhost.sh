#!/usr/bin/env bash
# GREP_SUMMARY: add-vhost nginx vhost generate ai-platform.yaml domain FQDN-conflict uniq SSL
# STRUCTURE: parse_args → read_project_yaml → check_expose → check_fqdn_unique → generate_vhost → summary
# region MODULE_CONTRACT
## @purpose  Read ai-platform.yaml, generate nginx vhost config in node-configs overlays.
## @scope    Called by add-project.sh (or manually) after project creation.
## @location core/internal/scaffold/add-vhost.sh — moved from core/scripts/add-vhost.sh
## @invariants
##   - Only generates vhost if ai-platform.yaml has needs.expose: true AND needs.domain set.
##   - FQDN uniqueness enforced (E1): if another project claims the same domain, exit 1.
##   - Vhost written to: <node-configs>/<node>/overlays/nginx/<fqdn>.conf
## @rationale Platform owns routing; project only declares needs.domain.
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || echo "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")")}"

PROJECT_DIR=""
NODE_CONFIGS_DIR=""

__LOG_PREFIX="add-vhost"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

# region USAGE
usage() {
    cat <<'HELP'
USAGE: add-vhost.sh --project-dir <path> --node-configs-dir <path>

REQUIRED:
  --project-dir <path>         Path to the project directory (contains ai-platform.yaml)
  --node-configs-dir <path>    Path to node-configs/ directory

EXAMPLES:
  core/scripts/add-vhost.sh \
    --project-dir /path/to/project \
    --node-configs-dir /path/to/node-configs
HELP
    exit 1
}
# endregion USAGE

# region PARSE_ARGS
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --project-dir)       PROJECT_DIR="$2"; shift 2 ;;
            --node-configs-dir)  NODE_CONFIGS_DIR="$2"; shift 2 ;;
            --help|-h)           usage ;;
            *)                   log_crit "Unknown argument: $1"; usage ;;
        esac
    done

    if [[ -z "$PROJECT_DIR" ]] || [[ -z "$NODE_CONFIGS_DIR" ]]; then
        log_crit "Missing required arguments: --project-dir and --node-configs-dir are required"
        usage
    fi

    if [[ ! -d "$PROJECT_DIR" ]]; then
        log_crit "Project directory not found: ${PROJECT_DIR}"
        exit 1
    fi

    if [[ ! -d "$NODE_CONFIGS_DIR" ]]; then
        log_crit "Node configs directory not found: ${NODE_CONFIGS_DIR}"
        exit 1
    fi
}
# endregion PARSE_ARGS

# region READ_PROJECT_YAML
read_project_yaml() {
    local project_yaml=""

    if [[ -f "${PROJECT_DIR}/ai-platform.yaml" ]]; then
        project_yaml="${PROJECT_DIR}/ai-platform.yaml"
    else
        log_crit "No ai-platform.yaml found in: ${PROJECT_DIR}"
        exit 1
    fi

    log_info "Reading: ${project_yaml}"

    local expose_val domain_val node_val

    expose_val="$(grep -E '^\s*expose:\s*true' "$project_yaml" 2>/dev/null | head -1 || true)"
    domain_val="$(grep -E '^[[:space:]]*domain:' "$project_yaml" 2>/dev/null | head -1 | awk '{sub(/^[[:space:]]*domain:[[:space:]]*/, ""); gsub(/["\047]/, ""); print $1}' || true)"
    node_val="$(grep -E '^\s*target_node:\s*' "$project_yaml" 2>/dev/null | head -1 | awk '{print $2}' || true)"

    if [[ "$domain_val" == "false" ]]; then
        domain_val=""
    fi

    if [[ -z "$expose_val" ]]; then
        log_info "Project does not have expose: true — skipping vhost generation"
        exit 0
    fi

    if [[ -z "$domain_val" ]]; then
        log_info "Project has expose: true but no domain — skipping vhost generation"
        exit 0
    fi

    if [[ -z "$node_val" ]]; then
        log_crit "target_node not found in $(basename "${project_yaml}")"
        exit 1
    fi

    log_info "Parsed: expose=true, domain=${domain_val}, target_node=${node_val}"

    PROJECT_DOMAIN="$domain_val"
    PROJECT_NODE="$node_val"
    PROJECT_NAME="$(basename "$PROJECT_DIR")"
}
# endregion READ_PROJECT_YAML

# region GENERATE_VHOST
generate_vhost() {
    local fqdn="$1"
    local project_name="$2"
    local node="$3"

    # Resolve cert domain: subdomains of PLATFORM_DOMAIN use wildcard cert
    local platform_domain="${PLATFORM_DOMAIN:-}"
    local cert_domain="$fqdn"
    if [[ -n "$platform_domain" ]] && [[ "$fqdn" == *".${platform_domain}" ]]; then
        cert_domain="$platform_domain"
    fi

    # ⚠️ TRAP[BUG] · 2026-07-17 · DRIFT-1 · Flat directory (depth 1) required
    # · Symptom: vhost silently not loaded (fall-through to catch-all, class D12)
    # · Root: producer wrote to conf.d/ subdir, non-recursive include overlay/*.conf
    #   reads parent level — path mismatch → vhost never picked up
    # · Fix: flat layout overlays/nginx/, no subdirectories
    # · Prevention: static test test_add_vhost_writes_to_mounted_dir (F4)
    local vhost_dir="${NODE_CONFIGS_DIR}/${node}/overlays/nginx"
    local vhost_file="${vhost_dir}/${fqdn}.conf"

    mkdir -p "$vhost_dir"

    if [[ -f "$vhost_file" ]]; then
        log_warn "Vhost file already exists: ${vhost_file}"
        read -r -p "  Overwrite? [y/N] " response
        case "$response" in
            [yY][eE][sS]|[yY]) ;;
            *) log_warn "Skipping vhost overwrite"; return 0 ;;
        esac
    fi

    log_info "Generating nginx vhost: ${vhost_file}"

    cat > "$vhost_file" <<VHOST
# ============================================================
# GENERATED by add-vhost.sh — DO NOT EDIT MANUALLY
# Source: ${PROJECT_DIR}/ai-platform.yaml
# Project: ${project_name}
# Node: ${node}
# ============================================================

# HTTP → HTTPS redirect
server {
    listen 80;
    listen [::]:80;
    server_name ${fqdn};

    location /.well-known/acme-challenge/ {
        root /var/www/acme;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

# HTTPS vhost
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ${fqdn};

    ssl_certificate /etc/letsencrypt/live/${cert_domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${cert_domain}/privkey.pem;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;

    location / {
        proxy_pass http://${project_name}:80;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /health {
        proxy_pass http://${project_name}:80/health;
        access_log off;
    }
}
VHOST

    log_ok "Nginx vhost written: ${vhost_file}"
}
# endregion GENERATE_VHOST

# region MAIN
main() {
    parse_args "$@"

    log_info "START: add-vhost for ${PROJECT_DIR}"

    read_project_yaml

    # FQDN uniqueness check delegated to validate.sh
    # Resolve validate.sh: SCRIPT_DIR first (for testing with mock), then PLATFORM_ROOT
    VALIDATE_BIN="${SCRIPT_DIR}/validate.sh"
    if [[ ! -x "$VALIDATE_BIN" ]]; then
        VALIDATE_BIN="${PLATFORM_ROOT}/core/internal/validate/validate.sh"
    fi
    if ! "$VALIDATE_BIN" --check-fqdn "$PROJECT_DIR"; then
        log_fail "FQDN ${PROJECT_DOMAIN} validation failed"
        exit 1
    fi
    generate_vhost "$PROJECT_DOMAIN" "$PROJECT_NAME" "$PROJECT_NODE"

    echo ""
    echo "──────────────────────────────────────────────────────"
    echo "  ✅ nginx vhost создан"
    echo "──────────────────────────────────────────────────────"
    echo ""

    log_ok "DONE: vhost for ${PROJECT_DOMAIN} generated successfully"
}

# endregion MAIN

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
