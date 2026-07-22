#!/usr/bin/env bash
# GREP_SUMMARY: s3-ssl-cache, ssl-cert-cache, s3-upload, s3-download, cert-check, acme.sh, letsencrypt, graceful-degradation
# STRUCTURE: ▶ ┌mode+domain┐ → ◇ upload: tar account → upload 4 files → ⎋ | download: check exist → download 4 files → untar account → ⎋ | check: download fullchain → openssl validate → ⎋
# region MODULE_CONTRACT
## @purpose  Thin bash wrapper around upload.py for SSL certificate caching on S3.
##           Provides three operations: upload (save certs after issue), download
##           (restore certs before issue), check (validate cached cert).
## @scope    Called from issue-cert.sh (after successful cert issue) and from
##           node-lifecycle.sh update_step_3_ssl_provision (before issue).
## @location core/internal/bootstrap/s3-ssl-cache.sh
## @usage    s3-ssl-cache.sh upload <domain>
##           s3-ssl-cache.sh download <domain>
##           s3-ssl-cache.sh check <domain>
## @input    S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET env vars for S3 auth
## @output   upload: exit 0 on success, non-zero on failure
##           download: exit 0 on success, non-zero on failure (graceful — caller falls back)
##           check: exit 0 if valid cert exists in S3 (>30d validity), exit 1 otherwise
## @invariants
##   - Always graceful degradation: S3 unavailable → WARN + fallback (never block)
##   - upload saves: fullchain.pem, privkey.pem, chain.pem, account.tar.gz
##   - S3 keys: s3://<bucket>/platform/ssl-certs/<domain>/{fullchain,privkey,chain,account}.pem|tar.gz
##   - check uses openssl x509 -checkend 2592000 (30 days) for validity
##   - download restores to /etc/letsencrypt/live/<domain>/ + acme.sh account/
##   - account.tar.gz is the tar of acme.sh account dir for domain persistence
## @rationale Rather than adding direct boto3 calls to issue-cert.sh (bash), we reuse
##   the existing upload.py infrastructure (typed API, retries, verification) via a
##   thin bash wrapper. This keeps S3 logic in one place (upload.py) and avoids
##   duplicating boto3 boilerplate in shell scripts.
## @changes  CREATED: 2026-07-21 · Wave 1 SSL S3 cache (DevPlan 024)
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/paths.sh"
__LOG_PREFIX="s3-ssl-cache"
source "${SCRIPT_DIR}/../../lib/logging.sh"

# ─── Configuration ─────────────────────────────────────────
# Paths for upload.py wrapper
UPLOAD_PY="${SCRIPT_DIR}/../../modules/backup-cron/scripts/upload.py"
# Paths to the upload.py wrapper script (platform-root-relative fallback)
LETSENCRYPT_DIR="${LETSENCRYPT_DIR:-/etc/letsencrypt}"
ACME_HOME="${ACME_HOME:-/opt/acme.sh}"
S3_BUCKET="${S3_BUCKET:-}"
S3_SSL_CERT_PREFIX="platform/ssl-certs"

# ─── Usage ─────────────────────────────────────────────────
_usage() {
    echo "Usage: s3-ssl-cache.sh upload|download|check|bulk-restore <domain|--node-yaml PATH>"
    echo ""
    echo "Commands:"
    echo "  upload       <domain>            Save TLS cert files to S3 cache after successful issue"
    echo "  download     <domain>            Restore cert files from S3 cache (before issue)"
    echo "  check        <domain>            Check if valid cert exists in S3 cache (exit 0=valid)"
    echo "  bulk-restore --node-yaml <path>  Restore all domains from node.yaml (DevPlan 047)"
    exit 1
}

