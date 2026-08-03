#!/usr/bin/env python3
# GREP_SUMMARY: dev-certs, cert-generator, mkcert, openssl, self-signed, SAN, idempotent, PLATFORM_DOMAIN, wildcard, python3
# STRUCTURE: ▶ ┌required_sans()┐ → ◇ cert_is_current() (literal SAN ⊇ required + checkend 30d) → ◇ generate_mkcert()/generate_openssl() → ◇ verify_san() → ⎋ main() → sys.exit(0/1)
# region MODULE_CONTRACT
## @purpose  Idempotent dev certificate generator for nginx — hybrid mkcert→openssl.
##           SAN derived from domain scheme: `*.ai-platform.local` (base) + `*.${PLATFORM_DOMAIN}`
##           if loaded context differs + localhost + 127.0.0.1. Behaviour-preserving 1:1
##           migration of the legacy generate-dev-certs shell (295 LOC shell → Python).
## @scope    core/modules/nginx/dev-certs/ — output files fullchain.pem, privkey.pem.
##           Python module (DevPlan 099); thin shell facade kept for backward compat.
## @invariants
##   - Idempotent: no-op ⟺ (literal SAN set ⊇ required) AND (not expiring in 30 days)
##   - NEVER touches system trust store (no `mkcert -install`)
##   - CERT_BACKEND=auto → mkcert if in PATH, else openssl
##   - Exit 0 = valid cert (new or up-to-date), Exit 1 = failure
##   - LDD logs at [IMP:1-10] via print(..., file=sys.stderr) — NOT logging module
##   - Subprocess only for external tools (openssl/mkcert) — never for business logic
## @rationale  Two backends: mkcert for owner machine (green lock in browser),
##             openssl for CI (no brew). Single output contract (DD1). print(stderr) keeps
##             byte-compat with the old shell `>&2 echo "[IMP:X]..."` logs and stays visible
##             in docker logs/stderr without log-level configuration (DevPlan 099 §11).
## @changes  2026-07-31 · DevPlan 099 — Created (Strangler-Fig migration, 295→~40 LOC facade)
## @modulemap
##   ┌required_sans(platform_domain)┐ → sorted DNS:/IP: list
##   ┌get_cert_sans(cert_file)┐      → openssl x509 -ext subjectAltName parse → sorted list
##   ┌cert_is_current(cert,key,dom)┐ → SAN ⊇ required ∧ -checkend 30d → bool
##   ┌generate_mkcert(dir, sans)┐    → mkcert subprocess → cert_file Path
##   ┌generate_openssl(dir,dom,sans)┐ → tempfile cnf + openssl req subprocess → cert_file Path
##   ┌verify_san(cert, required)┐    → set comparison → bool
##   ┌main()┐                        → env vars → idempotency → backend → generate → verify → exit
# endregion MODULE_CONTRACT

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_PLATFORM_DOMAIN = "ai-platform.local"
DEFAULT_DEV_CERTS_DIR = str(Path(__file__).resolve().parent / "dev-certs")
DEFAULT_CERT_BACKEND = "auto"
EXPIRY_DAYS = 825  # ~2.25 years, matches openssl default
EXPIRY_CHECK_DAYS = 30  # -checkend window

# Matches openssl x509 -ext subjectAltName output: "DNS:*.ai-platform.local, DNS:localhost, IP Address:127.0.0.1"
_SAN_RE = re.compile(r"DNS:[^,]*|IP Address:[^,]*|IP:[^,]*")


# ═══════════════════════════════════════════════════════════════════
# region FUNC__log
# ═══════════════════════════════════════════════════════════════════
def _log(level: int, func_name: str, message: str) -> None:
    """Write an LDD telemetry line to stderr.

    ▶ ┌level+func+message┐ → ⊕ print(..., file=sys.stderr) → ⎋ None

    ## @purpose — LDD log sink. Mirrors the old shell `>&2 echo "[IMP:X][func] ..."`
    ##            contract so docker logs / test capsys capture the same trajectory.
    ## @io — ⇥ level: int (1-10), func_name: str, message: str → ⎋ None (stderr side-effect)
    ## @complexity — O(1)
    ## @rationale — print(stderr) chosen over logging: zero config, visible in stderr
    ##              without log-level setup, byte-compatible with the shell facade era.
    """
    print(f"[IMP:{level}][{func_name}] {message}", file=sys.stderr)


