#!/usr/bin/env bash
# GREP_SUMMARY: add-vhost nginx vhost generate remove delete ai-platform.yaml domain FQDN-conflict uniq SSL wildcard personal-domain audit
# STRUCTURE: parse_args(─add/─remove) → read_project_yaml → check_expose → check_fqdn_unique → ⊕ {[─add] generate_vhost | [─remove] remove_vhost} → summary
# region MODULE_CONTRACT
## @purpose  Manage nginx vhost configs for projects: create vhost with cert path
##           (wildcard for subdomains of PLATFORM_DOMAIN, own cert for personal domains)
##           or remove vhost. Writes audit-log on removal.
## @scope    Called by add-project.sh (add mode) or remove-project.sh (remove mode).
## @location core/internal/scaffold/add-vhost.sh
## @invariants
##   - ─add (default): generates vhost only if needs.expose: true AND needs.domain set
##   - ─remove: deletes vhost file, writes audit-log, idempotent (no-op if file missing)
##   - FQDN uniqueness enforced on add (E1): if another project claims the same domain, exit 1
##   - Wildcard cert for *.${PLATFORM_DOMAIN} subdomains (DD3)
##   - Personal domain (non-${PLATFORM_DOMAIN}) → own cert path (O11)
##   - Vhost written to: <node-configs>/<node>/overlays/nginx/<fqdn>.conf
##   - Flat directory — no subdirectories (TRAP[BUG] DRIFT-1)
## @rationale Platform owns routing; project only declares needs.domain.
##            Personal domain support required for adopt case (dance-site / O11).
##            remove_vhost() enables lifecycle completion (T10).
## @changes 2026-07-17 · T3 — Added ─remove mode, remove_vhost(), personal domain cert path
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || echo "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")")}"

PROJECT_DIR=""
NODE_CONFIGS_DIR=""
MODE="add"  # add|remove

__LOG_PREFIX="add-vhost"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

# ═══════════════════════════════════════════════════════════════════
# USAGE
# ═══════════════════════════════════════════════════════════════════
# region USAGE
usage() {
    cat <<'HELP'
USAGE: add-vhost.sh --project-dir <path> --node-configs-dir <path> [--add|--remove]

REQUIRED:
  --project-dir <path>         Path to the project directory (contains ai-platform.yaml)
  --node-configs-dir <path>    Path to node-configs/ directory

OPTIONS:
  --add                        Generate vhost (default)
  --remove                     Remove vhost (deletes file + writes audit-log)
  --help|-h                    Show this help

EXAMPLES:
  core/scripts/add-vhost.sh \
    --project-dir /path/to/project \
    --node-configs-dir /path/to/node-configs \
    --add

  core/scripts/add-vhost.sh \
    --project-dir /path/to/project \
    --node-configs-dir /path/to/node-configs \
    --remove
HELP
    exit 1
}
# endregion USAGE

# ═══════════════════════════════════════════════════════════════════
# PARSE_ARGS
# ═══════════════════════════════════════════════════════════════════
# region PARSE_ARGS
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --project-dir)       PROJECT_DIR="$2"; shift 2 ;;
            --node-configs-dir)  NODE_CONFIGS_DIR="$2"; shift 2 ;;
            --add)               MODE="add"; shift ;;
            --remove)            MODE="remove"; shift ;;
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

# ═══════════════════════════════════════════════════════════════════
# READ_PROJECT_YAML
# ═══════════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════════
# RESOLVE_CERT_DOMAIN — wildcard vs personal
# ═══════════════════════════════════════════════════════════════════
# region RESOLVE_CERT_DOMAIN
## @purpose  Determine cert domain for SSL certificate path.
##           Subdomains of PLATFORM_DOMAIN → wildcard cert (DD3).
##           Personal domains → own cert path (O11).
## @param $1 fqdn — the full domain from ai-platform.yaml
## @stdout   cert domain string
resolve_cert_domain() {
    local fqdn="$1"
    local platform_domain="${PLATFORM_DOMAIN:-}"

    if [[ -n "$platform_domain" ]] && [[ "$fqdn" == *".${platform_domain}" ]]; then
        # Wildcard cert: /etc/letsencrypt/live/${PLATFORM_DOMAIN}/
        echo "$platform_domain"
        log_imp 9 "cert" "Wildcard cert domain: ${platform_domain} (subdomain of PLATFORM_DOMAIN)"
    else
        # Personal domain: /etc/letsencrypt/live/${fqdn}/
        echo "$fqdn"
        log_imp 9 "cert" "Personal cert domain: ${fqdn} (own cert path)"
    fi
}
# endregion RESOLVE_CERT_DOMAIN

