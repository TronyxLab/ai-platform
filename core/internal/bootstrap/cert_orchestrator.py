#!/usr/bin/env python3
# GREP_SUMMARY: cert-orchestrator, bulk-restore, s3-cache, acme-issue, ssl, letsencrypt, idempotent, graceful-degradation
# STRUCTURE: ▶ ┌domains list┐ → ○ for each domain: s3 check → s3 download → (miss?) issue-cert.sh → ⊕ CertResult → ⎋
# region MODULE_CONTRACT
## @purpose  Certificate orchestrator: bulk-restore SSL certs from S3 cache first,
##           then issue missing ones via acme.sh (issue-cert.sh).
##           Restore-first strategy minimizes acme.sh API calls and bootstrap latency.
## @scope    Called from state_machine.py deploy_context step (18.2 + 18.3).
##           Orchestrates s3-ssl-cache.sh (check/download/upload) and issue-cert.sh.
## @invariants
##   1. Restore-first: try S3 cache before acme.sh issue
##   2. Idempotent: valid certs (>30 days) are skipped
##   3. Non-fatal: failure of one domain does NOT block others
##   4. Cache: successful issue → upload to S3 for future restores (handled by issue-cert.sh)
##   5. Graceful: S3 unavailable → fall back to acme.sh only
##   6. All subprocess calls have 120s timeout (s3) / 300s timeout (issue)
## @rationale StatusReport 045: acme.sh DNS-01 issue is slow (60-120s per domain) and
##           can fail if DNS propagation is incomplete. S3 cache (bulk-restore) allows
##           instant cert restoration for previously-bootstrapped nodes, reducing
##           bootstrap time from minutes to seconds for cert phase.
## @changes  2026-07-22 | DevPlan 047 Phase 3 — Created cert orchestrator
## @changes  2026-07-23 | DevPlan 058 — ACME_CHALLENGE_MODE env var passthrough, DomainCertResult.challenge field
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
S3_TIMEOUT = 120  # seconds for s3-ssl-cache.sh operations
ISSUE_TIMEOUT = 300  # seconds for issue-cert.sh
CERT_VALIDITY_PATH = "/etc/letsencrypt/live"


# region DATACLASSES


@dataclass
class DomainCertResult:
    """Result of cert orchestration for a single domain.

    ## @purpose — Track per-domain cert status, source (S3 or acme), and errors.
    ## @io — ⇥ constructor params → ⎋ serializable result
    ## @complexity — O(1)
    """

    domain: str
    status: str = "pending"  # restored | issued | skipped | failed
    source: str = ""  # s3 | acme | skip | none
    challenge: str = ""  # dns | http — which challenge type was used for issuance
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        d = asdict(self)
        if self.error is None:
            d.pop("error", None)
        return d


@dataclass
class CertResult:
    """Aggregated result of cert orchestration across all domains.

    ## @purpose — Collect per-domain results and summary counts.
    ## @io — ⇥ domains list → ⎋ serializable result with per-domain breakdown
    ## @complexity — O(N) where N = number of domains
    """

    domains: dict[str, DomainCertResult] = field(default_factory=dict)
    restored: int = 0
    issued: int = 0
    skipped: int = 0
    failed: int = 0

    def add(self, result: DomainCertResult) -> None:
        """Add a per-domain result and increment summary counter."""
        self.domains[result.domain] = result
        if result.status == "restored":
            self.restored += 1
        elif result.status == "issued":
            self.issued += 1
        elif result.status == "skipped":
            self.skipped += 1
        elif result.status == "failed":
            self.failed += 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "domains": {k: v.to_dict() for k, v in self.domains.items()},
            "summary": {
                "restored": self.restored,
                "issued": self.issued,
                "skipped": self.skipped,
                "failed": self.failed,
            },
        }


# endregion DATACLASSES


# region ORCHESTRATION


