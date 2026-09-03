"""
# GREP_SUMMARY: test_cert_orchestrator, bulk-restore, s3-cache, acme-issue, graceful-degradation, idempotent, DI, runner, facts, validity_path, cert_validity_fn
# STRUCTURE: ▶ tmp_path + DI (runner/facts/validity_path/cert_validity_fn/s3_cache) → ◇ bulk-restore from S3 → ◇ partial restore + issue → ◇ S3 unavailable → ◇ idempotent skip → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for cert_orchestrator.py — cert orchestration (S3 restore + acme issue).
## @scope    Tests orchestrate_certs, _process_single_domain, _is_cert_valid.
## @invariants
##   - Все subprocess/файловые зависимости через DI-параметры (E1): runner, facts,
##     validity_path, cert_validity_fn, s3_cache — 0 monkeypatch subprocess/os
##   - Cert files created in tmp_path
##   - Каждый тест валидирует IMP:9 бизнес-логику через ldd_trajectory
## @rationale DevPlan 047 Phase 7: cert orchestrator eliminates manual cert management.
## @changes  2026-07-22 | DevPlan 047 Phase 7 — Created
##           2026-08-13 | E1 (160) — DI-конвертация (setattr 14 → 0, −100%)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"
sys.path.insert(0, str(_MODULE_DIR))
import cert_orchestrator as cert

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import pytest

from core.internal.shared.ssl_certs import cert_is_le_issuer, cert_is_valid  # C9: единая комбинация
from tests.helpers.fakes import FakeCommandRunner
from tests.helpers.fakes import make_proc as _proc

pytestmark = pytest.mark.static_audit


def _ok_runner() -> FakeCommandRunner:
    """Fake-раннер успеха: все команды → rc=0."""
    return FakeCommandRunner(default=_proc(0))


class _FakeFacts:
    """Fake EnvironmentFacts: файлы существуют по факту (real os.path.isfile на tmp_path)."""

    def is_root(self) -> bool:
        return True

    def which(self, _binary) -> str | None:
        return None

    def path_isfile(self, path) -> bool:
        return Path(path).is_file()


# ═══════════════════════════════════════════════════════════════════
# region Tests: orchestrate_certs
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · orchestrate_certs restores all domains from S3
# · Scenario: All domains return S3 cache hit → all restored, 0 issued
# · Last fail: N/A (new test)
# · Remove if: orchestrate_certs logic changes
@ldd_trajectory
def test_bulk_restore_all_from_s3(caplog, tmp_path):
    """orchestrate_certs should restore all domains from S3 when available.

    DevPlan 052 Phase 1: now uses direct s3_ssl_cache import instead of subprocess.
    Mocks s3_ssl_cache через s3_cache DI-параметр.
    """
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).touch()

    # S3 mock через s3_cache DI (0 monkeypatch cert.s3_ssl_cache)
    mock_s3 = MagicMock()
    mock_s3.check_cert.return_value = True
    mock_s3.download_cert.return_value = True

    # cert_is_valid → False (нет валидного сертификата на диске) — cert_validity_fn DI
    # facts.path_isfile: cert появляется после "download" (fullchain.pem → True)
    def _fake_facts_after_download(path):
        if "fullchain.pem" in str(path):
            return True
        return _FakeFacts().path_isfile(path)

    facts = _FakeFacts()
    facts.path_isfile = _fake_facts_after_download  # type: ignore[method-assign]

    result = cert.orchestrate_certs(
        ["example.com", "test.com"],
        issue_script,
        cert_validity_fn=lambda *_, **__: False,
        s3_cache=mock_s3,
        facts=facts,
        environ={"S3_BUCKET": "test-bucket"},
    )

    assert result.restored == 2
    assert result.issued == 0
    assert result.failed == 0
    # Verify s3_ssl_cache was called
    assert mock_s3.check_cert.call_count == 2
    assert mock_s3.download_cert.call_count == 2
    logger.critical("[IMP:9][test] Bulk restore from S3 — all domains restored via direct import")


# 🧪 TRAP[TEST] · Regression · orchestrate_certs issues certs when S3 miss
# · Scenario: S3 check fails → issue-cert.sh called → cert issued
# · Last fail: N/A (new test)
# · Remove if: partial restore + issue logic changes
@ldd_trajectory
def test_partial_restore_then_issue(caplog, tmp_path):
    """orchestrate_certs should fall back to issue when S3 miss."""
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).touch()

    # S3 miss (check → False) → issue-cert.sh → rc=0 (issued)
    mock_s3 = MagicMock()
    mock_s3.check_cert.return_value = False

    runner = _ok_runner()

    result = cert.orchestrate_certs(
        ["example.com"],
        issue_script,
        cert_validity_fn=lambda *_, **__: False,
        s3_cache=mock_s3,
        runner=runner,
        facts=_FakeFacts(),
        environ={"S3_BUCKET": "test-bucket"},
    )

    assert result.issued >= 1 or result.failed >= 1
    # issue-cert.sh вызван (bash <script>)
    assert any(c[0] == "bash" and str(issue_script) in c for c in runner.calls), (
        f"issue-cert.sh не вызван: {runner.calls}"
    )
    logger.critical("[IMP:9][test] Partial restore + issue — fallback to acme.sh works")


# 🧪 TRAP[TEST] · Regression · orchestrate_certs handles S3 unavailable gracefully
# · Scenario: s3-ssl-cache.sh not found → falls back to issue-cert.sh
# · Last fail: N/A (new test)
# · Remove if: S3 graceful degradation logic changes
@ldd_trajectory
def test_s3_unavailable_graceful(caplog, tmp_path):
    """orchestrate_certs should gracefully handle S3 unavailable."""
    # NONEXISTENT issue script — форсирует self-signed fallback (F6), как в тесте:
    # S3_BUCKET отсутствует + issue-cert.sh нет → _generate_self_signed
    nonexistent_issue = str(tmp_path / "nonexistent-issue-cert.sh")
    validity_path = str(tmp_path / "live")

    # openssl self-signed (F6): genrsa + req — создают файлы (side-effect через runner)
    def _self_signed_runner_run(cmd, **kwargs):
        if isinstance(cmd, list) and "openssl" in " ".join(cmd):
            out_idx = cmd.index("-out") + 1 if "-out" in cmd else -1
            if out_idx > 0 and out_idx < len(cmd):
                Path(cmd[out_idx]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[out_idx]).write_text("mock-cert", encoding="utf-8")
        return _proc(0)

    runner = _ok_runner()
    runner.run = _self_signed_runner_run  # type: ignore[method-assign]

    result = cert.orchestrate_certs(
        ["example.com"],
        nonexistent_issue,
        cert_validity_fn=lambda *_, **__: False,
        validity_path=validity_path,
        runner=runner,
        facts=_FakeFacts(),
        environ={},  # S3_BUCKET отсутствует → graceful S3 unavailable
    )

    # Should not crash — either issued or failed gracefully
    assert len(result.domains) == 1
    # With F6 self-signed fallback, status should be "issued" (self_signed)
    assert result.domains["example.com"].status == "issued", (
        f"Expected self-signed issued, got '{result.domains['example.com'].status}'"
    )
    assert result.domains["example.com"].source == "self_signed", (
        f"Expected self_signed fallback, got '{result.domains['example.com'].source}'"
    )
    logger.critical("[IMP:9][test] S3 unavailable — graceful fallback to self-signed (F6)")


# 🧪 TRAP[TEST] · Regression · orchestrate_certs skips already-valid certs (idempotent)
# · Scenario: cert_validity_fn returns True → domain skipped
# · Last fail: N/A (new test)
# · Remove if: idempotency skip logic changes
@ldd_trajectory
def test_idempotent_skip_valid(caplog, tmp_path):
    """orchestrate_certs should skip domains with valid certs on disk
    and upload to S3 (source="disk_synced")."""
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).touch()
    validity_path = str(tmp_path / "live")

    # cert_validity_fn → True (cert already valid); facts.path_isfile → True для fullchain.pem
    mock_s3 = MagicMock()
    mock_s3.upload_cert.return_value = True

    class _CertPresentFacts(_FakeFacts):
        def path_isfile(self, path) -> bool:
            return "fullchain.pem" in str(path) or Path(path).is_file()

    result = cert.orchestrate_certs(
        ["example.com"],
        issue_script,
        cert_validity_fn=lambda *_, **__: True,
        validity_path=validity_path,
        s3_cache=mock_s3,
        facts=_CertPresentFacts(),
        environ={"S3_BUCKET": "test-bucket"},
    )

    assert result.skipped == 1
    assert result.domains["example.com"].status == "skipped"
    assert result.domains["example.com"].source == "disk_synced", (
        f"Expected source='disk_synced', got '{result.domains['example.com'].source}'"
    )
    logger.critical("[IMP:9][test] Idempotent skip — valid cert on disk, source=disk_synced")


# endregion Tests: orchestrate_certs

# ═══════════════════════════════════════════════════════════════════
# region Tests: _is_le_issuer — P0 fix: reject non-LE certs
# ═══════════════════════════════════════════════════════════════════
# DevPlan 117 D21: _is_le_issuer удалён → тестируется shared/ssl_certs.cert_is_le_issuer
# (единый openssl-примитив; cert_orchestrator._is_cert_valid вызывает его через shared)


# 🧪 TRAP[TEST] · Regression · _is_le_issuer accepts Let's Encrypt certs
# · Scenario: openssl x509 -issuer returns "Let's Encrypt" → True
# · Last fail: N/A (new test for P0 fix)
# · Remove if: issuer check logic changes
@ldd_trajectory
def test_is_le_issuer_accepts_le_cert(caplog):
    """cert_is_le_issuer should return True for Let's Encrypt issuer."""
    with __import__("unittest.mock").mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n",
            stderr="",
        )
        result = cert_is_le_issuer("/fake/path/fullchain.pem")
    assert result is True
    logger.critical("[IMP:9][test] cert_is_le_issuer accepts LE cert")


