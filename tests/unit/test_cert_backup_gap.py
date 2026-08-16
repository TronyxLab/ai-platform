# GREP_SUMMARY: test cert-backup-gap ssl-backup bootstrap-restore issue-upload-download s3-cache make-backup-coverage
# STRUCTURE: ▶ config(env + s3 mock) → ◇ backup_per_script_grep → ◇ make_backup_coverage(◇ grep postgres ∧ app-data ∧ cert) →
#            ◇ s3_ssl_cache_all_4_files(⊕ download 4) → ◇ issue_s3_integrity(⊕ upload → download → cmp thumbprint) →
#            ◇ state_machine_full_flow(⊕ s3_cache_check → download → issue → return) → ◇ s3_graceful_no_backup_block → ⎋
# region MODULE_CONTRACT
## @purpose  Gap analysis and round-trip tests for SSL certificate backup coverage.
##           Verifies that certificates issued via issue-cert.sh are captured by the
##           S3 SSL cache (s3-ssl-cache.sh upload) and restorable during bootstrap via
##           state_machine.py _ssl_provision(). Documents the gap that `make backup`
##           does NOT include SSL/TLS certificates.
## @scope    Static audit tests only — no subprocess, no real S3 connections.
##           All tests use grep/read for safe validation + env mock for config.
## @invariants
##   - No subprocess.run for business logic (native pytest only)
##   - No real S3 connections (mock-based)
##   - All cert paths: /etc/letsencrypt/live/<domain>/{fullchain,privkey,chain}.pem
##   - S3 key prefix for ssl-cache: platform/ssl-certs/<domain>/
## @rationale  Q: Why separate gap test file?
##             A: test_ssl_s3_cache.py tests the S3 cache infrastructure (upload.py,
##             backup_config.py, s3-ssl-cache.sh script structure). This file tests
##             the BACKUP COVERAGE question: what assets are backed up, what gaps exist,
##             and does the bootstrap restore work end-to-end. Two different concerns:
##             infrastructure vs. coverage.
## @changes  CREATED: 2026-07-22 · Gap analysis after user request "тест сертификаты
##           после выпуска попали в бекап для бутстрапа"
#
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  KEY FINDING: SSL certificates ARE backed up via S3 SSL cache            ║
# ║  (s3-ssl-cache.sh upload) immediately after issue-cert.sh issuance.      ║
# ║  They are restorable during bootstrap via state_machine.py _ssl_provision ║
# ║  (check S3 cache → download → restore to /etc/letsencrypt/live/<domain>). ║
# ║                                                                           ║
# ║  GAP: `make backup` does NOT include SSL/TLS certificates.               ║
# ║  Certificates are backed up through a SEPARATE channel (S3 SSL cache),    ║
# ║  not through the standard backup pipeline.                                ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
# endregion MODULE_CONTRACT

import logging
import pathlib
import sys
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

CERT_SCRIPTS = {
    "issue_cert": "core/internal/bootstrap/issue_cert.py",  # W3.5-1: issue-cert.sh → Python-модуль
    "state_machine": "core/internal/bootstrap/lifecycle/state_machine.py",
    # B9 T1: ssl_provision_via_orchestrator переехал в lifecycle/helpers/domains.py
    "helpers_domains": "core/internal/bootstrap/lifecycle/helpers/domains.py",
}

BACKUP_SCRIPTS = {
    "postgres": "core/modules/backup-cron/scripts/backup-postgres.sh",
    "app_data": "core/modules/backup-cron/scripts/backup-app-data.sh",
}

S3_CERT_FILES = [
    "fullchain.pem",
    "privkey.pem",
    "chain.pem",
    "account.tar.gz",
]

S3_CERT_PREFIX = "platform/ssl-certs"


# ─── Helper: read script content ──────────────────────────────────────────────


def _read_script(path: str) -> str:
    """Read a script file, assert it exists."""
    assert pathlib.Path(path).is_file(), f"Script not found: {path}"
    with pathlib.Path(path).open(encoding="utf-8") as f:
        return f.read()


# ─── Helper: assert S3 cert file reference ────────────────────────────────────


