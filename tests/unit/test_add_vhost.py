"""Tests for add-vhost.sh — thin facade → vhost_renderer.py (DevPlan 173 W2.3)."""
# GREP_SUMMARY: test add-vhost nginx vhost cert-path duplicate-fqdn http2 hyphen-normalization stale-cleanup
# STRUCTURE: [helper] ▶ _run_add_vhost (subprocess add-vhost.sh) → ╪ create ai-platform.yaml → ╪ create node-configs → ◇ assert vhost content → ⎋ assert_ldd_stderr
# region MODULE_CONTRACT
## @purpose  Integration tests for core/internal/scaffold/add-vhost.sh — тонкий фасад над
##           vhost_renderer.py (DevPlan 173 W2.3: parse_args+dispatch извлечены в Python).
##           Проверяют end-to-end генерацию vhost через subprocess (bash add-vhost.sh).
## @scope    subprocess.run — только для запуска shell-фасада (exempt from business-logic rule).
## @invariants
##   - SCRIPT_PATH → core/internal/scaffold/add-vhost.sh (exec python3 -m vhost_renderer)
##   - LDD trajectory из stderr (vhost_renderer logging.basicConfig stream=sys.stderr)
## @rationale
##   Миграция DevPlan 173 W2.3: тесты source_and_run(main ...) → subprocess add-vhost.sh;
##   бизнес-логика (cert-paths, FQDN-uniqueness, http2, underscore-нормализация) — в vhost_renderer.py.
## @changes  2026-07-07 | Created per DevPlan $TEST_SPEC
## @changes  2026-08-16 | DevPlan 173 W2.3 — source_and_run → subprocess-инвокация thin facade
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
from pathlib import Path

from conftest import assert_ldd_stderr

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────
SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scaffold" / "add-vhost.sh"
PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent  # /Users/.../ai-platform


# ─── Helpers ─────────────────────────────────────────────────────────


