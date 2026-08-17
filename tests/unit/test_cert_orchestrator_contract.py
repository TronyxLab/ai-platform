"""
# GREP_SUMMARY: test cert-orchestrator contract orchestrate_certs restore-first s3-restore issue-fallback disk-synced non-fatal monkeypatch B10-T2 DI san-only
# STRUCTURE: ▶ import cert_orchestrator (native) → ◇ S3_BUCKET env → ○ scenarios (parametrize):
#            ┌disk-valid→skip+upload┐ ┌s3-hit→restored┐ ┌s3-miss→issue-fallback┐ ┌s3-fail→non-fatal┐ ┌no-domains→noop┐
#            ┌s3-hit+SAN-only real cert→restored┐ → ⊕ assert DomainCertResult fields → ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Native behavioral contract tests for core/internal/bootstrap/cert_orchestrator.py —
##           replaces deleted grep-asserts in tests/test_cert_backup_gap.py (386-396, 566-582) and
##           tests/test_ssl_s3_cache.py (cert_orchestrator block). DevPlan 116 B10 T2 (D2: Python → native).
## @scope    Tests orchestrate_certs() restore-first strategy: disk-valid skip, S3 restore, issue-cert
##           fallback (stub script in tmp_path), S3 failure non-fatal. ВСЯ инъекция через DI-параметры
##           orchestrate_certs (validity_path/cert_validity_fn/s3_cache/runner/environ) — 0 monkeypatch.
##           Scenario 6 (DevPlan 004 W2 AC4): S3 restore с РЕАЛЬНЫМ SAN-only LE-подобным fullchain
##           (openssl-генерация в tmp_path; monkeypatch ТОЛЬКО cert_is_le_issuer — issuer self-generated).
## @invariants
##   - s3_ssl_cache functions НЕ патчатся — fake s3_cache объект (check_cert/download_cert/upload_cert)
##   - issue-cert fallback via stub bash script in tmp_path (exit 0) — no real acme.sh
##   - CERT_VALIDITY_PATH не патчится — validity_path= параметр (real path /etc/letsencrypt/live root-only)
##   - Assertions on observable result: DomainCertResult.status/source + CertResult summary counts
##   - Each test emits IMP:9 logs (LDD)
## @rationale  Grep-asserts froze cert_orchestrator.py internals; native tests verify BEHAVIOR:
##             restore-first ordering, upload-on-skip, graceful S3 degradation (non-fatal).
##             W3.5-4 (164 S8): тесты переведены на существующие DI-швы orchestrate_certs
##             (добавлены E1/160) — setattr 12 → 0, DI-HYG 0 отклонений.
##             DevPlan 004 W2: SAN-only серт из S3-кеша больше НЕ отвергается (SAN-aware W1) —
##             сценарий 6 фиксирует исходную форму бага (false-miss → re-issue → LE rate-limit).
## @changes  2026-08-01 · Created (DevPlan 116 B10 T2)
##           2026-08-14 · W3.5-4 (164 S8) — DI-конверсия (setattr 12 → 0)
##           2026-08-16 · DevPlan 004 W2 — +Scenario 6: S3 restore реального SAN-only fullchain (AC4)
# endregion MODULE_CONTRACT
"""

import subprocess
import sys
from pathlib import Path

import pytest

# ── Import the module under test (core/internal/bootstrap on sys.path) ──
_BOOTSTRAP_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_BOOTSTRAP_DIR))
import cert_orchestrator as co

# ssl_certs для monkeypatch cert_is_le_issuer (Scenario 6: issuer self-generated)
import core.internal.shared.ssl_certs as ssl_certs_module

pytestmark = pytest.mark.static_audit

logger = pytest.importorskip("logging").getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# region Fixtures / fakes
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def cert_env(tmp_path: Path) -> Path:
    """Fixture: tmp-каталог валидности сертификатов (передаётся validity_path= — без патча CERT_VALIDITY_PATH)."""
    cert_dir = tmp_path / "le"
    cert_dir.mkdir(parents=True)
    return cert_dir


@pytest.fixture
def issue_script(tmp_path: Path) -> Path:
    """Stub issue-cert.sh — exits 0 (successful issuance), never calls acme.sh."""
    script = tmp_path / "issue-cert.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)
    return script


class _FakeS3Cache:
    """Fake S3-кэш (DI s3_cache=): записывает вызовы, конфигурируемое поведение.

    ## @purpose — Заменяет monkeypatch s3_ssl_cache.check_cert/download_cert/upload_cert —
    ##            объект с нужными методами (DI-канон 163 W-H).
    ## @io       ⇥ check_hit: bool (check_cert → True/False), raise_check: bool (check_cert → OSError)
    ##           ⎋ fake-объект с .uploaded/.downloaded рекордерами
    ## @complexity O(1)
    """

    def __init__(self, *, check_hit: bool = False, raise_check: bool = False) -> None:
        self.uploaded: list[str] = []
        self.downloaded: list[str] = []
        self._check_hit = check_hit
        self._raise_check = raise_check

    def check_cert(self, _domain: str, _s3_bucket: str) -> bool:
        if self._raise_check:
            msg = "S3 unavailable"
            raise OSError(msg)
        return self._check_hit

    def download_cert(self, domain: str, _cert_dir: str, _acme_home: str, _s3_bucket: str) -> bool:
        self.downloaded.append(domain)
        return True

    def upload_cert(self, domain: str, _validity_path: str, _acme_home: str, _s3_bucket: str) -> bool:
        self.uploaded.append(domain)
        return True