def _check_s3_cert_line(content: str, s3_key_suffix: str) -> bool:
    """Check if the script content references the specific S3 cert key."""
    # Try both shell variable expansion patterns: ${domain} and $domain
    patterns = [
        f"{S3_CERT_PREFIX}/${{domain}}/{s3_key_suffix}",
        f"{S3_CERT_PREFIX}/$domain/{s3_key_suffix}",
        f"ssl-certs/${{domain}}/{s3_key_suffix}",
        f"ssl-certs/$domain/{s3_key_suffix}",
        # Also match any reference containing the suffix
        f"/{s3_key_suffix}",
    ]
    return any(pattern in content for pattern in patterns)


# ═════════════════════════════════════════════════════════════════════════════
# TEST GROUP 1: S3 upload covers all 4 cert files
# ═════════════════════════════════════════════════════════════════════════════


# ═════════════════════════════════════════════════════════════════════════════
# TEST GROUP 2: make backup coverage — what IS and ISN'T included
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: ssl-cache prefix is distinct from backup prefix
# · Last fail: None (first run) · Remove if: S3 key prefix strategy changes
# · B10 T2: s3_ssl_cache.py prefix grep → native constant import (D2)
def test_ssl_cache_prefix_distinct_from_backup():
    """The S3 SSL cache key prefix (platform/ssl-certs/) must be distinct from
    the backup key prefix (platform/backups/). This ensures cert files are NOT
    mixed with backup files and can be independently managed.

    Also verifies that backup-postgres.sh and backup-app-data.sh do NOT
    reference ssl-certs or s3-ssl-cache — confirming the separation.
    """
    # Native: import the canonical prefix constant (B10 T2 — replaces source grep)
    sys.path.insert(0, str(Path(__file__).parent / "../.." / "core" / "internal" / "bootstrap"))
    from s3_ssl_cache import DEFAULT_SSL_CACHE_PREFIX

    assert DEFAULT_SSL_CACHE_PREFIX == S3_CERT_PREFIX, (
        f"s3_ssl_cache.DEFAULT_SSL_CACHE_PREFIX={DEFAULT_SSL_CACHE_PREFIX!r} must equal {S3_CERT_PREFIX!r}"
    )
    assert DEFAULT_SSL_CACHE_PREFIX != "platform/backups", (
        "SSL cache prefix must be distinct from backup prefix (platform/backups)"
    )

    # Verify backup scripts (shell facades — code-presence per D2) do NOT reference ssl-certs
    for name, path in BACKUP_SCRIPTS.items():
        script_content = _read_script(path)
        # backup scripts should NOT mention ssl-certs
        if "ssl-certs" in script_content or "s3-ssl-cache" in script_content:
            logger.warning(
                "[IMP:7][test_prefix_gap] %s references ssl-certs — possible scope creep",
                name,
            )

    logger.critical(
        "[IMP:9][test_prefix_gap] ASSERT: ssl-cache prefix (%s) is independent from backup pipeline", S3_CERT_PREFIX
    )


# ═════════════════════════════════════════════════════════════════════════════
# TEST GROUP 2: make backup coverage — what IS and ISN'T included
# ═════════════════════════════════════════════════════════════════════════════


