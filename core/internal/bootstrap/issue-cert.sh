#!/usr/bin/env bash
# GREP_SUMMARY: issue-cert, acme.sh, letsencrypt, tls, dns-01, webnames, dnsapi, wildcard-cert, idempotent, cron, cert-expiry, project-certs
# STRUCTURE: ▶ ┌NODE_YAML env┐ → python3 parse → ○ cert exists? → SKIP exit 0 → ◇ validate env → issue_tls_cert → _acme_install_cron → _acme_verify_cert → ◇ _issue_project_certs → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Standalone, idempotent SSL/TLS certificate issuance via acme.sh DNS-01.
##           Handles only certificate issuance, renewal, cron, verification, and project certs.
##           Does NOT install acme.sh — that is install-acme.sh's responsibility (called once at bootstrap).
## @scope    Called from node-lifecycle.sh update step 3, BEFORE docker compose up.
##           Requires acme.sh already installed (via install-acme.sh at bootstrap/init).
## @invariants
##   - Idempotent: if /etc/letsencrypt/live/$domain/fullchain.pem exists → SKIP, exit 0
##   - DNS-01 only — HTTP-01 NOT supported
##   - Requires PLATFORM_ACME_DNS_PLUGIN; webnames plugin needs WEBNAMES_API_KEY (shredded after use)
##   - acme.sh cron installed AFTER cert issuance; cert expiry via openssl x509 (read-only)
##   - LETSENCRYPT_DIR env override supported (for testing)
## @rationale Split from ssl-provision.sh per D3: issue-cert.sh called at each update/renew,
##   install-acme.sh only once at init. Decoupling reduces update latency.
## @changes   CREATED: 2026-07-17 · T3 — Extract from ssl-provision.sh (DevPlan 005)
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/paths.sh"
__LOG_PREFIX="issue-cert"
source "${SCRIPT_DIR}/../../lib/logging.sh"

# NOTE: All functions extracted from ssl-provision.sh. Original TRAP comments preserved.
# The install_acme() function lives in install-acme.sh — this script handles cert issuance only.

# region ACME_ISSUE
## @purpose  Issue a Let's Encrypt TLS certificate via acme.sh DNS-01 challenge
## @param $1  domain        Domain name (e.g., tronyx.ru)
## @param $2  email         Email for Let's Encrypt registration
## @param $3  dns_plugin    DNS plugin name: "webnames" or generic (e.g., "cf", "dp")
## @param $4  wildcard      "true" to issue *.domain wildcard (default), "false" for single-domain
## @invariants
##   - webnames plugin: injects API key via sed into dnsapi script, copies to acme.sh dnsapi/
##   - webnames API key is shredded from ALL on-disk locations immediately after acme.sh completes
##   - Generic DNS-01: uses acme.sh convention (CF_Token, DP_Id env vars)
##   - Cert installed to LETSENCRYPT_DIR/live/$domain/ (default: /etc/letsencrypt)
##   - --reloadcmd is set to "systemctl reload nginx" (preserved from original install.sh)
## ⚠️ TRAP[BUG] · 2026-06-11 · HI · Единственный метод выпуска TLS — acme.sh DNS-01
## · Для webnames.ru инжектит API-ключ в dns_webnames.sh через sed,
## · затем вызывает acme.sh --issue + --install-cert. Использует short plugin name
## · (dns_webnames), а не полный путь (см. TRAP[BUG] acme.sh basename bug ниже).
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

    # [IMP:9][issue-cert][acme-cron] BUSINESS INVARIANT: idempotency — skip if already installed
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

    # [IMP:9][issue-cert][acme-cron] Verify cronjob was actually installed
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

    # [IMP:9][issue-cert][acme-verify] BUSINESS INVARIANT: cert must be valid >30 days
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
## @purpose  Check if domain is a subdomain of parent domain (used by _issue_project_certs)
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
##   - Issues wildcard certs (*.domain) for independent domains (not subdomains of platform)
##   - Idempotent: issue_tls_cert() skips if cert already exists
##   - Non-fatal: failure for one domain does not stop processing others
## @rationale  Independent project domains (not subdomains of platform domain) need
##   their own wildcard certs to support arbitrary subdomains (www, api, etc.).
##   Subdomains of platform domain are covered by the platform wildcard cert.
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

        log_step "project-certs" "INFO" "Issuing wildcard cert for: ${domain}"
        if issue_tls_cert "$domain" "$email" "$dns_plugin" "true"; then
            issued=$((issued + 1))
        else
            log_step "project-certs" "WARN" "Failed to issue cert for ${domain} — continuing"
        fi
    done

    log_step "project-certs" "DONE" "Project certs: issued=${issued} skipped=${skipped}"
}
# endregion _ISSUE_PROJECT_CERTS

