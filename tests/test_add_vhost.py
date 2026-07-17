"""Tests for add-vhost.sh — FQDN check delegation to validate.sh --check-fqdn."""
# GREP_SUMMARY: test add-vhost FQDN delegation validate.sh check-fqdn TRAP-6 nginx vhost
# STRUCTURE: [tmp_copy] ▶ copy add-vhost.sh → ┌mock validate.sh┐ → ╪ create ai-platform.yaml → ╪ source_and_run main() → ◇ stderr contains "Called with: --check-fqdn" → ⎋ assert_ldd_stderr
# region MODULE_CONTRACT
## @purpose  Unit test for add-vhost.sh — verify that FQDN uniqueness check delegates to
##           validate.sh --check-fqdn (TRAP-6 from DevPlan) instead of a local grep-based
##           check_fqdn_unique() function.
## @scope    Uses shell subprocess via source_and_run with a temp copy of the script
##           and a mock validate.sh to intercept the delegation call.
## @invariants
##   - SCRIPT_PATH must point to core/internal/scaffold/add-vhost.sh
##   - subprocess.run is used ONLY for shell script sourcing (exempt from business-logic rule)
##   - LDD trajectory printed from stderr (caplog cannot capture subprocess output)
##   - A mock validate.sh is placed next to the temp script copy to intercept the delegation
## @rationale
##   TRAP-6 replaced fragile grep-based check_fqdn_unique() with validate.sh --check-fqdn.
##   This test ensures the delegation is in place and correctly routes the project directory arg.
## @changes  2026-07-07 | Created per DevPlan $TEST_SPEC
## @usecases AC-MIG-6 (TRAP-6 handled), AC-MIG-7 (tests pass)
# endregion MODULE_CONTRACT

import shutil
from pathlib import Path

from conftest import assert_ldd_stderr, source_and_run

# ─── Constants ───────────────────────────────────────────────────────
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "core" / "internal" / "scaffold" / "add-vhost.sh"
PLATFORM_ROOT = Path(__file__).resolve().parent.parent  # /Users/.../ai-platform


# ─── Helpers ─────────────────────────────────────────────────────────

# source_and_run and assert_ldd_stderr are in conftest.py (SHARED_TEST_UTILITIES)

# ═══════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════


# region FUNC_test_add_vhost_cert_path
## @purpose  Verify generated vhost uses Let's Encrypt cert paths instead of self-signed default.crt.
##           Subdomains of PLATFORM_DOMAIN should use the platform wildcard cert;
##           independent domains should use their own cert.
## @regression  TRAP-6: vhost generated with /etc/nginx/ssl/default.crt → all project TLS broken
def test_add_vhost_cert_path(tmp_path: Path) -> None:
    """Verify add-vhost.sh generates LE cert paths (not self-signed default.crt)."""
    # ── Arrange ──

    # 1. Copy add-vhost.sh to temp
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    # 2. Create mock validate.sh
    mock_validate = tmp_path / "validate.sh"
    mock_validate.write_text("""#!/bin/bash
echo "[IMP:9][validate][mock] Called with: $@" >&2
exit 0
""")
    mock_validate.chmod(0o755)

    # 3. Create project directory with ai-platform.yaml
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("""expose: true
domain: app.test.local
target_node: mynode
""")

    # 4. Create node-configs directory (flat layout — no conf.d/ subdir)
    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    # 5. Set PLATFORM_ROOT + PLATFORM_DOMAIN (app.test.local is subdomain of test.local)
    env = {
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
        "PLATFORM_DOMAIN": "test.local",
    }

    # ── Act ──
    result = source_and_run(
        f'main "--project-dir" "{project_dir}" "--node-configs-dir" "{node_configs_dir}"',
        env=env,
        script_path=str(script_copy),
    )

    # ── Assert ──
    assert result.returncode == 0, f"main() failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    # Read generated vhost (flat layout per DRIFT-1 fix)
    vhost_file = node_configs_dir / "mynode" / "overlays" / "nginx" / "app.test.local.conf"
    assert vhost_file.is_file(), f"Vhost file not generated: {vhost_file}"
    vhost_content = vhost_file.read_text()

    print("--- VHOST CONTENT ---")
    print(vhost_content)
    print("--- END VHOST ---")

    # Assert NOT using default.crt
    assert "default.crt" not in vhost_content, (
        f"Vhost should NOT use self-signed default.crt — should use LE paths:\n{vhost_content}"
    )
    assert "default.key" not in vhost_content, (
        f"Vhost should NOT use self-signed default.key — should use LE paths:\n{vhost_content}"
    )

    # Assert using LE path for platform domain (subdomain → wildcard cert)
    assert "letsencrypt/live/test.local/fullchain.pem" in vhost_content, (
        f"Subdomain app.test.local should use platform wildcard cert (test.local):\n{vhost_content}"
    )
    assert "letsencrypt/live/test.local/privkey.pem" in vhost_content, (
        f"Subdomain should use test.local key:\n{vhost_content}"
    )

    # Assert NOT using app.test.local cert path (should NOT point to its own cert)
    assert "letsencrypt/live/app.test.local/fullchain.pem" not in vhost_content, (
        f"Subdomain vhost should reference PLATFORM_DOMAIN cert, not its own:\n{vhost_content}"
    )

    # LDD telemetry
    assert_ldd_stderr(result)


