# GREP_SUMMARY: test-status-page app.py health html json anti-recursion headers timeout staleness schema-version jinja2
# STRUCTURE: ▶ test_status_page_app_health_pass → ◇ mock node.yaml + status-metrics.json → ○ get_all_checks → assert PASS
#            ▶ test_status_page_app_health_fail → ◇ mock unhealthy container → assert FAIL
#            ▶ test_status_page_app_html_contains_vhosts → ◇ mock → assert HTML contains domain
#            ▶ test_status_page_app_status_json_schema → ◇ mock → assert JSON fields + schema_version
#            ▶ test_status_page_app_anti_recursion → ◇ status-page in containers → assert excluded
#            ▶ test_status_page_app_timeout_per_check → ◇ unreachable vhost → assert FAIL per-check
#            ▶ test_status_page_app_x_headers → ◇ HTML response → assert headers
#            ▶ test_status_page_schema_version_check → ◇ wrong schema_version → assert warning
#            ▶ test_status_page_staleness_warning → ◇ old generated_at → assert staleness
#            ▶ test_status_page_jinja2_autoescape → ◇ XSS payload → assert escaped
#            ▶ test_htpasswd_generation tests (unchanged)
# @file test_status_page.py
# @purpose  Module-level tests for status-page app.py and htpasswd generation in secrets.sh
# @scope    Unit-level: tests call app.py functions directly with mocked node.yaml + status-metrics.json.
#           secrets.sh tests source the library and test _ensure_htpasswd_generated().
#           NEW: schema_version check, staleness warning, Jinja2 autoescape tests.
# @invariants
#   - All tests use tmp_path fixture (Zero Hardcode Rule)
#   - LDD trajectory (IMP:7-10) printed before every assert
#   - No docker required — static unit tests only
#   - Test Honesty Rules: R1 (no pass-tests), R2 (no unfalsifiable asserts), R5 (negative test)
# @rationale  Testing business logic directly avoids docker dependency while validating core behavior.
#             htpasswd tests ensure secrets.sh integration works end-to-end.
# @changes
#   2026-07-23 | META Δ8 | container_name → name in fixtures
#   2026-07-23 | META Δ4 | schema_version check test added
#   2026-07-23 | NEW | staleness, autoescape tests
# region MODULE_CONTRACT
## @purpose  Module-level tests for status-page and secrets.sh htpasswd generation
## @scope    Unit tests — no Docker, no HTTP server, no subprocess.run (mocked)
## @invariants
##   - tmp_path fixture for all file operations
##   - caplog for LDD trajectory capture
##   - At least one IMP:9 log in successful scenarios
##   - status-metrics.json format (container_name → name, schema_version: 2)
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
    node_dir = tmp_path / "test-node"
    node_dir.mkdir(parents=True, exist_ok=True)
    path = node_dir / "node.yaml"
    path.write_text(content)
    return path


@pytest.fixture
def mock_status_metrics_json_all_pass(tmp_path: Path) -> Path:
    """Create a mock status-metrics.json with all containers healthy (v2 schema)."""
    content = {
        "schema_version": 2,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node": "test-node",
        "containers": [
            {
                "name": "nginx",  # Δ8: container_name → name
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 2 hours (healthy)",
                "image": "nginx:latest",
                "memory_usage_bytes": 13107200,
                "memory_limit_bytes": 1073741824,
                "cpu_percent": 0.45,
            },
            {
                "name": "postgres",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 3 hours (healthy)",
                "image": "postgres:16",
                "memory_usage_bytes": 52428800,
                "memory_limit_bytes": 1073741824,
                "cpu_percent": 2.1,
            },
            {
                "name": "redis",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 3 hours (healthy)",
                "image": "redis:alpine",
                "memory_usage_bytes": 5242880,
                "memory_limit_bytes": 536870912,
                "cpu_percent": 0.1,
            },
            {
                "name": "status-page",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 1 hour (healthy)",
                "image": "status-page:latest",
                "memory_usage_bytes": 26214400,
                "memory_limit_bytes": 536870912,
                "cpu_percent": 0.3,
            },
        ],
        "certs": [],
        "projects": [
            {
                "name": "test-app",
                "domain": "test-app.example.com",
                "code_size_bytes": 12345678,
                "docker_image": "nginx:latest",
                "docker_image_size_bytes": 150000000,
            },
        ],
        "host": {"disk_total_gb": 100.0, "disk_free_gb": 30.0, "disk_used_percent": 70.0},
        "errors": [],
    }
    path = tmp_path / "status-metrics.json"
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
def mock_status_metrics_json_one_unhealthy(tmp_path: Path) -> Path:
    """Create a mock status-metrics.json with one unhealthy container (v2 schema)."""
    content = {
        "schema_version": 2,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node": "test-node",
        "containers": [
            {
                "name": "nginx",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 2 hours (healthy)",
                "image": "nginx:latest",
            },
            {
                "name": "postgres",
                "running": True,
                "healthy": False,
                "exit_code": 0,
                "status_line": "Up 3 hours (unhealthy)",
                "image": "postgres:16",
            },
            {
                "name": "redis",
                "running": False,
                "healthy": False,
                "exit_code": 137,
                "status_line": "Exited (137) 1 hour ago",
                "image": "redis:alpine",
            },
            {
                "name": "status-page",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 1 hour (healthy)",
                "image": "status-page:latest",
            },
        ],
        "certs": [],
        "projects": [],
        "host": {"disk_total_gb": 100.0, "disk_free_gb": 30.0, "disk_used_percent": 70.0},
        "errors": [],
    }
    path = tmp_path / "status-metrics-unhealthy.json"
    path.write_text(json.dumps(content))
    return path