# 🧪 TRAP[TEST] · Regression · _is_le_issuer rejects mkcert certs
# · Scenario: openssl x509 -issuer returns "mkcert development CA" → False
# · Last fail: 2026-07-22 — P0 mkcert certs survived bootstrap
# · Remove if: NEVER — this is the regression test for the P0 fix
@ldd_trajectory
def test_is_le_issuer_rejects_mkcert_cert(caplog):
    """cert_is_le_issuer should return False for mkcert/self-signed certs."""
    with __import__("unittest.mock").mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "issuer=O = mkcert development CA, "
                "OU = tronyx@MacBook-Pro-Vladimir-2.local, "
                "CN = mkcert tronyx@MacBook-Pro-Vladimir-2.local\n"
            ),
            stderr="",
        )
        result = cert_is_le_issuer("/fake/path/fullchain.pem")
    assert result is False
    logger.critical("[IMP:9][test] cert_is_le_issuer rejects mkcert cert")


# 🧪 TRAP[TEST] · Regression · _is_le_issuer handles openssl failure
# · Scenario: openssl returns non-zero → return False
# · Last fail: N/A (new test)
@ldd_trajectory
def test_is_le_issuer_handles_openssl_failure(caplog):
    """cert_is_le_issuer should return False when openssl fails."""
    with __import__("unittest.mock").mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = cert_is_le_issuer("/nonexistent.pem")
    assert result is False
    logger.critical("[IMP:9][test] cert_is_le_issuer handles openssl failure gracefully")


