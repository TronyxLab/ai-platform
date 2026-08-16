# GREP_SUMMARY: test-status-collectors load-node-yaml resolve-node-yaml-path extract-node-name load-status-metrics vhosts modules curl-vhost curl-platform-service check-platform-service dns-probe check-container staleness get-all-checks disabled
# STRUCTURE: ┌test functions┐ → ◇ load_node_yaml (1) → ◇ resolve_node_yaml_path (4) → ◇ extract_node_name (2)
#            → ◇ _load_status_metrics (2) → ◇ get_vhosts (2) → ◇ _check_container (3) → ◇ _curl_vhost (1)
#            → ◇ _check_platform_service dns-probe (2) → ◇ _compute_staleness (1) → ◇ get_all_checks disabled/warn (2)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/status-page/collectors.py — pure data collectors
#            extracted from app.py (DevPlan 117 G T55). Characterization-based: all tests
##           reproduce pre-refactor behavior of the identical code in app.py.
## @scope    No Docker, no HTTP — subprocess/threading mocked; tmp_path for files.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T55 §TEST_SPEC — collectors direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T55 — created
## @changes  2026-08-12 · DevPlan 158 W1 T1.5 — DNS-probe/resolve/extract + DISABLED/WARN overall
# endregion MODULE_CONTRACT

import json
import sys
import time
from pathlib import Path
from unittest import mock

# status-page/ dir has a hyphen — not a valid package name. Import via sys.path
# (same pattern as tests/test_status_page.py).
_STATUS_PAGE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "status-page"
if str(_STATUS_PAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_STATUS_PAGE_DIR))

import pytest
from collectors import (
    _check_container,
    _check_platform_service,
    _curl_vhost,
    extract_node_name,
    get_all_checks,
    get_modules,
    get_vhosts,
    load_node_yaml,
    resolve_node_yaml_path,
)
from collectors import (
    compute_staleness as _compute_staleness,
)
from collectors import (
    load_status_metrics as _load_status_metrics,
)

pytestmark = pytest.mark.static_audit

# ══════════════════════════════════════════════════════════════════════
# TESTS: load_node_yaml
# ══════════════════════════════════════════════════════════════════════


class TestLoadNodeYaml:
    """Tests for load_node_yaml()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: valid node.yaml
    # · Expect: dict returned
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: load_node_yaml logic changes
    def test_load_node_yaml_reads_file(self, tmp_path: Path) -> None:
        """Valid node.yaml → dict."""
        p = tmp_path / "node.yaml"
        p.write_text("projects:\n  - name: app\n    domain: app.example.com\n    expose: true\n", encoding="utf-8")

        data = load_node_yaml(str(p))

        assert data["projects"][0]["name"] == "app"

    # 🧪 TRAP[TEST] · Regression · Scenario: missing file
    # · Expect: {} (graceful)
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: load_node_yaml logic changes
    def test_load_node_yaml_missing_file(self, tmp_path: Path) -> None:
        """Missing file → {} (no raise)."""
        data = load_node_yaml(str(tmp_path / "missing.yaml"))

        assert data == {}


# ══════════════════════════════════════════════════════════════════════
# TESTS: _load_status_metrics
# ══════════════════════════════════════════════════════════════════════


class TestLoadStatusMetrics:
    """Tests for _load_status_metrics()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: valid v2 metrics file
    # · Expect: full data returned
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: metrics loading logic changes
    def test_load_metrics_valid(self, tmp_path: Path) -> None:
        """Valid v2 metrics → data dict."""
        p = tmp_path / "status-metrics.json"
        p.write_text(json.dumps({"schema_version": 2, "containers": []}), encoding="utf-8")

        data = _load_status_metrics(str(p))

        assert data["schema_version"] == 2

    # 🧪 TRAP[TEST] · Regression · Scenario: path is a directory (P1)
    # · Expect: fallback structure with errors[]
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: metrics loading logic changes
    def test_load_metrics_dir_fallback(self, tmp_path: Path) -> None:
        """Directory path → fallback with errors."""
        data = _load_status_metrics(str(tmp_path))

        assert data["errors"]
        assert data["containers"] == []

    # 🧪 TRAP[TEST] · Regression · Scenario: unreadable/corrupt JSON
    # · Expect: fallback structure
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: metrics loading logic changes
    def test_load_metrics_corrupt(self, tmp_path: Path) -> None:
        """Corrupt JSON → fallback."""
        p = tmp_path / "status-metrics.json"
        p.write_text("{not json", encoding="utf-8")

        data = _load_status_metrics(str(p))

        assert data["errors"] == ["Failed to load status-metrics.json"]


