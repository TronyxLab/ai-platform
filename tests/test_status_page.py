# GREP_SUMMARY: test-status-page app.py health html json anti-recursion headers timeout staleness schema-version jinja2 format-bytes memory swap os backup quick-nav progress-bar
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
#            ▶ test_htpasswd_generation tests (thin shell facade → secrets_manager.py htpasswd, DevPlan 102)
#            ▶ 047: test_format_bytes_autoscale + _format_bytes unit → assert correct unit
#            ▶ 047: test_html_structure_has_memory/os/progress/nav/backup/no-cicd → assert new HTML fields
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
        "host": {
            "disk_total_gb": 100.0,
            "disk_free_gb": 30.0,
            "disk_used_percent": 70.0,
            "memory_total_gb": 15.5,
            "memory_available_gb": 7.9,
            "memory_used_percent": 49.3,
            "swap_total_gb": 4.0,
            "swap_free_gb": 3.7,
            "swap_used_percent": 7.0,
            "os_name": "Linux",
            "kernel_version": "6.1.0",
            "arch": "x86_64",
        },
        "backup": {
            "status": "ok",
            "last_postgres_at": "2026-07-24T10:00:00Z",
            "last_app_data_at": "2026-07-24T10:00:00Z",
        },
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
    """Set environment variables for app.py and import the module.

    ## @purpose — Подготовка env для status-page app.py. app.py читает env ТОЛЬКО на
    ##            уровне модуля (строки 91-98 — module-level константы), поэтому env
    ##            нужен лишь на время importlib.reload. После reload константы захвачены
    ##            в память модуля — env восстанавливается (snapshot/restore), чтобы не
    ##            отравлять последующие тесты (NODE_NAME/NODE_YAML_PATH leak, флейк
    ##            test_node_lifecycle_static.py:291 в полном прогоне).
    ## ⚠️ TRAP[BUG] · 2026-08-02 · P2 · env-утечка: os.environ["NODE_NAME"] без отката
    ## · Symptom: test_node_lifecycle_static.py::test_node_lifecycle_dry_run_contract FAIL
    ## ·   "Expected NODE_NAME-required diagnostic" — node-lifecycle.sh видел NODE_NAME из env
    ## · Root: прямые записи os.environ в тест-хелперах без restore (test_status_page,
    ## ·   test_platform_export_metrics) — глобальное env-состояние между тестами
    ## · Fix: snapshot/restore после reload (app.py читает env только на уровне модуля)
    ## · Prevention: тест-хелперы, мутирующие os.environ, обязаны использовать
    ## ·   monkeypatch.setenv или snapshot/restore в finally
    ## 2026-08-04 (DevPlan 129 W4): del sys.modules ЗАМЕНЁН на reload_safe.reload_module —
    ##   канон reload-безопасности (НЕ удалять модули из sys.modules: удаление создаёт новый
    ##   объект при следующем import, старые __globals__ других модулей держат старый →
    ##   monkeypatch-заглушки в других тестах не применяются, реальный poller/SSH — 1276.8s).
    """
    node_configs_dir = str(Path(node_yaml_path).parent.parent)
    node_name = Path(node_yaml_path).parent.name

    _ENV_KEYS = ("NODE_YAML_PATH", "STATUS_METRICS_JSON", "NODE_NAME", "NODE_CONFIGS_DIR", "PLATFORM_DOMAIN")
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}

    os.environ["NODE_YAML_PATH"] = node_yaml_path
    os.environ["STATUS_METRICS_JSON"] = metrics_json_path  # Δ: new env var name
    os.environ["NODE_NAME"] = node_name
    os.environ["NODE_CONFIGS_DIR"] = node_configs_dir
    os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

    sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "modules" / "status-page"))
    from _conftest.reload_safe import reload_module

    # Канон W4: importlib.reload того же объекта (БЕЗ del sys.modules) — env прочитан
    # module-level строками, объект модуля остался тем же для внешних __globals__-ссылок.
    app_module = reload_module("app", expected_file_substring="status-page")

    # Restore env — app.py уже захватил константы при reload (module-level reads).
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    return app_module


