#!/usr/bin/env bash
# GREP_SUMMARY: generate-dev-certs mkcert openssl self-signed SAN idempotent PLATFORM_DOMAIN wildcard
# STRUCTURE: ┌required_sans()┐ → ◇ cert_is_current() (literal SAN ⊇ required + checkend 30d) → ◇ generate_mkcert() / generate_openssl() → ◇ verify_san() → ⎋ main()
# region MODULE_CONTRACT
## @purpose  Idempotent dev certificate generator for nginx — hybrid mkcert→openssl.
##           SAN derived from domain scheme: `*.ai-platform.local` (base) + `*.${PLATFORM_DOMAIN}`
##           if loaded context differs + localhost + 127.0.0.1.
## @scope    core/modules/nginx/dev-certs/ — output files _local.pem, _local-key.pem
## @invariants
##   - Idempotent: no-op ⟺ (literal SAN set ⊇ required) AND (not expiring in 30 days)
##   - NEVER touches system trust store (no `mkcert -install`)
##   - CERT_BACKEND=auto → mkcert if in PATH, else openssl
##   - Exit 0 = valid cert (new or up-to-date), Exit 1 = failure
##   - LDD logs at [IMP:1-10] levels
## @rationale  Two backends: mkcert for owner machine (green lock in browser),
##             openssl for CI (no brew). Single output contract (DD1).
## @see DevPlan 012 — TASK-1
# endregion MODULE_CONTRACT

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${DEV_CERTS_DIR:="${SCRIPT_DIR}/dev-certs"}"
: "${PLATFORM_DOMAIN:="ai-platform.local"}"
: "${CERT_BACKEND:="auto"}"

_CERT_FILE="${DEV_CERTS_DIR}/_local.pem"
_KEY_FILE="${DEV_CERTS_DIR}/_local-key.pem"
_EXPIRY_DAYS=825  # ~2.25 years, matches openssl default

# ── Helpers ──────────────────────────────────────────────────────────────────────

# region FUNC_required_sans
## @purpose  Build the required SAN set for the dev certificate.
## @io       ⇥ (PLATFORM_DOMAIN) → ⎋ newline-separated sorted DNS: and IP: entries
## @logic    Base: *.ai-platform.local always. If PLATFORM_DOMAIN != ai-platform.local,
##           add *.${PLATFORM_DOMAIN}. Always add localhost + 127.0.0.1.
required_sans() {
    local -a sans=()
    sans+=("DNS:*.ai-platform.local")
    if [ "$PLATFORM_DOMAIN" != "ai-platform.local" ]; then
        >&2 echo "[IMP:8][required_sans] Context domain detected: ${PLATFORM_DOMAIN} — adding wildcard SAN"
        sans+=("DNS:*.${PLATFORM_DOMAIN}")
    fi
    sans+=("DNS:localhost")
    sans+=("IP:127.0.0.1")
    # Sort for deterministic comparison
    local -a sorted=()
    mapfile -t sorted < <(printf '%s\n' "${sans[@]}" | sort)
    printf "%s\n" "${sorted[@]}"
}
# endregion FUNC_required_sans

# region FUNC_get_cert_sans
## @purpose  Extract literal SAN entries from an existing PEM certificate.
## @io       ⇥ cert_file → ⎋ newline-separated DNS: and IP: entries, or empty string
## @note     Parses openssl x509 -ext subjectAltName output. Returns sorted lines.
get_cert_sans() {
    local cert_file="$1"
    if [ ! -f "$cert_file" ]; then
        echo ""
        return
    fi
    # Extract SAN section: lines between "X509v3 Subject Alternative Name:" and next blank line
    local san_raw
    san_raw=$(openssl x509 -in "$cert_file" -noout -ext subjectAltName 2>/dev/null || true)
    if [ -z "$san_raw" ]; then
        echo ""
        return
    fi
    # Parse: "DNS:*.ai-platform.local, DNS:localhost, IP Address:127.0.0.1" → one per line
    # Normalize "IP Address:" → "IP:" for consistent comparison with required_sans
    local entries
    entries=$(echo "$san_raw" \
        | grep -o 'DNS:[^,]*\|IP Address:[^,]*\|IP:[^,]*' \
        | sed 's/^[[:space:]]*//' \
        | sed 's/IP Address:/IP:/g' \
        | sort)
    echo "$entries"
}
# endregion FUNC_get_cert_sans