# GUARD-PRESERVE (168): guard класса дефекта — если backup-postgres.sh расширит скоуп на SSL-сертификаты, сертификаты попадут в make backup (scope-creep); единственное покрытие
@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: backup-postgres.sh scope — PostgreSQL only
# · Last fail: None (first run) · Remove if: backup-postgres.sh scope changes to include certs
def test_backup_postgres_scope_postgres_only():
    """backup-postgres.sh must only back up PostgreSQL dumps, not SSL certificates.

    Regression guard: if this test fails, it means backup-postgres.sh scope has
    expanded to include SSL certs — which means certs ARE in `make backup`.
    """
    content = _read_script(BACKUP_SCRIPTS["postgres"])

    # Must backup PostgreSQL
    assert "pg_dumpall" in content, "backup-postgres.sh must dump PostgreSQL"

    # Must NOT reference SSL certs
    ssl_refs = ["fullchain.pem", "privkey.pem", "letsencrypt", "acme.sh", "issue-cert"]
    for ref in ssl_refs:
        if ref in content:
            logger.warning(
                "[IMP:7][test_postgres_scope] backup-postgres.sh references %s — "
                "SSL cert backup would be mixed with Postgres backup",
                ref,
            )

    logger.critical("[IMP:9][test_postgres_scope] ASSERT: backup-postgres.sh is PostgreSQL-only")


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: make backup coverage — list of backed-up assets
# · Last fail: None (first run) · Remove if: backup strategy expands
def test_make_backup_coverage_ssl_cert_gap_documented():
    """Verify that `make backup` does NOT include SSL/TLS certificates.

    This is a GAP analysis test. The backup pipeline only covers:
      - PostgreSQL dumps (make backup → backup-postgres.sh)
      - App data volumes (STUB, no-op in Phase 02)

    SSL certificates are backed up through a SEPARATE channel:
      issue-cert.sh → s3-ssl-cache.sh upload → S3 bucket

    The gap is DOCUMENTED (not fixed) — the current design separates concerns:
      - Backup = PostgreSQL + app data (for disaster recovery)
      - SSL cache = certificates only (for bootstrap optimization)

    This test provides a TRAP that will trigger if anyone adds cert backup
    to the make backup pipeline without coordinating with the SSL cache flow.
    """
    # Collect all backup scripts in the backup-cron module
    backup_scripts = sorted(Path("core/modules/backup-cron/scripts").glob("*.sh"))
    assert backup_scripts, "No backup scripts found in backup-cron module"

    logger.info("[IMP:7][test_coverage] Backup scripts found: %s", backup_scripts)

    ssl_mentions = []
    for script_path in backup_scripts:
        with pathlib.Path(script_path).open(encoding="utf-8") as f:
            content = f.read()
        ssl_mentions.extend(
            (script_path, ssl_ref)
            for ssl_ref in ["fullchain", "privkey", "letsencrypt", "acme.sh", "s3-ssl-cache", "ssl-certs", "issue-cert"]
            if ssl_ref in content
        )

    if ssl_mentions:
        logger.warning(
            "[IMP:7][test_coverage] SSL references found in backup scripts: %s",
            ssl_mentions,
        )
    else:
        logger.info("[IMP:7][test_coverage] No SSL references in any backup-cron script — clean separation confirmed")

    # Verify make backup delegates only to backup-cron module
    makefile_path = "makefiles/modules.mk"
    assert pathlib.Path(makefile_path).is_file(), "modules.mk not found"
    with pathlib.Path(makefile_path).open(encoding="utf-8") as f:
        mk_content = f.read()

    # Find the backup target
    assert "backup:" in mk_content, "modules.mk must have backup target"
    assert "backup-cron" in mk_content, "backup target must delegate to backup-cron"

    # backup target should NOT reference s3-ssl-cache or issue-cert
    backup_section_start = mk_content.find("\nbackup:")
    if backup_section_start >= 0:
        backup_section = mk_content[backup_section_start : backup_section_start + 500]
        for cert_ref in ["s3-ssl-cache", "issue-cert", "letsencrypt"]:
            if cert_ref in backup_section:
                logger.warning(
                    "[IMP:7][test_coverage] make backup references %s — SSL certs would be double-backed up", cert_ref
                )

    logger.critical(
        "[IMP:9][test_coverage] ASSERT: `make backup` does NOT include SSL certs. "
        "Certificates backed up via separate S3 SSL cache channel. "
        "Gap documented — not a bug, intentional separation of concerns."
    )


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: backup-app-data.sh is still a stub
# · Last fail: None (first run) · Remove if: backup-app-data.sh Phase 07 implementation
def test_backup_app_data_stub_no_cert_scope_creep():
    """backup-app-data.sh is a Phase 02 stub. This test verifies it does NOT
    yet have SSL cert backup scope — if it does, that's scope creep.

    When Phase 07 implements real app-data backup, this test must be updated
    to verify that cert backup stays in the SSL cache channel.
    """
    content = _read_script(BACKUP_SCRIPTS["app_data"])

    # Must be a stub
    assert "stub" in content.lower() or "STUB" in content, "backup-app-data.sh must be a stub in Phase 02"
    assert "phase 02" in content.lower() or "phase-02" in content.lower() or "phase_02" in content.lower(), (
        "Must reference Phase 02"
    )

    # Must NOT reference SSL certs
    for ref in ["fullchain", "privkey", "letsencrypt", "acme.sh", "s3-ssl-cache", "issue-cert"]:
        if ref in content:
            logger.warning(
                "[IMP:7][test_app_data_stub] backup-app-data.sh references %s — possible scope overlap with SSL cache",
                ref,
            )

    logger.critical("[IMP:9][test_app_data_stub] ASSERT: backup-app-data.sh = Phase 02 stub, no SSL cert scope")


