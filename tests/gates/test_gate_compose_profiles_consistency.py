# GREP_SUMMARY: gate compose-profiles consistency COMPOSE_PROFILES mismatch drift callsites composite-action
# STRUCTURE: ┌fixture: canonical profiles from platform-infra.yaml SoT┐ → ◇ test: no hardcoded copies at former callsites → ◇ test: verify CI workflows use composite action → ⎋ assert all valid
# region MODULE_CONTRACT
## @purpose — Gate: verify COMPOSE_PROFILES has РОВНО один SoT (core/platform-infra.yaml
##            env_defaults, DevPlan 116 T2/T9, U-02) и что бывшие хардкод-callsites
##            (Makefile, docker_orchestrator.py, helpers.mk, project_adopter.py) больше
##            НЕ содержат полную строку. Verify that CI workflows use the compose-profiles
##            composite action instead of hardcoded strings.
##            Read-only gate — does NOT modify any production code.
## @scope — Бывшие 4 хардкод-файла: Makefile, docker_orchestrator.py,
##          helpers.mk (_get_all_profiles), project_adopter.py
##          + 2 CI workflows verified to use composite action: push-gate.yml,
##          platform-test.yml
##          Deploy-project.sh callsite removed 2026-07-30 — file deleted during
##          Strangler-Fig migration (DevPlan 036E), COMPOSE_PROFILES now propagated
##          via os.environ from Makefile/docker_orchestrator.py.
## @invariants
##   - Canonical value obtained from core/platform-infra.yaml env_defaults (single SoT)
##   - Бывшие callsites НЕ должны содержать полную 13-item строку (хардкод-копии удалены)
##   - All extractors are read-only — no file modifications
##   - Test is marked @pytest.mark.gate — runs in `make gate MODE=fast`
##   - On violation, test fails with exact file:line guidance for developer
##   - CI workflows (push-gate.yml, platform-test.yml) MUST use compose-profiles
##     composite action, NOT hardcoded COMPOSE_PROFILES — verified by separate test
##   - Репо-wide «копий нет» проверяется гейтом test_gate_profiles_parity (d) — этот
##     тест даёт точечную регрессию по конкретным бывшим callsites (не дублирование)
## @rationale — MISMATCH-1 from VerificationReport-postfix (Wave 3). DevPlan 116 T2 (U-02):
##              COMPOSE_PROFILES консолидирован в platform-infra.yaml; 7 копий заменены
##              runtime-чтением. CI workflows используют compose-profiles composite action
##              (DevPlan 064 S1), который читает platform-env.yaml profiles.
## @changes — 2026-07-22 | Created per 037-DevPlan GOAL_MISMATCH
## @changes — 2026-07-23 | Updated per DevPlan 064 S1: removed CI workflow callsites,
##            added composite-action verification test
## @changes — 2026-07-31 | DevPlan 116 T9: canonical → platform-infra.yaml SoT;
##            CALLSITES-тест конвертирован в «хардкод-копий нет» (T2 устранил копии)
# endregion MODULE_CONTRACT

import re
from pathlib import Path

import pytest
import yaml

# === Constants ===

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLATFORM_INFRA = PROJECT_ROOT / "core" / "platform-infra.yaml"

# === Helpers ===


def _get_canonical_profiles() -> str:
    """Get canonical COMPOSE_PROFILES from core/platform-infra.yaml env_defaults (SoT, U-02).

    ▶ yaml.safe_load(platform-infra.yaml) → env_defaults.COMPOSE_PROFILES → ⎋ str
    """
    with open(PLATFORM_INFRA) as f:
        data = yaml.safe_load(f)
    profiles = (data.get("env_defaults") or {}).get("COMPOSE_PROFILES")
    if not profiles:
        pytest.fail("platform-infra.yaml env_defaults.COMPOSE_PROFILES missing (SoT)")
    return str(profiles).strip()


# === Fixtures ===