def _run_add_vhost(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run add-vhost.sh as subprocess (bash thin facade → python3 -m vhost_renderer).

    ## @purpose — subprocess-инвокация thin facade (DevPlan 173 W2.3: exec python3 -m).
    ## @io — ⇥ args, env → ⎋ CompletedProcess
    ## @complexity O(1)
    """
    full_env = os.environ.copy()
    full_env.update(env)
    full_env.setdefault("PLATFORM_ROOT", str(PLATFORM_ROOT))
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
    )


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
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("""expose: true
domain: app.test.local
target_node: mynode
""")

    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    env = {
        "PLATFORM_DOMAIN": "test.local",
    }

    # ── Act ──
    result = _run_add_vhost(["--project-dir", str(project_dir), "--node-configs-dir", str(node_configs_dir)], env)

    # ── Assert ──
    assert result.returncode == 0, f"add-vhost.sh failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_file = node_configs_dir / "mynode" / "overlays" / "nginx" / "app.test.local.conf"
    assert vhost_file.is_file(), f"Vhost file not generated: {vhost_file}"
    vhost_content = vhost_file.read_text()

    logger.info("--- VHOST CONTENT ---")
    logger.info("%s", vhost_content)
    logger.info("--- END VHOST ---")

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
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("""expose: true
domain: myapp.com
target_node: mynode
""")

    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    env = {
        "PLATFORM_DOMAIN": "test.local",
    }

    # ── Act ──
    result = _run_add_vhost(["--project-dir", str(project_dir), "--node-configs-dir", str(node_configs_dir)], env)

    # ── Assert ──
    assert result.returncode == 0, f"add-vhost.sh failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_file = node_configs_dir / "mynode" / "overlays" / "nginx" / "myapp.com.conf"
    assert vhost_file.is_file(), f"Vhost file not generated: {vhost_file}"
    vhost_content = vhost_file.read_text()

    logger.info("--- VHOST CONTENT (independent) ---")
    logger.info("%s", vhost_content)
    logger.info("--- END VHOST ---")

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
## @purpose  Verify FQDN uniqueness enforcement (check_duplicate_domains):
##           render-all с двумя проектами с одинаковым domain → DuplicateDomainError → ненулевой rc,
##           stderr содержит "Duplicate domain".


# 🧪 TRAP[TEST] · Regression: duplicate FQDN must abort render-all
# · Scenario: node.yaml с двумя проектами, domain=a.test.local у обоих → render-all FAIL
# · Last fail: shell-делегация удалена; Python check_duplicate_domains (vhost_renderer.py)
# · Remove if: FQDN check is moved to a different mechanism
def test_fqdn_duplicate_domain_rejected(tmp_path: Path) -> None:
    """Duplicate FQDN in node.yaml → render-all abort with DuplicateDomainError."""
    # ── Arrange ──
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

    env = {
        "PLATFORM_DOMAIN": "test.local",
    }

    # ── Act: render-all (FQDN uniqueness check выполняется ДО генерации) ──
    result = _run_add_vhost(["--render-all", "--node", "testnode", "--node-configs-dir", str(node_configs_dir)], env)

    # ── Assert ──
    # Duplicate domain → DuplicateDomainError → exit ≠ 0, НИ ОДИН vhost не записан
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


# 🧪 TRAP[TEST] · Regression: T3 — add-vhost.sh http2 modernization
# · Scenario: add-vhost.sh generates nginx vhost for domain with SSL
# · Remove if: nginx stops supporting http2 on; directive (unlikely)
def test_vhost_template_http2_directive(tmp_path: Path) -> None:
    """Generated vhost uses modern http2 syntax: listen 443 ssl; + separate http2 on; line."""
    # ── Arrange ──
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("""expose: true
domain: example.com
target_node: mynode
""")

    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    env: dict[str, str] = {}

    # ── Act ──
    result = _run_add_vhost(["--project-dir", str(project_dir), "--node-configs-dir", str(node_configs_dir)], env)

    # ── Assert ──
    assert result.returncode == 0, f"add-vhost.sh failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_file = node_configs_dir / "mynode" / "overlays" / "nginx" / "example.com.conf"
    assert vhost_file.is_file(), f"Vhost file not generated: {vhost_file}"
    vhost_content = vhost_file.read_text()

    logger.info("--- VHOST CONTENT ---")
    logger.info("%s", vhost_content)
    logger.info("--- END VHOST ---")

    # Check 1: modern http2 on; on its own line
    assert "http2 on;" in vhost_content, f"Vhost must contain 'http2 on;' directive:\n{vhost_content}"
    http2_lines = [line.strip() for line in vhost_content.split("\n") if "http2" in line]
    assert all(line == "http2 on;" or line.startswith("#") for line in http2_lines if "http2" in line), (
        f"'http2' lines must be 'http2 on;', not part of listen: {http2_lines}"
    )

    # Check 2: no deprecated listen ... http2
    listen_lines = [line.strip() for line in vhost_content.split("\n") if "listen" in line]
    assert "http2" not in " ".join([line for line in listen_lines if "ssl" in line]), (
        f"No listen line should contain 'http2': {listen_lines}"
    )

    # Check 3: both IPv4 and IPv6 listen have ssl without http2 flag
    assert "listen 443 ssl;" in vhost_content, "Missing IPv4 ssl listen"
    assert "listen [::]:443 ssl;" in vhost_content, "Missing IPv6 ssl listen"

    # LDD telemetry
    assert_ldd_stderr(result)


# endregion FUNC_test_vhost_template_http2_directive


# region FUNC_test_add_vhost_hyphen_normalization
## @purpose  Verify that vhost rendering normalizes hyphens to underscores in nginx
##           upstream variable names. Project name `my-cool-app` → `$upstream_my_cool_app`.


# 🧪 TRAP[TEST] · Regression: D1.1 — hyphens in project name → underscore normalization
# · Scenario: my-cool-app project → vhost body must use $upstream_my_cool_app
# · Remove if: nginx variable names are no longer derived from project name
def test_add_vhost_hyphen_normalization(tmp_path: Path) -> None:
    """Verify vhost body uses underscores in upstream variable for hyphenated project names."""
    # ── Arrange ──
    project_dir = tmp_path / "my-cool-app"
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text("""expose: true
domain: app.test.local
target_node: mynode
""")

    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    env = {
        "PLATFORM_DOMAIN": "test.local",
    }

    # ── Act ──
    result = _run_add_vhost(["--project-dir", str(project_dir), "--node-configs-dir", str(node_configs_dir)], env)

    # ── Assert ──
    assert result.returncode == 0, f"add-vhost.sh failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_file = node_configs_dir / "mynode" / "overlays" / "nginx" / "app.test.local.conf"
    assert vhost_file.is_file(), f"Vhost file not generated: {vhost_file}"
    body = vhost_file.read_text()

    logger.info("--- VHOST BODY (hyphen normalization) ---")
    logger.info("%s", body)
    logger.info("--- END VHOST ---")

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


# 🧪 TRAP[TEST] · Regression: cert domain resolution — subdomain/apex/independent
# · Scenario: app.tronyx.ru → platform domain; myapp.com → own domain
# · Remove if: cert resolution logic is replaced with different mechanism
def test_add_vhost_wildcard_cert_resolution(tmp_path: Path) -> None:
    """Verify rendered vhost uses correct cert domain for subdomain, apex, and independent domains."""
    # ── Arrange ──
    node_configs_dir = tmp_path / "node-configs"
    (node_configs_dir / "mynode" / "overlays" / "nginx").mkdir(parents=True)

    env = {
        "PLATFORM_DOMAIN": "tronyx.ru",
    }

    def run_add(project_name: str, domain: str) -> subprocess.CompletedProcess:
        """Run add for a project with the given domain; return CompletedProcess."""
        project_dir = tmp_path / project_name
        project_dir.mkdir(exist_ok=True)
        (project_dir / "ai-platform.yaml").write_text(f"expose: true\ndomain: {domain}\ntarget_node: mynode\n")
        return _run_add_vhost(["--project-dir", str(project_dir), "--node-configs-dir", str(node_configs_dir)], env)

    # ── Test 1: Subdomain of PLATFORM_DOMAIN → wildcard cert path ──
    result = run_add("sub-app", "app.tronyx.ru")
    assert result.returncode == 0, f"subdomain failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"
    assert "cert=tronyx.ru" in result.stderr, f"Subdomain should use wildcard cert 'tronyx.ru', got:\n{result.stderr}"

    # ── Test 2: Apex domain → wildcard cert path (apex IS the platform domain) ──
    result = run_add("apex-app", "tronyx.ru")
    assert result.returncode == 0, f"apex failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"
    assert "cert=tronyx.ru" in result.stderr, f"Apex domain should use cert 'tronyx.ru', got:\n{result.stderr}"

    # ── Test 3: Independent domain (not subdomain of PLATFORM_DOMAIN) → personal cert path ──
    result = run_add("indie-app", "myapp.com")
    assert result.returncode == 0, f"independent failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"
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


# 🧪 TRAP[TEST] · Regression: D1.2 — stale vhost cleanup on rerender
# · Scenario: render_all with 2 projects → render_all with 1 project → vhost for removed project gone
# · Remove if: vhost cleanup logic is replaced with different mechanism
def test_add_vhost_stale_cleanup_on_rerender(tmp_path: Path) -> None:
    """Verify render_all removes stale vhosts for projects removed from node.yaml."""
    # ── Arrange ──
    node_configs_dir = tmp_path / "node-configs"
    overlay_dir = node_configs_dir / "testnode" / "overlays" / "nginx"
    overlay_dir.mkdir(parents=True)

    env = {
        "PLATFORM_DOMAIN": "test.local",
    }

    # ── Phase 1: Render with 2 projects ──
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

    render_args = ["--render-all", "--node", "testnode", "--node-configs-dir", str(node_configs_dir)]
    result = _run_add_vhost(render_args, env)

    # Verify both vhosts were rendered
    assert result.returncode == 0, f"First render_all failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_files_p1 = list(overlay_dir.glob("*.conf"))
    assert len(vhost_files_p1) == 2, (
        f"Phase 1: expected 2 vhost files, got {len(vhost_files_p1)}: {[f.name for f in vhost_files_p1]}"
    )
    assert (overlay_dir / "a.test.local.conf").is_file(), "Phase 1: a.test.local.conf not generated"
    assert (overlay_dir / "b.test.local.conf").is_file(), "Phase 1: b.test.local.conf not generated"

    # ── Phase 2: Render with only 1 project ──
    node_yaml.write_text("""domain: test.local
projects:
  - name: project-a
    domain: a.test.local
    repo: git@github.com:test/a.git
""")

    result = _run_add_vhost(render_args, env)

    # Verify only project-a vhost remains
    assert result.returncode == 0, f"Second render_all failed:\nSTDERR:{result.stderr}\nSTDOUT:{result.stdout}"

    vhost_files_p2 = list(overlay_dir.glob("*.conf"))
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
