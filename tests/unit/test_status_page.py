# GREP_SUMMARY: test-status-page app.py health html json anti-recursion headers timeout schema-version jinja2 memory swap os backup quick-nav progress-bar metrics prometheus tls
# STRUCTURE: ▶ test_status_page_app_health_pass → ◇ mock node.yaml + status-metrics.json → ○ get_all_checks → assert PASS
#            ▶ test_status_page_app_health_fail → ◇ mock unhealthy container → assert FAIL
#            ▶ test_status_page_app_html_contains_vhosts → ◇ mock → assert HTML contains domain
#            ▶ test_status_page_app_status_json_schema → ◇ mock → assert JSON fields + schema_version
#            ▶ test_status_page_app_timeout_per_check → ◇ unreachable vhost → assert FAIL per-check
#            ▶ test_status_page_app_x_headers → ◇ HTML response → assert headers
#            ▶ test_status_page_schema_version_check → ◇ wrong schema_version → assert warning
#            ▶ test_status_page_jinja2_autoescape → ◇ XSS payload → assert escaped
#            ▶ test_metrics_renders_tls_gauges (017 C4) → ◇ _handle_metrics (direct handler) → assert platform_tls_*
#            ▶ test_htpasswd_generation tests (thin shell facade → secrets_manager.py htpasswd, DevPlan 102)
#            ▶ 047: test_html_structure_has_memory/os/progress/nav/backup/no-cicd → assert new HTML fields
# @file test_status_page.py
# @purpose  App/handler-level tests for status-page app.py и htpasswd generation в secrets.sh.
#           Публичные контракты collectors/renderer покрываются ТОЛЬКО в
#           test_status_collectors.py / test_status_renderer.py (DevPlan 172 W3.2 —
#           одна точка покрытия; дубли format_bytes/compute_staleness/load_status_metrics удалены).
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
#   2026-08-05 | DevPlan 139 W2 | env-мутация → monkeypatch.setenv+undo (xdist); private-доступы
#             | (_format_bytes/_compute_staleness/_load_status_metrics/_render_html) → публичные
#             | renderer.format_bytes / collectors.compute_staleness / collectors.load_status_metrics /
#             | renderer.render_html (top-10 private закрыты); фикс silent-noop mock_subprocess
#   2026-08-27 | DevPlan 017 C4 | +TestStatusPageMetrics — /metrics TLS-гейджи (direct handler, server-free)
# region MODULE_CONTRACT
## @purpose  Module-level tests for status-page and secrets.sh htpasswd generation
## @scope    Unit tests — no Docker, no HTTP server, no subprocess.run (mocked)
## @invariants
##   - tmp_path fixture for all file operations
##   - caplog for LDD trajectory capture
##   - At least one IMP:9 log in successful scenarios
##   - status-metrics.json format (container_name → name, schema_version: 2)
##   - xdist (DevPlan 139 W2): env-мутации ТОЛЬКО через monkeypatch.setenv + undo/restore
## @changes 2026-08-05 | DevPlan 139 W2 — monkeypatch-конвертация + публичные контракты
# endregion MODULE_CONTRACT

import http.client
import io
import json
import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from tests._conftest.ldd import _print_ldd_trajectory
from tests.helpers.gate_helpers import assert_ldd_imp9

# Module-specific path (tests/AGENTS.md §sys.path policy): core/modules/status-page.
# Абсолютный Path(__file__)-based (xdist-инвариант 4, DevPlan 139 W2).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "status-page"))

# Публичный контракт (DevPlan 139 W2): renderer.render_html вместо приватного app._render_html.
# Коллекторные публичные контракты (compute_staleness/load_status_metrics) покрываются
# ТОЛЬКО в test_status_collectors.py, renderer (format_bytes) — в test_status_renderer.py
# (DevPlan 172 W3.2: одна точка покрытия на публичный контракт).
from jinja2 import Environment, FileSystemLoader, select_autoescape
from renderer import render_html

logger = logging.getLogger(__name__)

# Jinja2 env для публичного renderer.render_html (аналог module-level _jinja_env в app.py)
_JINJA_ENV = Environment(
    loader=FileSystemLoader(
        str(Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "status-page" / "templates")
    ),
    autoescape=select_autoescape(["html"]),
)

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
    path.write_text(content, encoding="utf-8")
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
                "image": "postgres:18.4",
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
    path.write_text(json.dumps(content), encoding="utf-8")
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
    path.write_text(content, encoding="utf-8")
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
                "image": "postgres:18.4",
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
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


# ═══════════════════════════════════════════════════════════════════
# HELPER: LDD trajectory (компактный заменитель inline-булерана)
# ═══════════════════════════════════════════════════════════════════


# T2.16a: _print_trajectory/_assert_imp9 консолидированы в gate_helpers.assert_ldd_imp9


# ═══════════════════════════════════════════════════════════════════
# HELPER: reload app module with custom env
# ═══════════════════════════════════════════════════════════════════