# 🧪 TRAP[TEST] · Regression · _is_cert_valid rejects mkcert even if not expired
# · Scenario: cert not expired but issuer is mkcert → cert_is_valid (C9) returns False
# · Last fail: 2026-07-22 — P0: mkcert cert passed as "valid" because only expiry checked
# · Remove if: NEVER — this is the regression test for the P0 fix
# · C9 (DevPlan 118): приватный cert_orchestrator._is_cert_valid удалён — регрессия тестирует
# ·   shared/ssl_certs.cert_is_valid (единая комбинация, AC-C9)
@ldd_trajectory
def test_is_cert_valid_rejects_mkcert_even_if_not_expired(caplog):
    """cert_is_valid should return False for non-LE certs regardless of expiry."""
    # Mock openssl: parseable OK, но issuer — mkcert (cert не истёк, но issuer не LE)
    parse_result = MagicMock(returncode=0, stdout="", stderr="")  # -noout OK
    issuer_result = MagicMock(
        returncode=0,
        stdout="issuer=O = mkcert development CA\n",
        stderr="",
    )
    call_count = [0]

    def mock_run(cmd, **kwargs):
        call_count[0] += 1
        if "-noout" in str(cmd) and "-checkend" not in str(cmd) and "-subject" not in str(cmd):
            return parse_result
        if "-issuer" in str(cmd):
            return issuer_result
        return MagicMock(returncode=1, stdout="", stderr="")

    with __import__("unittest.mock").mock.patch("subprocess.run", side_effect=mock_run):
        result = cert_is_valid("/fake/path/fullchain.pem")

    assert result is False, "mkcert cert should NOT pass cert_is_valid regardless of expiry"
    assert call_count[0] == 2, "Should have called parseable + issuer (mkcert rejected before subject/expiry)"
    logger.critical("[IMP:9][test] cert_is_valid rejects mkcert cert — P0 regression test")