# endregion FUNC__log


# ═══════════════════════════════════════════════════════════════════
# region FUNC__command_exists
# ═══════════════════════════════════════════════════════════════════
def _command_exists(name: str) -> bool:
    """Check whether an external tool is available on PATH.

    ▶ ┌name┐ → ◇ shutil.which(name) ? T/F → ⎋ bool

    ## @purpose — Backend tool detection (mkcert/openssl) — maps to shell `command -v`.
    ## @io — ⇥ name: str (binary name) → ⎋ bool
    ## @complexity — O(1)
    """
    return shutil.which(name) is not None


# endregion FUNC__command_exists


# ═══════════════════════════════════════════════════════════════════
# region FUNC__ensure_tool
# ═══════════════════════════════════════════════════════════════════
def _ensure_tool(name: str) -> None:
    """Fail fast (exit 1) if the selected backend binary is missing.

    ▶ ┌name┐ → ◇ _command_exists(name) ? → ⎋ ok | ⚡ IMP:9 error → sys.exit(1)

    ## @purpose — Hard gate before invoking a backend: CERT_BACKEND=mkcert but no mkcert
    ##            in PATH must fail immediately with a visible IMP:9 error (shell parity).
    ## @io — ⇥ name: str → ⎋ None | sys.exit(1)
    ## @complexity — O(1)
    ## @invariants — Never silently falls back here — backend was already explicit.
    """
    if not _command_exists(name):
        _log(9, "main", f"ERROR: CERT_BACKEND={name} but {name} not in PATH")
        sys.exit(1)


# endregion FUNC__ensure_tool


# ═══════════════════════════════════════════════════════════════════
# region FUNC__strip_prefix
# ═══════════════════════════════════════════════════════════════════
def _strip_prefix(entry: str) -> str:
    """Remove DNS:/IP: prefix from a SAN entry (for mkcert bare host list).

    ▶ ┌"DNS:x"┐ → x · ┌"IP:y"┐ → y · else unchanged

    ## @purpose — Maps shell `sed 's/^DNS://' | sed 's/^IP://'` for mkcert args.
    ## @io — ⇥ entry: str (e.g. "DNS:localhost") → ⎋ str (e.g. "localhost")
    ## @complexity — O(1)
    """
    if entry.startswith("DNS:"):
        return entry[len("DNS:") :]
    if entry.startswith("IP:"):
        return entry[len("IP:") :]
    return entry


# endregion FUNC__strip_prefix


# ═══════════════════════════════════════════════════════════════════
# region FUNC_required_sans
# ═══════════════════════════════════════════════════════════════════
def required_sans(platform_domain: str) -> list[str]:
    """Build the sorted required SAN set for the dev certificate.

    ▶ ┌platform_domain┐ → ⊕ base *.ai-platform.local → ◇ context ? +*.{domain} → ⊕ localhost + 127.0.0.1 → ∑ sorted() → ⎋ list[str]

    ## @purpose — Deterministic SAN contract. Base: `*.ai-platform.local` always; if
    ##            PLATFORM_DOMAIN differs from the default, add `*.${PLATFORM_DOMAIN}`;
    ##            always add localhost + 127.0.0.1. Sorted for deterministic comparison.
    ## @io — ⇥ platform_domain: str → ⎋ list[str] sorted DNS:/IP: entries
    ## @complexity — O(N log N) where N ≤ 4
    ## @invariants
    ##   - Sort is stable across runs (pure function of platform_domain)
    ##   - Base wildcard `*.ai-platform.local` always present — never conditional
    ## @rationale — Default domain must be covered even when context env is absent,
    ##              otherwise nginx default vhosts would reject the cert (DD1 parity).
    """
    sans = ["DNS:*.ai-platform.local"]
    if platform_domain != DEFAULT_PLATFORM_DOMAIN:
        _log(8, "required_sans", f"Context domain detected: {platform_domain} — adding wildcard SAN")
        sans.append(f"DNS:*.{platform_domain}")
    sans.append("DNS:localhost")
    sans.append("IP:127.0.0.1")
    return sorted(sans)