@pytest.fixture
def mock_subprocess():
    """Boundary fixture (T3 D1): ONE subprocess.run mock for the whole file.

    ## @purpose — Replaces 16 inline subprocess.run patch blocks. Default
    ##            behavior: curl returns HTTP 200 (healthy vhost). Tests override
    ##            return_value/side_effect for specific status codes / timeouts.
    ## @io — ⎋ MagicMock (subprocess.run) — assert on rendered result, not on calls
    ## @complexity — O(1)
    ## @invariants
    ##   - Patching GLOBAL subprocess.run — the only I/O boundary status-page app.py uses
    ##   - Assertions on observable rendered results only (D1, без интроспекции вызовов)
    """
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        yield mock_run


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — health endpoint
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageHealth:
    """Tests for /health endpoint — binary verdict."""

    def test_health_pass(self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess):
        """All services healthy → /health returns PASS."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
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

    def test_html_contains_vhosts(self, mock_node_yaml, mock_status_metrics_json_all_pass, caplog, mock_subprocess):
        """HTML response contains vhosts from node.yaml."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
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
# TESTS: app.py — _format_bytes()
# ═══════════════════════════════════════════════════════════════════


class TestFormatBytes:
    """Tests for _format_bytes() auto-unit selection."""

    def test_format_bytes_autoscale(self, tmp_path):
        """_format_bytes() selects correct unit automatically."""
        # Create minimal files to allow app module import
        node_dir = tmp_path / "test-node"
        node_dir.mkdir(parents=True, exist_ok=True)
        node_yaml = node_dir / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n")
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text('{"schema_version":2,"containers":[]}')

        app = _setup_app_env(str(node_yaml), str(metrics_file))

        # ── Values ──
        assert app._format_bytes(0) == "0 B"
        assert app._format_bytes(-1) == "0 B"
        assert app._format_bytes(500) == "500 B"
        assert app._format_bytes(1024) == "1.0 KB"
        assert app._format_bytes(1536000) == "1.5 MB"  # 1500 KB
        assert app._format_bytes(1073741824) == "1.0 GB"
        assert app._format_bytes(1099511627776) == "1.0 TB"

    def test_format_bytes_precision(self, tmp_path):
        """_format_bytes() respects precision parameter."""
        node_dir = tmp_path / "test-node"
        node_dir.mkdir(parents=True, exist_ok=True)
        node_yaml = node_dir / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n")
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text('{"schema_version":2,"containers":[]}')

        app = _setup_app_env(str(node_yaml), str(metrics_file))

        assert app._format_bytes(1536000, precision=0) == "1 MB"
        assert app._format_bytes(1536000, precision=2) == "1.46 MB"
        assert app._format_bytes(1073741824, precision=3) == "1.000 GB"


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — HTML structure (new 047 fields)
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageHtml047:
    """Tests for 047 enhancements: memory, swap, OS, backup, quick-nav, progress bars, no CI/CD badges."""

    def test_html_structure_has_memory_fields(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog
    ):
        """HTML contains RAM Total, RAM Available, Swap Total."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = app._render_html(data)

        assert "RAM Total" in html, "HTML missing 'RAM Total'"
        assert "RAM Available" in html, "HTML missing 'RAM Available'"
        assert "Swap Total" in html, "HTML missing 'Swap Total'"

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

    def test_html_structure_has_os_fields(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """HTML contains OS / Kernel row."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = app._render_html(data)

        assert "OS / Kernel" in html, "HTML missing 'OS / Kernel'"
        assert "kernel_version" not in html, "raw kernel_version should not appear (displayed as formatted)"

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

    def test_html_structure_no_cicd_badges(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """HTML does NOT contain CI/CD Pipeline Verified badges."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = app._render_html(data)

        assert "CI/CD Pipeline Verified" not in html, "CI/CD badges should be removed from footer"
        assert "Pipeline Verified" not in html, "Pipeline verified badge should be removed"

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

    def test_html_structure_has_quick_nav(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """HTML contains quick-nav navbar with section anchors."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = app._render_html(data)

        assert '<nav class="quick-nav">' in html, "HTML missing quick-nav navbar"
        assert "#services" in html, "HTML missing #services anchor"
        assert "#projects" in html, "HTML missing #projects anchor"
        assert "#containers" in html, "HTML missing #containers anchor"
        assert "#host" in html, "HTML missing #host anchor"

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

    def test_html_structure_has_progress_bars(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog
    ):
        """HTML contains progress-bar elements for disk usage."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = app._render_html(data)

        assert '<div class="progress-bar">' in html, "HTML missing progress-bar element"
        assert 'class="progress-fill' in html, "HTML missing progress-fill element"

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

    def test_html_structure_has_app_data_backup(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog
    ):
        """HTML contains App-Data Backup when backup.last_app_data_at is set."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = app._render_html(data)

        assert "App-Data Backup" in html, "HTML missing 'App-Data Backup' when backup.last_app_data_at is set"
        assert "last_app_data_at" not in html, "raw last_app_data_at should not appear in HTML (use formatted value)"

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

    def test_timeout_per_check(self, mock_node_yaml, tmp_path, caplog, mock_subprocess):
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

        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd=["curl"], timeout=5)
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
# TESTS: app.py — HTTP auth handling (401/403 → PASS)
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageAuthHandling:
    """Tests for HTTP auth handling: 401/403 treated as PASS (service alive, auth required)."""

    def test_vhost_401_is_pass(self, mock_node_yaml, tmp_path, caplog, mock_subprocess):
        """Vhost returning 401 (auth required) → PASS — service is alive and responding."""
        caplog.set_level(0)

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

        app = _setup_app_env(str(mock_node_yaml), str(metrics_file))
        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="401", stderr="")
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

        vhost_checks = [c for c in data["checks"] if c["type"] == "vhost"]
        assert len(vhost_checks) > 0, "Should have vhost checks"
        for vc in vhost_checks:
            assert vc["status"] == "PASS", (
                f"Expected PASS for 401 (auth required = service alive), got {vc['status']} for {vc['target']}"
            )
            assert vc["http_code"] == 401

    def test_vhost_403_is_pass(self, mock_node_yaml, tmp_path, caplog, mock_subprocess):
        """Vhost returning 403 (forbidden) → PASS — service is alive and responding."""
        caplog.set_level(0)

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

        app = _setup_app_env(str(mock_node_yaml), str(metrics_file))
        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="403", stderr="")
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

        vhost_checks = [c for c in data["checks"] if c["type"] == "vhost"]
        assert len(vhost_checks) > 0, "Should have vhost checks"
        for vc in vhost_checks:
            assert vc["status"] == "PASS", (
                f"Expected PASS for 403 (access denied = service alive), got {vc['status']} for {vc['target']}"
            )
            assert vc["http_code"] == 403

    def test_vhost_404_is_warn(self, mock_node_yaml, tmp_path, caplog, mock_subprocess):
        """Vhost returning 404 → WARN — service is reachable but path not found."""
        caplog.set_level(0)

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

        app = _setup_app_env(str(mock_node_yaml), str(metrics_file))
        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="404", stderr="")
        data = app.get_all_checks()

        vhost_checks = [c for c in data["checks"] if c["type"] == "vhost"]
        assert len(vhost_checks) > 0, "Should have vhost checks"
        for vc in vhost_checks:
            assert vc["status"] == "WARN", f"Expected WARN for 404 (not found), got {vc['status']} for {vc['target']}"
            assert vc["http_code"] == 404

    def test_vhost_500_is_warn(self, mock_node_yaml, tmp_path, caplog, mock_subprocess):
        """Vhost returning 500 → WARN — service is reachable but internal error."""
        caplog.set_level(0)

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

        app = _setup_app_env(str(mock_node_yaml), str(metrics_file))
        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="500", stderr="")
        data = app.get_all_checks()

        vhost_checks = [c for c in data["checks"] if c["type"] == "vhost"]
        assert len(vhost_checks) > 0, "Should have vhost checks"
        for vc in vhost_checks:
            assert vc["status"] == "WARN", (
                f"Expected WARN for 500 (internal error), got {vc['status']} for {vc['target']}"
            )
            assert vc["http_code"] == 500

    def test_platform_service_401_is_pass(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """Platform service returning 401 → PASS — service is alive, auth required."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="401", stderr="")
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

        platform_checks = [c for c in data["checks"] if c["type"] == "platform_service"]
        assert len(platform_checks) > 0, "Should have platform service checks"
        for pc in platform_checks:
            assert pc["status"] == "PASS", (
                f"Expected PASS for 401 (auth required = service alive), got {pc['status']} for {pc['target']}"
            )
            assert pc["http_code"] == 401

    def test_platform_service_403_is_pass(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """Platform service returning 403 → PASS — service is alive, access denied."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="403", stderr="")
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

        platform_checks = [c for c in data["checks"] if c["type"] == "platform_service"]
        assert len(platform_checks) > 0, "Should have platform service checks"
        for pc in platform_checks:
            assert pc["status"] == "PASS", (
                f"Expected PASS for 403 (access denied = service alive), got {pc['status']} for {pc['target']}"
            )
            assert pc["http_code"] == 403


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

    def test_status_page_schema_version_check(self, tmp_path, caplog, mock_subprocess):
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
        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
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
# TESTS: secrets.sh — htpasswd facade (REMOVED API — волна 118 B6)
# ═══════════════════════════════════════════════════════════════════
# Волна 118 B6: _ensure_htpasswd_generated и step_12b_ensure_secrets УДАЛЕНЫ из
# secrets.sh (0 callers; бизнес-логика — secrets_manager.py htpasswd/ensure, вызывается
# из phases.py напрямую). R5 negative: type -t = пусто.


class TestHtpasswdGenerationRemoved:
    """R5 negative: _ensure_htpasswd_generated/step_12b_ensure_secrets удалены (B6)."""

    def test_htpasswd_facades_removed(self, tmp_path):
        # 🧪 TRAP[TEST] · NEGATIVE (R5) · B6 — secrets.sh фасады удалены
        # · Scenario: source secrets.sh → type -t _ensure_htpasswd_generated → пусто (не функция)
        # · Last fail: _ensure_htpasswd_generated существовал до волны 118 B6 (secrets.sh L55-67)
        # · Remove if: shell htpasswd фасад будет восстановлен
        secrets_script = Path(__file__).parent.parent / "core" / "lib" / "secrets.sh"
        result = subprocess.run(
            [
                "bash",
                "-c",
                textwrap.dedent(f"""\
                set -euo pipefail
                step_start() {{ :; }}
                step_done() {{ :; }}
                log_step() {{ :; }}
                source "{secrets_script}"
                if [[ "$(type -t _ensure_htpasswd_generated)" == "function" ]] \
                    || [[ "$(type -t step_12b_ensure_secrets)" == "function" ]]; then
                    echo "[IMP:10][test] FAIL: htpasswd facades still defined" >&2
                    exit 1
                fi
                echo "[IMP:9][test] secrets.sh htpasswd facades REMOVED — OK" >&2
                exit 0
            """),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        print("STDERR:", result.stderr)
        assert result.returncode == 0, f"htpasswd facades not removed: {result.stderr}"
        assert "REMOVED" in result.stderr, f"no REMOVED marker: {result.stderr}"
        __import__("logging").getLogger(__name__).critical(
            "[IMP:9][test_htpasswd_facades_removed] PASS: secrets.sh htpasswd facades removed (B6 R5)"
        )


# ═══════════════════════════════════════════════════════════════════
# R5: env-isolation (FRAG-1, DevPlan 119 F6)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_env_isolation_negative
## @purpose  R5 negative (DevPlan 119 F6 AC-F6.3, TEST_SPEC test_env_isolation_negative):
##           _setup_app_env мутирует 5 env vars (NODE_YAML_PATH/STATUS_METRICS_JSON/NODE_NAME/
##           NODE_CONFIGS_DIR/PLATFORM_DOMAIN). FRAG-1 фикс — snapshot/restore после reload.
##           Этот тест верифицирует, что env восстановлен ПОСЛЕ вызова _setup_app_env
##           (никакой утечки в следующие тесты — флейк test_node_lifecycle_static:291).
## @io — ⇥ mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass → ⎋ None
## @complexity — O(1)
## @invariants
##   - _setup_app_env возвращает env к исходному состоянию (snapshot/restore, TRAP[BUG] 2026-08-02)
##   - NODE_NAME/PLATFORM_DOMAIN и др. НЕ должны «протекать» после вызова
# 🧪 TRAP[TEST] · NEGATIVE (R5) · status_page env isolation — DevPlan 119 F6 (FRAG-1)
# · Last fail: NODE_NAME из env видел node-lifecycle.sh (test_node_lifecycle_static.py:291 FAIL)
# · Remove if: _setup_app_env перестаёт мутировать env (или мигрирует на monkeypatch-фикстуру)
def test_env_isolation_negative(
    mock_node_yaml_no_vhosts,
    mock_status_metrics_json_all_pass,
) -> None:
    """R5: env vars восстановлены после _setup_app_env (нет утечки NODE_NAME/PLATFORM_DOMAIN)."""
    _ENV_KEYS = ("NODE_YAML_PATH", "STATUS_METRICS_JSON", "NODE_NAME", "NODE_CONFIGS_DIR", "PLATFORM_DOMAIN")

    # Snapshot ДО вызова
    before = {key: os.environ.get(key) for key in _ENV_KEYS}

    app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))
    assert app is not None, "_setup_app_env должна вернуть модуль app"

    # Snapshot ПОСЛЕ вызова — env должен совпадать с before (restore в finally-эквиваленте)
    after = {key: os.environ.get(key) for key in _ENV_KEYS}
    leaked = {key: (before[key], after[key]) for key in _ENV_KEYS if before[key] != after[key]}
    assert not leaked, (
        f"R5 FAIL: env утечка после _setup_app_env (FRAG-1): {leaked}. "
        f"NODE_NAME leak ломает node-lifecycle-тесты (test_node_lifecycle_static:291)."
    )
    print("[IMP:9][test_env_isolation_negative] R5 PASS: env vars restored after _setup_app_env")


# endregion FUNC_test_env_isolation_negative
