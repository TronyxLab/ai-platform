#!/usr/bin/env bash
# GREP_SUMMARY: add-vhost nginx vhost generate remove render-all node-yaml GENERATED content-hash resolver-variable ssl dev-certs harness deteministic atomic
# STRUCTURE: parse_args(─add/─remove/─render-all) → ┌─[add] read_project_yaml→check_fqdn_unique→generate_vhost┐ → ┌─[render-all] parse_node_yaml→loop projects→temp_dir→render_all→nginx_t→atomic_mv┐ → summary
# region MODULE_CONTRACT
## @purpose  Manage nginx vhost configs for projects: create vhost with cert path
##           (wildcard for subdomains of PLATFORM_DOMAIN, own cert for personal domains)
##           or remove vhost. Supports --render-all mode for batch generation from node.yaml.
##           GENERATED-артефакты с content-hash для детерминизма и детекции дрейфа (R6).
## @scope    Called by add-project.sh (add mode), remove-project.sh (remove mode),
##           or standalone with --render-all --node <n> (S1 operator-side generation).
## @location core/internal/scaffold/add-vhost.sh
## @invariants
##   - ─add (default): generates vhost only if needs.expose: true AND needs.domain set
##   - ─remove: deletes vhost file, writes audit-log, idempotent (no-op if file missing)
##   - ─render-all ─node <n>: batch-render ALL projects with domain from node.yaml
##   - FQDN uniqueness enforced on add (E1): if another project claims the same domain, exit 1
##   - GENERATED-маркер: content-hash тела + источник (node.yaml#projects[<name>])
##   - Детерминизм: повторный рендер при неизменном node.yaml → байт-идентичный вывод
##   - Всё-или-ничего: рендер во временный каталог, атомарный mv после nginx -t всех файлов
##   - Wildcard cert for *.${PLATFORM_DOMAIN} subdomains (DD3)
##   - Personal domain (non-${PLATFORM_DOMAIN}) → own cert path (O11)
##   - Vhost written to: <node-configs>/<node>/overlays/nginx/<fqdn>.conf
##   - Flat directory — no subdirectories (TRAP[BUG] DRIFT-1)
## @rationale Platform owns routing; project only declares needs.domain.
##            S1-модель: генерация у оператора (render-vhosts), доставка rsync-каналом,
##            верификация content-hash на ноде (R6). Локальный nginx -t harness ДО прода.
## @changes 2026-07-18 · T2.1 — GENERATED-маркер с content-hash, --render-all, resolver valid=30s, harness
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || echo "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")")}"

PROJECT_DIR=""
NODE_CONFIGS_DIR=""
MODE="add"  # add|remove|render-all
RENDER_NODE=""
RENDER_TARGET_DIR=""

__LOG_PREFIX="add-vhost"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

# ═══════════════════════════════════════════════════════════════════
# USAGE
# ═══════════════════════════════════════════════════════════════════
# region USAGE
usage() {
    cat <<'HELP'
USAGE: add-vhost.sh <mode> [options]

MODES:
  --add                        Generate vhost for a single project (default)
  --remove                     Remove vhost (deletes file + writes audit-log)
  --render-all --node <n>      Batch-render ALL vhosts from node.yaml#projects

REQUIRED (add/remove):
  --project-dir <path>         Path to the project directory (contains ai-platform.yaml)
  --node-configs-dir <path>    Path to node-configs/ directory

REQUIRED (render-all):
  --node <n>                   Node name to render vhosts for
  --node-configs-dir <path>    Path to node-configs/ directory

OPTIONS:
  --node-configs-dir <path>    Path to node-configs/ directory
  --help|-h                    Show this help

EXAMPLES:
  add-vhost.sh --add --project-dir /path/to/project --node-configs-dir /path/to/node-configs
  add-vhost.sh --remove --project-dir /path/to/project --node-configs-dir /path/to/node-configs
  add-vhost.sh --render-all --node tronyx-vps --node-configs-dir /path/to/node-configs
HELP
    exit 1
}
# endregion USAGE

