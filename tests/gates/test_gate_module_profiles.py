# GREP_SUMMARY: gate-test module-profiles docker-compose.base.yml profiles contract enforcement LDD
# STRUCTURE: ▶ test_all_base_yml_have_profiles → ◇ discover base.yml → ◇ parse YAML → ◇ assert profiles on every service → ▶ test_profile_matches_module_name → ◇ profile name == module dir name → ▶ test_no_stale_profiles → ◇ only expected profiles → ⊕ LDD [IMP:9] trajectory
# region MODULE_CONTRACT
## @purpose  Gate tests: validate all core/modules/*/docker-compose.base.yml declare `profiles: [module-name]`
## @scope    Validates architectural contract: every Docker module service must have profiles
##           matching the module directory name. This ensures --profile in deploy-modules.sh
##           selects the correct services. Without this, modules silently fail to start.
##           Includes LDD telemetry (IMP:9) per §TESTING.
## @invariants
##   - Each service in docker-compose.base.yml MUST have `profiles: [<module-name>]`
##   - Module name = parent directory name of docker-compose.base.yml
##   - Multiple services per module are allowed; ALL must share the same profile
##   - Extra profiles beyond module name are allowed but warned
## @rationale D4 fix: Architectural contract enforcement prevents silent module startup failures.
##            deploy-modules.sh runs `docker compose --profile $module_name up -d` — if base.yml
##            lacks `profiles: [module-name]`, that --profile is a no-op and the module stays down.
##            Gate test catches this at CI time, not at runtime on VPS.
## @changes — 2026-07-16 | NEW: D4 gate test (DevPlan 018)
## @changes — 2026-07-16 | ADD: LDD telemetry (IMP:9) per F6 DevPlan-fix-D12
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

MODULES_DIR = repo_root() / "core" / "modules"


def _discover_base_ymls() -> list[Path]:
    """Discover all docker-compose.base.yml files in core/modules/."""
    return sorted(MODULES_DIR.glob("*/docker-compose.base.yml"))


BASE_YMLS = _discover_base_ymls()


# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Module profiles — все base.yml используют profiles: [module-name]
# · Last fail: N/A (preventive)
# · Remove if: profiles-механизм заменён в compose-архитектуре
class TestModuleProfiles:
    @pytest.mark.gate
    def test_all_base_yml_have_profiles(self, caplog) -> None:
        """Все docker-compose.base.yml содержат profiles: [module-name] на каждом сервисе."""
        # 🧪 TRAP[TEST] · 2026-07-16 · gate/module-profiles · Регресс: base.yml без profiles
        caplog.set_level(logging.DEBUG)
        errors: list[str] = []

        for bm in BASE_YMLS:
            mod_name = bm.parent.name
            try:
                with open(bm) as f:
                    data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                errors.append(f"{mod_name}: YAML parse error: {e}")
                logger.info("[IMP:9][gate] FAIL: %s YAML parse error: %s", mod_name, e)
                continue

            services = data.get("services", {})
            if not services:
                errors.append(f"{mod_name}: No services defined")
                logger.info("[IMP:9][gate] FAIL: %s has no services", mod_name)
                continue

            for svc_name, svc_config in services.items():
                if not isinstance(svc_config, dict):
                    errors.append(f"{mod_name}/{svc_name}: service config is not a dict")
                    logger.info("[IMP:9][gate] FAIL: %s/%s config not a dict", mod_name, svc_name)
                    continue
                profiles = svc_config.get("profiles", [])
                if mod_name not in profiles:
                    errors.append(f"{mod_name}/{svc_name}: profiles={profiles!r} does not contain '{mod_name}'")
                    logger.info(
                        "[IMP:9][gate] FAIL: %s/%s profiles %r missing '%s'",
                        mod_name,
                        svc_name,
                        profiles,
                        mod_name,
                    )

        assert not errors, f"Profile contract violations in {len(errors)} service(s):\n" + "\n".join(errors)
        logger.info("[IMP:9][gate] PASS: All %d modules have correct profiles", len(BASE_YMLS))

    @pytest.mark.gate
    def test_profile_matches_module_name(self, caplog) -> None:
        """Имя профиля совпадает с именем директории модуля."""
        # 🧪 TRAP[TEST] · 2026-07-16 · gate/module-profiles · Регресс: profile name != module dir name
        caplog.set_level(logging.DEBUG)
        errors: list[str] = []

        for bm in BASE_YMLS:
            mod_name = bm.parent.name
            try:
                with open(bm) as f:
                    data = yaml.safe_load(f)
            except yaml.YAMLError:
                logger.info("[IMP:9][gate] WARN: %s YAML parse error (skipped)", mod_name)
                continue

            services = data.get("services", {})
            for svc_name, svc_config in services.items():
                if not isinstance(svc_config, dict):
                    continue
                profiles = svc_config.get("profiles", [])
                # The primary profile MUST match module directory name
                if not profiles or profiles[0] != mod_name:
                    errors.append(
                        f"{mod_name}/{svc_name}: first profile is '{profiles[0] if profiles else '<empty>'}' "
                        f"but module dir is '{mod_name}'"
                    )
                    logger.info(
                        "[IMP:9][gate] FAIL: %s/%s first profile %r != module dir '%s'",
                        mod_name,
                        svc_name,
                        profiles[0] if profiles else "<empty>",
                        mod_name,
                    )

        assert not errors, f"Profile name mismatch in {len(errors)} service(s):\n" + "\n".join(errors)
        logger.info("[IMP:9][gate] PASS: All %d modules have profile matching module dir name", len(BASE_YMLS))

    @pytest.mark.gate
    def test_no_stale_profiles(self, caplog) -> None:
        """Нет дублирующих/неиспользуемых профилей в base.yml (предупреждение)."""
        # 🧪 TRAP[TEST] · 2026-07-16 · gate/module-profiles · Предупреждение о лишних профилях
        caplog.set_level(logging.DEBUG)
        warnings: list[str] = []

        for bm in BASE_YMLS:
            mod_name = bm.parent.name
            try:
                with open(bm) as f:
                    data = yaml.safe_load(f)
            except yaml.YAMLError:
                logger.info("[IMP:9][gate] WARN: %s YAML parse error (skipped)", mod_name)
                continue

            services = data.get("services", {})
            for svc_name, svc_config in services.items():
                if not isinstance(svc_config, dict):
                    continue
                profiles = svc_config.get("profiles", [])
                # Profiles should be just [module-name], maybe with additional shared profiles
                # Like infra-metrics has multiple services all with [infra-metrics]
                if len(profiles) > 1:
                    warnings.append(
                        f"{mod_name}/{svc_name}: multiple profiles {profiles!r} "
                        f"(only '{mod_name}' expected, the rest may be stale)"
                    )
                    logger.info(
                        "[IMP:9][gate] WARN: %s/%s multiple profiles %r",
                        mod_name,
                        svc_name,
                        profiles,
                    )

        if warnings:
            logger.info("[IMP:9][gate] SKIP: stale profiles detected — %s", "; ".join(warnings))
            pytest.skip("Non-critical: " + "; ".join(warnings))
        else:
            logger.info("[IMP:9][gate] PASS: No stale profiles detected")
