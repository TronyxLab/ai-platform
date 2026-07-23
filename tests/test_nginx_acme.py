# GREP_SUMMARY: test nginx acme acme.sh tls cron dns-webnames wildcard cert shred api-key contract source_and_run LDD IMP bash-n declare-F guard http01-fallback ACME_CHALLENGE_MODE
# STRUCTURE: SCRIPT_PATH -> test_acme_wildcard_cert_contract(bash-n + declare -F) -> test_acme_webnames_basename_contract(issue_tls_cert guard) -> test_acme_api_key_shredded_contract(issue_tls_cert guard) -> test_acme_cron_contract(_acme_install_cron with missing deps) -> test_issue_tls_cert_guards(guards:no-dns-plugin + no-api-key) -> test_post_issue_wildcard_san_contract(_verify_wildcard_san with missing cert) -> test_http01_fallback(ACME_CHALLENGE_MODE: dns|auto|http)
# region MODULE_CONTRACT
## @purpose  Contract tests for acme.sh TLS + cron operations in nginx/install.sh.
##           Replaces former AcmeSimulator-based simulation tests with REAL subprocess
##           invocations of nginx/install.sh shell functions via source_and_run().
##           Verifies: bash syntax, function definitions, guard clauses (DNS plugin required,
##           WEBNAMES_API_KEY required), cron error handling, wildcard SAN verification.
## @scope    subprocess calls to nginx/install.sh shell functions. No Docker, no acme.sh,
##           no network access required. Tests guard clauses and error paths that don't
##           require system-level dependencies (root, crontab, acme.sh binary).
## @invariants
##   - All test_* functions use @ldd_trajectory decorator
##   - Each test logs IMP:9 business logic assertion
##   - nginx/install.sh must pass bash -n syntax check
##   - All expected functions (_issue_acme_cert, issue_tls_cert, _acme_install_cron, _verify_wildcard_san) must be declared
##   - issue_tls_cert() guard: empty dns_plugin → return 1 (no HTTP-01 fallback) — SSL only
##   - issue_tls_cert() guard: webnames without WEBNAMES_API_KEY → return 1
##   - issue_tls_cert() ACME_CHALLENGE_MODE=http bypasses DNS-01 guard (HTTP-01 fallback)
##   - issue_tls_cert() ACME_CHALLENGE_MODE=auto: DNS-01 first, HTTP-01 fallback on failure
##   - _acme_install_cron() guard: missing acme.sh → return 1
##   - _verify_wildcard_san() guard: missing cert → return 1
##   - HTTP-01 fallback tests source issue-cert.sh (separate from nginx/install.sh)
## @changes  2026-07-23 | DevPlan 058 — Added HTTP-01 fallback tests (5 tests) + issue-cert.sh helpers
## @rationale  Replaces AcmeSimulator (Archived per Epic 2 T2.1 — simulation created false
##   coverage). Contract tests call REAL bash functions, validating shell syntax, function
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

# Path to the actual nginx/install.sh script
_SCRIPT_PATH: pathlib.Path = (
    pathlib.Path(__file__).resolve().parent.parent / "core" / "modules" / "nginx" / "install.sh"
)
_SCRIPT_DIR: str = str(_SCRIPT_PATH.parent)
_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
# Path to issue-cert.sh (separate from nginx/install.sh — tested independently)
_ISSUE_CERT_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "issue-cert.sh"
_ISSUE_CERT_DIR: str = str(_ISSUE_CERT_PATH.parent)


# region HELPERS


