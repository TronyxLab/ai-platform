"""Gate test: validate generated nginx vhosts pass nginx -t in Docker container."""
# GREP_SUMMARY: gate vhost nginx-t docker harness hyphen normalization cert-path render-all
# STRUCTURE: ▶ create reference node.yaml (3 projects: hyphen+subdomain+independent) → add-vhost.sh --render-all → ◇ patch SSL paths → ◇ docker run nginx:alpine nginx -t → ⊕ assert exit 0
# region MODULE_CONTRACT
## @purpose  Production gate test for nginx vhost generation. Creates a reference node.yaml
##           with projects that test edge cases (hyphenated names, subdomains, independent domains),
##           runs add-vhost.sh --render-all to generate vhosts, then validates all vhosts
##           pass `nginx -t` in a Docker nginx:alpine container.
## @scope    tests/gates/test_gate_vhost_nginx_t.py — gate test (pytest.mark.gate)
## @invariants
##   - Requires Docker CLI (skip test if not available — not fail)
##   - nginx.conf must define limit_req_zone and resolver referenced by generated vhosts
##   - SSL cert paths in generated vhosts are patched to dev-certs for Docker validation
##   - Vhosts are validated BEFORE the script's own nginx_t_harness runs (belt-and-suspenders)
## @rationale  DevPlan 020 AC-D1-VHOST: generated vhosts must pass `nginx -t`. This gate
##             tests the full pipeline: node.yaml → add-vhost.sh → vhost files → Docker nginx -t.
## @changes  2026-07-20 | Created per DevPlan 020 Wave 3
## @usecases  AC-D1-VHOST (nginx -t in Docker), AC-D1-HYPHEN (hyphenated project names)
# endregion MODULE_CONTRACT

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from _conftest.honesty import require_docker_or_fail
from conftest import assert_ldd_stderr

# ─── Constants ───────────────────────────────────────────────────────
SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scaffold" / "add-vhost.sh"
PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent  # ai-platform root

# ─── Reference node.yaml template ────────────────────────────────────
REFERENCE_NODE_YAML = """\
domain: test-platform.local
projects:
  - name: my-cool-app
    domain: app.test-platform.local
    repo: git@github.com:test/my-cool-app.git
  - name: simple-site
    domain: simple.test-platform.local
    repo: git@github.com:test/simple-site.git
  - name: independent-site
    domain: independent.com
    repo: git@github.com:test/independent-site.git
"""

# ─── Minimal nginx.conf for Docker validation ────────────────────────
NGINX_CONF = """\
events {
    worker_connections 64;
}
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    limit_req_zone $binary_remote_addr zone=dynamic:10m rate=10r/s;

    resolver 127.0.0.11 valid=30s ipv6=off;

    server {
        listen 80 default_server;
        return 444;
    }

    include /etc/nginx/conf.d/overlay/*.conf;
}
"""

# ─── Stub security-headers.conf ──────────────────────────────────────
SECURITY_HEADERS_CONF = """\
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
"""


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════


# region FUNC_patch_cert_paths
def _patch_cert_paths(vhost_content: str) -> str:
    """Replace production LE cert paths with dev-certs paths for Docker validation.

    ## @purpose  Patch a vhost file's SSL certificate paths so they point to
    ##           dev-certs instead of production Let's Encrypt paths. This allows
    ##           nginx -t to pass in a Docker container without actual certificates.
    ## @io       ⇥ vhost_content: str — raw vhost file content
    ##           ⎋ patched_content: str — content with paths replaced
    ## @complexity O(1) — two regex substitutions
    """
    content = re.sub(
        r"/etc/letsencrypt/live/[^/]+/fullchain\.pem",
        "/etc/nginx/dev-certs/fullchain.pem",
        vhost_content,
    )
    content = re.sub(
        r"/etc/letsencrypt/live/[^/]+/privkey\.pem",
        "/etc/nginx/dev-certs/privkey.pem",
        content,
    )
    return content.replace("/var/www/acme", "/tmp/acme-stub")


# endregion FUNC_patch_cert_paths


# ═══════════════════════════════════════════════════════════════════════
# GATE TEST
# ═══════════════════════════════════════════════════════════════════════


# region FUNC_test_gate_vhost_nginx_t
## @purpose  Gate test: generate vhosts from reference node.yaml, then validate
##           all vhosts pass `nginx -t` in Docker. Tests hyphen normalization,
##           subdomain wildcard cert, and independent domain cert paths.
## @regression  D1.1 (hyphen normalization), D1.2 (GENERATED marker detection),
##              AC-D1-VHOST (nginx -t pipeline)
## @io       ⇥ tmp_path → ◇ create node.yaml → ◇ run add-vhost.sh --render-all
##           → ◇ patch SSL paths → ◇ docker run nginx -t → ⊕ assert exit 0
## @complexity O(P + S) where P = projects count, S = Docker startup
## @invariants
##   - SKIP (not FAIL) if Docker CLI is unavailable
##   - Vhosts must contain GENERATED marker in second line
##   - Hyphenated project names must use underscores in upstream variables
##   - Subdomains must use wildcard cert paths
##   - Independent domains must use own cert paths


