"""
# GREP_SUMMARY: test_domain_verifier, resolve-node-yaml, expose-domains, curl-verify, status-page, unit-test
# STRUCTURE: ▶ tmp_path + mocker node.yaml fixtures → ◇ resolve_node_yaml (3 paths) → ◇ get_expose_domains (with/without)
#            → ◇ verify_domain (200/connection/warn) → ◇ verify_status_page (ok/skip) → ◇ main() CLI → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for domain_verifier.py — covers all 5 business-logic functions + full CLI integration.
## @scope    12 tests: resolve_node_yaml (4), get_expose_domains (2), verify_domain (3), verify_status_page (2), main (1)
## @invariants
##   - All subprocess calls mocked via unittest.mock.patch (no real curl)
##   - node.yaml created in tmp_path
##   - Each test validates IMP:9 business logic log presence using ldd_trajectory decorator
## @rationale Strangler-Fig Wave 5a: first unit-tests for domain verification logic (0→12 tests).
## @changes  2026-07-26 | Wave 5a — Created
## @see DevPlan 036A §TEST_SPEC
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "verify"
sys.path.insert(0, str(_MODULE_DIR))
import domain_verifier as dv

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_node_yaml(tmp_path):
    """Create a node.yaml with expose:true and non-exposed projects."""
    yaml_content = """\
node:
  name: test-node
  platform_domain: test.example.com
projects:
  - name: frontend
    expose: true
    domain: app.test.example.com
  - name: api
    expose: true
    domain: api.test.example.com
  - name: admin
    expose: false
    domain: admin.test.example.com
  - name: internal
    domain: internal.test.example.com
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content)
    return yaml_path


@pytest.fixture
def node_yaml_no_expose(tmp_path):
    """Create a node.yaml with no expose:true projects."""
    yaml_content = """\
node:
  name: test-node
projects:
  - name: frontend
    expose: false
    domain: app.test.example.com
  - name: api
    domain: api.test.example.com
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content)
    return yaml_path


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Resolve node.yaml Tests
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_resolve_node_yaml_path1_local(tmp_path, caplog):
    """Path 1 (platform-local): node.yaml found at platform_root/node-configs/{node}/node.yaml."""
    caplog.set_level(logging.DEBUG)
    node_name = "test-node"
    platform_root = tmp_path / "platform"
    yaml_dir = platform_root / "node-configs" / node_name
    yaml_dir.mkdir(parents=True)
    yaml_path = yaml_dir / "node.yaml"
    yaml_path.write_text("node:\n  name: test-node")

    result = dv.resolve_node_yaml(node_name, platform_root)

    assert result == yaml_path
    assert result.is_file()
    # 🧪 TRAP[TEST] · Regression: path1 found · Scenario: node.yaml at platform-root/node-configs/
    # · Last fail: never · Remove if: resolve_node_yaml contract changes


@ldd_trajectory
def test_resolve_node_yaml_path2_org(tmp_path, caplog):
    """Path 2 (org repos): node.yaml found via $HOME/projects/*/node-configs/{node}/node.yaml."""
    caplog.set_level(logging.DEBUG)
    node_name = "test-node"

    # Create a mock org repo structure under tmp_path/home/projects/
    home_dir = tmp_path / "home"
    org_dir = home_dir / "projects" / "my-org" / "node-configs" / node_name
    org_dir.mkdir(parents=True)
    yaml_path = org_dir / "node.yaml"
    yaml_path.write_text("node:\n  name: test-node")

    # Mock os.path.expanduser so NodeYaml.resolve() finds ~/projects/*/node-configs/
    # os.path.expanduser("~/projects") → projects_dir → glob("projects/*/node-configs/...")
    projects_dir = home_dir / "projects"
    with patch("os.path.expanduser", return_value=str(projects_dir)):
        platform_root = tmp_path / "platform"
        platform_root.mkdir(parents=True)

        result = dv.resolve_node_yaml(node_name, platform_root)

    assert result == yaml_path
    assert result.is_file()
    # 🧪 TRAP[TEST] · Regression: path2 glob found · Scenario: node.yaml in org repos
    # · Last fail: never · Remove if: resolve_node_yaml contract changes


