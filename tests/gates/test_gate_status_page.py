# GREP_SUMMARY: test-gate-status-page module-contract htpasswd-consistency nginx-stealth secrets-registered ci-negative dockerignore-symlink
# STRUCTURE: ▶ test_gate_status_page_module_contract → assert module.yaml D4
#            ▶ test_gate_status_page_htpasswd_consistency → assert no .htpasswd-monitoring in nginx configs
#            ▶ test_gate_status_page_nginx_stealth → assert return 444 + @stealth_drop + access_log off
#            ▶ test_gate_status_page_secrets_registered → assert PLATFORM_MASTER_* in secrets-manifest.yaml
#            ▶ test_gate_status_page_ci_negative → assert no Basic Auth → 444 (stealth)
#            ▶ test_gate_status_page_dockerignore_symlink → assert .dockerignore symlink to templates/.dockerignore
# @file test_gate_status_page.py
# @purpose  CI gate tests for 016-node-status-page feature — validates module contract,
#           htpasswd unification, nginx stealth config, secrets registration, and CI-negative auth test.
# @scope    Gate-level: static file analysis, no Docker required.
# @invariants
#   - All tests marked @pytest.mark.gate
#   - R5 (anti-survivorship): negative test for CI /health without auth
#   - No hardcoded secrets in test code
#   - Uses Path (not os.path) for portability
# @rationale  Gate tests prevent regressions: htpasswd drift, nginx stealth degradation,
#             module contract violations. Negative test ensures stealth 444 works correctly.
# region MODULE_CONTRACT
## @purpose  Gate tests for 016 status-page — contract enforcement
## @scope    Static analysis gate tests — no Docker, no live services
## @invariants
##   - All tests @pytest.mark.gate
##   - File-level checks: module.yaml, nginx configs, secrets-manifest.yaml, .dockerignore symlink
# endregion MODULE_CONTRACT

from pathlib import Path

import pytest
import yaml

# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _read_file(path: Path) -> str:
    """Read file content, return empty string if not found."""
    try:
        return path.read_text()
    except (FileNotFoundError, IsADirectoryError):
        return ""


# ═══════════════════════════════════════════════════════════════════
# TEST: module contract
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
class TestGateStatusPageModuleContract:
    """Gate: module.yaml D4 contract validation."""

    def test_module_yaml_exists(self):
        """module.yaml exists for status-page."""
        path = PROJECT_ROOT / "core" / "modules" / "status-page" / "module.yaml"
        assert path.exists(), f"Missing module.yaml: {path}"

    def test_module_yaml_required_fields(self):
        """module.yaml has required D4 fields."""
        path = PROJECT_ROOT / "core" / "modules" / "status-page" / "module.yaml"
        content = yaml.safe_load(_read_file(path))

        assert content is not None, "module.yaml is empty or invalid YAML"
        assert content.get("name") == "status-page", f"name should be status-page, got {content.get('name')}"
        assert content.get("install_type") == "docker", (
            f"install_type should be docker, got {content.get('install_type')}"
        )
        assert "description" in content, "Missing description"
        assert "depends_on" in content, "Missing depends_on"
        assert "nginx" in content.get("depends_on", []), "status-page must depend on nginx"

    def test_compose_base_has_profiles_and_healthcheck(self):
        """docker-compose.base.yml has profiles and healthcheck."""
        path = PROJECT_ROOT / "core" / "modules" / "status-page" / "docker-compose.base.yml"
        content = _read_file(path)

        assert "profiles:" in content, "Missing profiles in docker-compose.base.yml"
        assert "status-page" in content, "Profile 'status-page' not found"
        assert "healthcheck:" in content, "Missing healthcheck in docker-compose.base.yml"

    def test_makefile_exists(self):
        """Makefile exists and includes module.mk."""
        path = PROJECT_ROOT / "core" / "modules" / "status-page" / "Makefile"
        assert path.exists(), f"Missing Makefile: {path}"

        content = _read_file(path)
        assert "module.mk" in content, "Makefile must include module.mk"
        assert "MODULE_NAME" in content, "Makefile must define MODULE_NAME"


# ═══════════════════════════════════════════════════════════════════
# TEST: htpasswd consistency
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
class TestGateStatusPageHtpasswdConsistency:
    """Gate: all nginx vhost configs use .htpasswd-platform, no .htpasswd-monitoring."""

    def test_no_htpasswd_monitoring_in_nginx_configs(self):
        """No nginx vhost config references .htpasswd-monitoring."""
        nginx_config_dir = PROJECT_ROOT / "core" / "modules" / "nginx" / "config"
        conf_files = list(nginx_config_dir.glob("*.conf"))

        violations = []
        for cf in conf_files:
            content = _read_file(cf)
            if ".htpasswd-monitoring" in content:
                violations.append(cf.name)

        assert len(violations) == 0, (
            f"Found .htpasswd-monitoring in nginx configs (should be .htpasswd-platform): {violations}"
        )

    def test_htpasswd_platform_in_prometheus_loki(self):
        """Prometheus and Loki vhosts use .htpasswd-platform."""
        prom = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "prometheus-vhost.conf")
        loki = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "loki-vhost.conf")

        assert ".htpasswd-platform" in prom, "prometheus-vhost.conf must use .htpasswd-platform"
        assert ".htpasswd-platform" in loki, "loki-vhost.conf must use .htpasswd-platform"