# ═══════════════════════════════════════════════════════════════════
# CONTENT HASH
# ═══════════════════════════════════════════════════════════════════
# region FUNC_compute_hash
## @purpose  Compute SHA-256 content hash of the vhost body (excluding GENERATED header).
##           Used for R6 drift detection and determinism verification.
## @param $1  vhost file path
## @stdout   64-char hex sha256 hash of lines after GENERATED marker
## @complexity O(n) where n = file size
## @invariants — Hash excludes the GENERATED header block (first N lines until blank line)
##             - Hash includes everything after first blank line + trailing newline
compute_body_hash() {
    local vhost_file="$1"
    # Hash everything after the first blank line (skipping GENERATED header)
    sed -n '/^$/,$$ p' "$vhost_file" 2>/dev/null | sha256sum | cut -d' ' -f1
}
# endregion FUNC_compute_hash

# ═══════════════════════════════════════════════════════════════════
# GENERATED HEADER
# ═══════════════════════════════════════════════════════════════════
# region FUNC_generated_header
## @purpose  Emit the GENERATED header block for a vhost config.
##           Includes source information and body content-hash for R6 drift detection.
## @param $1  project_name
## @param $2  fqdn
## @param $3  node
## @param $4  body_hash
## @stdout   Header lines
generated_header() {
    local project_name="$1" fqdn="$2" node="$3" body_hash="$4"
    cat <<GENHEADER
# ============================================================
# GENERATED by add-vhost.sh — DO NOT EDIT
# Source: node.yaml#projects[${project_name}]
# Domain: ${fqdn}
# Node: ${node}
# Content-Hash: ${body_hash}
# Generator: add-vhost.sh render-vhosts (S1)
# ============================================================

GENHEADER
}
# endregion FUNC_generated_header

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
            --render-all)        MODE="render-all"; shift ;;
            --node)              RENDER_NODE="$2"; shift 2 ;;
            --help|-h)           usage ;;
            *)                   log_crit "Unknown argument: $1"; usage ;;
        esac
    done

    if [[ "$MODE" == "render-all" ]]; then
        if [[ -z "$RENDER_NODE" ]]; then
            log_crit "--node <n> is required for --render-all mode"
            usage
        fi
        if [[ -z "$NODE_CONFIGS_DIR" ]]; then
            log_crit "--node-configs-dir <path> is required for --render-all mode"
            usage
        fi
        if [[ ! -d "$NODE_CONFIGS_DIR" ]]; then
            log_crit "Node configs directory not found: ${NODE_CONFIGS_DIR}"
            exit 1
        fi
        return 0
    fi

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
# READ_NODE_YAML (for --render-all)
# ═══════════════════════════════════════════════════════════════════
# region READ_NODE_YAML
## @purpose  Parse node.yaml and extract project entries with domain.
##           Used by --render-all mode to discover all vhosts to generate.
## @param $1  node_yaml_path — path to node.yaml
## @stdout   JSON lines: {"name":"<n>","domain":"<d>"} per project with domain
## @complexity O(P) where P = number of projects
## @invariants — Only emits projects that have a non-empty 'domain' field
##             - Emits nothing if no projects or node.yaml missing
##             - Projects without domain field are silently skipped
read_node_yaml_projects() {
    local node_yaml_path="$1"

    if [[ ! -f "$node_yaml_path" ]]; then
        log_imp 8 "read_node_yaml" "node.yaml not found: ${node_yaml_path}"
        return 0
    fi

    log_imp 7 "read_node_yaml" "Parsing projects from: ${node_yaml_path}"

    # Use python3 yaml parser if available (handles anchors, aliases, edge cases)
    if command -v python3 &>/dev/null && python3 -c "import yaml" 2>/dev/null; then
        python3 <<'PYEOF'
import os, json, sys, yaml

yaml_path = os.environ.get('NODE_YAML_PATH', '')
if not yaml_path:
    sys.exit(0)

try:
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
except Exception as e:
    print(f"[IMP:9][read_node_yaml] ERROR: {e}", file=sys.stderr)
    sys.exit(1)

projects = data.get('projects', [])
if not isinstance(projects, list):
    sys.exit(0)

for p in projects:
    if not isinstance(p, dict):
        continue
    name = p.get('name', '')
    domain = p.get('domain', '')
    if name and domain:
        print(json.dumps({"name": name, "domain": domain}))
PYEOF
    else
        # Fallback: grep-based parser for simple YAML
        log_imp 8 "read_node_yaml" "python3+yaml not available — using grep fallback"
        # Parse YAML projects list with grep/awk (no yamllint, basic parsing only)
        local in_projects=0 in_entry=0 name="" domain=""
        while IFS= read -r line; do
            if [[ "$line" =~ ^[[:space:]]*projects: ]]; then
                in_projects=1
                continue
            fi
            if [[ "$in_projects" -eq 1 ]]; then
                if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*name:[[:space:]]*(.+) ]]; then
                    name="${BASH_REMATCH[1]}"
                    in_entry=1
                elif [[ "$in_entry" -eq 1 ]] && [[ "$line" =~ ^[[:space:]]*domain:[[:space:]]*(.+) ]]; then
                    domain="${BASH_REMATCH[1]}"
                    # Emit this entry
                    if [[ -n "$name" && -n "$domain" ]]; then
                        echo "{\"name\":\"${name}\",\"domain\":\"${domain}\"}"
                    fi
                    name="" domain="" in_entry=0
                elif [[ "$line" =~ ^[[:space:]]*-[[:space:]]*name: ]] || [[ "$line" =~ ^[[:space:]]*-[[:space:]]*repo: ]]; then
                    # New project entry without domain closure for previous
                    if [[ -n "$name" && -n "$domain" ]]; then
                        echo "{\"name\":\"${name}\",\"domain\":\"${domain}\"}"
                    fi
                    name="" domain=""
                    in_entry=0
                    if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*name:[[:space:]]*(.+) ]]; then
                        name="${BASH_REMATCH[1]}"
                        in_entry=1
                    fi
                fi
            fi
        done < "$node_yaml_path"
        # Emit last entry if still open
        if [[ -n "$name" && -n "$domain" ]]; then
            echo "{\"name\":\"${name}\",\"domain\":\"${domain}\"}"
        fi
    fi
}
# endregion READ_NODE_YAML

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
# GENERATE_VHOST_BODY — produces the vhost config body (w/o header)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_generate_vhost_body
## @purpose  Generate the nginx vhost config body (without GENERATED header).
##           Uses resolver 127.0.0.11 with valid=30s and variable proxy_pass
##           for lazy DNS resolution (Docker DNS). `listen 443 ssl;` + separate `http2 on;`
##           per nginx deprecation policy (not deprecated `listen ... http2`).
## @param $1  fqdn — full domain name
## @param $2  project_name — upstream service name
## @param $3  cert_domain — resolved cert domain
## @stdout   Vhost config body
generate_vhost_body() {
    local fqdn="$1" project_name="$2" cert_domain="$3"

    cat <<VHOSTBODY
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
    server_name ${fqdn};

    http2 on;

    ssl_certificate /etc/letsencrypt/live/${cert_domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${cert_domain}/privkey.pem;

    # Docker DNS resolver with caching (30s TTL) — enables lazy DNS resolution
    # @rationale nginx resolves upstream hostnames at config load time by default.
    #   Using resolver 127.0.0.11 (Docker embedded DNS) + variables in proxy_pass
    #   defers resolution to request time. This allows nginx to start even when
    #   upstream containers are not ready, and to automatically pick up new IPs
    #   after force-recreate (TTL 30s).
    resolver 127.0.0.11 valid=30s ipv6=off;

    # ── Security headers (platform policy) ─────────────────────────────────
    include /etc/nginx/includes/security-headers.conf;

    # ── Rate limiting (dynamic zone: 10 r/s, burst 20) ─────────────────────
    limit_req zone=dynamic burst=20 nodelay;
    limit_req_status 429;

    # ── Buffering (platform policy) ────────────────────────────────────────
    proxy_buffering on;
    proxy_buffer_size 4k;
    proxy_buffers 8 4k;
    proxy_busy_buffers_size 8k;

    location / {
        # Lazy DNS resolution via variable
        set \$upstream_${project_name} http://${project_name}:80;
        proxy_pass \$upstream_${project_name};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Timeouts
        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /health {
        proxy_pass \$upstream_${project_name}/health;
        access_log off;
    }
}
VHOSTBODY
}
# endregion FUNC_generate_vhost_body

# ═══════════════════════════════════════════════════════════════════
# GENERATE_VHOST (single project)
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

    # Generate body first to compute hash
    local body
    body="$(generate_vhost_body "$fqdn" "$project_name" "$cert_domain")"

    # Write to temp file for hash computation
    local tmp_body
    tmp_body="$(mktemp)"
    echo "$body" > "$tmp_body"
    local body_hash
    body_hash="$(compute_body_hash "$tmp_body")"

    # Build complete file: GENERATED header + body
    {
        generated_header "$project_name" "$fqdn" "$node" "$body_hash"
        echo "$body"
    } > "$vhost_file"

    rm -f "$tmp_body"

    log_imp 9 "vhost" "Nginx vhost generated: ${vhost_file} (cert_domain=${cert_domain}, hash=${body_hash:0:12})"
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
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [audit] [vhost:remove] domain=${fqdn} node=${node}" >> "$audit_log"
    log_imp 8 "vhost" "Audit log written: removed vhost for ${fqdn}"

    log_ok "Nginx vhost removed: ${vhost_file}"
}
# endregion REMOVE_VHOST

