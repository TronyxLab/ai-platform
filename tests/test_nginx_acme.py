# GREP_SUMMARY: test nginx acme acme.sh tls http01-fallback ACME_CHALLENGE_MODE issue-cert contract LDD IMP
# STRUCTURE: ISSUE_CERT_PATH → helpers → HTTP01_FALLBACK_TESTS(5 tests)
# region MODULE_CONTRACT
## @purpose  Contract tests for acme.sh TLS operations in issue-cert.sh (canonical cert script).
##           nginx/install.sh deleted per DevPlan 080 — all cert tests now source issue-cert.sh.
## @scope    subprocess calls to issue-cert.sh shell functions via _source_and_run_issue_cert_no_main.
##           No Docker, no acme.sh, no network access required. Tests guard clauses and error paths.
## @invariants
##   - All test_* functions use @ldd_trajectory decorator
##   - Each test logs IMP:9 business logic assertion
##   - Tests validate HTTP-01 fallback, ACME_CHALLENGE_MODE behavior
##   - WEBNAMES_API_KEY can be empty for HTTP-01 mode (bypasses DNS guard)
## @changes  2026-07-23 | DevPlan 058 — Added HTTP-01 fallback tests (5 tests) + issue-cert.sh helpers
## @changes  2026-07-26 | DevPlan 080 — Removed install.sh-dependent tests (dead code deletion)
## @rationale  Contract tests call REAL bash functions, validating shell syntax, function
##   definitions, and business logic guards without requiring system-level dependencies.
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import subprocess
import tempfile

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
# Path to issue-cert.sh (canonical cert script after nginx/install.sh deletion per DevPlan 080)
_ISSUE_CERT_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "issue-cert.sh"
_ISSUE_CERT_DIR: str = str(_ISSUE_CERT_PATH.parent)


# region HELPERS (issue-cert.sh)