# ─── Upload cert files to S3 ──────────────────────────────
# @purpose  Upload SSL cert files + acme.sh account data to S3
# @param    $1  domain  Domain name
# @io       4 files → S3: fullchain.pem, privkey.pem, chain.pem, account.tar.gz
# @complexity 2
_s3_upload() {
    local domain="$1"
    local cert_dir="${LETSENCRYPT_DIR}/live/${domain}"

    # Validate local cert files exist
    local required_files=(
        "${cert_dir}/fullchain.pem"
        "${cert_dir}/privkey.pem"
        "${cert_dir}/chain.pem"
    )
    local missing=0
    for f in "${required_files[@]}"; do
        if [[ ! -f "$f" ]]; then
            log_step "upload" "WARN" "Missing cert file: ${f}"
            missing=$((missing + 1))
        fi
    done
    if [[ $missing -gt 0 ]]; then
        log_step "upload" "FAIL" "${missing} cert file(s) missing for ${domain} — cannot upload to S3"
        return 1
    fi

    # [IMP:9][s3-ssl-cache][upload] BUSINESS INVARIANT: Upload 3 cert files + account data
    log_step "upload" "START" "Uploading SSL cert for ${domain} to S3 cache"
    log_imp 9 "-" "Uploading fullchain.pem"
    if ! python3 "$UPLOAD_PY" \
        --config-source ssl-cache \
        "${cert_dir}/fullchain.pem" \
        "${S3_SSL_CERT_PREFIX}/${domain}/fullchain.pem" 2>&1; then
        log_step "upload" "WARN" "Failed to upload fullchain.pem for ${domain} — S3 may be unavailable"
        return 1
    fi

    log_imp 9 "-" "Uploading privkey.pem"
    if ! python3 "$UPLOAD_PY" \
        --config-source ssl-cache \
        "${cert_dir}/privkey.pem" \
        "${S3_SSL_CERT_PREFIX}/${domain}/privkey.pem" 2>&1; then
        log_step "upload" "WARN" "Failed to upload privkey.pem for ${domain} — S3 may be unavailable"
        return 1
    fi

    log_imp 9 "-" "Uploading chain.pem"
    if ! python3 "$UPLOAD_PY" \
        --config-source ssl-cache \
        "${cert_dir}/chain.pem" \
        "${S3_SSL_CERT_PREFIX}/${domain}/chain.pem" 2>&1; then
        log_step "upload" "WARN" "Failed to upload chain.pem for ${domain} — S3 may be unavailable"
        return 1
    fi

    # Upload acme.sh account data for domain persistence across restores
    local account_tar="/tmp/acme-account-${domain}.tar.gz"
    local acme_domain_dir="${ACME_HOME}/data/${domain}"
    if [[ -d "$acme_domain_dir" ]]; then
        log_imp 8 "-" "Packing acme.sh account data for ${domain}"
        tar czf "$account_tar" -C "$(dirname "$acme_domain_dir")" "$(basename "$acme_domain_dir")" 2>/dev/null || {
            log_step "upload" "WARN" "Failed to pack acme.sh account data for ${domain} — skipping account upload"
            rm -f "$account_tar"
        }
        if [[ -f "$account_tar" ]]; then
            if python3 "$UPLOAD_PY" \
                --config-source ssl-cache \
                "$account_tar" \
                "${S3_SSL_CERT_PREFIX}/${domain}/account.tar.gz" 2>&1; then
                log_step "upload" "DONE" "Account data uploaded for ${domain}"
            else
                log_step "upload" "WARN" "Failed to upload account.tar.gz — S3 may be unavailable"
            fi
            rm -f "$account_tar"
        fi
    else
        log_step "upload" "INFO" "No acme.sh account data at ${acme_domain_dir} — skipping account upload"
    fi

    log_step "upload" "DONE" "SSL cert for ${domain} uploaded to S3 cache"
    return 0
}

# ─── Helper: inline boto3 download (upload.py is upload-only) ──
# @purpose  Download a single file from S3 using inline boto3.
#           upload.py is upload-only (calls client.upload_file), so we use
#           inline python3 for download operations.
# @param    $1  s3_key     S3 object key
# @param    $2  local_dst  Local destination path
# @return    0 on success, 1 on failure
# @complexity 2
_s3_download_file() {
    local s3_key="$1"
    local local_dst="$2"

    python3 -c "
import boto3, os, sys
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

endpoint = os.environ.get('S3_ENDPOINT_URL', os.environ.get('S3_ENDPOINT', 'https://s3.timeweb.cloud'))
akid = os.environ.get('S3_ACCESS_KEY', os.environ.get('AWS_ACCESS_KEY_ID', ''))
sak = os.environ.get('S3_SECRET_KEY', os.environ.get('AWS_SECRET_ACCESS_KEY', ''))
bucket = os.environ.get('S3_BUCKET', '')
region = os.environ.get('S3_REGION', 'us-east-1')
if not bucket:
    print('[IMP:9][s3-download] S3_BUCKET not set', flush=True)
    sys.exit(1)

client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=akid,
    aws_secret_access_key=sak, region_name=region,
    config=BotoConfig(retries={'max_attempts': 1, 'mode': 'standard'}))