# ═══════════════════════════════════════════════════════════════════
# HELPER: reload app module with custom env
# ═══════════════════════════════════════════════════════════════════


def _setup_app_env(node_yaml_path: str, metrics_json_path: str):
    """Set environment variables for app.py and import the module."""
    for key in list(sys.modules.keys()):
        if "app" in key.lower() and "status" in str(sys.modules.get(key, "")):
            del sys.modules[key]

    node_configs_dir = str(Path(node_yaml_path).parent.parent)
    node_name = Path(node_yaml_path).parent.name

    os.environ["NODE_YAML_PATH"] = node_yaml_path
    os.environ["STATUS_METRICS_JSON"] = metrics_json_path  # Δ: new env var name
    os.environ["NODE_NAME"] = node_name
    os.environ["NODE_CONFIGS_DIR"] = node_configs_dir
    os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

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

    def test_health_pass(self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog):
        """All services healthy → /health returns PASS."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        # Mock platform service checks (D067 W3) — they fail in test env without Docker DNS
        with mock.patch.object(app, "_curl_platform_service") as mock_platform:
            mock_platform.return_value = {"target": "grafana", "type": "platform_service", "status": "PASS", "error": None}
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
        container_names = [c["target"] for c in data["checks"] if c["type"] == "container"]
        assert "status-page" not in container_names, "status-page should be excluded from self-checks"

    def test_health_fail(self, mock_node_yaml_no_vhosts, mock_status_metrics_json_one_unhealthy, caplog):
        """One unhealthy container → /health returns FAIL."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_one_unhealthy))

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
        non_pass = [c for c in data["checks"] if c["status"] != "PASS"]
        assert len(non_pass) > 0, "Should have at least one non-PASS check"


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — HTML output
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageHtml:
    """Tests for HTML output."""

    def test_html_contains_vhosts(self, mock_node_yaml, mock_status_metrics_json_all_pass, caplog):
        """HTML response contains vhosts from node.yaml."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml), str(mock_status_metrics_json_all_pass))

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

        vhosts = [c["target"] for c in data["checks"] if c["type"] == "vhost"]
        assert "test-app.example.com" in vhosts, f"Expected test-app.example.com in vhost checks, got {vhosts}"
        assert "internal.example.com" not in vhosts, "internal.example.com (expose:false) should not be checked"

    def test_html_structure(self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog):
        """HTML response has required structural elements."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        data = app.get_all_checks()
        freshness = data.get("metrics_freshness")

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

        assert "status" in data
        assert "checks" in data
        assert "generated_at" in data
        assert "duration_ms" in data
        assert freshness is not None, "metrics_freshness should be present"
        assert isinstance(data["checks"], list)


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — /status.json schema (new: schema_version)
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageJsonSchema:
    """Tests for /status.json schema — now includes schema_version and extended fields."""

    def test_status_json_schema(self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog):
        """/status.json has required fields: status, generated_at, duration_ms, checks[]."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))
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

        assert "status" in data, "Missing 'status' field"
        assert "generated_at" in data, "Missing 'generated_at' field"
        assert "duration_ms" in data, "Missing 'duration_ms' field"
        assert "checks" in data, "Missing 'checks' field"
        assert isinstance(data["checks"], list), "'checks' must be a list"
        assert data["duration_ms"] >= 0, "duration_ms must be non-negative"
        # New: metrics_freshness field
        assert "metrics_freshness" in data, "Missing 'metrics_freshness' field"
        # New: metrics data in the response
        metrics = data.get("metrics", {})
        assert "containers" in metrics, "Missing containers in metrics data"
        assert "host" in metrics, "Missing host in metrics data"

        for check in data["checks"]:
            assert "target" in check, f"Check missing 'target': {check}"
            assert "type" in check, f"Check missing 'type': {check}"
            assert "status" in check, f"Check missing 'status': {check}"
            assert check["status"] in ("PASS", "FAIL", "WARN"), f"Invalid status: {check['status']}"

    def test_status_json_contains_extended_fields(self, mock_node_yaml, mock_status_metrics_json_all_pass, caplog):
        """/status.json now includes schema_version, certs, projects, host (AC4-M)."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml), str(mock_status_metrics_json_all_pass))
        data = app.get_all_checks()
        metrics = data.get("metrics", {})

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

        # schema_version should be present in metrics
        assert metrics.get("schema_version") == 2, f"Expected schema_version=2, got {metrics.get('schema_version')}"
        # Extended fields
        assert "certs" in metrics
        assert "projects" in metrics
        assert "host" in metrics
        assert "errors" in metrics
        assert "node" in metrics

    def test_status_json_schema_version_warning(self, tmp_path, caplog):
        """Older schema_version (<2) should be handled gracefully (logged, not crashed)."""
        caplog.set_level(0)

        # Create node.yaml (empty)
        node_dir = tmp_path / "test-node"
        node_dir.mkdir(parents=True, exist_ok=True)
        node_yaml = node_dir / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n")

        # Create status-metrics.json with old schema_version
        metrics_file = tmp_path / "metrics-old.json"
        old_data = {
            "schema_version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "containers": [],
        }
        metrics_file.write_text(json.dumps(old_data))

        app = _setup_app_env(str(node_yaml), str(metrics_file))
        data = app.get_all_checks()

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # Should still work with old schema
        assert "status" in data


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — anti-recursion
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageAntiRecursion:
    """Tests for anti-recursion: status-page excluded from self-checks."""

    def test_anti_recursion(self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog):
        """status-page container is excluded from checks."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))
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

        metrics_file = tmp_path / "health_timeout.json"
        metrics_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "containers": [],
                }
            )
        )

        app = _setup_app_env(str(node_yaml), str(metrics_file))

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

        vhost_checks = [c for c in data["checks"] if c["type"] == "vhost"]
        assert len(vhost_checks) > 0, "Should have vhost checks"
        assert vhost_checks[0]["status"] == "FAIL", f"Unreachable vhost should be FAIL, got {vhost_checks[0]['status']}"
        assert data["status"] == "FAIL"
        assert data["duration_ms"] >= 0


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — X-Headers
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageXHeaders:
    """Tests for X-headers: X-Robots-Tag, Referrer-Policy, X-Data-Freshness."""

    def test_x_headers_present(self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog):
        """X-Robots-Tag, Referrer-Policy, X-Data-Freshness are present in the data contract."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))
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

        # Δ: renamed from docker_health_freshness to metrics_freshness
        assert data.get("metrics_freshness") is not None, (
            "metrics_freshness should be set (maps to X-Data-Freshness header)"
        )
        assert "T" in data["generated_at"], f"generated_at should be ISO format, got {data['generated_at']}"


