#!/usr/bin/env bash
# GREP_SUMMARY: nginx install system apt wildcard-only dns-01-required tls atomic-config reload idempotent acme.sh webnames dnsapi-fallback cron post-issue-verify san-check no-http01 verify expiry acme-only
# STRUCTURE: guard(installed?) → apt install nginx → install acme.sh + dnsapi → write config(temp+nginx-t+atomic mv) → systemctl reload → acme.sh DNS-01 only (dns_plugin required) → issue wildcard *.domain → verify wildcard SAN via openssl → acme.sh --install-cronjob (idempotent) ─ verify expiry >30d
# region MODULE_CONTRACT
## @purpose  Idempotent system nginx installation with acme.sh DNS-01 TLS configuration
## @scope    Called during bootstrap step ⑪ for system-type modules; includes acme.sh --install-cronjob and cert expiry verification
## @invariants
##   - Config changes use temp file + nginx -t + atomic mv (never reload broken config)
##   - systemctl reload (not restart) on config change — zero-downtime
##   - acme.sh does NOT re-issue existing valid certificate (idempotency guard)
##   - No Docker image, no Dockerfile, no compose.yaml — install_type: system
## @rationale nginx is the traffic gateway; containerizing it adds network complexity (06 §3)
## ⚠️ TRAP[DECISION] · 2026-07-01 · — · nginx as system package rationale
## ·   nginx is installed as a system package (apt), NOT as a Docker container.
## ·   Rationale: nginx is the ingress gateway for ALL platform services. If it runs
## ·   in a container, a Docker daemon restart kills ALL external access. System nginx
## ·   survives Docker restarts, provides host-level socket for ACME, and simplifies
## ·   Rev: if multi-node load balancing required, switch to nginx plus keepalived or HAProxy
## ·   network setup (no host→container port mapping for ports 80/443).
## ·   Rejected: nginx in Docker (06 §3) — single point of failure on Docker restart
## ·   Rejected: Traefik/Caddy — overkill for current scale; nginx is proven, simple
# endregion MODULE_CONTRACT

# ══════════════════════════════════════════════════════════════════
# ⚠️ DEPRECATED — install.sh is NOT called for docker-type nginx
# · nginx is install_type: docker (module.yaml:15) → deploy-modules.sh
# ·   uses docker compose up, NOT deploy_system_module().
# · ACME SSL provisioning has been extracted to:
# ·   core/internal/bootstrap/ssl-provision.sh
# · This file is retained for reference only. Do NOT add new logic.
# · See DevPlan 002 for migration rationale.
# ══════════════════════════════════════════════════════════════════

# 📝 TRAP[DEBT] · 2026-07-16 · MED · legacy system-nginx installer (systemctl) — extracted to ssl-provision.sh
# · Observed: модуль конвертирован в docker (module.yaml install_type: docker);
#   deploy-modules.sh вызывает install.sh только для system-модулей.
# · Suspected: install.sh — мёртвый код для docker-модуля nginx, не вызывается
#   в текущей архитектуре (deploy-modules.sh guard по install_type).
# · Impact: вводит в заблуждение — next agent видит systemctl/apt логику и может
#   решить, что nginx работает как system-сервис, а не Docker.
# · When: during 502 vhost fix

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/audit_logging.sh" 2>/dev/null || true

__LOG_PREFIX="nginx-install"
source "${SCRIPT_DIR}/../../lib/logging.sh"