# ══════════════════════════════════════════════════════════════════════
# TESTS: get_vhosts / get_modules
# ══════════════════════════════════════════════════════════════════════


class TestVhostsAndModules:
    """Tests for get_vhosts() and get_modules()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: expose:true only
    # · Expect: only exposed projects with domain returned
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: get_vhosts logic changes
    def test_get_vhosts_exposed_only(self) -> None:
        """Only expose:true projects with domain → vhosts."""
        node_data = {
            "projects": [
                {"name": "a", "domain": "a.example.com", "expose": True},
                {"name": "b", "domain": "b.example.com", "expose": False},
                {"name": "c", "expose": True},  # no domain → skipped
            ]
        }

        vhosts = get_vhosts(node_data)

        assert len(vhosts) == 1
        assert vhosts[0]["domain"] == "a.example.com"

    def test_get_vhosts_empty(self) -> None:
        """No projects → []."""
        assert get_vhosts({}) == []

    def test_get_modules(self) -> None:
        """get_modules returns module list."""
        assert get_modules({"modules": ["nginx", "redis"]}) == ["nginx", "redis"]


# ══════════════════════════════════════════════════════════════════════
# TESTS: _check_container
# ══════════════════════════════════════════════════════════════════════


class TestCheckContainer:
    """Tests for _check_container()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: running + healthy
    # · Expect: PASS
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: container check logic changes
    def test_check_running_healthy(self) -> None:
        """Running + healthy → PASS."""
        result = _check_container({"name": "nginx", "running": True, "healthy": True})

        assert result is not None
        assert result["status"] == "PASS"

    # 🧪 TRAP[TEST] · Regression · Scenario: exited with code 0 (oneshot)
    # · Expect: PASS
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: container check logic changes
    def test_check_exited_zero_oneshot(self) -> None:
        """Exited(0) → PASS (oneshot completed)."""
        result = _check_container({"name": "backup", "running": False, "healthy": False, "exit_code": 0})

        assert result is not None
        assert result["status"] == "PASS"

    # 🧪 TRAP[TEST] · Regression · Scenario: exited non-zero + status_line parse
    # · Expect: FAIL
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: container check logic changes
    def test_check_exited_nonzero_status_line(self) -> None:
        """Exited(1) parsed from status_line → FAIL."""
        result = _check_container({
            "name": "svc",
            "running": False,
            "healthy": False,
            "exit_code": None,
            "status_line": "Exited (1)",
        })

        assert result is not None
        assert result["status"] == "FAIL"
        assert result["exit_code"] == 1

    # 🧪 TRAP[TEST] · Regression · Scenario: self-recursion guard
    # · Expect: None (status-page excluded)
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: anti-recursion logic changes
    def test_check_status_page_excluded(self) -> None:
        """status-page container → None (anti-recursion)."""
        assert _check_container({"name": "status-page", "running": True, "healthy": True}) is None


# ══════════════════════════════════════════════════════════════════════
# TESTS: _curl_vhost / _compute_staleness
# ══════════════════════════════════════════════════════════════════════