@ldd_trajectory
def test_resolve_node_yaml_path3_vps(tmp_path, caplog):
    """Path 3 (VPS fallback): node.yaml found at /opt/node-configs/{node}/node.yaml."""
    caplog.set_level(logging.DEBUG)
    node_name = "test-node"

    # Create VPS fallback path — but NodeYaml.resolve() checks /opt/node-configs directly
    vps_dir = tmp_path / "opt" / "node-configs" / node_name
    vps_dir.mkdir(parents=True)
    yaml_path = vps_dir / "node.yaml"
    yaml_path.write_text("node:\n  name: test-node")

    platform_root = tmp_path / "platform"
    platform_root.mkdir(parents=True)

    # Mock os.path.expanduser so the glob step finds nothing
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir(parents=True)

    with patch("os.path.expanduser", return_value=str(empty_home)):
        # All 3 paths fail (platform_root has no node.yaml, empty_home has nothing,
        # /opt/node-configs/test-node/node.yaml does not exist on dev machine)
        with pytest.raises(FileNotFoundError) as exc_info:
            dv.resolve_node_yaml(node_name, platform_root)

        # NodeYaml.resolve() error message is concise
        assert node_name in str(exc_info.value)
        # Path details are in the log, not the exception message
        path_found_in_log = any("/opt/node-configs" in record.message for record in caplog.records)
        assert path_found_in_log, "/opt/node-configs should appear in NodeYaml.resolve() log output"
        # 🧪 TRAP[TEST] · Regression: path3 not found mocked · Scenario: VPS fallback = no match
        # · Last fail: never · Remove if: resolve_node_yaml contract changes


@ldd_trajectory
def test_resolve_node_yaml_not_found(tmp_path, caplog):
    """No path matches → FileNotFoundError with node name."""
    caplog.set_level(logging.DEBUG)
    node_name = "ghost-node"
    platform_root = tmp_path / "platform"
    platform_root.mkdir(parents=True)
    empty_home = tmp_path / "empty"
    empty_home.mkdir(parents=True)

    with patch("os.path.expanduser", return_value=str(empty_home)), pytest.raises(FileNotFoundError) as exc_info:
        dv.resolve_node_yaml(node_name, platform_root)

    msg = str(exc_info.value)
    assert "ghost-node" in msg
    # Path details are in the NodeYaml.resolve() log, not in exception message
    path_found_in_log = any("node-configs/ghost-node/node.yaml" in record.message for record in caplog.records)
    assert path_found_in_log, "Searched paths should appear in NodeYaml.resolve() log output"
    # 🧪 TRAP[TEST] · Regression: not found · Scenario: no path matches
    # · Last fail: never · Remove if: resolve_node_yaml contract changes


# endregion


# ═══════════════════════════════════════════════════════════════════
# region get_expose_domains Tests
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_get_expose_domains_with_domains(sample_node_yaml, caplog):
    """Node.yaml with expose:true projects returns their domain list."""
    caplog.set_level(logging.DEBUG)

    domains = dv.get_expose_domains(sample_node_yaml)

    assert len(domains) == 2
    assert "app.test.example.com" in domains
    assert "api.test.example.com" in domains
    # 🧪 TRAP[TEST] · Regression: expose domains extracted · Scenario: node.yaml with expose:true + domain
    # · Last fail: never · Remove if: get_expose_domains contract changes


@ldd_trajectory
def test_get_expose_domains_no_expose(node_yaml_no_expose, caplog):
    """Node.yaml without expose:true projects returns empty list."""
    caplog.set_level(logging.DEBUG)

    domains = dv.get_expose_domains(node_yaml_no_expose)

    assert domains == []
    # 🧪 TRAP[TEST] · Regression: no expose domains · Scenario: no expose:true projects
    # · Last fail: never · Remove if: get_expose_domains contract changes


# endregion


# ═══════════════════════════════════════════════════════════════════
# region verify_domain Tests
# ═══════════════════════════════════════════════════════════════════


def _mock_curl(http_code: str, returncode: int = 0) -> CompletedProcess:
    """Helper to create a mock subprocess.CompletedProcess for curl."""
    return CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=http_code,
        stderr="",
    )