# ═══════════════════════════════════════════════════════════════════
# TEST: nginx stealth config
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
class TestGateStatusPageNginxStealth:
    """Gate: nginx config contains stealth 444 and auth directives."""

    def test_nginx_stealth_return_444(self):
        """platform-default.conf.template contains return 444 and @stealth_drop."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "platform-default.conf.template")

        assert "return 444" in content, "Missing 'return 444' — stealth connection close"
        assert "@stealth_drop" in content, "Missing '@stealth_drop' named location"
        assert "access_log off" in content, "Missing 'access_log off' for stealth location"

    def test_nginx_login_location(self):
        """platform-default.conf.template has /login/ location with auth_basic."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "platform-default.conf.template")

        assert "location = /login/" in content, "Missing /login/ location"
        assert "auth_basic" in content, "Missing auth_basic directive"

    def test_nginx_htpasswd_mount(self):
        """nginx docker-compose.base.yml mounts .htpasswd-platform."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "docker-compose.base.yml")

        assert ".htpasswd-platform" in content, "nginx compose must mount .htpasswd-platform volume"


# ═══════════════════════════════════════════════════════════════════
# TEST: secrets registration
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
class TestGateStatusPageSecretsRegistered:
    """Gate: PLATFORM_MASTER_EMAIL/PASSWORD registered in secrets-manifest.yaml."""

    def test_master_email_registered(self):
        """PLATFORM_MASTER_EMAIL in secrets-manifest.yaml."""
        manifest_path = PROJECT_ROOT / "core" / "secrets-manifest.yaml"
        content = yaml.safe_load(_read_file(manifest_path))

        secrets = content.get("secrets", [])
        names = [s["name"] for s in secrets]

        assert "PLATFORM_MASTER_EMAIL" in names, (
            f"PLATFORM_MASTER_EMAIL not found in secrets-manifest.yaml. Found: {names}"
        )

        email_entry = next(s for s in secrets if s["name"] == "PLATFORM_MASTER_EMAIL")
        assert email_entry.get("tier") == "required", "PLATFORM_MASTER_EMAIL tier should be required"
        assert "nginx" in email_entry.get("consumers", []), "nginx should consume PLATFORM_MASTER_EMAIL"
        assert "status-page" in email_entry.get("consumers", []), "status-page should consume PLATFORM_MASTER_EMAIL"

    def test_master_password_registered(self):
        """PLATFORM_MASTER_PASSWORD in secrets-manifest.yaml."""
        manifest_path = PROJECT_ROOT / "core" / "secrets-manifest.yaml"
        content = yaml.safe_load(_read_file(manifest_path))

        secrets = content.get("secrets", [])
        names = [s["name"] for s in secrets]

        assert "PLATFORM_MASTER_PASSWORD" in names, (
            f"PLATFORM_MASTER_PASSWORD not found in secrets-manifest.yaml. Found: {names}"
        )

        pass_entry = next(s for s in secrets if s["name"] == "PLATFORM_MASTER_PASSWORD")
        assert pass_entry.get("tier") == "required", "PLATFORM_MASTER_PASSWORD tier should be required"

    def test_env_example_has_master_creds(self):
        """.env.example contains PLATFORM_MASTER_EMAIL and PLATFORM_MASTER_PASSWORD."""
        content = _read_file(PROJECT_ROOT / ".env.example")

        assert "PLATFORM_MASTER_EMAIL" in content, ".env.example missing PLATFORM_MASTER_EMAIL"
        assert "PLATFORM_MASTER_PASSWORD" in content, ".env.example missing PLATFORM_MASTER_PASSWORD"


# ═══════════════════════════════════════════════════════════════════
# TEST: CI negative — R5 anti-survivorship
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
class TestGateStatusPageCiNegative:
    """Gate: R5 — negative test for /health without auth (should get 444, not 200)."""

    def test_nginx_config_blocks_unauthorized(self):
        """nginx config blocks requests without Authorization header (return 444)."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "platform-default.conf.template")

        # Verify the stealth logic: empty Authorization → 444
        assert '$http_authorization = ""' in content or '$http_authorization =""' in content, (
            "Missing check for empty Authorization header"
        )
        # Verify that successful auth leads to proxy_pass, NOT free access
        assert "proxy_pass http://$upstream_status_page" in content, "Missing proxy_pass for authenticated requests"

    def test_negative_no_auth_returns_444(self):
        """Configuration enforces: no auth → 444 (connection close, no response)."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "platform-default.conf.template")

        # Extract the location / block to verify the logic chain
        # No Authorization → 444 (stealth drop, no response)
        # Wrong Authorization → 444 (via @stealth_drop)
        # Correct Authorization → proxy_pass to status-page
        assert "return 444" in content
        assert "@stealth_drop" in content
        assert "error_page 401 = @stealth_drop" in content


# ═══════════════════════════════════════════════════════════════════
# TEST: .dockerignore symlink
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
class TestGateStatusPageDockerignoreSymlink:
    """Gate: .dockerignore is a symlink to templates/.dockerignore."""

    def test_dockerignore_is_symlink(self):
        """.dockerignore in status-page is a symlink."""
        path = PROJECT_ROOT / "core" / "modules" / "status-page" / ".dockerignore"
        assert path.exists(), f"Missing .dockerignore: {path}"
        assert path.is_symlink(), f".dockerignore must be a symlink, got regular file: {path}"

    def test_dockerignore_points_to_template(self):
        """.dockerignore symlink points to ../../templates/.dockerignore."""
        path = PROJECT_ROOT / "core" / "modules" / "status-page" / ".dockerignore"
        target = path.resolve()

        template_path = (PROJECT_ROOT / "core" / "templates" / ".dockerignore").resolve()
        assert target == template_path, f".dockerignore should point to {template_path}, got {target}"