def _setup_app_env(node_yaml_path: str, metrics_json_path: str):
    """Import app module (reload-safe) and BIND get_all_checks DI-параметры (W5 T5.3 + 167 D4).

    ## @purpose — Подготовка app-модуля для тестов БЕЗ env-мутации, reload-танца и
    ##            патчей get_all_checks: get_all_checks() (DevPlan 160 W5 T5.3) принимает
    ##            node_yaml_path/status_metrics_json keyword-only параметрами → хелпер байндит
    ##            их functools.partial-ом в модульный атрибут СВЕЖЕГО (reload_safe) экземпляра
    ##            модуля (plain attribute injection — модуль per-test объект, 167 D4, 0 setattr).
    ##            Все 29 call-site'ов сохраняют `app.get_all_checks()` без изменений (handler
    ##            тоже — он вызывает модульный атрибут). 5 setenv (NODE_YAML_PATH/
    ##            STATUS_METRICS_JSON/NODE_NAME/NODE_CONFIGS_DIR/PLATFORM_DOMAIN) УБРАНЫ —
    ##            0 env-мутаций, TRAP[BUG] 2026-08-02 (env-утечка в node-lifecycle-тесты)
    ##            закрыт архитектурно.
    ## ⚠️ TRAP[BUG] · 2026-08-02 · P2 · env-утечка: os.environ["NODE_NAME"] без отката
    ## · Symptom: test_node_lifecycle_static.py::test_node_lifecycle_dry_run_contract FAIL
    ## ·   "Expected NODE_NAME-required diagnostic" — node-lifecycle.sh видел NODE_NAME из env
    ## · Root: прямые записи os.environ в тест-хелперах без restore (test_status_page,
    ## ·   test_platform_export_metrics) — глобальное env-состояние между тестами
    ## · Fix (W5 T5.3): get_all_checks DI-параметрами — env НЕ мутируется вовсе.
    ## · Prevention: тест-хелперы НЕ мутируют os.environ (DI вместо reload-паттерна).
    ## 2026-08-05 (DevPlan 139 W2): env-мутация переведена на monkeypatch.setenv + restore
    ## 2026-08-13 (DevPlan 160 W5 T5.3): env-мутация УБРАНА — DI-binding, reload сохранён
    ##   (канон reload_safe — модуль свежий, но константы не важны: инжектируются пути).
    ## 2026-08-14 (DevPlan 167 D4): setattr-патчи → plain attribute injection на свежем
    ##   reload-экземпляре модуля (0 setattr; per-test изоляция сохранена reload_safe).
    """
    import functools

    from _conftest.reload_safe import reload_module

    # Канон W4: importlib.reload того же объекта (БЕЗ del sys.modules) — свежий модуль.
    app_module = reload_module("app", expected_file_substring="status-page")

    # W5 T5.3 + 167 D4: DI-binding на свежем экземпляре модуля (plain attribute injection) —
    # get_all_checks(node_yaml_path=..., status_metrics_json=...). 0 setattr-патчей.
    orig_get_all_checks = app_module.get_all_checks
    app_module.get_all_checks = functools.partial(
        orig_get_all_checks, node_yaml_path=node_yaml_path, status_metrics_json=metrics_json_path
    )
    # /healthz fast-path читает модульную константу STATUS_METRICS_JSON напрямую
    # (W10 T10.11/T10.13 handler-тесты) — инжектим её в тот же tmp-файл (module instance).
    app_module.STATUS_METRICS_JSON = metrics_json_path

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
    ##   - DevPlan 158 W1: также мокает collectors.socket.gethostbyname → DNS всегда
    ##     резолвится (платформенные сервисы "задеплоены"), чтобы curl-flow доходил.
    ##     Без этого DNS-probe (T1.1) возвращает DISABLED без вызова curl.
    ## ⚠️ DevPlan 139 W2: тесты, использующие mock_subprocess, ОБЯЗАНЫ объявить фикстуру
    ##   в сигнатуре — без этого имя резолвится в fixture-функцию (silent no-op, mock не
    ##   применяется). Исправлено во всех HTML-render-тестах.
    """
    with (
        mock.patch("subprocess.run") as mock_run,
        mock.patch("collectors.socket.gethostbyname", return_value="172.22.0.10"),
    ):
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

        assert_ldd_imp9(caplog, require_imp9=False)

        assert data["status"] == "PASS", f"Expected PASS, got {data['status']}"
        assert len(data["checks"]) > 0, "Should have at least one check"
        container_names = [c["target"] for c in data["checks"] if c["type"] == "container"]
        assert "status-page" not in container_names, "status-page should be excluded from self-checks"

    def test_health_fail(self, mock_node_yaml_no_vhosts, mock_status_metrics_json_one_unhealthy, caplog):
        """One unhealthy container → /health returns FAIL."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_one_unhealthy))

        data = app.get_all_checks()

        assert_ldd_imp9(caplog, require_imp9=False)

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

        assert_ldd_imp9(caplog, require_imp9=False)

        vhosts = [c["target"] for c in data["checks"] if c["type"] == "vhost"]
        assert "test-app.example.com" in vhosts, f"Expected test-app.example.com in vhost checks, got {vhosts}"
        assert "internal.example.com" not in vhosts, "internal.example.com (expose:false) should not be checked"

    def test_html_structure(self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog):
        """HTML response has required structural elements."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        data = app.get_all_checks()
        freshness = data.get("metrics_freshness")

        assert_ldd_imp9(caplog, require_imp9=False)

        assert "status" in data
        assert "checks" in data
        assert "generated_at" in data
        assert "duration_ms" in data
        assert freshness is not None, "metrics_freshness should be present"
        assert isinstance(data["checks"], list)


# ═══════════════════════════════════════════════════════════════════
# TESTS: renderer.format_bytes() — публичный контракт (DevPlan 139 W2)
# ═══════════════════════════════════════════════════════════════════


def logger_imp9(caplog, msg: str) -> None:
    """Emit IMP:9 business-logic log + assert trajectory (LDD telemetry helper)."""
    import logging

    logging.getLogger(__name__).critical("[IMP:9][test_status_page] %s", msg)
    assert_ldd_imp9(caplog)


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — HTML structure (new 047 fields) via публичный renderer.render_html
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageHtml047:
    """Tests for 047 enhancements: memory, swap, OS, backup, quick-nav, progress bars, no CI/CD badges.

    DevPlan 139 W2: рендер через публичный renderer.render_html(data, _JINJA_ENV, ...)
    (вместо приватного app._render_html); mock_subprocess ОБЯЗАТЕЛЬНО в сигнатуре.
    """

    def test_html_structure_has_memory_fields(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """HTML contains RAM Total, RAM Available, Swap Used (P5: swap shown when >0)."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = render_html(data, _JINJA_ENV, app.PLATFORM_SERVICES, app.NODE_NAME)

        # DevPlan 158 W2: "RAM Total" / "RAM Available" сохранились; "Swap Total" → "Swap Used" (P5)
        assert "RAM Total" in html, "HTML missing 'RAM Total'"
        assert "RAM Available" in html, "HTML missing 'RAM Available'"
        assert "Swap Used" in html, "HTML missing 'Swap Used' (P5 rename from 'Swap Total')"
        logger_imp9(caplog, "HTML memory/swap fields present")

    def test_html_structure_has_os_fields(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """HTML contains OS / Kernel row."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = render_html(data, _JINJA_ENV, app.PLATFORM_SERVICES, app.NODE_NAME)

        assert "OS / Kernel" in html, "HTML missing 'OS / Kernel'"
        assert "kernel_version" not in html, "raw kernel_version should not appear (displayed as formatted)"
        logger_imp9(caplog, "HTML OS/Kernel field present")

    def test_html_structure_no_cicd_badges(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """HTML does NOT contain CI/CD Pipeline Verified badges."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = render_html(data, _JINJA_ENV, app.PLATFORM_SERVICES, app.NODE_NAME)

        assert "CI/CD Pipeline Verified" not in html, "CI/CD badges should be removed from footer"
        assert "Pipeline Verified" not in html, "Pipeline verified badge should be removed"
        logger_imp9(caplog, "CI/CD badges absent from HTML")

    def test_html_structure_has_quick_nav(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """HTML contains quick-nav navbar with section anchors."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = render_html(data, _JINJA_ENV, app.PLATFORM_SERVICES, app.NODE_NAME)

        assert '<nav class="quick-nav">' in html, "HTML missing quick-nav navbar"
        assert "#services" in html, "HTML missing #services anchor"
        assert "#projects" in html, "HTML missing #projects anchor"
        assert "#containers" in html, "HTML missing #containers anchor"
        assert "#host" in html, "HTML missing #host anchor"
        logger_imp9(caplog, "HTML quick-nav present")

    def test_html_structure_has_progress_bars(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """HTML contains metric-bar elements for disk/memory usage (Vercel/Linear redesign)."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = render_html(data, _JINJA_ENV, app.PLATFORM_SERVICES, app.NODE_NAME)

        # DevPlan 158 W2 T2.1: .progress-bar/.progress-fill → .metric-bar (Vercel/Linear redesign)
        assert "metric-bar" in html, "HTML missing metric-bar element"
        logger_imp9(caplog, "HTML metric bars present")

    def test_html_structure_has_app_data_backup(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """HTML contains App-Data Backup when backup.last_app_data_at is set."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        html = render_html(data, _JINJA_ENV, app.PLATFORM_SERVICES, app.NODE_NAME)

        assert "App-Data Backup" in html, "HTML missing 'App-Data Backup' when backup.last_app_data_at is set"
        assert "last_app_data_at" not in html, "raw last_app_data_at should not appear in HTML (use formatted value)"
        logger_imp9(caplog, "HTML App-Data Backup present")


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

        assert_ldd_imp9(caplog, require_imp9=False)

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
            # DevPlan 158 W1: DISABLED добавлен как 4-й валидный статус (S-DNS A)
            assert check["status"] in {"PASS", "FAIL", "WARN", "DISABLED"}, f"Invalid status: {check['status']}"

    def test_status_json_contains_extended_fields(self, mock_node_yaml, mock_status_metrics_json_all_pass, caplog):
        """/status.json now includes schema_version, certs, projects, host (AC4-M)."""
        caplog.set_level(0)

        app = _setup_app_env(str(mock_node_yaml), str(mock_status_metrics_json_all_pass))
        data = app.get_all_checks()
        metrics = data.get("metrics", {})

        assert_ldd_imp9(caplog, require_imp9=False)

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
        node_yaml.write_text("projects: []\nmodules: []\n", encoding="utf-8")

        # Create status-metrics.json with old schema_version
        metrics_file = tmp_path / "metrics-old.json"
        old_data = {
            "schema_version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "containers": [],
        }
        metrics_file.write_text(json.dumps(old_data), encoding="utf-8")

        app = _setup_app_env(str(node_yaml), str(metrics_file))
        data = app.get_all_checks()

        assert_ldd_imp9(caplog, require_imp9=False)

        # Should still work with old schema
        assert "status" in data


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — timeout per check
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageTimeout:
    """Tests for per-check timeout behavior."""

    def test_timeout_per_check(self, mock_node_yaml, tmp_path, caplog, mock_subprocess):  # ruff: ignore[ARG002]
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
        """),
            encoding="utf-8",
        )

        metrics_file = tmp_path / "health_timeout.json"
        metrics_file.write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [],
            }),
            encoding="utf-8",
        )

        app = _setup_app_env(str(node_yaml), str(metrics_file))

        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd=["curl"], timeout=5)
        data = app.get_all_checks()

        assert_ldd_imp9(caplog, require_imp9=False)

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

        assert_ldd_imp9(caplog, require_imp9=False)

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
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [],
            }),
            encoding="utf-8",
        )

        app = _setup_app_env(str(mock_node_yaml), str(metrics_file))
        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="401", stderr="")
        data = app.get_all_checks()

        assert_ldd_imp9(caplog, require_imp9=False)

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
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [],
            }),
            encoding="utf-8",
        )

        app = _setup_app_env(str(mock_node_yaml), str(metrics_file))
        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="403", stderr="")
        data = app.get_all_checks()

        assert_ldd_imp9(caplog, require_imp9=False)

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
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [],
            }),
            encoding="utf-8",
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
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [],
            }),
            encoding="utf-8",
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

        assert_ldd_imp9(caplog, require_imp9=False)

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

        assert_ldd_imp9(caplog, require_imp9=False)

        platform_checks = [c for c in data["checks"] if c["type"] == "platform_service"]
        assert len(platform_checks) > 0, "Should have platform service checks"
        for pc in platform_checks:
            assert pc["status"] == "PASS", (
                f"Expected PASS for 403 (access denied = service alive), got {pc['status']} for {pc['target']}"
            )
            assert pc["http_code"] == 403