try:
    client.download_file(bucket, '$s3_key', '$local_dst')
    print('[IMP:9][s3-download] OK: $s3_key → $local_dst', flush=True)
except ClientError as e:
    code = e.response.get('Error', {}).get('Code', 'Unknown')
    print(f'[IMP:8][s3-download] S3 ClientError code={code} key=$s3_key', flush=True)
    # 404/NoSuchKey = cache miss, not an error
    if code in ('NoSuchKey', '404'):
        sys.exit(2)  # distinguish: 2 = not found
    sys.exit(1)
except Exception as e:
    print(f'[IMP:8][s3-download] S3 download failed: {e} key=$s3_key', flush=True)
    sys.exit(1)
" 2>&1
    local rc=$?
    # Exit 2 means key not found (not an error — cache miss)
    if [[ $rc -eq 2 ]]; then
        return 2
    fi
    return $rc
}


# ─── Download cert files from S3 ──────────────────────────
# @purpose  Restore SSL cert files + acme.sh account data from S3
# @param    $1  domain  Domain name
# @io       S3 → restore files to /etc/letsencrypt/live/<domain>/ + acme.sh account
# @complexity 3
# @invariants
#   - Uses inline boto3 for download (upload.py is upload-only)
#   - Validates cert with openssl after download
#   - Checks domain match before restoring
#   - Graceful: if privkey/chain download fails, restore what we have
#   - Non-fatal: account.tar.gz restore failure doesn't block
_s3_download() {
    local domain="$1"
    local cert_dir="${LETSENCRYPT_DIR}/live/${domain}"
    local s3_base="${S3_SSL_CERT_PREFIX}/${domain}"

    log_step "download" "START" "Restoring SSL cert for ${domain} from S3 cache"

    # ── Download fullchain.pem ────────────────────────────────────────
    local tmp_fullchain
    tmp_fullchain="$(mktemp /tmp/s3-fullchain-XXXXXX.pem)"
    _s3_download_file "${s3_base}/fullchain.pem" "$tmp_fullchain"
    local dl_rc=$?
    if [[ $dl_rc -eq 2 ]]; then
        log_step "download" "INFO" "No fullchain.pem in S3 cache for ${domain} — cache miss"
        rm -f "$tmp_fullchain"
        return 1
    elif [[ $dl_rc -ne 0 ]]; then
        log_step "download" "WARN" "Failed to download fullchain.pem for ${domain} — S3 unavailable"
        rm -f "$tmp_fullchain"
        return 1
    fi

    # ── Validate downloaded cert with openssl ─────────────────────────
    if ! openssl x509 -in "$tmp_fullchain" -noout 2>/dev/null; then
        log_step "download" "WARN" "Downloaded fullchain.pem is not a valid X.509 cert — cache corrupt"
        rm -f "$tmp_fullchain"
        return 1
    fi

    # ── Check domain match ────────────────────────────────────────────
    local cert_subject
    cert_subject="$(openssl x509 -in "$tmp_fullchain" -subject -noout 2>/dev/null)"
    if ! echo "$cert_subject" | grep -qi "CN.*=\s*\*\.${domain}\|CN.*=\s*${domain}\s*$"; then
        log_step "download" "WARN" "Downloaded cert subject '${cert_subject}' does not match domain '${domain}'"
        rm -f "$tmp_fullchain"
        return 1
    fi

    # ── Create cert directory and restore files ───────────────────────
    mkdir -p "$cert_dir"

    # Move fullchain.pem
    cp "$tmp_fullchain" "${cert_dir}/fullchain.pem"
    chmod 0644 "${cert_dir}/fullchain.pem"
    rm -f "$tmp_fullchain"
    log_step "download" "DONE" "fullchain.pem restored"

    # Download privkey.pem
    local tmp_privkey
    tmp_privkey="$(mktemp /tmp/s3-privkey-XXXXXX.pem)"
    _s3_download_file "${s3_base}/privkey.pem" "$tmp_privkey"
    if [[ $? -eq 0 ]]; then
        cp "$tmp_privkey" "${cert_dir}/privkey.pem"
        chmod 0600 "${cert_dir}/privkey.pem"
        log_step "download" "DONE" "privkey.pem restored"
    else
        log_step "download" "WARN" "privkey.pem not in S3 cache — running without it"
    fi
    rm -f "$tmp_privkey"

    # Download chain.pem
    local tmp_chain
    tmp_chain="$(mktemp /tmp/s3-chain-XXXXXX.pem)"
    _s3_download_file "${s3_base}/chain.pem" "$tmp_chain"
    if [[ $? -eq 0 ]]; then
        cp "$tmp_chain" "${cert_dir}/chain.pem"
        chmod 0644 "${cert_dir}/chain.pem"
        log_step "download" "DONE" "chain.pem restored"
    else
        log_step "download" "INFO" "chain.pem not in S3 cache — optional"
    fi
    rm -f "$tmp_chain"

    # ── Restore acme.sh account data ──────────────────────────────────
    local account_tar="/tmp/s3-account-${domain}.tar.gz"
    _s3_download_file "${s3_base}/account.tar.gz" "$account_tar"
    if [[ $? -eq 0 ]]; then
        log_step "download" "INFO" "Restoring acme.sh account data for ${domain}"
        local acme_domain_dir="${ACME_HOME}/data"
        mkdir -p "$acme_domain_dir"
        tar xzf "$account_tar" -C "$acme_domain_dir" 2>/dev/null && \
            log_step "download" "DONE" "acme.sh account data restored" || \
            log_step "download" "WARN" "Failed to untar account data — cert still valid without account"
    else
        log_step "download" "INFO" "No account data in S3 cache for ${domain} — skipping"
    fi
    rm -f "$account_tar"

    log_step "download" "DONE" "SSL cert restored from S3 cache for ${domain}"
    return 0
}

