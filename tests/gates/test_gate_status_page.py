# GREP_SUMMARY: test-gate-status-page module-contract htpasswd-consistency platform-vhost secrets-registered ci-negative dockerignore-symlink disabled-contract
# STRUCTURE: ▶ test_gate_status_page_module_contract → assert module.yaml D4
#            ▶ test_gate_status_page_htpasswd_consistency → assert no .htpasswd-monitoring in nginx configs
#            ▶ test_gate_status_page_nginx_vhost → assert platform-vhost auth + proxy to status-page:8080 + default catch-all return 444
#            ▶ test_gate_status_page_secrets_registered → assert PLATFORM_MASTER_* in secrets-manifest.yaml
#            ▶ test_gate_status_page_ci_negative → assert platform-vhost requires auth + no status-page in default vhost
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
# @rationale  Gate tests prevent regressions: htpasswd drift, nginx platform-vhost auth contract,
#             module contract violations. Negative test ensures auth is enforced on platform subdomain.
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
        return path.read_text(encoding="utf-8")
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
# TEST: nginx platform-vhost config
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
class TestGateStatusPageNginxVhost:
    """Gate: platform-vhost.conf.template contains auth and proxy directives."""

    def test_platform_vhost_exists_and_has_auth(self):
        """platform-vhost.conf.template proxies to status-page with Basic Auth."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "platform-vhost.conf.template")

        assert content, "platform-vhost.conf.template is empty or missing"
        assert "server_name platform.${PLATFORM_DOMAIN}" in content, "Missing server_name platform.${PLATFORM_DOMAIN}"
        assert "auth_basic" in content, "Missing auth_basic directive"
        assert ".htpasswd-platform" in content, "Must use .htpasswd-platform"
        assert "status-page:8080" in content, "Must proxy_pass to status-page:8080"

    def test_platform_vhost_health_location(self):
        """platform-vhost.conf.template has /health location with auth and proxy."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "platform-vhost.conf.template")

        assert "location /health" in content, "Missing /health location"
        assert "proxy_pass" in content, "Missing proxy_pass in /health location"

    def test_platform_default_catch_all_return_444(self):
        """platform-default.conf.template default catch-all returns 444 (not status-page)."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "platform-default.conf.template")

        assert "return 444" in content, "Missing 'return 444' in default catch-all"
        # Verify no status-page proxy in default catch-all (it's now in platform-vhost)
        assert "status-page:8080" not in content, (
            "status-page proxy must NOT be in platform-default.conf.template — moved to platform-vhost"
        )

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
    """Gate: R5 — negative test for platform-vhost requires auth (401 without creds)."""

    def test_platform_vhost_requires_auth(self):
        """platform-vhost.conf.template requires Basic Auth for all paths."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "platform-vhost.conf.template")

        # All locations have auth_basic
        assert "auth_basic" in content, "platform-vhost must have auth_basic"
        assert ".htpasswd-platform" in content, "Must use .htpasswd-platform"
        # No free access — every location has auth
        auth_count = content.count("auth_basic")
        loc_count = content.count("location ")
        assert auth_count >= loc_count, (
            f"auth_basic count ({auth_count}) must be >= location count ({loc_count}) "
            f"— every location must be protected"
        )

    def test_platform_vhost_proxies_to_status_page(self):
        """platform-vhost.conf.template proxies to status-page:8080 on auth success."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "platform-vhost.conf.template")

        assert "proxy_pass http://$upstream_platform" in content, (
            "Missing proxy_pass for authenticated requests in platform-vhost"
        )
        assert "status-page:8080" in content, "Must proxy to status-page:8080"

    def test_default_catch_all_no_status_page(self):
        """platform-default.conf.template default_server must NOT serve status-page."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "nginx" / "config" / "platform-default.conf.template")

        # Status-page is on platform.tronyx.ru subdomain now, not at apex
        assert "status-page:8080" not in content, (
            "status-page proxy must NOT be in platform-default — it's in platform-vhost.conf.template"
        )
        # Default catch-all returns 444
        assert "return 444" in content, "Default catch-all must return 444"