# region FUNC_orchestrate_certs
## @purpose — Orchestrate cert restoration + issuance for a list of domains.
##            Restore-first: try S3 cache, then fall back to acme.sh issue.
## @io — ⇥ domains: list[str], s3_cache_script: str, issue_cert_script: str,
##       secrets_env: str → ⎋ CertResult
## @complexity — O(D * T) where D = domains, T = timeout per operation
## @invariants
##   - Each domain is processed independently (non-fatal on failure)
##   - Valid certs (>30 days, checked via s3-ssl-cache.sh check) are skipped
##   - S3 restore failure → fall back to issue-cert.sh
##   - All subprocess calls have timeout
def orchestrate_certs(
    domains: list[str],
    s3_cache_script: str,
    issue_cert_script: str,
    secrets_env: str = "",
) -> CertResult:
    """Restore certs from S3 first, issue missing ones via acme.sh.

    ▶ ┌domains┐ → ○ for each: s3 check → download → (miss?) issue → ⊕ CertResult → ⎋
    """
    result = CertResult()
    if not domains:
        logger.info("[IMP:7][cert_orchestrator] No domains to orchestrate — skipping")
        return result

    logger.info("[IMP:8][cert_orchestrator] Orchestrating certs for %d domains", len(domains))

    # Source secrets.env if provided (for WEBNAMES_API_KEY)
    if secrets_env and os.path.isfile(secrets_env):
        logger.info("[IMP:8][cert_orchestrator] Sourcing secrets.env: %s", secrets_env)
        _source_secrets_env(secrets_env)

    for domain in domains:
        if not domain:
            continue
        domain_result = _process_single_domain(domain, s3_cache_script, issue_cert_script)
        if domain_result is not None:
            result.add(domain_result)

    logger.info(
        "[IMP:9][cert_orchestrator] Done: restored=%d issued=%d skipped=%d failed=%d",
        result.restored,
        result.issued,
        result.skipped,
        result.failed,
    )
    return result


# endregion FUNC_orchestrate_certs


# region FUNC_process_single_domain
## @purpose — Process a single domain: check validity, restore from S3, or issue via acme.sh.
## @io — ⇥ domain: str, s3_cache_script: str, issue_cert_script: str → ⎋ DomainCertResult
## @complexity — O(T) where T = timeout per operation
## @invariants
##   - Step 1: Check if valid cert already exists on disk (skip if present)
##   - Step 2: Try S3 restore (check + download)
##   - Step 3: Fall back to issue-cert.sh if S3 miss/unavailable
##   - Non-fatal: any failure returns DomainCertResult(status="failed")
def _process_single_domain(
    domain: str,
    s3_cache_script: str,
    issue_cert_script: str,
) -> DomainCertResult:
    """Process a single domain through restore → issue pipeline."""
    logger.info("[IMP:8][cert_orchestrator] Processing domain: %s", domain)

    # ── Step 1: Check if cert already valid on disk ──
    cert_path = os.path.join(CERT_VALIDITY_PATH, domain, "fullchain.pem")
    if os.path.isfile(cert_path) and _is_cert_valid(domain, cert_path):
        logger.info("[IMP:9][cert_orchestrator] %s — valid cert on disk, skipping", domain)
        return DomainCertResult(domain=domain, status="skipped", source="disk")

    # ── Step 2: Try S3 restore ──
    if os.path.isfile(s3_cache_script):
        s3_result = _try_s3_restore(domain, s3_cache_script)
        if s3_result.status == "restored":
            return s3_result
        logger.info("[IMP:7][cert_orchestrator] %s — S3 miss/unavailable, falling back to issue", domain)
    else:
        logger.warning("[IMP:7][cert_orchestrator] s3-ssl-cache.sh not found: %s", s3_cache_script)

    # ── Step 3: Fall back to issue-cert.sh ──
    if os.path.isfile(issue_cert_script):
        return _issue_cert(domain, issue_cert_script)

    logger.error("[IMP:10][cert_orchestrator] %s — no issue-cert.sh available", domain)
    return DomainCertResult(
        domain=domain,
        status="failed",
        source="none",
        error=f"Neither S3 cache nor issue-cert.sh available for {domain}",
    )


# endregion FUNC_process_single_domain


