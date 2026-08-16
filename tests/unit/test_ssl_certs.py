# GREP_SUMMARY: test-shared-ssl-certs openssl x509 parseable expiry issuer lets-encrypt checkend constants
# STRUCTURE: ▶ test_parseable [ok|fail|timeout] → test_expiry [ok|expired] → test_issuer [le|non-le|fail] → test_constants
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/ssl_certs.py — единый SoT openssl-примитивов (DevPlan 117 D21).
## @scope    Tests: cert_is_parseable, cert_check_expiry, cert_get_issuer, cert_is_le_issuer, константы.
## @invariants
##   - Все openssl вызовы мокаются (subprocess.run) — нет реальных вызовов openssl
##   - Non-fatal контракт: subprocess ошибки → False/None (никогда не raise)
##   - LDD: IMP:9 в успешных сценариях (assert в каждом тесте)
## @changes 2026-08-01 | DevPlan 117 D21 — создан
# endregion MODULE_CONTRACT

import logging
from unittest.mock import MagicMock, patch

import pytest

from core.internal.shared.ssl_certs import (
    DEFAULT_EXPIRY_THRESHOLD,
    DEFAULT_OPENSSL_TIMEOUT,
    cert_check_expiry,
    cert_get_issuer,
    cert_is_le_issuer,
    cert_is_parseable,
    cert_is_valid,
)
from core.internal.shared.ssl_certs import (
    main as ssl_certs_cli_main,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _assert_imp9(caplog: pytest.LogCaptureFixture, needle: str | None = None) -> None:
    """Assert at least one IMP:9 log (LDD telemetry standard)."""
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
            if needle and needle in record.message:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    if needle:
        assert found, f"Critical LDD Error: No IMP:9 log containing '{needle}'"
    else:
        assert any("[IMP:9]" in r.message for r in caplog.records), "Critical LDD Error: No IMP:9 log found"


# region TEST_constants
def test_default_constants(caplog: pytest.LogCaptureFixture) -> None:
    """DEFAULT_OPENSSL_TIMEOUT=10, DEFAULT_EXPIRY_THRESHOLD=2592000 (30 дней)."""
    caplog.set_level(logging.INFO)
    assert DEFAULT_OPENSSL_TIMEOUT == 10
    assert DEFAULT_EXPIRY_THRESHOLD == 2592000
    logger.info(
        "[IMP:9][test][constants] DEFAULT_OPENSSL_TIMEOUT=%d DEFAULT_EXPIRY_THRESHOLD=%d",
        DEFAULT_OPENSSL_TIMEOUT,
        DEFAULT_EXPIRY_THRESHOLD,
    )


# endregion TEST_constants


# region TEST_cert_is_parseable
def test_parseable_ok(caplog: pytest.LogCaptureFixture) -> None:
    """openssl x509 -noout returncode=0 → True."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert cert_is_parseable("/tmp/cert.pem") is True


def test_parseable_fail(caplog: pytest.LogCaptureFixture) -> None:
    """openssl returncode!=0 → False (corrupt cert)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert cert_is_parseable("/tmp/cert.pem") is False


def test_parseable_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """openssl TimeoutExpired → False (никогда не raise)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run", side_effect=TimeoutError("timeout")):
        assert cert_is_parseable("/tmp/cert.pem") is False


# endregion TEST_cert_is_parseable


# region TEST_cert_check_expiry
def test_expiry_ok(caplog: pytest.LogCaptureFixture) -> None:
    """openssl -checkend returncode=0 → True (>threshold осталось)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert cert_check_expiry("/tmp/cert.pem", 2592000) is True
        # Threshold передаётся в команду
        args = mock_run.call_args.args[0]
        assert "2592000" in args


def test_expiry_expired(caplog: pytest.LogCaptureFixture) -> None:
    """openssl -checkend returncode!=0 → False (истёк / в пределах порога)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert cert_check_expiry("/tmp/cert.pem", 2592000) is False


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · cert_check_expiry использует DEFAULT_OPENSSL_TIMEOUT (B5)
# · Scenario: timeout kwarg == DEFAULT_OPENSSL_TIMEOUT (10), не литерал 30
# · Last fail: cert_orchestrator/nginx_harness — timeout=30 для openssl (дубль SoT, AUDIT-4 T4)
# · Remove if: ssl_certs перестаёт выполнять openssl subprocess
def test_openssl_timeout_default(caplog: pytest.LogCaptureFixture) -> None:
    """cert_check_expiry передаёт timeout=DEFAULT_OPENSSL_TIMEOUT в openssl subprocess (B5)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert cert_check_expiry("/tmp/cert.pem", 2592000) is True
        assert mock_run.call_args.kwargs["timeout"] == DEFAULT_OPENSSL_TIMEOUT
    logger.info("[IMP:9][test][openssl_timeout] cert_check_expiry timeout=%s (канон)", DEFAULT_OPENSSL_TIMEOUT)


# endregion TEST_cert_check_expiry


# region TEST_cert_get_issuer
def test_get_issuer_ok(caplog: pytest.LogCaptureFixture) -> None:
    """openssl -issuer returncode=0 → issuer строка (stripped)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n")
        issuer = cert_get_issuer("/tmp/cert.pem")
        assert issuer == "issuer=C = US, O = Let's Encrypt, CN = R11"


def test_get_issuer_fail(caplog: pytest.LogCaptureFixture) -> None:
    """openssl returncode!=0 → None."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert cert_get_issuer("/tmp/cert.pem") is None


def test_get_issuer_empty(caplog: pytest.LogCaptureFixture) -> None:
    """openssl успех, но пустой issuer → None (пустая строка не является валидным issuer)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="   \n")
        assert cert_get_issuer("/tmp/cert.pem") is None


# endregion TEST_cert_get_issuer


# region TEST_cert_is_le_issuer
def test_is_le_issuer_accepts_le(caplog: pytest.LogCaptureFixture) -> None:
    """Issuer содержит 'Let's Encrypt' → True."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n")
        assert cert_is_le_issuer("/tmp/cert.pem") is True


def test_is_le_issuer_rejects_mkcert(caplog: pytest.LogCaptureFixture) -> None:
    """Issuer mkcert/self-signed → False (P0 regression, DevPlan 117 D21)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=O = mkcert development CA, CN = mkcert\n")
        assert cert_is_le_issuer("/tmp/cert.pem") is False
        _assert_imp9(caplog, "not Let's Encrypt")


def test_is_le_issuer_openssl_failure(caplog: pytest.LogCaptureFixture) -> None:
    """openssl возвращает ненулевой код → False (никогда не raise)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert cert_is_le_issuer("/tmp/cert.pem") is False


# endregion TEST_cert_is_le_issuer


# ═══════════════════════════════════════════════════════════════════════════
# region TEST_cert_is_valid (DevPlan 118 C9 — единая комбинация)
# ═══════════════════════════════════════════════════════════════════════════


def _fake_run_results(
    caplog: pytest.LogCaptureFixture,
    *,
    parseable=True,
    issuer="Let's Encrypt",
    subject=None,
    checkend=True,
) -> patch:
    """Вернуть патчер subprocess.run с fake openssl-результатами (C9 — комбинация живёт в shared)."""
    import subprocess as _sp

    def _run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "-noout" in joined and "-subject" not in joined and "-issuer" not in joined and "-checkend" not in joined:
            return _sp.CompletedProcess(cmd, 0 if parseable else 1, stdout="", stderr="")
        if "-issuer" in joined:
            out = f"issuer=O = {issuer}" if issuer else ""
            return _sp.CompletedProcess(cmd, 0 if issuer else 1, stdout=out, stderr="")
        if "-subject" in joined:
            out = subject or "subject=CN = example.com"
            return _sp.CompletedProcess(cmd, 0, stdout=out, stderr="")
        if "-checkend" in joined:
            return _sp.CompletedProcess(cmd, 0 if checkend else 1, stdout="", stderr="")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    caplog.set_level(logging.INFO)
    return patch("core.internal.shared.ssl_certs.subprocess.run", _run)


# 🧪 TRAP[TEST] · Regression · cert_is_valid — все проверки OK (C9)
# · Scenario: parseable + LE + domain match + expiry → True
# · Last fail: три реализации «валиден» расходились (s3_ssl_cache/cert_orchestrator/context_deployer)
# · Remove if: cert_is_valid combination changes
# GUARD-PRESERVE (168): P0-mkcert guard — единственное покрытие happy-path комбинации cert_is_valid (C9), R5-база для негативов
def test_cert_is_valid_ok(caplog: pytest.LogCaptureFixture) -> None:
    """Все проверки проходят → True."""
    with _fake_run_results(caplog):
        assert cert_is_valid("/tmp/cert.pem", expected_domains="example.com") is True


# 🧪 TRAP[TEST] · NEGATIVE (R5) · cert_is_valid — 3 R5-негатива консолидированы (168 Batch 9, приём P)
# · Scenario: expired (checkend fail) / non-LE (mkcert issuer, P0) / domain mismatch (expected_domains≠subject) → False
# · Last fail: 052 expired certs restored; 2026-07-22 P0 — только expiry проверялся; 052 wrong domain's cert served
# · Remove if: соответствующие проверки удалены из cert_is_valid (P0 mkcert — NEVER)
@pytest.mark.parametrize(
    "run_kwargs,valid_kwargs",
    [
        ({"checkend": False}, {}),  # expired — -checkend returncode!=0
        ({"issuer": "mkcert development CA"}, {}),  # non-LE — P0 mkcert certs survived bootstrap
        ({"subject": "subject=CN = example.com"}, {"expected_domains": "other.com"}),  # domain mismatch
    ],
    ids=["expired", "not_le", "domain_mismatch"],
)
def test_cert_is_valid_negative(caplog: pytest.LogCaptureFixture, run_kwargs: dict, valid_kwargs: dict) -> None:
    """NEGATIVE (R5): expired / non-LE / domain mismatch → cert_is_valid(...) is False (C9)."""
    with _fake_run_results(caplog, **run_kwargs):
        assert cert_is_valid("/tmp/cert.pem", **valid_kwargs) is False


# 🧪 TRAP[TEST] · Regression · cert_is_valid — expected_domains=None пропускает domain-check (C9)
# · Scenario: без expected_domains → True (cert_orchestrator/context_deployer семантика)
# · Last fail: cert_orchestrator._is_cert_valid — только expiry+LE
# · Remove if: domain-check opt-in semantics changes
# GUARD-PRESERVE (168): единственное покрытие ветки expected_domains=None (opt-in domain-check, cert_orchestrator/context_deployer семантика)
def test_cert_is_valid_no_domains_skips_domain_check(caplog: pytest.LogCaptureFixture) -> None:
    """expected_domains=None → domain-check пропускается (True)."""
    with _fake_run_results(caplog):
        assert cert_is_valid("/tmp/cert.pem") is True


# 🧪 TRAP[TEST] · Regression · cert_is_valid — check_expiry=False пропускает expiry (C9)
# · Scenario: check_expiry=False → True даже при checkend fail (s3 download семантика)
# · Last fail: s3_ssl_cache.download_cert check_expiry=False
# · Remove if: check_expiry opt-out semantics changes
# GUARD-PRESERVE (168): единственное покрытие ветки check_expiry=False (opt-out expiry, s3 download семантика)
def test_cert_is_valid_check_expiry_false(caplog: pytest.LogCaptureFixture) -> None:
    """check_expiry=False → expiry-check пропускается (s3 download семантика)."""
    with _fake_run_results(caplog, checkend=False):
        assert cert_is_valid("/tmp/cert.pem", check_expiry=False) is True


# endregion TEST_cert_is_valid


# ═══════════════════════════════════════════════════════════════════════════
# region TEST_CLI (DevPlan 119 D1 — CLI-фасад для issue-cert.sh, паттерн ssh_opts --shell)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --is-le: LE-сертификат → exit 0 (D1, TEST_SPEC)
# · Scenario: issuer "Let's Encrypt" → main(["--is-le", cert]) == 0
# · Last fail: N/A (new — D1 CLI)
# · Remove if: ssl_certs CLI --is-le удалён
def test_cli_is_le(caplog: pytest.LogCaptureFixture) -> None:
    """CLI --is-le: LE-сертификат → exit 0 (issue-cert.sh _is_le_cert замена, D1)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=C = US, O = Let's Encrypt, CN = R11\n")
        rc = ssl_certs_cli_main(["--is-le", "/tmp/le-cert.pem"])
    assert rc == 0
    _assert_imp9(caplog, "--is-le")


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --is-le: mkcert → exit 1 (D1)
# · Scenario: issuer mkcert → main(["--is-le", cert]) == 1
# · Last fail: 2026-07-22 P0 — mkcert certs survived bootstrap (без issuer check)
# · Remove if: ssl_certs CLI --is-le удалён
def test_cli_is_le_mkcert_rejected(caplog: pytest.LogCaptureFixture) -> None:
    """CLI --is-le: mkcert/self-signed issuer → exit 1 (P0 регрессия)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=O = mkcert development CA, CN = mkcert\n")
        rc = ssl_certs_cli_main(["--is-le", "/tmp/mkcert.pem"])
    assert rc == 1


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --check-expiry: >30 дней → exit 0 (D1)
# · Scenario: openssl -checkend returncode=0 → main(["--check-expiry", cert, "30"]) == 0
# · Last fail: N/A (new — D1 CLI; replaces _acme_verify_cert openssl -enddate pipeline)
# · Remove if: ssl_certs CLI --check-expiry удалён
def test_cli_check_expiry_ok(caplog: pytest.LogCaptureFixture) -> None:
    """CLI --check-expiry CERT 30: >30 дней до истечения → exit 0."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        rc = ssl_certs_cli_main(["--check-expiry", "/tmp/cert.pem", "30"])
        # Порог передаётся в секундах: 30 дней × 86400 = 2592000
        args = mock_run.call_args.args[0]
        assert "2592000" in args
    assert rc == 0
    _assert_imp9(caplog, "--check-expiry")


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --check-expiry: expires within days → exit 1 (D1)
# · Scenario: -checkend returncode!=0 → main(["--check-expiry", cert, "30"]) == 1
# · Last fail: N/A (new — D1 CLI)
# · Remove if: ssl_certs CLI --check-expiry удалён
def test_cli_check_expiry_fail(caplog: pytest.LogCaptureFixture) -> None:
    """CLI --check-expiry: истёк/в пределах 30 дней → exit 1 (как shell _acme_verify_cert)."""
    caplog.set_level(logging.INFO)
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        rc = ssl_certs_cli_main(["--check-expiry", "/tmp/cert.pem", "30"])
    assert rc == 1


# 🧪 TRAP[TEST] · NEGATIVE (R5) · test_issue_cert_wrapper_consistency — shell/Python parity (D1)
# · Scenario: удалённая _is_le_cert()/_acme_verify_cert() заменены CLI (issue-cert.sh вызывает
# ·   python3 -m ... --is-le/--check-expiry). Вердикт CLI (main) == вердикт функции на том же
# ·   входе (исходный вход P0: mkcert-сертификат / истёкший cert).
# · Last fail: 2026-07-22 P0 — shell-функции не имели parity-покрытия; удаление без negative = survivorship
# · Remove if: issue-cert.sh перестаёт вызывать ssl_certs CLI (возврат shell-функций)
def test_issue_cert_wrapper_consistency_negative(caplog: pytest.LogCaptureFixture) -> None:
    """R5: CLI-обёртка (что теперь вызывает issue-cert.sh) == функция на исходных входах P0."""
    caplog.set_level(logging.INFO)

    # Исходный вход 1 (P0 2026-07-22): mkcert-сертификат, который пережил bootstrap
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="issuer=O = mkcert development CA, CN = mkcert\n")
        assert cert_is_le_issuer("/tmp/mkcert.pem") is False
        assert ssl_certs_cli_main(["--is-le", "/tmp/mkcert.pem"]) == 1

    # Исходный вход 2 (AC-8): сертификат, истекающий в пределах 30 дней — оба пути отвергают
    with patch("core.internal.shared.ssl_certs.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert cert_check_expiry("/tmp/expiring.pem", 30 * 86400) is False
        assert ssl_certs_cli_main(["--check-expiry", "/tmp/expiring.pem", "30"]) == 1


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI без аргументов → usage error (D1, fail-fast)
# · Scenario: main([]) → SystemExit(2) (argparse parser.error)
# · Last fail: N/A (new — D1 CLI)
# · Remove if: ssl_certs CLI удалён
# GUARD-PRESERVE (168): P0-mkcert guard-часть CLI — fail-fast usage error (argparse exit 2, D1 канон ssh_opts), единственное покрытие no-args ветки
def test_cli_no_args_usage_error(caplog: pytest.LogCaptureFixture) -> None:
    """CLI без --is-le/--check-expiry → parser.error (fail-fast канон, как ssh_opts)."""
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit) as excinfo:
        ssl_certs_cli_main([])
    assert excinfo.value.code == 2


# endregion TEST_CLI