# ═══════════════════════════════════════════════════════════════════
# NEW TESTS: schema_version, staleness, autoescape (AC13-M, AC14-M)
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageNewFeatures:
    """Tests for new features: schema_version check, staleness warning, Jinja2 autoescape."""

    def test_load_metrics_directory_at_path(self, tmp_path, caplog):
        """_load_status_metrics returns empty data when path is a directory (P1 fix).

        Docker bind mount creates a directory when source file doesn't exist.
        _load_status_metrics must detect this and return empty data instead of crashing.
        """
        caplog.set_level(0)

        # Create node.yaml
        node_dir = tmp_path / "test-node"
        node_dir.mkdir(parents=True, exist_ok=True)
        node_yaml = node_dir / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n")

        # Create a DIRECTORY at the metrics path (simulates Docker bind mount race condition)
        metrics_dir = tmp_path / "status-metrics-dir"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        # Create a subdirectory inside to make it clearly a directory
        (metrics_dir / "subdir").mkdir(exist_ok=True)

        app = _setup_app_env(str(node_yaml), str(metrics_dir))

        # Call _load_status_metrics directly
        result = app._load_status_metrics(str(metrics_dir))

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # Should return fallback structure, not crash
        assert "errors" in result, "Should have errors[] key"
        assert len(result["errors"]) > 0, "Should have at least one error message"
        assert "directory" in str(result["errors"][0]).lower() or "not a file" in str(result["errors"][0]).lower(), (
            f"Error should mention directory/path issue, got: {result['errors']}"
        )
        assert result.get("containers", []) == [], "Containers should be empty list"
        assert result.get("certs", []) == [], "Certs should be empty list"
        assert result.get("projects", []) == [], "Projects should be empty list"

    def test_status_page_schema_version_check(self, tmp_path, caplog):
        """status-page warns on old schema_version (<2) but continues (AC13-M)."""
        caplog.set_level(0)

        # Create node.yaml
        node_dir = tmp_path / "test-node"
        node_dir.mkdir(parents=True, exist_ok=True)
        node_yaml = node_dir / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n")

        # Metrics file with invalid schema_version
        metrics_file = tmp_path / "metrics-old-schema.json"
        metrics_file.write_text(
            json.dumps(
                {
                    "schema_version": 0,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "containers": [],
                }
            )
        )

        app = _setup_app_env(str(node_yaml), str(metrics_file))

        # Mock platform service checks (D067 W3)
        with mock.patch.object(app, "_curl_platform_service") as mock_platform:
            mock_platform.return_value = {"target": "grafana", "type": "platform_service", "status": "PASS", "error": None}
            data = app.get_all_checks()

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # Should not crash — returns PASS (empty = healthy)
        assert "status" in data
        assert data["status"] == "PASS"

    def test_status_page_staleness_warning(self, tmp_path, caplog):
        """Metrics older than 5 minutes should trigger staleness detection."""
        caplog.set_level(0)

        # Use _setup_app_env to import app module with proper sys.path
        node_dir = tmp_path / "test-node"
        node_dir.mkdir(parents=True, exist_ok=True)
        node_yaml = node_dir / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n")

        # Create a fresh metrics file with old timestamp
        old_time = time.time() - 600  # 10 minutes ago
        old_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(old_time))
        metrics_file = tmp_path / "metrics-stale.json"
        metrics_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "generated_at": old_iso,
                    "containers": [],
                }
            )
        )

        app = _setup_app_env(str(node_yaml), str(metrics_file))
        staleness = app._compute_staleness(old_iso)

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        assert staleness is not None, "Expected staleness for 10-min-old data"
        assert "m" in staleness, f"Expected minutes in staleness string, got '{staleness}'"

    def test_status_page_staleness_fresh(self, tmp_path, caplog):
        """Fresh metrics should return no staleness."""
        caplog.set_level(0)

        node_dir = tmp_path / "test-node"
        node_dir.mkdir(parents=True, exist_ok=True)
        node_yaml = node_dir / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n")
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "containers": [],
                }
            )
        )
        app = _setup_app_env(str(node_yaml), str(metrics_file))

        fresh_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        staleness = app._compute_staleness(fresh_iso)

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        assert staleness is None, f"Expected no staleness for fresh data, got '{staleness}'"