# endregion Tests: _is_le_issuer — P0 fix: reject non-LE certs

# ═══════════════════════════════════════════════════════════════════
# region Tests: ACME_CHALLENGE_MODE passthrough — DevPlan 058
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _issue_cert passes ACME_CHALLENGE_MODE env var
# · Scenario: ACME_CHALLENGE_MODE set in environ → passed to issue-cert.sh subprocess env
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: env var passthrough logic changes
@ldd_trajectory
def test_orchestrate_passes_challenge_mode(caplog, tmp_path):
    """_issue_cert should pass ACME_CHALLENGE_MODE env var to issue-cert.sh subprocess.

    ## @purpose  Verify the env var passthrough: cert_orchestrator reads ACME_CHALLENGE_MODE
    ##           from env and passes it to the subprocess running issue-cert.sh.
    """
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    Path(issue_script).chmod(0o755)

    runner = _ok_runner()

    result = cert._issue_cert(
        "example.com",
        issue_script,
        runner=runner,
        environ={"ACME_CHALLENGE_MODE": "http"},
    )

    assert result.status == "issued", f"Expected issued status, got {result.status}"
    assert result.challenge == "http", f"Expected challenge=http, got {result.challenge}"
    # issue-cert.sh вызван с bash <script> (env пробрасывается в prod-ветке subprocess.run)
    assert runner.last_cmd == ["bash", issue_script], f"Unexpected cmd: {runner.last_cmd}"

    logger.critical("[IMP:9][test_orchestrate_passes_challenge_mode] PASS: ACME_CHALLENGE_MODE passed to subprocess")


# 🧪 TRAP[TEST] · Regression · DomainCertResult contains challenge field
# · Scenario: _issue_cert returns DomainCertResult with challenge="dns" (default)
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: DomainCertResult.challenge field removed or renamed
@ldd_trajectory
def test_domain_cert_result_includes_challenge_field(caplog, tmp_path):
    """_issue_cert should return DomainCertResult with challenge field set.

    ## @purpose  Verify the new challenge field is populated in DomainCertResult.
    ## @scenario  Default ACME_CHALLENGE_MODE (unset → "dns") → _issue_cert
    ##           → DomainCertResult.challenge == "dns"
    """
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    Path(issue_script).chmod(0o755)

    runner = _ok_runner()

    result = cert._issue_cert("example.com", issue_script, runner=runner, environ={})

    assert result.status == "issued", f"Expected issued, got {result.status}"
    assert result.challenge == "dns", f"Expected challenge='dns' (default), got '{result.challenge}'"
    assert "challenge" in result.to_dict(), f"challenge field missing from to_dict(): {result.to_dict()}"

    logger.critical(
        "[IMP:9][test_domain_cert_result_includes_challenge_field] PASS: challenge field in DomainCertResult"
    )


# endregion Tests: ACME_CHALLENGE_MODE passthrough — DevPlan 058