# endregion FUNC_required_sans


# ═══════════════════════════════════════════════════════════════════
# region FUNC_get_cert_sans
# ═══════════════════════════════════════════════════════════════════
def get_cert_sans(cert_file: Path) -> list[str]:
    """Extract literal SAN entries from an existing PEM certificate.

    ▶ ┌cert_file┐ → ◇ is_file() ? → ⚡ openssl x509 -ext subjectAltName → ⊕ regex parse → ∑ normalize IP Address: → ∑ sorted → ⎋ list[str]

    ## @purpose — Parse literal SAN from PEM via openssl (same tool contract as the shell).
    ##            Normalizes "IP Address:" → "IP:" for consistent comparison with required_sans.
    ## @io — ⇥ cert_file: Path → ⎋ list[str] sorted DNS:/IP: entries (empty if missing/unparseable)
    ## @complexity — O(N) where N = SAN entries (subprocess I/O dominates)
    ## @invariants
    ##   - Missing file → [] (fail-soft, caller decides severity)
    ##   - Unparseable/empty openssl output → [] (never raises)
    ##   - openssl binary missing → [] (FileNotFoundError caught)
    ## @rationale — subprocess (not pyOpenSSL) keeps byte-compatible parsing with the
    ##              original shell grep/sed pipeline (DevPlan 099 §11 Design Decisions).
    """
    if not cert_file.is_file():
        _log(7, "get_cert_sans", f"Cert file not found: {cert_file} — no SAN")
        return []
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(cert_file), "-noout", "-ext", "subjectAltName"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        _log(7, "get_cert_sans", "openssl not found on PATH — cannot parse SAN")
        return []
    if result.returncode != 0 or not result.stdout.strip():
        _log(7, "get_cert_sans", f"Cannot parse SAN from {cert_file} — empty openssl output")
        return []
    entries = []
    for match in _SAN_RE.findall(result.stdout):
        entry = match.strip()
        if entry.startswith("IP Address:"):
            entry = "IP:" + entry[len("IP Address:") :]
        entries.append(entry)
    return sorted(entries)


# endregion FUNC_get_cert_sans