# region INSTALL_PACKAGES
install_packages() {
    local packages=(nginx)
    local to_install=()
    for pkg in "${packages[@]}"; do
        if ! dpkg -s "$pkg" &>/dev/null 2>&1; then
            to_install+=("$pkg")
        fi
    done

    if [[ ${#to_install[@]} -eq 0 ]]; then
        log_step "packages" "SKIP" "nginx already installed"
    else
        log_step "packages" "START" "Installing: ${to_install[*]}"
        apt-get update -qq
        apt-get install -y -qq "${to_install[@]}"
        log_step "packages" "DONE" "Packages installed"
    fi
}
# endregion INSTALL_PACKAGES

# region INSTALL_ACME
install_acme() {
    local acme_home="${ACME_HOME:-/opt/acme.sh}"
    local acme_sh="${acme_home}/acme.sh"

    if [[ -x "$acme_sh" ]]; then
        log_step "acme" "SKIP" "acme.sh already installed at ${acme_home}"
        return 0
    fi

    log_step "acme" "START" "Installing acme.sh to ${acme_home}"
    apt-get install -y -qq git 2>/dev/null || true

    # Proxy vars not needed here: unset_platform_proxy() in bootstrap.sh ran before any
    # module install step, so HTTP_PROXY/HTTPS_PROXY are already clean on the host level.
    if ! git clone --depth 1 https://github.com/acmesh-official/acme.sh.git "$acme_home" 2>&1; then
        log_step "acme" "FAIL" "Failed to clone acme.sh repository"
        return 1
    fi

    # Clone dnsapi extensions for Russian registrars (webnames, reg.ru, etc.)
    local dnsapi_ext="${acme_home}/dnsapi_ext"
    if [[ ! -d "$dnsapi_ext" ]]; then
        git clone --depth 1 https://github.com/regtime-ltd/dnsapi.git "$dnsapi_ext" 2>/dev/null || \
            log_step "acme" "WARN" "Failed to clone regtime-ltd/dnsapi — webnames TLS will not work"
    fi

    log_step "acme" "DONE" "acme.sh installed at ${acme_home}"
}
# endregion INSTALL_ACME

# region ACME_ISSUE
# ⚠️ TRAP[BUG] · 2026-06-11 · HI · Единственный метод выпуска TLS — acme.sh DNS-01
# · Для webnames.ru инжектит API-ключ в dns_webnames.sh через sed,
# · затем вызывает acme.sh --issue + --install-cert. Использует short plugin name
# · (dns_webnames), а не полный путь (см. TRAP[BUG] acme.sh basename bug ниже).
_issue_acme_cert() {
    local domain="$1"
    local email="$2"
    local dns_plugin="$3"
    local wildcard="${4:-true}"

    local acme_home="${ACME_HOME:-/opt/acme.sh}"
    local acme_sh="${acme_home}/acme.sh"

    if [[ ! -x "$acme_sh" ]]; then
        log_step "acme" "FAIL" "acme.sh not found at ${acme_sh}"
        return 1
    fi

    log_step "acme" "START" "Issuing TLS certificate via acme.sh (${dns_plugin}) for ${domain} (email: ${email})"

    case "$dns_plugin" in
    webnames)
        local webnames_script="${acme_home}/dnsapi_ext/dns_webnames.sh"
        if [[ ! -f "$webnames_script" ]]; then
            log_step "acme" "FAIL" "dns_webnames.sh not found at ${webnames_script} — ensure regtime-ltd/dnsapi is cloned"
            return 1
        fi

        local api_key="${WEBNAMES_API_KEY:-}"
        if [[ -z "$api_key" ]]; then
            log_step "acme" "FAIL" "WEBNAMES_API_KEY not set in secrets — cannot authenticate to webnames.ru API"
            return 1
        fi

        # ⚠️ TRAP[BUG] · 2026-07-03 · P2 · acme.sh basename bug — PID in temp dir path
        # · acme.sh распознаёт DNS-плагины ТОЛЬКО по короткому имени (dns_webnames)
        # · из директории dnsapi/, а НЕ по полному пути. При передаче полного пути
        # · (--dns /tmp/...) acme.sh молча игнорирует флаг и падает в HTTP-01,
        # · который не поддерживает wildcard-сертификаты (*.domain).
        # · Решение: копируем скрипт с инжектированным API-ключом в dnsapi/
        # · и используем короткое имя — консистентно с generic-case.
        # · Symptom: acme.sh creates temp dirs like /tmp/acme_webnames_$$/ with PID;
        # · Symptom: acme.sh creates temp dirs like /tmp/acme_webnames_$$/ with PID;
        #   calling `--dns /tmp/acme_webnames_12345/dns_webnames.sh` fails because
        #   acme.sh resolves the plugin basename from the full path and ignores it
        # · Root: acme.sh only recognizes plugins by short name (dns_webnames) from
        #   its own dnsapi/ directory, not by full path. The PID in temp dir name
        #   makes the path non-deterministic between runs.
        # · Fix: Copy the temp file explicitly to ${acme_home}/dnsapi/dns_webnames.sh
        #   (deterministic path), then use `--dns dns_webnames` (short name).
        # · Prevention: Always copy dnsapi plugin scripts to acme.sh dnsapi/ dir
        #   with a fixed name before calling --issue with --dns short name.
        # 💼 TRAP[BUSINESS] · 2026-06-11 · HI · API key cleaned from disk after use — security requirement
        # · Risk: plaintext API key on persistent disk is a security vulnerability
        # · Mitigation: key written to tmpfs (/tmp), used for acme.sh, then shredded immediately
        local dnsapi_tmp
        dnsapi_tmp="$(mktemp /tmp/dns_webnames.XXXXXX)"

        # Inject API key into temp file (webnames script has API_KEY hardcoded)
        sed "s|^API_KEY=.*|API_KEY=\"${api_key}\"|" "$webnames_script" > "$dnsapi_tmp"
        chmod +x "$dnsapi_tmp"

        # Copy to acme.sh dnsapi directory — acme.sh recognizes plugins by short name
        # from dnsapi/ directory (not by full path). See TRAP[BUG] above.
        cp "$dnsapi_tmp" "${acme_home}/dnsapi/dns_webnames.sh"

        local -a domain_args=(-d "$domain")
        if [[ "$wildcard" == "true" ]]; then
            domain_args+=(-d "*.${domain}")
        fi
        "$acme_sh" --issue \
            --home "$acme_home" \
            --dns dns_webnames \
            --server letsencrypt \
            --email "$email" \
            "${domain_args[@]}" \
            --keylength ec-256

        local acme_ret=$?

        # Wipe API key from all on-disk locations immediately after acme.sh completes
        shred -u "$dnsapi_tmp" 2>/dev/null || rm -f "$dnsapi_tmp"
        if [[ -f "${acme_home}/dnsapi/dns_webnames.sh" ]]; then
            shred -u "${acme_home}/dnsapi/dns_webnames.sh" 2>/dev/null || rm -f "${acme_home}/dnsapi/dns_webnames.sh"
        fi

        if [[ $acme_ret -ne 0 ]]; then
            log_step "acme" "FAIL" "acme.sh --issue exited with ${acme_ret}"
            return 1
        fi
        ;;

    *)
        # Generic DNS-01: использует стандартный плагин acme.sh из dnsapi/
        # Креды передаются через env vars (конвенция acme.sh: CF_Token, DP_Id и т.д.)
        local -a domain_args=(-d "$domain")
        if [[ "$wildcard" == "true" ]]; then
            domain_args+=(-d "*.${domain}")
        fi
        "$acme_sh" --issue \
            --home "$acme_home" \
            --dns "dns_${dns_plugin}" \
            --server letsencrypt \
            --email "$email" \
            "${domain_args[@]}" \
            --keylength ec-256

        if [[ $? -ne 0 ]]; then
            log_step "acme" "FAIL" "acme.sh --issue (generic dns_${dns_plugin}) failed"
            return 1
        fi
        ;;
    esac

    # Install cert to configurable location for nginx (/etc/letsencrypt/live/ by default)
    # LETSENCRYPT_DIR allows test environments to set a temp dir (see test_nginx_acme.py)
    local cert_root="${LETSENCRYPT_DIR:-/etc/letsencrypt}"
    local cert_dir="${cert_root}/live/${domain}"
    mkdir -p "$cert_dir"

    "$acme_sh" --install-cert -d "$domain" \
        --home "$acme_home" \
        --key-file "${cert_dir}/privkey.pem" \
        --fullchain-file "${cert_dir}/fullchain.pem" \
        --reloadcmd "systemctl reload nginx"

    log_step "acme" "DONE" "TLS certificate installed via acme.sh: ${cert_dir}/fullchain.pem"
}
# endregion ACME_ISSUE

# region ACME_INSTALL_CRON
## @purpose  Install acme.sh cronjob for automatic daily certificate renewal
## @scope    Idempotent: skips if cron entry already exists in crontab
## @rationale  Per D-5: industry-standard cron mechanism (10+ years in production);
##   acme.sh manages the cron task internally, checks certs daily, renews within 30 days
## @invariants
##   - acme.sh --install-cronjob is idempotent — running multiple times is safe
##   - Must be called AFTER certificate is issued (no cert → cron has nothing to renew)
##   - Uses the --home flag to point to our /opt/acme.sh installation
_acme_install_cron() {
    local acme_home="${ACME_HOME:-/opt/acme.sh}"
    local acme_sh="${acme_home}/acme.sh"

    if [[ ! -x "$acme_sh" ]]; then
        log_step "acme-cron" "SKIP" "acme.sh not installed at ${acme_sh} — cannot install cronjob"
        return 1
    fi

    # [IMP:9][nginx-install][acme-cron] BUSINESS INVARIANT: idempotency — skip if already installed
    if crontab -l 2>/dev/null | grep -q "${acme_sh}.*--cron"; then
        log_step "acme-cron" "SKIP" "acme.sh cronjob already installed (idempotent)"
        return 0
    fi

    log_step "acme-cron" "START" "Installing acme.sh cronjob for daily certificate renewal"

    # acme.sh --install-cronjob creates a daily crontab entry that calls
    # acme.sh --cron to check and renew certs expiring within 30 days.
    # Per D-5: standard cron mechanism chosen over systemd timer (rejected).
    "$acme_sh" --install-cronjob --home "$acme_home" 2>&1 || {
        local cron_err=$?
        log_step "acme-cron" "FAIL" "acme.sh --install-cronjob exited with code ${cron_err}"
        return 1
    }

    # [IMP:9][nginx-install][acme-cron] Verify cronjob was actually installed
    if crontab -l 2>/dev/null | grep -q "${acme_sh}.*--cron"; then
        log_step "acme-cron" "DONE" "acme.sh cronjob installed — daily renewal active"
    else
        log_step "acme-cron" "FAIL" "Cronjob not found after acme.sh --install-cronjob — check crontab manually"
        return 1
    fi
}
# endregion ACME_INSTALL_CRON

# region ACME_VERIFY_CERT
## @purpose  Verify TLS certificate expiry — ensure >30 days remaining
## @scope    Validation function; called after cert issuance or from main()
## @rationale  AC-8 requires cert to be valid >30 days; openssl x509 parsing
##   is the reliable way to read the actual cert (not acme.sh metadata)
## @invariants
##   - Uses openssl x509 — the cert file itself is the source of truth
##   - Returns non-zero if cert missing, unreadable, or expires within 30 days
##   - Does NOT re-issue the cert (read-only check)
_acme_verify_cert() {
    local domain="$1"
    local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"

    if [[ ! -f "$cert_path" ]]; then
        log_step "acme-verify" "FAIL" "No certificate found at ${cert_path}"
        return 1
    fi

    # [IMP:9][nginx-install][acme-verify] BUSINESS INVARIANT: cert must be valid >30 days
    local expiry_date
    expiry_date="$(openssl x509 -enddate -noout -in "$cert_path" 2>/dev/null | cut -d= -f2-)"
    if [[ -z "$expiry_date" ]]; then
        log_step "acme-verify" "FAIL" "Failed to parse certificate expiry from ${cert_path}"
        return 1
    fi

    local expiry_epoch
    expiry_epoch="$(date -d "$expiry_date" +%s 2>/dev/null)" || {
        log_step "acme-verify" "FAIL" "Failed to convert expiry date '${expiry_date}' to epoch"
        return 1
    }

    local now_epoch
    now_epoch="$(date +%s)"
    local days_remaining=$(( (expiry_epoch - now_epoch) / 86400 ))

    if [[ $days_remaining -le 30 ]]; then
        log_step "acme-verify" "WARN" "Certificate expires in ${days_remaining} days — less than 30 day threshold"
        return 1
    fi

    log_step "acme-verify" "DONE" "Certificate valid — expires in ${days_remaining} days (threshold: >30)"
    return 0
}
# endregion ACME_VERIFY_CERT

# region _IS_SUBDOMAIN
## @purpose  Check if domain is a subdomain of parent domain
## @param    $1  domain   Domain to check (e.g. app.tronyx.ru)
## @param    $2  parent   Parent domain (e.g. tronyx.ru)
## @return   0 if domain is a subdomain of parent, 1 otherwise
## @invariants
##   - Empty domain or parent → return 1
##   - Exact match (domain == parent) → return 1 (not a subdomain)
##   - Subdomain check uses suffix matching: domain == *.parent
_is_subdomain() {
    local domain="$1"
    local parent="$2"
    [[ -z "$domain" || -z "$parent" ]] && return 1
    [[ "$domain" == *".${parent}" ]] && return 0
    return 1
}
# endregion _IS_SUBDOMAIN

# region _ISSUE_PROJECT_CERTS
## @purpose  Issue Let's Encrypt certificates for independent project domains
## @param    $1  platform_domain  Platform domain (for subdomain detection)
## @param    $2  email            Email for Let's Encrypt registration
## @param    $3  dns_plugin       DNS plugin name (webnames or generic)
## @env      PLATFORM_PROJECT_DOMAINS  Space-separated list of project domains
## @invariants
##   - Skips domains that are subdomains of platform_domain (covered by wildcard)
##   - Issues single-domain certs (wildcard=false) for independent domains
##   - Idempotent: issue_tls_cert() skips if cert already exists
##   - Non-fatal: failure for one domain does not stop processing others
## @rationale  Project domains use single-domain LE certs to avoid LE rate limit issues
_issue_project_certs() {
    local platform_domain="${1:-}"
    local email="${2:-}"
    local dns_plugin="${3:-}"
    local project_domains="${PLATFORM_PROJECT_DOMAINS:-}"

    if [[ -z "$project_domains" ]]; then
        log_step "project-certs" "SKIP" "No PLATFORM_PROJECT_DOMAINS — nothing to issue"
        return 0
    fi

    if [[ -z "$dns_plugin" ]]; then
        log_step "project-certs" "SKIP" "No DNS plugin configured — cannot issue project certs"
        return 0
    fi

    log_step "project-certs" "START" "Processing project domains: ${project_domains}"

    local issued=0 skipped=0
    for domain in $project_domains; do
        [[ -z "$domain" ]] && continue

        if [[ -n "$platform_domain" ]] && _is_subdomain "$domain" "$platform_domain"; then
            log_step "project-certs" "SKIP" "${domain} — subdomain of ${platform_domain}, covered by wildcard"
            skipped=$((skipped + 1))
            continue
        fi

        log_step "project-certs" "INFO" "Issuing single-domain cert for: ${domain}"
        if issue_tls_cert "$domain" "$email" "$dns_plugin" "false"; then
            issued=$((issued + 1))
        else
            log_step "project-certs" "WARN" "Failed to issue cert for ${domain} — continuing"
        fi
    done

    log_step "project-certs" "DONE" "Project certs: issued=${issued} skipped=${skipped}"
}
# endregion _ISSUE_PROJECT_CERTS

# region VERIFY_WILDCARD_SAN
## @purpose  Post-issue validation: verify TLS certificate contains wildcard SAN (*.domain)
## @scope    Called after cert issuance, before deploying HTTPS vhost configs
## @param    domain  Domain name to check wildcard for
## @rationale  Pre-deploy gate cannot check actual cert on VPS. Post-issue openssl check
##   is the only way to guarantee wildcard SAN before deploying HTTPS configs.
## @invariants
##   - FAIL if cert file missing or unreadable
##   - FAIL if SAN does not contain *.domain (no wildcard)
##   - Uses openssl x509 to verify the actual cert on disk
_verify_wildcard_san() {
    local domain="$1"

    if [[ -z "$domain" ]]; then
        log_step "verify-san" "SKIP" "No domain specified — skipping wildcard SAN verification"
        return 0
    fi

    local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
    if [[ ! -f "$cert_path" ]]; then
        log_step "verify-san" "FAIL" "No certificate found at ${cert_path} — cannot verify wildcard SAN"
        return 1
    fi

    log_step "verify-san" "START" "Verifying wildcard SAN (*.${domain}) in certificate: ${cert_path}"

    # [IMP:9][nginx-install][verify-san] BUSINESS INVARIANT: SAN must contain *.domain
    if openssl x509 -in "$cert_path" -noout -text 2>/dev/null | grep -q "DNS:\*\.${domain}"; then
        log_step "verify-san" "DONE" "Wildcard SAN (*.${domain}) confirmed in certificate: ${cert_path}"
        return 0
    fi

    # Diagnostic output: show actual SAN entries to help debug
    local actual_san
    actual_san="$(openssl x509 -in "$cert_path" -noout -text 2>/dev/null | grep -A1 "Subject Alternative Name" | tail -1)"
    log_step "verify-san" "FAIL" "Wildcard SAN (*.${domain}) NOT found in certificate"
    log_step "verify-san" "DIAG" "Actual SAN entries: ${actual_san:-unreadable}"
    return 1
}
# endregion VERIFY_WILDCARD_SAN

# region WRITE_CONFIG_ATOMIC
# 🧐 TRAP[DECISION] · 2026-06-11 · — · Atomic config deploy: mv (rename), not cp (copy)
# · Rejected: cp + reload · Reason: mv атомарен на одном FS — nginx либо видит старый файл, либо новый;
#   cp создаёт окно, где файл существует частично (nginx читает половину)
# · Rev: если конфиги переедут на отдельный раздел, mv не атомарен → нужен symlink swap
# ⚠️ TRAP[BUG] · 2026-06-07 · HI · nginx -t тестировал СТАРЫЙ конфиг — .testcopy не включался
# · Старый подход копировал новый конфиг в «dst.testcopy» и запускал nginx -t.
# · Но nginx включает только *.conf (include /etc/nginx/conf.d/*.conf),
# · поэтому .testcopy игнорировался, и тестировался СТАРЫЙ (сломанный) конфиг.
# · Результат: конфиг с ошибкой atomically replace'ился, nginx падал при reload.
# · Новый подход: backup → replace → nginx -t → rollback при ошибке.
write_config_atomic() {
    local src="$1"
    local dst="$2"
    local tmp="${dst}.tmp.$$"
    local bak="${dst}.bak.$$"

    log_step "config" "START" "Writing config: ${dst}"

    # Prepare new config
    cp "$src" "$tmp"

    # Backup current config if it exists, then place new config for testing
    if [[ -f "$dst" ]]; then
        cp "$dst" "$bak"
    fi

    # [IMP:9][nginx-install][config] Place new config at destination and run nginx -t.
    # If test fails, restore backup — never leave broken config on disk.
    cp "$tmp" "$dst"
    if nginx -t &>/dev/null 2>&1; then
        # Config valid — set permissions for platform user readability
        # [IMP:8][nginx-install][config] chmod 644 ensures platform user can read conf.d/*.conf
        chmod 644 "$dst"
        # cleanup backup and temp
        rm -f "$bak" "$tmp"
        log_step "config" "DONE" "Config validated and atomically replaced: ${dst}"
    else
        # Config invalid — capture error, restore backup, abort
        local nginx_err
        nginx_err="$(nginx -t 2>&1 || true)"
        log_step "config" "FAIL" "nginx -t FAILED for new config — rolling back"
        log_step "config" "ERROR" "nginx error: ${nginx_err}"

        if [[ -f "$bak" ]]; then
            cp "$bak" "$dst"
            rm -f "$bak"
            log_step "config" "ROLLBACK" "Previous config restored: ${dst}"
        else
            rm -f "$dst"
            log_step "config" "ROLLBACK" "No backup — removed broken config: ${dst}"
        fi
        rm -f "$tmp"
        return 1
    fi
}
# endregion WRITE_CONFIG_ATOMIC

# region DEPLOY_CONFIG
## @brief  Deploy base nginx.conf, error pages, and vhost config.
## @note   Vhost selection: if TLS cert exists → full HTTPS config (platform-default.conf);
##         otherwise → HTTP-only config (platform-http.conf, safe to load without cert).
##         This avoids nginx startup failure when cert files are missing (chicken-and-egg).
# region REMOVE_UBUNTU_DEFAULT_SITE
# ⚠️ TRAP[BUG] · 2026-06-25 · HI · Duplicate default_server conflict with Ubuntu default site
# · Symptom: nginx -t FAILS with "a duplicate default server for 0.0.0.0:80 in
# ·   /etc/nginx/sites-enabled/default:22" when our platform-http.conf also has
# ·   "listen 80 default_server"
# · Root: Ubuntu's nginx package creates /etc/nginx/sites-enabled/default with
# ·   a default_server directive. Our nginx.conf does NOT include sites-enabled/,
# ·   but the OLD Ubuntu nginx.conf does — and nginx -t tests ALL included files
# ·   during write_config_atomic, when our nginx.conf hasn't been deployed yet.
# · Fix: Remove Ubuntu default site symlink before deploying our vhost config.
# ·   Idempotent — skipped if file doesn't exist (already removed on prior run).
_remove_ubuntu_default_site() {
    local default_site="/etc/nginx/sites-enabled/default"
    if [[ -f "$default_site" ]]; then
        rm -f "$default_site"
        log_step "config" "INFO" "Removed Ubuntu default site: ${default_site}"
        # Also remove the source file in sites-available to prevent accidental re-enable
        local default_available="/etc/nginx/sites-available/default"
        [[ -f "$default_available" ]] && rm -f "$default_available"
    else
        log_step "config" "SKIP" "Ubuntu default site already removed"
    fi
}
# endregion REMOVE_UBUNTU_DEFAULT_SITE

deploy_nginx_config() {
    local domain="${1:-}"
    local overlay_dir="${2:-}"

    log_step "deploy-config" "START" "Deploying nginx configuration"

    # Remove Ubuntu default site to avoid duplicate default_server conflict
    _remove_ubuntu_default_site

    # ⚠️ TRAP[BUG] · 2026-07-03 · HI · Duplicate default_server when domain is set
    # · Symptom: nginx -t FAILS with "a duplicate default server for 0.0.0.0:80 in
    # ·   /etc/nginx/conf.d/tronyx.ru.conf:13" when PLATFORM_DOMAIN is set and
    # ·   old platform-default.conf still exists.
    # · Root: When domain IS set, vhost_dst becomes <domain>.conf. But the OLD
    # ·   platform-default.conf (from a previous run without domain) remains on disk
    # ·   with "listen 80 default_server". The new HTTP-only vhost (platform-http.conf)
    # ·   has the SAME default_server directive → duplicate → nginx -t fails.
    # · Fix: Remove stale platform-default.conf when deploying a domain-specific vhost.
    # ·   Add: [[ -n "$domain" ]] && rm -f /etc/nginx/conf.d/platform-default.conf
    # · Workaround: Manually rm -f /etc/nginx/conf.d/platform-default.conf before re-run.
    # · Impact: Blocks install.sh when migrating from no-domain to domain-based config.
    if [[ -n "$domain" ]] && [[ -f "/etc/nginx/conf.d/platform-default.conf" ]]; then
        rm -f "/etc/nginx/conf.d/platform-default.conf"
        log_step "config" "INFO" "Removed stale platform-default.conf (migrating to domain-specific vhost: ${domain}.conf)"
    fi

    # Ensure conf.d directory exists
    mkdir -p /etc/nginx/conf.d

    # ⚠️ TRAP[BUG] · 2026-06-07 · HI · Vhost MUST be deployed BEFORE nginx.conf
    # · write_config_atomic runs nginx -t (which tests ALL included files).
    # · If the old vhost is broken (from a prior failed deploy), nginx -t fails
    # · and the atomic write rolls back. Deploying the new valid vhost first
    # · ensures nginx -t passes when nginx.conf is deployed next.

    # ── Phase A: Deploy vhost first (replaces potentially broken old vhost) ──
    local vhost_dst
    if [[ -n "$domain" ]]; then
        vhost_dst="/etc/nginx/conf.d/${domain}.conf"
    else
        vhost_dst="/etc/nginx/conf.d/platform-default.conf"
    fi

    local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
    # 🧐 TRAP[DECISION] · 2026-06-11 · — · Chicken-and-egg vhost deploy: HTTP first, then HTTPS
    # · Rejected: deploy HTTPS config immediately · Reason: acme.sh DNS-01 needs cert first;
    # ·   nginx не запустится с SSL-директивой, если сертификата ещё нет;
    # ·   двухфазный деплой (HTTP → cert → HTTPS) гарантирует атомарность
    # · Rev: если перейти на DNS-01 challenge (acme.sh), HTTP-фаза не нужна
    if [[ -n "$domain" ]] && [[ -f "$cert_path" ]]; then
        log_step "deploy-config" "INFO" "TLS cert found — deploying full HTTPS vhost"
        # 🧐 TRAP[DECISION] · 2026-06-28 · — · Vhost deploy failure is non-fatal for nginx install
        # · Rejected: abort on vhost failure · Reason: vhost failure (e.g., upstream DNS) blocks
        # ·   deployment of subdomain vhosts (grafana, hermes) and json_combined log format.
        # ·   Non-fatal approach ensures observability subdomains are deployed even if
        # ·   main vhost overlay has temporary issues (container not running).
        # · Rev: if vhost failures indicate systemic issues, consider fail-fast
        _deploy_vhost_full "$domain" "$overlay_dir" "$vhost_dst" || \
            log_step "deploy-config" "WARN" "Full vhost deploy failed — nginx will continue with previous config"
    else
        log_step "deploy-config" "INFO" "No TLS cert yet — deploying HTTP-only vhost"
        _deploy_vhost_http "$vhost_dst"
    fi

    # ── Phase B: Deploy base nginx.conf (now safe — vhost is valid) ─────
    local nginx_conf_src="${SCRIPT_DIR}/config/nginx.conf"
    if [[ -f "$nginx_conf_src" ]]; then
        write_config_atomic "$nginx_conf_src" "/etc/nginx/nginx.conf"
    fi

    # ── Deploy default static root and error pages ─────────────────────
    _deploy_static_assets

    # ── Deploy shared snippets (security-headers + SSL params) ─────────
    # audit 013: security-headers.conf → /etc/nginx/includes/
    #            ssl-params.conf.template → /etc/nginx/conf.d/ (sed PLATFORM_DOMAIN)
    _deploy_shared_snippets "$domain"

    log_step "deploy-config" "DONE" "nginx config deployed"
}

# region _DEPLOY_SHARED_SNIPPETS
## @brief  Deploy shared nginx snippet files (security-headers.conf, ssl-params.conf).
## @param  domain  Domain name for ${PLATFORM_DOMAIN} substitution in ssl-params.conf.template
## @note   security-headers.conf has no PLATFORM_DOMAIN placeholders — plain copy.
##         ssl-params.conf.template requires sed substitution for PLATFORM_DOMAIN.
## @changes 2026-07-18 · audit 013 · Added for security hardening wave 1.
_deploy_shared_snippets() {
    local domain="$1"
    local includes_dir="/etc/nginx/includes"

    # ── security-headers.conf (no substitution needed) ────────────────────
    local headers_src="${SCRIPT_DIR}/config/security-headers.conf"
    if [[ -f "$headers_src" ]]; then
        mkdir -p "$includes_dir"
        if [[ ! -f "${includes_dir}/security-headers.conf" ]] || \
           ! diff -q "$headers_src" "${includes_dir}/security-headers.conf" &>/dev/null; then
            cp "$headers_src" "${includes_dir}/security-headers.conf"
            chmod 644 "${includes_dir}/security-headers.conf"
            log_step "shared-snippets" "DONE" "security-headers.conf deployed to ${includes_dir}/"
        else
            log_step "shared-snippets" "SKIP" "security-headers.conf already up-to-date"
        fi
    else
        log_step "shared-snippets" "WARN" "security-headers.conf not found at ${headers_src}"
    fi

    # ── ssl-params.conf (requires PLATFORM_DOMAIN substitution) ───────────
    local ssl_src="${SCRIPT_DIR}/config/ssl-params.conf.template"
    local ssl_dst="/etc/nginx/conf.d/ssl-params.conf"
    if [[ -f "$ssl_src" ]]; then
        if [[ -n "$domain" ]]; then
            local rendered
            rendered="$(mktemp)"
            sed "s|\${PLATFORM_DOMAIN}|${domain}|g" "$ssl_src" > "$rendered"
            write_config_atomic "$rendered" "$ssl_dst"
            rm -f "$rendered"
        else
            # No domain — copy as-is (PLATFORM_DOMAIN stays unresolved, may break)
            log_step "shared-snippets" "WARN" "No PLATFORM_DOMAIN set — ssl-params.conf deployed with unresolved placeholder"
            write_config_atomic "$ssl_src" "$ssl_dst"
        fi
    else
        log_step "shared-snippets" "WARN" "ssl-params.conf.template not found at ${ssl_src}"
    fi
}
# endregion _DEPLOY_SHARED_SNIPPETS

# region _DEPLOY_VHOST_FULL
## @brief  Deploy full HTTPS vhost config (platform-default.conf) with domain substitution.
## @param  domain       Domain name for ${PLATFORM_DOMAIN} substitution
## @param  overlay_dir  Optional node-specific overlay directory
## @param  dst          Destination path (e.g. /etc/nginx/conf.d/tronyx.ru.conf)
_deploy_vhost_full() {
    local domain="$1"
    local overlay_dir="${2:-}"
    local dst="$3"

    local vhost_src="${SCRIPT_DIR}/config/platform-default.conf"
    if [[ ! -f "$vhost_src" ]]; then
        log_step "deploy-config" "FAIL" "Full vhost template not found: ${vhost_src}"
        return 1
    fi

    # Apply node-specific overlay if provided
    if [[ -n "$overlay_dir" ]] && [[ -f "${overlay_dir}/nginx.conf" ]]; then
        write_config_atomic "${overlay_dir}/nginx.conf" "$dst"
        return 0
    fi

    # Substitute ${PLATFORM_DOMAIN} with actual domain via sed
    local resolved_src
    resolved_src="$(mktemp)"
    sed "s|\${PLATFORM_DOMAIN}|${domain}|g" "$vhost_src" > "$resolved_src"
    write_config_atomic "$resolved_src" "$dst"
    rm -f "$resolved_src"
}
# endregion _DEPLOY_VHOST_FULL

# region _DEPLOY_VHOST_HTTP
## @brief  Deploy HTTP-only vhost config (platform-http.conf) — safe when TLS cert is absent.
## @param  dst  Destination path
_deploy_vhost_http() {
    local dst="$1"

    local vhost_src="${SCRIPT_DIR}/config/platform-http.conf"
    if [[ ! -f "$vhost_src" ]]; then
        log_step "deploy-config" "FAIL" "HTTP-only vhost template not found: ${vhost_src}"
        return 1
    fi

    write_config_atomic "$vhost_src" "$dst"
}
# endregion _DEPLOY_VHOST_HTTP

# region _DEPLOY_STATIC_ASSETS
## @brief  Deploy default index.html and custom error pages to /var/www/platform-default.
_deploy_static_assets() {
    local www_root="/var/www/platform-default"
    mkdir -p "${www_root}/error-pages"

    # Default index.html (placeholder until apps are deployed)
    local index_src="${SCRIPT_DIR}/error-pages/index.html"
    if [[ -f "$index_src" ]]; then
        cp "$index_src" "${www_root}/index.html"
        log_step "deploy-config" "DONE" "Default index.html deployed"
    fi

    # Error pages (404, 50x, maintenance)
    local error_src_dir="${SCRIPT_DIR}/error-pages"
    if [[ -d "$error_src_dir" ]]; then
        for page in 404.html 50x.html maintenance.html; do
            if [[ -f "${error_src_dir}/${page}" ]]; then
                cp "${error_src_dir}/${page}" "${www_root}/error-pages/${page}"
            fi
        done
        log_step "deploy-config" "DONE" "Error pages deployed (404, 50x, maintenance)"
    fi
}
# endregion _DEPLOY_STATIC_ASSETS

# region INSTALL_HTPASSWD_MONITORING
## @brief  Create/update .htpasswd-monitoring for Prometheus/Loki Basic Auth.
## @note   Uses openssl apr1 (APR1) hash — no apache2-utils dependency.
##         openssl is already present on the server (nginx dependency).
##         Idempotent: does nothing if file exists with correct hash.
_install_htpasswd_monitoring() {
    local htpasswd_file="/etc/nginx/conf.d/.htpasswd-monitoring"
    local username="${MONITORING_AUTH_USER:-Tronyx}"
    local password="${MONITORING_AUTH_PASSWORD:?MONITORING_AUTH_PASSWORD not set}"

    # Check if file exists and has correct credentials (idempotency)
    if [[ -f "$htpasswd_file" ]]; then
        if grep -q "^${username}:" "$htpasswd_file" 2>/dev/null; then
            log_step "htpasswd-monitoring" "SKIP" "htpasswd file already exists for user ${username}"
            chmod 644 "$htpasswd_file"
            return 0
        fi
    fi

    log_step "htpasswd-monitoring" "START" "Creating htpasswd file for ${username}"

    # Generate APR1 hash using openssl (available on all Ubuntu/Debian systems)
    local hash
    hash="$(openssl passwd -apr1 "$password" 2>/dev/null)" || {
        log_step "htpasswd-monitoring" "FAIL" "openssl passwd failed — is openssl installed?"
        return 1
    }

    # Write htpasswd file
    printf '%s:%s\n' "$username" "$hash" > "$htpasswd_file"
    chmod 644 "$htpasswd_file"

    log_step "htpasswd-monitoring" "DONE" "htpasswd file created: ${htpasswd_file}"
}
# endregion INSTALL_HTPASSWD_MONITORING

# endregion DEPLOY_CONFIG

# region DEPLOY_HERMES_DASHBOARD
## @brief  Deploy Hermes Dashboard vhost config (hermes-dashboard.conf) if PLATFORM_HERMES_ENABLED=true.
## @param  domain  Domain name for ${PLATFORM_DOMAIN} substitution
## @note   Uses write_config_atomic for idempotent deploy + nginx -t validation.
##         Only deploys when TLS certificate exists (same wildcard cert covers hermes subdomain).
deploy_hermes_dashboard() {
    local domain="${1:-}"

    if [[ "${PLATFORM_HERMES_ENABLED:-}" != "true" ]]; then
        log_step "hermes-dashboard" "SKIP" "PLATFORM_HERMES_ENABLED != true — hermes dashboard vhost not deployed"
        return 0
    fi

    if [[ -z "$domain" ]]; then
        log_step "hermes-dashboard" "SKIP" "No domain specified — cannot deploy hermes dashboard vhost"
        return 0
    fi

    # [IMP:9][nginx-install][hermes-dashboard] Require TLS cert — wildcard covers hermes subdomain
    local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
    if [[ ! -f "$cert_path" ]]; then
        log_step "hermes-dashboard" "SKIP" "TLS cert not yet present at ${cert_path} — will deploy on next run"
        return 0
    fi

    local template="${SCRIPT_DIR}/config/hermes-dashboard.conf"
    if [[ ! -f "$template" ]]; then
        log_step "hermes-dashboard" "FAIL" "Template not found: ${template}"
        return 1
    fi

    local dst="/etc/nginx/conf.d/hermes.${domain}.conf"

    # Check if already deployed and identical (idempotency optimization)
    if [[ -f "$dst" ]]; then
        local rendered_check
        rendered_check="$(mktemp)"
        sed "s|\${PLATFORM_DOMAIN}|${domain}|g" "$template" > "$rendered_check"
        if diff -q "$dst" "$rendered_check" &>/dev/null; then
            rm -f "$rendered_check"
            log_step "hermes-dashboard" "SKIP" "Config already up-to-date: ${dst}"
            return 0
        fi
        rm -f "$rendered_check"
    fi

    log_step "hermes-dashboard" "START" "Deploying hermes dashboard vhost: ${dst}"

    local rendered_src
    rendered_src="$(mktemp)"
    sed "s|\${PLATFORM_DOMAIN}|${domain}|g" "$template" > "$rendered_src"
    write_config_atomic "$rendered_src" "$dst"
    local ret=$?
    rm -f "$rendered_src"

    if [[ $ret -eq 0 ]]; then
        log_step "hermes-dashboard" "DONE" "Hermes dashboard vhost deployed: ${dst}"
    fi

    return $ret
}
# endregion DEPLOY_HERMES_DASHBOARD

# region DEPLOY_VHOST
## @brief  Deploy a vhost config from template with idempotency check and atomic write.
## @param  $1  template  Template name (grafana|langfuse|prometheus|loki)
## @param  $2  dst       Destination path for the rendered config
## @param  $3  domain    Domain name for ${PLATFORM_DOMAIN} substitution
## @note   Unified parametrized replacement for 4 duplicate functions
##         (deploy_grafana_vhost, deploy_langfuse_vhost,
##          deploy_prometheus_vhost, deploy_loki_vhost).
##         All 4 functions had identical guard + render + idempotency logic.
##         Single difference: grafana/langfuse check TLS cert before deploying,
##         prometheus/loki do not (they use htpasswd basic auth).
deploy_vhost() {
    local template="$1"
    local dst="$2"
    local domain="${3:-}"

    if [[ "${PLATFORM_OBSERVABILITY_ENABLED:-}" != "true" ]]; then
        log_step "vhost" "SKIP" "PLATFORM_OBSERVABILITY_ENABLED != true — ${template} vhost not deployed"
        return 0
    fi

    if [[ -z "$domain" ]]; then
        log_step "vhost" "SKIP" "No domain specified — cannot deploy ${template} vhost"
        return 0
    fi

    # [IMP:9][nginx-install][vhost] TLS cert guard for user-facing vhosts only
    # grafana/langfuse require cert before deploying (original behavior preserved).
    # prometheus/loki do not check cert (they rely on htpasswd basic auth).
    case "$template" in
        grafana|langfuse)
            local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
            if [[ ! -f "$cert_path" ]]; then
                log_step "vhost" "SKIP" "TLS cert not yet present at ${cert_path} — will deploy ${template} vhost on next run"
                return 0
            fi
            ;;
    esac

    local config_src="${SCRIPT_DIR}/config/${template}-vhost.conf"
    if [[ ! -f "$config_src" ]]; then
        log_step "vhost" "FAIL" "Template not found: ${config_src}"
        return 1
    fi

    # Check if already deployed and identical (idempotency optimization)
    if [[ -f "$dst" ]]; then
        local rendered_check
        rendered_check="$(mktemp)"
        sed "s|\${PLATFORM_DOMAIN}|${domain}|g" "$config_src" > "$rendered_check"
        if diff -q "$dst" "$rendered_check" &>/dev/null; then
            rm -f "$rendered_check"
            log_step "vhost" "SKIP" "Config already up-to-date: ${dst}"
            return 0
        fi
        rm -f "$rendered_check"
    fi

    log_step "vhost" "START" "Deploying ${template} vhost: ${dst}"

    local rendered_src
    rendered_src="$(mktemp)"
    sed "s|\${PLATFORM_DOMAIN}|${domain}|g" "$config_src" > "$rendered_src"
    write_config_atomic "$rendered_src" "$dst"
    local ret=$?
    rm -f "$rendered_src"

    if [[ $ret -eq 0 ]]; then
        log_step "vhost" "DONE" "${template} vhost deployed: ${dst}"
    fi

    return $ret
}
# endregion DEPLOY_VHOST