# ═══════════════════════════════════════════════════════════════════
# region FL15 (DevPlan 125 T5): wildcard-покрытие после issue
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_post_issue_coverage_wildcard
# 🧪 TRAP[TEST] · DevPlan 125 T5 (FL15) · wildcard-покрытие — домен под *.parent → covered, НЕ alarm
# · Regression: issue-cert.sh SKIP'ает поддомены wildcard'а (rc=0) — проверка только rc
# ·   даёт «issued successfully» без сертификата → ложный alarm «Missing cert»
# · Scenario: botanika.tronyx.ru; live/tronyx.ru/fullchain.pem с subject CN = *.tronyx.ru → covered by wildcard
# · Last fail: 2026-08-03 — «botanika issued successfully» без сертификата (false-lead #15)
# · Remove if: issue-cert.sh начинает выпускать direct-сертификаты поддоменов (архитектурно)
@ldd_trajectory
def test_post_issue_coverage_wildcard(caplog, tmp_path) -> None:
    """Домен под wildcard'ом родителя → covered (INFO, НЕ alarm) — FL15."""
    caplog.set_level(logging.INFO)
    validity_path = str(tmp_path)

    # live/<domain>/fullchain.pem отсутствует; live/tronyx.ru/fullchain.pem — wildcard
    wildcard_dir = tmp_path / "tronyx.ru"
    wildcard_dir.mkdir()
    (wildcard_dir / "fullchain.pem").write_text("FAKE-PEM", encoding="utf-8")

    def _fake_subject(path: str) -> str:
        if path.endswith("/tronyx.ru/fullchain.pem"):
            return "subject=CN = *.tronyx.ru"
        return None

    with __import__("unittest.mock").mock.patch.object(cert, "cert_get_subject", side_effect=_fake_subject):
        coverage = cert._log_post_issue_coverage("botanika.tronyx.ru", validity_path=validity_path, facts=_FakeFacts())

    assert coverage == "wildcard:tronyx.ru", f"ожидалось wildcard-покрытие, got {coverage}"
    assert any("covered by wildcard" in r.message for r in caplog.records), "должен быть INFO-лог covered by wildcard"
    logger.critical("[IMP:9][test] wildcard-покрытие детектировано — НЕ alarm (FL15)")


# endregion FUNC_test_post_issue_coverage_wildcard


# region FUNC_test_post_issue_coverage_direct
# 🧪 TRAP[TEST] · DevPlan 125 T5 (FL15) · direct-покрытие — обычный выпуск не регрессирует
# · Regression: покрытие-проверка ломает штатный direct-путь
# · Scenario: live/example.com/fullchain.pem с CN = example.com → covered (direct)
# · Last fail: never (new test) · Remove if: _log_post_issue_coverage удалён
@ldd_trajectory
def test_post_issue_coverage_direct(caplog, tmp_path) -> None:
    """Direct-сертификат домена → covered (direct) — штатный путь без регрессии."""
    caplog.set_level(logging.INFO)
    validity_path = str(tmp_path)

    direct_dir = tmp_path / "example.com"
    direct_dir.mkdir()
    (direct_dir / "fullchain.pem").write_text("FAKE-PEM", encoding="utf-8")

    with __import__("unittest.mock").mock.patch.object(
        cert, "cert_get_subject", return_value="subject=CN = example.com"
    ):
        coverage = cert._log_post_issue_coverage("example.com", validity_path=validity_path, facts=_FakeFacts())

    assert coverage == "direct"
    logger.critical("[IMP:9][test] direct-покрытие детектировано — OK")


# endregion FUNC_test_post_issue_coverage_direct


# region FUNC_test_post_issue_coverage_none
# 🧪 TRAP[TEST] · NEGATIVE (R5) · FL15 — реальное отсутствие покрытия → WARN (не вечнозелёный INFO)
# · Regression: покрытие-проверка всегда говорит «covered» (гейт вечнозелёный)
# · Scenario: домен вне wildcard'а (другой apex), direct отсутствует → coverage = none
# · Last fail: 2026-08-03 — ложный «Missing cert» alarm (FL15 false-lead #15)
# · Remove if: _log_post_issue_coverage удалён
@ldd_trajectory
def test_post_issue_coverage_none(caplog, tmp_path) -> None:
    """Домен без покрытия (ни direct, ни wildcard) → 'none' + WARN — детектор честен (R5)."""
    caplog.set_level(logging.INFO)
    validity_path = str(tmp_path)

    # live/other.com/fullchain.pem НЕ существует; домен apex (other.com) — wildcard не применим
    with __import__("unittest.mock").mock.patch.object(cert, "cert_get_subject", return_value=None):
        coverage = cert._log_post_issue_coverage("other.com", validity_path=validity_path, facts=_FakeFacts())

    assert coverage == "none", f"ожидалось отсутствие покрытия, got {coverage}"
    assert any("NO cert coverage" in r.message for r in caplog.records), "должен быть WARN-лог NO cert coverage"
    logger.critical("[IMP:9][test] отсутствие покрытия детектируется — WARN, НЕ alarm (R5)")


# endregion FUNC_test_post_issue_coverage_none


# endregion FL15 (DevPlan 125 T5): wildcard-покрытие после issue


