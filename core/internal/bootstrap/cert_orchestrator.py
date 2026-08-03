#!/usr/bin/env python3
# GREP_SUMMARY: cert-orchestrator, bulk-restore, s3-cache, acme-issue, ssl, letsencrypt, idempotent, graceful-degradation, secrets_env_parser
# STRUCTURE: ▶ ┌domains list┐ → ○ for each domain: s3 check → s3 download → (miss?) issue-cert.sh → ⊕ CertResult → ⎋
# region MODULE_CONTRACT
## @purpose  Certificate orchestrator: bulk-restore SSL certs from S3 cache first,
##           then issue missing ones via acme.sh (issue-cert.sh).
##           Restore-first strategy minimizes acme.sh API calls and bootstrap latency.
## @scope    Called from state_machine.py deploy_context step (18.2 + 18.3).
##           Orchestrates the S3 SSL cache (check/download/upload) and issue-cert.sh.
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
## @changes  2026-07-30 | DevPlan 086 — Migrated _source_secrets_env() from bash subprocess to shared secrets_env_parser.parse()
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any

from core.internal.config import platform_config
from core.internal.shared.deploy_paths import letsencrypt_live  # C7: единый резолвер /etc/letsencrypt/live
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    PlatformFatalError,
)
from core.internal.shared.secrets_env_parser import parse as parse_secrets_env
from core.internal.shared.ssl_certs import (
    DEFAULT_OPENSSL_TIMEOUT,  # B5: канон openssl-таймаута (литерал 30 удалён)
    cert_get_subject,  # FL15 (DevPlan 125 T5): SAN/subject-разбор для wildcard-покрытия
    cert_is_valid,  # C9: единая комбинация «cert валиден» (DevPlan 118 C9); _is_cert_valid удалён
    cert_subject_matches_domain,  # FL15 (DevPlan 125 T5): CN-матчинг direct/wildcard
)

logger = logging.getLogger(__name__)

# ── Direct import of s3_ssl_cache (DevPlan 052 Phase 1) ──
# Replaces subprocess calls to the legacy shell S3 cache with direct Python calls.
# Eliminates subshell credential propagation bug — S3_* env vars are read
# directly by s3_ssl_cache functions from os.environ (no subshell).
# ⚠️ TRAP[BUG] 2026-08-03 · top-level `import s3_ssl_cache` ломался на VPS
# · Symptom: прод-бустрап φ7 — «s3_ssl_cache module not available» при работающем
#   boto3 (s3_ssl_cache.py сам использует ТОЛЬКО dotted core.internal импорты).
# · Root: top-level import требует bootstrap-директорию в sys.path; cli.py (VPS)
#   запускается без неё → ImportError → s3_ssl_cache=None → S3 cache выключен.
# · Fix: канонический dotted-импорт from core.internal.bootstrap import s3_ssl_cache.
try:
    from core.internal.bootstrap import s3_ssl_cache
except ImportError:
    s3_ssl_cache = None  # type: ignore[assignment]
    logger.warning("[IMP:7][cert_orchestrator] s3_ssl_cache module not available — S3 operations disabled")