# region FUNC_is_cert_valid
## @purpose — Check if a certificate on disk is valid (>30 days remaining)
##            AND issued by a trusted CA (Let's Encrypt).
##            Uses openssl x509 -checkend + -issuer.
## @io — ⇥ domain: str, cert_path: str → ⎋ bool (True = valid LE cert >30 days)
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Returns False if cert file missing or unparseable
##   - Uses openssl x509 -checkend 2592000 (30 days in seconds)
##   - ⚠️ TRAP[BUG] · 2026-07-22 · P0 · mkcert certs passed as "valid" — no issuer check
##   - · Symptom: mkcert/dev certs at /etc/letsencrypt/live/ survived bootstrap
##   - · Root: _is_cert_valid() checked only expiry, not issuer trustworthiness
##   - · Fix: added _is_le_issuer() check — rejects non-LE certs (mkcert, self-signed)
##   - · Prevention: any cert at /etc/letsencrypt/live/ must have LE issuer to be considered valid
def _is_cert_valid(domain: str, cert_path: str) -> bool:
    """Check if cert at cert_path is valid (>30 days) AND from Let's Encrypt."""
    try:
        # Check 1: Cert not expired (>30 days remaining)
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-checkend", "2592000", "-noout"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.info("[IMP:7][cert_orchestrator] %s — cert expires within 30 days or unparseable", domain)
            return False

        # Check 2: Issuer must be Let's Encrypt (reject mkcert, self-signed, etc.)
        if not _is_le_issuer(cert_path):
            logger.warning(
                "[IMP:9][cert_orchestrator] %s — cert on disk is NOT from Let's Encrypt (mkcert/self-signed?), re-issuing",
                domain,
            )
            return False

        logger.info("[IMP:9][cert_orchestrator] %s — valid LE cert >30 days", domain)
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — openssl check failed: %s", domain, e)
        return False


# endregion FUNC_is_cert_valid