class TestCurlAndStaleness:
    """Tests for _curl_vhost() and _compute_staleness()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: curl returns HTTP 200
    # · Expect: PASS result
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: curl vhost logic changes
    def test_curl_vhost_success(self) -> None:
        """HTTP 200 → PASS."""
        with mock.patch(
            "collectors.subprocess.run",
            return_value=mock.MagicMock(returncode=0, stdout="200", stderr=""),
        ):
            result = _curl_vhost("app.example.com")

        assert result["status"] == "PASS"
        assert result["http_code"] == 200

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · _curl_vhost --resolve — TRAP[BUG] 2026-08-12
    # · Last fail: resolve entry 'tronyx.ru:443:nginx' → curl exit 49 (container name
    #   в поле address, curl требует IP) — все vhost-чеки статус-страницы ноды FAIL
    # · Remove if: _curl_vhost перестанет строить --resolve по IP контейнера nginx
    def test_curl_vhost_resolve_uses_ip_not_container_name(self) -> None:
        """--resolve entry MUST contain nginx container IP, not bare name."""
        with (
            mock.patch("collectors.socket.gethostbyname", return_value="172.22.0.2"),
            mock.patch(
                "collectors.subprocess.run",
                return_value=mock.MagicMock(returncode=0, stdout="200", stderr=""),
            ) as run_mock,
        ):
            _curl_vhost("app.example.com")

        cmd = run_mock.call_args.args[0]
        resolve_entries = [a for a in cmd if a.startswith("app.example.com:443:")]
        assert resolve_entries == ["app.example.com:443:172.22.0.2"], cmd
        assert "app.example.com:443:nginx" not in cmd

    def test_curl_vhost_resolve_omitted_when_nginx_unresolvable(self) -> None:
        """No --resolve when nginx IP unavailable (test env without Docker)."""
        with (
            mock.patch("collectors.socket.gethostbyname", side_effect=OSError("nxdomain")),
            mock.patch(
                "collectors.subprocess.run",
                return_value=mock.MagicMock(returncode=0, stdout="200", stderr=""),
            ) as run_mock,
        ):
            _curl_vhost("app.example.com")

        cmd = run_mock.call_args.args[0]
        assert "--resolve" not in cmd

    # 🧪 TRAP[TEST] · Regression · Scenario: curl times out
    # · Expect: FAIL with timeout error
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: curl vhost timeout handling changes
    def test_curl_vhost_timeout(self) -> None:
        """TimeoutExpired → FAIL."""
        with mock.patch(
            "collectors.subprocess.run",
            side_effect=__import__("subprocess").TimeoutExpired("curl", 5),
        ):
            result = _curl_vhost("app.example.com")

        assert result["status"] == "FAIL"
        assert "timeout" in result["error"]

    # 🧪 TRAP[TEST] · Regression · Scenario: fresh generated_at
    # · Expect: None
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: staleness logic changes
    # 📝 2026-08-15 · DevPlan 171 W3.1 — sync-inventory-флак упразднён вместе с механизмом;
    # ·   флак-источник (time.strftime в parametrize) упразднён.
    # · RESOLVED 2026-08-14 (DevPlan 167): parametrize переведён на фиксированную константу
    # ·   2099-01-01T00:00:00Z (future = всегда fresh) — nodeid детерминирован.
    @pytest.mark.parametrize(
        "started_at",
        [
            "2099-01-01T00:00:00Z",  # fresh (future, фикс. константа — детерминированный nodeid)
            None,  # no timestamp
        ],
    )
    def test_staleness_not_stale(self, started_at) -> None:
        """Fresh/None timestamps → no staleness (parametrized, F5-reduction)."""
        assert _compute_staleness(started_at) is None

    # 🧪 TRAP[TEST] · Regression · Scenario: old generated_at
    # · Expect: "Xm Ys" description
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: staleness logic changes
    def test_staleness_old(self) -> None:
        """10-min-old timestamp → staleness description."""
        import datetime

        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)).isoformat()
        result = _compute_staleness(old)

        assert result is not None
        assert "m" in result

    # 🧪 TRAP[TEST] · Regression (170 W2-A2 B3) · Scenario: invalid generated_at
    # · Expect: None (fallback) + IMP:7 stderr-warning с repr входа — silent swallow устранён
    # · Last fail: collectors.py:518-519 `except (ValueError, TypeError): pass` (research-B B3)
    # · Remove if: compute_staleness fallback contract changes
    def test_staleness_invalid_input_warns_and_returns_none(self, capsys) -> None:
        """Невалидный generated_at → fallback None + warning (не тихий swallow, 170 W2-A2).

        ValueError-кейс ("not-a-date" → fromisoformat). int-аргумент НЕ включён: контракт
        str|None, и endswith() бросал бы AttributeError ещё до except (поведение сохранено).
        """
        assert _compute_staleness("not-a-date") is None

        captured = capsys.readouterr()
        assert "compute_staleness" in captured.err, (
            f"FAIL (170 W2-A2): silent swallow остался — нет warning на невалидном входе: {captured.err!r}"
        )
        assert "not-a-date" in captured.err, f"FAIL (170 W2-A2): warning без repr входа: {captured.err!r}"
        print("[IMP:9][test][staleness_invalid] invalid generated_at → None + warning (repr) — OK", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════════
# TESTS: get_all_checks (integration)
# ══════════════════════════════════════════════════════════════════════


class TestGetAllChecks:
    """Tests for get_all_checks()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: healthy container + no vhosts
    # · Expect: PASS aggregate
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: get_all_checks logic changes
    def test_get_all_checks_structure(self, tmp_path: Path) -> None:
        """get_all_checks returns aggregate keys + PASS for healthy container."""
        node_yaml = tmp_path / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n", encoding="utf-8")
        metrics = tmp_path / "status-metrics.json"
        metrics.write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [{"name": "nginx", "running": True, "healthy": True}],
            }),
            encoding="utf-8",
        )

        result = get_all_checks(str(node_yaml), str(metrics), [], per_check_timeout=2, total_timeout=10)

        assert result["status"] == "PASS"
        assert "checks" in result
        assert "metrics" in result
        assert result["checks"][0]["target"] == "nginx"