# ═══════════════════════════════════════════════════════════════════
# TEST: crontab contract — no metrics export in backup-cron (DevPlan 066 P2 fix)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.gate
class TestGateStatusPageCrontabContract:
    """Gate: metrics export runs via host cron (cron.d/platform-metrics), NOT container cron.

    Code-presence invariant (DevPlan 116 B3 T1, U-03): the metrics cron installer exists
    (helpers/system.py::install_cron_metrics) AND φ3 phase_platform_setup calls it —
    the assertion is verified against CODE, not docstrings.
    """

    def test_no_metrics_export_in_backup_cron_crontab(self):
        """backup-cron crontab must NOT have platform-export-metrics line.

        Metrics export now runs via host cron — installed by phase_platform_setup (φ3)
        via helpers/system.py::install_cron_metrics (see code-presence test below).
        """
        crontab_path = PROJECT_ROOT / "core" / "modules" / "backup-cron" / "scripts" / "crontab"
        content = _read_file(crontab_path)

        assert content, "crontab file is empty or missing"
        assert "platform-export-metrics" not in content, (
            "platform-export-metrics line must NOT be in backup-cron crontab "
            "(metrics export runs via host cron, not container cron)"
        )

    def test_no_metrics_every_minute_cron_in_backup_cron(self):
        """backup-cron crontab must NOT have every-minute cron pattern for metrics."""
        crontab_path = PROJECT_ROOT / "core" / "modules" / "backup-cron" / "scripts" / "crontab"
        content = _read_file(crontab_path)

        # Check that no line has both '* * * * *' and 'platform-export-metrics'
        lines = content.split("\n")
        for line in lines:
            if "* * * * *" in line:
                assert "platform-export-metrics" not in line, (
                    f"Found every-minute metrics cron line in backup-cron crontab: '{line.strip()}'"
                )

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · metrics cron installer code-presence (DevPlan 116 B3 T1, U-03)
    # · Last fail: N/A (new test — previous gate asserted docstring «installed by node-lifecycle.sh»,
    # ·   which was false: no installer existed anywhere in core)
    # · Remove if: install_cron_metrics / phase_platform_setup contract changes
    def test_phase_platform_setup_calls_install_cron_metrics(self):
        """phase_platform_setup (φ3) invokes install_cron_metrics — code-presence, not docstring.

        AST-based check: import phases.py, locate phase_platform_setup body, assert a Call
        node whose function attribute name is 'install_cron_metrics'. The modulemap docstring
        («docker auth, metrics cron, setup-node») becomes code-truth after this test.
        """
        import ast

        # DevPlan 119 E3: phases.py → phases/ пакет; phase_platform_setup живёт в phases/system.py
        phases_path = PROJECT_ROOT / "core" / "internal" / "bootstrap" / "lifecycle" / "phases" / "system.py"
        assert phases_path.exists(), f"phases/system.py not found: {phases_path}"

        tree = ast.parse(_read_file(phases_path))
        called = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "phase_platform_setup":
                for sub in ast.walk(node):
                    # helpers_system.install_cron_metrics(core_dir) — attribute chain
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "install_cron_metrics"
                    ):
                        called = True
                        break
        assert called, (
            "phase_platform_setup (φ3) must CALL install_cron_metrics (code-presence, "
            "DevPlan 116 B3 T1) — metrics cron installer is missing from the phase"
        )

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · CRON_METRICS_LINE contract (DevPlan 116 B3 T1)
    # · Last fail: N/A (new test)
    # · Remove if: CRON_METRICS_LINE contract changes (flock/timeout/script path)
    def test_cron_metrics_line_contract(self):
        """CRON_METRICS_LINE contains flock -n + timeout 50 + absolute script path.

        Imports helpers/system.py (not a docstring claim): the cron line contract for
        cron.d/platform-metrics — flock prevents overlapping runs, timeout 50s bounds
        execution, absolute script path survives cron.d's minimal PATH.
        """
        import importlib.util

        system_path = PROJECT_ROOT / "core" / "internal" / "bootstrap" / "lifecycle" / "helpers" / "system.py"
        assert system_path.exists(), f"helpers/system.py not found: {system_path}"
        spec = importlib.util.spec_from_file_location("helpers_system_gate", system_path)
        assert spec is not None and spec.loader is not None, f"Cannot build spec for {system_path}"
        system_helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(system_helpers)

        line = system_helpers.CRON_METRICS_LINE
        assert "flock -n" in line, f"CRON_METRICS_LINE missing 'flock -n': {line!r}"
        assert "timeout 50" in line, f"CRON_METRICS_LINE missing 'timeout 50': {line!r}"
        assert "platform-export-metrics.sh" in line, f"CRON_METRICS_LINE missing absolute script path: {line!r}"
        assert "/usr/bin/" in line, "CRON_METRICS_LINE must use absolute binary paths (cron.d minimal PATH)"

        # Absolute script path must resolve via the {core_dir} template
        assert "{core_dir}" in line, "CRON_METRICS_LINE must embed {core_dir} template"


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


