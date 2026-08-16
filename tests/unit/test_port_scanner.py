# GREP_SUMMARY: test-port-scanner extract_host_port scan_compose_ports scan_test_ports port-mapping override-tag
# STRUCTURE: ┌12 test functions┐ → ◇ extract_host_port formats (6) → ◇ scan_compose_ports (3) → ◇ scan_test_ports (3)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scripts/port_scanner.py — direct tests of the
##           port scanner extracted from generate_platform_env.py (DevPlan 117 G T56).
## @scope    No Docker required — pure YAML/regex logic on tmp_path fixtures.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T56 §TEST_SPEC — port scanner direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T56 — created
# endregion MODULE_CONTRACT

from pathlib import Path

import pytest

from core.internal.scripts.port_scanner import (
    _PORT_NAME_MAP,
    extract_host_port,
    scan_compose_ports,
    scan_test_ports,
)

pytestmark = pytest.mark.static_audit

# ══════════════════════════════════════════════════════════════════════
# TESTS: extract_host_port formats
# ══════════════════════════════════════════════════════════════════════


class TestExtractHostPort:
    """Tests for extract_host_port() — 5 supported mapping formats."""

    # 🧪 TRAP[TEST] · Regression · Scenario: "8080:8080" bare mapping
    # · Expect: 8080
    # · Last fail: None (new test for DevPlan 117 G T56)
    # · Remove if: extract_host_port regex changes
    @pytest.mark.parametrize(
        ("mapping", "expected"),
        [
            ("8080:8080", 8080),  # Pattern 4: bare
            ("127.0.0.1:8080:8080", 8080),  # Pattern 3: IP-prefixed
            ("0.0.0.0:9090:9090", 9090),  # Pattern 3: zero-IP
            ("127.0.0.1:${PORT:-5432}:5432", 5432),  # Pattern 1: IP + env default
            ("${PORT:-5432}:5432", 5432),  # Pattern 2: env default bare
            (" ${PORT:-5432}:5432 ", 5432),  # whitespace stripped
        ],
    )
    def test_extract_host_port_parses(self, mapping: str, expected: int) -> None:
        """Valid mapping formats → host port int."""
        assert extract_host_port(mapping) == expected

    # 🧪 TRAP[TEST] · Regression · Scenario: variable-only mapping (no default)
    # · Expect: None (skip)
    # · Last fail: None (new test for DevPlan 117 G T56)
    # · Remove if: extract_host_port regex changes
    @pytest.mark.parametrize(
        "mapping",
        [
            "${PORT}:8080",  # variable-only (no default) → None
            "   ",  # empty string → None
        ],
    )
    def test_extract_host_port_none_cases(self, mapping: str, caplog) -> None:
        """extract_host_port → None для variable-only/empty mappings (F5)."""
        caplog.set_level(0)
        assert extract_host_port(mapping) is None

    # 🧪 TRAP[TEST] · Regression · Scenario: unparseable garbage mapping
    # · Expect: None + WARN log
    # · Last fail: None (new test for DevPlan 117 G T56)
    # · Remove if: extract_host_port regex changes
    def test_extract_host_port_unknown_returns_none(self, caplog) -> None:
        """Garbage mapping → None + [UNKNOWN] warning."""
        caplog.set_level(0)
        assert extract_host_port("http://example.com") is None
        assert any("Cannot parse port mapping" in r.message for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════
# TESTS: scan_compose_ports
# ══════════════════════════════════════════════════════════════════════


class TestScanComposePorts:
    """Tests for scan_compose_ports() with tmp_path compose fixtures."""

    # 🧪 TRAP[TEST] · Regression · Scenario: minio-style module (service==module, 2 ports)
    # · Expect: MINIO_PORT=9000 first, MINIO_CONSOLE_PORT=9001 second (177 W2.5 — канон-оверрайд;
    #   ранее MINIO_MINIO_PORT — мусорный дубль канона MINIO_CONSOLE_PORT)
    # · Last fail: None (new test for DevPlan 117 G T56; updated 177 W2.5)
    # · Remove if: naming scheme changes
    def test_scan_compose_ports_service_equals_module_two_ports(self, tmp_path: Path) -> None:
        """First port → MODULE_PORT, second → канон-имя MINIO_CONSOLE_PORT (177 W2.5)."""
        module_dir = tmp_path / "minio"
        module_dir.mkdir()
        (module_dir / "docker-compose.base.yml").write_text(
            'services:\n  minio:\n    ports:\n      - "9000:9000"\n      - "9001:9001"\n',
            encoding="utf-8",
        )

        result = scan_compose_ports(tmp_path)

        assert result["MINIO_PORT"] == 9000
        assert result["MINIO_CONSOLE_PORT"] == 9001
        assert "MINIO_MINIO_PORT" not in result, "177 W2.5: мусорное имя MINIO_MINIO_PORT не генерируется"

    # 🧪 TRAP[TEST] · Regression · Scenario: multi-service module (infra-metrics style)
    # · Expect: INFRA_METRICS_PORT + INFRA_METRICS_NODE_EXPORTER_PORT
    # · Last fail: None (new test for DevPlan 117 G T56)
    # · Remove if: naming scheme changes
    def test_scan_compose_ports_multi_service(self, tmp_path: Path) -> None:
        """Two services in one module → MODULE_PORT + MODULE_SERVICE_PORT."""
        module_dir = tmp_path / "infra-metrics"
        module_dir.mkdir()
        (module_dir / "docker-compose.base.yml").write_text(
            "services:\n"
            "  infra-metrics:\n"
            "    ports:\n"
            '      - "9090:9090"\n'
            "  node-exporter:\n"
            "    ports:\n"
            '      - "9100:9100"\n',
            encoding="utf-8",
        )

        result = scan_compose_ports(tmp_path)

        assert result["INFRA_METRICS_PORT"] == 9090
        assert result["INFRA_METRICS_NODE_EXPORTER_PORT"] == 9100

    # 🧪 TRAP[TEST] · Regression · Scenario: malformed compose file skipped
    # · Expect: no crash, warning logged, missing module absent from result
    # · Last fail: None (new test for DevPlan 117 G T56)
    # · Remove if: scan_compose_ports error handling changes
    def test_scan_compose_ports_skips_malformed(self, tmp_path: Path, caplog) -> None:
        """Broken YAML → module skipped with WARN, no exception."""
        caplog.set_level(0)
        module_dir = tmp_path / "broken"
        module_dir.mkdir()
        (module_dir / "docker-compose.base.yml").write_text("services: [unclosed\n", encoding="utf-8")

        result = scan_compose_ports(tmp_path)

        assert result == {}
        assert any("Failed to parse" in r.message for r in caplog.records)

    def test_scan_compose_ports_dict_port_entry(self, tmp_path: Path) -> None:
        """dict-style port entry (published field) → parsed."""
        module_dir = tmp_path / "app"
        module_dir.mkdir()
        (module_dir / "docker-compose.base.yml").write_text(
            "services:\n  app:\n    ports:\n      - published: 4000\n        target: 4000\n",
            encoding="utf-8",
        )

        result = scan_compose_ports(tmp_path)

        assert result["APP_PORT"] == 4000


# ══════════════════════════════════════════════════════════════════════
# TESTS: scan_test_ports
# ══════════════════════════════════════════════════════════════════════


class TestScanTestPorts:
    """Tests for scan_test_ports() with the !override YAML tag."""

    # 🧪 TRAP[TEST] · Regression · Scenario: test compose with !override tag
    # · Expect: port name from _PORT_NAME_MAP (9090 → prometheus)
    # · Last fail: None (new test for DevPlan 117 G T56)
    # · Remove if: scan_test_ports loader changes
    def test_scan_test_ports_override_tag(self, tmp_path: Path) -> None:
        """!override tag handled; well-known port named from _PORT_NAME_MAP."""
        module_dir = tmp_path / "monitoring"
        module_dir.mkdir()
        (module_dir / "docker-compose.test.yml").write_text(
            'services:\n  prometheus:\n    ports: !override\n      - "9090:9090"\n',
            encoding="utf-8",
        )

        result = scan_test_ports(tmp_path)

        assert result == {"monitoring": {"prometheus": 9090}}

    # 🧪 TRAP[TEST] · Regression · Scenario: unknown port → port_<N> fallback name
    # · Expect: port_12345
    # · Last fail: None (new test for DevPlan 117 G T56)
    # · Remove if: scan_test_ports naming changes
    def test_scan_test_ports_unknown_port_fallback(self, tmp_path: Path) -> None:
        """Unmapped port → port_<N> fallback name."""
        module_dir = tmp_path / "custom"
        module_dir.mkdir()
        (module_dir / "docker-compose.test.yml").write_text(
            'services:\n  svc:\n    ports:\n      - "12345:12345"\n',
            encoding="utf-8",
        )

        result = scan_test_ports(tmp_path)

        assert result == {"custom": {"port_12345": 12345}}

    # 🧪 TRAP[TEST] · Regression · Scenario: malformed test compose skipped
    # · Expect: no crash, empty result
    # · Last fail: None (new test for DevPlan 117 G T56)
    # · Remove if: scan_test_ports error handling changes
    def test_scan_test_ports_skips_malformed(self, tmp_path: Path, caplog) -> None:
        """Broken test YAML → module skipped, no exception."""
        caplog.set_level(0)
        module_dir = tmp_path / "broken"
        module_dir.mkdir()
        (module_dir / "docker-compose.test.yml").write_text("services: [unclosed\n", encoding="utf-8")

        result = scan_test_ports(tmp_path)

        assert result == {}
        assert any("Failed to parse" in r.message for r in caplog.records)

    def test_scan_test_ports_dict_entry(self, tmp_path: Path) -> None:
        """dict-style test port entry → parsed."""
        module_dir = tmp_path / "dictmod"
        module_dir.mkdir()
        (module_dir / "docker-compose.test.yml").write_text(
            "services:\n  svc:\n    ports:\n      - published: 9100\n        target: 9100\n",
            encoding="utf-8",
        )

        result = scan_test_ports(tmp_path)

        assert result == {"dictmod": {"node_exporter": 9100}}

    def test_port_name_map_non_empty(self) -> None:
        """_PORT_NAME_MAP covers the canonical well-known service ports."""
        assert 80 in _PORT_NAME_MAP
        assert 9119 in _PORT_NAME_MAP
        assert _PORT_NAME_MAP[5432] == "postgres"