# region ACME_TLS
## @purpose  Public wrapper for TLS certificate issuance with guard logic
## @param $1  domain        Domain name
## @param $2  email         Email for Let's Encrypt registration
## @param $3  dns_plugin    DNS plugin name (e.g., webnames, cf, dp)
## @param $4  wildcard      "true" (default) for wildcard *.domain, "false" for single-domain
## @invariants
##   - Domain may be empty → SKIP (not an error — main() handles this case) — RETURN 0
##   - Idempotent: checks /etc/letsencrypt/live/$domain/fullchain.pem — returns 0 if exists
##   - DNS plugin required for wildcard cert issuance — FAIL if empty
##   - webnames plugin requires WEBNAMES_API_KEY env var — FAIL if empty
## 💼 TRAP[BUSINESS] · 2026-07-04 · HI · Wildcard TLS mandatory for all subdomains
## · Source: platform architecture — все сервисы на поддоменах *.domain
## · Risk: HTTP-01 cert без wildcard ломает все поддомены. Платформа неработоспособна.
## · Protection: HTTP-01 ветка удалена; post-issue openssl verify; gate test проверяет конфиги.
issue_tls_cert() {
    local domain="$1"
    local email="$2"
    local dns_plugin="${3:-}"
    local wildcard="${4:-true}"

    if [[ -z "$domain" ]]; then
        log_step "acme.sh" "SKIP" "No domain specified — skipping TLS certificate"
        return 0
    fi

    # [IMP:9][issue-cert][acme.sh] Idempotency: do NOT re-issue existing valid certificate
    local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
    if [[ -f "$cert_path" ]]; then
        log_step "acme.sh" "SKIP" "Certificate already exists: ${cert_path}"
        return 0
    fi

    # [IMP:9][issue-cert][acme.sh] BUSINESS INVARIANT: DNS plugin required for wildcard cert
    if [[ -z "$dns_plugin" ]]; then
        log_step "tls" "FAIL" "TLS certificate requires DNS plugin (set PLATFORM_ACME_DNS_PLUGIN in env)"
        return 1
    fi

    # [IMP:9][issue-cert][acme.sh] BUSINESS INVARIANT: WEBNAMES_API_KEY required for webnames
    if [[ "$dns_plugin" == "webnames" ]] && [[ -z "${WEBNAMES_API_KEY:-}" ]]; then
        log_step "tls" "FAIL" "WEBNAMES_API_KEY not set — required for wildcard TLS via acme.sh DNS-01"
        return 1
    fi

    log_step "acme.sh" "START" "Issuing TLS certificate for ${domain} (email: ${email}) via acme.sh DNS-01 (${dns_plugin})"

    _issue_acme_cert "$domain" "$email" "$dns_plugin" "$wildcard"
}
# endregion ACME_TLS