# region FUNC_cert_is_current
## @purpose  Check if existing cert satisfies idempotency: literal SAN ⊇ required AND not expiring.
## @io       ⇥ (CERT_FILE, KEY_FILE, PLATFORM_DOMAIN) → ⎋ 0 = current, 1 = needs regeneration
## @logic    Primary: every required SAN entry must exist in cert's literal SAN set.
##           Secondary: cert must not expire within 30 days.
##           Fail-fast if cert or key file missing.
cert_is_current() {
    if [ ! -f "$_CERT_FILE" ] || [ ! -f "$_KEY_FILE" ]; then
        echo "[IMP:8][cert_is_current] Cert or key file missing — needs generation"
        return 1
    fi

    local required
    required=$(required_sans)
    local cert_sans
    cert_sans=$(get_cert_sans "$_CERT_FILE")

    if [ -z "$cert_sans" ]; then
        echo "[IMP:8][cert_is_current] Cannot parse SAN from existing cert — needs regeneration"
        return 1
    fi

    echo "[IMP:8][cert_is_current] Required SAN (sorted):"
    # shellcheck disable=SC2001
    sed 's/^/  /' <<< "$required"
    echo "[IMP:8][cert_is_current] Actual cert SAN (sorted):"
    # shellcheck disable=SC2001
    sed 's/^/  /' <<< "$cert_sans"

    # Primary: literal SAN set inclusion check (DD8 — exact match, no wildcard matching)
    local missing=0
    while IFS= read -r entry; do
        if [ -z "$entry" ]; then continue; fi
        if ! echo "$cert_sans" | grep -qF "$entry"; then
            echo "[IMP:7][cert_is_current] SAN entry missing: ${entry}"
            missing=1
        fi
    done <<< "$required"

    if [ "$missing" -ne 0 ]; then
        echo "[IMP:7][cert_is_current] SAN drift detected — needs regeneration"
        return 1
    fi

    # Secondary: check expiry (30 day window)
    if ! openssl x509 -in "$_CERT_FILE" -checkend $((30 * 86400)) >/dev/null 2>&1; then
        echo "[IMP:7][cert_is_current] Cert expires within 30 days — needs regeneration"
        return 1
    fi

    echo "[IMP:9][cert_is_current] Cert is current (SAN matches, >30d until expiry)"
    return 0
}
# endregion FUNC_cert_is_current

# region FUNC_generate_mkcert
## @purpose  Generate dev cert using mkcert.
## @io       ⇥ (DEV_CERTS_DIR, required_sans) → ⚡ mkcert → ⎋ _local.pem + _local-key.pem
## @pre      mkcert is in PATH
generate_mkcert() {
    local sans_list
    sans_list=$(required_sans | sed 's/^DNS://' | sed 's/^IP://' | tr '\n' ' ' | sed 's/ $//')
    echo "[IMP:7][generate_mkcert] Using mkcert backend"
    echo "[IMP:8][generate_mkcert] SAN: ${sans_list}"

    mkdir -p "$DEV_CERTS_DIR"
    # shellcheck disable=SC2086
    mkcert \
        -cert-file "$_CERT_FILE" \
        -key-file "$_KEY_FILE" \
        $sans_list 2>&1 | sed 's/^/[IMP:8][mkcert] /'

    echo "[IMP:9][generate_mkcert] mkcert generated: ${_CERT_FILE}"
}
# endregion FUNC_generate_mkcert

# region FUNC_generate_openssl
## @purpose  Generate self-signed dev cert using openssl.
## @io       ⇥ (DEV_CERTS_DIR, PLATFORM_DOMAIN, EXPIRY_DAYS) → ⚡ openssl req → ⎋ _local.pem + _local-key.pem
## @note     Uses RSA 2048, SHA256, subjectAltName from required_sans().
generate_openssl() {
    local sans_list
    sans_list=$(required_sans | paste -sd, -)
    echo "[IMP:7][generate_openssl] Using openssl backend"
    echo "[IMP:8][generate_openssl] SAN: ${sans_list}"

    mkdir -p "$DEV_CERTS_DIR"

    # Build openssl config with SAN section
    local openssl_cnf
    openssl_cnf=$(mktemp)
    cat > "$openssl_cnf" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no
[req_distinguished_name]
CN = *.ai-platform.local
[v3_req]
keyUsage = keyEncipherment, dataEncipherment, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = ${sans_list}
EOF

    openssl req -x509 \
        -newkey rsa:2048 \
        -nodes \
        -days "$_EXPIRY_DAYS" \
        -keyout "$_KEY_FILE" \
        -out "$_CERT_FILE" \
        -config "$openssl_cnf" \
        2>&1 | sed 's/^/[IMP:8][openssl] /'

    rm -f "$openssl_cnf"
    echo "[IMP:9][generate_openssl] OpenSSL generated: ${_CERT_FILE}"
}
# endregion FUNC_generate_openssl

