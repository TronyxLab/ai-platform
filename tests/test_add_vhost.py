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
# B10 T9: stale-предупреждение «Все 7 тестов падают» удалено — проверено 2026-08-01:
# 7 passed (python3 -m pytest tests/test_add_vhost.py). Предупреждение о провале вводило в заблуждение.
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


# region FUNC_test_fqdn_duplicate_domain_rejected
## @purpose  Verify FQDN uniqueness enforcement moved to Python (check_duplicate_domains):
##           render-all с двумя проектами с одинаковым domain → DuplicateDomainError → ненулевой rc,
##           stderr содержит "Duplicate domain". Заменяет stale-тест делегации в validate.sh --check-fqdn
##           (контракт удалён при Strangler-миграции — см. TRAP в vhost_renderer.py:30).
## @io       ⇥ tmp_path fixture → ⎛ assertions on stderr + return code
## @complexity O(1)


# 🧪 TRAP[TEST] · Regression: duplicate FQDN must abort render-all (бывш. TRAP-6 validate.sh --check-fqdn)
# · Scenario: node.yaml с двумя проектами, domain=a.test.local у обоих → render-all FAIL
# · Last fail: shell-делегация удалена; Python check_duplicate_domains (vhost_renderer.py:519)
# · Remove if: FQDN check is moved to a different mechanism
def test_fqdn_duplicate_domain_rejected(tmp_path: Path) -> None:
    """Duplicate FQDN in node.yaml → render-all abort with DuplicateDomainError."""
    # ── Arrange: copy script to temp, create fixtures ──

    # 1. Copy add-vhost.sh to temp (so SCRIPT_DIR points to temp dir)
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    # 2. Create node-configs directory structure with node.yaml (2 projects, SAME domain)
    node_configs_dir = tmp_path / "node-configs"
    overlay_dir = node_configs_dir / "testnode" / "overlays" / "nginx"
    overlay_dir.mkdir(parents=True)
    node_yaml = node_configs_dir / "testnode" / "node.yaml"
    node_yaml.write_text("""domain: test.local
projects:
  - name: project-a
    domain: a.test.local
    repo: git@github.com:test/a.git
  - name: project-b
    domain: a.test.local
    repo: git@github.com:test/b.git
""")

    # 3. Set PLATFORM_ROOT + PLATFORM_DOMAIN
    env = {
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
        "PLATFORM_DOMAIN": "test.local",
    }

    # ── Act: main --render-all (FQDN uniqueness check выполняется ДО генерации) ──
    result = source_and_run(
        f'main "--render-all" "--node" "testnode" "--node-configs-dir" "{node_configs_dir}"',
        env=env,
        script_path=str(script_copy),
    )

    # ── Assert ──
    # Duplicate domain → DuplicateDomainError → exit 1, НИ ОДИН vhost не записан
    assert result.returncode != 0, (
        f"render-all должен упасть при дубликате FQDN (all-or-nothing), но вернул 0.\nSTDERR:\n{result.stderr}"
    )
    assert "Duplicate domain" in result.stderr, f"stderr должен содержать 'Duplicate domain':\n{result.stderr}"
    assert not list(overlay_dir.glob("*.conf")), (
        "Ни один vhost не должен быть записан при дубликате FQDN (all-or-nothing)"
    )

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
                print(f"  [MALFORMED] {line}")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_vhost_template_http2_directive


# ═══════════════════════════════════════════════════════════════════════
# WAVE 2: D1 unit tests (DevPlan 020)
# ═══════════════════════════════════════════════════════════════════════


# region FUNC_test_add_vhost_hyphen_normalization
## @purpose  Verify that vhost rendering normalizes hyphens to underscores in nginx
##           upstream variable names. Project name `my-cool-app` → `$upstream_my_cool_app`
##           (underscores), NOT `$upstream_my-cool-app` (which nginx would parse as variable minus literals).
##           Контракт: main --add → vhost файл (раньше: прямой вызов generate_vhost_body в shell —
##           функция удалена при Strangler-миграции в vhost_renderer.py).
## @regression  D1.1: hyphens in project name cause nginx syntax error in upstream variable
## @io       ⇥ tmp_path → ◇ main --add → ⊕ assert upstream uses underscores
##           ⊕ assert no hyphenated upstream variable ⊕ assert server_name preserves hyphens
## @complexity O(1)