# endregion FUNC_test_add_vhost_cert_path


# region FUNC_test_add_vhost_cert_path_independent
## @purpose  Verify an independent project domain (NOT subdomain of PLATFORM_DOMAIN)
##           uses its own LE cert path, not the platform wildcard cert.
def test_add_vhost_cert_path_independent(tmp_path: Path) -> None:
    """Verify independent domain uses its own LE cert path in generated vhost."""
    # ── Arrange ──
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    mock_validate = tmp_path / "validate.sh"
    mock_validate.write_text("""#!/bin/bash
echo "[IMP:9][validate][mock] Called with: $@" >&2
exit 0
""")
    mock_validate.chmod(0o755)

    # Independent domain (myapp.com is NOT a subdomain of test.local)
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("""expose: true
domain: myapp.com
target_node: mynode
""")

    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    env = {
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
        "PLATFORM_DOMAIN": "test.local",
    }

    # ── Act ──
    result = source_and_run(
        f'main "--project-dir" "{project_dir}" "--node-configs-dir" "{node_configs_dir}"',
        env=env,
        script_path=str(script_copy),
    )

    # ── Assert ──
    assert result.returncode == 0, f"main() failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_file = node_configs_dir / "mynode" / "overlays" / "nginx" / "myapp.com.conf"
    assert vhost_file.is_file(), f"Vhost file not generated: {vhost_file}"
    vhost_content = vhost_file.read_text()

    print("--- VHOST CONTENT (independent) ---")
    print(vhost_content)
    print("--- END VHOST ---")

    # Assert NOT using default.crt
    assert "default.crt" not in vhost_content, "Independent vhost should not use self-signed cert"
    assert "default.key" not in vhost_content

    # Independent domain uses its own cert (not platform wildcard)
    assert "letsencrypt/live/myapp.com/fullchain.pem" in vhost_content, (
        f"Independent domain should use its own cert path:\n{vhost_content}"
    )
    assert "letsencrypt/live/myapp.com/privkey.pem" in vhost_content

    # Assert NOT using platform domain cert
    assert "letsencrypt/live/test.local/fullchain.pem" not in vhost_content, (
        f"Independent domain should NOT reference platform cert:\n{vhost_content}"
    )

    assert_ldd_stderr(result)


# endregion FUNC_test_add_vhost_cert_path_independent


# region FUNC_test_fqdn_check_delegates_to_validate
## @purpose  Verify that FQDN checking delegates to validate.sh --check-fqdn (TRAP-6).
##           The test:
##           1. Copies add-vhost.sh to a temp directory with a mock validate.sh alongside
##           2. Creates a minimal ai-platform.yaml with expose:true + domain
##           3. Runs main() via source_and_run
##           4. Asserts that validate.sh --check-fqdn was called by checking mock's stderr
## @io       ⇥ tmp_path fixture → ⎛ assertions on stderr + return code
## @complexity O(1)


# 🧪 TRAP[TEST] · Regression: TRAP-6 — add-vhost.sh must delegate to validate.sh --check-fqdn
# · Scenario: basic ai-platform.yaml with expose:true + domain → main() calls validate.sh
# · Last fail: N/A (new test)
# · Remove if: FQDN check is moved to a different mechanism (not shell script delegation)
def test_fqdn_check_delegates_to_validate(tmp_path: Path) -> None:
    """FQDN delegation: add-vhost.sh calls validate.sh --check-fqdn (not local grep)."""
    # ── Arrange: copy script to temp, create mock validate.sh, create fixtures ──

    # 1. Copy add-vhost.sh to temp (so SCRIPT_DIR points to temp dir)
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    # 2. Create mock validate.sh next to the temp copy
    mock_validate = tmp_path / "validate.sh"
    mock_validate.write_text("""#!/bin/bash
# Mock validate.sh — logs call and exits 0 (no FQDN conflict)
echo "[IMP:9][validate][mock] Called with: $@" >&2
exit 0
""")
    mock_validate.chmod(0o755)

    # 3. Create project directory with ai-platform.yaml
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("""expose: true
domain: example.com
target_node: mynode
""")

    # 4. Create node-configs directory (flat layout — no conf.d/ subdir)
    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    # 5. Set PLATFORM_ROOT to real project root (so logging.sh resolves)
    env = {
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
    }

    # ── Act ──
    result = source_and_run(
        f'main "--project-dir" "{project_dir}" "--node-configs-dir" "{node_configs_dir}"',
        env=env,
        script_path=str(script_copy),
    )

    # ── Assert ──
    assert result.returncode == 0, f"main() failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    # Verify mock validate.sh received --check-fqdn with project dir
    mock_output = [line for line in result.stderr.splitlines() if "[validate][mock]" in line]
    assert any("Called with: --check-fqdn" in line for line in mock_output), (
        "Mock validate.sh was not called with --check-fqdn.\n"
        "Mock stderr lines:\n" + "\n".join(mock_output) + "\n"
        f"Full stderr:\n{result.stderr}"
    )

    # Verify the project directory was passed to validate.sh
    assert any(str(project_dir) in line for line in mock_output), (
        "validate.sh did not receive project directory.\nMock lines:\n" + "\n".join(mock_output)
    )

    # Verify the nginx vhost was generated (confirming full flow completed, flat layout)
    vhost_file = node_configs_dir / "mynode" / "overlays" / "nginx" / "example.com.conf"
    assert vhost_file.is_file(), f"Vhost file was not generated: {vhost_file}"
    assert "proxy_pass" in vhost_file.read_text(), "Vhost missing proxy_pass directive"

    # LDD telemetry
    assert_ldd_stderr(result)