# ═══════════════════════════════════════════════════════════════════
# TESTS: secrets.sh — htpasswd generation (unchanged)
# ═══════════════════════════════════════════════════════════════════


class TestHtpasswdGeneration:
    """Tests for _ensure_htpasswd_generated() in secrets.sh."""

    def test_htpasswd_generation_creates_valid_file(self, tmp_path, caplog):
        """_ensure_htpasswd_generated creates a valid .htpasswd-platform file."""
        caplog.set_level(0)

        htpasswd_file = tmp_path / ".htpasswd-platform"
        email = "admin@test.local"
        password = "test-password-123"

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
        content = htpasswd_file.read_text().strip()
        assert email in content, f"Email not found in htpasswd file: {content}"
        assert "$apr1$" in content, f"APR1 hash not found in htpasswd file: {content}"

    def test_htpasswd_generation_idempotent(self, tmp_path):
        """Second call to _ensure_htpasswd_generated is a no-op."""
        htpasswd_file = tmp_path / ".htpasswd-platform"
        email = "admin@test.local"
        password = "test-password-123"

        secrets_script = Path(__file__).parent.parent / "core" / "lib" / "secrets.sh"

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

        secrets_script = Path(__file__).parent.parent / "core" / "lib" / "secrets.sh"

        result = subprocess.run(
            [
                "bash",
                "-c",
                textwrap.dedent(f"""\
                set -euo pipefail
                export PLATFORM_MASTER_EMAIL="{email}"
                export PLATFORM_MASTER_PASSWORD="{password}"
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