# 🧪 TRAP[TEST] · Regression: D1.1 — hyphens in project name → underscore normalization
# · Scenario: my-cool-app project → vhost body must use $upstream_my_cool_app
# · Last fail: nginx syntax error on $upstream_my minus cool minus app
# · Remove if: nginx variable names are no longer derived from project name
def test_add_vhost_hyphen_normalization(tmp_path: Path) -> None:
    """Verify vhost body uses underscores in upstream variable for hyphenated project names."""
    # ── Arrange ──
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    # Create project directory named my-cool-app with ai-platform.yaml
    project_dir = tmp_path / "my-cool-app"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("""expose: true
domain: app.test.local
target_node: mynode
""")

    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    env = {
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
        "PLATFORM_DOMAIN": "test.local",
    }

    # ── Act: main --add (публичный контракт facade; render_vhost в Python) ──
    result = source_and_run(
        f'main "--project-dir" "{project_dir}" "--node-configs-dir" "{node_configs_dir}"',
        env=env,
        script_path=str(script_copy),
    )

    # ── Assert ──
    assert result.returncode == 0, f"main() failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_file = node_configs_dir / "mynode" / "overlays" / "nginx" / "app.test.local.conf"
    assert vhost_file.is_file(), f"Vhost file not generated: {vhost_file}"
    body = vhost_file.read_text()

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

    # LDD telemetry
    assert_ldd_stderr(result)


# endregion FUNC_test_add_vhost_hyphen_normalization


# region FUNC_test_add_vhost_wildcard_cert_resolution
## @purpose  Verify cert domain resolution in rendered vhost:
##           - Subdomains of PLATFORM_DOMAIN → wildcard cert path (PLATFORM_DOMAIN)
##           - Apex PLATFORM_DOMAIN → wildcard cert path (PLATFORM_DOMAIN)
##           - Independent domains → personal cert path (own FQDN)
##           Контракт: main --add → stderr render_vhost "(cert=...)" (раньше: прямой вызов
##           resolve_cert_domain в shell — функция удалена при Strangler-миграции).
## @regression  Ensure cert path resolution for DD3 (wildcard) and O11 (own cert)
## @io       ⇥ tmp_path → ◇ main --add с разными FQDNs → ⊕ assert cert= в stderr
## @complexity O(1)


# 🧪 TRAP[TEST] · Regression: cert domain resolution — subdomain/apex/independent
# · Scenario: app.tronyx.ru → platform domain; myapp.com → own domain
# · Last fail: N/A (new test)
# · Remove if: cert resolution logic is replaced with different mechanism
def test_add_vhost_wildcard_cert_resolution(tmp_path: Path) -> None:
    """Verify rendered vhost uses correct cert domain for subdomain, apex, and independent domains."""
    # ── Arrange ──
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    env = {
        "PLATFORM_DOMAIN": "tronyx.ru",
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
    }

    def run_add(project_name: str, domain: str):
        """Run main --add for a project with the given domain; return CompletedProcess."""
        project_dir = tmp_path / project_name
        project_dir.mkdir(exist_ok=True)
        (project_dir / "ai-platform.yaml").write_text(f"expose: true\ndomain: {domain}\ntarget_node: mynode\n")
        return source_and_run(
            f'main "--project-dir" "{project_dir}" "--node-configs-dir" "{node_configs_dir}"',
            env=env,
            script_path=str(script_copy),
        )

    # ── Test 1: Subdomain of PLATFORM_DOMAIN → wildcard cert path ──
    result = run_add("sub-app", "app.tronyx.ru")
    assert result.returncode == 0, f"main() subdomain failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"
    print("--- Test 1: subdomain app.tronyx.ru ---")
    print(f"STDERR: [{result.stderr.strip()}]")
    assert "cert=tronyx.ru" in result.stderr, f"Subdomain should use wildcard cert 'tronyx.ru', got:\n{result.stderr}"

    # ── Test 2: Apex domain → wildcard cert path (apex IS the platform domain) ──
    result = run_add("apex-app", "tronyx.ru")
    assert result.returncode == 0, f"main() apex failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"
    print("--- Test 2: apex tronyx.ru ---")
    print(f"STDERR: [{result.stderr.strip()}]")
    assert "cert=tronyx.ru" in result.stderr, f"Apex domain should use cert 'tronyx.ru', got:\n{result.stderr}"

    # ── Test 3: Independent domain (not subdomain of PLATFORM_DOMAIN) → personal cert path ──
    result = run_add("indie-app", "myapp.com")
    assert result.returncode == 0, f"main() independent failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"
    print("--- Test 3: independent myapp.com ---")
    print(f"STDERR: [{result.stderr.strip()}]")
    assert "cert=myapp.com" in result.stderr, (
        f"Independent domain should use own cert 'myapp.com', got:\n{result.stderr}"
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

    # nginx_t_harness выполняется в Python (vhost_renderer) — не мокается через shell-функцию.
    # nginx:1.28-alpine образ локально закеширован → harness работает быстро.
    function_call = f'main "--render-all" "--node" "testnode" "--node-configs-dir" "{node_configs_dir}"'
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