# ── Constants ──────────────────────────────────────────────────────────────
S3_TIMEOUT = 120  # seconds for S3 cache operations
ISSUE_TIMEOUT = 300  # seconds for issue-cert.sh
CERT_VALIDITY_PATH = str(letsencrypt_live())  # C7: единый резолвер shared/deploy_paths


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
## @io — ⇥ domains: list[str], issue_cert_script: str,
##       secrets_env: str → ⎋ CertResult
## @complexity — O(D * T) where D = domains, T = timeout per operation
## @invariants
##   - Each domain is processed independently (non-fatal on failure)
##   - Valid certs (>30 days, checked via S3 cache check) are skipped
##   - S3 restore failure → fall back to issue-cert.sh
##   - All subprocess calls have timeout
def orchestrate_certs(
    domains: list[str],
    issue_cert_script: str,
    secrets_env: str = "",
    migrate_cron: bool = False,
) -> CertResult:
    """Restore certs from S3 first, issue missing ones via acme.sh.

    ▶ ┌domains┐ → ○ for each: s3 check → download → (miss?) issue → ⊕ CertResult → ⎋
    """
    result = CertResult()

    # T0.3 (048.P3): Fallback — try PLATFORM_DOMAIN env var if no domains provided
    if not domains:
        pd = os.environ.get("PLATFORM_DOMAIN", "").strip()
        if pd:
            domains = [pd]
            logger.info("[IMP:7][cert_orchestrator] Using PLATFORM_DOMAIN from env: %s", pd)

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
        domain_result = _process_single_domain(domain, issue_cert_script)
        if domain_result is not None:
            result.add(domain_result)

    # ── Install cron after any certs were processed ──
    if result.restored > 0 or result.issued > 0 or result.skipped > 0:
        _install_cron()
        # ── Migrate old cron entries if requested (bootstrap init) ──
        if migrate_cron:
            migrate_cron_if_needed()

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
##            Calls _upload_to_s3() on skip (disk present) and after successful issue.
## @io — ⇥ domain: str, issue_cert_script: str → ⎋ DomainCertResult
## @complexity — O(T) where T = timeout per operation
## @invariants
##   - Step 1: Check if valid cert already exists on disk (skip + upload to S3)
##   - Step 2: Try S3 restore (check + download via direct import)
##   - Step 3: Fall back to issue-cert.sh if S3 miss/unavailable
##   - After successful issue, upload to S3
##   - Non-fatal: any failure returns DomainCertResult(status="failed")
def _process_single_domain(
    domain: str,
    issue_cert_script: str,
) -> DomainCertResult:
    """Process a single domain through restore → issue pipeline."""
    logger.info("[IMP:8][cert_orchestrator] Processing domain: %s", domain)

    # ── Step 1: Check if cert already valid on disk ──
    cert_path = os.path.join(CERT_VALIDITY_PATH, domain, "fullchain.pem")
    if os.path.isfile(cert_path) and cert_is_valid(cert_path):  # C9: единая комбинация shared/ssl_certs
        logger.info("[IMP:9][cert_orchestrator] %s — valid cert on disk, uploading to S3", domain)
        _upload_to_s3(domain)  # Always sync to S3 (DevPlan 052 §4.5)
        return DomainCertResult(domain=domain, status="skipped", source="disk_synced")

    # ── Step 2: Try S3 restore via direct import (no subprocess) ──
    if s3_ssl_cache is not None:
        s3_result = _try_s3_restore(domain)
        if s3_result.status == "restored":
            return s3_result
        logger.info("[IMP:7][cert_orchestrator] %s — S3 miss/unavailable, falling back to issue", domain)
    else:
        logger.warning("[IMP:7][cert_orchestrator] s3_ssl_cache module not loaded — S3 restore unavailable")

    # ── Step 3: Fall back to issue-cert.sh ──
    if os.path.isfile(issue_cert_script):
        result = _issue_cert(domain, issue_cert_script)
        if result.status == "issued":
            _upload_to_s3(domain)  # Upload after successful issue (DevPlan 052 §4.5)
            # ── FL15 (DevPlan 125 T5): покрытие домена после issue ──
            # issue-cert.sh SKIP'ает поддомены уже выпущенного wildcard'а с rc=0 →
            # «issued successfully» без сертификата live/<domain>/ → ложный alarm «Missing cert».
            # Проверяем реальное покрытие (direct | wildcard родителя); только отсутствие
            # покрытия → WARN (не alarm): INFO «covered by wildcard» — НЕ alarm (FL15).
            _log_post_issue_coverage(domain)
            return result
        # issue failed — fall through to self-signed
        logger.warning("[IMP:8][cert_orchestrator] %s — issue-cert.sh failed, trying self-signed fallback", domain)
    else:
        logger.warning("[IMP:8][cert_orchestrator] %s — no issue-cert.sh, trying self-signed fallback", domain)

    # ── Step 4: Self-signed as last resort (DevPlan 053 F6) ──
    # Both S3 restore and acme.sh issue failed — generate self-signed
    # to prevent nginx crash-loop. Monitoring should alert on self_signed source.
    logger.warning(
        "[IMP:8][cert_orchestrator] %s — all issuance methods failed, generating self-signed fallback", domain
    )
    return _generate_self_signed(domain)