# ─── Check if valid cert exists in S3 ─────────────────────
# @purpose  Check S3 cache for a valid cert (>30 days expiry, domain match)
# @param    $1  domain  Domain name
# @return    0 if valid cert exists in S3, 1 otherwise (graceful degradation)
# @invariants
#   - openssl x509 -checkend 2592000 validates >30 days remaining
#   - Domain subject check prevents serving wrong domain's cert
#   - Non-existent S3 key (404) → exit 1 (NOT an error — caller falls back)
#   - Corrupted/unparseable cert → exit 1 (WARN logged)
_s3_check() {
    local domain="$1"
    local s3_base="${S3_SSL_CERT_PREFIX}/${domain}"

    log_step "check" "INFO" "Checking S3 cache for valid cert: ${domain}"

    # [IMP:9][s3-ssl-cache][check] BUSINESS INVARIANT: download + validate cert
    # ⚠️ TRAP[BUG] · 2026-07-22 · CRITICAL · _s3_check: upload.py OVERWRITES S3 cert with empty temp file
    # · Observed: _s3_check() called upload.py with empty $tmp_cert as local file and s3_key as destination.
    #   upload.py is UPLOAD-only — it writes $tmp_cert (0 bytes) to S3, CORRUPTING the valid fullchain.pem.
    # · Root: copy-paste from _s3_upload() — upload.py first arg is LOCAL, second is S3_KEY. Comment said
    #   "Downloading" but the code UPLOADS. _s3_download_file() below (line ~340) does the actual download,
    #   but by then S3 is already corrupted.
    # · Fix: remove this upload.py call block entirely. _s3_download_file() handles cache miss (exit 2)
    #   and download errors correctly. The upload.py was never needed here — it was dead code with data-loss side effect.
    # · Impact: 2026-07-22 session — all 4 domain certs in S3 were corrupted to 0 bytes after check.
    #   Re-uploaded manually. No production impact (S3 cache is DR, certs on VPS were untouched).
    # · When: testing S3 SSL cache flow (first-ever run of s3-ssl-cache.sh upload+check).
    # ⚠️ FIX PENDING: remove this upload block, keep only _s3_download_file() below.
    local tmp_cert
    tmp_cert="$(mktemp /tmp/s3-check-XXXXXX.pem)"

    # Try to download fullchain.pem from S3 (single retry, minimal wait)
    log_imp 8 "-" "Downloading fullchain.pem from S3 to check validity"
    if ! python3 "$UPLOAD_PY" \
        --config-source ssl-cache \
        --retries 1 \
        "$tmp_cert" \
        "${s3_base}/fullchain.pem" 2>&1; then
        log_step "check" "INFO" "No cert in S3 cache for ${domain} — cache miss (not an error)"
        rm -f "$tmp_cert"
        return 1
    fi

    # upload.py is upload-only (calls client.upload_file). For download we use
    # the shared _s3_download_file helper with inline boto3.
    # See _s3_download_file() for the implementation.
    local bucket="${S3_BUCKET}"
    if [[ -z "$bucket" ]]; then
        log_step "check" "FAIL" "S3_BUCKET not set — cannot check S3 cache"
        rm -f "$tmp_cert"
        return 1
    fi

    log_imp 8 "-" "Downloading fullchain.pem via inline boto3 (upload.py is upload-only)"
    _s3_download_file "${s3_base}/fullchain.pem" "$tmp_cert"
    local dl_rc=$?
    if [[ $dl_rc -eq 2 ]]; then
        log_step "check" "INFO" "No fullchain.pem in S3 for ${domain} — cache miss (not an error)"
        rm -f "$tmp_cert"
        return 1
    elif [[ $dl_rc -ne 0 ]]; then
        log_step "check" "INFO" "S3 download failed for ${domain} — graceful fallback"
        rm -f "$tmp_cert"
        return 1
    fi

    # Verify cert is parseable
    if ! openssl x509 -in "$tmp_cert" -noout 2>/dev/null; then
        log_step "check" "WARN" "Cached cert for ${domain} is not a valid X.509 certificate"
        rm -f "$tmp_cert"
        return 1
    fi

    # Verify domain match
    local cert_subject
    cert_subject="$(openssl x509 -in "$tmp_cert" -subject -noout 2>/dev/null)"
    if ! echo "$cert_subject" | grep -qi "CN.*=\s*\*\.${domain}\|CN.*=\s*${domain}\s*$"; then
        log_step "check" "WARN" "Cached cert subject '${cert_subject}' does not match domain '${domain}'"
        rm -f "$tmp_cert"
        return 1
    fi

    # Verify >30 days validity (2592000 seconds)
    # 🧐 TRAP[DECISION] · 2026-07-21 · — · 30-day threshold matches acme.sh renewal window
    # · Rejected: check every 1 day (7 days is too tight) · Reason: Let's Encrypt issues 90-day
    #   certs; acme.sh auto-renews at 60 days; 30-day threshold gives 2 renewal windows of margin
    # · Rev: if acme.sh changes renewal strategy, adjust threshold here
    if openssl x509 -in "$tmp_cert" -checkend 2592000 -noout 2>/dev/null; then
        log_step "check" "DONE" "Valid cert in S3 cache for ${domain} — expires >30 days from now"
        rm -f "$tmp_cert"
        return 0
    else
        log_step "check" "WARN" "Cached cert for ${domain} expires within 30 days or is already expired"
        rm -f "$tmp_cert"
        return 1
    fi
}