# region MAIN
## @purpose  Entry point — parse NODE_YAML, validate env, run provisioning sequence
## @workflow
##   ▶ python3 parse node.yaml → env vars
##   → ○ /etc/letsencrypt/live/<domain>/fullchain.pem exists? → SKIP, exit 0
##   → ○ validate PLATFORM_DOMAIN, PLATFORM_EMAIL, PLATFORM_ACME_DNS_PLUGIN, WEBNAMES_API_KEY
##   → install_acme → issue_tls_cert → _acme_install_cron → _acme_verify_cert
##   → [optional] _issue_project_certs for PLATFORM_PROJECT_DOMAINS
##   → exit 0 (success) | exit 1 (failure)
## @invariants
##   - Root required (acme.sh needs filesystem access, apt install git)
##   - NODE_YAML env var points to node.yaml with domain/email/acme_dns_plugin fields
##   - PLATFORM_* env vars override node.yaml values (backward compat with existing CI)
main() {
    # ── Parse NODE_YAML to extract platform config ──────────────────
    # Uses the same python3/yaml pattern as node-lifecycle.sh step_14 (lines 470-498)
    if [[ -n "${NODE_YAML:-}" ]] && [[ -f "$NODE_YAML" ]]; then
        local yaml_info
        yaml_info="$(python3 - "$NODE_YAML" <<'PYEOF' 2>/dev/null
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
domain = data.get('domain', '')
email = data.get('email', '')
acme_dns_plugin = data.get('acme_dns_plugin', '')
projects = data.get('projects', [])
project_domains = [p.get('domain', '') for p in projects if isinstance(p, dict) and p.get('domain')]
print(f"platform_domain:{domain}")
print(f"email:{email}")
print(f"acme_dns_plugin:{acme_dns_plugin}")
print(f"project_domains:{' '.join(project_domains)}")
PYEOF
)" || {
            log_warn "Failed to parse NODE_YAML via python3 — falling back to env vars"
        }
        if [[ -n "${yaml_info:-}" ]]; then
            local yaml_domain yaml_email yaml_acme_dns yaml_project_domains
            yaml_domain="$(echo "$yaml_info" | grep '^platform_domain:' | cut -d: -f2-)"
            yaml_email="$(echo "$yaml_info" | grep '^email:' | cut -d: -f2-)"
            yaml_acme_dns="$(echo "$yaml_info" | grep '^acme_dns_plugin:' | cut -d: -f2-)"
            yaml_project_domains="$(echo "$yaml_info" | grep '^project_domains:' | cut -d: -f2-)"

            # Export with fallback: node.yaml value takes priority, then existing env, then empty
            export PLATFORM_DOMAIN="${yaml_domain:-${PLATFORM_DOMAIN:-}}"
            export PLATFORM_EMAIL="${yaml_email:-${PLATFORM_EMAIL:-}}"
            export PLATFORM_ACME_DNS_PLUGIN="${yaml_acme_dns:-${PLATFORM_ACME_DNS_PLUGIN:-}}"
            export PLATFORM_PROJECT_DOMAINS="${yaml_project_domains:-${PLATFORM_PROJECT_DOMAINS:-}}"
        fi
    fi

    local domain="${PLATFORM_DOMAIN:-}"
    local email="${PLATFORM_EMAIL:-}"
    local dns_plugin="${PLATFORM_ACME_DNS_PLUGIN:-}"
    local project_domains="${PLATFORM_PROJECT_DOMAINS:-}"

    # ── Idempotency: skip main cert if already exists ──────────────
    # ⚠️ TRAP[BUG] · 2026-07-17 · P1 · Early exit blocked project domains
    # · Symptom: project domains skipped on subsequent node-update runs because early
    #   `exit 0` on main cert exists check happened BEFORE _issue_project_certs
    # · Root: idempotency check used `exit 0` which terminated the entire process
    # · Fix: use boolean flag to skip main cert issuance but continue to project domains
    # · Prevention: always process project domains independently of main cert status
    # ⚠️ TRAP[DECISION] · 2026-07-17 · — · exit → return in main()
    # · Rejected: keeping exit (breaks source-ability and causes early termination)
    # · Reason: return is semantically correct for functions; caller (main "$@") handles exit
    # · Rev: if main() needs to truly terminate parent process, use exit selectively
    local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
    local main_cert_exists=false
    if [[ -n "$domain" ]] && [[ -f "$cert_path" ]]; then
        log_step "main" "SKIP" "Certificate already exists: ${cert_path} (idempotent)"
        log_imp 9 "-" "BUSINESS INVARIANT: main cert exists — skip main, continue project domains"
        main_cert_exists=true
    fi

    if ! $main_cert_exists; then
        # ── Validate required environment ───────────────────────────────
        if [[ -z "$domain" ]]; then
            log_fail "PLATFORM_DOMAIN not set — cannot provision SSL certificate"
            return 1
        fi

        if [[ -z "$email" ]]; then
            log_fail "PLATFORM_EMAIL not set — required for Let's Encrypt registration"
            return 1
        fi

        if [[ -z "$dns_plugin" ]]; then
            log_fail "PLATFORM_ACME_DNS_PLUGIN not set — required for DNS-01 challenge"
            return 1
        fi

        if [[ "$dns_plugin" == "webnames" ]] && [[ -z "${WEBNAMES_API_KEY:-}" ]]; then
            log_fail "WEBNAMES_API_KEY not set — required for webnames DNS-01 TLS"
            return 1
        fi

        # ── Step 1: Issue wildcard TLS certificate ─────────────────────
        log_step "main" "START" "SSL provisioning for ${domain} via acme.sh (${dns_plugin})"
        if ! issue_tls_cert "$domain" "$email" "$dns_plugin" "true"; then
            log_fail "TLS certificate issuance failed for ${domain}"
            return 1
        fi

        # ── Step 2: Install acme.sh cron for daily renewal ─────────────
        # [IMP:9][issue-cert][main] BUSINESS INVARIANT: cron must be installed when TLS cert exists
        if [[ -f "$cert_path" ]]; then
            _acme_install_cron || log_warn "acme.sh cron install failed — cert still valid, renew manually"
        fi

        # ── Step 3: Verify certificate expiry >30 days ─────────────────
        # [IMP:9][issue-cert][main] BUSINESS INVARIANT: cert must be valid >30 days
        _acme_verify_cert "$domain" || log_warn "Certificate expires within 30 days — renew soon"
    fi

    # ── Step 4 (Optional): Issue project domain certs ─────────────
    # [IMP:9][issue-cert][main] BUSINESS INVARIANT: independent project domains
    # get single-domain certs. Skips subdomains of PLATFORM_DOMAIN (covered by wildcard).
    # ALWAYS processed — NOT blocked by main cert idempotency check (see TRAP[BUG] above).
    if [[ -n "$project_domains" ]]; then
        _issue_project_certs "$domain" "$email" "$dns_plugin"
    fi

    log_step "main" "DONE" "SSL provisioning complete for ${domain}"
    return 0
}
# endregion MAIN

main "$@"