# ═══════════════════════════════════════════════════════════════════
# region FUNC_cert_is_current
# ═══════════════════════════════════════════════════════════════════
def cert_is_current(cert_file: Path, key_file: Path, platform_domain: str) -> bool:
    """Check if the existing cert satisfies idempotency: SAN ⊇ required AND not expiring.

    ▶ ┌cert+key+domain┐ → ◇ files exist ? → ◇ SAN parse ok ? → ◇ required ⊆ cert (set) → ◇ openssl -checkend 30d → ⎋ bool

    ## @purpose — Idempotency gate. Primary: every required SAN entry must exist in the
    ##            cert's literal SAN set (DD8 — exact match, no wildcard matching).
    ##            Secondary: cert must not expire within EXPIRY_CHECK_DAYS (30).
    ##            Fail-fast if cert or key file missing.
    ## @io — ⇥ cert_file: Path, key_file: Path, platform_domain: str → ⎋ bool
    ## @complexity — O(R + C + S) where R/C = required/cert SAN sizes, S = subprocess openssl
    ## @invariants
    ##   - Missing cert OR key → False (never partial-success)
    ##   - Unparseable SAN → False (conservative: regenerate)
    ##   - Expiry check failure (incl. openssl missing) → False
    ## @rationale — Exact-set inclusion (not wildcard matching) matches nginx literal
    ##              SAN requirements — a wildcard cert without the literal entry would
    ##              fail nginx server_name matching in dev overlays.
    """
    if not cert_file.is_file() or not key_file.is_file():
        _log(8, "cert_is_current", "Cert or key file missing — needs generation")
        return False

    required = required_sans(platform_domain)
    cert_sans = get_cert_sans(cert_file)

    if not cert_sans:
        _log(8, "cert_is_current", "Cannot parse SAN from existing cert — needs regeneration")
        return False

    _log(8, "cert_is_current", "Required SAN (sorted):")
    for entry in required:
        _log(8, "cert_is_current", f"  {entry}")
    _log(8, "cert_is_current", "Actual cert SAN (sorted):")
    for entry in cert_sans:
        _log(8, "cert_is_current", f"  {entry}")

    # Primary: literal SAN set inclusion check (DD8 — exact match, no wildcard matching)
    missing = [entry for entry in required if entry not in cert_sans]
    for entry in missing:
        _log(7, "cert_is_current", f"SAN entry missing: {entry}")

    if missing:
        _log(7, "cert_is_current", "SAN drift detected — needs regeneration")
        return False

    # Secondary: expiry check (30 day window)
    try:
        result = subprocess.run(
            [
                "openssl",
                "x509",
                "-in",
                str(cert_file),
                "-checkend",
                str(EXPIRY_CHECK_DAYS * 86400),
            ],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        _log(7, "cert_is_current", "openssl not found on PATH — cannot check expiry")
        return False
    if result.returncode != 0:
        _log(7, "cert_is_current", f"Cert expires within {EXPIRY_CHECK_DAYS} days — needs regeneration")
        return False

    _log(9, "cert_is_current", f"Cert is current (SAN matches, >{EXPIRY_CHECK_DAYS}d until expiry)")
    return True


# endregion FUNC_cert_is_current


# ═══════════════════════════════════════════════════════════════════
# region FUNC_generate_mkcert
# ═══════════════════════════════════════════════════════════════════
def generate_mkcert(dev_certs_dir: Path, sans: list[str]) -> Path:
    """Generate dev cert using mkcert.

    ▶ ┌dir+sans┐ → ⊕ strip prefixes → ⚡ mkcert -cert-file -key-file → ◇ rc==0 ? → ⎋ cert_file Path

    ## @purpose — mkcert backend: produces fullchain.pem + privkey.pem with browser-trusted
    ##            local CA (pre-installed by user — module NEVER runs `mkcert -install`).
    ## @io — ⇥ dev_certs_dir: Path, sans: list[str] (DNS:/IP: entries) → ⎋ cert_file: Path
    ## @complexity — O(N) + subprocess I/O
    ## @pre — mkcert is in PATH (checked by caller via _ensure_tool)
    ## @invariants
    ##   - Output file names fixed: fullchain.pem + privkey.pem
    ##   - Failure → IMP:9 error + sys.exit(1) (shell pipefail parity)
    """
    cert_file = dev_certs_dir / "fullchain.pem"
    key_file = dev_certs_dir / "privkey.pem"
    sans_list = " ".join(_strip_prefix(entry) for entry in sans)
    _log(7, "generate_mkcert", "Using mkcert backend")
    _log(8, "generate_mkcert", f"SAN: {sans_list}")

    dev_certs_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["mkcert", "-cert-file", str(cert_file), "-key-file", str(key_file), *sans_list.split()],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        _log(9, "generate_mkcert", "ERROR: mkcert not found on PATH")
        sys.exit(1)
    if result.returncode != 0:
        for line in (result.stderr or "").splitlines():
            _log(8, "mkcert", line)
        _log(9, "generate_mkcert", f"ERROR: mkcert failed (exit {result.returncode})")
        sys.exit(1)

    _log(9, "generate_mkcert", f"mkcert generated: {cert_file}")
    return cert_file


# endregion FUNC_generate_mkcert


# ═══════════════════════════════════════════════════════════════════
# region FUNC__write_openssl_config
# ═══════════════════════════════════════════════════════════════════
def _write_openssl_config(subject_alt_name: str) -> str:
    """Write the tempfile openssl req config with a SAN section.

    ▶ ┌subject_alt_name┐ → ⊕ mkstemp .cnf → ⊕ write [req]/[v3_req] → ⎋ cnf_path str

    ## @purpose — Shell heredoc equivalent: self-signed RSA 2048/SHA256 config with
    ##            keyUsage/extendedKeyUsage and subjectAltName. CN is always
    ##            `*.ai-platform.local` (shell parity — never context-dependent).
    ## @io — ⇥ subject_alt_name: str (comma-joined SAN list) → ⎋ str tempfile path
    ## @complexity — O(1)
    ## @invariants — Caller MUST os.unlink() the returned path (finally block).
    """
    fd, path = tempfile.mkstemp(suffix=".cnf")
    with os.fdopen(fd, "w") as f:
        f.write(
            "[req]\n"
            "distinguished_name = req_distinguished_name\n"
            "x509_extensions = v3_req\n"
            "prompt = no\n"
            "[req_distinguished_name]\n"
            "CN = *.ai-platform.local\n"
            "[v3_req]\n"
            "keyUsage = keyEncipherment, dataEncipherment, digitalSignature\n"
            "extendedKeyUsage = serverAuth\n"
            f"subjectAltName = {subject_alt_name}\n"
        )
    return path


# endregion FUNC__write_openssl_config


# ═══════════════════════════════════════════════════════════════════
# region FUNC_generate_openssl
# ═══════════════════════════════════════════════════════════════════
def generate_openssl(
    dev_certs_dir: Path,
    platform_domain: str,
    sans: list[str],
    expiry_days: int = EXPIRY_DAYS,
) -> Path:
    """Generate a self-signed dev cert using openssl req.

    ▶ ┌dir+domain+sans+expiry┐ → ⊕ cnf tempfile → ⚡ openssl req -x509 -newkey rsa:2048 → ◇ rc==0 ? → ⊕ rm cnf → ⎋ cert_file Path

    ## @purpose — openssl backend (CI-compatible, no brew/mkcert needed). RSA 2048, SHA256,
    ##            subjectAltName from the required SAN list. Output identical contract to mkcert.
    ## @io — ⇥ dev_certs_dir: Path, platform_domain: str (reserved — CN stays *.ai-platform.local),
    ##   sans: list[str], expiry_days: int = 825 → ⎋ cert_file: Path
    ## @complexity — O(N) + subprocess I/O (RSA keygen dominates)
    ## @pre — openssl is in PATH (checked by caller via _ensure_tool)
    ## @invariants
    ##   - Temp config always removed (finally) — no /tmp litter on failure
    ##   - Failure → IMP:9 error + sys.exit(1) (shell pipefail parity)
    ## @rationale — `platform_domain` param kept for signature parity with DevPlan Code
    ##              Graph; shell config hardcodes CN=*.ai-platform.local and this module
    ##              preserves that byte-for-byte (AC3 behaviour-preserving migration).
    """
    cert_file = dev_certs_dir / "fullchain.pem"
    key_file = dev_certs_dir / "privkey.pem"
    sans_list = ",".join(sans)
    _log(7, "generate_openssl", "Using openssl backend")
    _log(8, "generate_openssl", f"SAN: {sans_list}")

    dev_certs_dir.mkdir(parents=True, exist_ok=True)
    cnf_path = _write_openssl_config(sans_list)
    try:
        try:
            result = subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    str(expiry_days),
                    "-keyout",
                    str(key_file),
                    "-out",
                    str(cert_file),
                    "-config",
                    cnf_path,
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            _log(9, "generate_openssl", "ERROR: openssl not found on PATH")
            sys.exit(1)
        if result.returncode != 0:
            for line in (result.stderr or "").splitlines():
                _log(8, "openssl", line)
            _log(9, "generate_openssl", f"ERROR: openssl req failed (exit {result.returncode})")
            sys.exit(1)
    finally:
        os.unlink(cnf_path)

    _log(9, "generate_openssl", f"OpenSSL generated: {cert_file}")
    return cert_file


# endregion FUNC_generate_openssl


# ═══════════════════════════════════════════════════════════════════
# region FUNC_verify_san
# ═══════════════════════════════════════════════════════════════════
def verify_san(cert_file: Path, required_sans_list: list[str]) -> bool:
    """Verify that the generated certificate contains all required SAN entries.

    ▶ ┌cert+required┐ → ⚡ get_cert_sans → ⊕ set comparison → ◇ all present ? → ⎋ bool

    ## @purpose — Post-generation SAN verification (fail-fast): every required entry must
    ##            be present in the freshly generated cert — catches backend misconfig.
    ## @io — ⇥ cert_file: Path, required_sans_list: list[str] → ⎋ bool
    ## @complexity — O(R + C) where R/C = required/cert SAN sizes
    ## @invariants
    ##   - Any missing entry → False with IMP:9 per-entry + summary log
    ##   - Unparseable cert (empty get_cert_sans) → False (conservative)
    """
    cert_sans = get_cert_sans(cert_file)
    _log(8, "verify_san", "Verifying generated cert SAN...")
    missing = []
    for entry in required_sans_list:
        if entry not in cert_sans:
            _log(9, "verify_san", f"SAN MISSING: {entry}")
            missing.append(entry)
        else:
            _log(8, "verify_san", f"OK {entry}")
    if missing:
        _log(9, "verify_san", "SAN verification FAILED — missing entries above")
        return False
    _log(9, "verify_san", "All required SAN entries present")
    return True


# endregion FUNC_verify_san


# ═══════════════════════════════════════════════════════════════════
# region FUNC_sync_live_layout
# ═══════════════════════════════════════════════════════════════════
def sync_live_layout(dev_certs_dir: Path, live_root: Path, platform_domain: str) -> bool:
    """Mirror the generated dev cert into letsencrypt-style live/<domain>/ layout.

    ▶ ┌dev_certs_dir + live_root + platform_domain┐ → ◇ files exist? → ⊕ mkdir + copy2
    → ◇ both copied? → ⎋ bool

    ## @purpose — Dev-mode bridge: production-style nginx vhosts reference
    ##            /etc/letsencrypt/live/<domain>/fullchain.pem. In local dev the
    ##            compose stack maps NGINX_CERT_DIR=./dev-certs → /etc/letsencrypt,
    ##            so the same cert must also exist at <live_root>/live/<platform_domain>/.
    ##            This is env/config-driven (DEV_CERTS_LIVE_ROOT), NOT an if-local branch:
    ##            prod never runs make dev-certs (ACME is the cert channel there).
    ## @io — ⇥ dev_certs_dir: Path — flat cert source (fullchain.pem + privkey.pem)
    ##       ⇥ live_root: Path — letsencrypt-style root (e.g. <repo>/dev-certs)
    ##       ⇥ platform_domain: str — wildcard domain directory under live/
    ##       → ⎋ bool — True on success (or source missing → False)
    ## @complexity — O(1) file copies
    ## @invariants
    ##   - No-op (True) if source cert/key missing — never fabricates files
    ##   - Copies via shutil.copy2 (preserves mode/timestamps)
    ##   - Idempotent — repeated calls are byte-identical overwrites
    ##   - Returns False only on copy failure (logged IMP:9)
    """
    cert_src = dev_certs_dir / "fullchain.pem"
    key_src = dev_certs_dir / "privkey.pem"
    if not (cert_src.exists() and key_src.exists()):
        _log(8, "sync_live_layout", "Source flat cert missing — skipping live layout sync")
        return True

    live_dir = live_root / "live" / platform_domain
    try:
        live_dir.mkdir(parents=True, exist_ok=True)
        for src in (cert_src, key_src):
            shutil.copy2(src, live_dir / src.name)
    except OSError as e:
        _log(9, "sync_live_layout", f"ERROR: live layout sync failed: {e}")
        return False

    _log(
        9,
        "sync_live_layout",
        f"Dev cert mirrored to {live_dir}/ (letsencrypt-style live layout for local nginx)",
    )
    return True


# endregion FUNC_sync_live_layout


# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
# ═══════════════════════════════════════════════════════════════════
def main() -> None:
    """Main entrypoint: check idempotency → select backend → generate → verify.

    ▶ ┌env vars┐ → ◇ cert_is_current? → ◇ live layout sync → exit 0 → ◇ auto? → ⊕ which(mkcert) → ◇ backend → ⚡ generate → ◇ verify_san? → ⎋ sys.exit(0/1)

    ## @purpose — Orchestration. Reads DEV_CERTS_DIR / PLATFORM_DOMAIN / CERT_BACKEND /
    ##            DEV_CERTS_LIVE_ROOT from environment (Makefile or facade contract).
    ##            Exit 0 = valid cert (new or up-to-date), exit 1 = failure.
    ## @io — ⇥ env: DEV_CERTS_DIR (default: module dir /dev-certs), PLATFORM_DOMAIN
    ##   (default: ai-platform.local), CERT_BACKEND (default: auto),
    ##   DEV_CERTS_LIVE_ROOT (optional — letsencrypt-style mirror root) → ⎋ sys.exit(0|1)
    ## @complexity — O(1) + backend subprocess I/O
    ## @invariants
    ##   - Idempotent no-op path exits 0 BEFORE any subprocess call
    ##   - Live layout sync runs on BOTH paths (up-to-date + freshly generated)
    ##   - Unknown CERT_BACKEND → IMP:9 error + exit 1
    ##   - Explicit backend with missing tool → IMP:9 error + exit 1
    ##   - Post-generation SAN verification failure → IMP:9 error + exit 1
    ## @rationale — Env-var interface (not CLI args) preserves the `make dev-certs`
    ##              contract and the thin shell facade (DevPlan 099 §5.1).
    """
    dev_certs_dir = Path(os.environ.get("DEV_CERTS_DIR", DEFAULT_DEV_CERTS_DIR))
    platform_domain = os.environ.get("PLATFORM_DOMAIN", DEFAULT_PLATFORM_DOMAIN)
    cert_backend = os.environ.get("CERT_BACKEND", DEFAULT_CERT_BACKEND)
    live_root_env = os.environ.get("DEV_CERTS_LIVE_ROOT", "").strip()
    live_root = Path(live_root_env) if live_root_env else None
    cert_file = dev_certs_dir / "fullchain.pem"
    key_file = dev_certs_dir / "privkey.pem"

    _log(7, "main", "=== Dev certificate generator ===")
    _log(8, "main", f"DEV_CERTS_DIR={dev_certs_dir}")
    _log(8, "main", f"PLATFORM_DOMAIN={platform_domain}")
    _log(8, "main", f"CERT_BACKEND={cert_backend}")
    if live_root:
        _log(8, "main", f"DEV_CERTS_LIVE_ROOT={live_root} (letsencrypt-style mirror)")

    # ── Check idempotency ──────────────────────────────────────────────────────
    if cert_is_current(cert_file, key_file, platform_domain):
        _log(9, "main", "Cert up-to-date — no action needed")
        if live_root and not sync_live_layout(dev_certs_dir, live_root, platform_domain):
            sys.exit(1)
        sys.exit(0)

    # ── Select backend ─────────────────────────────────────────────────────────
    backend = cert_backend
    if backend == "auto":
        if _command_exists("mkcert"):
            _log(7, "main", "Auto-select: mkcert found in PATH")
            backend = "mkcert"
        else:
            _log(7, "main", "Auto-select: mkcert not found — falling back to openssl")
            backend = "openssl"

    # ── Generate ────────────────────────────────────────────────────────────────
    sans = required_sans(platform_domain)
    if backend == "mkcert":
        _ensure_tool("mkcert")
        generate_mkcert(dev_certs_dir, sans)
    elif backend == "openssl":
        _ensure_tool("openssl")
        generate_openssl(dev_certs_dir, platform_domain, sans, EXPIRY_DAYS)
    else:
        _log(9, "main", f"ERROR: Unknown CERT_BACKEND='{backend}'. Use auto|mkcert|openssl")
        sys.exit(1)

    # ── Verify generated cert SAN ──────────────────────────────────────────────
    if not verify_san(cert_file, sans):
        _log(9, "main", "FAILED: generated cert SAN verification failed")
        sys.exit(1)

    if live_root and not sync_live_layout(dev_certs_dir, live_root, platform_domain):
        sys.exit(1)

    _log(9, "main", "Certificate generated successfully")
    _log(
        7,
        "main",
        "Hint: run 'make -C core/modules/nginx restart' (or docker compose restart nginx) to pick up new cert",
    )
    sys.exit(0)


# endregion FUNC_main


if __name__ == "__main__":
    main()