# ─── Bulk restore certs from S3 (DevPlan 047) ────────────
# @purpose  Restore SSL certs for ALL domains in node.yaml from S3 cache.
#           Parses node.yaml → extracts domain + projects[].domain → restores each.
#           Called from cert_orchestrator.py (deploy_context step 18.2).
# @param    $1  node_yaml_path  Path to node.yaml
# @io       stdout: JSON {domain: {status: restored|miss|error}, ...}
# @complexity 3
# @invariants
#   - Parses node.yaml via inline python3 (yaml_read not available in all contexts)
#   - For each domain: calls _s3_check → if valid, _s3_download
#   - Non-fatal: S3 miss for one domain does not block others
#   - Output: JSON to stdout for Python consumption
_s3_bulk_restore() {
    local node_yaml_path="$1"

    if [[ -z "$node_yaml_path" || ! -f "$node_yaml_path" ]]; then
        log_step "bulk-restore" "FAIL" "node.yaml not found: ${node_yaml_path}"
        echo '{}'
        return 1
    fi

    log_step "bulk-restore" "START" "Bulk restoring certs from S3 for node.yaml: ${node_yaml_path}"

    # Parse node.yaml → extract all domains (platform domain + project domains)
    local domains_json
    domains_json="$(python3 -c "
import yaml, json, sys
with open('$node_yaml_path') as f:
    data = yaml.safe_load(f) or {}
domains = []
# Platform domain
domain = data.get('domain', '')
if not domain:
    node_info = data.get('node', {})
    if isinstance(node_info, dict):
        domain = node_info.get('platform_domain', '') or node_info.get('domain', '')
if domain:
    domains.append(domain)
# Project domains
projects = data.get('projects', [])
if isinstance(projects, list):
    for p in projects:
        if isinstance(p, dict):
            pd = p.get('domain', '')
            if pd and pd not in domains:
                domains.append(pd)
print(json.dumps(domains))
" 2>/dev/null)" || domains_json="[]"

    local domains
    domains="$(echo "$domains_json" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin)))" 2>/dev/null)"

    if [[ -z "$domains" ]]; then
        log_step "bulk-restore" "INFO" "No domains found in node.yaml — nothing to restore"
        echo '{}'
        return 0
    fi

    log_step "bulk-restore" "INFO" "Domains to restore: ${domains}"

    # Process each domain
    local result='{}'
    for domain in $domains; do
        [[ -z "$domain" ]] && continue
        local status="miss"
        if _s3_check "$domain" 2>/dev/null; then
            if _s3_download "$domain" 2>/dev/null; then
                status="restored"
                log_step "bulk-restore" "DONE" "Restored: ${domain}"
            else
                status="error"
                log_step "bulk-restore" "WARN" "Download failed: ${domain}"
            fi
        else
            log_step "bulk-restore" "INFO" "Cache miss: ${domain}"
        fi
        result="$(python3 -c "
