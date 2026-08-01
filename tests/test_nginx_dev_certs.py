# GREP_SUMMARY: test-nginx-dev-certs contract dev-certs cert-generation openssl san-idempotency regeneration
# STRUCTURE: ┌tmp_path per test┐ → ◇ test_generate_certs_openssl_backend(◇ CERT_BACKEND=openssl → SAN check) → ◇ test_context_domain_in_san(◇ PLATFORM_DOMAIN=demo-ctx.local → *.demo-ctx.local) → ◇ test_second_run_is_noop(◇ same SAN → no-op, mtime unchanged) → ◇ test_regenerates_on_san_drift(◇ missing SAN entry → regenerates)
# region MODULE_CONTRACT
## @purpose  Static contract tests for core/modules/nginx/dev_cert_generator.py (DevPlan 099,
##           migrated from the legacy dev-certs shell). Validate idempotency, SAN generation,
##           context domain inclusion, SAN drift detection.
## @scope    Pure subprocess tests — no Docker required. All tests use tmp_path + env overrides.
## @invariants
##   - Zero Hardcode: DEV_CERTS_DIR always via tmp_path
##   - CERT_BACKEND=openssl for all tests (CI-compatible, no mkcert dependency)
##   - LDD telemetry: caplog, IMP:7-10 filtering, at least one IMP:9 per test
##   - Detached from filesystem — no hardcoded paths (tmp_path)
## @rationale  DevPlan 012 TASK-5 — static contract ensures module works on CI without mkcert.
##             DevPlan 099 — contract migrated from bash facade to python3 module (streams
##             merged stdout+stderr because the Python module writes LDD logs to stderr).
## @see DevPlan 012 — §TEST_SPEC · DevPlan 099 — §11 (print-to-stderr rationale)
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# Path to the module under test (DevPlan 099: dev_cert_generator.py replaces the shell facade)
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "core" / "modules" / "nginx" / "dev_cert_generator.py"

# Required SAN entries for base domain
_BASE_SAN = {"DNS:*.ai-platform.local", "DNS:localhost", "IP:127.0.0.1"}


