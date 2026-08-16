# GREP_SUMMARY: test-dev-cert-generator, unit, required-sans, get-cert-sans, cert-is-current, verify-san, main, openssl, mkcert, idempotent
# STRUCTURE: ┌mock subprocess/which┐ → ○ test matrix: required_sans(3) → get_cert_sans(2) → cert_is_current(4) → verify_san(2) → main(4) → integration(1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/nginx/dev_cert_generator.py — 16 tests per
##           DevPlan 099 §8 $TEST_SPEC. Validates SAN contract, idempotency gate,
##           backend generation orchestration, and LDD IMP:9 telemetry.
## @scope    Pure unit tests — no Docker, no real openssl/mkcert invocation.
##           All subprocess calls mocked; cert files via tmp_path.
## @invariants
##   - Native imports: from core.modules.nginx.dev_cert_generator import ...
##   - Zero Hardcode: DEV_CERTS_DIR always via tmp_path (env-дикт DI, main(env=))
##   - LDD capture via capsys (module writes to sys.stderr, NOT logging — caplog blind)
##   - Mocks via unittest.mock.patch / monkeypatch (pytest-mock NOT in project deps)
##   - Every business-logic test asserts IMP:9 presence in captured stderr
## @rationale  DevPlan 099 TASK-5 — unit contract for the Strangler-Fig migration.
##             capsys over caplog because the module prints LDD lines to stderr
##             (DevPlan 099 §11: print-vs-logging rationale).
## @changes  2026-07-31 · DevPlan 099 — Created (16 tests)
# endregion MODULE_CONTRACT

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from _conftest.ldd import _dump_ldd_trajectory

from core.modules.nginx.dev_cert_generator import (
    DEFAULT_PLATFORM_DOMAIN,
    EXPIRY_CHECK_DAYS,
    cert_is_current,
    get_cert_sans,
    main,
    required_sans,
    verify_san,
)

pytestmark = pytest.mark.static_audit

# Base SAN entries for the default domain (matches shell required_sans contract)
_BASE_SAN = ["DNS:*.ai-platform.local", "DNS:localhost", "IP:127.0.0.1"]


# region HELPER
def _assert_imp9(captured, needle: str) -> None:
    """Assert an IMP:9 log line containing needle is present in stderr.

    ## @purpose — LDD IMP:9 gate per test (Anti-Illusion rule).
    ## @io — ⇥ captured, needle: str → ⎋ None (asserts)
    ## @complexity — O(N)
    """
    assert "[IMP:9]" in captured.err, f"No IMP:9 log found. stderr:\n{captured.err}"
    assert needle in captured.err, f"IMP:9 log missing needle {needle!r}. stderr:\n{captured.err}"