def _source_and_run_issue_cert_no_main(function_call: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Source issue-cert.sh (minus main "$@" tail call) and run function_call.

    ## @purpose  issue-cert.sh calls main "$@" at end of script. When non-root,
    ##            main() fails with "must run as root". This helper creates a temp
    ##            copy of issue-cert.sh with:
    ##            1. The dynamic SCRIPT_DIR replaced with the correct absolute path
    ##               (so ../../lib/logging.sh resolves correctly from temp location)
    ##            2. The trailing 'main "$@"' line removed
    ##            Then sources the temp copy and runs function_call.
    ## @io       ⇥ function_call: str — bash function call expression
    ##           ⇥ env: dict | None — optional env vars
    ##           ⎋ subprocess.CompletedProcess
    ## @complexity O(N) where N = lines in issue-cert.sh
    """
    content = _ISSUE_CERT_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Remove trailing main "$@" call
    if lines and lines[-1].strip() == 'main "$@"':
        content_modified = "\n".join(lines[:-1])
    else:
        content_modified = content

    # Replace dynamic SCRIPT_DIR with absolute path so relative deps resolve
    script_dir_line = 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"'
    content_modified = content_modified.replace(
        script_dir_line,
        f'SCRIPT_DIR="{_ISSUE_CERT_DIR}"',
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(content_modified + "\n")
        f.write(f"{function_call}\n")
        tmp_script = f.name

    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    result = subprocess.run(
        ["bash", tmp_script],
        capture_output=True,
        text=True,
        env=full_env,
    )
    os.unlink(tmp_script)
    return result


def _create_mock_acme_for_issue_cert(
    tmp_path: pathlib.Path,
    *,
    http01_fail: bool = False,
    dns01_fail: bool = False,
) -> str:
    """Create mock acme.sh in tmp_path for issue-cert.sh HTTP-01 tests.

    ## @purpose  Creates a mock acme.sh binary that supports both DNS-01 and HTTP-01
    ##            (--standalone) modes. The mock logs its args to stderr for test verification.
    ## @param     http01_fail  If True, HTTP-01 (--standalone) calls exit 1
    ## @param     dns01_fail   If True, DNS-01 (--dns) calls exit 1
    ## @io       ⇥ tmp_path, flags → ⎋ str (ACME_HOME path)
    """
    acme_home = tmp_path / "acme-mock"
    acme_home.mkdir(parents=True, exist_ok=True)

    # Build mock acme.sh dynamically based on failure flags
    lines = ["#!/bin/bash", 'echo "[MOCK-ACME] $@" >&2']
    if dns01_fail and http01_fail:
        lines.append('echo "[MOCK-ACME-ALL-FAIL]" >&2')
        lines.append("exit 1")
    elif dns01_fail:
        lines.append('if [[ "$*" == *"--issue"* ]] && [[ "$*" != *"--standalone"* ]]; then')
        lines.append('    echo "[MOCK-ACME-DNS-FAIL]" >&2')
        lines.append("    exit 1")
        lines.append("fi")
        lines.append('if [[ "$*" == *"--issue"* ]] && [[ "$*" == *"--standalone"* ]]; then')
        lines.append('    echo "[MOCK-ACME-HTTP]" >&2')
        lines.append("fi")
    elif http01_fail:
        lines.append('if [[ "$*" == *"--standalone"* ]]; then')
        lines.append('    echo "[MOCK-ACME-HTTP-FAIL]" >&2')
        lines.append("    exit 1")
        lines.append("fi")
        lines.append('if [[ "$*" == *"--dns"* ]]; then')
        lines.append('    echo "[MOCK-ACME-DNS]" >&2')
        lines.append("fi")
    else:
        lines.append('if [[ "$*" == *"--standalone"* ]]; then')
        lines.append('    echo "[MOCK-ACME-HTTP]" >&2')
        lines.append('elif [[ "$*" == *"--dns"* ]]; then')
        lines.append('    echo "[MOCK-ACME-DNS]" >&2')
        lines.append("fi")
    lines.append("exit 0")
    acme_sh_content = "\n".join(lines) + "\n"

    acme_sh = acme_home / "acme.sh"
    acme_sh.write_text(acme_sh_content)
    acme_sh.chmod(0o755)

    # dnsapi/ with dns_webnames.sh (for DNS-01 tests)
    dnsapi = acme_home / "dnsapi"
    dnsapi.mkdir(exist_ok=True)
    dns_script = dnsapi / "dns_webnames.sh"
    dns_script.write_text('#!/bin/bash\necho "[MOCK-DNS] $@" >&2\nexit 0\n')
    dns_script.chmod(0o755)

    # dnsapi_ext/ with dns_webnames.sh
    dnsapi_ext = acme_home / "dnsapi_ext"
    dnsapi_ext.mkdir(exist_ok=True)
    ext_script = dnsapi_ext / "dns_webnames.sh"
    ext_script.write_text('#!/bin/bash\nAPI_KEY=""\necho "[MOCK-DNS-EXT] $@" >&2\nexit 0\n')
    ext_script.chmod(0o755)

    return str(acme_home)


# endregion HELPERS (issue-cert.sh)


# region HTTP01_FALLBACK_TESTS
## @purpose  Contract tests for HTTP-01 fallback in issue-cert.sh.
##            Tests ACME_CHALLENGE_MODE env var behavior, _issue_http01_cert() function,
##            and DNS-01 → HTTP-01 graceful degradation.
## @scope    Sources issue-cert.sh via _source_and_run_issue_cert_no_main.
##            Uses mock acme.sh (no real acme.sh, no network).
## @invariants
##   - Each test uses @ldd_trajectory decorator
##   - No root, no Docker, no network access required
##   - Mock acme.sh simulates DNS-01 (~dns) and HTTP-01 (--standalone) modes
##   - WEBNAMES_API_KEY can be empty for HTTP-01 mode (bypasses DNS guard)


# 🧪 TRAP[TEST] · Regression · DNS-01 success with ACME_CHALLENGE_MODE=auto issues wildcard
# · Scenario: DNS-01 succeeds (mock returns 0) → wildcard cert issued
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: DNS-01 + wildcard logic changes
@pytest.mark.contract
@ldd_trajectory
def test_dns01_success_wildcard(caplog, tmp_path) -> None:
    """Verify DNS-01 success with ACME_CHALLENGE_MODE=auto issues wildcard cert.

    ## @purpose  Baseline: when DNS-01 works, wildcard cert is issued.
    ## @scenario  ACME_CHALLENGE_MODE=auto, DNS-01 mock returns 0
    ## → mock acme.sh called with --dns and -d *.domain, no HTTP-01 fallback
    ## @regression  ACME_CHALLENGE_MODE breaks DNS-01 path
    """
    acme_home = _create_mock_acme_for_issue_cert(tmp_path)
    env = {
        "ACME_HOME": acme_home,
        "WEBNAMES_API_KEY": "*test-key-123",
        "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt"),
        "ACME_CHALLENGE_MODE": "auto",
    }
    result = _source_and_run_issue_cert_no_main(
        '_issue_acme_cert "test.example.com" "admin@test.com" "webnames" "true"',
        env=env,
    )

    logger.critical("[IMP:9][test_dns01_success_wildcard] ASSERT: DNS-01 success with wildcard")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode == 0, f"Function failed: {result.stderr}"
    assert "[MOCK-ACME]" in result.stderr, "Mock acme.sh was not called"
    assert "[MOCK-ACME-DNS]" in result.stderr, f"Expected DNS-01 mode, got:\n{result.stderr}"
    assert "*.test.example.com" in result.stderr, f"Expected wildcard -d *.domain in DNS-01 mode:\n{result.stderr}"

    logger.critical("[IMP:9][test_dns01_success_wildcard] PASS: DNS-01 success issues wildcard cert")


# 🧪 TRAP[TEST] · Regression · DNS-01 failure triggers HTTP-01 fallback
# · Scenario: ACME_CHALLENGE_MODE=auto, DNS-01 mock fails → HTTP-01 fallback called
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: fallback logic changes
@pytest.mark.contract
@ldd_trajectory
def test_dns01_fail_http01_fallback(caplog, tmp_path) -> None:
    """Verify DNS-01 failure triggers HTTP-01 fallback with ACME_CHALLENGE_MODE=auto.

    ## @purpose  Core fallback behavior: DNS-01 fails → HTTP-01 is called.
    ## @scenario  ACME_CHALLENGE_MODE=auto, DNS-01 mock fails (dns01_fail=True)
    ## → issue_tls_cert tries DNS-01 first → fails → fallback to _issue_http01_cert
    ## → mock acme.sh called with --standalone (HTTP-01)
    ## @regression  Fallback not triggered — cert issuance fails completely
    """
    acme_home = _create_mock_acme_for_issue_cert(tmp_path, dns01_fail=True)
    env = {
        "ACME_HOME": acme_home,
        "WEBNAMES_API_KEY": "*test-key-123",
        "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt"),
        "ACME_CHALLENGE_MODE": "auto",
    }
    result = _source_and_run_issue_cert_no_main(
        'issue_tls_cert "test.example.com" "admin@test.com" "webnames" "true"',
        env=env,
    )

    logger.critical("[IMP:9][test_dns01_fail_http01_fallback] ASSERT: HTTP-01 fallback on DNS-01 failure")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    # Should still succeed (HTTP-01 fallback works)
    assert result.returncode == 0, f"HTTP-01 fallback should succeed: {result.stderr}"
    # Should show HTTP-01 was called
    assert "[MOCK-ACME-HTTP]" in result.stderr, f"Expected HTTP-01 fallback, DNS-01 didn't fail:\n{result.stderr}"
    # DNS-01 path should have been attempted first
    assert "[MOCK-ACME-DNS-FAIL]" in result.stderr, f"Expected DNS-01 to be attempted first:\n{result.stderr}"

    logger.critical("[IMP:9][test_dns01_fail_http01_fallback] PASS: HTTP-01 fallback on DNS-01 failure")


# 🧪 TRAP[TEST] · Regression · ACME_CHALLENGE_MODE=http bypasses DNS-01
# · Scenario: ACME_CHALLENGE_MODE=http → DNS-01 skipped, HTTP-01 used directly
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: http mode logic changes
@pytest.mark.contract
@ldd_trajectory
def test_challenge_mode_http_bypasses_dns(caplog, tmp_path) -> None:
    """Verify ACME_CHALLENGE_MODE=http bypasses DNS-01 entirely.

    ## @purpose  HTTP-only mode: no DNS plugin required, no WEBNAMES_API_KEY needed.
    ## @scenario  ACME_CHALLENGE_MODE=http, no WEBNAMES_API_KEY, empty dns_plugin
    ## → issue_tls_cert skips all DNS-01 guards → calls _issue_http01_cert directly
    ## @regression  HTTP-01 mode still requires DNS plugin — defeats purpose
    """
    acme_home = _create_mock_acme_for_issue_cert(tmp_path)
    env = {
        "ACME_HOME": acme_home,
        "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt"),
        "ACME_CHALLENGE_MODE": "http",
        # WEBNAMES_API_KEY intentionally NOT set — should not matter for HTTP-01
    }
    result = _source_and_run_issue_cert_no_main(
        'issue_tls_cert "test.example.com" "admin@test.com" "" "true"',
        env=env,
    )

    logger.critical("[IMP:9][test_challenge_mode_http_bypasses_dns] ASSERT: HTTP-01 bypasses DNS-01 guards")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode == 0, f"HTTP-01 mode should succeed without DNS plugin or API key:\n{result.stderr}"
    assert "[MOCK-ACME-HTTP]" in result.stderr, f"Expected HTTP-01 mode (--standalone), got:\n{result.stderr}"
    assert "[MOCK-ACME-DNS]" not in result.stderr, "HTTP-01 mode should NOT call DNS-01"

    logger.critical("[IMP:9][test_challenge_mode_http_bypasses_dns] PASS: HTTP-01 mode bypasses DNS-01")


# 🧪 TRAP[TEST] · Regression · ACME_CHALLENGE_MODE=auto fallback logs IMP:9 warning
# · Scenario: DNS-01 fails → HTTP-01 fallback → IMP:9 warning logged
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: fallback logging logic changes
@pytest.mark.contract
@ldd_trajectory
def test_challenge_mode_auto_fallback_logs_warning(caplog, tmp_path) -> None:
    """Verify ACME_CHALLENGE_MODE=auto logs WARN when DNS-01 fails and falls back to HTTP-01.

    ## @purpose  Traceability: operator must see clear IMP:9 log when fallback occurs.
    ## @scenario  ACME_CHALLENGE_MODE=auto, DNS-01 fails (dns01_fail=True)
    ## → issue_tls_cert falls back to HTTP-01 → stderr contains "falling back to HTTP-01"
    ## @regression  No warning → operator unaware of degraded mode
    """
    acme_home = _create_mock_acme_for_issue_cert(tmp_path, dns01_fail=True)
    env = {
        "ACME_HOME": acme_home,
        "WEBNAMES_API_KEY": "*test-key-123",
        "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt"),
        "ACME_CHALLENGE_MODE": "auto",
    }
    result = _source_and_run_issue_cert_no_main(
        'issue_tls_cert "test.example.com" "admin@test.com" "webnames" "true"',
        env=env,
    )

    logger.critical("[IMP:9][test_challenge_mode_auto_fallback_logs_warning] ASSERT: fallback warning in stderr")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode == 0, f"Fallback should succeed: {result.stderr}"
    # Verify the IMP:9 warning message about fallback
    assert "falling back to HTTP-01" in result.stderr, (
        f"Expected 'falling back to HTTP-01' warning in stderr:\n{result.stderr}"
    )
    assert "does NOT support wildcard" in result.stderr, f"Expected wildcard limitation warning:\n{result.stderr}"

    logger.critical("[IMP:9][test_challenge_mode_auto_fallback_logs_warning] PASS: fallback warning logged")


# 🧪 TRAP[TEST] · Regression · HTTP-01 issues individual cert, not wildcard
# · Scenario: _issue_http01_cert called → single -d domain, no -d *.domain
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: HTTP-01 issue logic changes
@pytest.mark.contract
@ldd_trajectory
def test_http01_issues_individual_not_wildcard(caplog, tmp_path) -> None:
    """Verify HTTP-01 issues individual domain cert without *.domain wildcard.

    ## @purpose  HTTP-01 via --standalone does NOT support wildcard certs.
    ##            The acme.sh call should have -d "domain" but NOT -d "*.domain".
    ## @scenario  _issue_http01_cert "test.example.com" "admin@test.com"
    ## → mock acme.sh called with --standalone -d "test.example.com"
    ## → no -d "*.test.example.com" in args
    ## @regression  HTTP-01 accidentally issues wildcard (LE rejects it)
    """
    acme_home = _create_mock_acme_for_issue_cert(tmp_path)
    env = {
        "ACME_HOME": acme_home,
        "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt"),
    }
    result = _source_and_run_issue_cert_no_main(
        '_issue_http01_cert "test.example.com" "admin@test.com"',
        env=env,
    )

    logger.critical("[IMP:9][test_http01_issues_individual_not_wildcard] ASSERT: individual cert only")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode == 0, f"HTTP-01 should succeed: {result.stderr}"
    assert "[MOCK-ACME-HTTP]" in result.stderr, f"Expected HTTP-01 mode:\n{result.stderr}"
    # Should NOT have *.domain (wildcard)
    assert "*.test.example.com" not in result.stderr, f"HTTP-01 should NOT issue wildcard cert:\n{result.stderr}"

    logger.critical("[IMP:9][test_http01_issues_individual_not_wildcard] PASS: HTTP-01 issues individual cert only")


# endregion HTTP01_FALLBACK_TESTS