@pytest.fixture(scope="module")
def canonical_profiles() -> str:
    """Canonical COMPOSE_PROFILES from platform-infra.yaml env_defaults (SoT).

    ◇ read SoT → ⎋ canonical string
    """
    return _get_canonical_profiles()


# === FORMER CALLSITES — must NOT contain the full hardcoded profile list ===
# region CALLSITES — each entry: (label, filepath) where the hardcoded copy was REMOVED (DevPlan 116 T2)

FORMER_CALLSITES: list[tuple[str, Path]] = [
    ("Makefile:30", PROJECT_ROOT / "Makefile"),
    (
        "docker_orchestrator.py:511-515",
        PROJECT_ROOT / "core/internal/bootstrap/deploy/docker_orchestrator.py",
    ),
    ("helpers.mk (_get_all_profiles)", PROJECT_ROOT / "makefiles/helpers.mk"),
    (
        "project_adopter.py (former _DEFAULT_COMPOSE_PROFILES)",
        PROJECT_ROOT / "core/internal/scaffold/project_adopter.py",
    ),
]

# endregion CALLSITES


# === Tests ===


# 🧪 TRAP[TEST] · Regression · Scenarios: AC-2 (U-02) · Last fail: 2026-07-31 · Remove if: COMPOSE_PROFILES centralized to single source
# · Check that former hardcoded callsites no longer carry the full 13-item profile list
@pytest.mark.gate
def test_no_hardcoded_profiles_at_former_callsites(canonical_profiles: str, caplog) -> None:
    """Verify COMPOSE_PROFILES hardcoded copies were REMOVED from former callsites.

    ◇ canonical_profiles → ⚡ for each former callsite → assert full string absent
       → ∋ violation? → ⎋ fail with line-guidance | pass

    ## @purpose  DevPlan 116 T2 (U-02): все 7 копий COMPOSE_PROFILES заменены runtime-
    ##            чтением SoT. Этот тест точечно проверяет 4 бывших callsite на отсутствие
    ##            полной строки (repo-wide проверка — в test_gate_profiles_parity (d)).
    ## @io        Input: canonical_profiles (str) from platform-infra.yaml
    ##            Output: pass or pytest.fail with per-callsite violation details
    ## @complexity O(N) where N = len(FORMER_CALLSITES) = 4
    """
    import logging

    logger = logging.getLogger(__name__)

    violations: list[str] = []

    for label, filepath in FORMER_CALLSITES:
        logger.info("[IMP:8][test_no_hardcoded_profiles] Checking: %s (%s)", label, filepath)

        if not filepath.exists():
            violations.append(f"[{label}] File not found: {filepath}")
            continue

        content = filepath.read_text()
        if canonical_profiles in content:
            violations.append(
                f"[{label}] full COMPOSE_PROFILES string still hardcoded — replace with "
                "runtime-read (yaml_query.py / platform-infra.yaml) per DevPlan 116 T2"
            )
            logger.error("[IMP:4][test_no_hardcoded_profiles] VIOLATION: %s", label)
        else:
            logger.info("[IMP:9][test_no_hardcoded_profiles] ✅ %s: no hardcoded copy", label)

    if violations:
        logger.error("[IMP:10][test_no_hardcoded_profiles] FAIL: %d callsite(s) still hardcoded", len(violations))
        pytest.fail(
            f"COMPOSE_PROFILES hardcoded copy detected in {len(violations)} former callsite(s):\n"
            + "\n".join(violations)
            + "\n\nSoT: core/platform-infra.yaml env_defaults.COMPOSE_PROFILES. "
            "All other places must read it at runtime (DevPlan 116 T2)."
        )

    logger.info(
        "[IMP:9][test_no_hardcoded_profiles] ✅ All %d former callsites free of hardcoded COMPOSE_PROFILES",
        len(FORMER_CALLSITES),
    )