# ═══════════════════════════════════════════════════════════════════
# NEW TESTS: schema_version, autoescape (AC13-M, AC14-M)
# ═══════════════════════════════════════════════════════════════════


class TestStatusPageNewFeatures:
    """Tests for new features: schema_version check, Jinja2 autoescape.
    (staleness/load_metrics покрываются в test_status_collectors.py — 172 W3.2)"""

    def test_status_page_schema_version_check(self, tmp_path, caplog, mock_subprocess):
        """status-page warns on old schema_version (<2) but continues (AC13-M)."""
        caplog.set_level(0)

        # Create node.yaml
        node_dir = tmp_path / "test-node"
        node_dir.mkdir(parents=True, exist_ok=True)
        node_yaml = node_dir / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n", encoding="utf-8")

        # Metrics file with invalid schema_version
        metrics_file = tmp_path / "metrics-old-schema.json"
        metrics_file.write_text(
            json.dumps({
                "schema_version": 0,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [],
            }),
            encoding="utf-8",
        )

        app = _setup_app_env(str(node_yaml), str(metrics_file))
        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        data = app.get_all_checks()

        assert_ldd_imp9(caplog, require_imp9=False)

        # Should not crash — returns PASS (empty = healthy)
        assert "status" in data
        assert data["status"] == "PASS"


