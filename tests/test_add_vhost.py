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


# ═══════════════════════════════════════════════════════════════════════
# WAVE 2: D1 unit tests (DevPlan 020)
# ═══════════════════════════════════════════════════════════════════════


# region FUNC_test_add_vhost_hyphen_normalization
## @purpose  Verify that generate_vhost_body() normalizes hyphens to underscores in nginx
##           upstream variable names. Project name `my-cool-app` → `$upstream_my_cool_app`
##           (underscores), NOT `$upstream_my-cool-app` (which nginx would parse as variable minus literals).
## @regression  D1.1: hyphens in project name cause nginx syntax error in upstream variable
## @io       ⇥ tmp_path → ◇ source_and_run(generate_vhost_body) → ⊕ assert upstream uses underscores
##           ⊕ assert no hyphenated upstream variable ⊕ assert server_name preserves hyphens
## @complexity O(1)


# 🧪 TRAP[TEST] · Regression: D1.1 — hyphens in project name → underscore normalization
# · Scenario: my-cool-app project → generate_vhost_body → must use $upstream_my_cool_app
# · Last fail: nginx syntax error on $upstream_my minus cool minus app
# · Remove if: nginx variable names are no longer derived from project name
def test_add_vhost_hyphen_normalization(tmp_path: Path) -> None:
    """Verify vhost body uses underscores in upstream variable for hyphenated project names."""
    # ── Arrange ──
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    # Create mock validate.sh (needed for script source, not used by generate_vhost_body directly)
    mock_validate = tmp_path / "validate.sh"
    mock_validate.write_text("""#!/bin/bash
echo "[IMP:9][validate][mock] Called with: $@" >&2
exit 0
""")
    mock_validate.chmod(0o755)

    env = {
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
    }

    # ── Act: call generate_vhost_body directly ──
    function_call = 'generate_vhost_body "app.test.local" "my-cool-app" "test.local"'
    result = source_and_run(function_call, env=env, script_path=str(script_copy))

    # ── Assert ──
    assert result.returncode == 0, f"generate_vhost_body failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"
    body = result.stdout

    print("--- VHOST BODY (hyphen normalization) ---")
    print(body)
    print("--- END VHOST ---")

    # Must use underscores in upstream variable name
    assert "$upstream_my_cool_app" in body, f"Vhost body must contain '$upstream_my_cool_app' (underscores):\n{body}"

    # Must NOT use hyphens in upstream variable name (would cause nginx syntax error)
    assert "$upstream_my-cool-app" not in body, (
        f"Vhost body must NOT contain '$upstream_my-cool-app' (hyphens):\n{body}"
    )

    # server_name can still use hyphens (nginx allows hyphens in server_name)
    assert "server_name app.test.local" in body, f"Vhost body must contain 'server_name app.test.local':\n{body}"

    # LDD telemetry — generate_vhost_body has no log_imp calls (pure stdout),
    # so manual trajectory print without IMP:9 assertion
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


# endregion FUNC_test_add_vhost_hyphen_normalization


# region FUNC_test_add_vhost_wildcard_cert_resolution
## @purpose  Verify resolve_cert_domain() returns correct cert path:
##           - Subdomains of PLATFORM_DOMAIN → wildcard cert path (PLATFORM_DOMAIN)
##           - Apex PLATFORM_DOMAIN → wildcard cert path (PLATFORM_DOMAIN)
##           - Independent domains → personal cert path (own FQDN)
## @regression  Ensure cert path resolution for DD3 (wildcard) and O11 (own cert)
## @io       ⇥ tmp_path → ◇ source_and_run(resolve_cert_domain) with various FQDNs
##           ⊕ assert stdout contains expected cert domain
## @complexity O(1)


# 🧪 TRAP[TEST] · Regression: cert domain resolution — subdomain/apex/independent
# · Scenario: resolve_cert_domain with app.tronyx.ru → platform domain;
#             resolve_cert_domain with myapp.com → own domain
# · Last fail: N/A (new test)
# · Remove if: cert resolution logic is replaced with different mechanism
def test_add_vhost_wildcard_cert_resolution(tmp_path: Path) -> None:
    """Verify resolve_cert_domain returns correct cert domain for subdomain, apex, and independent domains."""
    # ── Arrange ──
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    # Base env: PLATFORM_DOMAIN + PLATFORM_ROOT for logging.sh
    env = {
        "PLATFORM_DOMAIN": "tronyx.ru",
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
    }

    # ── Test 1: Subdomain of PLATFORM_DOMAIN → wildcard cert path ──
    result = source_and_run(
        'resolve_cert_domain "app.tronyx.ru"',
        env=env,
        script_path=str(script_copy),
    )
    assert result.returncode == 0, (
        f"resolve_cert_domain subdomain failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"
    )
    print("--- Test 1: subdomain app.tronyx.ru ---")
    print(f"STDOUT: [{result.stdout.strip()}]")
    print(f"STDERR: [{result.stderr.strip()}]")
    assert "tronyx.ru" in result.stdout, (
        f"Subdomain should resolve to wildcard cert domain 'tronyx.ru', got: '{result.stdout.strip()}'"
    )

    # ── Test 2: Apex domain → wildcard cert path (apex IS the platform domain) ──
    result = source_and_run(
        'resolve_cert_domain "tronyx.ru"',
        env=env,
        script_path=str(script_copy),
    )
    assert result.returncode == 0, f"resolve_cert_domain apex failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"
    print("--- Test 2: apex tronyx.ru ---")
    print(f"STDOUT: [{result.stdout.strip()}]")
    assert "tronyx.ru" in result.stdout, (
        f"Apex domain should resolve to its own cert path 'tronyx.ru', got: '{result.stdout.strip()}'"
    )

    # ── Test 3: Independent domain (not subdomain of PLATFORM_DOMAIN) → personal cert path ──
    result = source_and_run(
        'resolve_cert_domain "myapp.com"',
        env=env,
        script_path=str(script_copy),
    )
    assert result.returncode == 0, (
        f"resolve_cert_domain independent failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"
    )
    print("--- Test 3: independent myapp.com ---")
    print(f"STDOUT: [{result.stdout.strip()}]")
    assert "myapp.com" in result.stdout, (
        f"Independent domain should resolve to personal cert domain 'myapp.com', got: '{result.stdout.strip()}'"
    )

    # LDD telemetry from last result
    assert_ldd_stderr(result)