def _source_and_run_no_main(function_call: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Source nginx/install.sh (minus main "$@" tail call) and run function_call.

    ## @purpose  nginx/install.sh calls main "$@" at end of script. When non-root,
    ##            main() fails with "must run as root". This helper creates a temp
    ##            copy of install.sh with:
    ##            1. The dynamic SCRIPT_DIR replaced with the correct absolute path
    ##               (so ../../lib/logging.sh resolves correctly from temp location)
    ##            2. The trailing 'main "$@"' line removed
    ##            Then sources the temp copy and runs function_call.
    ## @io       ⇥ function_call: str — bash function call expression
    ##           ⇥ env: dict | None — optional env vars
    ##           ⎋ subprocess.CompletedProcess
    ## @complexity O(N) where N = lines in install.sh
    """
    content = _SCRIPT_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Remove trailing main "$@" call
    if lines and lines[-1].strip() == 'main "$@"':
        content_modified = "\n".join(lines[:-1])
    else:
        content_modified = content

    # Replace dynamic SCRIPT_DIR with absolute path so relative deps resolve
    # The dynamic line is: SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # We replace it with: SCRIPT_DIR="/absolute/path/to/core/modules/nginx"
    script_dir_line = 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"'
    content_modified = content_modified.replace(
        script_dir_line,
        f'SCRIPT_DIR="{_SCRIPT_DIR}"',
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


# endregion HELPERS1 (nginx/install.sh)


# region HELPERS2 (issue-cert.sh)


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
    # Only tag ISSUE calls with challenge mode markers.
    # --install-cert calls log [MOCK-ACME] but do NOT add challenge markers.
    lines = ["#!/bin/bash", 'echo "[MOCK-ACME] $@" >&2']
    if dns01_fail and http01_fail:
        # Both modes fail
        lines.append('echo "[MOCK-ACME-ALL-FAIL]" >&2')
        lines.append("exit 1")
    elif dns01_fail:
        # Only fail DNS-01 --issue calls; HTTP-01 works; --install-cert always succeeds
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


# endregion HELPERS2 (issue-cert.sh)


# region CONTRACT_TESTS


@pytest.mark.contract
@ldd_trajectory
def test_acme_wildcard_cert_contract(caplog) -> None:
    """Verify nginx/install.sh passes bash -n syntax check and declares expected functions.

    ## @purpose  Baseline contract test: validate shell script syntax and function inventory.
    ##            Replaces old AcmeSimulator-based wildcard cert issuance test.
    ## @scenario  bash -n install.sh → exit 0; source install.sh → declare -F for 4 functions
    ## @regression  Any syntax error or function rename/missing function
    """
    logger.info("[IMP:7][test_acme_wildcard_cert_contract] START: syntax + function inventory")

    # ── Phase 1: bash -n syntax check ──
    logger.info("[IMP:8][test_acme_wildcard_cert_contract] Running bash -n on %s", _SCRIPT_PATH)
    result = subprocess.run(
        ["bash", "-n", str(_SCRIPT_PATH)],
        capture_output=True,
        text=True,
    )
    logger.critical(
        "[IMP:9][test_acme_wildcard_cert_contract] ASSERT: bash -n exit code = %d",
        result.returncode,
    )
    assert result.returncode == 0, f"bash -n syntax check FAILED for {_SCRIPT_PATH}:\n{result.stderr}"
    logger.info("[IMP:8][test_acme_wildcard_cert_contract] bash -n PASS")

    # ── Phase 2: Function existence check via declare -F ──
    expected_functions = [
        "_issue_acme_cert",
        "issue_tls_cert",
        "_acme_install_cron",
        "_verify_wildcard_san",
    ]

    for func_name in expected_functions:
        result_declare = _source_and_run_no_main(f"declare -F {func_name}")
        logger.critical(
            "[IMP:9][test_acme_wildcard_cert_contract] ASSERT: declare -F %s exit = %d",
            func_name,
            result_declare.returncode,
        )
        assert result_declare.returncode == 0, (
            f"Function '{func_name}' not declared in {_SCRIPT_PATH.name}. stderr: {result_declare.stderr}"
        )
        logger.info("[IMP:8][test_acme_wildcard_cert_contract] Function '%s' declared ✓", func_name)

    logger.critical(
        "[IMP:9][test_acme_wildcard_cert_contract] PASS: syntax OK, all %d functions declared",
        len(expected_functions),
    )


@pytest.mark.contract
@ldd_trajectory
def test_acme_webnames_basename_contract(caplog) -> None:
    """Verify issue_tls_cert() returns non-zero when DNS plugin is empty (guard clause).

    ## @purpose  Contract test for the issue_tls_cert() guard: no DNS plugin → must fail.
    ##            This validates the first guard in issue_tls_cert() via real shell invocation.
    ## @scenario  source install.sh → issue_tls_cert "test.example.com" "admin@test.com" ""
    ##            → exit 1 (dns_plugin required guard)
    ## @regression  HTTP-01 fallback (D-1) — wildcard TLS requires DNS-01
    """

    result = _source_and_run_no_main(
        'issue_tls_cert "test.example.com" "admin@test.com" ""',
    )

    logger.critical(
        "[IMP:9][test_acme_webnames_basename_contract] ASSERT: exit code = %d (expected non-zero, dns_plugin guard)",
        result.returncode,
    )
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    # Guard clause returns 1 → set -e causes subprocess to exit 1
    assert result.returncode != 0, (
        "issue_tls_cert with empty dns_plugin must FAIL (DNS plugin required for wildcard). Expected non-zero exit."
    )

    # Verify the guard message in stderr
    assert "TLS certificate requires DNS plugin" in result.stderr, f"Expected guard message in stderr:\n{result.stderr}"

    logger.critical(
        "[IMP:9][test_acme_webnames_basename_contract] PASS: guard correctly rejected empty dns_plugin",
    )


@pytest.mark.contract
@ldd_trajectory
def test_acme_api_key_shredded_contract(caplog) -> None:
    """Verify issue_tls_cert() returns non-zero when WEBNAMES_API_KEY is missing for webnames.

    ## @purpose  Security invariant contract test (TRAP[BUSINESS] 2026-06-11): when
    ##            dns_plugin=webnames but WEBNAMES_API_KEY is not set, issue_tls_cert()
    ##            must fail before attempting cert issuance.
    ## @scenario  source install.sh → issue_tls_cert "test.example.com" "admin@test.com" "webnames"
    ##            → exit 1 (WEBNAMES_API_KEY required guard)
    ## @regression  API key not set — cert issuance without auth
    """

    result = _source_and_run_no_main(
        'issue_tls_cert "test.example.com" "admin@test.com" "webnames"',
    )

    logger.critical(
        "[IMP:9][test_acme_api_key_shredded_contract] ASSERT: exit code = %d (expected non-zero, API key guard)",
        result.returncode,
    )
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode != 0, (
        "issue_tls_cert with webnames but no WEBNAMES_API_KEY must FAIL. Expected non-zero exit."
    )

    assert "WEBNAMES_API_KEY not set" in result.stderr, (
        f"Expected 'WEBNAMES_API_KEY not set' in stderr:\n{result.stderr}"
    )

    logger.critical(
        "[IMP:9][test_acme_api_key_shredded_contract] PASS: guard correctly rejected missing WEBNAMES_API_KEY",
    )


@pytest.mark.contract
@ldd_trajectory
def test_acme_cron_contract(caplog) -> None:
    """Verify _acme_install_cron() returns non-zero when acme.sh is not installed.

    ## @purpose  Contract test for _acme_install_cron() guard: when ACME_HOME does not
    ##            contain an executable acme.sh, the function must return 1 gracefully.
    ## @scenario  source install.sh → _acme_install_cron → exit 1 (acme.sh not found)
    ## @regression  Cron installation with missing acme.sh dependency
    """

    result = _source_and_run_no_main(
        "_acme_install_cron",
        env={"ACME_HOME": "/tmp/nonexistent-acme-test"},
    )

    logger.critical(
        "[IMP:9][test_acme_cron_contract] ASSERT: exit code = %d (expected non-zero, acme.sh not found)",
        result.returncode,
    )
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode != 0, "_acme_install_cron with nonexistent ACME_HOME must FAIL. Expected non-zero exit."

    assert "acme.sh not installed" in result.stderr or "acme.sh not found" in result.stderr, (
        f"Expected acme.sh not found message in stderr:\n{result.stderr}"
    )

    logger.critical(
        "[IMP:9][test_acme_cron_contract] PASS: guard correctly rejected missing acme.sh",
    )


@pytest.mark.contract
@pytest.mark.parametrize(
    "scenario,domain,email,dns_plugin,expected_msg",
    [
        (
            "dns_plugin_required",
            "test.example.com",
            "admin@test.com",
            "",
            "TLS certificate requires DNS plugin",
        ),
        (
            "webnames_api_key_missing",
            "test.example.com",
            "admin@test.com",
            "webnames",
            "WEBNAMES_API_KEY not set",
        ),
    ],
    ids=["dns_plugin_required", "webnames_api_key_missing"],
)
@ldd_trajectory
def test_issue_tls_cert_guards(scenario, domain, email, dns_plugin, expected_msg, caplog) -> None:
    """Parametrized guard tests for issue_tls_cert(): empty dns_plugin and missing WEBNAMES_API_KEY.

    ## @purpose  Regression tests for the two guard clauses in issue_tls_cert():
    ##            D-1: DNS plugin required for wildcard TLS
    ##            D-2: WEBNAMES_API_KEY required for webnames plugin
    ## @scenario  source install.sh → issue_tls_cert(domain, email, dns_plugin)
    ##            → exit code non-zero; stderr contains expected_msg
    ## @regression  HTTP-01 fallback (D-1), API key pre-issue check (D-2)
    """

    func_call = f'issue_tls_cert "{domain}" "{email}" "{dns_plugin}"'
    result = _source_and_run_no_main(func_call)

    logger.critical(
        "[IMP:9][test_issue_tls_cert_guards][%s] ASSERT: exit=%d (expected non-zero), msg check",
        scenario,
        result.returncode,
    )
    print(f"--- STDERR [{scenario}] ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode != 0, f"[{scenario}] Expected non-zero exit, got {result.returncode}. {expected_msg}"

    assert expected_msg in result.stderr, f"[{scenario}] Expected '{expected_msg}' in stderr:\n{result.stderr}"

    logger.critical(
        "[IMP:9][test_issue_tls_cert_guards][%s] PASS: guard '%s' verified",
        scenario,
        expected_msg,
    )


@pytest.mark.contract
@ldd_trajectory
def test_post_issue_wildcard_san_contract(caplog) -> None:
    """Verify _verify_wildcard_san() returns non-zero when certificate file does not exist.

    ## @purpose  Contract test for _verify_wildcard_san() guard: when the certificate file
    ##            at /etc/letsencrypt/live/${domain}/fullchain.pem does not exist, the
    ##            function must return 1. This validates the precondition check.
    ## @scenario  source install.sh → _verify_wildcard_san "test.example.com"
    ##            → exit 1 (cert not found at hardcoded path)
    ## @regression  SAN verification without cert — should fail gracefully
    """

    result = _source_and_run_no_main(
        '_verify_wildcard_san "test.example.com"',
    )

    logger.critical(
        "[IMP:9][test_post_issue_wildcard_san_contract] ASSERT: exit code = %d (expected non-zero, cert not found)",
        result.returncode,
    )
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode != 0, "_verify_wildcard_san with nonexistent cert must FAIL. Expected non-zero exit."

    assert "No certificate found" in result.stderr, f"Expected 'No certificate found' in stderr:\n{result.stderr}"

    logger.critical(
        "[IMP:9][test_post_issue_wildcard_san_contract] PASS: guard correctly rejected missing cert",
    )


# endregion CONTRACT_TESTS


# region WILDCARD_PARAM_TESTS


def _create_mock_acme(tmp_path: pathlib.Path) -> str:
    """Create mock acme.sh in tmp_path, return ACME_HOME path.

    ## @purpose  Creates a mock acme.sh binary and supporting DNS API scripts
    ##            so _issue_acme_cert() can execute without real acme.sh.
    ##            The mock logs its args to stderr for test verification.
    ## @io       ⇥ tmp_path → ⎋ str (ACME_HOME path)
    """
    acme_home = tmp_path / "acme-mock"
    acme_home.mkdir(parents=True, exist_ok=True)

    # Main acme.sh binary — logs args to stderr
    acme_sh = acme_home / "acme.sh"
    acme_sh.write_text('#!/bin/bash\necho "[MOCK-ACME] $@" >&2\nexit 0\n')
    acme_sh.chmod(0o755)

    # dnsapi/ with dns_webnames.sh
    dnsapi = acme_home / "dnsapi"
    dnsapi.mkdir(exist_ok=True)
    dns_script = dnsapi / "dns_webnames.sh"
    dns_script.write_text('#!/bin/bash\necho "[MOCK-DNS] $@" >&2\nexit 0\n')
    dns_script.chmod(0o755)

    # dnsapi_ext/ with dns_webnames.sh (needs API_KEY placeholder)
    dnsapi_ext = acme_home / "dnsapi_ext"
    dnsapi_ext.mkdir(exist_ok=True)
    ext_script = dnsapi_ext / "dns_webnames.sh"
    ext_script.write_text('#!/bin/bash\nAPI_KEY=""\necho "[MOCK-DNS-EXT] $@" >&2\nexit 0\n')
    ext_script.chmod(0o755)

    return str(acme_home)


@pytest.mark.contract
@ldd_trajectory
def test_acme_cert_wildcard_param_true(caplog, tmp_path) -> None:
    """Verify _issue_acme_cert with wildcard=true includes -d '*.domain'.

    ## @purpose  When wildcard flag is explicitly true, the acme.sh call
    ##            must include -d "domain" AND -d "*.domain".
    """
    acme_home = _create_mock_acme(tmp_path)
    env = {
        "ACME_HOME": acme_home,
        "WEBNAMES_API_KEY": "*test-key-123",
        "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt"),
    }
    result = _source_and_run_no_main(
        '_issue_acme_cert "test.example.com" "admin@test.com" "webnames" "true"',
        env=env,
    )

    logger.critical("[IMP:9][test_acme_cert_wildcard_param_true] ASSERT: mock acme.sh received -d *.domain")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    # Mock acme.sh ran with correct args
    assert result.returncode == 0, f"Function failed: {result.stderr}"
    assert "[MOCK-ACME]" in result.stderr, "Mock acme.sh was not called"
    assert "*.test.example.com" in result.stderr, (
        f"Expected wildcard -d '*.test.example.com' in mock acme.sh args:\n{result.stderr}"
    )

    logger.critical("[IMP:9][test_acme_cert_wildcard_param_true] PASS: wildcard=true includes -d *.domain")


@pytest.mark.contract
@ldd_trajectory
def test_acme_cert_wildcard_param_false(caplog, tmp_path) -> None:
    """Verify _issue_acme_cert with wildcard=false does NOT include -d '*.domain'.

    ## @purpose  When wildcard flag is explicitly false, only -d "domain"
    ##            should be passed to acme.sh (single-domain cert).
    """
    acme_home = _create_mock_acme(tmp_path)
    env = {
        "ACME_HOME": acme_home,
        "WEBNAMES_API_KEY": "*test-key-123",
        "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt"),
    }
    result = _source_and_run_no_main(
        '_issue_acme_cert "test.example.com" "admin@test.com" "webnames" "false"',
        env=env,
    )

    logger.critical("[IMP:9][test_acme_cert_wildcard_param_false] ASSERT: mock acme.sh received -d without *.domain")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    # Mock acme.sh ran with correct args
    assert result.returncode == 0, f"Function failed: {result.stderr}"
    assert "[MOCK-ACME]" in result.stderr, "Mock acme.sh was not called"
    assert "-d" in result.stderr
    assert "*.test.example.com" not in result.stderr, f"wildcard=false should NOT include *.domain:\n{result.stderr}"

    logger.critical("[IMP:9][test_acme_cert_wildcard_param_false] PASS: wildcard=false omits -d *.domain")


@pytest.mark.contract
@ldd_trajectory
def test_acme_cert_wildcard_default(caplog, tmp_path) -> None:
    """Verify backward compatibility: _issue_acme_cert without 4th param defaults to wildcard=true.

    ## @purpose  Existing callers pass only 3 args. Default wildcard=true
    ##            ensures backward compatibility — wildcard cert is issued.
    """
    acme_home = _create_mock_acme(tmp_path)
    env = {
        "ACME_HOME": acme_home,
        "WEBNAMES_API_KEY": "*test-key-123",
        "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt"),
    }
    result = _source_and_run_no_main(
        '_issue_acme_cert "test.example.com" "admin@test.com" "webnames"',
        env=env,
    )

    logger.critical("[IMP:9][test_acme_cert_wildcard_default] ASSERT: mock acme.sh received -d *.domain (default)")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    # Mock acme.sh ran with correct args (default wildcard=true)
    assert result.returncode == 0, f"Function failed: {result.stderr}"
    assert "[MOCK-ACME]" in result.stderr, "Mock acme.sh was not called"
    assert "*.test.example.com" in result.stderr, f"Default wildcard should include -d '*.domain':\n{result.stderr}"

    logger.critical("[IMP:9][test_acme_cert_wildcard_default] PASS: backward compat — default wildcard=true")


@pytest.mark.contract
@ldd_trajectory
def test_issue_tls_cert_passes_wildcard(caplog, tmp_path) -> None:
    """Verify issue_tls_cert() passes wildcard parameter to _issue_acme_cert().

    ## @purpose  issue_tls_cert is the public API. It must forward the
    ##            4th wildcard arg to _issue_acme_cert. When wildcard=false,
    ##            the mock acme.sh should NOT receive -d *.domain.
    ## @scenario  issue_tls_cert "myapp.com" "admin@test.com" "webnames" "false"
    ## @regression  Wildcard arg not forwarded — single-domain cert request broken
    """
    acme_home = _create_mock_acme(tmp_path)
    env = {
        "ACME_HOME": acme_home,
        "WEBNAMES_API_KEY": "*test-key-123",
        "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt"),
    }
    result = _source_and_run_no_main(
        'issue_tls_cert "myapp.com" "admin@test.com" "webnames" "false"',
        env=env,
    )

    logger.critical("[IMP:9][test_issue_tls_cert_passes_wildcard] ASSERT: mock acme.sh received -d without *.myapp.com")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    # Mock acme.sh ran with correct args (wildcard=false forwarded correctly)
    assert result.returncode == 0, f"Function failed: {result.stderr}"
    assert "[MOCK-ACME]" in result.stderr, "Mock acme.sh was not called"
    assert "-d" in result.stderr
    assert "*.myapp.com" not in result.stderr, (
        f"issue_tls_cert with wildcard=false should NOT include *.domain:\n{result.stderr}"
    )

    logger.critical("[IMP:9][test_issue_tls_cert_passes_wildcard] PASS: wildcard forwarded correctly")


# endregion WILDCARD_PARAM_TESTS


# region IS_SUBDOMAIN_TESTS


@pytest.mark.contract
@ldd_trajectory
def test_is_subdomain_true(caplog) -> None:
    """Verify _is_subdomain returns 0 when domain IS a subdomain of parent.

    ## @scenario  app.tronyx.ru ⊂ tronyx.ru → exit 0
    """
    result = _source_and_run_no_main('_is_subdomain "app.tronyx.ru" "tronyx.ru"')

    logger.critical("[IMP:9][test_is_subdomain_true] ASSERT: returncode=0 (subdomain detected)")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode == 0, "app.tronyx.ru should be a subdomain of tronyx.ru"

    logger.critical("[IMP:9][test_is_subdomain_true] PASS: subdomain detected correctly")


@pytest.mark.contract
@ldd_trajectory
def test_is_subdomain_false(caplog) -> None:
    """Verify _is_subdomain returns 1 when domain is NOT a subdomain of parent.

    ## @scenario  myapp.com ⊄ tronyx.ru → exit 1
    """
    result = _source_and_run_no_main('_is_subdomain "myapp.com" "tronyx.ru"')

    logger.critical("[IMP:9][test_is_subdomain_false] ASSERT: returncode=1 (not a subdomain)")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode == 1, "myapp.com should NOT be a subdomain of tronyx.ru"

    logger.critical("[IMP:9][test_is_subdomain_false] PASS: non-subdomain rejected")


@pytest.mark.contract
@ldd_trajectory
def test_is_subdomain_exact_match(caplog) -> None:
    """Verify _is_subdomain returns 1 for exact match (not a subdomain).

    ## @scenario  tronyx.ru vs tronyx.ru → exit 1
    """
    result = _source_and_run_no_main('_is_subdomain "tronyx.ru" "tronyx.ru"')

    logger.critical("[IMP:9][test_is_subdomain_exact_match] ASSERT: returncode=1 (exact match is not subdomain)")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode == 1, "Exact match should NOT be considered a subdomain"

    logger.critical("[IMP:9][test_is_subdomain_exact_match] PASS: exact match correctly rejected")


@pytest.mark.contract
@ldd_trajectory
def test_is_subdomain_edge_similar_tld(caplog) -> None:
    """Verify _is_subdomain correctly handles similar but different domains.

    ## @scenario  fake-tronyx.ru vs tronyx.ru → exit 1 (different domain entirely)
    """
    result = _source_and_run_no_main('_is_subdomain "fake-tronyx.ru" "tronyx.ru"')

    logger.critical("[IMP:9][test_is_subdomain_edge_similar_tld] ASSERT: returncode=1 (similar TLD is not subdomain)")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode == 1, "fake-tronyx.ru should NOT be a subdomain of tronyx.ru"

    logger.critical("[IMP:9][test_is_subdomain_edge_similar_tld] PASS: similar-name rejection correct")


@pytest.mark.contract
@ldd_trajectory
def test_is_subdomain_empty_args(caplog) -> None:
    """Verify _is_subdomain returns 1 when args are empty."""
    result = _source_and_run_no_main('_is_subdomain "" "tronyx.ru"')

    logger.critical("[IMP:9][test_is_subdomain_empty_args] ASSERT: returncode=1 (empty domain)")
    print("--- STDERR ---")
    print(result.stderr)

    assert result.returncode == 1, "Empty domain should return 1"

    logger.critical("[IMP:9][test_is_subdomain_empty_args] PASS: empty domain handled")


# endregion IS_SUBDOMAIN_TESTS


# region PROJECT_CERTS_TESTS


@pytest.mark.contract
@ldd_trajectory
def test_project_certs_skips_subdomain(caplog, tmp_path) -> None:
    """Verify _issue_project_certs() skips subdomains of PLATFORM_DOMAIN.

    ## @scenario  PLATFORM_PROJECT_DOMAINS="app.test.local",
    ##            PLATFORM_DOMAIN="test.local" → skip, no cert issued.
    ## @regression  Subdomains should NOT get separate certs (covered by wildcard)
    """
    acme_home = _create_mock_acme(tmp_path)
    env = {
        "ACME_HOME": acme_home,
        "WEBNAMES_API_KEY": "*test-key-123",
        "PLATFORM_PROJECT_DOMAINS": "app.test.local",
    }
    result = _source_and_run_no_main(
        '_issue_project_certs "test.local" "admin@test.com" "webnames"',
        env=env,
    )

    logger.critical("[IMP:9][test_project_certs_skips_subdomain] ASSERT: skip subdomain, no ACME call")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode == 0, f"Function failed: {result.stderr}"
    assert "SKIP" in result.stderr
    # The mock acme.sh should NOT have been called (no [MOCK-ACME] in stderr)
    mock_lines = [line for line in result.stderr.splitlines() if "[MOCK-ACME]" in line]
    assert len(mock_lines) == 0, f"Mock acme.sh was called but should have been skipped:\n{result.stderr}"

    logger.critical("[IMP:9][test_project_certs_skips_subdomain] PASS: subdomain correctly skipped")


@pytest.mark.contract
@ldd_trajectory
def test_project_certs_issues_independent(caplog, tmp_path) -> None:
    """Verify _issue_project_certs() issues wildcard cert for independent domain.

    ## @scenario  PLATFORM_PROJECT_DOMAINS="myapp.com",
    ##            PLATFORM_DOMAIN="test.local" → calls issue_tls_cert with wildcard=true
    ## @regression  Independent domains should get wildcard certs (*.domain)
    ##              to support arbitrary subdomains (www, api, etc.).
    ##              Subdomains of platform domain are skipped (covered by platform wildcard).
    """
    acme_home = _create_mock_acme(tmp_path)
    env = {
        "ACME_HOME": acme_home,
        "WEBNAMES_API_KEY": "*test-key-123",
        "PLATFORM_PROJECT_DOMAINS": "myapp.com",
    }
    result = _source_and_run_no_main(
        '_issue_project_certs "test.local" "admin@test.com" "webnames"',
        env=env,
    )

    logger.critical("[IMP:9][test_project_certs_issues_independent] ASSERT: independent domain issued with wildcard")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode == 0, f"Function failed: {result.stderr}"
    # Mock acme.sh should have been called with -d "myapp.com" AND -d "*.myapp.com"
    assert "-d" in result.stderr
    assert "*.myapp.com" in result.stderr, f"Independent domain SHOULD get wildcard cert:\n{result.stderr}"

    logger.critical("[IMP:9][test_project_certs_issues_independent] PASS: independent domain cert issued with wildcard")


@pytest.mark.contract
@ldd_trajectory
def test_project_certs_no_domains(caplog) -> None:
    """Verify _issue_project_certs() skips when PLATFORM_PROJECT_DOMAINS is empty."""
    result = _source_and_run_no_main(
        '_issue_project_certs "test.local" "admin@test.com" "webnames"',
        env={"PLATFORM_PROJECT_DOMAINS": ""},
    )

    logger.critical("[IMP:9][test_project_certs_no_domains] ASSERT: skip when no project domains")
    print("--- STDERR ---")
    print(result.stderr)

    assert result.returncode == 0
    assert "No PLATFORM_PROJECT_DOMAINS" in result.stderr

    logger.critical("[IMP:9][test_project_certs_no_domains] PASS: empty domains handled")


# endregion PROJECT_CERTS_TESTS


# region PLATFORM_EMAIL_GUARD_TEST


@pytest.mark.contract
@ldd_trajectory
def test_platform_email_guard_contract(caplog) -> None:
    """Verify main() exits 1 when PLATFORM_EMAIL is empty.

    ## @purpose  PLATFORM_EMAIL guard prevents Let's Encrypt registration without contact email.
    ## @scenario  source install.sh → main (with empty PLATFORM_EMAIL) → exit 1
    ## @regression  Bootstrap fails silently without email — no cert issued
    ## @note  main() checks $(id -u) == 0 first. We mock id() as bash function
    ##        to bypass the root check and test the PLATFORM_EMAIL guard.
    """
    result = _source_and_run_no_main(
        "id() { echo 0; }; main",
        env={"PLATFORM_DOMAIN": "test.example.com", "PLATFORM_EMAIL": ""},
    )

    logger.critical("[IMP:9][test_platform_email_guard_contract] ASSERT: exit != 0 for empty PLATFORM_EMAIL")
    print("--- STDERR ---")
    print(result.stderr)
    print("--- END STDERR ---")

    assert result.returncode != 0, "main() with empty PLATFORM_EMAIL must FAIL"
    assert "PLATFORM_EMAIL not set" in result.stderr, (
        f"Expected 'PLATFORM_EMAIL not set' error message:\n{result.stderr}"
    )

    logger.critical("[IMP:9][test_platform_email_guard_contract] PASS: guard blocks empty email")


# endregion PLATFORM_EMAIL_GUARD_TEST


# region HTTP01_FALLBACK_TESTS
## @purpose  Contract tests for HTTP-01 fallback in issue-cert.sh.
##            Tests ACME_CHALLENGE_MODE env var behavior, _issue_http01_cert() function,
##            and DNS-01 → HTTP-01 graceful degradation.
## @scope    Sources issue-cert.sh (not nginx/install.sh) via _source_and_run_issue_cert_no_main.
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
    # Should have -d "test.example.com"
    assert "-d" in result.stderr, "Expected -d domain flag"
    assert "-d" in result.stderr
    # Should NOT have *.domain (wildcard)
    assert "*.test.example.com" not in result.stderr, f"HTTP-01 should NOT issue wildcard cert:\n{result.stderr}"

    logger.critical("[IMP:9][test_http01_issues_individual_not_wildcard] PASS: HTTP-01 issues individual cert only")


# endregion HTTP01_FALLBACK_TESTS