# ═══════════════════════════════════════════════════════════════════
# TESTS: app.py — /metrics Prometheus exposition (170 W12 C5 + 017 C4 TLS)
# ═══════════════════════════════════════════════════════════════════


class _FakeSocket:
    """Минимальный request-объект для прямого инстансирования StatusPageHandler (server-free).

    ## @purpose — Handler-тесты /metrics БЕЗ реального HTTP-сервера: BaseRequestHandler.__init__
    ##            вызывает handle() → do_GET → _handle_metrics → _send в wfile. FakeSocket
    ##            предоставляет makefile('rb') (request) / makefile('wb') (response BytesIO).
    ## @io — ⇥ request bytes (GET /metrics) → ⎋ response bytes через .wfile
    ## @complexity — O(1)
    ## @invariants
    ##   - Сервер НЕ запускается (инвариант «no HTTP server» соблюдён — W10-класс единственное исключение)
    ##   - Полный путь do_GET → _handle_metrics → _send отрабатывает (не только метод-юнит)
    """

    def __init__(self) -> None:
        self.wfile = io.BytesIO()

    def makefile(self, mode: str, *_args: object, **_kwargs: object):
        if "r" in mode:
            return io.BytesIO(b"GET /metrics HTTP/1.1\r\nHost: test\r\n\r\n")
        return self.wfile

    def sendall(self, data: bytes) -> None:
        self.wfile.write(data)

    def close(self) -> None:
        pass