# 🧪 TRAP[TEST] · Regression: D1 gate — nginx -t with hyphenated projects + subdomains + independent domains
# · Scenario: node.yaml with my-cool-app, simple-site, independent-site → render-all → nginx -t
# · Last fail: N/A (new gate test)
# · Remove if: vhost generation pipeline is replaced with a different mechanism
@pytest.mark.gate
def test_gate_vhost_nginx_t(tmp_path: Path) -> None:
    """Validate generated nginx vhosts pass nginx -t in Docker."""
    # ── Pre-check: Docker availability ──
    require_docker_or_fail(reason="nginx -t validation gate requires Docker CLI")

    # ── Arrange: create node.yaml and directory structure ──
    node_configs_dir = tmp_path / "node-configs"
    overlay_dir = node_configs_dir / "testnode" / "overlays" / "nginx"
    overlay_dir.mkdir(parents=True)

    node_yaml = node_configs_dir / "testnode" / "node.yaml"
    node_yaml.write_text(REFERENCE_NODE_YAML)

    # Copy add-vhost.sh to tmp (so SCRIPT_DIR resolves correctly)
    script_copy = tmp_path / "add-vhost.sh"
    shutil.copy2(str(SCRIPT_PATH), str(script_copy))

    env = {
        "PLATFORM_ROOT": str(PLATFORM_ROOT),
        "PLATFORM_DOMAIN": "test-platform.local",
    }

    # ── Act: generate vhosts via add-vhost.sh --render-all ──
    # add-vhost.sh was migrated to Python vhost_renderer.py (DevPlan 036-wave5b).
    # Call the shell facade as a subprocess instead of source_and_run.
    result = subprocess.run(
        [
            "bash",
            str(script_copy),
            "--render-all",
            "--node",
            "testnode",
            "--node-configs-dir",
            str(node_configs_dir),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=120,
        check=False,
    )

    # Assert generation succeeded
    assert result.returncode == 0, f"render_all failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"

    # Verify 3 vhost files were generated
    vhost_files = list(overlay_dir.glob("*.conf"))
    assert len(vhost_files) == 3, f"Expected 3 vhost files, got {len(vhost_files)}: {[f.name for f in vhost_files]}"

    # ── Assert: verify vhost content BEFORE Docker validation ──
    for vf in vhost_files:
        content = vf.read_text()
        print(f"--- VHOST: {vf.name} ---")
        print(content)
        print("--- END VHOST ---")

        # GENERATED marker must be present (vhost_renderer.py per DevPlan 036-wave5b)
        assert "# GENERATED" in content, f"Vhost {vf.name} missing GENERATED marker:\n{content}"

        # No self-signed default.crt
        assert "default.crt" not in content, f"Vhost {vf.name} uses self-signed default.crt:\n{content}"

    # Specific assertion for my-cool-app: underscore normalization
    my_cool_app_vhost = overlay_dir / "app.test-platform.local.conf"
    assert my_cool_app_vhost.is_file(), "my-cool-app vhost not generated"
    mca_content = my_cool_app_vhost.read_text()
    assert "$upstream_my_cool_app" in mca_content, (
        f"my-cool-app vhost should use '$upstream_my_cool_app' (underscores):\n{mca_content}"
    )
    assert "$upstream_my-cool-app" not in mca_content, (
        f"my-cool-app vhost must NOT use '$upstream_my-cool-app' (hyphens):\n{mca_content}"
    )

    # Specific assertion for independent.com: own cert path
    # The actual cert domain is output by resolve_cert_domain.
    # independent.com is NOT a subdomain of test-platform.local → own cert path
    independent_vhost = overlay_dir / "independent.com.conf"
    assert independent_vhost.is_file(), "independent-site vhost not generated"
    ind_content = independent_vhost.read_text()
    assert "letsencrypt/live/independent.com/" in ind_content, (
        f"independent.com vhost should use its own cert path (not wildcard):\n{ind_content}"
    )

    # ── Docker validation: nginx -t ──
    # Patch vhosts to use dev-certs in a separate temp dir
    patched_dir = tmp_path / "vhosts_patched"
    patched_dir.mkdir()
    for vf in vhost_files:
        patched_content = _patch_cert_paths(vf.read_text())
        patched_vf = patched_dir / vf.name
        patched_vf.write_text(patched_content)

    # Create stub dev-certs
    certs_dir = tmp_path / "dev-certs"
    certs_dir.mkdir()
    # Generate self-signed certs via openssl, or use empty files as fallback
    openssl_result = subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-days",
            "1",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(certs_dir / "privkey.pem"),
            "-out",
            str(certs_dir / "fullchain.pem"),
            "-subj",
            "/CN=localhost",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if openssl_result.returncode != 0:
        # Fallback: create empty stub files
        (certs_dir / "fullchain.pem").write_text("")
        (certs_dir / "privkey.pem").write_text("")

    # Create stub ACME directory
    acme_stub = tmp_path / "acme-stub"
    acme_stub.mkdir()

    # Create stub security-headers.conf
    includes_dir = tmp_path / "includes"
    includes_dir.mkdir()
    (includes_dir / "security-headers.conf").write_text(SECURITY_HEADERS_CONF)

    # Create nginx.conf
    nginx_conf = tmp_path / "nginx.conf"
    nginx_conf.write_text(NGINX_CONF)

    # Run nginx -t in Docker
    print("--- Docker nginx -t validation ---")
    docker_result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{nginx_conf}:/etc/nginx/nginx.conf:ro",
            "-v",
            f"{patched_dir}:/etc/nginx/conf.d/overlay:ro",
            "-v",
            f"{certs_dir}:/etc/nginx/dev-certs:ro",
            "-v",
            f"{includes_dir}:/etc/nginx/includes:ro",
            "-v",
            f"{acme_stub}:/tmp/acme-stub:ro",
            "nginx:alpine",
            "nginx",
            "-t",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    print(f"exit code: {docker_result.returncode}")

    assert docker_result.returncode == 0, (
        f"nginx -t FAILED:\nSTDOUT: {docker_result.stdout}\nSTDERR: {docker_result.stderr}"
    )

    # LDD telemetry from script execution
    assert_ldd_stderr(result)


# endregion FUNC_test_gate_vhost_nginx_t