# region HELPER
def _run_script(
    tmp_path: Path,
    *,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run dev_cert_generator.py with DEV_CERTS_DIR pointing to tmp_path.

    ## @purpose — Isolated module invocation for contract tests.
    ## @io — ⇥ tmp_path, env_overrides → ⚡ subprocess.run → ⎋ CompletedProcess
    ## @complexity — O(1)
    """
    env = {
        **os.environ,
        "DEV_CERTS_DIR": str(tmp_path),
        "CERT_BACKEND": "openssl",
        "PLATFORM_DOMAIN": "ai-platform.local",
    }
    if env_overrides:
        env.update(env_overrides)

    # DevPlan 099: python3 invocation. Module writes LDD logs to stderr (print-to-stderr
    # contract) — tests merge stdout+stderr for marker assertions (facade-era 2>&1 parity).
    return subprocess.run(
        ["python3", str(_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _combined_output(result: subprocess.CompletedProcess) -> str:
    """Return stdout+stderr merged (Python module writes LDD logs to stderr).

    ## @purpose — DevPlan 099: dev_cert_generator.py logs via print(file=sys.stderr).
    ##            The old bash facade merged streams (exec python3 2>&1) so stdout-only
    ##            assertions worked. Direct python3 invocation requires merged reads.
    ## @io — ⇥ result: CompletedProcess → ⎋ str (stdout + stderr)
    ## @complexity — O(1)
    """
    return (result.stdout or "") + (result.stderr or "")


def _get_cert_sans(cert_file: Path) -> set[str]:
    """Extract literal SAN entries from a PEM certificate file.

    ## @purpose — Parse openssl x509 subjectAltName output into a set.
    ## @io — ⇥ cert_file → ⎋ set of "DNS:*" and "IP:*" strings
    ## @complexity — O(N) where N = SAN entries
    """
    result = subprocess.run(
        ["openssl", "x509", "-in", str(cert_file), "-noout", "-ext", "subjectAltName"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return set()

    sans: set[str] = set()
    for line in result.stdout.split("\n"):
        entries = line.strip()
        # Match DNS:xxx and IP:xxx / IP Address:xxx patterns
        # openssl x509 -ext subjectAltName outputs "IP Address:" not "IP:"
        for entry in entries.split(","):
            entry = entry.strip()
            if entry.startswith("DNS:"):
                sans.add(entry)
            elif entry.startswith("IP Address:"):
                sans.add("IP:" + entry[len("IP Address:") :])
            elif entry.startswith("IP:"):
                sans.add(entry)
    return sans


def _assert_ldd_trajectory(caplog) -> None:
    """Assert that at least one IMP:9 log is present in caplog records.

    ## @purpose — LDD telemetry verification: prints IMP:7-10 trajectory and asserts IMP:9 presence.
    ## @io — ⇥ caplog (pytest fixture) → ⎋ None (asserts found_log)
    ## @complexity — O(N) where N = caplog.records
    """
    found_log = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_log = True
    print("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# endregion HELPER


# region TESTS


# region FUNC_test_generate_certs_openssl_backend
@pytest.mark.contract
def test_generate_certs_openssl_backend(tmp_path: Path, caplog) -> None:
    """CERT_BACKEND=openssl DEV_CERTS_DIR=<tmp_path> → cert+key created, SAN matches required set.

    ## @purpose — Baseline: openssl backend produces valid cert + key with correct SAN.
    ## @io — ⇥ tmp_path → ⚡ _run_script → ⎋ assert cert exists, SAN = {*.ai-platform.local, localhost, 127.0.0.1}
    ## @complexity — O(1)
    """
    caplog.set_level(logging.INFO)

    logger.info("[IMP:7][test_generate_certs_openssl_backend] Starting")
    result = _run_script(tmp_path)
    logger.info("[IMP:8][test_generate_certs_openssl_backend] Script exited %d", result.returncode)
    for line in _combined_output(result).strip().split("\n"):
        if line.strip():
            logger.info("[IMP:8][test_generate_certs_openssl_backend] %s", line.strip())

    assert result.returncode == 0, f"Script failed: {result.stderr}"

    cert_file = tmp_path / "fullchain.pem"
    key_file = tmp_path / "privkey.pem"
    assert cert_file.exists(), f"Cert file not created: {cert_file}"
    assert key_file.exists(), f"Key file not created: {key_file}"

    sans = _get_cert_sans(cert_file)
    logger.info("[IMP:8][test_generate_certs_openssl_backend] SAN entries: %s", sorted(sans))

    for required in _BASE_SAN:
        assert required in sans, f"Missing required SAN: {required}. Got: {sans}"

    logger.info("[IMP:9][test_generate_certs_openssl_backend] ✅ Cert created with all required SAN entries")
    _assert_ldd_trajectory(caplog)


# endregion FUNC_test_generate_certs_openssl_backend


# region FUNC_test_context_domain_in_san
@pytest.mark.contract
def test_context_domain_in_san(tmp_path: Path, caplog) -> None:
    """PLATFORM_DOMAIN=demo-ctx.local → SAN contains *.demo-ctx.local in addition to base SAN.

    ## @purpose — Context domain inclusion: non-default PLATFORM_DOMAIN adds wildcard SAN.
    ## @io — ⇥ tmp_path → ⚡ _run_script(PLATFORM_DOMAIN=demo-ctx.local) → ⎋ assert *.demo-ctx.local ∈ SAN
    ## @complexity — O(1)
    """
    caplog.set_level(logging.INFO)

    logger.info("[IMP:7][test_context_domain_in_san] Starting")
    result = _run_script(tmp_path, env_overrides={"PLATFORM_DOMAIN": "demo-ctx.local"})
    logger.info("[IMP:8][test_context_domain_in_san] Script exited %d", result.returncode)
    for line in _combined_output(result).strip().split("\n"):
        if line.strip():
            logger.info("[IMP:8][test_context_domain_in_san] %s", line.strip())

    assert result.returncode == 0, f"Script failed: {result.stderr}"

    cert_file = tmp_path / "fullchain.pem"
    sans = _get_cert_sans(cert_file)

    # Base SAN must still be present
    for required in _BASE_SAN:
        assert required in sans, f"Missing base SAN: {required}. Got: {sans}"

    # Context domain wildcard must be present
    assert "DNS:*.demo-ctx.local" in sans, f"Missing context domain SAN: DNS:*.demo-ctx.local. Got: {sans}"

    logger.info("[IMP:9][test_context_domain_in_san] ✅ SAN includes base + context domain *.demo-ctx.local")
    _assert_ldd_trajectory(caplog)


# endregion FUNC_test_context_domain_in_san


# region FUNC_test_second_run_is_noop
@pytest.mark.contract
def test_second_run_is_noop(tmp_path: Path, caplog) -> None:
    """Second run with valid cert → exit 0, files not overwritten (mtime unchanged).

    ## @purpose — Idempotency: running script again on a current cert is no-op (F5).
    ## @io — ⇥ tmp_path → ⚡ _run_script twice → ⎋ assert mtime unchanged, no-regeneration log
    ## @complexity — O(1)
    """
    caplog.set_level(logging.INFO)

    logger.info("[IMP:7][test_second_run_is_noop] Starting — first run")
    result1 = _run_script(tmp_path)
    assert result1.returncode == 0, f"First run failed: {result1.stderr}"

    cert_file = tmp_path / "fullchain.pem"
    key_file = tmp_path / "privkey.pem"
    mtime_cert_1 = cert_file.stat().st_mtime
    mtime_key_1 = key_file.stat().st_mtime

    logger.info("[IMP:8][test_second_run_is_noop] First run mtime: cert=%s, key=%s", mtime_cert_1, mtime_key_1)

    # Second run — should be no-op
    logger.info("[IMP:7][test_second_run_is_noop] Starting — second run (should be no-op)")
    result2 = _run_script(tmp_path)
    assert result2.returncode == 0, f"Second run failed: {result2.stderr}"

    mtime_cert_2 = cert_file.stat().st_mtime
    mtime_key_2 = key_file.stat().st_mtime

    # mtime must be identical (no overwrite)
    assert mtime_cert_1 == mtime_cert_2, f"Cert file was overwritten! mtime changed: {mtime_cert_1} → {mtime_cert_2}"
    assert mtime_key_1 == mtime_key_2, f"Key file was overwritten! mtime changed: {mtime_key_1} → {mtime_key_2}"

    # Output should indicate no-op (markers live in stderr for the python3 module)
    noop_output = _combined_output(result2)
    assert "up-to-date" in noop_output.lower() or "no action" in noop_output.lower(), (
        f"Second run output does not indicate no-op: {noop_output}"
    )

    logger.info("[IMP:9][test_second_run_is_noop] ✅ Second run is no-op (mtime unchanged, up-to-date)")
    _assert_ldd_trajectory(caplog)


# endregion FUNC_test_second_run_is_noop


# region FUNC_test_regenerates_on_san_drift
@pytest.mark.contract
def test_regenerates_on_san_drift(tmp_path: Path, caplog) -> None:
    """Cert without *.demo-ctx.local + PLATFORM_DOMAIN=demo-ctx.local → script detects drift and regenerates.

    ## @purpose — SAN drift detection (F5 primary trigger): missing required SAN entry causes regeneration.
    ## @io — ⇥ tmp_path → ⚡ generate baseline cert → ⚡ run with PLATFORM_DOMAIN=demo-ctx.local → ⎋ assert regenerated
    ## @complexity — O(1)
    """
    caplog.set_level(logging.INFO)

    logger.info("[IMP:7][test_regenerates_on_san_drift] Starting — generate baseline cert")
    # First: generate cert with default domain (ai-platform.local)
    result1 = _run_script(tmp_path)
    assert result1.returncode == 0, f"Baseline generation failed: {result1.stderr}"

    cert_file = tmp_path / "fullchain.pem"
    key_file = tmp_path / "privkey.pem"
    mtime_cert_1 = cert_file.stat().st_mtime
    mtime_key_1 = key_file.stat().st_mtime

    # Verify baseline does NOT have context domain
    baseline_sans = _get_cert_sans(cert_file)
    assert "DNS:*.demo-ctx.local" not in baseline_sans, f"Baseline should not contain *.demo-ctx.local: {baseline_sans}"
    logger.info("[IMP:8][test_regenerates_on_san_drift] Baseline SAN (no context): %s", sorted(baseline_sans))

    # Second: run with PLATFORM_DOMAIN=demo-ctx.local — should detect drift and regenerate
    logger.info("[IMP:7][test_regenerates_on_san_drift] Running with PLATFORM_DOMAIN=demo-ctx.local")
    result2 = _run_script(tmp_path, env_overrides={"PLATFORM_DOMAIN": "demo-ctx.local"})
    assert result2.returncode == 0, f"Regeneration failed: {result2.stderr}"

    mtime_cert_2 = cert_file.stat().st_mtime
    mtime_key_2 = key_file.stat().st_mtime

    # Files must be regenerated (mtime changes)
    assert mtime_cert_2 > mtime_cert_1, f"Cert was not regenerated! mtime unchanged: {mtime_cert_1}"
    assert mtime_key_2 > mtime_key_1, f"Key was not regenerated! mtime unchanged: {mtime_key_1}"

    # Verify new cert contains context domain SAN
    new_sans = _get_cert_sans(cert_file)
    assert "DNS:*.demo-ctx.local" in new_sans, f"Regenerated cert missing context domain SAN: {new_sans}"
    # Base SAN should still be present
    for required in _BASE_SAN:
        assert required in new_sans, f"Regenerated cert missing base SAN: {required}. Got: {new_sans}"

    # Output should indicate SAN drift (markers live in stderr for the python3 module)
    drift_output = _combined_output(result2)
    assert "SAN drift" in drift_output.lower() or "missing" in drift_output.lower(), (
        f"Regeneration output does not indicate drift detection: {drift_output}"
    )

    logger.info("[IMP:9][test_regenerates_on_san_drift] ✅ SAN drift detected and cert regenerated")
    _assert_ldd_trajectory(caplog)


# endregion FUNC_test_regenerates_on_san_drift

# endregion TESTS
