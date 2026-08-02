"""
# GREP_SUMMARY: test cert-orchestrator contract orchestrate_certs restore-first s3-restore issue-fallback disk-synced non-fatal monkeypatch B10-T2
# STRUCTURE: ▶ import cert_orchestrator (native) → ◇ S3_BUCKET env → ○ scenarios (parametrize):
#            ┌disk-valid→skip+upload┐ ┌s3-hit→restored┐ ┌s3-miss→issue-fallback┐ ┌s3-fail→non-fatal┐ ┌no-domains→noop┐ → ⊕ assert DomainCertResult fields → ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Native behavioral contract tests for core/internal/bootstrap/cert_orchestrator.py —
##           replaces deleted grep-asserts in tests/test_cert_backup_gap.py (386-396, 566-582) and
##           tests/test_ssl_s3_cache.py (cert_orchestrator block). DevPlan 116 B10 T2 (D2: Python → native).
## @scope    Tests orchestrate_certs() restore-first strategy: disk-valid skip, S3 restore, issue-cert
##           fallback (stub script in tmp_path), S3 failure non-fatal. All I/O monkeypatched at the
##           s3_ssl_cache/issue-script boundary — real orchestration logic, 0 call_args_list.
## @invariants
##   - s3_ssl_cache functions monkeypatched (check/download) — no real S3
##   - issue-cert fallback via stub bash script in tmp_path (exit 0) — no real acme.sh
##   - CERT_VALIDITY_PATH monkeypatched to tmp_path (real path /etc/letsencrypt/live is root-only)
##   - Assertions on observable result: DomainCertResult.status/source + CertResult summary counts
##   - Each test emits IMP:9 logs (LDD)
## @rationale  Grep-asserts froze cert_orchestrator.py internals; native tests verify BEHAVIOR:
##             restore-first ordering, upload-on-skip, graceful S3 degradation (non-fatal).
## @changes  2026-08-01 · Created (DevPlan 116 B10 T2)
# endregion MODULE_CONTRACT
"""

import sys
from pathlib import Path

import pytest

# ── Import the module under test (core/internal/bootstrap on sys.path) ──
_BOOTSTRAP_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_BOOTSTRAP_DIR))
import cert_orchestrator as co