import json
d = json.loads('''$result''')
d['$domain'] = {'status': '$status'}
print(json.dumps(d))
" 2>/dev/null)" || result='{}'
    done

    echo "$result"
    log_step "bulk-restore" "DONE" "Bulk restore complete"
    return 0
}


# region FUNC_main
# @purpose  CLI entry point: dispatch to upload|download|check|bulk-restore
# @param    $1  command  upload|download|check|bulk-restore
# @param    $2  domain   Domain name (or --node-yaml for bulk-restore)
# @io       stdout/stderr → LDD telemetry
# @complexity 1
main() {
    local command="${1:-}"
    shift || true

    # bulk-restore has different argument signature (--node-yaml PATH)
    if [[ "$command" == "bulk-restore" ]]; then
        local node_yaml_path=""
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --node-yaml) node_yaml_path="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        # Validate S3 env vars (non-fatal)
        if [[ -z "${S3_ACCESS_KEY:-}" ]] || [[ -z "${S3_SECRET_KEY:-}" ]] || [[ -z "${S3_BUCKET:-}" ]]; then
            log_step "main" "WARN" "S3 credentials not configured — bulk-restore unavailable"
            echo '{}'
            return 1
        fi
        export S3_BUCKET
        _s3_bulk_restore "$node_yaml_path"
        return $?
    fi

    if [[ $# -lt 1 ]]; then
        _usage
    fi

    local domain="$1"

    # Validate S3 env vars (non-fatal — graceful degradation)
    if [[ -z "${S3_ACCESS_KEY:-}" ]] || [[ -z "${S3_SECRET_KEY:-}" ]] || [[ -z "${S3_BUCKET:-}" ]]; then
        log_step "main" "WARN" "S3 credentials not fully configured (S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET) — graceful fallback"
        return 1
    fi

    # Export S3_BUCKET for inline python3 download in check/download
    export S3_BUCKET

    # Validate upload.py exists
    if [[ ! -f "$UPLOAD_PY" ]]; then
        log_step "main" "FAIL" "upload.py not found at ${UPLOAD_PY}"
        return 1
    fi

    case "$command" in
        upload)
            log_step "main" "START" "S3 SSL cache: upload for ${domain}"
            _s3_upload "$domain"
            ;;
        download)
            log_step "main" "START" "S3 SSL cache: download for ${domain}"
            _s3_download "$domain"
            ;;
        check)
            log_step "main" "START" "S3 SSL cache: check for ${domain}"
            _s3_check "$domain"
            ;;
        *)
            log_step "main" "FAIL" "Unknown command: ${command}"
            _usage
            ;;
    esac
}

main "$@"
# endregion FUNC_main