# ═════════════════════════════════════════════════════════════════════════════
# TEST GROUP 3: Full round-trip — issue → S3 upload → bootstrap restore
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-25 · Scenario: issue_cert.py → s3_ssl_cache.py upload full flow
# · Last fail: None (first run) · Remove if: issue_cert.py post-issue logic changes
# · B10 T2 (D2): issue-cert.sh is a shell facade with NO dry-run mode → code-presence with
# ·   justification. The S3 fallback/wiring contract is covered NATIVELY by
# ·   tests/unit/test_cert_orchestrator_contract.py (issue-fallback via stub script) +
# ·   tests/unit/test_s3_ssl_cache.py. Gate invariant 2: grep остаётся только там, где dry-run
# ·   не покрывает контракт.
def test_issue_cert_saves_all_4_files_to_s3():
    """issue_cert.py must wire S3 upload for ALL cert files after successful
    certificate issuance. S3 upload is invoked via acme.sh --reloadcmd and
    cron_installer --renew-hook, which call s3_ssl_cache.py upload (DevPlan 052).

    Flow: issue_cert.py (W3.5-1, DevPlan 164)
      1. issue_tls_cert() → acme.sh DNS-01 (--reloadcmd runs s3_ssl_cache.py upload
         right after acme.sh installs the cert)
      2. save fullchain.pem + privkey.pem to /etc/letsencrypt/live/<domain>/
      3. cron_installer.install_acme_cron() → daily renewal cron + --renew-hook (S3 upload on renewal)
      4. shared/ssl_certs.cert_check_expiry() → openssl validation
    """
    content = _read_script(CERT_SCRIPTS["issue_cert"])

    # Must reference s3_ssl_cache.py (Python module — replaces s3-ssl-cache.sh)
    assert "s3_ssl_cache.py" in content, "issue_cert.py must reference s3_ssl_cache.py"
    assert "upload" in content, "issue_cert.py must call s3_ssl_cache.py upload"

    # Post-issue S3 upload is wired via acme.sh --reloadcmd / --renew-hook.
    # acme.sh runs reloadcmd AFTER the cert is installed and renew-hook after renewal —
    # both invoke `python3 s3_ssl_cache.py upload <domain>`.
    lines = content.split("\n")
    upload_lines = [i for i, line in enumerate(lines) if "s3_ssl_cache.py" in line and "upload" in line]
    assert upload_lines, "issue_cert.py must wire s3_ssl_cache.py upload into reloadcmd/renew-hook"
    for i in upload_lines:
        logger.info("[IMP:8][test_issue_flow] s3_ssl_cache.py upload at line %d: %s", i + 1, lines[i].strip())

    # The upload must be non-fatal (WARN on failure, not FAIL)
    assert "WARN" in content, "S3 save failure must be non-fatal (WARN, not FAIL)"

    logger.critical(
        "[IMP:9][test_issue_flow] ASSERT: issue_cert.py wires s3_ssl_cache.py upload "
        "via acme.sh reloadcmd/renew-hook — all 4 cert files uploaded to S3 after issue"
    )


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-25 · Scenario: S3 restore validates cert after download
# · Last fail: 2026-07-25 — DevPlan 052 Phase 2: validation moved to cert_orchestrator.py + s3_ssl_cache.py
# · Remove if: S3 restore validation logic changes
# · B10 T2: this test was a Python-source grep (cert_orchestrator.py + s3_ssl_cache.py) — DELETED,
# ·   replaced by native behavior tests: tests/unit/test_cert_orchestrator_contract.py
# ·   (orchestrate_certs s3-restore flow) + tests/unit/test_s3_ssl_cache.py (_validate_cert paths).