# ═══════════════════════════════════════════════════════════════════════════
# region F5 (DevPlan 031 T4): wildcard-parent skip в _process_single_domain
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_process_single_domain_wildcard_covered_skips_issue
# 🧪 TRAP[TEST] · 2026-09-03 · REGRESSION (R5) · F5 — домен под wildcard родителя → skip, НЕ issue
# · Scenario: на диске wildcard *.parent покрывает домен (cert_wildcard_covers_domain → True),
#   direct-серт невалиден → _process_single_domain возвращает skipped/wildcard_covered БЕЗ
#   обращения к S3 и issue (S3-кэш с битым self-signed + acme-попытка + self-signed fallback +
#   TG-алерт впустую — исходный churn asi roadmap.asiteam.ru, FINDING-p3-2 ночного прогона).
# · Last fail: 2026-09-03 — converge R-ssl пере-выпускал roadmap.asiteam.ru под *.asiteam.ru
# · Remove if: wildcard-parent skip удалён из _process_single_domain
@ldd_trajectory
def test_process_single_domain_wildcard_covered_skips_issue(
    caplog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5: wildcard-parent покрытие → skipped/wildcard_covered, S3+issue НЕ вызываются."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(cert, "cert_wildcard_covers_domain", lambda _le_live, _domain: True)

    mock_s3 = MagicMock()
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).touch()

    result = cert._process_single_domain(
        "roadmap.example.com",
        issue_script,
        cert_validity_fn=lambda *_, **__: False,
        validity_path=str(tmp_path / "live"),
        s3_cache=mock_s3,
        facts=_FakeFacts(),
        environ={"S3_BUCKET": "test-bucket"},
    )

    assert result.status == "skipped", f"F5: ожидался skip, got {result.status}"
    assert result.source == "wildcard_covered", f"F5: source должен быть wildcard_covered, got {result.source}"
    mock_s3.check_cert.assert_not_called()
    mock_s3.download_cert.assert_not_called()
    assert any("covered by on-disk wildcard parent" in r.message for r in caplog.records), (
        "должен быть IMP:9-лог wildcard-parent skip"
    )
    logger.critical("[IMP:9][test] F5: wildcard-covered домен заскипан (no S3/issue churn)")


# endregion FUNC_test_process_single_domain_wildcard_covered_skips_issue


# region FUNC_test_process_single_domain_no_wildcard_proceeds_to_issue
# 🧪 TRAP[TEST] · 2026-09-03 · NEGATIVE (R5) · F5 — без wildcard-покрытия issue-путь сохраняется
# · Scenario: cert_wildcard_covers_domain → False (direct junk есть, wildcard-родителя НЕТ) →
#   _process_single_domain идёт в S3/issue как раньше (сломанный direct-серт ОБЯЗАН
#   перевыпускаться). Падает, если skip-предикат начнёт маскировать выпуск direct-сертов.
# · Last fail: F5-дизайн — cert_covers_domain в skip-предикате замаскировал бы direct-выпуск
# · Remove if: skip-семантика F5 изменена
@ldd_trajectory
def test_process_single_domain_no_wildcard_proceeds_to_issue(
    caplog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5 negative: без wildcard-покрытия S3-miss → issue-cert.sh вызывается (прежний путь)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(cert, "cert_wildcard_covers_domain", lambda _le_live, _domain: False)

    mock_s3 = MagicMock()
    mock_s3.check_cert.return_value = False
    issue_script = str(tmp_path / "issue-cert.sh")
    Path(issue_script).touch()
    runner = _ok_runner()

    result = cert._process_single_domain(
        "broken.example.com",
        issue_script,
        cert_validity_fn=lambda *_, **__: False,
        validity_path=str(tmp_path / "live"),
        s3_cache=mock_s3,
        runner=runner,
        facts=_FakeFacts(),
        environ={"S3_BUCKET": "test-bucket"},
    )

    mock_s3.check_cert.assert_called_once()
    assert any(c[0] == "bash" and str(issue_script) in c for c in runner.calls), (
        f"F5 negative: issue-cert.sh должен быть вызван: {runner.calls}"
    )
    assert result.status in {"issued", "failed"}, f"F5 negative: статус issue-пути, got {result.status}"
    logger.critical("[IMP:9][test] F5 negative: без wildcard-покрытия issue-путь жив")


# endregion FUNC_test_process_single_domain_no_wildcard_proceeds_to_issue


# endregion F5 (DevPlan 031 T4)