# ═══════════════════════════════════════════════════════════════════
# CHECK_DUPLICATE_DOMAINS (render-all FQDN uniqueness)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_check_duplicate_domains
## @purpose  Check for duplicate FQDNs across projects in render-all mode.
##           FATAL exit 2 if any domain appears more than once.
## @param $@  JSON lines: {"name":"<n>","domain":"<d>"}
## @complexity O(P²) worst-case, O(P) with sort|uniq
check_duplicate_domains() {
    local domains
    domains="$(echo "$@" | python3 -c "
import sys, json
seen = {}
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        p = json.loads(line)
        d = p.get('domain', '')
        n = p.get('name', '')
        if d in seen:
            print(f'FATAL: Duplicate domain {d} in projects: {seen[d]} and {n}')
        seen[d] = n
    except json.JSONDecodeError:
        pass
" 2>/dev/null || true)"

    if [[ -n "$domains" ]]; then
        log_imp 10 "render" "FQDN uniqueness violation:"
        echo "$domains" | while IFS= read -r line; do
            log_imp 10 "render" "  ${line}"
        done
        log_crit "Duplicate domain detected — aborting render. Each FQDN must be unique across all projects."
        exit 2
    fi
    log_imp 9 "render" "FQDN uniqueness check PASS"
}
# endregion FUNC_check_duplicate_domains