# endregion FUNC_process_single_domain


# region FUNC_try_s3_restore
## @purpose — Try to restore a cert from S3 via s3_ssl_cache (direct import, no subprocess).
##            Replaces subprocess calls to the legacy shell S3 cache (DevPlan 052 Phase 1).
##            Eliminates subshell credential propagation bug.
## @io — ⇥ domain: str → ⎋ DomainCertResult
## @complexity — O(T) where T = S3 round-trip time
## @invariants
##   - Step 1: s3_ssl_cache.check_cert(domain, s3_bucket) → bool
##   - Step 2: s3_ssl_cache.download_cert(domain, ...) → bool
##   - Returns status="restored" on success, status="pending" on miss
def _try_s3_restore(domain: str) -> DomainCertResult:
    """Try S3 check + download via s3_ssl_cache (direct import, no subprocess).

    ## @rationale DevPlan 052 Phase 1: Replace subprocess.run calls with direct
    ##            s3_ssl_cache function calls. S3_* env vars are read directly
    ##            from os.environ by s3_ssl_cache — no subshell credential loss.
    """
    if s3_ssl_cache is None:
        logger.warning("[IMP:7][cert_orchestrator] s3_ssl_cache module not available — S3 restore disabled")
        return DomainCertResult(domain=domain, status="pending", source="s3")

    s3_bucket = os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
    if not s3_bucket:
        logger.warning("[IMP:7][cert_orchestrator] S3_BUCKET not set — S3 restore unavailable")
        return DomainCertResult(domain=domain, status="pending", source="s3")

    try:
        # Step 1: Check S3 cache via direct import
        if not s3_ssl_cache.check_cert(domain, s3_bucket):
            logger.info("[IMP:7][cert_orchestrator] %s — S3 cache miss", domain)
            return DomainCertResult(domain=domain, status="pending", source="s3")

        # Step 2: Download from S3 via direct import
        cert_dir = CERT_VALIDITY_PATH  # C7: единый резолвер shared/deploy_paths (литерал удалён)
        acme_home = "/opt/acme.sh"
        if not s3_ssl_cache.download_cert(domain, cert_dir, acme_home, s3_bucket):
            logger.warning("[IMP:7][cert_orchestrator] %s — S3 download failed", domain)
            return DomainCertResult(
                domain=domain,
                status="pending",
                source="s3",
                error="download failed",
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
    except (ConfigNotFoundError, ConfigParseError, PlatformFatalError, OSError) as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 operation failed: %s", domain, e)
        return DomainCertResult(domain=domain, status="pending", source="s3", error=f"{type(e).__name__}: {e}")


# endregion FUNC_try_s3_restore


# region FUNC_upload_to_s3
## @purpose — Upload cert files to S3 via s3_ssl_cache (direct import, no subprocess).
##            Called on skip (cert on disk) and after successful acme.sh issue.
##            Non-fatal: returns False on failure, never raises.
## @io — ⇥ domain: str → ⎋ bool (True = upload succeeded)
## @complexity — O(N) where N = files to upload (~4)
## @invariants
##   - Returns False if S3_BUCKET not set (S3 not configured)
##   - Non-fatal: failure logs WARN, returns False
##   - Uses s3_ssl_cache.upload_cert() directly (same process, no subshell)
## @rationale DevPlan 052 §4.4: Guaranteed S3 upload on every cert path
##           (skip, restore, issue) prevents cert loss for platform domain.
def _upload_to_s3(domain: str) -> bool:
    """Upload cert to S3 via s3_ssl_cache (direct import)."""
    # ⚠️ TRAP[BUG] 2026-08-03 · NoneType.upload_cert (прод-бустрап φ7)
    # · Symptom: 'SSL provision failed (non-fatal): 'NoneType' object has no attribute
    #   'upload_cert'' — после успешного issue cert (bootstrap tronyx-vps run3).
    # · Root: guard s3_ssl_cache is None был только в try_s3_restore, НЕ в _upload_to_s3.
    # · Fix: ранний return False при недоступном s3_ssl_cache (S3 опциональна).
    if s3_ssl_cache is None:
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 upload skipped (module unavailable)", domain)
        return False
    s3_bucket = os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
    if not s3_bucket:
        return False
    try:
        return s3_ssl_cache.upload_cert(domain, CERT_VALIDITY_PATH, "/opt/acme.sh", s3_bucket)
    except (ConfigNotFoundError, ConfigParseError, PlatformFatalError, OSError) as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — S3 upload failed: %s", domain, e)
        return False


# endregion FUNC_upload_to_s3


# region FUNC_issue_cert
## @purpose — Issue a cert via issue-cert.sh (acme.sh DNS-01).
##            Non-fatal: failure logs WARN and returns failed result.
## @io — ⇥ domain: str, issue_cert_script: str → ⎋ DomainCertResult
## @complexity — O(T) where T = ISSUE_TIMEOUT
## @invariants
##   - Sets PLATFORM_DOMAIN env var for the domain being issued
##   - issue-cert.sh handles idempotency internally (skips if cert exists)
##   - Non-fatal: failure returns status="failed"
## ⚠️ TRAP[DECISION] · 2026-07-25 · — · issue-cert.sh kept as acme.sh executor (not absorbed into Python)
## · Rejected: full Python port of acme.sh DNS-01/HTTP-01 interaction
## · Reason: webnames API key shred protocol + acme.sh edge cases are battle-tested in shell.
##   Python absorption would require re-testing all LE staging/production edge cases.
##   Strangler-Fig: Python absorbs orchestration, shell stays as executor.
## · Rev: when acme.sh interaction stabilizes (no changes for 6+ months), port to Python.
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
            logger.info(
                "[IMP:9][cert_orchestrator] %s — cert issued successfully (challenge=%s)", domain, challenge_mode
            )
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
        return DomainCertResult(
            domain=domain, status="failed", source="acme", challenge=challenge_mode, error="timeout"
        )
    except FileNotFoundError as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — issue-cert.sh error: %s", domain, e)
        return DomainCertResult(
            domain=domain, status="failed", source="acme", challenge=challenge_mode, error=f"{type(e).__name__}: {e}"
        )


# endregion FUNC_issue_cert


# region FUNC_generate_self_signed
## @purpose — Generate self-signed certificate as last-resort fallback (F6).
##            Called when BOTH S3 restore and acme.sh issue fail (e.g., DNS API down,
##            no credentials). Self-signed cert allows nginx to start (avoids crash-loop),
##            but browsers will show security warning. Valid 90 days.
## @io — ⇥ domain: str → ⎋ DomainCertResult
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Generates 2048-bit RSA key + self-signed x509 cert valid 90 days
##   - Non-fatal: returns failed result on error
##   - Logs WARN on success (must be replaced with real cert)
##   - Sets proper file permissions (key=0600, cert=0644)
def _generate_self_signed(domain: str) -> DomainCertResult:
    """Generate self-signed certificate as last-resort fallback.

    ## @purpose — Disaster recovery: keep nginx running when cert issuance fails.
    ## @rationale F6: self-signed cert allows nginx to start (avoids crash-loop),
    ##            but monitoring should alert on self_signed source.
    """
    cert_dir = os.path.join(CERT_VALIDITY_PATH, domain)
    os.makedirs(cert_dir, exist_ok=True)

    key_path = os.path.join(cert_dir, "privkey.pem")
    cert_path = os.path.join(cert_dir, "fullchain.pem")

    try:
        # Generate RSA private key (B5: канон DEFAULT_OPENSSL_TIMEOUT — литерал 30 удалён)
        subprocess.run(
            ["openssl", "genrsa", "-out", key_path, "2048"],
            capture_output=True,
            timeout=DEFAULT_OPENSSL_TIMEOUT,
            check=True,
        )
        os.chmod(key_path, 0o600)

        # Generate self-signed x509 certificate
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-x509",
                "-key",
                key_path,
                "-out",
                cert_path,
                "-days",
                "90",
                "-subj",
                f"/CN={domain}",
            ],
            capture_output=True,
            timeout=DEFAULT_OPENSSL_TIMEOUT,
            check=True,
        )
        os.chmod(cert_path, 0o644)

        logger.warning(
            "[IMP:7][cert_orchestrator] %s — SELF-SIGNED cert generated (browsers will warn). "
            "Fix: ensure DNS-01 credentials in secrets.env or wait for acme.sh retry.",
            domain,
        )
        return DomainCertResult(domain=domain, status="issued", source="self_signed")
    except (OSError, FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.warning("[IMP:7][cert_orchestrator] %s — self-signed generation failed: %s", domain, e)
        return DomainCertResult(domain=domain, status="failed", source="none", error=f"{type(e).__name__}: {e}")


# endregion FUNC_generate_self_signed


# region FUNC_install_cron
## @purpose — Lazy facade for cron_installer.install_acme_cron (DevPlan 117 G T58.4).
## @io — ⇥ acme_home: str → ⎋ bool (True = cron installed or already present)
## @complexity — O(1) + delegate
## @invariants
##   - Includes --renew-hook to upload certs to S3 after each renewal
##   - Non-fatal: failure logs WARN, returns False
##   - Idempotent: no-op if cron entry already has s3_ssl_cache reference
def _install_cron(acme_home: str = "/opt/acme.sh") -> bool:
    """Install acme.sh --install-cronjob + --renew-hook with S3 upload."""
    from core.internal.bootstrap.cron_installer import install_acme_cron as _impl

    return _impl(acme_home)


# endregion FUNC_install_cron


# region FUNC_migrate_cron_if_needed
## @purpose — Lazy facade for cron_installer.migrate_acme_cron_if_needed (DevPlan 117 G T58.4).
## @io — ⇥ acme_home: str → ⎋ bool (True = migration succeeded or was not needed)
## @complexity — O(1) + delegate
## @invariants
##   - Idempotent: if cron already has s3_ssl_cache reference, skips
##   - Non-fatal: failure logs WARN, returns False
##   - Non-fatal: no crontab → returns True (nothing to migrate)
##   - Runs on bootstrap init (step_18_deploy_context) and update
## @rationale DRIFT-C4: old nginx/install.sh _acme_install_cron() installed
##            cron WITHOUT --renew-hook for S3 upload.
def migrate_cron_if_needed(acme_home: str = "/opt/acme.sh") -> bool:
    """Check crontab for old acme.sh entry (no S3 sync) → replace with new one."""
    from core.internal.bootstrap.cron_installer import migrate_acme_cron_if_needed as _impl

    return _impl(acme_home)


# endregion FUNC_migrate_cron_if_needed


# endregion ORCHESTRATION


# region HELPERS


# region FUNC_log_post_issue_coverage
## @purpose  Проверить покрытие домена после issue-cert.sh (FL15, DevPlan 125 T5):
##            direct-сертификат live/{domain}/ ИЛИ wildcard родителя (*.tronyx.ru покрывает
##            botanika.tronyx.ru). issue-cert.sh SKIP'ает поддомены wildcard'а с rc=0 —
##            прежняя проверка только rc давала ложный alarm «Missing cert».
## @io       ⇥ domain: str → ⎋ str («direct» | «wildcard:parent» | «none»)
## @complexity — O(ancestors) — до 2 openssl subject-проверок
## @invariants
##   - direct: live/{domain}/fullchain.pem с subject, покрывающим domain (exact CN)
##   - wildcard: live/{parent}/fullchain.pem с CN = *.parent (cert_subject_matches_domain)
##   - INFO «covered by wildcard» — НЕ alarm; только реальное отсутствие покрытия → WARN (FL15)
##   - Non-fatal: openssl ошибки → «none» (WARN-путь, никогда не raise)
def _log_post_issue_coverage(domain: str) -> str:
    """Проверить покрытие домена (direct или wildcard родителя) и залогировать вердикт (FL15)."""
    # 1. Direct: сертификат самого домена
    direct = os.path.join(CERT_VALIDITY_PATH, domain, "fullchain.pem")
    if os.path.isfile(direct):
        subject = cert_get_subject(direct)
        if subject and cert_subject_matches_domain(subject, domain):
            logger.info(
                "[IMP:9][cert_orchestrator] %s — covered by direct cert (live/%s/fullchain.pem)", domain, domain
            )
            return "direct"

    # 2. Wildcard: *.parent покрывает поддомен (только для subdomains — parent != domain)
    labels = domain.split(".")
    for i in range(1, len(labels) - 1):
        parent = ".".join(labels[i:])
        wildcard_path = os.path.join(CERT_VALIDITY_PATH, parent, "fullchain.pem")
        if not os.path.isfile(wildcard_path):
            continue
        subject = cert_get_subject(wildcard_path)
        if subject and cert_subject_matches_domain(subject, parent):
            logger.info(
                "[IMP:9][cert_orchestrator] %s — covered by wildcard %s (issue-cert SKIP поддомена), НЕ alarm (FL15)",
                domain,
                f"*.{parent}",
            )
            return f"wildcard:{parent}"

    logger.warning(
        "[IMP:7][cert_orchestrator] %s — NO cert coverage after issue (ни direct, ни wildcard родителя) — "
        "возможен «Missing cert» alarm; проверьте каталог сертификатов %s",
        domain,
        CERT_VALIDITY_PATH,
    )
    return "none"


# endregion FUNC_log_post_issue_coverage


# region FUNC_source_secrets_env
## @purpose  Source secrets.env file to load WEBNAMES_API_KEY into environment.
##            Required for acme.sh DNS-01 challenges.
## @io — ⇥ secrets_env_path: str → ⎋ None (side-effect: env vars set)
## @complexity — O(1)
## @invariants
##   - Non-fatal: if source fails, logs WARN
##   - Only exports env vars, does not modify the file
def _source_secrets_env(secrets_env_path: str) -> None:
    """Source secrets.env to load WEBNAMES_API_KEY and other secrets.

    Uses shared secrets_env_parser.parse() instead of bash subprocess with
    `set -a; source`. Eliminates subshell credential-propagation bug and
    removes subprocess dependency for secrets parsing.
    """
    try:
        # ── Parse secrets.env via shared parser (no subprocess) ──
        parsed = parse_secrets_env(secrets_env_path)

        # ── Prefix filter: only WEBNAMES, S3_, PLATFORM_ ──
        target_prefixes = ("WEBNAMES", "S3_", "PLATFORM_")
        for key, value in parsed.items():
            if key.startswith(target_prefixes):
                os.environ[key] = value
                logger.debug("[IMP:8][cert_orchestrator] Set env: %s", key)

        # ── Defence-in-depth: strip proxy vars that leaked from secrets.env ──
        for proxy_var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "NO_PROXY", "no_proxy"):
            os.environ.pop(proxy_var, None)

        logger.info(
            "[IMP:9][cert_orchestrator] Secrets loaded from %s (%d entries matched)", secrets_env_path, len(parsed)
        )

        # ══════════════════════════════════════════════════════════
        # Validate WEBNAMES_API_KEY format — must include leading asterisk
        # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · FALSE DIAGNOSIS: zone_manager_unavailable ≠ DNS-01 broken
        # · Symptom: webnames.ru API returns {"result":"ERROR","details":"zone_manager_unavailable"}
        #   for `domains_list` action only. TXT record add/delete work correctly.
        # · Reality: DNS-01 via webnames.ru WORKS for certificate issuance.
        #   Verified 2026-07-23: wildcard *.tronyx.ru issued via LE staging.
        # · Root cause of prior failure: LE rate-limit (50 certs/domain/week), not DNS API.
        # · Prevention: DO NOT treat zone_manager_unavailable as DNS-01 failure.
        #   Test add/delete before concluding DNS API is broken.
        # · Rev: if add/delete also fail → DNS-01 truly broken, HTTP-01 fallback needed.
        # ══════════════════════════════════════════════════════════
        webnames_key = os.environ.get("WEBNAMES_API_KEY", "")
        if webnames_key and not webnames_key.startswith("*"):
            logger.warning(
                "[IMP:9][cert_orchestrator] WEBNAMES_API_KEY missing leading '*' — "
                "webnames.ru API may return zone_manager_unavailable for domains_list "
                "(listing only, add/delete TXT records still work). "
                "The key shown in webnames control panel includes the asterisk prefix."
            )

    except FileNotFoundError:
        logger.warning("[IMP:7][cert_orchestrator] Secrets file not found (non-fatal): %s", secrets_env_path)


# endregion FUNC_source_secrets_env


# endregion HELPERS