# ═══════════════════════════════════════════════════════════════════
# GENERATE_VHOST
# ═══════════════════════════════════════════════════════════════════
# region GENERATE_VHOST
generate_vhost() {
    local fqdn="$1"
    local project_name="$2"
    local node="$3"

    local cert_domain
    cert_domain="$(resolve_cert_domain "$fqdn")"

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

    log_imp 9 "vhost" "Nginx vhost generated: ${vhost_file} (cert_domain=${cert_domain})"
    log_ok "Nginx vhost written: ${vhost_file}"
}
# endregion GENERATE_VHOST

# ═══════════════════════════════════════════════════════════════════
# REMOVE_VHOST
# ═══════════════════════════════════════════════════════════════════
# region REMOVE_VHOST
## @purpose  Remove an existing nginx vhost config: delete file and write audit-log.
##           Idempotent — no-op if vhost file does not exist.
## @invariants — No service restart, no cert cleanup
##             — Audit log entry written on actual removal
##             — Safe to call multiple times (idempotent)
remove_vhost() {
    local fqdn="$1"
    local node="$2"

    local vhost_dir="${NODE_CONFIGS_DIR}/${node}/overlays/nginx"
    local vhost_file="${vhost_dir}/${fqdn}.conf"

    if [[ ! -f "$vhost_file" ]]; then
        log_warn "Vhost file not found (already removed): ${vhost_file}"
        log_imp 9 "vhost" "Remove SKIP — vhost file does not exist for ${fqdn} (idempotent)"
        return 0
    fi

    log_info "Removing nginx vhost: ${vhost_file}"

    rm -f "$vhost_file"
    log_imp 9 "vhost" "Vhost file deleted: ${vhost_file}"

    # Write audit-log entry
    local audit_log="${PLATFORM_ROOT}/var/log/audit.log"
    mkdir -p "$(dirname "$audit_log")" 2>/dev/null || true
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [audit] [vhost:remove] domain=${fqdn} node=${node} project=${PROJECT_NAME}" >> "$audit_log"
    log_imp 8 "vhost" "Audit log written: removed vhost for ${fqdn}"

    log_ok "Nginx vhost removed: ${vhost_file}"
}
# endregion REMOVE_VHOST

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
# region MAIN
main() {
    parse_args "$@"

    log_imp 8 "main" "START: add-vhost mode=${MODE} for ${PROJECT_DIR}"

    read_project_yaml

    if [[ "$MODE" == "remove" ]]; then
        log_imp 9 "main" "Mode: remove vhost for domain=${PROJECT_DOMAIN}"
        remove_vhost "$PROJECT_DOMAIN" "$PROJECT_NODE"
        echo ""
        echo "──────────────────────────────────────────────────────"
        echo "  ✅ nginx vhost удалён"
        echo "──────────────────────────────────────────────────────"
        log_ok "DONE: vhost for ${PROJECT_DOMAIN} removed successfully"
        exit 0
    fi

    # ── Add mode (default) ────────────────────────────────────────
    log_imp 9 "main" "Mode: add vhost for domain=${PROJECT_DOMAIN}"

    # FQDN uniqueness check delegated to validate.sh
    local validate_bin="${SCRIPT_DIR}/validate.sh"
    if [[ ! -x "$validate_bin" ]]; then
        validate_bin="${PLATFORM_ROOT}/core/internal/validate/validate.sh"
    fi
    if ! "$validate_bin" --check-fqdn "$PROJECT_DIR"; then
        log_fail "FQDN ${PROJECT_DOMAIN} validation failed"
        exit 1
    fi
    generate_vhost "$PROJECT_DOMAIN" "$PROJECT_NAME" "$PROJECT_NODE"

    echo ""
    echo "──────────────────────────────────────────────────────"
    echo "  ✅ nginx vhost создан"
    echo "──────────────────────────────────────────────────────"
    echo ""

    log_imp 9 "main" "DONE: vhost for ${PROJECT_DOMAIN} generated successfully"
}

# endregion MAIN

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