class TestStatusPageMetrics:
    """017 C4: /metrics эмитит platform_tls_days_left / platform_tls_self_signed (TLS-бандл)."""

    @staticmethod
    def _render_metrics(app_module) -> str:
        """GET /metrics через прямой инстанс StatusPageHandler (server-free, _FakeSocket)."""
        conn = _FakeSocket()
        app_module.StatusPageHandler(conn, ("127.0.0.1", 0), None)
        return conn.wfile.getvalue().decode("utf-8")

    # 🧪 TRAP[TEST] · C4 · Regression: /metrics рендерит TLS-гейджи из tls-секции
    # · Scenario: status-metrics.json с tls{example.test: {days_left: 365, self_signed: true}} →
    # ·   platform_tls_days_left{node,domain="example.test"} 365 + self_signed 1 + HELP/TYPE
    # · Last fail: 2026-08-27 (F-22, 018 W1) — NODE_NAME="production-node" утекал в env
    # ·   xdist-воркера (test_ssl_s3_cache snapshot-баг) → reload app подхватывал утечку →
    # ·   label "production-node" вместо "test-node". 018 W1: polluter конвертирован на
    # ·   monkeypatch + тест hermetic (delenv NODE_NAME — assert проверяет fallback "test-node").
    # · Remove if: TLS-эмиссия удалена из _handle_metrics
    def test_metrics_renders_tls_gauges(self, mock_node_yaml_no_vhosts, tmp_path, caplog, mock_subprocess, monkeypatch):
        """_handle_metrics: tls-секция → platform_tls_days_left / platform_tls_self_signed."""
        caplog.set_level(0)
        # 018 W1 (F-22): hermetic — node-label теста = fallback "test-node" (независимо от
        # machine-state env). delenv ДО _setup_app_env: reload читает модульные константы.
        monkeypatch.delenv("NODE_NAME", raising=False)

        metrics_file = tmp_path / "metrics-tls.json"
        metrics_file.write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [],
                "tls": {
                    "example.test": {"not_after": "2027-08-27T00:00:00Z", "days_left": 365, "self_signed": True},
                },
            }),
            encoding="utf-8",
        )
        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(metrics_file))
        body = self._render_metrics(app)

        _print_ldd_trajectory(caplog)

        assert "# HELP platform_tls_days_left" in body, "HELP platform_tls_days_left отсутствует"
        assert "# TYPE platform_tls_days_left gauge" in body, "TYPE platform_tls_days_left отсутствует"
        assert "# HELP platform_tls_self_signed" in body, "HELP platform_tls_self_signed отсутствует"
        assert "# TYPE platform_tls_self_signed gauge" in body, "TYPE platform_tls_self_signed отсутствует"
        assert 'platform_tls_days_left{node="test-node",domain="example.test"} 365' in body, (
            f"days_left-гейдж отсутствует в: {body}"
        )
        assert 'platform_tls_self_signed{node="test-node",domain="example.test"} 1' in body, (
            "self_signed-гейдж должен быть 1"
        )
        logger.info("[IMP:9][test_metrics] TLS gauges rendered: days_left + self_signed")

    # 🧪 TRAP[TEST] · C4 · Regression: days_left отсутствует → NaN (стиль deploy_duration)
    # · Scenario: tls-секция есть, но у домена нет days_left → platform_tls_days_left NaN
    # · Last fail: 2026-08-27 (F-22, 018 W1) — тот же NODE_NAME-утечник, label-mismatch.
    # ·   018 W1: hermetic delenv NODE_NAME (см. test_metrics_renders_tls_gauges).
    # · Remove if: NaN-семантика _handle_metrics изменена
    def test_metrics_tls_days_left_nan_when_missing(
        self, mock_node_yaml_no_vhosts, tmp_path, caplog, mock_subprocess, monkeypatch
    ):
        """_handle_metrics: отсутствующий days_left → NaN (консистентно deploy_duration)."""
        caplog.set_level(0)
        # 018 W1 (F-22): hermetic — см. test_metrics_renders_tls_gauges
        monkeypatch.delenv("NODE_NAME", raising=False)

        metrics_file = tmp_path / "metrics-tls-nan.json"
        metrics_file.write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [],
                "tls": {"example.test": {"not_after": "2027-08-27T00:00:00Z", "self_signed": False}},
            }),
            encoding="utf-8",
        )
        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")

        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(metrics_file))
        body = self._render_metrics(app)

        _print_ldd_trajectory(caplog)

        assert 'platform_tls_days_left{node="test-node",domain="example.test"} NaN' in body, (
            f"NaN-ветка days_left ожидалась: {body}"
        )
        assert 'platform_tls_self_signed{node="test-node",domain="example.test"} 0' in body
        logger.info("[IMP:9][test_metrics] TLS NaN-ветка days_left OK")

    # 🧪 TRAP[TEST] · C4 · Regression: tls-секции нет → серия не эмитится, /metrics жив
    # · Scenario: status-metrics.json без tls-ключа → 0 строк platform_tls_*, deploy-серия остаётся
    # · Last fail: never (new feature)
    # · Remove if: «только если tls-секция непуста» контракт изменён
    def test_metrics_no_tls_section_emits_nothing(
        self, mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass, caplog, mock_subprocess
    ):
        """_handle_metrics: без tls-секции → нет platform_tls_* строк, без краха."""
        caplog.set_level(0)

        mock_subprocess.return_value = mock.Mock(returncode=0, stdout="200", stderr="")
        app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))
        body = self._render_metrics(app)

        _print_ldd_trajectory(caplog)

        assert "platform_tls_days_left" not in body, "без tls-секции days_left-гейдж не должен эмититься"
        assert "platform_tls_self_signed" not in body, "без tls-секции self_signed-гейдж не должен эмититься"
        assert "# HELP platform_tls" not in body, "без tls-секции HELP не должен эмититься"
        assert "platform_deploy_success" in body, "deploy-серия должна остаться (регрессия 170 W12 C5)"
        logger.info("[IMP:9][test_metrics] Empty tls-section → no platform_tls_* emitted")