# ══════════════════════════════════════════════════════════════════════
# TESTS: resolve_node_yaml_path (DevPlan 158 W1 T1.3 — F1 glob-fallback)
# ══════════════════════════════════════════════════════════════════════


class TestResolveNodeYamlPath:
    """Tests for resolve_node_yaml_path() — F1 glob-fallback fix."""

    # 🧪 TRAP[TEST] · Regression · Scenario: exact path wins over glob (F1)
    # · Expect: exact path returned when file exists
    # · Last fail: None (new test for DevPlan 158 W1 T1.3)
    # · Remove if: resolve_node_yaml_path logic changes
    def test_resolve_node_yaml_path_exact_wins(self, tmp_path: Path) -> None:
        """Exact path → returned directly."""
        # Create the node-configs/<name>/node.yaml structure
        node_configs = tmp_path / "node-configs"
        node_configs.mkdir()
        (node_configs / "tronyx-vps").mkdir()
        exact = node_configs / "tronyx-vps" / "node.yaml"
        exact.write_text("node:\n  name: tronyx-vps\n", encoding="utf-8")

        result = resolve_node_yaml_path(str(exact))

        assert result == str(exact)

    # 🧪 TRAP[TEST] · Regression · Scenario: glob fallback finds single candidate (F1 core)
    # · Expect: broken exact path (empty dir) → single candidate via glob
    # · Last fail: None (new test for DevPlan 158 W1 T1.3)
    # · Remove if: glob-fallback logic changes
    def test_resolve_node_yaml_path_glob_fallback(self, tmp_path: Path) -> None:
        """Broken exact path (unknown/) → glob finds the real node.yaml."""
        node_configs = tmp_path / "node-configs"
        node_configs.mkdir()
        # The "unknown" subdir exists but is empty (Docker creates it on missing mount)
        (node_configs / "unknown").mkdir()
        # The real node.yaml lives in a different subdir
        (node_configs / "tronyx-vps").mkdir()
        real = node_configs / "tronyx-vps" / "node.yaml"
        real.write_text("node:\n  name: tronyx-vps\n", encoding="utf-8")

        # Path points to unknown/node.yaml (broken mount)
        broken = node_configs / "unknown" / "node.yaml"
        result = resolve_node_yaml_path(str(broken))

        assert result == str(real)

    # 🧪 TRAP[TEST] · Regression · Scenario: scripts/secrets dirs skipped
    # · Expect: scripts/ and secrets/ subdirs not considered as candidates
    # · Last fail: None (new test for DevPlan 158 W1 T1.3)
    # · Remove if: skip-list logic changes
    def test_resolve_node_yaml_path_skips_scripts_secrets(self, tmp_path: Path) -> None:
        """scripts/ and secrets/ dirs skipped during glob."""
        node_configs = tmp_path / "node-configs"
        node_configs.mkdir()
        # node-configs/scripts/node.yaml and node-configs/secrets/node.yaml exist (noise)
        (node_configs / "scripts").mkdir()
        (node_configs / "scripts" / "node.yaml").write_text("# noise\n", encoding="utf-8")
        (node_configs / "secrets").mkdir()
        (node_configs / "secrets" / "node.yaml").write_text("# noise\n", encoding="utf-8")
        # The real node.yaml
        (node_configs / "tronyx-vps").mkdir()
        real = node_configs / "tronyx-vps" / "node.yaml"
        real.write_text("node:\n  name: tronyx-vps\n", encoding="utf-8")

        broken = node_configs / "unknown" / "node.yaml"
        result = resolve_node_yaml_path(str(broken))

        assert result == str(real)

    # 🧪 TRAP[TEST] · Regression · Scenario: no candidates → None → load_node_yaml → {}
    # · Expect: None when no node.yaml found anywhere
    # · Last fail: None (new test for DevPlan 158 W1 T1.3)
    # · Remove if: None-return logic changes
    def test_resolve_node_yaml_path_none_when_missing(self, tmp_path: Path) -> None:
        """Empty node-configs → None → graceful {} downstream."""
        node_configs = tmp_path / "node-configs"
        node_configs.mkdir()
        # No node.yaml anywhere
        broken = node_configs / "unknown" / "node.yaml"

        result = resolve_node_yaml_path(str(broken))

        assert result is None
        # Verify graceful downstream: load_node_yaml on the original path → {}
        data = load_node_yaml(str(broken))
        assert data == {}


