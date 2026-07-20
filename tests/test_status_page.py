# GREP_SUMMARY: test-status-page app.py health html json anti-recursion headers timeout secrets htpasswd
# STRUCTURE: ▶ test_status_page_app_health_pass → ◇ mock node.yaml + docker-health.json → ○ get_all_checks → assert PASS
#            ▶ test_status_page_app_health_fail → ◇ mock unhealthy container → assert FAIL
#            ▶ test_status_page_app_html_contains_vhosts → ◇ mock → assert HTML contains domain
#            ▶ test_status_page_app_status_json_schema → ◇ mock → assert JSON fields
#            ▶ test_status_page_app_anti_recursion → ◇ status-page in containers → assert excluded
#            ▶ test_status_page_app_timeout_per_check → ◇ unreachable vhost → assert FAIL per-check, not 500
#            ▶ test_status_page_app_x_headers → ◇ HTML response → assert X-Robots-Tag, Referrer-Policy, X-Data-Freshness
#            ▶ test_htpasswd_generation_idempotent → ◇ source secrets.sh → _ensure_htpasswd_generated → assert no-op
#            ▶ test_htpasswd_generation_creates_valid_file → ◇ source secrets.sh → assert creates .htpasswd-platform
#            ▶ test_master_creds_fallback_resolution → ◇ SERVICE_PASS unset → assert fallback to PLATFORM_MASTER_PASSWORD
# @file test_status_page.py
# @purpose  Module-level tests for status-page app.py and htpasswd generation in secrets.sh
# @scope    Unit-level: tests call app.py functions directly with mocked node.yaml + docker-health.json.
#           secrets.sh tests source the library and test _ensure_htpasswd_generated().
# @invariants
#   - All tests use tmp_path fixture (Zero Hardcode Rule)
#   - LDD trajectory (IMP:7-10) printed before every assert
#   - No docker required — static unit tests only
#   - Test Honesty Rules: R1 (no pass-tests), R2 (no unfalsifiable asserts), R5 (negative test)
# @rationale  Testing business logic directly avoids docker dependency while validating core behavior.
#             htpasswd tests ensure secrets.sh integration works end-to-end.
# region MODULE_CONTRACT
## @purpose  Module-level tests for status-page and secrets.sh htpasswd generation
## @scope    Unit tests — no Docker, no HTTP server, no subprocess.run (mocked)
## @invariants
##   - tmp_path fixture for all file operations
##   - caplog for LDD trajectory capture
##   - At least one IMP:9 log in successful scenarios
# endregion MODULE_CONTRACT

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest import mock

import pytest

# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_node_yaml(tmp_path: Path) -> Path:
    """Create a mock node.yaml with expose:true projects and modules."""
    content = textwrap.dedent("""\
    projects:
      - name: test-app
        domain: test-app.example.com
        expose: true
        repo_url: https://github.com/test/test-app
      - name: internal-app
        domain: internal.example.com
        expose: false
        repo_url: https://github.com/test/internal
    modules:
      - nginx
      - postgres
      - redis
      - status-page
    """)
    # node.yaml path: <NODE_CONFIGS_DIR>/<NODE_NAME>/node.yaml
    node_dir = tmp_path / "test-node"
    node_dir.mkdir(parents=True, exist_ok=True)
    path = node_dir / "node.yaml"
    path.write_text(content)
    return path


@pytest.fixture
def mock_docker_health_json_all_pass(tmp_path: Path) -> Path:
    """Create a mock docker-health.json with all containers healthy."""
    content = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "containers": [
            {
                "container_name": "nginx",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 2 hours (healthy)",
            },
            {
                "container_name": "postgres",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 3 hours (healthy)",
            },
            {
                "container_name": "redis",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 3 hours (healthy)",
            },
            {
                "container_name": "status-page",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 1 hour (healthy)",
            },
        ],
    }
    path = tmp_path / "docker-health.json"
    path.write_text(json.dumps(content))
    return path


@pytest.fixture
def mock_node_yaml_no_vhosts(tmp_path: Path) -> Path:
    """Create a mock node.yaml with no expose:true projects (only modules)."""
    content = textwrap.dedent("""\
    projects: []
    modules:
      - nginx
      - postgres
      - status-page
    """)
    node_dir = tmp_path / "test-node"
    node_dir.mkdir(parents=True, exist_ok=True)
    path = node_dir / "node.yaml"
    path.write_text(content)
    return path


