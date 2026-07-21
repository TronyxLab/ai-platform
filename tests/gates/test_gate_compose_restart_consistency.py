# GREP_SUMMARY: gate-test compose restart consistency test-compose base-compose D5 P08 drift
# STRUCTURE: ▶ glob core/modules/*/docker-compose.test.yml → ○ per-service → ◇ assert restart == "no" → ○ base.yml → ◇ assert restart ∈ {always, unless-stopped} → ⊕ violations
# region MODULE_CONTRACT
## @purpose  Gate-тест: compose-file restart audit (DevPlan 033 W3-E4). Проверяет, что test-compose
##           имеет `restart: "no"` на всех сервисах, а base-compose — restart ∈ {always, unless-stopped}.
## @scope    Все 13 Docker-модулей под core/modules/. Не проверяет Makefile restart-семантику —
##           это ответственность test_restart_consistency.py (H2 finding).
## @invariants
##   - test-compose: все сервисы имеют `restart: "no"` (P08 enforcement)
##   - base-compose: все сервисы имеют restart ∈ {always, unless-stopped} (drift detection)
##   - init/one-shot сервисы с `restart: "no"` в base-compose — carve-out (принимается)
##   - severity:critical модули с `restart: always` в module.yaml — carve-out (принимается)
## @rationale Test-isolation требует restart: "no" чтобы тестовые контейнеры не авто-перезапускались
##            (zombie prevention). Base-compose должен иметь restart для production resilience.
##            Отдельный файл от test_restart_consistency.py (Makefile restart семантика) —
##            разные домены (H2 finding DevPlan 033 §4 W3-E4).
## @changes   CREATED 2026-07-21 | DevPlan 033 W3-E4
# endregion MODULE_CONTRACT

import logging
import pathlib
from typing import Any

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

MODULES_DIR = repo_root() / "core" / "modules"
EXPECTED_TEST_RESTART = "no"
EXPECTED_BASE_RESTART = {"always", "unless-stopped"}


def _load_yaml(path: pathlib.Path) -> dict[str, Any] | None:
    """Load YAML file or return None on error. Handles !override tag (compose merge)."""
    import io

    # Read raw text and strip !override tags before parsing
    # (yaml.safe_load cannot handle custom tags)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None

    # Replace `!override` with empty string (the tag literal in YAML)
    # Pattern: `: !override` at the end of a key: value line, or `: !override\n`
    import re
    raw = re.sub(r":\s*!override\b", ":", raw)

    try:
        data = yaml.safe_load(io.StringIO(raw))
        return data if isinstance(data, dict) else None
    except yaml.YAMLError:
        return None


def _get_services(compose: dict) -> dict:
    """Extract services dict from compose."""
    svc = compose.get("services", {})
    return svc if isinstance(svc, dict) else {}


def _get_service_restart(svc_def: dict) -> str:
    """Get restart policy from a service definition."""
    if not isinstance(svc_def, dict):
        return ""
    r = svc_def.get("restart", "")
    return str(r) if r else ""


@pytest.mark.gate
class TestComposeRestartConsistency:
    """Gate: test-compose restart: "no", base-compose restart: unless-stopped|always."""

    # 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · All test-compose services must have restart: "no"
    # · Scenario: 13 core/modules/*/docker-compose.test.yml → every service → assert restart == "no"
    # · Last fail: N/A (new test)
    # · Remove if: test-compose contract changed or modules removed
    def test_test_compose_restart_no(self, caplog):
        """All test-compose services have restart: 'no' (P08 — test isolation)."""
        violations: list[str] = []
        test_files = sorted(MODULES_DIR.glob("*/docker-compose.test.yml"))
        total_files = len(test_files)
        checked_services = 0

        for test_path in test_files:
            module_name = test_path.parent.name
            compose = _load_yaml(test_path)
            if compose is None:
                violations.append(f"{module_name}: cannot parse test-compose")
                continue

            services = _get_services(compose)
            for svc_name, svc_def in services.items():
                restart = _get_service_restart(svc_def)
                checked_services += 1
                if restart != EXPECTED_TEST_RESTART:
                    violations.append(
                        f"{module_name}/{svc_name}: test-compose has restart: "
                        f"'{restart or '<missing>'}' — expected 'no'"
                    )
                    logger.info(
                        "[IMP:9][restart_consistency] FAIL: %s/%s test-compose restart=%s",
                        module_name,
                        svc_name,
                        restart or "<missing>",
                    )

        logger.info(
            "[IMP:9][restart_consistency] Test-compose: %d files, %d services checked, %d violations",
            total_files,
            checked_services,
            len(violations),
        )
        assert not violations, (
            f"[restart_consistency] Test-compose restart violations ({len(violations)}):\n"
            + "\n".join(violations)
        )

    # 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · All base-compose services must have restart in {unless-stopped, always}
    # · Scenario: 13 core/modules/*/docker-compose.base.yml → every service → assert restart ∈ {always, unless-stopped}
    # · Carve-out: init/one-shot services with restart: "no" — accepted
    # · Last fail: N/A (new test)
    # · Remove if: base-compose contract changed
    def test_base_compose_restart_production(self, caplog):
        """All base-compose services have restart: unless-stopped or always (production resilience)."""
        violations: list[str] = []
        base_files = sorted(MODULES_DIR.glob("*/docker-compose.base.yml"))
        total_files = len(base_files)
        checked_services = 0

        for base_path in base_files:
            module_name = base_path.parent.name
            compose = _load_yaml(base_path)
            if compose is None:
                violations.append(f"{module_name}: cannot parse base-compose")
                continue

            services = _get_services(compose)
            for svc_name, svc_def in services.items():
                restart = _get_service_restart(svc_def)
                checked_services += 1

                if restart == "no":
                    # init/one-shot services with restart: "no" — accepted as carve-out
                    logger.info(
                        "[IMP:7][restart_consistency] %s/%s: restart=no (init carve-out accepted)",
                        module_name,
                        svc_name,
                    )
                    continue

                if restart not in EXPECTED_BASE_RESTART:
                    violations.append(
                        f"{module_name}/{svc_name}: base-compose has restart: "
                        f"'{restart or '<missing>'}' — expected 'unless-stopped' or 'always'"
                    )
                    logger.info(
                        "[IMP:9][restart_consistency] FAIL: %s/%s base-compose restart=%s",
                        module_name,
                        svc_name,
                        restart or "<missing>",
                    )

        logger.info(
            "[IMP:9][restart_consistency] Base-compose: %d files, %d services checked, %d violations",
            total_files,
            checked_services,
            len(violations),
        )
        assert not violations, (
            f"[restart_consistency] Base-compose restart violations ({len(violations)}):\n"
            + "\n".join(violations)
        )