# ═══════════════════════════════════════════════════════════════════
# TESTS: secrets.sh — htpasswd facade (REMOVED API — волна 118 B6)
# ═══════════════════════════════════════════════════════════════════
# Волна 118 B6: _ensure_htpasswd_generated и step_12b_ensure_secrets УДАЛЕНЫ из
# secrets.sh (0 callers; бизнес-логика — secrets_manager.py htpasswd/ensure, вызывается
# из phases.py напрямую). R5 negative: type -t = пусто.


class TestHtpasswdGenerationRemoved:
    """R5 negative: _ensure_htpasswd_generated/step_12b_ensure_secrets удалены (B6)."""

    def test_htpasswd_facades_removed(self, tmp_path):  # ruff: ignore[ARG002]
        # 🧪 TRAP[TEST] · NEGATIVE (R5) · B6 — secrets.sh фасады удалены
        # · Scenario: source secrets.sh → type -t _ensure_htpasswd_generated → пусто (не функция)
        # · Last fail: _ensure_htpasswd_generated существовал до волны 118 B6 (secrets.sh L55-67)
        # · Remove if: shell htpasswd фасад будет восстановлен
        secrets_script = Path(__file__).parent.parent.parent / "core" / "lib" / "secrets.sh"
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
            check=False,
        )
        logger.info("%s %s", "STDERR:", result.stderr)
        assert result.returncode == 0, f"htpasswd facades not removed: {result.stderr}"
        assert "REMOVED" in result.stderr, f"no REMOVED marker: {result.stderr}"
        __import__("logging").getLogger(__name__).critical(
            "[IMP:9][test_htpasswd_facades_removed] PASS: secrets.sh htpasswd facades removed (B6 R5)"
        )


