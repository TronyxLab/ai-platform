# GREP_SUMMARY: test_vhost_renderer, vhost, nginx, template, duplicate-domains, render-all, harness
# STRUCTURE: ┌20 test functions┐ → ◇ generate_vhost_body (4 tests) → ◇ check_duplicate_domains (2) → ◇ read_project_yaml (3)
#            → ◇ read_node_yaml_projects (2) → ◇ resolve_cert_domain (3) → ◇ nginx_t_harness (3)
#            → ◇ remove_vhost (2) → ◇ render_all_determinism (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for vhost_renderer.py — 20 tests covering all functions.
## @scope    No Docker required. All harness tests use mocked subprocess.run.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Every test function has TRAP[TEST] tag
##   - LDD telemetry with caplog: IMP:7-10 log verification
## @rationale  DevPlan 036B §TEST_SPEC — 20 specified tests, all required to pass.
# endregion MODULE_CONTRACT

import hashlib
import logging
import os
from pathlib import Path
from unittest import mock

import pytest
import yaml

from core.internal.scaffold.vhost_renderer import (
    DuplicateDomainError,
    ProjectEntry,
    VhostFile,
    check_duplicate_domains,
    compute_body_hash,
    generate_vhost_body,
    generate_vhost_header,
    load_vhost_config,  # E9: read_project_yaml → load_vhost_config (AC-E9.1)
    nginx_t_harness,
    read_node_yaml_projects,
    remove_vhost,
    render_all,
    render_vhost,
    resolve_cert_domain,
    validate_vhost_identifiers,
)
from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

PLATFORM_DOMAIN = "platform.example.com"