# ══════════════════════════════════════════════════════════════════════
# TESTS: extract_node_name (DevPlan 158 W1 T1.3 — S-NAME B)
# ══════════════════════════════════════════════════════════════════════


class TestExtractNodeName:
    """Tests for extract_node_name() — S-NAME B (node.yaml primary)."""

    # 🧪 TRAP[TEST] · Regression · Scenario: node.name nested (confirmed format T1.6)
    # · Expect: "tronyx-vps" extracted from node.name
    # · Last fail: None (new test for DevPlan 158 W1 T1.3)
    # · Remove if: extract_node_name format changes
    def test_extract_node_name_from_yaml(self) -> None:
        """node.name (nested) extracted as primary source."""
        node_data = {"node": {"name": "tronyx-vps", "host": "1.2.3.4"}}

        assert extract_node_name(node_data) == "tronyx-vps"

    # 🧪 TRAP[TEST] · Regression · Scenario: missing node.name → fallback
    # · Expect: fallback "unknown" when no name anywhere
    # · Last fail: None (new test for DevPlan 158 W1 T1.3)
    # · Remove if: fallback logic changes
    def test_extract_node_name_fallback(self) -> None:
        """Empty dict → fallback 'unknown'."""
        assert extract_node_name({}) == "unknown"
        # Custom fallback
        assert extract_node_name({}, fallback="env-node") == "env-node"
        # node section exists but no name
        assert extract_node_name({"node": {"host": "1.2.3.4"}}) == "unknown"
        # Top-level name as secondary source
        assert extract_node_name({"name": "top-level"}) == "top-level"


# ══════════════════════════════════════════════════════════════════════
# TESTS: _check_platform_service DNS-probe (DevPlan 158 W1 T1.1 — S-DNS A)
# ══════════════════════════════════════════════════════════════════════