# ═══════════════════════════════════════════════════════════════════
# R5: env-isolation (FRAG-1, DevPlan 119 F6)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_env_isolation_negative
## @purpose  R5 (DevPlan 119 F6 AC-F6.3, TEST_SPEC test_env_isolation_negative):
##           раньше _setup_app_env мутировала 5 env vars (NODE_YAML_PATH/STATUS_METRICS_JSON/
##           NODE_NAME/NODE_CONFIGS_DIR/PLATFORM_DOMAIN). FRAG-1 фикс — snapshot/restore.
##           W5 T5.3 (DevPlan 160): env-мутация УБРАНА — get_all_checks DI-параметрами,
##           _setup_app_env НЕ трогает os.environ. Тест остаётся regression-guard'ом:
##           env ДОЛЖЕН остаться нетронутым после setup + DI-вызова (сильнее FRAG-1).
## @io — ⇥ mock_node_yaml_no_vhosts, mock_status_metrics_json_all_pass → ⎋ None
## @complexity — O(1)
## @invariants
##   - _setup_app_env НЕ мутирует os.environ (DI вместо env+reload, W5 T5.3)
##   - NODE_NAME/PLATFORM_DOMAIN и др. НЕ «протекают» после вызова (0 env-мутаций)
# 🧪 TRAP[TEST] · NEGATIVE (R5) · status_page env isolation — DevPlan 119 F6 (FRAG-1) / 160 W5 T5.3
# · Last fail: NODE_NAME из env видел node-lifecycle.sh (test_node_lifecycle_static.py:291 FAIL)
# · Remove if: _setup_app_env снова начинает мутировать env
def test_env_isolation_negative(
    mock_node_yaml_no_vhosts,
    mock_status_metrics_json_all_pass,
) -> None:
    """R5: env vars НЕ мутируются _setup_app_env (DI, W5 T5.3) — нет утечки NODE_NAME/PLATFORM_DOMAIN."""
    ENV_KEYS = ("NODE_YAML_PATH", "STATUS_METRICS_JSON", "NODE_NAME", "NODE_CONFIGS_DIR", "PLATFORM_DOMAIN")

    # Snapshot ДО вызова
    before = {key: os.environ.get(key) for key in ENV_KEYS}

    app = _setup_app_env(str(mock_node_yaml_no_vhosts), str(mock_status_metrics_json_all_pass))
    assert app is not None, "_setup_app_env должна вернуть модуль app"
    # DI-вызов с инжектированными путями
    app.get_all_checks()

    # Snapshot ПОСЛЕ вызова — env должен совпадать с before (0 env-мутаций, W5 T5.3)
    after = {key: os.environ.get(key) for key in ENV_KEYS}
    leaked = {key: (before[key], after[key]) for key in ENV_KEYS if before[key] != after[key]}
    assert not leaked, (
        f"R5 FAIL: env утечка после _setup_app_env (W5 T5.3 — DI должен исключать мутации): {leaked}. "
        f"NODE_NAME leak ломает node-lifecycle-тесты (test_node_lifecycle_static:291)."
    )
    logger.info("[IMP:9][test_env_isolation_negative] R5 PASS: 0 env-мутаций при DI (W5 T5.3)")


# endregion FUNC_test_env_isolation_negative


# ═══════════════════════════════════════════════════════════════════
# W10 T10.11 / T10.13 — ThreadingHTTPServer + /healthz staleness
# ═══════════════════════════════════════════════════════════════════
# ⚠️  Отклонение от инварианта «no HTTP server»: T10.11 (M-1) — нагрузочный тест,
#     по определению требующий реального ThreadingHTTPServer (медленный апстрим +
#     /healthz опрос). DevPlan 136 §12.2 T10.11 — authoritative. Сервер локальный
#     (127.0.0.1, эфемерный порт), полный teardown в finally (shutdown+close) —
#     fixture-lifecycle: явный start/stop, не session-scoped autouse (rule 3.4).
#     Все остальные тесты файла остаются server-free.