# region FUNC_verify_san
## @purpose  Verify that generated certificate contains all required SAN entries.
## @io       ⇥ (CERT_FILE, PLATFORM_DOMAIN) → ⎋ 0 = valid, 1 = SAN mismatch
## @fail-fast  Fail if any required SAN entry is missing in the generated cert.
verify_san() {
    local required
    required=$(required_sans)
    local cert_sans
    cert_sans=$(get_cert_sans "$_CERT_FILE")

    echo "[IMP:8][verify_san] Verifying generated cert SAN..."
    local missing=0
    while IFS= read -r entry; do
        if [ -z "$entry" ]; then continue; fi
        if ! echo "$cert_sans" | grep -qF "$entry"; then
            echo "[IMP:9][verify_san] ❌ SAN MISSING: ${entry}"
            missing=1
        else
            echo "[IMP:8][verify_san] ✅ ${entry}"
        fi
    done <<< "$required"

    if [ "$missing" -ne 0 ]; then
        echo "[IMP:9][verify_san] ❌ SAN verification FAILED — missing entries above" >&2
        return 1
    fi

    echo "[IMP:9][verify_san] ✅ All required SAN entries present"
    return 0
}
# endregion FUNC_verify_san

# region FUNC_main
## @purpose  Main entrypoint: check idempotency → select backend → generate → verify.
## @io       ⇥ env vars → ⚡ cert generation → ⎋ exit 0/1
main() {
    echo "[IMP:7][dev-certs] === Dev certificate generator ==="
    echo "[IMP:8][dev-certs] DEV_CERTS_DIR=${DEV_CERTS_DIR}"
    echo "[IMP:8][dev-certs] PLATFORM_DOMAIN=${PLATFORM_DOMAIN}"
    echo "[IMP:8][dev-certs] CERT_BACKEND=${CERT_BACKEND}"

    # ── Check idempotency ──────────────────────────────────────────────────────
    if cert_is_current; then
        echo "[IMP:9][dev-certs] ✅ Cert up-to-date — no action needed"
        exit 0
    fi

    # ── Select backend ─────────────────────────────────────────────────────────
    local backend="$CERT_BACKEND"
    if [ "$backend" = "auto" ]; then
        if command -v mkcert &>/dev/null; then
            echo "[IMP:7][dev-certs] Auto-select: mkcert found in PATH"
            backend="mkcert"
        else
            echo "[IMP:7][dev-certs] Auto-select: mkcert not found — falling back to openssl"
            backend="openssl"
        fi
    fi

    # ── Generate ────────────────────────────────────────────────────────────────
    case "$backend" in
        mkcert)
            if ! command -v mkcert &>/dev/null; then
                echo "[IMP:9][dev-certs] ❌ ERROR: CERT_BACKEND=mkcert but mkcert not in PATH" >&2
                exit 1
            fi
            generate_mkcert
            ;;
        openssl)
            if ! command -v openssl &>/dev/null; then
                echo "[IMP:9][dev-certs] ❌ ERROR: CERT_BACKEND=openssl but openssl not in PATH" >&2
                exit 1
            fi
            generate_openssl
            ;;
        *)
            echo "[IMP:9][dev-certs] ❌ ERROR: Unknown CERT_BACKEND='${backend}'. Use auto|mkcert|openssl" >&2
            exit 1
            ;;
    esac

    # ── Verify generated cert SAN ──────────────────────────────────────────────
    if ! verify_san; then
        echo "[IMP:9][dev-certs] ❌ FAILED: generated cert SAN verification failed" >&2
        exit 1
    fi

    echo "[IMP:9][dev-certs] ✅ Certificate generated successfully"
    echo "[IMP:7][dev-certs] Hint: run 'make -C core/modules/nginx restart' (or docker compose restart nginx) to pick up new cert"
    exit 0
}
# endregion FUNC_main

main "$@"