def _make_mock_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a subprocess.run result mock.

    ## @purpose — Uniform mock result for subprocess.run calls.
    ## @io — ⇥ returncode/stdout/stderr → ⎋ MagicMock
    ## @complexity — O(1)
    """
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


# endregion HELPER


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_required_sans_default
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · required_sans default domain → 3 base entries
# · Scenario: required_sans(DEFAULT_PLATFORM_DOMAIN) must return exact base SAN set
# · Last fail: N/A (new test)
# · Remove if: SAN scheme changes
def test_required_sans_default() -> None:
    """PLATFORM_DOMAIN=ai-platform.local → base SAN set (no context wildcard).

    ## @purpose — Default domain produces exactly the 3 base entries, deterministically.
    ## @io — ⇥ required_sans(DEFAULT_PLATFORM_DOMAIN) → ⎋ assert exact list
    ## @complexity — O(N log N)
    """
    result = required_sans(DEFAULT_PLATFORM_DOMAIN)
    assert result == _BASE_SAN, f"Expected base SAN {_BASE_SAN}, got {result}"
    assert "DNS:*.ai-platform.local" in result
    assert "DNS:localhost" in result
    assert "IP:127.0.0.1" in result
    assert not any("custom" in entry for entry in result), "No context wildcard expected for default domain"


# endregion FUNC_test_required_sans_default


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_required_sans_context
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · context domain adds wildcard SAN + IMP:8 log
# · Scenario: required_sans("custom.local") → 4 entries incl. DNS:*.custom.local
# · Last fail: N/A (new test)
# · Remove if: context SAN scheme changes
def test_required_sans_context(capsys) -> None:
    """PLATFORM_DOMAIN=custom.local → 4 entries including DNS:*.custom.local + IMP:8 log.

    ## @purpose — Context domain adds its wildcard on top of the base SAN.
    ## @io — ⇥ required_sans("custom.local") → ⎋ assert 4 entries + IMP:8 stderr log
    ## @complexity — O(N log N)
    """
    result = required_sans("custom.local")
    assert len(result) == 4, f"Expected 4 entries with context wildcard, got {result}"
    assert "DNS:*.custom.local" in result
    assert all(entry in result for entry in _BASE_SAN), f"Base SAN missing: {result}"

    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    assert "[IMP:8]" in captured.err and "Context domain detected" in captured.err


# endregion FUNC_test_required_sans_context


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_required_sans_sorted
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · required_sans output deterministic (sorted, unique)
# · Scenario: multiple domains → sorted + duplicate-free
# · Last fail: N/A (new test)
# · Remove if: ordering contract changes
def test_required_sans_sorted() -> None:
    """Output is sorted deterministically and duplicate-free for any domain.

    ## @purpose — Deterministic ordering is the contract for cert_is_current comparison.
    ## @io — ⇥ required_sans over multiple domains → ⎋ assert sorted + unique
    ## @complexity — O(D * N log N)
    """
    for domain in ("ai-platform.local", "custom.local", "zzz.test", "*.wild.test"):
        result = required_sans(domain)
        assert result == sorted(result), f"Not sorted for {domain}: {result}"
        assert len(result) == len(set(result)), f"Duplicate entries for {domain}: {result}"


# endregion FUNC_test_required_sans_sorted


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_get_cert_sans_parse
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · openssl subjectAltName parse + IP Address normalization
# · Scenario: mocked openssl x509 output → parsed sorted SAN list
# · Last fail: N/A (new test)
# · Remove if: parsing implementation changes
def test_get_cert_sans_parse(tmp_path: Path, capsys) -> None:
    """Mock openssl x509 output → parse + normalize IP Address → sorted list.

    ## @purpose — get_cert_sans parses realistic openssl subjectAltName output,
    ##            normalizing "IP Address:" to "IP:" for comparison parity.
    ## @io — ⇥ tmp_path cert file + mocked subprocess → ⎋ assert parsed SAN list
    ## @complexity — O(N)
    """
    cert_file = tmp_path / "fullchain.pem"
    cert_file.write_text("dummy PEM", encoding="utf-8")
    fake_stdout = "X509v3 Subject Alternative Name:\n    DNS:*.ai-platform.local, DNS:localhost, IP Address:127.0.0.1\n"

    with patch(
        "core.modules.nginx.dev_cert_generator.subprocess.run",
        return_value=_make_mock_result(0, fake_stdout),
    ) as mock_run:
        sans = get_cert_sans(cert_file)

    assert sans == _BASE_SAN, f"Parsed SAN mismatch: {sans}"
    pos_args = mock_run.call_args[0][0]
    assert pos_args[:2] == ["openssl", "x509"], f"Unexpected openssl invocation: {pos_args}"
    assert "subjectAltName" in pos_args

    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)


# endregion FUNC_test_get_cert_sans_parse


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_get_cert_sans_no_file
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · missing cert file → empty list, no subprocess
# · Scenario: get_cert_sans(nonexistent) → []
# · Last fail: N/A (new test)
# · Remove if: fail-soft contract changes
def test_get_cert_sans_no_file(tmp_path: Path, capsys) -> None:
    """Non-existent cert file → empty list (fail-soft, no subprocess call).

    ## @purpose — Missing file must not raise and must not invoke openssl.
    ## @io — ⇥ get_cert_sans(tmp_path / "missing.pem") → ⎋ assert [] + IMP:7 log
    ## @complexity — O(1)
    """
    with patch("core.modules.nginx.dev_cert_generator.subprocess.run") as mock_run:
        sans = get_cert_sans(tmp_path / "missing.pem")
    assert sans == []
    mock_run.assert_not_called()

    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    assert "[IMP:7]" in captured.err and "not found" in captured.err


# endregion FUNC_test_get_cert_sans_no_file


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_cert_is_current_exists
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · up-to-date cert → True + IMP:9
# · Scenario: files exist, SAN match, checkend 0 → True
# · Last fail: N/A (new test)
# · Remove if: idempotency gate changes
def test_cert_is_current_exists(tmp_path: Path, capsys) -> None:
    """Cert+key exist, SANs match, -checkend passes → True + IMP:9 log.

    ## @purpose — Positive idempotency gate: up-to-date cert → True, no regeneration.
    ## @io — ⇥ tmp_path cert/key + mocked get_cert_sans/checkend → ⎋ assert True + IMP:9
    ## @complexity — O(R + C + S)
    """
    cert_file = tmp_path / "fullchain.pem"
    key_file = tmp_path / "privkey.pem"
    cert_file.write_text("dummy cert", encoding="utf-8")
    key_file.write_text("dummy key", encoding="utf-8")

    with (
        patch(
            "core.modules.nginx.dev_cert_generator.get_cert_sans",
            return_value=_BASE_SAN,
        ),
        patch(
            "core.modules.nginx.dev_cert_generator.subprocess.run",
            return_value=_make_mock_result(0),
        ) as mock_run,
    ):
        is_current = cert_is_current(cert_file, key_file, DEFAULT_PLATFORM_DOMAIN)

    assert is_current is True
    pos_args = mock_run.call_args[0][0]
    assert pos_args[1] == "x509" and "-checkend" in pos_args, f"checkend not invoked: {pos_args}"
    assert str(EXPIRY_CHECK_DAYS * 86400) in pos_args, f"Wrong checkend window: {pos_args}"

    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    _assert_imp9(captured, "Cert is current")


# endregion FUNC_test_cert_is_current_exists


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_cert_is_current_missing_san
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · SAN drift → False
# · Scenario: cert missing *.ai-platform.local → False + drift logs
# · Last fail: N/A (new test)
# · Remove if: drift detection changes
def test_cert_is_current_missing_san(tmp_path: Path, capsys) -> None:
    """Cert exists but a required SAN entry missing → False (SAN drift).

    ## @purpose — Primary drift trigger: cert without *.ai-platform.local is stale.
    ## @io — ⇥ tmp_path cert/key + mocked get_cert_sans (incomplete) → ⎋ assert False + drift logs
    ## @complexity — O(R + C)
    """
    cert_file = tmp_path / "fullchain.pem"
    key_file = tmp_path / "privkey.pem"
    cert_file.write_text("dummy cert", encoding="utf-8")
    key_file.write_text("dummy key", encoding="utf-8")
    incomplete = ["DNS:localhost", "IP:127.0.0.1"]  # missing DNS:*.ai-platform.local

    with patch("core.modules.nginx.dev_cert_generator.get_cert_sans", return_value=incomplete):
        is_current = cert_is_current(cert_file, key_file, DEFAULT_PLATFORM_DOMAIN)

    assert is_current is False
    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    assert "SAN entry missing" in captured.err
    assert "SAN drift detected" in captured.err


# endregion FUNC_test_cert_is_current_missing_san


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_cert_is_current_missing_file
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · missing cert file → False, no subprocess
# · Scenario: cert_is_current(nonexistent paths) → False
# · Last fail: N/A (new test)
# · Remove if: fail-fast contract changes
def test_cert_is_current_missing_file(tmp_path: Path, capsys) -> None:
    """No cert file → False (fail-fast before any SAN/expiry work).

    ## @purpose — Missing cert must short-circuit idempotency to regeneration.
    ## @io — ⇥ cert_is_current(tmp_path paths) → ⎋ assert False + IMP:8 log
    ## @complexity — O(1)
    """
    with patch("core.modules.nginx.dev_cert_generator.subprocess.run") as mock_run:
        is_current = cert_is_current(
            tmp_path / "fullchain.pem",
            tmp_path / "privkey.pem",
            DEFAULT_PLATFORM_DOMAIN,
        )
    assert is_current is False
    mock_run.assert_not_called()

    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    assert "Cert or key file missing" in captured.err


# endregion FUNC_test_cert_is_current_missing_file


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_cert_is_current_expiring
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · expiring cert (checkend fail) → False
# · Scenario: -checkend returncode 1 → False + expiry log
# · Last fail: N/A (new test)
# · Remove if: expiry window logic changes
def test_cert_is_current_expiring(tmp_path: Path, capsys) -> None:
    """-checkend fails (expiring <30d) → False (expiry trigger).

    ## @purpose — Secondary idempotency trigger: near-expiry cert regenerates.
    ## @io — ⇥ tmp_path cert/key + mocked checkend returncode=1 → ⎋ assert False + expiry log
    ## @complexity — O(R + C + S)
    """
    cert_file = tmp_path / "fullchain.pem"
    key_file = tmp_path / "privkey.pem"
    cert_file.write_text("dummy cert", encoding="utf-8")
    key_file.write_text("dummy key", encoding="utf-8")

    with (
        patch("core.modules.nginx.dev_cert_generator.get_cert_sans", return_value=_BASE_SAN),
        patch(
            "core.modules.nginx.dev_cert_generator.subprocess.run",
            return_value=_make_mock_result(1),
        ),
    ):
        is_current = cert_is_current(cert_file, key_file, DEFAULT_PLATFORM_DOMAIN)

    assert is_current is False
    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    assert "expires within" in captured.err


# endregion FUNC_test_cert_is_current_expiring


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_verify_san_all_present
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · all SANs present → True + IMP:9
# · Scenario: verify_san with full set → True
# · Last fail: N/A (new test)
# · Remove if: verification logic changes
def test_verify_san_all_present(tmp_path: Path, capsys) -> None:
    """All required SANs in cert → True + IMP:9 success log.

    ## @purpose — Positive post-generation verification.
    ## @io — ⇥ tmp_path cert + mocked get_cert_sans (full set) → ⎋ assert True + IMP:9
    ## @complexity — O(R + C)
    """
    cert_file = tmp_path / "fullchain.pem"
    cert_file.write_text("dummy cert", encoding="utf-8")

    with patch("core.modules.nginx.dev_cert_generator.get_cert_sans", return_value=_BASE_SAN):
        ok = verify_san(cert_file, _BASE_SAN)

    assert ok is True
    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    _assert_imp9(captured, "All required SAN entries present")


# endregion FUNC_test_verify_san_all_present


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_verify_san_missing
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · missing SAN → False + IMP:9 failure log
# · Scenario: verify_san with incomplete set → False, SAN MISSING log
# · Last fail: N/A (new test)
# · Remove if: verification logic changes
def test_verify_san_missing(tmp_path: Path, capsys) -> None:
    """One SAN missing → False + IMP:9 failure log (DevPlan §8 example).

    ## @purpose — Post-generation failure path must be loud: per-entry MISSING + summary.
    ## @io — ⇥ tmp_path cert + mocked get_cert_sans (incomplete) → ⎋ assert False + IMP:9
    ## @complexity — O(R + C)
    """
    cert_file = tmp_path / "fullchain.pem"
    cert_file.write_text("dummy cert", encoding="utf-8")

    with patch("core.modules.nginx.dev_cert_generator.get_cert_sans", return_value=["DNS:localhost"]):
        result = verify_san(cert_file, _BASE_SAN)

    assert result is False
    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    assert "[IMP:9]" in captured.err, "No IMP:9 log on SAN verification failure"
    assert any(token in captured.err for token in ("SAN MISSING", "FAILED"))


# endregion FUNC_test_verify_san_missing


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_main_idempotent_noop
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · main no-op when cert current
# · Scenario: cert_is_current=True → exit 0, no generation
# · Last fail: N/A (new test)
# · Remove if: main orchestration changes
def test_main_idempotent_noop(tmp_path: Path, capsys) -> None:
    """cert_is_current=True → exit 0, no generation backend invoked.

    ## @purpose — Idempotency: main() short-circuits before any subprocess/backend call.
    ## @io — ⇥ env-дикт (DI) + mocked cert_is_current → ⎋ assert exit 0, no generate
    ## @complexity — O(1)
    """
    env = {
        "DEV_CERTS_DIR": str(tmp_path),
        "PLATFORM_DOMAIN": DEFAULT_PLATFORM_DOMAIN,
        "CERT_BACKEND": "openssl",
    }

    with (
        patch("core.modules.nginx.dev_cert_generator.cert_is_current", return_value=True),
        patch("core.modules.nginx.dev_cert_generator.generate_openssl") as mock_gen,
        patch("core.modules.nginx.dev_cert_generator.generate_mkcert") as mock_mkcert,
        pytest.raises(SystemExit) as excinfo,
    ):
        main(env=env)

    assert excinfo.value.code == 0
    mock_gen.assert_not_called()
    mock_mkcert.assert_not_called()

    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    _assert_imp9(captured, "no action needed")


# endregion FUNC_test_main_idempotent_noop


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_main_generate_missing
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · main generation path (openssl)
# · Scenario: no cert → generate → verify → exit 0
# · Last fail: N/A (new test)
# · Remove if: main orchestration changes
def test_main_generate_missing(tmp_path: Path, capsys) -> None:
    """No cert → openssl backend generates → verify passes → exit 0.

    ## @purpose — Generation path: cert_is_current=False routes through backend + verify.
    ## @io — ⇥ env-дикт (DI) + mocked cert_is_current/which/generate_openssl/verify_san → ⎋ assert exit 0
    ## @complexity — O(1) + mocks
    """
    env = {
        "DEV_CERTS_DIR": str(tmp_path),
        "PLATFORM_DOMAIN": DEFAULT_PLATFORM_DOMAIN,
        "CERT_BACKEND": "openssl",
    }
    fake_cert = tmp_path / "fullchain.pem"

    with (
        patch("core.modules.nginx.dev_cert_generator.cert_is_current", return_value=False),
        patch("core.modules.nginx.dev_cert_generator.shutil.which", return_value="/usr/bin/openssl"),
        patch("core.modules.nginx.dev_cert_generator.generate_openssl", return_value=fake_cert) as mock_gen,
        patch("core.modules.nginx.dev_cert_generator.verify_san", return_value=True) as mock_verify,
        pytest.raises(SystemExit) as excinfo,
    ):
        main(env=env)

    assert excinfo.value.code == 0
    mock_gen.assert_called_once()
    mock_verify.assert_called_once()

    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    _assert_imp9(captured, "Certificate generated successfully")


# endregion FUNC_test_main_generate_missing


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_main_unknown_backend
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · unknown CERT_BACKEND → exit 1
# · Scenario: CERT_BACKEND=invalid → SystemExit(1) + IMP:9 error
# · Last fail: N/A (new test)
# · Remove if: backend selection changes
def test_main_unknown_backend(tmp_path: Path, capsys) -> None:
    """CERT_BACKEND=invalid → exit 1 + IMP:9 error log.

    ## @purpose — Unknown backend must fail loudly, never silently fall back.
    ## @io — ⇥ env-дикт (DI) + mocked cert_is_current=False → ⎋ assert exit 1 + IMP:9 error
    ## @complexity — O(1)
    """
    env = {
        "DEV_CERTS_DIR": str(tmp_path),
        "PLATFORM_DOMAIN": DEFAULT_PLATFORM_DOMAIN,
        "CERT_BACKEND": "invalid",
    }

    with (
        patch("core.modules.nginx.dev_cert_generator.cert_is_current", return_value=False),
        pytest.raises(SystemExit) as excinfo,
    ):
        main(env=env)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    _assert_imp9(captured, "Unknown CERT_BACKEND")


# endregion FUNC_test_main_unknown_backend


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_main_missing_backend_tool
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · explicit backend without binary → exit 1
# · Scenario: CERT_BACKEND=mkcert, which→None → SystemExit(1) + IMP:9
# · Last fail: N/A (new test)
# · Remove if: backend tool gate changes
def test_main_missing_backend_tool(tmp_path: Path, capsys) -> None:
    """CERT_BACKEND=mkcert but mkcert not in PATH → exit 1 + IMP:9 error.

    ## @purpose — Explicit backend without the binary is a hard failure (shell parity).
    ## @io — ⇥ env-дикт (DI) + mocked cert_is_current=False + which=None → ⎋ assert exit 1 + IMP:9
    ## @complexity — O(1)
    """
    env = {
        "DEV_CERTS_DIR": str(tmp_path),
        "PLATFORM_DOMAIN": DEFAULT_PLATFORM_DOMAIN,
        "CERT_BACKEND": "mkcert",
    }

    with (
        patch("core.modules.nginx.dev_cert_generator.cert_is_current", return_value=False),
        patch("core.modules.nginx.dev_cert_generator.shutil.which", return_value=None),
        pytest.raises(SystemExit) as excinfo,
    ):
        main(env=env)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    _assert_imp9(captured, "mkcert not in PATH")


# endregion FUNC_test_main_missing_backend_tool


# ═══════════════════════════════════════════════════════════════════
# region FUNC_test_integration_full_flow
# ═══════════════════════════════════════════════════════════════════
# 🧪 TRAP[TEST] · Regression · full flow generate → verify → idempotent no-op
# · Scenario: mocked openssl; 2× main() → req invoked exactly once
# · Last fail: N/A (new test)
# · Remove if: end-to-end contract changes
def test_integration_full_flow(tmp_path: Path, capsys) -> None:
    """Full flow with mocked subprocess: generate → verify → idempotent no-op.

    ## @purpose — End-to-end main() trajectory (AC5): first run generates certs and
    ##            verifies SAN; second run is a true no-op (req NOT re-invoked).
    ## @io — ⇥ env-дикт (DI) + mocked openssl subprocess (req/x509/checkend) → ⎋ assert exit 0 both runs
    ## @complexity — O(1) + mock trajectory
    ## @invariants
    ##   - req subprocess invoked exactly once across both runs
    ##   - Generated cert/key files exist in tmp_path
    """
    env = {
        "DEV_CERTS_DIR": str(tmp_path),
        "PLATFORM_DOMAIN": DEFAULT_PLATFORM_DOMAIN,
        "CERT_BACKEND": "openssl",
    }

    required = required_sans(DEFAULT_PLATFORM_DOMAIN)
    req_count = {"n": 0}

    def _side_effect(cmd, **kwargs):
        if cmd[1] == "req":
            req_count["n"] += 1
            key_path = Path(cmd[cmd.index("-keyout") + 1])
            cert_path = Path(cmd[cmd.index("-out") + 1])
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_text("fake key\n", encoding="utf-8")
            cert_path.write_text("fake cert\n", encoding="utf-8")
            return _make_mock_result(0)
        if "-checkend" in cmd:
            return _make_mock_result(0)
        if "subjectAltName" in cmd:
            san_text = ", ".join(
                entry.replace("IP:", "IP Address:", 1) if entry.startswith("IP:") else entry for entry in required
            )
            return _make_mock_result(0, f"X509v3 Subject Alternative Name:\n    {san_text}\n")
        return _make_mock_result(0)

    with patch("core.modules.nginx.dev_cert_generator.subprocess.run", side_effect=_side_effect):
        # First run: generate + verify
        with pytest.raises(SystemExit) as excinfo:
            main(env=env)
        assert excinfo.value.code == 0, "First run should succeed"

        cert_file = tmp_path / "fullchain.pem"
        key_file = tmp_path / "privkey.pem"
        assert cert_file.exists(), f"Cert not created: {cert_file}"
        assert key_file.exists(), f"Key not created: {key_file}"
        assert req_count["n"] == 1, f"openssl req invoked {req_count['n']} times on first run"

        # Second run: idempotent no-op (real cert_is_current reads mocked SAN + checkend)
        with pytest.raises(SystemExit) as excinfo2:
            main(env=env)
        assert excinfo2.value.code == 0, "Second run should be a no-op"
        assert req_count["n"] == 1, f"Cert regenerated — idempotency broken (req x{req_count['n']})"

    captured = capsys.readouterr()
    _dump_ldd_trajectory(captured.err)
    _assert_imp9(captured, "Certificate generated successfully")
    assert "no action needed" in captured.err, "Second run did not log no-op"


# endregion FUNC_test_integration_full_flow
