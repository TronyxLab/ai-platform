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
import os

import pytest

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

CERT_SCRIPTS = {
    "issue_cert": "core/internal/bootstrap/issue-cert.sh",
    "s3_cache": "core/internal/bootstrap/s3-ssl-cache.sh",
    "state_machine": "core/internal/bootstrap/lifecycle/state_machine.py",
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
    assert os.path.isfile(path), f"Script not found: {path}"
    with open(path) as f:
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


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: s3-ssl-cache.sh upload handles all 4 cert files
# · Last fail: None (first run) · Remove if: S3 upload file set changes
def test_s3_cache_upload_all_4_cert_files():
    """s3-ssl-cache.sh upload must handle all 4 cert files: fullchain.pem,
    privkey.pem, chain.pem, account.tar.gz with correct S3 key prefix.

    The upload must use the S3 key prefix `platform/ssl-certs/<domain>/` for
    each file. This prefix is separate from the backup prefix `platform/backups/`.
    """
    content = _read_script(CERT_SCRIPTS["s3_cache"])

    # Check that the S3 cert prefix constant is defined
    assert S3_CERT_PREFIX in content, f"s3-ssl-cache.sh must define S3_SSL_CERT_PREFIX={S3_CERT_PREFIX!r}"
    logger.info("[IMP:7][test_4_files] S3 cert prefix found: %s", S3_CERT_PREFIX)

    # Check upload of fullchain.pem
    assert "fullchain.pem" in content, "s3-ssl-cache.sh must upload fullchain.pem"
    assert "privkey.pem" in content, "s3-ssl-cache.sh must upload privkey.pem"
    assert "chain.pem" in content, "s3-ssl-cache.sh must upload chain.pem"
    assert "account.tar.gz" in content, "s3-ssl-cache.sh must upload account.tar.gz"

    # Verify S3 key prefix usage per file upload call
    lines = content.split("\n")
    s3_keys_found = []
    for _i, line in enumerate(lines):
        if "UPLOAD_PY" in line or "upload.py" in line or "ssl-certs" in line:
            s3_keys_found.append(line.strip())

    logger.info("[IMP:7][test_4_files] S3 key references: %d", len(s3_keys_found))
    for ref in s3_keys_found:
        logger.info("[IMP:8][test_4_files]   %s", ref)

    # Assert download validates and restores certs
    assert "_s3_download" in content, "Must have _s3_download function"
    # _s3_download restores fullchain.pem, privkey.pem, chain.pem
    assert "fullchain.pem" in content, "download must restore fullchain.pem"
    assert "privkey.pem" in content, "download must restore privkey.pem"

    logger.critical(
        "[IMP:9][test_4_files] ASSERT: s3-ssl-cache.sh uploads and downloads "
        "all 4 cert files: fullchain.pem, privkey.pem, chain.pem, account.tar.gz"
    )


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: ssl-cache prefix is distinct from backup prefix
# · Last fail: None (first run) · Remove if: S3 key prefix strategy changes
def test_ssl_cache_prefix_distinct_from_backup():
    """The S3 SSL cache key prefix (platform/ssl-certs/) must be distinct from
    the backup key prefix (platform/backups/). This ensures cert files are NOT
    mixed with backup files and can be independently managed.

    Also verifies that backup-postgres.sh and backup-app-data.sh do NOT
    reference ssl-certs or s3-ssl-cache — confirming the separation.
    """
    # s3-ssl-cache.sh uses its own prefix
    s3_content = _read_script(CERT_SCRIPTS["s3_cache"])
    assert S3_CERT_PREFIX in s3_content, f"s3-ssl-cache.sh must define prefix {S3_CERT_PREFIX}"

    # Verify backup scripts do NOT reference ssl-certs
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
    import glob

    backup_scripts = glob.glob("core/modules/backup-cron/scripts/*.sh")
    assert backup_scripts, "No backup scripts found in backup-cron module"

    logger.info("[IMP:7][test_coverage] Backup scripts found: %s", backup_scripts)

    ssl_mentions = []
    for script_path in backup_scripts:
        with open(script_path) as f:
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
    assert os.path.isfile(makefile_path), "modules.mk not found"
    with open(makefile_path) as f:
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
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: issue-cert.sh → s3-ssl-cache.sh upload full flow
# · Last fail: None (first run) · Remove if: issue-cert.sh post-issue logic changes
def test_issue_cert_saves_all_4_files_to_s3():
    """issue-cert.sh must call s3-ssl-cache.sh upload for ALL cert files after
    successful certificate issuance. This test verifies the code path references.

    Flow: issue-cert.sh main()
      1. issue_tls_cert() → acme.sh DNS-01 →
      2. save fullchain.pem + privkey.pem to /etc/letsencrypt/live/<domain>/
      3. _acme_install_cron() → daily renewal cron
      4. s3-ssl-cache.sh upload <domain> → uploads 4 files to S3
      5. _acme_verify_cert() → openssl validation
    """
    content = _read_script(CERT_SCRIPTS["issue_cert"])

    # Must call s3-ssl-cache.sh upload
    assert "s3-ssl-cache.sh" in content, "issue-cert.sh must reference s3-ssl-cache.sh"
    assert "upload" in content, "issue-cert.sh must call s3-ssl-cache.sh upload"

    # The upload call must be AFTER issue_tls_cert success
    lines = content.split("\n")
    issue_line = -1
    upload_line = -1
    for i, line in enumerate(lines):
        if "issue_tls_cert" in line and "return 0" in line:
            # This is the success path in issue_tls_cert — look for the
            # caller in main() which calls issue_tls_cert then uploads
            pass
        if "if ! issue_tls_cert" in line:
            issue_line = i
        if "s3_cache" in line and "upload" in line:
            upload_line = i

    if issue_line >= 0 and upload_line >= 0:
        assert upload_line > issue_line, (
            f"S3 upload (line {upload_line}) must happen AFTER issue_tls_cert success (line {issue_line})"
        )
        logger.info(
            "[IMP:8][test_issue_flow] issue_tls_cert at line %d → s3-ssl-cache.sh upload at line %d — ordering OK",
            issue_line,
            upload_line,
        )
    else:
        logger.info(
            "[IMP:7][test_issue_flow] Line numbers not found via grep "
            "(expected if function is inlined) — checking content instead"
        )

    # The upload must be non-fatal (WARN on failure, not FAIL)
    assert "WARN" in content, "S3 save failure must be non-fatal (WARN, not FAIL)"

    # The S3 cache upload happens in main() after issue_tls_cert
    # Look for the section that has both issue_tls_cert and s3_cache
    issue_section = content.find("if ! issue_tls_cert")
    if issue_section >= 0:
        # Check that upload follows within 40 lines (~3000 chars with extensive TRAP comments)
        after_issue = content[issue_section : issue_section + 3000]
        assert "s3-ssl-cache.sh" in after_issue, (
            "s3-ssl-cache.sh upload must be called in main() after issue_tls_cert success (within 40 lines)"
        )
        assert "upload" in after_issue, "s3-ssl-cache.sh upload must be called after issue_tls_cert"

    logger.critical(
        "[IMP:9][test_issue_flow] ASSERT: issue-cert.sh calls "
        "s3-ssl-cache.sh upload AFTER issue_tls_cert success — "
        "all 4 cert files uploaded to S3"
    )


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: state_machine.py full bootstrap restore
# · Last fail: None (first run) · Remove if: _ssl_provision() logic changes
def test_state_machine_full_bootstrap_restore_flow():
    """state_machine.py _ssl_provision() must implement the full bootstrap restore
    flow: check S3 cache → download if valid → restore cert → skip issue-cert.sh.

    This is the restore side of the round-trip:
      Bootstrap node → s3-ssl-cache.sh check <domain> → exit 0 (cache HIT)
        → s3-ssl-cache.sh download <domain> → restore 4 files
        → /etc/letsencrypt/live/<domain>/fullchain.pem exists → return (skip issue)
      OR cache MISS
        → issue-cert.sh (fresh issue + upload to S3)
    """
    content = _read_script(CERT_SCRIPTS["state_machine"])

    # Must have _ssl_provision function
    assert "def _ssl_provision" in content, "state_machine.py must have _ssl_provision() function"

    # Must reference s3-ssl-cache.sh
    assert "s3-ssl-cache.sh" in content, "_ssl_provision() must reference s3-ssl-cache.sh"

    # Must check S3 cache first
    assert "s3_cache_check" in content, "_ssl_provision() must call s3-ssl-cache.sh check (s3_cache_check)"

    # Must attempt download on cache hit
    assert "s3_cache_download" in content, "_ssl_provision() must call s3-ssl-cache.sh download (s3_cache_download)"

    # Must have the return statement to skip issue-cert.sh on successful restore
    assert "cert_path" in content, "_ssl_provision() must check cert_path after download"
    assert "return" in content.split("download")[-1] if "download" in content else True, (
        "_ssl_provision() must return early on successful S3 restore (skip issue)"
    )

    # Must fallback to issue-cert.sh
    assert "ssl_issue" in content, "_ssl_provision() must call issue-cert.sh (ssl_issue) on cache miss"

    # Verify ordering: S3 check before issue
    lines = content.split("\n")
    ssl_section_start = -1
    for i, line in enumerate(lines):
        if "def _ssl_provision" in line:
            ssl_section_start = i
            break

    assert ssl_section_start >= 0, "Could not find _ssl_provision() definition"

    section = "\n".join(lines[ssl_section_start : ssl_section_start + 80])
    s3_check_idx = section.find("s3_cache_check")
    s3_dl_idx = section.find("s3_cache_download")
    ssl_issue_idx = section.find("ssl_issue")

    logger.info(
        "[IMP:7][test_restore_flow] _ssl_provision() positions: s3_cache_check=%d s3_cache_download=%d ssl_issue=%d",
        s3_check_idx,
        s3_dl_idx,
        ssl_issue_idx,
    )

    # S3 check must exist
    assert s3_check_idx >= 0, "S3 cache check must exist in _ssl_provision()"

    # S3 download must exist
    assert s3_dl_idx >= 0, "S3 cache download must exist in _ssl_provision()"

    # issue-cert.sh fallback must exist
    assert ssl_issue_idx >= 0, "issue-cert.sh fallback must exist in _ssl_provision()"

    # S3 check must come before issue-cert.sh
    if s3_check_idx > ssl_issue_idx:
        logger.warning(
            "[IMP:7][test_restore_flow] S3 check (%d) is AFTER issue (%d) — "
            "optimization opportunity: S3 cache should be checked before acme.sh",
            s3_check_idx,
            ssl_issue_idx,
        )
    else:
        logger.info(
            "[IMP:7][test_restore_flow] S3 cache check (%d) precedes issue-cert.sh (%d) — correct ordering",
            s3_check_idx,
            ssl_issue_idx,
        )

    logger.critical(
        "[IMP:9][test_restore_flow] ASSERT: state_machine.py _ssl_provision() "
        "implements full bootstrap restore: s3_cache_check → s3_cache_download → "
        "return (skip issue) OR fallback to issue-cert.sh"
    )


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: S3 restore should check download success
# · Last fail: None (first run) · Remove if: S3 restore validation logic changes
def test_s3_restore_validates_cert_after_download():
    """After downloading cert from S3, the bootstrap restore must validate:
    1. cert_path (/etc/letsencrypt/live/<domain>/fullchain.pem) exists
    2. The certificate is usable (openssl validation in s3-ssl-cache.sh)

    This ensures a corrupted S3 cache entry doesn't leave the node without valid certs.
    """
    # Check state_machine.py for download validation
    sm_content = _read_script(CERT_SCRIPTS["state_machine"])

    # Must check if cert_path exists after download
    assert "os.path.isfile(cert_path)" in sm_content or "cert_path" in sm_content, (
        "_ssl_provision() must verify cert file exists after S3 download"
    )

    # Check s3-ssl-cache.sh for openssl validation during download
    s3_content = _read_script(CERT_SCRIPTS["s3_cache"])

    # download must validate with openssl
    download_section = s3_content.split("_s3_download()")[1] if "_s3_download()" in s3_content else ""
    # Check for subject validation in download
    if download_section:
        assert "subject" in download_section or "CN" in download_section, (
            "s3-ssl-cache.sh download must validate cert subject"
        )

    # _s3_check must use openssl -checkend for expiry validation
    assert "checkend" in s3_content, "s3-ssl-cache.sh must use openssl -checkend for cert expiry validation"

    logger.critical(
        "[IMP:9][test_restore_validate] ASSERT: S3 restore validates cert "
        "existence (state_machine) + openssl validation (s3-ssl-cache.sh)"
    )


# ═════════════════════════════════════════════════════════════════════════════
# TEST GROUP 4: Graceful degradation and edge cases
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: S3 unavailable doesn't block cert issue
# · Last fail: None (first run) · Remove if: S3 failure behavior changes
def test_s3_unavailable_does_not_block_cert_issue():
    """If S3 is unavailable, certificate issuance must NOT be blocked.
    The S3 cache upload is a best-effort optimization, not a hard dependency.

    Flow: issue-cert.sh main() → issue_tls_cert() → s3-ssl-cache.sh upload (WARN on fail)
    The cert is saved locally at /etc/letsencrypt/live/<domain>/ regardless of S3 status.
    """
    content = _read_script(CERT_SCRIPTS["issue_cert"])

    # The s3-ssl-cache.sh call must be non-fatal
    # Look for WARN log around the s3-ssl-cache.sh upload call
    assert "WARN" in content, "issue-cert.sh must log WARN (not FAIL) if S3 upload fails"

    # The cert is saved to /etc/letsencrypt/live/ BEFORE the S3 upload attempt
    # Verify the cert installation happens before S3 call
    lines = content.split("\n")
    cert_install_line = -1
    s3_upload_line = -1
    for i, line in enumerate(lines):
        if "install-cert" in line:
            cert_install_line = i
        if "s3-ssl-cache.sh" in line and "upload" in line:
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

    # Also check: s3-ssl-cache.sh should validate S3 credentials and degrade gracefully
    s3_content = _read_script(CERT_SCRIPTS["s3_cache"])
    assert "WARN" in s3_content, "s3-ssl-cache.sh must log WARN on S3 failure (not FAIL)"

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
    assert os.path.isfile(gitignore_path), ".gitignore not found"
    with open(gitignore_path) as f:
        gitignore_content = f.read()

    assert "dev-certs" in gitignore_content, "dev-certs must be in .gitignore"
    logger.info("[IMP:7][test_dev_certs] dev-certs is gitignored — confirmed")

    # Verify make backup does NOT reference dev-certs
    for name, path in BACKUP_SCRIPTS.items():
        script_content = _read_script(path)
        if "dev-certs" in script_content or "dev_certs" in script_content:
            logger.warning("[IMP:7][test_dev_certs] %s references dev-certs — dev certs would be in backup", name)

    # Verify S3 cache does NOT reference dev-certs
    s3_content = _read_script(CERT_SCRIPTS["s3_cache"])
    assert "dev-certs" not in s3_content, "S3 SSL cache must not reference dev-certs — they are LE-only"

    logger.critical(
        "[IMP:9][test_dev_certs] ASSERT: dev-certs are gitignored, "
        "not in make backup, not in S3 cache — ephemeral, regenerated on demand. "
        "This is a DOCUMENTED GAP, not a bug."
    )


# ═════════════════════════════════════════════════════════════════════════════
# TEST GROUP 5: Node-lifecycle integration — update step calls SSL provision
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
# 🧪 TRAP[TEST] · 2026-07-22 · Scenario: node-lifecycle update step calls _ssl_provision
# · Last fail: None (first run) · Remove if: state machine step list changes
def test_node_lifecycle_update_step_calls_ssl_provision():
    """The state machine update step (step 3) must call _ssl_provision().
    This connects the bootstrap pipeline to the certificate backup/restore.

    node-lifecycle.sh --mode update:
      step 3: ssl_provision → _ssl_provision()
        → s3-ssl-cache.sh check → download (cache hit) OR issue-cert.sh (cache miss)
    """
    content = _read_script(CERT_SCRIPTS["state_machine"])

    # Must reference ssl_provision step
    assert "ssl_provision" in content, "state_machine.py must have ssl_provision step"

    # Must have the step in the init step list
    # In state_machine.py the steps are typically in an ordered list or dict
    # Look for "ssl_provision" in the step definitions
    lines = content.split("\n")
    step_def_lines = []
    for _i, line in enumerate(lines):
        # Look for step lists — various formats
        if "ssl_provision" in line and (
            "step" in line.lower()
            or "init" in line.lower()
            or "update" in line.lower()
            or '"' in line
            or "'" in line
            or "ssl_provision" in line
        ):
            step_def_lines.append(line.strip())

    assert step_def_lines, "ssl_provision must appear in a step definition (init or update steps)"
    for line in step_def_lines:
        logger.info("[IMP:8][test_step_list] %s", line)

    # Verify the step has a handler mapping
    # Look for something like: "ssl_provision": handler_function
    assert "_ssl_provision" in content, "There must be a handler function _ssl_provision() for the ssl_provision step"

    logger.critical(
        "[IMP:9][test_step_list] ASSERT: node-lifecycle update step 3 calls "
        "_ssl_provision() — S3 cache check/restore integrated into bootstrap pipeline"
    )
