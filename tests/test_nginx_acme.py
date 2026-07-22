# GREP_SUMMARY: test nginx acme acme.sh tls cron dns-webnames wildcard cert shred api-key contract source_and_run LDD IMP bash-n declare-F guard
# STRUCTURE: SCRIPT_PATH -> test_acme_wildcard_cert_contract(bash-n + declare -F) -> test_acme_webnames_basename_contract(issue_tls_cert guard) -> test_acme_api_key_shredded_contract(issue_tls_cert guard) -> test_acme_cron_contract(_acme_install_cron with missing deps) -> test_issue_tls_cert_guards(guards:no-dns-plugin + no-api-key) -> test_post_issue_wildcard_san_contract(_verify_wildcard_san with missing cert)
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
##   - issue_tls_cert() guard: empty dns_plugin → return 1 (no HTTP-01 fallback)
##   - issue_tls_cert() guard: webnames without WEBNAMES_API_KEY → return 1
##   - _acme_install_cron() guard: missing acme.sh → return 1
##   - _verify_wildcard_san() guard: missing cert → return 1
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


# endregion HELPERS


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
    env = {"ACME_HOME": acme_home, "WEBNAMES_API_KEY": "test-key-123", "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt")}
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
    env = {"ACME_HOME": acme_home, "WEBNAMES_API_KEY": "test-key-123", "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt")}
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
    env = {"ACME_HOME": acme_home, "WEBNAMES_API_KEY": "test-key-123", "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt")}
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
    env = {"ACME_HOME": acme_home, "WEBNAMES_API_KEY": "test-key-123", "LETSENCRYPT_DIR": str(tmp_path / "letsencrypt")}
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
        "WEBNAMES_API_KEY": "test-key-123",
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
        "WEBNAMES_API_KEY": "test-key-123",
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