class _FailingRunner:
    """CommandRunner-fake: run() всегда падает (CalledProcessError) — self-signed fallback → failed."""

    def run(
        self,
        cmd: list[str],
        *,
        timeout: int | None = None,  # ruff: ignore[ARG002] — протокольный fake (сигнатура CommandRunner)
        check: bool = False,  # ruff: ignore[ARG002]
        non_fatal: bool = False,  # ruff: ignore[ARG002]
        fatal_rc: tuple[int, ...] = (),  # ruff: ignore[ARG002]
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, cmd)


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Scenario 1: valid cert on disk → SKIP + upload to S3 (restore-first)
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · disk-synced · valid cert on disk skips issue and uploads to S3
# · Regression: DevPlan 052 §4.5 — cert on disk must upload to S3 (upload-on-skip)
# · Last fail: N/A (native replacement for test_node_lifecycle_checks_s3_before_issue grep)
# · Remove if: orchestrate_certs restore-first flow changes
def test_orchestrate_certs_skips_valid_disk_cert(cert_env, caplog) -> None:
    """Valid cert on disk → status='skipped', source='disk_synced', S3 upload invoked."""
    caplog.set_level(0)
    domain = "example.com"
    (cert_env / domain).mkdir(parents=True)
    (cert_env / domain / "fullchain.pem").write_text("cert")

    fake_cache = _FakeS3Cache()
    result = co.orchestrate_certs(
        [domain],
        "/tmp/nonexistent-issue.sh",
        validity_path=str(cert_env),
        cert_validity_fn=lambda _: True,
        s3_cache=fake_cache,
        environ={"S3_BUCKET": "test-bucket"},
    )

    entry = result.domains[domain]
    assert entry.status == "skipped", f"Expected skipped, got {entry.status}"
    assert entry.source == "disk_synced", f"Expected disk_synced, got {entry.source}"
    assert fake_cache.uploaded == [domain], "upload-on-skip must sync the disk cert to S3"
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
def test_orchestrate_certs_restores_from_s3(cert_env, caplog) -> None:
    """No disk cert + S3 cache hit → status='restored', source='s3', no issue fallback."""
    caplog.set_level(0)
    domain = "example.com"
    (cert_env / domain).mkdir(parents=True)
    (cert_env / domain / "fullchain.pem").write_text("cert")

    fake_cache = _FakeS3Cache(check_hit=True)
    result = co.orchestrate_certs(
        [domain],
        "/tmp/nonexistent-issue.sh",
        validity_path=str(cert_env),
        cert_validity_fn=lambda _: False,
        s3_cache=fake_cache,
        environ={"S3_BUCKET": "test-bucket"},
    )

    entry = result.domains[domain]
    assert entry.status == "restored", f"Expected restored, got {entry.status}"
    assert entry.source == "s3", f"Expected source s3, got {entry.source}"
    assert fake_cache.downloaded == [domain], "S3 download must be invoked on cache hit"
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
def test_orchestrate_certs_issue_fallback_on_s3_miss(cert_env, issue_script, caplog) -> None:
    """S3 cache miss → issue-cert.sh fallback (stub exit 0) → status='issued', source='acme', upload."""
    caplog.set_level(0)
    domain = "example.com"

    fake_cache = _FakeS3Cache(check_hit=False)
    result = co.orchestrate_certs(
        [domain],
        str(issue_script),
        validity_path=str(cert_env),
        cert_validity_fn=lambda _: False,
        s3_cache=fake_cache,
        environ={"S3_BUCKET": "test-bucket"},
    )

    entry = result.domains[domain]
    assert entry.status == "issued", f"Expected issued, got {entry.status}"
    assert entry.source == "acme", f"Expected source acme, got {entry.source}"
    assert fake_cache.uploaded == [domain], "post-issue S3 upload must run (issue-cert.sh wiring contract)"
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
def test_orchestrate_certs_s3_failure_non_fatal(cert_env, caplog) -> None:
    """S3 check raises OSError → orchestrate_certs does NOT raise; domain ends 'failed' (last resort)."""
    caplog.set_level(0)
    domain = "example.com"

    fake_cache = _FakeS3Cache(raise_check=True)
    # runner падает (CalledProcessError) → self-signed fallback → status="failed" (не raise)
    result = co.orchestrate_certs(
        [domain],
        "/tmp/nonexistent-issue.sh",
        validity_path=str(cert_env),
        cert_validity_fn=lambda _: False,
        s3_cache=fake_cache,
        runner=_FailingRunner(),
        environ={"S3_BUCKET": "test-bucket"},
    )

    assert result.domains[domain].status == "failed", "S3 failure must degrade to failed, not crash"
    assert result.failed == 1
    logger.critical("[IMP:9][test] S3 failure non-fatal — orchestrate_certs returned without raising")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Scenario 6: S3 hit + SAN-only LE-style real cert → restored (DevPlan 004 W2 AC4)