# region ENABLE_SERVICE
enable_service() {
    log_step "service" "START" "Enabling and starting nginx"
    systemctl enable nginx --quiet
    if ! systemctl is-active nginx &>/dev/null; then
        systemctl start nginx
        # ⚠️ TRAP[BUG] · 2026-07-08 · HI · systemctl start может вернуть 0 (job accepted), но сервис упал
        # · systemctl start может вернуть 0 (job accepted), но сервис реально упал.
        # · Явно опрашиваем is-active для проверки фактического состояния.
        if ! systemctl is-active --quiet nginx; then
            local journal_tail
            journal_tail="$(journalctl -xeu nginx --no-pager -n 10 2>/dev/null || true)"
            log_step "service" "FAIL" "nginx failed to start after systemctl start — journal tail: ${journal_tail}"
            return 1
        fi
        log_step "service" "DONE" "nginx started"
    else
        # [IMP:8][nginx-install][service] reload (not restart) for zero-downtime config apply
        systemctl reload nginx || {
            log_step "service" "FAIL" "nginx reload failed — config may be invalid"
            return 1
        }
        log_step "service" "DONE" "nginx reloaded (already active)"
    fi
}
# endregion ENABLE_SERVICE

# region ACME_TLS
# 💼 TRAP[BUSINESS] · 2026-07-04 · HI · Wildcard TLS mandatory for all subdomains
# · Source: platform architecture — все сервисы на поддоменах *.domain
# · Risk: HTTP-01 cert без wildcard ломает все поддомены. Платформа неработоспособна.
# · Protection: HTTP-01 ветка удалена; post-issue openssl verify; gate test проверяет конфиги.
issue_tls_cert() {
    local domain="$1"
    local email="$2"
    local dns_plugin="${3:-}"
    local wildcard="${4:-true}"

    if [[ -z "$domain" ]]; then
        log_step "acme.sh" "SKIP" "No domain specified — skipping TLS certificate"
        return 0
    fi

    # [IMP:9][nginx-install][acme.sh] Idempotency: do NOT re-issue existing valid certificate
    local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
    if [[ -f "$cert_path" ]]; then
        log_step "acme.sh" "SKIP" "Certificate already exists: ${cert_path}"
        return 0
    fi

    # [IMP:9][nginx-install][acme.sh] BUSINESS INVARIANT: DNS plugin required for wildcard cert
    if [[ -z "$dns_plugin" ]]; then
        log_step "tls" "FAIL" "TLS certificate requires DNS plugin (set PLATFORM_ACME_DNS_PLUGIN in env)"
        return 1
    fi

    # [IMP:9][nginx-install][acme.sh] BUSINESS INVARIANT: WEBNAMES_API_KEY required for webnames
    if [[ "$dns_plugin" == "webnames" ]] && [[ -z "${WEBNAMES_API_KEY:-}" ]]; then
        log_step "tls" "FAIL" "WEBNAMES_API_KEY not set — required for wildcard TLS via acme.sh DNS-01"
        return 1
    fi

    log_step "acme.sh" "START" "Issuing TLS certificate for ${domain} (email: ${email}) via acme.sh DNS-01 (${dns_plugin})"

    _issue_acme_cert "$domain" "$email" "$dns_plugin" "$wildcard"
}
# endregion ACME_TLS

main() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[IMP:10][nginx-install][main] ERROR: must run as root" >&2
        exit 1
    fi

    local domain="${PLATFORM_DOMAIN:-}"
    local email="${PLATFORM_EMAIL:-}"
    if [[ -z "$email" ]]; then
        log_step "main" "FAIL" "PLATFORM_EMAIL not set — required for Let's Encrypt registration"
        exit 1
    fi
    local dns_plugin="${PLATFORM_ACME_DNS_PLUGIN:-}"
    local overlay_dir="${PLATFORM_CONFIG_OVERLAY:-}"

    install_packages
    # ⚠️ TRAP[BUG] · 2026-07-08 · HI · acme.sh нужен ДО issue_tls_cert
    # · acme.sh — единственный ACME-клиент для DNS-провайдеров (webnames и др.).
    # · Нефатально: если acme.sh не установился — wildcard-сертификат не будет
    # · выпущен. acme.sh DNS-01 обязателен для wildcard.
    install_acme || true

    # ⚠️ TRAP[BUG] · 2026-06-07 · HI · Двухфазный деплой решает проблему курицы-яйца
    # · фаза 1 — HTTP-only конфиг (безопасен без сертификата) → старт nginx →
    # · фаза 2 — выпуск сертификата → фаза 3 — полный HTTPS-конфиг + reload.

    # Phase 1: Deploy nginx config (HTTP-only if no cert yet, full HTTPS if cert exists)
    deploy_nginx_config "$domain" "$overlay_dir"
    enable_service

    # Phase 2: Issue TLS certificate (idempotent — skipped if cert exists)
    issue_tls_cert "$domain" "$email" "$dns_plugin"

    # Phase 2b: Post-issue wildcard SAN verification
    # [IMP:9][nginx-install][main] BUSINESS INVARIANT: wildcard SAN must be present
    # before deploying HTTPS config. If verification fails → exit 1 (do NOT deploy
    # HTTPS vhost with non-wildcard cert — all subdomains would be broken).
    if [[ -n "$domain" ]]; then
        _verify_wildcard_san "$domain" || {
            log_step "main" "FAIL" "Wildcard SAN verification failed — certificate does not cover subdomains"
            exit 1
        }
    fi

    # Phase 2c: Issue certificates for independent project domains
    # [IMP:9][nginx-install][main] BUSINESS INVARIANT: independent project domains get single-domain certs
    # Skips subdomains of PLATFORM_DOMAIN (covered by wildcard cert from Phase 2)
    if [[ -n "${PLATFORM_PROJECT_DOMAINS:-}" ]]; then
        _issue_project_certs "$domain" "$email" "$dns_plugin"
    fi

    # Phase 3: If cert now exists (just issued or already existed), ensure full HTTPS config
    local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
    if [[ -n "$domain" ]] && [[ -f "$cert_path" ]]; then
        local vhost_dst="/etc/nginx/conf.d/${domain}.conf"
        # Check if the deployed vhost already has HTTPS blocks (avoids redundant redeploy)
        if ! grep -q 'listen 443 ssl' "$vhost_dst" 2>/dev/null; then
            log_step "main" "INFO" "Cert ready — redeploying full HTTPS vhost config"
            _deploy_vhost_full "$domain" "$overlay_dir" "$vhost_dst"
            _deploy_static_assets
            if systemctl reload nginx 2>/dev/null; then
                log_step "main" "DONE" "nginx reloaded with full HTTPS config"
            else
                log_step "main" "WARN" "nginx reload after HTTPS deploy had warnings — check config"
            fi
        else
            log_step "main" "INFO" "Full HTTPS config already active — no redeploy needed"
        fi
    fi

    # ── Phase 3b: Install acme.sh cron for automatic renewal (idempotent) ──
    # Per D-5: industry-standard cron mechanism for cert auto-renewal.
    # acme.sh --install-cronjob is idempotent — safe to call every run.
    # Called AFTER cert issuance (Phase 2/3) so cron has a cert to manage.
    # [IMP:9][nginx-install][main] BUSINESS INVARIANT: cron must be installed when TLS cert exists
    if [[ -n "$domain" ]] && [[ -f "$cert_path" ]]; then
        _acme_install_cron || log_step "main" "WARN" "acme cron install failed — cert still valid, renew manually"
    else
        log_step "main" "SKIP" "acme cron not installed — no TLS cert yet (will install on next run)"
    fi

    # ── Phase 3c: Verify certificate expiry >30 days ────────────────────────
    # Per AC-8: cert must be valid for more than 30 days. This is a read-only
    # validation — does NOT re-issue. Warning only (non-fatal) so deployment
    # continues even if cert is near expiry.
    if [[ -n "$domain" ]] && [[ -f "$cert_path" ]]; then
        _acme_verify_cert "$domain" || log_step "main" "WARN" "Certificate expires within 30 days — renew soon"
    fi

    # ── Phase 4: Deploy Hermes Dashboard vhost (conditional) ────────────────
    if deploy_hermes_dashboard "$domain"; then
        # [IMP:8][nginx-install][hermes-dashboard] Reload nginx if hermes dashboard config was deployed
        if systemctl reload nginx; then
            log_step "hermes-dashboard" "DONE" "nginx reloaded with hermes dashboard config"
        else
            log_step "hermes-dashboard" "FAIL" "nginx reload after hermes dashboard deploy FAILED"
        fi
    fi

    # ── Phase 5: Deploy Grafana vhost (conditional) ────────────────────────
    if deploy_vhost "grafana" "/etc/nginx/conf.d/grafana.${domain}.conf" "$domain"; then
        # [IMP:8][nginx-install][grafana-vhost] Reload nginx if grafana config was deployed
        if systemctl reload nginx; then
            log_step "grafana-vhost" "DONE" "nginx reloaded with grafana vhost config"
        else
            log_step "grafana-vhost" "FAIL" "nginx reload after grafana vhost deploy FAILED"
        fi
    fi

    # ── Phase 6: Deploy Langfuse vhost (conditional) ────────────────────────
    if deploy_vhost "langfuse" "/etc/nginx/conf.d/langfuse.${domain}.conf" "$domain"; then
        if systemctl reload nginx; then
            log_step "langfuse-vhost" "DONE" "nginx reloaded with langfuse vhost config"
        else
            log_step "langfuse-vhost" "FAIL" "nginx reload after langfuse vhost deploy FAILED"
        fi
    fi

    # ── Phase 7: Install htpasswd for Prometheus/Loki Basic Auth ───────────
    _install_htpasswd_monitoring || true

    # ── Phase 8: Deploy Prometheus and Loki vhosts (conditional) ──────────
    if [[ "${PLATFORM_OBSERVABILITY_ENABLED:-}" == "true" ]]; then
        if deploy_vhost "prometheus" "/etc/nginx/conf.d/prometheus.${domain}.conf" "$domain"; then
            if systemctl reload nginx; then
                log_step "prometheus-vhost" "DONE" "nginx reloaded with prometheus vhost config"
            else
                log_step "prometheus-vhost" "FAIL" "nginx reload after prometheus vhost deploy FAILED"
            fi
        fi

        if deploy_vhost "loki" "/etc/nginx/conf.d/loki.${domain}.conf" "$domain"; then
            if systemctl reload nginx; then
                log_step "loki-vhost" "DONE" "nginx reloaded with loki vhost config"
            else
                log_step "loki-vhost" "FAIL" "nginx reload after loki vhost deploy FAILED"
            fi
        fi
    fi

    # ── Fallback chmod: ensure ALL conf.d files are readable by platform user ──
    # ⚠️ TRAP[BUG] · 2026-07-08 · HI · write_config_atomic() только chmod'ит изменённые конфиги
    # · Existing configs that are already up-to-date (idempotency skip) retain
    # · their old permissions (e.g., 600 from initial deploy before the fix).
    # · This fallback fixes those without re-deploying unchanged configs.
    # 🧐 TRAP[DECISION] · 2026-07-01 · — · Explicit chmod 644 on all conf.d
    # · Rejected: rely only on write_config_atomic · Reason: idempotency gates
    # ·   prevent write_config_atomic from running on unchanged files.
    # · Rev: if all future deploys go through write_config_atomic, this may be removed.
    # [IMP:9][nginx-install][main] BUSINESS INVARIANT: all conf.d must be 644
    chmod 644 /etc/nginx/conf.d/*.conf 2>/dev/null || true

    # 🧐 TRAP[DECISION] · 2026-06-29 · — · Final unconditional nginx reload after all phases
    # · Rejected: rely only on per-phase reloads · Reason: per-phase reloads may be skipped
    # ·   when idempotency guards falsely detect "no changes" (e.g., config file was
    # ·   deployed outside this script flow via rsync/manual copy). A final reload ensures
    # ·   nginx always picks up any config changes regardless of per-phase SKIP decisions.
    # · Rev: if per-phase idempotency becomes fully reliable, this safety net may be removed.
    if systemctl reload nginx 2>/dev/null; then
        log_step "main" "INFO" "Final nginx reload OK"
    else
        log_step "main" "WARN" "Final nginx reload had warnings — check nginx error.log"
    fi

    log_step "main" "DONE" "nginx module installation complete"
}

main "$@"