# endregion FUNC_test_add_vhost_wildcard_cert_resolution


# region FUNC_test_add_vhost_stale_cleanup_on_rerender
## @purpose  Verify that render_all cleans up stale vhost files for removed projects.
##           1. node.yaml with project A + project B → render_all → 2 vhost files
##           2. node.yaml with only project A → render_all → project B vhost removed
## @regression  D1.2: `head -1` (old) missed GENERATED marker on line 2 → stale vhosts not removed.
##              Now uses `grep -q` to find marker anywhere in file.
## @io       ⇥ tmp_path → ◇ render_all 2× (first with 2 projects, then 1)
##           ⊕ assert vhost count changes from 2 → 1
## @complexity O(P) where P = number of projects


# 🧪 TRAP[TEST] · Regression: D1.2 — stale vhost cleanup on rerender
# · Scenario: render_all with 2 projects → render_all with 1 project → vhost for removed project gone
# · Last fail: old `head -1` didn't reach GENERATED marker on line 2 → stale vhosts persisted
# · Remove if: vhost cleanup logic is replaced with different mechanism
def test_add_vhost_stale_cleanup_on_rerender(tmp_path: Path) -> None:
    """Verify render_all removes stale vhosts for projects removed from node.yaml."""
    # ── Arrange ──
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    # Create mock validate.sh (sourced by script, only needed for sourcing)
    mock_validate = tmp_path / "validate.sh"
    mock_validate.write_text("""#!/bin/bash
echo "[IMP:9][validate][mock] Called with: $@" >&2
exit 0
""")
    mock_validate.chmod(0o755)

    # Create node-configs directory structure
    node_configs_dir = tmp_path / "node-configs"
    overlay_dir = node_configs_dir / "testnode" / "overlays" / "nginx"
    overlay_dir.mkdir(parents=True)

    env = {
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
        "PLATFORM_DOMAIN": "test.local",
    }

    # ── Phase 1: Render with 2 projects ──
    # Create node.yaml with project-a and project-b
    node_yaml = node_configs_dir / "testnode" / "node.yaml"
    node_yaml.write_text("""domain: test.local
projects:
  - name: project-a
    domain: a.test.local
    repo: git@github.com:test/a.git
  - name: project-b
    domain: b.test.local
    repo: git@github.com:test/b.git
""")

    # Mock nginx_t_harness to skip Docker validation (unit test focus: file cleanup)
    function_call = (
        'nginx_t_harness() { log_imp 7 "harness" "MOCK: skipping nginx -t for unit test"; return 0; }; '
        'parse_args "--render-all" "--node" "testnode" '
        f'"--node-configs-dir" "{node_configs_dir}" && render_all'
    )
    result = source_and_run(function_call, env=env, script_path=str(script_copy))

    # Verify both vhosts were rendered
    assert result.returncode == 0, f"First render_all failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_files_p1 = list(overlay_dir.glob("*.conf"))
    print("--- Phase 1: vhost files ---")
    for f in vhost_files_p1:
        print(f"  {f.name}")
    print(f"  Count: {len(vhost_files_p1)}")
    print("--- END Phase 1 ---")

    assert len(vhost_files_p1) == 2, (
        f"Phase 1: expected 2 vhost files, got {len(vhost_files_p1)}: {[f.name for f in vhost_files_p1]}"
    )
    assert (overlay_dir / "a.test.local.conf").is_file(), "Phase 1: a.test.local.conf not generated"
    assert (overlay_dir / "b.test.local.conf").is_file(), "Phase 1: b.test.local.conf not generated"

    # ── Phase 2: Render with only 1 project ──
    # Update node.yaml — remove project-b
    node_yaml.write_text("""domain: test.local
projects:
  - name: project-a
    domain: a.test.local
    repo: git@github.com:test/a.git
""")

    result = source_and_run(function_call, env=env, script_path=str(script_copy))

    # Verify only project-a vhost remains
    assert result.returncode == 0, f"Second render_all failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_files_p2 = list(overlay_dir.glob("*.conf"))
    print("--- Phase 2: vhost files ---")
    for f in vhost_files_p2:
        print(f"  {f.name}")
    print(f"  Count: {len(vhost_files_p2)}")
    print("--- END Phase 2 ---")

    assert len(vhost_files_p2) == 1, (
        f"Phase 2: expected 1 vhost file, got {len(vhost_files_p2)}: {[f.name for f in vhost_files_p2]}"
    )

    # project-a vhost must still exist
    assert (overlay_dir / "a.test.local.conf").is_file(), "Phase 2: a.test.local.conf should still exist"

    # project-b vhost must be GONE
    assert not (overlay_dir / "b.test.local.conf").is_file(), (
        "Phase 2: b.test.local.conf should have been removed (stale cleanup)"
    )

    # LDD telemetry from second render
    assert_ldd_stderr(result)


# endregion FUNC_test_add_vhost_stale_cleanup_on_rerender
