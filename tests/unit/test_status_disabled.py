# GREP_SUMMARY: test-status-disabled negative dns-failure disabled not-fail r5-anti-survivorship regression
# STRUCTURE: ┌R5 negative tests┐ → ◇ DNS unresolved → DISABLED (not FAIL) → ◇ all-DISABLED overall=PASS
# region MODULE_CONTRACT
## @purpose  R5 negative tests for status-page DISABLED logic (DevPlan 158 W1 T1.5).
##           Verifies the original bug (unresolved DNS → FAIL) is now correctly DISABLED.
## @scope    No Docker, no HTTP — socket.gethostbyname mocked; tmp_path for files.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - R5 anti-survivorship: exact trigger of the original bug (unresolved DNS)
## @rationale  DevPlan 158 W1 T1.5 — R5 negative tests for DISABLED status.
## @changes  2026-08-12 · DevPlan 158 W1 T1.5 — created
# endregion MODULE_CONTRACT

import json
import sys
import time
from pathlib import Path
from unittest import mock

# status-page/ dir has a hyphen — import via sys.path (pattern: tests/test_status_page.py).
_STATUS_PAGE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "status-page"
if str(_STATUS_PAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_STATUS_PAGE_DIR))

from collectors import _check_platform_service, get_all_checks


class TestNegativeDisabled:
    """R5 negative tests — original bug form must now produce DISABLED, not FAIL.

    Original bug: unresolved DNS (service not deployed) produced FAIL because
    _curl_platform_service was called unconditionally and curl exit 6/7 → FAIL.
    Fix (DevPlan 158 W1 T1.1): DNS pre-probe → DISABLED if unresolved.
    """

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · DNS failure not marked FAIL — DevPlan 158 W1 T1.1
    # · Last fail: unresolved DNS → curl exit 6 → FAIL (asiteam показывал FAIL ложно,
    # ·   т.к. на ноде не подключены grafana/loki/etc — Docker DNS не резолвится)
    # · Remove if: _check_platform_service перестанет использовать DNS pre-probe
    def test_negative_dns_failure_not_marked_fail(self) -> None:
        """R5 negative: unresolved DNS → DISABLED, NOT FAIL.

        This is the exact input that triggered the original bug — a platform service
        whose Docker DNS name does not resolve (service not deployed on this node).
        Pre-fix: curl was called → exit 6 → FAIL. Post-fix: DNS probe → DISABLED.
        """
        with (
            mock.patch("collectors.socket.gethostbyname", side_effect=OSError("nxdomain")),
            mock.patch("collectors.checks.platform._curl_platform_service") as curl_mock,
        ):
            result = _check_platform_service("grafana:3000", "/api/health")

        assert result["status"] != "FAIL", f"R5 FAIL: DNS-unresolved service marked FAIL (should be DISABLED): {result}"
        assert result["status"] == "DISABLED", f"Expected DISABLED, got {result['status']}"
        curl_mock.assert_not_called()

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · all-DISABLED services → overall PASS (S-WARN A)
    # · Last fail: asiteam overall=FAIL при здоровой ноде (все 6 сервисов не подключены)
    # · Remove if: DISABLED-исключение из overall меняется
    def test_negative_all_disabled_overall_pass(self, tmp_path: Path) -> None:
        """R5 negative: node with no platform services deployed → overall PASS.

        Original bug: asi-team-vps showed overall=FAIL because all 6 platform services
        (Grafana, Prometheus, Loki, Hermes, Langfuse, LiteLLM) returned FAIL (DNS unresolved).
        Fix (S-WARN A): DISABLED excluded from overall — not-deployed is configuration.
        """
        node_yaml = tmp_path / "node.yaml"
        node_yaml.write_text(
            "node:\n  name: asi-team-vps\nprojects: []\nmodules: []\n",
            encoding="utf-8",
        )
        metrics = tmp_path / "status-metrics.json"
        metrics.write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "containers": [{"name": "nginx", "running": True, "healthy": True}],
            }),
            encoding="utf-8",
        )
        # All 6 platform services unresolved → all DISABLED
        platform_services = [
            {"internal": "grafana:3000", "health_path": "/api/health"},
            {"internal": "prometheus:9090", "health_path": "/-/healthy"},
            {"internal": "loki:3100", "health_path": "/ready"},
            {"internal": "hermes-agent:9119", "health_path": "/"},
            {"internal": "langfuse:3000", "health_path": "/api/public/health"},
            {"internal": "litellm:4000", "health_path": "/health/liveliness"},
        ]

        with mock.patch("collectors.socket.gethostbyname", side_effect=OSError("nxdomain")):
            result = get_all_checks(
                str(node_yaml),
                str(metrics),
                platform_services,
                per_check_timeout=2,
                total_timeout=10,
            )

        statuses = [c["status"] for c in result["checks"] if c["type"] == "platform_service"]
        assert all(s == "DISABLED" for s in statuses), f"All platform services should be DISABLED: {statuses}"
        assert result["status"] == "PASS", (
            f"R5 FAIL: all-DISABLED node should be overall PASS (S-WARN A): {result['status']}"
        )