# 🧪 TRAP[TEST] · Regression · Scenarios: UC_PR, UC_PUSH (DevPlan 064) · Last fail: 2026-07-23 · Remove if: COMPOSE_PROFILES management redesigned again
# · Verify CI workflows use compose-profiles composite action instead of hardcoded strings
# · DevPlan 064 S1: eliminated 3× hardcoded COMPOSE_PROFILES list
@pytest.mark.gate
def test_ci_workflows_use_compose_profiles_composite(caplog) -> None:
    """Verify CI workflows use compose-profiles composite action, not hardcoded COMPOSE_PROFILES.

    ⚡ for each CI workflow → ◇ has_composite_ref? → ◇ has_hardcoded_profiles?
       → assert has_composite_ref AND NOT has_hardcoded_profiles

    ## @purpose  CI workflows (platform-test.yml, push-gate.yml) MUST use the
    ##            compose-profiles composite action per DevPlan 064 S1. Hardcoded
    ##            COMPOSE_PROFILES strings are forbidden in CI workflows.
    ## @io        Input: CI workflow YAML files
    ##            Output: pass or pytest.fail with per-workflow details
    ## @complexity O(N) where N = 2 (platform-test.yml, push-gate.yml)
    """
    import logging

    logger = logging.getLogger(__name__)

    ci_workflows: list[tuple[str, Path]] = [
        ("platform-test.yml", PROJECT_ROOT / ".github/workflows/platform-test.yml"),
        ("push-gate.yml", PROJECT_ROOT / ".github/workflows/push-gate.yml"),
    ]

    failures: list[str] = []

    for label, filepath in ci_workflows:
        logger.info("[IMP:8][test_ci_workflows] Checking: %s", label)

        if not filepath.exists():
            failures.append(f"[{label}] File not found: {filepath}")
            continue

        content = filepath.read_text()

        # Check 1: must reference compose-profiles composite action
        has_composite_ref = "uses: ./.github/actions/compose-profiles" in content
        if not has_composite_ref:
            failures.append(
                f"[{label}] Missing uses: ./.github/actions/compose-profiles — "
                "CI workflow must use compose-profiles composite action per DevPlan 064 S1"
            )

        # Check 2: must NOT have hardcoded COMPOSE_PROFILES in env section
        has_hardcoded = bool(re.search(r'COMPOSE_PROFILES:\s*".+?"', content))
        if has_hardcoded:
            failures.append(
                f"[{label}] Found hardcoded COMPOSE_PROFILES — "
                "CI workflow must NOT have hardcoded COMPOSE_PROFILES per DevPlan 064 S1"
            )

        if not failures:
            logger.info("[IMP:9][test_ci_workflows] ✅ %s: uses compose-profiles composite", label)

    # Check 3: composite action itself exists and is valid
    composite_action_path = PROJECT_ROOT / ".github/actions/compose-profiles/action.yml"
    if not composite_action_path.exists():
        failures.append(
            "[compose-profiles] Composite action file not found: .github/actions/compose-profiles/action.yml"
        )
    else:
        composite_content = composite_action_path.read_text()
        if "platform-env.yaml" not in composite_content:
            failures.append("[compose-profiles] Composite action must reference platform-env.yaml")
        if "yaml_query.py" not in composite_content:
            failures.append("[compose-profiles] Composite action must use yaml_query.py to read profiles")
        if "COMPOSE_PROFILES" not in composite_content:
            failures.append("[compose-profiles] Composite action must export COMPOSE_PROFILES")
        if not failures:
            logger.info("[IMP:9][test_ci_workflows] ✅ compose-profiles composite action is valid")

    if failures:
        logger.error("[IMP:10][test_ci_workflows] FAIL: %d issue(s)", len(failures))
        pytest.fail("CI workflow COMPOSE_PROFILES composite action verification failed:\n" + "\n".join(failures))

    logger.info("[IMP:9][test_ci_workflows] ✅ All CI workflows correctly use compose-profiles composite action")