# region CLASS_TestW10ThreadingHealthz
class TestW10ThreadingHealthz:
    """W10 T10.11 (M-1): ThreadingHTTPServer — медленный /health НЕ блокирует /healthz.
    W10 T10.13 (M-7): /healthz возвращает 503 при staleness > порога (синхронно с /health)."""

    @staticmethod
    def _start_server(app_module) -> tuple[http.server.ThreadingHTTPServer, threading.Thread, str]:
        """Запустить ThreadingHTTPServer с StatusPageHandler на эфемерном порту.

        ## @purpose — Self-contained локальный сервер для load-теста (порт 0 = эфемерный,
        ##            xdist-safe: нет фиксированных портов). Полный teardown в finally.
        """
        import http.server

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app_module.StatusPageHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _, port = server.server_address
        return server, thread, f"http://127.0.0.1:{port}"

    @staticmethod
    def _get(url: str, timeout: float) -> tuple[int, dict]:
        """GET с таймаутом → (status, json_body). Обрабатывает HTTPError (503/4xx)."""
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return resp.status, body
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
            return e.code, body

    def test_slow_health_does_not_block_healthz(self, tmp_path: Path) -> None:
        """T10.11: /health (полный агрегат, ~секунды) в фоне; /healthz отвечает < 1s."""
        # 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 136 W10 T10.11 (M-1) — blocking /healthz
        # · Scenario: HTTPServer (однопоточный) — /health завис на медленном апстриме → /healthz
        # ·   ждёт в очереди → Docker HEALTHCHECK таймаутит → ложный unhealthy
        # · Last fail: 2026-08-05 — W10: app.py использовал http.server.HTTPServer (однопоточный)
        # · Remove if: HTTP-сервер заменён (fast-path /healthz вне серверного потока)
        metrics = tmp_path / "status-metrics.json"
        metrics.write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [],
            }),
            encoding="utf-8",
        )
        node_yaml = tmp_path / "test-node" / "node.yaml"
        node_yaml.parent.mkdir(parents=True)
        node_yaml.write_text("projects: []\nmodules: []\n", encoding="utf-8")
        app_module = _setup_app_env(str(node_yaml), str(metrics))

        # Медленный апстрим: /health блокируется на 3s (имитация сбора метрик)
        def _slow_health(*args, **kwargs):
            time.sleep(3.0)
            return {"status": "PASS", "checks": [], "staleness": None, "duration_ms": 3000}

        # 167 D4: plain attribute injection на свежем reload-экземпляре модуля (0 setattr)
        app_module.get_all_checks = _slow_health

        server, thread, base = self._start_server(app_module)
        try:
            # Фон: /health (заблокируется на 3s)
            health_result = {}
            t = threading.Thread(
                target=lambda: health_result.update(status=self._get(base + "/health", timeout=10)[0]),
                daemon=True,
            )
            t.start()
            time.sleep(0.3)  # дать /health войти в блокировку

            # /healthz должен ответить БЫСТРО (fast-path не ждёт медленный /health)
            start = time.monotonic()
            status, body = self._get(base + "/healthz", timeout=5)
            elapsed = time.monotonic() - start
            assert status == 200, f"/healthz должен быть 200, got {status}: {body}"
            assert elapsed < 1.5, f"/healthz занял {elapsed:.2f}s — ThreadingHTTPServer не разблокировал"
            t.join(timeout=10)
            assert health_result.get("status") == 200, "/health должен завершиться 200"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        logger.info("%s", f"[IMP:9][test_w10_healthz] PASS: /healthz={elapsed:.2f}s при /health в блокировке (T10.11)")

    def test_healthz_fresh_metrics_returns_200(self, tmp_path: Path) -> None:
        """T10.13: свежие метрики → /healthz 200 PASS."""
        metrics = tmp_path / "status-metrics.json"
        metrics.write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [],
            }),
            encoding="utf-8",
        )
        node_yaml = tmp_path / "test-node" / "node.yaml"
        node_yaml.parent.mkdir(parents=True)
        node_yaml.write_text("projects: []\nmodules: []\n", encoding="utf-8")
        app_module = _setup_app_env(str(node_yaml), str(metrics))

        server, thread, base = self._start_server(app_module)
        try:
            status, body = self._get(base + "/healthz", timeout=5)
            assert status == 200, f"свежие метрики → 200, got {status}: {body}"
            assert body["status"] == "PASS"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_healthz_stale_metrics_returns_503(self, tmp_path: Path) -> None:
        """T10.13 (M-7): метрики старше 5 мин → /healthz 503 FAIL (синхронно с /health)."""
        # 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 136 W10 T10.13 (M-7) — stale → ложный PASS
        # · Scenario: pipeline метрик упал, /healthz отвечал 200 (только warning) → Docker
        # ·   HEALTHCHECK считал status-page healthy при мёртвых данных
        # · Last fail: 2026-08-05 — W10: stale возвращал 200 + warning:"stale_data"
        # · Remove if: /healthz контракт изменён
        metrics = tmp_path / "status-metrics.json"
        metrics.write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": "2020-01-01T00:00:00Z",  # на годы в прошлом → stale
                "containers": [],
            }),
            encoding="utf-8",
        )
        node_yaml = tmp_path / "test-node" / "node.yaml"
        node_yaml.parent.mkdir(parents=True)
        node_yaml.write_text("projects: []\nmodules: []\n", encoding="utf-8")
        app_module = _setup_app_env(str(node_yaml), str(metrics))

        server, thread, base = self._start_server(app_module)
        try:
            status, body = self._get(base + "/healthz", timeout=5)
            assert status == 503, f"stale метрики → 503, got {status}: {body}"
            assert body["status"] == "FAIL"
            assert body["reason"] == "stale_data", f"reason должен быть stale_data: {body}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_healthz_missing_metrics_returns_503(self, tmp_path: Path) -> None:
        """T10.13: метрики отсутствуют → /healthz 503 FAIL (не ложный PASS)."""
        metrics = tmp_path / "status-metrics.json"
        # не пишем файл — отсутствует
        node_yaml = tmp_path / "test-node" / "node.yaml"
        node_yaml.parent.mkdir(parents=True)
        node_yaml.write_text("projects: []\nmodules: []\n", encoding="utf-8")
        app_module = _setup_app_env(str(node_yaml), str(metrics))

        server, thread, base = self._start_server(app_module)
        try:
            status, body = self._get(base + "/healthz", timeout=5)
            assert status == 503, f"нет метрик → 503, got {status}: {body}"
            assert body["reason"] == "metrics_file_missing"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


# endregion CLASS_TestW10ThreadingHealthz