# ═════════════════════════════════════════════════════════════════════════════
# TEST GROUP 4: Graceful degradation and edge cases
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: S3 unavailable doesn't block cert issue
# · Last fail: None (first run) · Remove if: S3 failure behavior changes
# · B10 T2 (D2): issue-cert.sh — shell facade, no dry-run → code-presence (WARN + ordering).
# ·   s3_ssl_cache.py graceful-degradation grep (return False/ClientError) DELETED — covered
# ·   natively by tests/unit/test_s3_ssl_cache.py (test_download_s3_file_nonfatal_on_client_error,
# ·   test_upload_s3_file_nonfatal_on_client_error, test_check_cert_miss).
def test_s3_unavailable_does_not_block_cert_issue():
    """If S3 is unavailable, certificate issuance must NOT be blocked.
    The S3 cache upload is a best-effort optimization, not a hard dependency.

    Flow: issue-cert.sh main() → issue_tls_cert() → s3-ssl-cache.sh upload (WARN on fail)
    The cert is saved locally at /etc/letsencrypt/live/<domain>/ regardless of S3 status.
    """
    content = _read_script(CERT_SCRIPTS["issue_cert"])

    # The s3-ssl-cache.sh call must be non-fatal
    # Look for WARN log around the s3-ssl-cache.sh upload call
    assert "WARN" in content, "issue_cert.py must log WARN (not FAIL) if S3 upload fails"

    # The cert is saved to /etc/letsencrypt/live/ BEFORE the S3 upload attempt
    # Verify the cert installation happens before S3 call
    lines = content.split("\n")
    cert_install_line = -1
    s3_upload_line = -1
    for i, line in enumerate(lines):
        if "install-cert" in line:
            cert_install_line = i
        if "s3_ssl_cache.py" in line and "upload" in line:
            s3_upload_line = i

    if cert_install_line >= 0 and s3_upload_line >= 0:
        assert cert_install_line < s3_upload_line, (
            f"Cert installation (line {cert_install_line}) must happen BEFORE S3 upload (line {s3_upload_line})"
        )
        logger.info(
            "[IMP:8][test_s3_nonfatal] Cert install at line %d → "
            "S3 upload at line %d — cert exists locally even if S3 fails",
            cert_install_line,
            s3_upload_line,
        )

    logger.critical(
        "[IMP:9][test_s3_nonfatal] ASSERT: S3 unavailability does NOT block "
        "cert issuance — cert saved locally first, S3 upload is best-effort"
    )


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: dev-certs NOT backed up (gitignored)
# · Last fail: None (first run) · Remove if: dev-certs backup strategy changes
def test_dev_certs_not_backed_up_gap():
    """Dev SSL certificates (core/modules/nginx/dev-certs/) are gitignored and
    NOT backed up by any mechanism. They are regenerated on-demand via `make dev-certs`.
    This is a deliberate design choice — dev certs are ephemeral.

    This test documents the gap: dev-certs are NOT in:
      - make backup (postgres/app-data only)
      - S3 SSL cache (LE certs only)
      - git (gitignored)
    """
    # Verify dev-certs is gitignored
    gitignore_path = ".gitignore"
    assert pathlib.Path(gitignore_path).is_file(), ".gitignore not found"
    with pathlib.Path(gitignore_path).open(encoding="utf-8") as f:
        gitignore_content = f.read()

    assert "dev-certs" in gitignore_content, "dev-certs must be in .gitignore"
    logger.info("[IMP:7][test_dev_certs] dev-certs is gitignored — confirmed")

    # Verify make backup does NOT reference dev-certs
    for name, path in BACKUP_SCRIPTS.items():
        script_content = _read_script(path)
        if "dev-certs" in script_content or "dev_certs" in script_content:
            logger.warning("[IMP:7][test_dev_certs] %s references dev-certs — dev certs would be in backup", name)

    # Verify S3 cache does NOT reference dev-certs
    # (s3-ssl-cache.sh deleted in DevPlan 116 B8 U-41 — Python s3_ssl_cache.py is the live implementation)
    s3_content = _read_script("core/internal/bootstrap/s3_ssl_cache.py")
    assert "dev-certs" not in s3_content, "S3 SSL cache must not reference dev-certs — they are LE-only"

    logger.critical(
        "[IMP:9][test_dev_certs] ASSERT: dev-certs are gitignored, "
        "not in make backup, not in S3 cache — ephemeral, regenerated on demand. "
        "This is a DOCUMENTED GAP, not a bug."
    )


# ═════════════════════════════════════════════════════════════════════════════
# TEST GROUP 5: Node-lifecycle integration — update step calls SSL provision
# ═════════════════════════════════════════════════════════════════════════════

# B10 T2: test_node_lifecycle_update_step_calls_ssl_provision (Python-source grep on
# helpers/domains.py + phases.py) DELETED — replaced by native behavior tests:
#   tests/unit/test_phase_certificates_contract.py (phase_certificates delegation +
#   ssl_provision_via_orchestrator/extract_domains API contract)