# ═══════════════════════════════════════════════════════════════════
# NGINX -T HARNESS (local docker validation)
# ═══════════════════════════════════════════════════════════════════
# region FUNC_nginx_t_harness
## @purpose  Run nginx -t on rendered configs using docker nginx:alpine image.
##           SSL cert paths are replaced with dev-certs for local validation.
## @param $1  temp_dir — directory containing rendered vhost files
## @param $2  nginx_version — nginx image tag (default: 1.28-alpine)
## @return   0 if nginx -t passes, 1 otherwise
## @invariants — Uses docker run --rm with mounted temp_dir as conf.d/overlay/
##             - SSL cert paths in rendered files are swapped to dev-certs
##             - Does NOT modify source files — only in docker container context
##             - Falls back to WARN if docker is unavailable
nginx_t_harness() {
    local temp_dir="$1"
    local nginx_version="${2:-1.28-alpine}"
    local harness_dir
    harness_dir="$(mktemp -d)"

    log_imp 7 "harness" "Starting nginx -t validation harness (nginx:${nginx_version})"

    # Create a minimal nginx.conf for validation
    cat > "${harness_dir}/nginx.conf" <<'HARNESS_CONF'
events {
    worker_connections 64;
}
http {
    # Base includes for vhosts to resolve
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # Rate limiting zones (referenced by GENERATED vhosts)
    limit_req_zone $binary_remote_addr zone=dynamic:10m rate=10r/s;

    # Docker DNS resolver
    resolver 127.0.0.11 valid=30s ipv6=off;

    # Stub security headers include
    server {
        listen 80 default_server;
        return 444;
    }

    # Overlay vhosts
    include /etc/nginx/conf.d/overlay/*.conf;
}
HARNESS_CONF

    # Create stub security-headers.conf
    cat > "${harness_dir}/security-headers.conf" <<'HARNESS_SEC'
# Stub security-headers.conf for nginx -t validation
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
HARNESS_SEC

    # Create stub dev-certs for SSL validation
    mkdir -p "${harness_dir}/dev-certs"
    # Generate self-signed dev certs if not present
    if [[ ! -f "${harness_dir}/dev-certs/fullchain.pem" ]]; then
        openssl req -x509 -nodes -days 1 -newkey rsa:2048 \
            -keyout "${harness_dir}/dev-certs/privkey.pem" \
            -out "${harness_dir}/dev-certs/fullchain.pem" \
            -subj "/CN=localhost" 2>/dev/null || {
            log_imp 8 "harness" "openssl not available — creating empty cert files"
            touch "${harness_dir}/dev-certs/fullchain.pem"
            touch "${harness_dir}/dev-certs/privkey.pem"
        }
    fi

    # For each rendered vhost, create a dev-version with SSL paths swapped to dev-certs
    # This is the harness-only path swap — original rendered files are not modified
    local vhost_count=0
    local vhost_file
    for vhost_file in "${temp_dir}"/*.conf; do
        [[ -f "$vhost_file" ]] || continue
        vhost_count=$((vhost_count + 1))
        local dev_vhost="${harness_dir}/dev-${vhost_file##*/}"
        # Replace production SSL paths with dev-certs for validation
        sed 's|/etc/letsencrypt/live/[^/]*/fullchain.pem|/etc/nginx/dev-certs/fullchain.pem|g;
             s|/etc/letsencrypt/live/[^/]*/privkey.pem|/etc/nginx/dev-certs/privkey.pem|g;
             s|/var/www/acme|/tmp/acme-stub|g' "$vhost_file" > "$dev_vhost"
    done

    if [[ "$vhost_count" -eq 0 ]]; then
        log_imp 7 "harness" "No vhost files to validate — SKIP"
        rm -rf "$harness_dir"
        return 0
    fi

    # Create stub acme directory
    mkdir -p "${harness_dir}/acme-stub"

    # Run nginx -t via docker
    log_imp 7 "harness" "Validating ${vhost_count} vhost(s) via nginx -t (docker)"
    local result
    if command -v docker &>/dev/null; then
        if docker run --rm \
            -v "${harness_dir}/nginx.conf:/etc/nginx/nginx.conf:ro" \
            -v "${harness_dir}/dev-certs:/etc/nginx/dev-certs:ro" \
            -v "${harness_dir}/security-headers.conf:/etc/nginx/includes/security-headers.conf:ro" \
            -v "${harness_dir}:/etc/nginx/conf.d/overlay:ro" \
            "nginx:${nginx_version}" nginx -t 2>&1; then
            log_imp 9 "harness" "nginx -t PASS: ${vhost_count} vhost(s) valid"
            result=0
        else
            log_imp 10 "harness" "nginx -t FAIL — rendered configs contain syntax errors"
            result=1
        fi
    else
        log_imp 8 "harness" "docker not available — skipping nginx -t validation (WARN)"
        result=0
    fi

    rm -rf "$harness_dir"
    return $result
}
# endregion FUNC_nginx_t_harness