# ═════════════════════════════════════════════════════════════════════
# TEST: DISABLED status contract (DevPlan 158 W3 T3.1)
# ═════════════════════════════════════════════════════════════════════


@pytest.mark.gate
class TestGateStatusPageDisabledContract:
    """Gate: DISABLED status exists as 4th status (PASS/WARN/FAIL/DISABLED/UNKNOWN).

    DevPlan 158 W3 T3.1 — ensures collectors (package, W7-E2) and status.html support
    DISABLED status for not-deployed platform services (S-DNS A decision).
    """

    @staticmethod
    def _collectors_package_source() -> str:
        """Read the collectors package source (package, W7-E2) as concatenated text.

        collectors.py → collectors/ пакет (DevPlan 170 W7-E2): сканируются все .py
        подкаталогов (checks/ включён), чтобы паттерны DISABLED/gethostbyname
        находились независимо от точного модуля реализации.
        """
        collectors_dir = PROJECT_ROOT / "core" / "modules" / "status-page" / "collectors"
        return "\n".join(_read_file(path) for path in sorted(collectors_dir.rglob("*.py")))

    def test_collectors_supports_disabled_status(self):
        """collectors package handles DISABLED in platform service checks."""
        content = self._collectors_package_source()
        assert "DISABLED" in content, "collectors package must support DISABLED status (DevPlan 158 W1)"

    def test_template_renders_disabled_badge(self):
        """status.html renders DISABLED with distinct visual (not FAIL)."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "status-page" / "templates" / "status.html")
        assert "disabled" in content.lower(), "status.html must render DISABLED status (DevPlan 158 W2)"
        # Distinct visual: status-dot.disabled OR badge-disabled class
        assert "status-dot disabled" in content or "badge-disabled" in content, (
            "status.html must have distinct DISABLED visual (dot or badge class)"
        )

    def test_collectors_excludes_disabled_from_overall(self):
        """collectors package excludes DISABLED from overall computation (S-WARN A)."""
        content = self._collectors_package_source()
        assert '!= "DISABLED"' in content or "!= 'DISABLED'" in content, (
            "collectors package must exclude DISABLED from overall (DevPlan 158 W1 T1.2, S-WARN A)"
        )

    def test_app_excludes_disabled_from_health_failed(self):
        """app.py /health excludes DISABLED from failed-list (S-HEALTH)."""
        content = _read_file(PROJECT_ROOT / "core" / "modules" / "status-page" / "app.py")
        assert '"DISABLED"' in content or "'DISABLED'" in content, (
            "app.py must exclude DISABLED from /health failed-list (DevPlan 158 W1 T1.4, S-HEALTH)"
        )

    def test_collectors_has_dns_probe(self):
        """collectors package has DNS pre-probe in _check_platform_service (S-DNS A)."""
        content = self._collectors_package_source()
        assert "gethostbyname" in content, (
            "collectors package must have DNS pre-probe via socket.gethostbyname (DevPlan 158 W1 T1.1, S-DNS A)"
        )