class TestCheckPlatformServiceDnsProbe:
    """Tests for _check_platform_service() — DNS probe → DISABLED."""

    # 🧪 TRAP[TEST] · Regression · Scenario: DNS unresolved → DISABLED (S-DNS A)
    # · Expect: DISABLED status, no curl invocation
    # · Last fail: None (new test for DevPlan 158 W1 T1.1)
    # · Remove if: DNS-probe logic changes
    def test_check_platform_service_dns_unresolved_returns_disabled(self) -> None:
        """Unresolved DNS → DISABLED, curl never called."""
        with (
            mock.patch("collectors.socket.gethostbyname", side_effect=OSError("nxdomain")),
            mock.patch("collectors.checks.platform._curl_platform_service") as curl_mock,
        ):
            result = _check_platform_service("grafana:3000", "/api/health")

        assert result["status"] == "DISABLED"
        assert result["target"] == "grafana"
        assert "DNS unresolved" in result["error"]
        curl_mock.assert_not_called()

    # 🧪 TRAP[TEST] · Regression · Scenario: DNS resolved → proceeds to curl
    # · Expect: curl invoked, result from curl returned
    # · Last fail: None (new test for DevPlan 158 W1 T1.1)
    # · Remove if: DNS-probe logic changes
    def test_check_platform_service_dns_resolved_proceeds_to_curl(self) -> None:
        """Resolved DNS → curl invoked, its result returned."""
        curl_result = {"target": "grafana", "status": "PASS", "http_code": 200}
        with (
            mock.patch("collectors.socket.gethostbyname", return_value="172.22.0.5"),
            mock.patch("collectors.checks.platform._curl_platform_service", return_value=curl_result) as curl_mock,
        ):
            result = _check_platform_service("grafana:3000", "/api/health")

        assert result == curl_result
        curl_mock.assert_called_once_with("grafana:3000", "/api/health", 5)


# ══════════════════════════════════════════════════════════════════════
# TESTS: get_all_checks — DISABLED/WARN overall logic (DevPlan 158 W1 T1.2 — S-WARN A)
# ══════════════════════════════════════════════════════════════════════


class TestGetAllChecksDisabledWarn:
    """Tests for get_all_checks() — DISABLED excluded, WARN strict (S-WARN A)."""

    # 🧪 TRAP[TEST] · Regression · Scenario: DISABLED check doesn't fail overall
    # · Expect: PASS overall even with DISABLED platform service
    # · Last fail: None (new test for DevPlan 158 W1 T1.2)
    # · Remove if: DISABLED-exclusion logic changes
    def test_get_all_checks_disabled_does_not_fail_overall(self, tmp_path: Path) -> None:
        """DISABLED check → overall PASS (not-deployed is configuration)."""
        node_yaml = tmp_path / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n", encoding="utf-8")
        metrics = tmp_path / "status-metrics.json"
        metrics.write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [{"name": "nginx", "running": True, "healthy": True}],
            }),
            encoding="utf-8",
        )
        # Platform service DNS unresolved → DISABLED
        with mock.patch("collectors.socket.gethostbyname", side_effect=OSError("nxdomain")):
            result = get_all_checks(
                str(node_yaml),
                str(metrics),
                [{"internal": "grafana:3000", "health_path": "/api/health"}],
                per_check_timeout=2,
                total_timeout=10,
            )

        statuses = [c["status"] for c in result["checks"]]
        assert "DISABLED" in statuses
        assert "PASS" in statuses
        # DISABLED excluded from overall → PASS
        assert result["status"] == "PASS", f"DISABLED must not fail overall, got {result['status']}"

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · WARN строго валит overall (S-WARN A, 2026-08-12)
    # · Last fail: WARN-чеки учитывались в overall → WARN → FAIL (правильно), но если
    # ·   DISABLED-исключение случайно поглотит WARN — регрессия. Тест фиксирует строгость.
    # · Remove if: WARN-строгость (S-WARN A) пересмотрена
    def test_get_all_checks_warn_still_fails_overall(self, tmp_path: Path) -> None:
        """WARN check → overall FAIL (S-WARN A: WARN stays strict)."""
        node_yaml = tmp_path / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n", encoding="utf-8")
        metrics = tmp_path / "status-metrics.json"
        metrics.write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                # running + not healthy → WARN
                "containers": [{"name": "degraded", "running": True, "healthy": False}],
            }),
            encoding="utf-8",
        )

        result = get_all_checks(str(node_yaml), str(metrics), [], per_check_timeout=2, total_timeout=10)

        statuses = [c["status"] for c in result["checks"]]
        assert "WARN" in statuses
        # WARN must FAIL overall (S-WARN A — only DISABLED is excluded)
        assert result["status"] == "FAIL", f"WARN must fail overall, got {result['status']}"