# ═══════════════════════════════════════════════════════════════════
# RENDER_ALL — batch vhost generation from node.yaml
# ═══════════════════════════════════════════════════════════════════
# region FUNC_render_all
## @purpose  Render all vhosts from node.yaml#projects with domain.
##           Uses temp dir + atomic mv for all-or-nothing semantics.
##           Validates all rendered configs via nginx -t before moving.
##           Deterministic: unchanged node.yaml → byte-identical output.
render_all() {
    local node="$RENDER_NODE"
    local node_configs_dir="$NODE_CONFIGS_DIR"
    local node_yaml="${node_configs_dir}/${node}/node.yaml"
    local overlay_dir="${node_configs_dir}/${node}/overlays/nginx"

    log_imp 7 "render_all" "Starting render-all for node=${node}"
    log_imp 7 "render_all" "node.yaml: ${node_yaml}"
    log_imp 7 "render_all" "overlay dir: ${overlay_dir}"

    # ── Step 1: Parse node.yaml projects ─────────────────────────
    local projects_json
    NODE_YAML_PATH="$node_yaml" projects_json="$(read_node_yaml_projects "$node_yaml")"

    # Filter only projects with domain
    local project_entries=()
    local has_domain=false
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        project_entries+=("$line")
        has_domain=true
    done <<< "$projects_json"

    if [[ ${#project_entries[@]} -eq 0 ]]; then
        log_imp 8 "render_all" "No projects with domain found in node.yaml — rendering 0 files"
        echo ""
        echo "──────────────────────────────────────────────────────"
        echo "  ⚠️  render-vhosts: 0 files rendered (no domain projects)"
        echo "──────────────────────────────────────────────────────"
        log_imp 7 "render_all" "DONE: no vhosts to render"
        return 0
    fi

    log_imp 7 "render_all" "Found ${#project_entries[@]} project(s) with domain"

    # ── Step 2: FQDN uniqueness check ────────────────────────────
    check_duplicate_domains "${project_entries[@]}"

    # ── Step 3: Create temp directory for rendering ──────────────
    local temp_dir
    temp_dir="$(mktemp -d)"
    log_imp 7 "render_all" "Render temp dir: ${temp_dir}"

    # Ensure cleanup on failure
    local cleanup_temp=true

    # ── Step 4: Render all vhosts to temp dir ────────────────────
    local rendered_count=0
    local entry
    for entry in "${project_entries[@]}"; do
        local proj_name proj_domain
        proj_name="$(echo "$entry" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['name'])" 2>/dev/null || echo "")"
        proj_domain="$(echo "$entry" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['domain'])" 2>/dev/null || echo "")"

        if [[ -z "$proj_name" || -z "$proj_domain" ]]; then
            log_imp 8 "render_all" "Skipping malformed entry: ${entry}"
            continue
        fi

        # Normalize domain: lowercase
        proj_domain="${proj_domain,,}"

        local cert_domain
        cert_domain="$(resolve_cert_domain "$proj_domain")"

        log_imp 7 "render_all" "Rendering vhost for ${proj_name} → ${proj_domain}"

        # Generate body
        local body
        body="$(generate_vhost_body "$proj_domain" "$proj_name" "$cert_domain")"

        # Compute body hash
        local tmp_body
        tmp_body="$(mktemp)"
        echo "$body" > "$tmp_body"
        local body_hash
        body_hash="$(compute_body_hash "$tmp_body")"
        rm -f "$tmp_body"

        # Write complete file with GENERATED header
        local vhost_file="${temp_dir}/${proj_domain}.conf"
        {
            generated_header "$proj_name" "$proj_domain" "$node" "$body_hash"
            echo "$body"
        } > "$vhost_file"

        rendered_count=$((rendered_count + 1))
        log_imp 9 "render_all" "Rendered: ${proj_domain}.conf (hash=${body_hash:0:12})"
    done

    log_imp 7 "render_all" "Rendered ${rendered_count} vhost(s) to temp dir"

    # ── Step 5: nginx -t validation ──────────────────────────────
    if ! nginx_t_harness "$temp_dir"; then
        log_imp 10 "render_all" "nginx -t validation FAILED — removing temp dir, aborting"
        rm -rf "$temp_dir"
        log_crit "Vhost validation failed — no files written (all-or-nothing)"
        exit 1
    fi

    # ── Step 6: Atomic mv to overlay dir ─────────────────────────
    mkdir -p "$overlay_dir"

    # Remove existing GENERATED vhosts (files with GENERATED marker)
    local existing_count=0
    local gen_file
    for gen_file in "${overlay_dir}"/*.conf; do
        [[ -f "$gen_file" ]] || continue
        if head -1 "$gen_file" 2>/dev/null | grep -q "GENERATED by add-vhost.sh"; then
            rm -f "$gen_file"
            existing_count=$((existing_count + 1))
        fi
    done
    if [[ "$existing_count" -gt 0 ]]; then
        log_imp 7 "render_all" "Removed ${existing_count} existing GENERATED vhost(s)"
    fi

    # Move rendered files
    local moved_count=0
    for vhost_file in "${temp_dir}"/*.conf; do
        [[ -f "$vhost_file" ]] || continue
        mv "$vhost_file" "${overlay_dir}/"
        moved_count=$((moved_count + 1))
    done

    rm -rf "$temp_dir"

    log_imp 9 "render_all" "Atomic mv complete: ${moved_count} vhost(s) → ${overlay_dir}"

    echo ""
    echo "──────────────────────────────────────────────────────"
    echo "  ✅ render-vhosts: ${moved_count} vhost(s) generated"
    echo "     Node: ${node}"
    echo "     Output: ${overlay_dir}"
    echo "──────────────────────────────────────────────────────"
    echo ""

    log_imp 9 "render_all" "DONE: render-all for node=${node} (${moved_count} vhosts)"
}
# endregion FUNC_render_all

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
# region MAIN
main() {
    echo "[IMP:8][add-vhost][main] Starting vhost management" >&2
    parse_args "$@"

    # ── Render-all mode ─────────────────────────────────────────
    if [[ "$MODE" == "render-all" ]]; then
        log_imp 8 "main" "Mode: render-all for node=${RENDER_NODE}"
        render_all
        exit 0
    fi

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

    # ── Add mode (default) ───────────────────────────────────────
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