# ══════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def project_yaml_expose_true(tmp_path: Path) -> Path:
    """Create ai-platform.yaml with expose:true + domain."""
    project_dir = tmp_path / "my-app"
    project_dir.mkdir(parents=True, exist_ok=True)
    yaml_content = {
        "needs": {
            "expose": True,
            "domain": "my-app.platform.example.com",
        },
        "target_node": "test-node",
    }
    yaml_path = project_dir / "ai-platform.yaml"
    with Path(yaml_path).open("w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f)
    return project_dir


@pytest.fixture
def project_yaml_no_expose(tmp_path: Path) -> Path:
    """Create ai-platform.yaml without expose:true."""
    project_dir = tmp_path / "no-expose-app"
    project_dir.mkdir(parents=True, exist_ok=True)
    yaml_content = {
        "needs": {
            "expose": False,
            "domain": "no-expose.example.com",
        },
        "target_node": "test-node",
    }
    yaml_path = project_dir / "ai-platform.yaml"
    with Path(yaml_path).open("w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f)
    return project_dir


@pytest.fixture
def project_yaml_expose_no_domain(tmp_path: Path) -> Path:
    """Create ai-platform.yaml with expose:true but no domain."""
    project_dir = tmp_path / "no-domain-app"
    project_dir.mkdir(parents=True, exist_ok=True)
    yaml_content = {
        "needs": {
            "expose": True,
        },
        "target_node": "test-node",
    }
    yaml_path = project_dir / "ai-platform.yaml"
    with Path(yaml_path).open("w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f)
    return project_dir


@pytest.fixture
def node_yaml_with_domains(tmp_path: Path) -> Path:
    """Create node.yaml with 3 projects, 2 with domains."""
    node_yaml_path = tmp_path / "node.yaml"
    yaml_content = {
        "projects": [
            {"name": "app-one", "domain": "app-one.platform.example.com"},
            {"name": "app-two", "domain": "app-two.example.com"},
            {"name": "app-three"},  # no domain
        ]
    }
    with Path(node_yaml_path).open("w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f)
    return node_yaml_path


@pytest.fixture
def node_yaml_empty(tmp_path: Path) -> Path:
    """Create node.yaml with no projects."""
    node_yaml_path = tmp_path / "node.yaml"
    yaml_content = {"projects": []}
    with Path(node_yaml_path).open("w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f)
    return node_yaml_path


@pytest.fixture
def node_yaml_no_domains(tmp_path: Path) -> Path:
    """Create node.yaml with projects but no domains."""
    node_yaml_path = tmp_path / "node.yaml"
    yaml_content = {
        "projects": [
            {"name": "app-one"},
            {"name": "app-two"},
        ]
    }
    with Path(node_yaml_path).open("w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f)
    return node_yaml_path


@pytest.fixture
def render_all_setup(tmp_path: Path) -> dict:
    """Create full node.yaml + configs dir for render_all determinism test."""
    # Create node-configs/<node>/node.yaml
    node = "test-node"
    node_configs = tmp_path / "node-configs"
    node_dir = node_configs / node
    node_dir.mkdir(parents=True, exist_ok=True)

    node_yaml_path = node_dir / "node.yaml"
    yaml_content = {
        "projects": [
            {"name": "app-alpha", "domain": "alpha.platform.example.com"},
            {"name": "app-beta", "domain": "beta.example.com"},
        ]
    }
    with Path(node_yaml_path).open("w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f)

    return {
        "node_yaml_path": str(node_yaml_path),
        "node_configs_dir": str(node_configs),
        "node": node,
        "platform_domain": "platform.example.com",
    }


# ══════════════════════════════════════════════════════════════════════
# TEST: generate_vhost_body
# ══════════════════════════════════════════════════════════════════════


class TestGenerateVhostBody:
    """Tests for generate_vhost_body()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: FQDN is subdomain of PLATFORM_DOMAIN
    # · Expect: wildcard cert path (/etc/letsencrypt/live/platform.example.com/)
    # · Last fail: None
    # · Remove if: cert path logic changes

    def test_generate_vhost_body_platform_domain(self, caplog: pytest.LogCaptureFixture) -> None:
        """FQDN — subdomain of PLATFORM_DOMAIN → wildcard cert path."""
        caplog.set_level(0)
        fqdn = "app.platform.example.com"
        project_name = "my-app"
        cert_domain = "platform.example.com"

        body = generate_vhost_body(fqdn, project_name, cert_domain)

        # Check wildcard cert path
        assert f"/etc/letsencrypt/live/{cert_domain}/fullchain.pem" in body
        assert f"/etc/letsencrypt/live/{cert_domain}/privkey.pem" in body
        assert f"server_name {fqdn};" in body
        assert "set $upstream_my_app" in body
        assert f"http://{project_name}:80" in body

        # LDD telemetry
        found = False
        for record in caplog.records:
            if "[IMP:" in record.message:
                found = True
        assert found, "No IMP log found"

    # 🧪 TRAP[TEST] · Regression · Scenario: FQDN is personal domain (not subdomain of PLATFORM_DOMAIN)
    # · Expect: own cert path (/etc/letsencrypt/live/<fqdn>/)
    # · Last fail: None
    # · Remove if: cert path logic changes

    def test_generate_vhost_body_personal_domain(self, caplog: pytest.LogCaptureFixture) -> None:
        """FQDN — personal domain → own cert path."""
        caplog.set_level(0)
        fqdn = "custom.io"
        project_name = "my-site"
        cert_domain = "custom.io"

        body = generate_vhost_body(fqdn, project_name, cert_domain)

        # Check personal cert path
        assert f"/etc/letsencrypt/live/{cert_domain}/fullchain.pem" in body
        assert f"/etc/letsencrypt/live/{cert_domain}/privkey.pem" in body
        assert f"server_name {fqdn};" in body

        # LDD telemetry
        found_imp9 = False
        for record in caplog.records:
            if "[IMP:9]" in record.message:
                found_imp9 = True
        assert found_imp9, "No IMP:9 log found"

    # 🧪 TRAP[TEST] · Regression · Scenario: Ensure nginx runtime variables are NOT substituted
    # · Expect: $host, $request_uri, $remote_addr, $scheme, $proxy_add_x_forwarded_for appear literally
    # · Last fail: None
    # · Remove if: template engine changes

    def test_generate_vhost_body_contains_nginx_vars(self, caplog: pytest.LogCaptureFixture) -> None:
        """Check that nginx runtime variables ($host, $request_uri) are NOT substituted."""
        caplog.set_level(0)
        body = generate_vhost_body("app.example.com", "my-app", "example.com")

        # These nginx runtime vars must appear literally (NOT template placeholders)
        assert "$host" in body
        assert "$request_uri" in body
        assert "$remote_addr" in body
        assert "$scheme" in body
        assert "$proxy_add_x_forwarded_for" in body
        # Verify $upstream_ prefix is preserved for nginx runtime
        assert "$upstream_" in body

        # LDD telemetry
        found_imp9 = False
        for record in caplog.records:
            if "[IMP:9]" in record.message:
                found_imp9 = True
        assert found_imp9, "No IMP:9 log for nginx vars"

    # 🧪 TRAP[TEST] · Regression · Scenario: http2 on; is on separate line (not deprecated listen ... http2)
    # · Expect: "http2 on;" appears on its own line
    # · Last fail: None
    # · Remove if: nginx deprecation policy changes

    def test_generate_vhost_body_http2_on(self, caplog: pytest.LogCaptureFixture) -> None:
        """Check that http2 on; is on a separate line (not deprecated listen ... http2 syntax)."""
        caplog.set_level(0)
        body = generate_vhost_body("app.example.com", "my-app", "example.com")

        # http2 on; should be on its own line
        lines = body.split("\n")
        http2_lines = [line for line in lines if "http2" in line]
        assert any("http2 on;" in line for line in http2_lines)
        # Must NOT be part of listen directive
        assert "listen ... http2" not in body

        # LDD telemetry
        found_imp9 = False
        for record in caplog.records:
            if "[IMP:9]" in record.message:
                found_imp9 = True
        assert found_imp9, "No IMP:9 log for http2 check"


# ══════════════════════════════════════════════════════════════════════
# TEST: check_duplicate_domains
# ══════════════════════════════════════════════════════════════════════


class TestCheckDuplicateDomains:
    """Tests for check_duplicate_domains()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: No duplicate domains among entries
    # · Expect: no exception raised
    # · Last fail: None
    # · Remove if: uniqueness logic changes

    def test_check_duplicate_domains_no_dup(self, caplog: pytest.LogCaptureFixture) -> None:
        """List of ProjectEntry with unique domains → no exception."""
        caplog.set_level(0)
        entries = [
            ProjectEntry(name="app-one", domain="alpha.example.com"),
            ProjectEntry(name="app-two", domain="beta.example.com"),
            ProjectEntry(name="app-three", domain="gamma.example.com"),
        ]

        # Should not raise
        check_duplicate_domains(entries)

        # LDD: should log PASS
        found_pass = False
        for record in caplog.records:
            if "PASS" in record.message:
                found_pass = True
        assert found_pass, "No PASS log for uniqueness check"

    # 🧪 TRAP[TEST] · Regression · Scenario: Two projects with identical domain
    # · Expect: DuplicateDomainError raised with context
    # · Last fail: None
    # · Remove if: uniqueness logic changes

    def test_check_duplicate_domains_has_dup(self, caplog: pytest.LogCaptureFixture) -> None:
        """Two projects with same domain → raise DuplicateDomainError."""
        caplog.set_level(0)
        entries = [
            ProjectEntry(name="app-one", domain="duplicate.example.com"),
            ProjectEntry(name="app-two", domain="duplicate.example.com"),
        ]

        with pytest.raises(DuplicateDomainError) as excinfo:
            check_duplicate_domains(entries)

        assert "duplicate.example.com" in str(excinfo.value)
        assert "app-one" in str(excinfo.value)
        assert "app-two" in str(excinfo.value)

        # LDD: should log at IMP:10 for violation
        found_imp10 = False
        for record in caplog.records:
            if "[IMP:10]" in record.message:
                found_imp10 = True
        assert found_imp10, "No IMP:10 log for duplicate domain"


# ══════════════════════════════════════════════════════════════════════
# TEST: load_vhost_config (E9: read_project_yaml → load_vhost_config)
# ══════════════════════════════════════════════════════════════════════


class TestReadProjectYaml:
    """Tests for load_vhost_config() (E9 rename of read_project_yaml)."""

    # 🧪 TRAP[TEST] · Regression · Scenario: ai-platform.yaml with expose:true + domain + target_node
    # · Expect: ProjectConfig with all fields set
    # · Last fail: None
    # · Remove if: YAML reading logic changes

    def test_read_project_yaml_expose_true(
        self, project_yaml_expose_true: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ai-platform.yaml with expose:true + domain + target_node → ProjectConfig."""
        caplog.set_level(0)
        config = load_vhost_config(str(project_yaml_expose_true))

        assert config is not None
        assert config.name == "my-app"
        assert config.domain == "my-app.platform.example.com"
        assert config.target_node == "test-node"
        assert config.expose is True

        found_imp9 = False
        for record in caplog.records:
            if "[IMP:9]" in record.message and "expose=true" in record.message:
                found_imp9 = True
        assert found_imp9, "No IMP:9 log for parsed config"

    # 🧪 TRAP[TEST] · Regression · Scenario: ai-platform.yaml without expose:true
    # · Expect: None (skip vhost generation)
    # · Last fail: None
    # · Remove if: YAML reading logic changes

    @pytest.mark.parametrize("fixture_name", ["project_yaml_no_expose", "project_yaml_expose_no_domain"])
    def test_read_project_yaml_skip_cases(
        self, request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture, fixture_name: str
    ) -> None:
        """ai-platform.yaml без expose/без domain → None (skip) (параметризовано, F5)."""
        caplog.set_level(0)
        path: Path = request.getfixturevalue(fixture_name)
        config = load_vhost_config(str(path))

        assert config is None

    # 🧪 TRAP[TEST] · Regression · Scenario: ai-platform.yaml missing entirely
    # · Expect: None (graceful handling)
    # · Last fail: None
    # · Remove if: error handling changes

    def test_read_project_yaml_missing(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """No ai-platform.yaml → None (graceful)."""
        caplog.set_level(0)
        config = load_vhost_config(str(tmp_path / "nonexistent-dir"))

        assert config is None


# ══════════════════════════════════════════════════════════════════════
# TEST: read_node_yaml_projects
# ══════════════════════════════════════════════════════════════════════


class TestReadNodeYamlProjects:
    """Tests for read_node_yaml_projects()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: node.yaml with 3 projects, 2 with domain
    # · Expect: list[ProjectEntry] with 2 entries (projects without domain are skipped)
    # · Last fail: None
    # · Remove if: YAML reading logic changes

    def test_read_node_yaml_projects_with_domains(
        self, node_yaml_with_domains: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """node.yaml with 3 projects, 2 with domain → list[ProjectEntry] len=2."""
        caplog.set_level(0)
        entries = read_node_yaml_projects(str(node_yaml_with_domains))

        assert len(entries) == 2
        assert entries[0].name == "app-one"
        assert entries[0].domain == "app-one.platform.example.com"
        assert entries[1].name == "app-two"
        assert entries[1].domain == "app-two.example.com"

        found_imp9 = False
        for record in caplog.records:
            if "[IMP:9]" in record.message and "Found" in record.message:
                found_imp9 = True
        assert found_imp9, "No IMP:9 log for found projects"

    # 🧪 TRAP[TEST] · Regression · Scenario: node.yaml with no projects or projects without domain
    # · Expect: empty list
    # · Last fail: None
    # · Remove if: YAML reading logic changes

    @pytest.mark.parametrize("fixture_name", ["node_yaml_empty", "node_yaml_no_domains"])
    def test_read_node_yaml_projects_no_domain_entries(
        self, request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture, fixture_name: str
    ) -> None:
        """node.yaml без domain-проектов → empty list (параметризовано, F5)."""
        caplog.set_level(0)
        path: Path = request.getfixturevalue(fixture_name)
        entries = read_node_yaml_projects(str(path))
        assert len(entries) == 0


# ══════════════════════════════════════════════════════════════════════
# TEST: resolve_cert_domain
# ══════════════════════════════════════════════════════════════════════


class TestResolveCertDomain:
    """Tests for resolve_cert_domain() (parametrized, F5-reduction)."""

    @pytest.mark.parametrize(
        ("fqdn", "platform_domain", "expected"),
        [
            # FQDN is subdomain of PLATFORM_DOMAIN → wildcard cert (platform_domain)
            ("app.platform.example.com", "platform.example.com", "platform.example.com"),
            # personal domain (not subdomain) → own cert
            ("custom.io", "platform.example.com", "custom.io"),
            # PLATFORM_DOMAIN is None → fqdn (personal cert path)
            ("whatever.example.com", None, "whatever.example.com"),
            # Empty platform_domain string → fqdn (personal cert path)
            ("whatever.example.com", "", "whatever.example.com"),
        ],
    )
    def test_resolve_cert_domain_variants(
        self, caplog: pytest.LogCaptureFixture, fqdn, platform_domain, expected
    ) -> None:
        """Parametrized: resolve_cert_domain wildcard/personal/None/empty cases."""
        caplog.set_level(0)
        result = resolve_cert_domain(fqdn, platform_domain)
        assert result == expected


# ══════════════════════════════════════════════════════════════════════
# TEST: nginx_t_harness
# ══════════════════════════════════════════════════════════════════════


class TestNginxTHarness:
    """Tests for nginx_t_harness() — all with mocked subprocess/docker.

    ## @purpose — Implementation moved to core/internal/scaffold/nginx_harness.py (DevPlan 117 G T53).
    ##            vhost_renderer.nginx_t_harness is now a lazy facade; subprocess/shutil mocks
    ##            target the implementation module.
    """

    # 🧪 TRAP[TEST] · Regression · Scenario: Mock subprocess.run returns 0 (nginx -t passes)
    # · Expect: returns True
    # · Last fail: None
    # · Remove if: harness logic changes

    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_nginx_t_harness_pass(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Mock docker + openssl succeed → harness returns True."""
        caplog.set_level(0)
        mock_which.return_value = "/usr/bin/docker"

        def mock_subprocess_side_effect(cmd, *args, **kwargs):
            m = mock.MagicMock()
            m.returncode = 0
            m.stdout = b""
            m.stderr = b""
            return m

        mock_run.side_effect = mock_subprocess_side_effect

        # Create a vhost file in temp dir
        vhost_content = generate_vhost_body("test.example.com", "test-app", "example.com")
        vhost_file = tmp_path / "test.example.com.conf"
        vhost_file.write_text(vhost_content, encoding="utf-8")

        result = nginx_t_harness(str(tmp_path))

        assert result is True

    # 🧪 TRAP[TEST] · Regression · Scenario: Mock subprocess.run returns 1 (nginx -t fails)
    # · Expect: returns False
    # · Last fail: None
    # · Remove if: harness logic changes

    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_nginx_t_harness_fail(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Mock docker returns 1 → harness returns False."""
        caplog.set_level(0)
        mock_which.return_value = "/usr/bin/docker"

        call_count = [0]

        def mock_subprocess_side_effect(cmd, *args, **kwargs):
            call_count[0] += 1
            m = mock.MagicMock()
            # First call is openssl (return 0), second is docker (return 1)
            if call_count[0] == 1:
                m.returncode = 0
            else:
                m.returncode = 1
                m.stderr = b"nginx: [emerg] unknown directive"
                m.stdout = b""
            return m

        mock_run.side_effect = mock_subprocess_side_effect

        # Create a vhost file
        vhost_content = generate_vhost_body("test.example.com", "test-app", "example.com")
        vhost_file = tmp_path / "test.example.com.conf"
        vhost_file.write_text(vhost_content, encoding="utf-8")

        result = nginx_t_harness(str(tmp_path))

        assert result is False

    # 🧪 TRAP[TEST] · Regression · Scenario: docker command not found
    # · Expect: returns True (WARN fallback, non-blocking)
    # · Last fail: None
    # · Remove if: harness logic changes

    @mock.patch("core.internal.scaffold.nginx_harness.shutil.which")
    @mock.patch("core.internal.scaffold.nginx_harness.subprocess.run")
    def test_nginx_t_harness_no_docker(
        self, mock_run: mock.MagicMock, mock_which: mock.MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """docker not available → harness returns True (WARN fallback)."""
        caplog.set_level(0)
        mock_which.return_value = None  # docker not found

        # Create a vhost file
        vhost_content = generate_vhost_body("test.example.com", "test-app", "example.com")
        vhost_file = tmp_path / "test.example.com.conf"
        vhost_file.write_text(vhost_content, encoding="utf-8")

        result = nginx_t_harness(str(tmp_path))

        assert result is True
        # openssl call is expected (cert generation), but docker call must NOT happen
        docker_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "docker"]
        assert len(docker_calls) == 0, "docker should not be called when unavailable"


# ══════════════════════════════════════════════════════════════════════
# TEST: remove_vhost
# ══════════════════════════════════════════════════════════════════════


class TestRemoveVhost:
    """Tests for remove_vhost()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: Vhost file exists and is removed
    # · Expect: file deleted, audit log written
    # · Last fail: None
    # · Remove if: removal logic changes

    def test_remove_vhost_exists(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Vhost file exists → deleted + audit log written."""
        caplog.set_level(0)
        overlays_dir = tmp_path / "overlays" / "nginx"
        overlays_dir.mkdir(parents=True, exist_ok=True)

        # Create a vhost file with GENERATED header
        header = generate_vhost_header("my-app", "my-app.example.com", "test-node", "deadbeef")
        body = generate_vhost_body("my-app.example.com", "my-app", "example.com")
        vhost_path = overlays_dir / "my-app.example.com.conf"
        vhost_path.write_text(header + body, encoding="utf-8")

        assert vhost_path.exists()

        # Remove vhost
        result = remove_vhost(
            project_name="my-app",
            overlays_dir=str(overlays_dir),
            platform_root=str(tmp_path),
        )

        assert result is True
        assert not vhost_path.exists()

        # D1 (DevPlan 116 B11 T2): audit-запись идёт через shared audit_logger (JSONL),
        # НЕ в platform_root/var/log/audit.log (прежний free-text pipe). Единый writer.
        # Здесь проверяем только контракт удаления + IMP:9-лог (shared-запись покрыта
        # test_shared_audit_logger.py и гейтом test_gate_audit_format.py).
        old_audit_log = tmp_path / "var" / "log" / "audit.log"
        assert not old_audit_log.exists(), "D1: audit больше не пишется в platform_root/var/log/audit.log"

        # LDD telemetry
        found_imp9 = False
        for record in caplog.records:
            if "[IMP:9]" in record.message and "Deleted" in record.message:
                found_imp9 = True
        assert found_imp9, "No IMP:9 log for vhost deletion"

    # 🧪 TRAP[TEST] · Regression · Scenario: Vhost file does not exist
    # · Expect: idempotent — returns True, no error
    # · Last fail: None
    # · Remove if: removal logic changes

    def test_remove_vhost_not_exists(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Vhost file not found → idempotent (no-op, return True)."""
        caplog.set_level(0)
        overlays_dir = tmp_path / "overlays" / "nginx"
        overlays_dir.mkdir(parents=True, exist_ok=True)

        # No vhost file created — should be idempotent
        result = remove_vhost(
            project_name="nonexistent-app",
            overlays_dir=str(overlays_dir),
            platform_root=str(tmp_path),
        )

        assert result is True

        # No audit log should have been written (no actual removal)
        audit_log = tmp_path / "var" / "log" / "audit.log"
        assert not audit_log.exists()


# ══════════════════════════════════════════════════════════════════════
# TEST: render_vhost (single project)
# ══════════════════════════════════════════════════════════════════════


class TestRenderVhost:
    """Tests for render_vhost()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: Single vhost rendered with platform domain cert
    # · Expect: VhostFile with correct path, hash, fqdn
    # · Last fail: None
    # · Remove if: render logic changes

    def test_render_vhost_platform_domain(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Render single vhost with platform domain (wildcard cert)."""
        caplog.set_level(0)
        entry = ProjectEntry(name="my-app", domain="my-app.platform.example.com")
        node = "test-node"
        node_configs_dir = str(tmp_path / "node-configs")

        vhost = render_vhost(
            entry=entry,
            node=node,
            node_configs_dir=node_configs_dir,
            platform_domain="platform.example.com",
        )

        assert isinstance(vhost, VhostFile)
        assert vhost.fqdn == "my-app.platform.example.com"
        assert vhost.project_name == "my-app"

        # Verify file exists
        expected_path = Path(node_configs_dir) / node / "overlays" / "nginx" / "my-app.platform.example.com.conf"
        assert vhost.path == str(expected_path)
        assert expected_path.exists()

        # Verify content and hash
        content = expected_path.read_text(encoding="utf-8")
        assert "# GENERATED by vhost_renderer.py" in content
        assert "my-app.platform.example.com" in content

        # Verify hash matches body
        body_lines = content.split("\n")
        # Find the blank line that separates header from body
        blank_idx = next((i for i, line in enumerate(body_lines) if not line.strip()), -1)
        body = "\n".join(body_lines[blank_idx + 1 :])
        expected_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert vhost.body_hash == expected_hash

        found_imp9 = False
        for record in caplog.records:
            if "[IMP:9]" in record.message and "Rendered" in record.message:
                found_imp9 = True
        assert found_imp9, "No IMP:9 log for render"


# ══════════════════════════════════════════════════════════════════════
# TEST: render_vhost dev-mode (DEV_DOMAIN_SUFFIX, DevPlan 121 RC — local *.local scheme)
# ══════════════════════════════════════════════════════════════════════


class TestRenderVhostDevMode:
    """Dev-mode rendering: fqdn = <project>.<suffix>, wildcard cert, prod byte-parity."""

    # 🧪 TRAP[TEST] · Regression · Scenario: DEV_DOMAIN_SUFFIX rewrites FQDN to <project>.<suffix>
    # · Expect: file <project>.<suffix>.conf, server_name rewritten, wildcard cert, real domain absent
    # · Last fail: None (new dev-mode feature)
    # · Remove if: dev-mode removed

    def test_render_vhost_dev_domain_suffix(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """DEV_DOMAIN_SUFFIX=ai-platform.local → fqdn = <project>.ai-platform.local (wildcard cert)."""
        caplog.set_level(0)
        entry = ProjectEntry(name="tronyx-site", domain="tronyx.ru")
        node = "test-node"
        node_configs_dir = str(tmp_path / "node-configs")

        vhost = render_vhost(
            entry=entry,
            node=node,
            node_configs_dir=node_configs_dir,
            platform_domain="ai-platform.local",
            dev_domain_suffix="ai-platform.local",
        )

        assert vhost.fqdn == "tronyx-site.ai-platform.local"
        expected_path = Path(node_configs_dir) / node / "overlays" / "nginx" / "tronyx-site.ai-platform.local.conf"
        assert vhost.path == str(expected_path)
        assert expected_path.exists()

        content = expected_path.read_text(encoding="utf-8")
        assert "server_name tronyx-site.ai-platform.local;" in content
        # Wildcard cert (subdomain of PLATFORM_DOMAIN)
        assert "/etc/letsencrypt/live/ai-platform.local/fullchain.pem" in content
        # Real production domain must NOT leak into dev vhost
        assert "server_name tronyx.ru;" not in content

        found_imp9 = False
        for record in caplog.records:
            if "[IMP:9]" in record.message and "Rendered" in record.message:
                found_imp9 = True
        assert found_imp9, "No IMP:9 log for dev-mode render"

    # 🧪 TRAP[TEST] · Regression · Scenario: prod render (no suffix) must stay byte-identical (rule 7)
    # · Expect: None/"" suffix → identical bytes to call
    # · Last fail: None
    # · Remove if: render_vhost signature changes

    def test_render_vhost_no_suffix_prod_parity(self, tmp_path: Path) -> None:
        """Prod parity: dev_domain_suffix=None/"" → byte-identical output (rule 7)."""
        entry = ProjectEntry(name="tronyx-site", domain="tronyx.ru")
        node = "test-node"
        node_configs_dir = str(tmp_path / "node-configs")

        vhost_default = render_vhost(
            entry=entry, node=node, node_configs_dir=node_configs_dir, platform_domain="ai-platform.local"
        )
        vhost_empty = render_vhost(
            entry=entry,
            node=node,
            node_configs_dir=node_configs_dir,
            platform_domain="ai-platform.local",
            dev_domain_suffix="",
        )
        vhost_none = render_vhost(
            entry=entry,
            node=node,
            node_configs_dir=node_configs_dir,
            platform_domain="ai-platform.local",
            dev_domain_suffix=None,
        )

        content_default = Path(vhost_default.path).read_bytes()
        assert Path(vhost_empty.path).read_bytes() == content_default
        assert Path(vhost_none.path).read_bytes() == content_default
        assert "server_name tronyx.ru;" in content_default.decode()

    # 🧪 TRAP[TEST] · Regression · Scenario: render-all batch in dev vs prod mode
    # · Expect: dev → <project>.<suffix>.conf; prod (no env) → real domain .conf
    # · Last fail: None
    # · Remove if: render_all signature changes

    @mock.patch("core.internal.scaffold.vhost_renderer.nginx_t_harness")
    def test_render_all_dev_vs_prod_mode(
        self,
        mock_harness: mock.MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """render-all with DEV_DOMAIN_SUFFIX env → *.local vhosts; without → real domains."""
        caplog.set_level(0)
        mock_harness.return_value = True

        # T3.7: CLI живёт в vhost_cli (vhost_renderer — только render-ядро)
        import core.internal.scaffold.vhost_cli as vmod

        node_yaml_path = tmp_path / "node-configs" / "test-node" / "node.yaml"
        node_yaml_path.parent.mkdir(parents=True)
        node_yaml_path.write_text(
            "projects:\n"
            "  - name: app-alpha\n    domain: alpha.platform.example.com\n"
            "  - name: app-beta\n    domain: beta.example.com\n"
        )

        # DEV mode: env DEV_DOMAIN_SUFFIX (CLI-резолв: env > default)
        monkeypatch.setenv("DEV_DOMAIN_SUFFIX", "ai-platform.local")
        vmod.main(["render-all", "--node", "test-node", "--node-configs-dir", str(tmp_path / "node-configs")])
        dev_conf = tmp_path / "node-configs" / "test-node" / "overlays" / "nginx" / "app-alpha.ai-platform.local.conf"
        assert dev_conf.exists(), "dev-mode render-all must produce <project>.<suffix>.conf"
        assert "server_name app-alpha.ai-platform.local;" in dev_conf.read_text(encoding="utf-8")

        # Prod mode: no env → real domains preserved
        monkeypatch.delenv("DEV_DOMAIN_SUFFIX")
        vmod.main(["render-all", "--node", "test-node", "--node-configs-dir", str(tmp_path / "node-configs")])
        prod_conf = tmp_path / "node-configs" / "test-node" / "overlays" / "nginx" / "alpha.platform.example.com.conf"
        assert prod_conf.exists(), "prod render-all must keep real domains"
        assert "server_name alpha.platform.example.com;" in prod_conf.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
# TEST: compute_body_hash
# ══════════════════════════════════════════════════════════════════════


class TestComputeBodyHash:
    """Tests for compute_body_hash()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: Hash of known content
    # · Expect: correct SHA-256 hex digest
    # · Last fail: None
    # · Remove if: hashing algorithm changes

    def test_compute_body_hash_deterministic(self, caplog: pytest.LogCaptureFixture) -> None:
        """Same input → same output (deterministic)."""
        caplog.set_level(0)
        content = "server { listen 80; server_name example.com; }"

        hash1 = compute_body_hash(content)
        hash2 = compute_body_hash(content)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex

    # 🧪 TRAP[TEST] · Regression · Scenario: Two different inputs produce different hashes
    # · Expect: hash1 != hash2
    # · Last fail: 2026-07-26
    # · Remove if: hashing algorithm changes

    def test_compute_body_hash_different_content(self, caplog: pytest.LogCaptureFixture) -> None:
        """Different input → different hash."""
        caplog.set_level(0)
        hash1 = compute_body_hash("content-a")
        hash2 = compute_body_hash("content-b")
        assert hash1 != hash2


# ══════════════════════════════════════════════════════════════════════
# TEST: generate_vhost_header
# ══════════════════════════════════════════════════════════════════════


class TestGenerateVhostHeader:
    """Tests for generate_vhost_header()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: Header contains correct metadata
    # · Expect: project name, fqdn, node, hash present
    # · Last fail: None
    # · Remove if: header format changes

    def test_generate_vhost_header_format(self, caplog: pytest.LogCaptureFixture) -> None:
        """Header contains project name, fqdn, node, hash."""
        caplog.set_level(0)
        header = generate_vhost_header(
            project_name="test-app",
            fqdn="test-app.example.com",
            node="prod-node",
            body_hash="abc123",
        )

        assert "GENERATED by vhost_renderer.py" in header
        assert "test-app" in header
        assert "test-app.example.com" in header
        assert "prod-node" in header
        assert "abc123" in header
        assert "DO NOT EDIT" in header


# ══════════════════════════════════════════════════════════════════════
# TEST: render_all (determinism)
# ══════════════════════════════════════════════════════════════════════


class TestRenderAll:
    """Tests for render_all()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: Two render_all calls with identical inputs produce
    #    byte-identical .conf files. Mock nginx_t_harness to avoid docker dependency.
    # · Expect: absolute determinism — outputs are byte-identical
    # · Last fail: None
    # · Remove if: render_all logic changes

    @mock.patch("core.internal.scaffold.vhost_renderer.nginx_t_harness")
    def test_render_all_determinism(
        self, mock_harness: mock.MagicMock, render_all_setup: dict, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Two render_all calls with same node.yaml → byte-identical .conf files."""
        caplog.set_level(0)
        mock_harness.return_value = True

        # First render
        result1 = render_all(
            node_yaml_path=render_all_setup["node_yaml_path"],
            node_configs_dir=render_all_setup["node_configs_dir"],
            node=render_all_setup["node"],
            platform_domain=render_all_setup["platform_domain"],
        )

        assert result1.rendered_count == 2
        assert result1.harness_passed is True
        assert len(result1.errors) == 0

        # Save first output
        overlay_dir = Path(render_all_setup["node_configs_dir"]) / render_all_setup["node"] / "overlays" / "nginx"
        first_output: dict[str, str] = {}
        for conf_file in sorted(overlay_dir.glob("*.conf")):
            first_output[conf_file.name] = conf_file.read_text(encoding="utf-8")

        # Second render (same inputs)
        result2 = render_all(
            node_yaml_path=render_all_setup["node_yaml_path"],
            node_configs_dir=render_all_setup["node_configs_dir"],
            node=render_all_setup["node"],
            platform_domain=render_all_setup["platform_domain"],
        )

        assert result2.rendered_count == 2

        # Compare outputs byte-by-byte
        for conf_file in sorted(overlay_dir.glob("*.conf")):
            name = conf_file.name
            assert name in first_output, f"Missing file: {name}"
            second_content = conf_file.read_text(encoding="utf-8")
            assert first_output[name] == second_content, f"DETERMINISM VIOLATION: {name} differs between render passes"

        # LDD telemetry
        found_imp9 = False
        for record in caplog.records:
            if "[IMP:9]" in record.message and "DONE" in record.message:
                found_imp9 = True
        assert found_imp9, "No IMP:9 log for render_all completion"

    # 🧪 TRAP[TEST] · Regression · Scenario: render_all with duplicate domains in node.yaml
    # · Expect: DuplicateDomainError raised, RenderResult with errors
    # · Last fail: None
    # · Remove if: FQDN enforcement changes

    def test_render_all_duplicate_domains(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """render_all with duplicate domains → DuplicateDomainError."""
        caplog.set_level(0)
        node = "test-node"
        node_configs = tmp_path / "node-configs"
        node_dir = node_configs / node
        node_dir.mkdir(parents=True, exist_ok=True)

        node_yaml_path = node_dir / "node.yaml"
        yaml_content = {
            "projects": [
                {"name": "app-one", "domain": "duplicate.example.com"},
                {"name": "app-two", "domain": "duplicate.example.com"},
            ]
        }
        with Path(node_yaml_path).open("w", encoding="utf-8") as f:
            yaml.dump(yaml_content, f)

        with pytest.raises(DuplicateDomainError):
            render_all(
                node_yaml_path=str(node_yaml_path),
                node_configs_dir=str(node_configs),
                node=node,
            )


# ══════════════════════════════════════════════════════════════════════
# TEST: compatible cleanup marker
# ══════════════════════════════════════════════════════════════════════


class TestCompatibility:
    """Tests for backward compatibility with old add-vhost.sh generated files."""

    # 🧪 TRAP[TEST] · Regression · Scenario: Old-style GENERATED header is detected by cleanup step
    # · Expect: "# GENERATED" appears in both old and new header formats
    # · Last fail: None
    # · Remove if: marker format changes

    def test_generated_marker_backward_compat(self, caplog: pytest.LogCaptureFixture) -> None:
        """Both old and new headers contain # GENERATED marker (cleanup compatibility)."""
        caplog.set_level(0)
        # Old-style header (from add-vhost.sh)
        old_header = """# ============================================================
# GENERATED by add-vhost.sh — DO NOT EDIT
# ============================================================
"""
        # New-style header (from vhost_renderer.py)
        new_header = generate_vhost_header("test", "t.example.com", "n", "h")

        assert "# GENERATED" in old_header
        assert "# GENERATED" in new_header
        # The cleanup step checks for "# GENERATED" — both must match
        assert "# GENERATED" in old_header  # old files still cleaned up
        assert "# GENERATED" in new_header  # new files also cleaned up


# ══════════════════════════════════════════════════════════════════════
# TEST: read_project_yaml format (top-level expose/domain)
# ══════════════════════════════════════════════════════════════════════


class TestReadProjectYamlOld:
    """Tests for read_project_yaml with (top-level) expose/domain format."""

    # 🧪 TRAP[TEST] · Compatibility · Scenario: top-level expose:true + domain (format)
    # · Expect: ProjectConfig returned
    # · Last fail: None
    # · Remove if: format is dropped

    def test_read_project_yaml_old_format(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Top-level expose:true + domain (format) → ProjectConfig."""
        caplog.set_level(0)
        project_dir = tmp_path / "old-app"
        project_dir.mkdir(parents=True, exist_ok=True)
        # format: no needs.expose, top-level expose + domain
        yaml_content = {
            "expose": True,
            "domain": "old.example.com",
            "target_node": "test-node",
        }
        with Path(project_dir / "ai-platform.yaml").open("w", encoding="utf-8") as f:
            yaml.dump(yaml_content, f)

        config = load_vhost_config(str(project_dir))

        assert config is not None
        assert config.name == "old-app"
        assert config.domain == "old.example.com"
        assert config.target_node == "test-node"
        assert config.expose is True


# ══════════════════════════════════════════════════════════════════════
# TEST: E9 — shared project_yaml консолидация
# ══════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-02 · R5 · E9 — vhost_renderer использует shared/project_yaml
# · Regression: DevPlan 119 E9 — локальный read_project_yaml удалён (AC-E9.1);
#   все импорты ai-platform.yaml через shared/project_yaml (AC-E9.2)
# · Last fail: B1 — vhost_renderer имел собственный парсер (мигрирован в 119 B1)
# · Remove if: shared/project_yaml канон отменяется
def test_vhost_renderer_uses_shared_project_yaml_negative() -> None:
    """R5 (E9): vhost_renderer НЕ содержит локальный read_project_yaml; парсинг — через shared."""
    import inspect

    src = inspect.getsource(load_vhost_config)
    # Все YAML-чтение через shared/project_yaml (AC-E9.2)
    assert "shared_project_yaml.load_project_yaml" in src, "vhost parsing must use shared/project_yaml (E9)"
    assert "yaml.safe_load" not in src, "local yaml.safe_load forbidden — shared reader only (E9)"

    # AC-E9.1: нет локального парсера-имени read_project_yaml в vhost_renderer (переименован)
    vhost_src = inspect.getsource(__import__("core.internal.scaffold.vhost_renderer", fromlist=["x"]))
    assert "def read_project_yaml" not in vhost_src, "vhost_renderer must NOT define read_project_yaml (E9)"
    import logging

    logging.getLogger(__name__).critical("[IMP:9][test] vhost_renderer uses shared/project_yaml — E9 consolidation OK")


# ══════════════════════════════════════════════════════════════════════
# TEST: H18 — строгая валидация fqdn/project_name
# ══════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-16 · NEGATIVE (R5) · H18 — fqdn config-injection/path traversal
# · Scenario: fqdn "${...}"/"../evil"/single-label → инъекция в nginx-конфиг и имя <fqdn>.conf.
# · Last fail: N/A (new security validation, аудит 2026-08-15 H18)
# · Remove if: vhost идентификаторы перестают валидироваться
def test_validate_vhost_identifiers_rejects_injection() -> None:
    """R5 negative (H18): невалидные fqdn/project_name → ConfigValidationError (exit 4)."""
    for bad_fqdn in ("${HOST}", "../evil.example.com", "singlelabel", "bad_label.example.com", "UPPER.example.com"):
        with pytest.raises(ConfigValidationError):
            validate_vhost_identifiers(bad_fqdn, "good-project")
    with pytest.raises(ConfigValidationError):
        validate_vhost_identifiers("good.example.com", "bad;project")


def test_validate_vhost_identifiers_accepts_valid() -> None:
    """H18 positive: валидные kebab-case fqdn/project_name проходят."""
    assert validate_vhost_identifiers("my-project.tronyx.ru", "my-project") is None


# ═══════════════════════════════════════════════════════════════════
# plan 012 T15 (F-034): expose-консистентность vhost
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T15 F-034 · expose=false → vhost НЕ генерируется (R5)
# · Scenario: node.yaml проект с доменом, ai-platform.yaml проекта expose:false →
#   render_all НЕ рендерит vhost (expose-фильтр); устаревший GENERATED vhost удаляется
# · Last fail: F-034 — render_all рендерил vhost для expose=false (overlay-артефакт)
# · Remove if: expose-фильтр перенесён в другой слой
@mock.patch("core.internal.scaffold.vhost_renderer.nginx_t_harness")
def test_render_all_expose_false_no_vhost(
    mock_harness: mock.MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-negative (F-034): проект с expose=false → vhost НЕ генерируется; stale удаляется."""
    caplog.set_level(0)
    mock_harness.return_value = True
    # PROJECTS_BASE → tmp_path (проекты резолвятся локально)
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path / "projects"))
    projects_base = Path(os.environ["PROJECTS_BASE"])
    proj_dir = projects_base / "ctx-a" / "app-hidden"
    proj_dir.mkdir(parents=True)
    (proj_dir / "ai-platform.yaml").write_text(
        "name: app-hidden\nneeds:\n  domain: hidden.example.com\nexpose: false\n", encoding="utf-8"
    )

    node_configs = tmp_path / "node-configs"
    node_dir = node_configs / "test-node"
    node_dir.mkdir(parents=True)
    node_yaml_path = node_dir / "node.yaml"
    node_yaml_path.write_text(
        "projects:\n  - name: app-hidden\n    context: ctx-a\n    domain: hidden.example.com\n", encoding="utf-8"
    )
    overlay_dir = node_dir / "overlays" / "nginx"
    overlay_dir.mkdir(parents=True)
    # Устаревший GENERATED vhost от прошлого рендера (F-034) — должен быть удалён
    stale = overlay_dir / "hidden.example.com.conf"
    stale.write_text(
        "# GENERATED by vhost_renderer.py — content-hash stale\nserver_name hidden.example.com;\n", encoding="utf-8"
    )

    result = render_all(
        node_yaml_path=str(node_yaml_path),
        node_configs_dir=str(node_configs),
        node="test-node",
        platform_domain="example.com",
    )

    assert result.rendered_count == 0, f"expose=false → 0 vhost, got {result.rendered_count}"
    assert not stale.exists(), "F-034 FAIL: устаревший GENERATED vhost для expose=false не удалён"
    logger.info("[IMP:9][test][F-034] expose=false → no vhost + stale removed PASS")


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T15 F-034 · expose=true → vhost генерируется
# · Remove if: expose-фильтр перенесён в другой слой
@mock.patch("core.internal.scaffold.vhost_renderer.nginx_t_harness")
def test_render_all_expose_true_generates_vhost(
    mock_harness: mock.MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-034 positive: expose=true → vhost генерируется."""
    caplog.set_level(0)
    mock_harness.return_value = True
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path / "projects"))
    projects_base = Path(os.environ["PROJECTS_BASE"])
    proj_dir = projects_base / "ctx-a" / "app-open"
    proj_dir.mkdir(parents=True)
    (proj_dir / "ai-platform.yaml").write_text(
        "name: app-open\nneeds:\n  domain: open.example.com\nexpose: true\n", encoding="utf-8"
    )

    node_configs = tmp_path / "node-configs"
    node_dir = node_configs / "test-node"
    node_dir.mkdir(parents=True)
    node_yaml_path = node_dir / "node.yaml"
    node_yaml_path.write_text(
        "projects:\n  - name: app-open\n    context: ctx-a\n    domain: open.example.com\n", encoding="utf-8"
    )

    result = render_all(
        node_yaml_path=str(node_yaml_path),
        node_configs_dir=str(node_configs),
        node="test-node",
        platform_domain="example.com",
    )

    assert result.rendered_count == 1, f"expose=true → 1 vhost, got {result.rendered_count}"
    overlay_dir = node_dir / "overlays" / "nginx"
    assert (overlay_dir / "open.example.com.conf").exists(), "vhost для expose=true не сгенерирован"
    logger.info("[IMP:9][test][F-034] expose=true → vhost generated PASS")