# ═════════════════════════════════════════════════════════════════════════════


class _FakeS3CacheSanRestore(_FakeS3Cache):
    """Fake S3-кэш для SAN-restore: download_cert пишет РЕАЛЬНЫЙ SAN-only openssl-сертификат.

    ## @purpose — Scenario 6 (DevPlan 004 W2 AC4): download_cert кладёт настоящий
    ##            SAN-only LE-подобный fullchain (openssl-генерация) в cert_dir/<domain>/ —
    ##            как настоящий s3_ssl_cache.download_cert после успешной загрузки из S3.
    ## @io       ⇥ domain/cert_dir через download_cert-параметры → ⎋ True (cert на диске)
    ## @complexity O(1) + openssl subprocess (генерация фикстуры)
    """

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(check_hit=True)
        self._tmp_path = tmp_path

    def download_cert(self, domain: str, cert_dir: str, _acme_home: str, _s3_bucket: str) -> bool:
        self.downloaded.append(domain)
        live_dir = Path(cert_dir) / domain
        live_dir.mkdir(parents=True, exist_ok=True)
        key_out = self._tmp_path / f"{domain}.key"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-nodes",
                "-newkey",
                "rsa:2048",
                "-days",
                "60",
                "-subj",
                "/",  # SAN-only LE-style: subject пуст
                "-addext",
                f"subjectAltName=DNS:{domain}",
                "-keyout",
                str(key_out),
                "-out",
                str(live_dir / "fullchain.pem"),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return True


# 🧪 TRAP[TEST] · 2026-08-16 · Regression · SAN-only LE-серт из S3-кеша восстанавливается (AC4)
# · Scenario: S3 hit + download пишет РЕАЛЬНЫЙ SAN-only fullchain (subject пуст, SAN=DNS:domain);
#   валидация (cert_is_valid DI-default + monkeypatch cert_is_le_issuer — issuer self-generated)
#   проходит через SAN-ветку W1 → status='restored', source='s3'; issue_script НЕ запускался
#   (/tmp/nonexistent-issue.sh: запуск дал бы failed — assert restored гарантирует не-issue)
# · Last fail: bootstrap 2026-08-16 — SAN-only certs rejected on restore → re-issue (LE rate-limit)
# · Remove if: _try_s3_restore flow changes или SAN-aware matching удалён из ssl_certs
def test_orchestrate_certs_restores_san_only_from_s3(
    tmp_path: Path, cert_env: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SAN-only real cert из S3 → restored (не issue-fallback); баг false-miss закрыт (AC4)."""
    caplog.set_level(0)
    domain = "example.com"
    # Garbage-серт на диске: Step 1 (реальная cert_is_valid) его отвергает → путь S3-restore
    (cert_env / domain).mkdir(parents=True)
    (cert_env / domain / "fullchain.pem").write_text("garbage-not-a-cert")

    # Issuer у openssl-генерированных сертов self-generated → патчим ТОЛЬКО LE-точку
    # (cert_is_valid вызывает cert_is_le_issuer из namespace ssl_certs на момент вызова)
    monkeypatch.setattr(ssl_certs_module, "cert_is_le_issuer", lambda *_a, **_k: True)

    fake_cache = _FakeS3CacheSanRestore(tmp_path)
    result = co.orchestrate_certs(
        [domain],
        "/tmp/nonexistent-issue.sh",
        validity_path=str(cert_env),
        s3_cache=fake_cache,
        environ={"S3_BUCKET": "test-bucket"},
    )

    entry = result.domains[domain]
    assert entry.status == "restored", f"Expected restored, got {entry.status}"
    assert entry.source == "s3", f"Expected source s3, got {entry.source}"
    assert fake_cache.downloaded == [domain], "S3 download must be invoked on cache hit"
    assert result.restored == 1
    # Реальный SAN-only fullchain действительно на диске
    restored_cert = (cert_env / domain / "fullchain.pem").read_text()
    assert "BEGIN CERTIFICATE" in restored_cert, "download must place the real PEM cert"
    logger.critical("[IMP:9][test] SAN-only S3 restore → restored(source=s3) — W1 SAN-aware confirmed")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Scenario 5: no domains → no-op
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · noop · empty domain list returns empty CertResult
# · Regression: orchestrate_certs([]) must not crash
# · Last fail: N/A (native contract)
# · Remove if: no-domains handling changes
def test_orchestrate_certs_no_domains_noop(caplog) -> None:
    """Empty domain list → empty CertResult, no subprocess, no crash."""
    caplog.set_level(0)
    # environ={} → PLATFORM_DOMAIN отсутствует (без delenv — DI-поверхность)
    result = co.orchestrate_certs([], "/tmp/nonexistent-issue.sh", environ={})

    assert result.domains == {}
    assert result.issued == 0 and result.restored == 0 and result.skipped == 0 and result.failed == 0
    logger.critical("[IMP:9][test] orchestrate_certs([]) → empty CertResult, no-op")


# endregion