logger = pytest.importorskip("logging").getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# region Fixtures
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def cert_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fixture: S3_BUCKET env + CERT_VALIDITY_PATH redirected to tmp (root-free cert paths)."""
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    cert_dir = tmp_path / "le"
    cert_dir.mkdir(parents=True)
    monkeypatch.setattr(co, "CERT_VALIDITY_PATH", str(cert_dir))
    return cert_dir


@pytest.fixture
def issue_script(tmp_path: Path) -> Path:
    """Stub issue-cert.sh — exits 0 (successful issuance), never calls acme.sh."""
    script = tmp_path / "issue-cert.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)
    return script


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Scenario 1: valid cert on disk → SKIP + upload to S3 (restore-first)
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · disk-synced · valid cert on disk skips issue and uploads to S3
# · Regression: DevPlan 052 §4.5 — cert on disk must upload to S3 (upload-on-skip)
# · Last fail: N/A (native replacement for test_node_lifecycle_checks_s3_before_issue grep)
# · Remove if: orchestrate_certs restore-first flow changes
def test_orchestrate_certs_skips_valid_disk_cert(cert_env, monkeypatch, caplog) -> None:
    """Valid cert on disk → status='skipped', source='disk_synced', S3 upload invoked."""
    caplog.set_level(0)
    domain = "example.com"
    (cert_env / domain).mkdir(parents=True)
    (cert_env / domain / "fullchain.pem").write_text("cert")

    monkeypatch.setattr(co, "cert_is_valid", lambda *a, **kw: True)
    uploaded: list[str] = []
    monkeypatch.setattr(co, "_upload_to_s3", lambda d: (uploaded.append(d), True)[1])

    result = co.orchestrate_certs([domain], "/tmp/nonexistent-issue.sh")

    entry = result.domains[domain]
    assert entry.status == "skipped", f"Expected skipped, got {entry.status}"
    assert entry.source == "disk_synced", f"Expected disk_synced, got {entry.source}"
    assert uploaded == [domain], "upload-on-skip must sync the disk cert to S3"
    assert result.skipped == 1
    logger.critical("[IMP:9][test] disk-valid → skipped(disk_synced) + upload-on-skip — restore-first confirmed")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Scenario 2: S3 cache hit → download + restore
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · s3-restore · S3 cache hit restores cert without issuing
# · Regression: DevPlan 052 — S3 restore must precede issue-cert.sh (restore-first)
# · Last fail: N/A (native replacement for test_s3_restore_validates_cert_after_download grep)
# · Remove if: _try_s3_restore flow changes
def test_orchestrate_certs_restores_from_s3(cert_env, monkeypatch, caplog) -> None:
    """No disk cert + S3 cache hit → status='restored', source='s3', no issue fallback."""
    caplog.set_level(0)
    domain = "example.com"
    (cert_env / domain).mkdir(parents=True)
    (cert_env / domain / "fullchain.pem").write_text("cert")

    monkeypatch.setattr(co, "cert_is_valid", lambda *a, **kw: False)
    monkeypatch.setattr(co.s3_ssl_cache, "check_cert", lambda d, s3_bucket: True)
    monkeypatch.setattr(co.s3_ssl_cache, "download_cert", lambda *a, **k: True)

    result = co.orchestrate_certs([domain], "/tmp/nonexistent-issue.sh")

    entry = result.domains[domain]
    assert entry.status == "restored", f"Expected restored, got {entry.status}"
    assert entry.source == "s3", f"Expected source s3, got {entry.source}"
    assert result.restored == 1
    logger.critical("[IMP:9][test] S3 cache hit → restored(source=s3) — restore-first confirmed")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Scenario 3: S3 miss → issue-cert.sh fallback (stub script)
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · issue-fallback · S3 miss falls back to issue-cert.sh
# · Regression: DevPlan 052 — cache miss must fall back to acme.sh issue, then upload to S3
# · Last fail: N/A (native replacement for issue-cert.sh WARN/upload grep context)
# · Remove if: issue fallback flow changes
def test_orchestrate_certs_issue_fallback_on_s3_miss(cert_env, monkeypatch, issue_script, caplog) -> None:
    """S3 cache miss → issue-cert.sh fallback (stub exit 0) → status='issued', source='acme', upload."""
    caplog.set_level(0)
    domain = "example.com"

    monkeypatch.setattr(co, "cert_is_valid", lambda *a, **kw: False)
    monkeypatch.setattr(co.s3_ssl_cache, "check_cert", lambda d, s3_bucket: False)
    uploaded: list[str] = []
    monkeypatch.setattr(co, "_upload_to_s3", lambda d: (uploaded.append(d), True)[1])

    result = co.orchestrate_certs([domain], str(issue_script))

    entry = result.domains[domain]
    assert entry.status == "issued", f"Expected issued, got {entry.status}"
    assert entry.source == "acme", f"Expected source acme, got {entry.source}"
    assert uploaded == [domain], "post-issue S3 upload must run (issue-cert.sh wiring contract)"
    assert result.issued == 1
    logger.critical("[IMP:9][test] S3 miss → issue-cert.sh fallback → issued(acme) + upload")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Scenario 4: S3 failure → non-fatal (WARN, never raises)
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · non-fatal · S3 failure must not raise (graceful degradation)
# · Regression: 052 — S3 unavailability must NOT block cert orchestration
# · Last fail: N/A (native replacement for test_s3_unavailable_does_not_block_cert_issue s3 grep)
# · Remove if: graceful degradation behavior changes
def test_orchestrate_certs_s3_failure_non_fatal(cert_env, monkeypatch, caplog) -> None:
    """S3 check raises OSError → orchestrate_certs does NOT raise; domain ends 'failed' (last resort)."""
    caplog.set_level(0)
    domain = "example.com"

    monkeypatch.setattr(co, "cert_is_valid", lambda *a, **kw: False)

    def _raise_check(d, s3_bucket):
        raise OSError("S3 unavailable")

    monkeypatch.setattr(co.s3_ssl_cache, "check_cert", _raise_check)
    monkeypatch.setattr(
        co,
        "_generate_self_signed",
        lambda d: co.DomainCertResult(domain=d, status="failed", source="none", error="test"),
    )

    # Must NOT raise — graceful degradation (WARN, not FAIL)
    result = co.orchestrate_certs([domain], "/tmp/nonexistent-issue.sh")

    assert result.domains[domain].status == "failed", "S3 failure must degrade to failed, not crash"
    assert result.failed == 1
    logger.critical("[IMP:9][test] S3 failure non-fatal — orchestrate_certs returned without raising")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Scenario 5: no domains → no-op
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · noop · empty domain list returns empty CertResult
# · Regression: orchestrate_certs([]) must not crash
# · Last fail: N/A (native contract)
# · Remove if: no-domains handling changes
def test_orchestrate_certs_no_domains_noop(cert_env, monkeypatch, caplog) -> None:
    """Empty domain list → empty CertResult, no subprocess, no crash."""
    caplog.set_level(0)
    monkeypatch.delenv("PLATFORM_DOMAIN", raising=False)

    result = co.orchestrate_certs([], "/tmp/nonexistent-issue.sh")

    assert result.domains == {}
    assert result.issued == 0 and result.restored == 0 and result.skipped == 0 and result.failed == 0
    logger.critical("[IMP:9][test] orchestrate_certs([]) → empty CertResult, no-op")


# endregion