# region FUNC_is_le_issuer
## @purpose — Check if a certificate was issued by Let's Encrypt.
##            Uses openssl x509 -issuer to extract the issuer field.
## @io — ⇥ cert_path: str → ⎋ bool (True = LE issuer)
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Returns False if cert missing, unparseable, or issuer doesn't contain "Let's Encrypt"
##   - Case-insensitive match on issuer string
def _is_le_issuer(cert_path: str) -> bool:
    """Check if cert at cert_path has Let's Encrypt issuer."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-issuer", "-noout"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        issuer = result.stdout.strip()
        is_le = "Let's Encrypt" in issuer
        if not is_le:
            logger.info("[IMP:7][cert_orchestrator] Cert issuer: %s", issuer[:120])
        return is_le
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# endregion FUNC_is_le_issuer


# region FUNC_try_s3_restore
## @purpose — Try to restore a cert from S3 cache (check + download).
## @io — ⇥ domain: str, s3_cache_script: str → ⎋ DomainCertResult
## @complexity — O(T) where T = S3 timeout
## @invariants
##   - Step 1: s3-ssl-cache.sh check <domain> (exit 0 = valid cert in S3)
##   - Step 2: s3-ssl-cache.sh download <domain> (exit 0 = restored)
##   - Returns status="restored" on success, status="pending" on miss
def _try_s3_restore(domain: str, s3_cache_script: str) -> DomainCertResult:
    """Try S3 check + download. Returns restored result or pending."""
    try:
        # Check S3 cache
        check = subprocess.run(
            ["bash", s3_cache_script, "check", domain],
            capture_output=True,
            text=True,
            timeout=S3_TIMEOUT,
        )
        if check.returncode != 0:
            logger.info("[IMP:7][cert_orchestrator] %s — S3 cache miss", domain)
            return DomainCertResult(domain=domain, status="pending", source="s3")

        # Download from S3
        download = subprocess.run(
            ["bash", s3_cache_script, "download", domain],
            capture_output=True,
            text=True,
            timeout=S3_TIMEOUT,
        )
        if download.returncode != 0:
            logger.warning("[IMP:7][cert_orchestrator] %s — S3 download failed", domain)
            return DomainCertResult(
                domain=domain,
                status="pending",
                source="s3",
                error=download.stderr.strip()[:200] if download.stderr else "download failed",
            )

        # Verify cert exists on disk after download
        cert_path = os.path.join(CERT_VALIDITY_PATH, domain, "fullchain.pem")
        if os.path.isfile(cert_path):
            logger.info("[IMP:9][cert_orchestrator] %s — cert restored from S3", domain)
            return DomainCertResult(domain=domain, status="restored", source="s3")

        logger.warning("[IMP:7][cert_orchestrator] %s — S3 download OK but cert not on disk", domain)
        return DomainCertResult(
            domain=domain,
            status="pending",
            source="s3",
            error="download succeeded but cert file missing",
        )
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 operation timed out", domain)
        return DomainCertResult(domain=domain, status="pending", source="s3", error="timeout")
    except FileNotFoundError as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 script error: %s", domain, e)
        return DomainCertResult(domain=domain, status="pending", source="s3", error=str(e))


# endregion FUNC_try_s3_restore


# region FUNC_issue_cert
## @purpose — Issue a cert via issue-cert.sh (acme.sh DNS-01).
##            Non-fatal: failure logs WARN and returns failed result.
## @io — ⇥ domain: str, issue_cert_script: str → ⎋ DomainCertResult
## @complexity — O(T) where T = ISSUE_TIMEOUT
## @invariants
##   - Sets PLATFORM_DOMAIN env var for the domain being issued
##   - issue-cert.sh handles idempotency internally (skips if cert exists)
##   - Non-fatal: failure returns status="failed"
def _issue_cert(domain: str, issue_cert_script: str) -> DomainCertResult:
    """Issue cert via issue-cert.sh. Returns issued or failed result."""
    challenge_mode = os.environ.get("ACME_CHALLENGE_MODE", "dns")
    logger.info("[IMP:9][cert_orchestrator] %s — issuing via acme.sh (challenge=%s)", domain, challenge_mode)
    # Set env for issue-cert.sh (it reads PLATFORM_DOMAIN + ACME_CHALLENGE_MODE)
    env = os.environ.copy()
    env["PLATFORM_DOMAIN"] = domain
    env["ACME_CHALLENGE_MODE"] = challenge_mode
    try:
        result = subprocess.run(
            ["bash", issue_cert_script],
            capture_output=True,
            text=True,
            timeout=ISSUE_TIMEOUT,
            env=env,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][cert_orchestrator] %s — cert issued successfully (challenge=%s)", domain, challenge_mode)
            return DomainCertResult(
                domain=domain,
                status="issued",
                source="acme",
                challenge=challenge_mode,
            )
        logger.warning(
            "[IMP:7][cert_orchestrator] %s — issue-cert.sh failed (exit=%d): %s",
            domain,
            result.returncode,
            result.stderr.strip()[:200] if result.stderr else "unknown",
        )
        return DomainCertResult(
            domain=domain,
            status="failed",
            source="acme",
            challenge=challenge_mode,
            error=result.stderr.strip()[:200] if result.stderr else f"exit={result.returncode}",
        )
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][cert_orchestrator] %s — issue-cert.sh timed out", domain)
        return DomainCertResult(domain=domain, status="failed", source="acme", challenge=challenge_mode, error="timeout")
    except FileNotFoundError as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — issue-cert.sh error: %s", domain, e)
        return DomainCertResult(domain=domain, status="failed", source="acme", challenge=challenge_mode, error=str(e))


# endregion FUNC_issue_cert


# endregion ORCHESTRATION


# region HELPERS


# region FUNC_source_secrets_env
## @purpose — Source secrets.env file to load WEBNAMES_API_KEY into environment.
##            Required for acme.sh DNS-01 challenges.
## @io — ⇥ secrets_env_path: str → ⎋ None (side-effect: env vars set)
## @complexity — O(1)
## @invariants
##   - Non-fatal: if source fails, logs WARN
##   - Only exports env vars, does not modify the file
def _source_secrets_env(secrets_env_path: str) -> None:
    """Source secrets.env to load WEBNAMES_API_KEY and other secrets."""
    try:
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"set -a; source '{secrets_env_path}'; set +a; unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; env",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Parse env output and update os.environ
            for line in result.stdout.splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    if key.startswith(("WEBNAMES", "S3_", "PLATFORM_")):
                        os.environ[key] = value
            # Defence-in-depth: strip proxy vars that leaked from secrets.env
            for proxy_var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "NO_PROXY", "no_proxy"):
                os.environ.pop(proxy_var, None)
            logger.info("[IMP:9][cert_orchestrator] Secrets loaded from %s", secrets_env_path)
            # Validate WEBNAMES_API_KEY format — must include leading asterisk
            webnames_key = os.environ.get("WEBNAMES_API_KEY", "")
            if webnames_key and not webnames_key.startswith("*"):
                logger.warning(
                    "[IMP:9][cert_orchestrator] WEBNAMES_API_KEY missing leading '*' — "
                    "webnames.ru API may return zone_manager_unavailable. "
                    "The key shown in webnames control panel includes the asterisk prefix."
                )
        else:
            logger.warning("[IMP:7][cert_orchestrator] Failed to source %s", secrets_env_path)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("[IMP:7][cert_orchestrator] Error sourcing secrets: %s", e)


# endregion FUNC_source_secrets_env


# endregion HELPERS