# endregion FUNC_test_fqdn_check_delegates_to_validate


# region FUNC_test_vhost_template_http2_directive
## @purpose  Verify generated vhost uses modern http2 syntax: `listen 443 ssl;` + `http2 on;`
##           instead of deprecated `listen 443 ssl http2;` (T3 from DevPlan 004).
## @io       ⇥ tmp_path → ◇ run add-vhost.sh → ◇ parse vhost → ⊕ assert http2 on; present
##           ⊕ assert listen.*http2 absent
## @complexity O(1)
## @invariants
##   - Generated vhost must contain `http2 on;` on a separate line
##   - Generated vhost must NOT contain `listen .* http2` (deprecated syntax)
##   - Both IPv4 (443) and IPv6 ([::]:443) listeners must be clean


# 🧪 TRAP[TEST] · Regression: T3 — add-vhost.sh http2 modernization
# · Scenario: add-vhost.sh generates nginx vhost for domain with SSL
# · Last fail: WARN on VPS — deprecated listen ... http2, protocol options redefined
# · Remove if: nginx stops supporting http2 on; directive (unlikely)
def test_vhost_template_http2_directive(tmp_path: Path) -> None:
    """Generated vhost uses modern http2 syntax: listen 443 ssl; + separate http2 on; line."""
    # ── Arrange ──

    # 1. Copy add-vhost.sh to temp
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    # 2. Create mock validate.sh
    mock_validate = tmp_path / "validate.sh"
    mock_validate.write_text("""#!/bin/bash
echo "[IMP:9][validate][mock] Called with: $@" >&2
exit 0
""")
    mock_validate.chmod(0o755)

    # 3. Create project directory with ai-platform.yaml
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("""expose: true
domain: example.com
target_node: mynode
""")

    # 4. Create node-configs directory
    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    # 5. Set PLATFORM_ROOT
    env = {
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
    }

    # ── Act ──
    result = source_and_run(
        f'main "--project-dir" "{project_dir}" "--node-configs-dir" "{node_configs_dir}"',
        env=env,
        script_path=str(script_copy),
    )

    # ── Assert ──
    assert result.returncode == 0, f"main() failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_file = node_configs_dir / "mynode" / "overlays" / "nginx" / "example.com.conf"
    assert vhost_file.is_file(), f"Vhost file not generated: {vhost_file}"
    vhost_content = vhost_file.read_text()

    print("--- VHOST CONTENT ---")
    print(vhost_content)
    print("--- END VHOST ---")

    # Check 1: modern http2 on; on its own line
    assert "http2 on;" in vhost_content, f"Vhost must contain 'http2 on;' directive:\n{vhost_content}"
    # Ensure http2 is on its own line, not part of listen directive
    http2_lines = [line.strip() for line in vhost_content.split("\n") if "http2" in line]
    assert all(line == "http2 on;" or line.startswith("#") for line in http2_lines if "http2" in line), (
        f"'http2' lines must be 'http2 on;', not part of listen: {http2_lines}"
    )

    # Check 2: no deprecated listen ... http2
    listen_lines = [line.strip() for line in vhost_content.split("\n") if "listen" in line]
    for line in listen_lines:
        assert " http2" not in line.split("ssl")[1] if "ssl" in line else True, (
            f"Deprecated listen ... http2 found: '{line}'"
        )
    assert "http2" not in " ".join([line for line in listen_lines if "ssl" in line]), (
        f"No listen line should contain 'http2': {listen_lines}"
    )

    # Check 3: both IPv4 and IPv6 listen have ssl without http2 flag
    assert "listen 443 ssl;" in vhost_content, "Missing IPv4 ssl listen"
    assert "listen [::]:443 ssl;" in vhost_content, "Missing IPv6 ssl listen"

    # LDD telemetry (IMP:9 from vhost content)
    print("--- LDD TRAJECTORY (stderr) ---")
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            try:
                imp_level = int(line.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    print(line)
            except (ValueError, IndexError):
                pass
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_vhost_template_http2_directive