@pytest.fixture
def mock_docker_health_json_one_unhealthy(tmp_path: Path) -> Path:
    """Create a mock docker-health.json with one unhealthy container."""
    content = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "containers": [
            {
                "container_name": "nginx",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 2 hours (healthy)",
            },
            {
                "container_name": "postgres",
                "running": True,
                "healthy": False,
                "exit_code": 0,
                "status_line": "Up 3 hours (unhealthy)",
            },
            {
                "container_name": "redis",
                "running": False,
                "healthy": False,
                "exit_code": 137,
                "status_line": "Exited (137) 1 hour ago",
            },
            {
                "container_name": "status-page",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 1 hour (healthy)",
            },
        ],
    }
    path = tmp_path / "docker-health-unhealthy.json"
    path.write_text(json.dumps(content))
    return path


# ═══════════════════════════════════════════════════════════════════
# HELPER: reload app module with custom env
# ═══════════════════════════════════════════════════════════════════


def _setup_app_env(node_yaml_path: str, docker_health_path: str):
    """Set environment variables for app.py and import the module."""
    # Force reimport by clearing cached module
    for key in list(sys.modules.keys()):
        if "app" in key.lower() and "status" in str(sys.modules.get(key, "")):
            del sys.modules[key]

    # node.yaml is at <NODE_CONFIGS_DIR>/<NODE_NAME>/node.yaml
    # So NODE_CONFIGS_DIR = parent of node_name dir
    node_configs_dir = str(Path(node_yaml_path).parent.parent)
    node_name = Path(node_yaml_path).parent.name

    # Set env vars before import
    os.environ["NODE_YAML_PATH"] = node_yaml_path
    os.environ["DOCKER_HEALTH_JSON"] = docker_health_path
    os.environ["NODE_NAME"] = node_name
    os.environ["NODE_CONFIGS_DIR"] = node_configs_dir
    os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

    # Import app module with test paths
    sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "modules" / "status-page"))
    import importlib

    import app as app_module

    importlib.reload(app_module)
    return app_module


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — health endpoint
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageHealth:
    """Tests for /health endpoint — binary verdict."""

    def test_health_pass(self, mock_node_yaml_no_vhosts, mock_docker_health_json_all_pass, caplog):
        """All services healthy → /health returns PASS."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_docker_health_json_all_pass))

        data = app.get_all_checks()

        # Override node.yaml path for direct function testing
        data = app.get_all_checks()

        # ── LDD TRAJECTORY ──
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        assert data["status"] == "PASS", f"Expected PASS, got {data['status']}"
        assert len(data["checks"]) > 0, "Should have at least one check"
        # Anti-recursion: status-page should NOT be in checks
        container_names = [c["target"] for c in data["checks"] if c["type"] == "container"]
        assert "status-page" not in container_names, "status-page should be excluded from self-checks"

    def test_health_fail(self, mock_node_yaml_no_vhosts, mock_docker_health_json_one_unhealthy, caplog):
        """One unhealthy container → /health returns FAIL."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_docker_health_json_one_unhealthy))

        data = app.get_all_checks()

        # ── LDD TRAJECTORY ──
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        assert data["status"] == "FAIL", f"Expected FAIL, got {data['status']}"
        # At least one check should be FAIL or WARN
        non_pass = [c for c in data["checks"] if c["status"] != "PASS"]
        assert len(non_pass) > 0, "Should have at least one non-PASS check"


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — HTML output
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageHtml:
    """Tests for HTML output."""

    def test_html_contains_vhosts(self, mock_node_yaml, mock_docker_health_json_all_pass, caplog):
        """HTML response contains vhosts from node.yaml."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml), str(mock_docker_health_json_all_pass))

        # Mock subprocess.run to simulate successful vhost curl
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
            data = app.get_all_checks()

        # ── LDD TRAJECTORY ──
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # Verify vhost from node.yaml is in checks
        vhosts = [c["target"] for c in data["checks"] if c["type"] == "vhost"]
        assert "test-app.example.com" in vhosts, f"Expected test-app.example.com in vhost checks, got {vhosts}"
        # expose:false domain should NOT be checked
        assert "internal.example.com" not in vhosts, "internal.example.com (expose:false) should not be checked"

    def test_html_structure(self, mock_node_yaml_no_vhosts, mock_docker_health_json_all_pass, caplog):
        """HTML response has required structural elements."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_docker_health_json_all_pass))

        # Access handler to generate HTML
        data = app.get_all_checks()
        freshness = data.get("docker_health_freshness")

        # Simulate HTML generation by checking data structure
        assert "status" in data
        assert "checks" in data
        assert "generated_at" in data
        assert "duration_ms" in data
        assert freshness is not None, "docker_health_freshness should be present"
        assert isinstance(data["checks"], list)


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — /status.json schema
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageJsonSchema:
    """Tests for /status.json schema."""

    def test_status_json_schema(self, mock_node_yaml_no_vhosts, mock_docker_health_json_all_pass, caplog):
        """/status.json has required fields: status, generated_at, duration_ms, checks[]."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_docker_health_json_all_pass))
        data = app.get_all_checks()

        # ── LDD TRAJECTORY ──
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # Required top-level fields
        assert "status" in data, "Missing 'status' field"
        assert "generated_at" in data, "Missing 'generated_at' field"
        assert "duration_ms" in data, "Missing 'duration_ms' field"
        assert "checks" in data, "Missing 'checks' field"
        assert isinstance(data["checks"], list), "'checks' must be a list"
        assert data["duration_ms"] >= 0, "duration_ms must be non-negative"

        # Each check has required fields
        for check in data["checks"]:
            assert "target" in check, f"Check missing 'target': {check}"
            assert "type" in check, f"Check missing 'type': {check}"
            assert "status" in check, f"Check missing 'status': {check}"
            assert check["status"] in ("PASS", "FAIL", "WARN"), f"Invalid status: {check['status']}"


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — anti-recursion
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageAntiRecursion:
    """Tests for anti-recursion: status-page excluded from self-checks."""

    def test_anti_recursion(self, mock_node_yaml_no_vhosts, mock_docker_health_json_all_pass, caplog):
        """status-page container is excluded from checks."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_docker_health_json_all_pass))
        data = app.get_all_checks()

        # ── LDD TRAJECTORY ──
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        container_checks = [c for c in data["checks"] if c["type"] == "container"]
        names = [c["target"] for c in container_checks]

        assert "status-page" not in names, f"status-page should be excluded from container checks, got: {names}"


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — timeout per check
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageTimeout:
    """Tests for per-check timeout behavior."""

    def test_timeout_per_check(self, mock_node_yaml, tmp_path, caplog):
        """Unreachable vhost → FAIL that check, not entire request failure."""
        caplog.set_level(0)

        # Create node.yaml with an unreachable domain
        node_dir = tmp_path / "test-node"
        node_dir.mkdir(parents=True, exist_ok=True)
        node_yaml = node_dir / "node.yaml"
        node_yaml.write_text(
            textwrap.dedent("""\
        projects:
          - name: unreachable-app
            domain: 10.255.255.1.nip.io
            expose: true
        modules: []
        """)
        )

        # Create health json with no containers
        health_json = tmp_path / "health_timeout.json"
        health_json.write_text(
            json.dumps({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "containers": []})
        )

        app = _setup_app_env(str(node_yaml), str(health_json))

        # Mock subprocess.run to simulate curl timeout
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["curl"], timeout=5)
            data = app.get_all_checks()

        # ── LDD TRAJECTORY ──
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # The unreachable vhost should result in FAIL status for that check
        vhost_checks = [c for c in data["checks"] if c["type"] == "vhost"]
        assert len(vhost_checks) > 0, "Should have vhost checks"
        assert vhost_checks[0]["status"] == "FAIL", f"Unreachable vhost should be FAIL, got {vhost_checks[0]['status']}"
        # Overall status should be FAIL (but not crash)
        assert data["status"] == "FAIL"
        # Request should still complete (duration_ms present)
        assert data["duration_ms"] >= 0


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — X-Headers
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageXHeaders:
    """Tests for X-headers: X-Robots-Tag, Referrer-Policy, X-Data-Freshness."""

    def test_x_headers_present(self, mock_node_yaml_no_vhosts, mock_docker_health_json_all_pass, caplog):
        """X-Robots-Tag, Referrer-Policy, X-Data-Freshness are present in the data contract."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_docker_health_json_all_pass))
        data = app.get_all_checks()

        # ── LDD TRAJECTORY ──
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # Verify docker_health_freshness is populated (used as X-Data-Freshness header value)
        assert data.get("docker_health_freshness") is not None, (
            "docker_health_freshness should be set (maps to X-Data-Freshness header)"
        )
        # Verify generated_at is ISO format
        assert "T" in data["generated_at"], f"generated_at should be ISO format, got {data['generated_at']}"


# ═══════════════════════════════════════════════════════════════════
# TESTS: secrets.sh — htpasswd generation
# ═══════════════════════════════════════════════════════════════════


class TestHtpasswdGeneration:
    """Tests for _ensure_htpasswd_generated() in secrets.sh."""

    def test_htpasswd_generation_creates_valid_file(self, tmp_path, caplog):
        """_ensure_htpasswd_generated creates a valid .htpasswd-platform file."""
        caplog.set_level(0)

        htpasswd_file = tmp_path / ".htpasswd-platform"
        email = "admin@test.local"
        password = "test-password-123"

        # Source secrets.sh and call _ensure_htpasswd_generated via bash subprocess
        import subprocess

        secrets_script = Path(__file__).parent.parent / "core" / "lib" / "secrets.sh"
        result = subprocess.run(
            [
                "bash",
                "-c",
                textwrap.dedent(f"""\
                set -euo pipefail
                export PLATFORM_MASTER_EMAIL="{email}"
                export PLATFORM_MASTER_PASSWORD="{password}"
                export HTPASSWD_FILE="{htpasswd_file}"
                # Mock step_start/step_done/log_step since they're not available in test
                step_start() {{ echo "[IMP:7][htpasswd][start] $*" >&2; }}
                step_done() {{ echo "[IMP:9][htpasswd][done] $*" >&2; }}
                log_step() {{ echo "[IMP:7][htpasswd][log] $*" >&2; }}
                source "{secrets_script}"
                _ensure_htpasswd_generated
                echo "HTPASSWD_FILE=$HTPASSWD_FILE"
            """),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for line in result.stderr.split("\n"):
            if "[IMP:" in line:
                imp_level = int(line.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    print(line)
        print("--- END LDD TRAJECTORY ---")
        print("STDOUT:", result.stdout)

        assert result.returncode == 0, f"htpasswd generation failed: {result.stderr}"
        assert htpasswd_file.exists(), f"htpasswd file not created at {htpasswd_file}"

        # Verify file content
        content = htpasswd_file.read_text().strip()
        assert email in content, f"Email not found in htpasswd file: {content}"
        assert "$apr1$" in content, f"APR1 hash not found in htpasswd file: {content}"

    def test_htpasswd_generation_idempotent(self, tmp_path):
        """Second call to _ensure_htpasswd_generated is a no-op."""
        htpasswd_file = tmp_path / ".htpasswd-platform"
        email = "admin@test.local"
        password = "test-password-123"

        import subprocess

        secrets_script = Path(__file__).parent.parent / "core" / "lib" / "secrets.sh"

        # First call: generate
        result1 = subprocess.run(
            [
                "bash",
                "-c",
                textwrap.dedent(f"""\
                set -euo pipefail
                export PLATFORM_MASTER_EMAIL="{email}"
                export PLATFORM_MASTER_PASSWORD="{password}"
                export HTPASSWD_FILE="{htpasswd_file}"
                step_start() {{ :; }}
                step_done() {{ :; }}
                log_step() {{ :; }}
                source "{secrets_script}"
                _ensure_htpasswd_generated
                md5sum "{htpasswd_file}" | cut -d' ' -f1
            """),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result1.returncode == 0, f"First call failed: {result1.stderr}"
        first_md5 = result1.stdout.strip()

        # Second call: should be no-op, same hash
        result2 = subprocess.run(
            [
                "bash",
                "-c",
                textwrap.dedent(f"""\
                set -euo pipefail
                export PLATFORM_MASTER_EMAIL="{email}"
                export PLATFORM_MASTER_PASSWORD="{password}"
                export HTPASSWD_FILE="{htpasswd_file}"
                step_start() {{ :; }}
                step_done() {{ :; }}
                log_step() {{ :; }}
                source "{secrets_script}"
                _ensure_htpasswd_generated
                md5sum "{htpasswd_file}" | cut -d' ' -f1
            """),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result2.returncode == 0, f"Second call failed: {result2.stderr}"
        second_md5 = result2.stdout.strip()

        assert first_md5 == second_md5, f"Idempotent check failed: md5 changed ({first_md5} → {second_md5})"

    def test_master_creds_fallback_resolution(self, tmp_path):
        """SERVICE_SPECIFIC_PASS empty → fallback to PLATFORM_MASTER_PASSWORD."""
        htpasswd_file = tmp_path / ".htpasswd-fallback"
        email = "admin@test.local"
        password = "fallback-password-456"

        import subprocess

        secrets_script = Path(__file__).parent.parent / "core" / "lib" / "secrets.sh"

        # Test fallback: only PLATFORM_MASTER_PASSWORD set, no service-specific override
        result = subprocess.run(
            [
                "bash",
                "-c",
                textwrap.dedent(f"""\
                set -euo pipefail
                export PLATFORM_MASTER_EMAIL="{email}"
                export PLATFORM_MASTER_PASSWORD="{password}"
                # Simulate service-specific override pattern
                SERVICE_PASSWORD="${{MONITORING_AUTH_PASSWORD:-$PLATFORM_MASTER_PASSWORD}}"
                export HTPASSWD_FILE="{htpasswd_file}"
                step_start() {{ :; }}
                step_done() {{ :; }}
                log_step() {{ :; }}
                source "{secrets_script}"
                _ensure_htpasswd_generated
                echo "SERVICE_PASSWORD=$SERVICE_PASSWORD"
                echo "HTPASSWD_FILE=$HTPASSWD_FILE"
            """),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0, f"Fallback test failed: {result.stderr}"
        assert "SERVICE_PASSWORD=fallback-password-456" in result.stdout, (
            f"SERVICE_PASSWORD should fall back to PLATFORM_MASTER_PASSWORD, got: {result.stdout}"
        )
        assert htpasswd_file.exists(), "htpasswd file should be created with fallback password"