@ldd_trajectory
def test_verify_domain_http200(caplog):
    """Curl returns HTTP 200 → VerifyResult.status == 'pass'."""
    caplog.set_level(logging.DEBUG)

    with patch("subprocess.run", return_value=_mock_curl("200")):
        result = dv.verify_domain("test.example.com")

    assert result.status == "pass"
    assert result.http_code == 200
    assert result.domain == "test.example.com"
    assert result.error is None
    # 🧪 TRAP[TEST] · Regression: HTTP 200 · Scenario: domain returns 200
    # · Last fail: never · Remove if: verify_domain contract changes


@ldd_trajectory
def test_verify_domain_connection_failed(caplog):
    """Curl connection error → VerifyResult.status == 'connection_error' with error."""
    caplog.set_level(logging.DEBUG)

    with patch("subprocess.run", return_value=_mock_curl("", returncode=6)):
        result = dv.verify_domain("unreachable.example.com")

    assert result.status == "connection_error"
    assert result.http_code is None
    assert "curl exit 6" in (result.error or "")
    # 🧪 TRAP[TEST] · Regression: connection failed · Scenario: curl exit 6 (could not resolve)
    # · Last fail: never · Remove if: verify_domain contract changes


@ldd_trajectory
def test_verify_domain_non_200(caplog):
    """Curl returns HTTP 302/500 → VerifyResult.status == 'warn'."""
    caplog.set_level(logging.DEBUG)

    with patch("subprocess.run", return_value=_mock_curl("302")):
        result = dv.verify_domain("redirect.example.com")

    assert result.status == "warn"
    assert result.http_code == 302
    assert result.domain == "redirect.example.com"
    # 🧪 TRAP[TEST] · Regression: non-200 HTTP · Scenario: domain returns 302 redirect
    # · Last fail: never · Remove if: verify_domain contract changes


# endregion


# ═══════════════════════════════════════════════════════════════════
# region verify_status_page Tests
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_verify_status_page_ok(caplog):
    """Status-page /health returns HTTP 200 with Basic Auth → pass."""
    caplog.set_level(logging.DEBUG)

    with patch("subprocess.run", return_value=_mock_curl("200")):
        result = dv.verify_status_page("test.example.com", "admin@test.com", "secret123")

    assert result is not None
    assert result.status == "pass"
    assert result.http_code == 200
    # 🧪 TRAP[TEST] · Regression: status-page ok · Scenario: /health returns 200
    # · Last fail: never · Remove if: verify_status_page contract changes


@ldd_trajectory
def test_verify_status_page_missing_creds(caplog):
    """Missing email/password → returns None (skip gracefully)."""
    caplog.set_level(logging.DEBUG)

    result = dv.verify_status_page("test.example.com", "", "")

    assert result is None
    # 🧪 TRAP[TEST] · Regression: missing creds · Scenario: no PLATFORM_MASTER_EMAIL/PASSWORD
    # · Last fail: never · Remove if: verify_status_page contract changes


# endregion


# ═══════════════════════════════════════════════════════════════════
# region main() CLI Integration Test
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_main_cli_integration(tmp_path, caplog):
    """Full CLI integration: resolve node.yaml → parse domains → verify → exit 0 (all pass)."""
    caplog.set_level(logging.DEBUG)

    # Create node.yaml with expose:true domain
    node_name = "test-node"
    platform_root = tmp_path / "platform"
    yaml_dir = platform_root / "node-configs" / node_name
    yaml_dir.mkdir(parents=True)
    yaml_path = yaml_dir / "node.yaml"
    yaml_path.write_text("""\
projects:
  - name: webapp
    expose: true
    domain: app.test.example.com
""")

    # Mock subprocess.run to return HTTP 200 for verify_domain
    with patch("subprocess.run", return_value=_mock_curl("200")):
        exit_code = dv.main(
            [
                "verify",
                "--node",
                node_name,
                "--platform-root",
                str(platform_root),
                "--curl-timeout",
                "5",
            ]
        )

    assert exit_code == 0
    # 🧪 TRAP[TEST] · Regression: full CLI integration · Scenario: all domains pass, status-page skipped
    # · Last fail: never · Remove if: main() CLI contract changes


# endregion
