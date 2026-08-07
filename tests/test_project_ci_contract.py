# GREP_SUMMARY: test CI contract deploy-yml reusable-workflow templates schema deploy-project workflow-call vars
# STRUCTURE: ┌template dirs discovery┐ → ○ 7 tests → ⊕ LDD trajectory IMP:7-10
# region MODULE_CONTRACT
## @purpose  Test suite for CI contract of template projects and reusable workflow
## @scope    7 test functions covering: deploy.yml ≤40/≤15 lines, no resolve-node action,
##           reusable workflow schema (on.workflow_call), no NODE_CONFIGS_TOKEN,
##           vars.NODE_HOST_MAP usage, no platform-deploy.yml in templates,
##           template completeness (.env.platform, Makefile, AGENTS.md)
## @invariants
##   - Template dirs auto-discovered via pathlib glob (no hardcoded paths)
##   - YAML parsed via yaml.safe_load for schema validation
##   - deploy-project.yml checked at project root if exists
## @rationale T19 per DevPlan $TEST_SPEC — validates CI contract of template projects
## @changes 2026-07-17 · T19 — initial implementation
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
_TEMPLATES_DIR: pathlib.Path = _PROJECT_ROOT / "templates"
_DEPLOY_PROJECT_YML: pathlib.Path = _PROJECT_ROOT / ".github" / "workflows" / "deploy-project.yml"

# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_template_deploy_ymls() -> list[pathlib.Path]:
    """Find all template deploy.yml files."""
    return sorted(_TEMPLATES_DIR.glob("template-*/.github/workflows/deploy.yml"))


def _get_template_dirs() -> list[pathlib.Path]:
    """Find all template directories."""
    return sorted(_TEMPLATES_DIR.glob("template-*"))


def _count_non_comment_lines(content: str) -> int:
    """Count non-blank, non-comment lines in a YAML file."""
    count = 0
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_deploy_yml_calls_reusable_workflow(caplog) -> None:
    """Template deploy.yml must be ≤40 lines, ≤15 non-comment, and use __ORG_NAME__/ai-platform/....

    ── Scenario: Each template deploy.yml checked for size and reusable workflow reference ──
    """
    deploy_ymls = _get_template_deploy_ymls()
    assert deploy_ymls, f"No deploy.yml files found under {_TEMPLATES_DIR / 'template-*/.github/workflows/'}"

    for dyml in deploy_ymls:
        template_name = dyml.relative_to(_PROJECT_ROOT)
        content = dyml.read_text()
        lines = content.splitlines()
        non_comment = _count_non_comment_lines(content)

        logger.info(
            "[IMP:7][test][deploy_yml] %s: total=%d, non_comment=%d",
            template_name,
            len(lines),
            non_comment,
        )

        assert len(lines) <= 40, f"{template_name}: {len(lines)} lines (max 40)"
        assert non_comment <= 15, f"{template_name}: {non_comment} non-comment lines (max 15)"
        assert "{{ORG_NAME}}/ai-platform/.github/workflows/deploy-project.yml" in content, (
            f"{template_name}: missing {{{{ORG_NAME}}}}/ai-platform/.github/workflows/deploy-project.yml reference"
        )
        logger.info("[IMP:9][test][deploy_yml] %s: contract OK", template_name)


@ldd_trajectory
def test_deploy_yml_no_resolve_node_action(caplog) -> None:
    """No template deploy.yml must reference ./.github/actions/resolve-node.

    ── Scenario: Search templates and root .github/workflows for resolve-node action (regression F6) ──
    """
    search_dirs = [
        _PROJECT_ROOT / ".github" / "workflows",
        _TEMPLATES_DIR,
    ]
    found_issues: list[str] = []

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for yml_file in search_dir.rglob("*.yml"):
            relative = yml_file.relative_to(_PROJECT_ROOT)
            content = yml_file.read_text()
            if "./.github/actions/resolve-node" in content or "resolve-node" in content:
                found_issues.append(str(relative))
                logger.info(
                    "[IMP:8][test][no_resolve_node] Found resolve-node reference in %s",
                    relative,
                )

    assert not found_issues, f"resolve-node action still referenced in: {found_issues}"
    logger.info("[IMP:9][test][no_resolve_node] No resolve-node references found")


@ldd_trajectory
def test_reusable_workflow_schema(caplog) -> None:
    """Reusable workflow deploy-project.yml must have valid on.workflow_call with required project_name input.

    ── Scenario: Parse deploy-project.yml, verify on.workflow_call schema ──
    """
    if not _DEPLOY_PROJECT_YML.exists():
        pytest.fail(
            f"Missing required file: {_DEPLOY_PROJECT_YML.relative_to(_PROJECT_ROOT)} — "
            "was T1 (Wave 1) properly implemented?"
        )

    with open(_DEPLOY_PROJECT_YML) as f:
        data = yaml.safe_load(f)

    assert data is not None, f"{_DEPLOY_PROJECT_YML} is empty"

    # Check on.workflow_call
    # YAML 1.1 parses bare `on:` as boolean True — handle both
    on_section = data.get("on") or data.get(True, {})
    assert "workflow_call" in on_section, (
        f"{_DEPLOY_PROJECT_YML}: 'on' must include 'workflow_call'. Found keys: {list(on_section.keys())}"
    )
    workflow_call = on_section["workflow_call"]
    assert isinstance(workflow_call, dict), "workflow_call must be a dict"

    # Check inputs
    inputs = workflow_call.get("inputs", {}) if workflow_call else {}
    assert "project_name" in inputs, (
        f"{_DEPLOY_PROJECT_YML}: required input 'project_name' not found in workflow_call.inputs. "
        f"Found inputs: {list(inputs.keys())}"
    )
    project_name_input = inputs["project_name"]
    assert project_name_input.get("required", False) is True, "Input 'project_name' must be required"

    logger.info(
        "[IMP:9][test][reusable_schema] deploy-project.yml validated with on.workflow_call and required project_name"
    )


@ldd_trajectory
def test_reusable_workflow_no_node_configs_token(caplog) -> None:
    """No deploy-project.yml or template deploy.yml must USE NODE_CONFIGS_TOKEN.

    ── Scenario: Search all workflow YAML files for actual usage of NODE_CONFIGS_TOKEN ──
    """
    search_files: list[pathlib.Path] = []
    if _DEPLOY_PROJECT_YML.exists():
        search_files.append(_DEPLOY_PROJECT_YML)

    search_files.extend(_get_template_deploy_ymls())

    found_issues: list[str] = []
    for yml_file in search_files:
        relative = yml_file.relative_to(_PROJECT_ROOT)
        content = yml_file.read_text()
        # Only flag actual usage in non-comment lines (exclude deprecation notes)
        for line in content.splitlines():
            stripped = line.strip()
            # Skip comment-only lines and lines where it's mentioned in a deprecation note
            if "NODE_CONFIGS_TOKEN" in stripped and not stripped.startswith("#"):
                found_issues.append(f"{relative}:{stripped}")
                logger.info(
                    "[IMP:8][test][no_node_token] Found NODE_CONFIGS_TOKEN usage in %s: %s",
                    relative,
                    stripped,
                )

    assert not found_issues, "NODE_CONFIGS_TOKEN usage found in:\n" + "\n".join(f"  - {i}" for i in found_issues)
    logger.info("[IMP:9][test][no_node_token] No NODE_CONFIGS_TOKEN usage found in non-comment lines")


@ldd_trajectory
def test_reusable_workflow_uses_org_variable(caplog) -> None:
    """deploy-project.yml must use vars.NODE_HOST_MAP and NOT have hardcoded org names (DD9).

    ── Scenario: Parse workflow YAML, check for vars.NODE_HOST_MAP and absence of hardcoded org ──
    """
    if not _DEPLOY_PROJECT_YML.exists():
        pytest.fail(
            f"Missing required file: {_DEPLOY_PROJECT_YML.relative_to(_PROJECT_ROOT)} — "
            "was T1 (Wave 1) properly implemented?"
        )

    content = _DEPLOY_PROJECT_YML.read_text()

    assert "vars.NODE_HOST_MAP" in content or "NODE_HOST_MAP" in content, (
        f"{_DEPLOY_PROJECT_YML.relative_to(_PROJECT_ROOT)}: must use vars.NODE_HOST_MAP "
        "(zero-secret node resolution, DD4)"
    )

    # Check for hardcoded org names (common patterns that are NOT __ORG_NAME__ placeholders)
    hardcoded_org_patterns = [
        r"tronyxLab/ai-platform",
        r"tronyxlab/ai-platform",
        r"TRONYXLAB/ai-platform",
    ]
    for pattern in hardcoded_org_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        assert not matches, (
            f"{_DEPLOY_PROJECT_YML.relative_to(_PROJECT_ROOT)}: hardcoded org reference '{pattern}' found: {matches} "
            "(must use __ORG_NAME__ placeholder per DD9)"
        )

    logger.info("[IMP:9][test][uses_org_variable] deploy-project.yml uses NODE_HOST_MAP and no hardcoded org")


@ldd_trajectory
def test_platform_deploy_yml_deleted_from_templates(caplog) -> None:
    """No platform-deploy.yml files should exist anywhere in templates/.

    ── Scenario: Glob for platform-deploy.yml under templates/ ──
    """
    legacy_files = list(_TEMPLATES_DIR.rglob("platform-deploy.yml"))

    assert not legacy_files, (
        f"Found {len(legacy_files)} legacy platform-deploy.yml files in templates/ that should have been deleted: "
        f"{[str(f.relative_to(_PROJECT_ROOT)) for f in legacy_files]}"
    )
    logger.info("[IMP:9][test][no_platform_deploy] No legacy platform-deploy.yml in templates")


@ldd_trajectory
def test_template_has_env_platform_makefile_agents(caplog, tmp_path: pathlib.Path) -> None:
    """Генераторы scaffold производят Makefile/AGENTS.md/.env.platform контракт (DevPlan 141 runtime).

    ── Scenario: .env.platform/Makefile/AGENTS.md больше НЕ хранятся в шаблонах (W1: GENERATED-дубли
    удалены) — они генерируются при scaffold (gen_env_platform/gen_project_makefile/gen_project_agents).
    Гейт проверяет генераторы: Makefile несёт sync-env/status/project-* таргеты (K3), AGENTS.md ≤60
    строк (DD13), .env.platform — ≥8 PLATFORM_* линий (инвариант gen_env_platform).
    ──
    """
    from core.internal.scaffold.gen_env_platform import generate_env_platform
    from core.internal.scaffold.scaffold_helpers import gen_project_agents, gen_project_makefile

    project_types = ("backend", "frontend")
    issues: list[str] = []

    for ptype in project_types:
        project_dir = tmp_path / f"test-{ptype}"
        project_dir.mkdir()

        # Makefile — K3 контракт: фасад платформенных операций (генератор — SoT)
        gen_project_makefile(f"test-{ptype}", "test.local", str(project_dir / "Makefile"), force=True)
        makefile = (project_dir / "Makefile").read_text()
        issues.extend(
            f"{ptype}: Makefile без таргета '{target}' (K3 contract)"
            for target in (
                "sync-env",
                "status",
                "project-check",
                "project-fix",
                "project-sync-practices",
                "project-set-practices",
            )
            if f"{target}:" not in makefile
        )
        logger.info("[IMP:8][test][template_files] %s: Makefile targets present (K3)", ptype)

        # AGENTS.md — DD13 контракт: ≤60 строк
        gen_project_agents(
            f"test-{ptype}",
            "tronyx161",
            ptype,
            "test-node",
            "test.local",
            str(project_dir / "AGENTS.md"),
            force=True,
        )
        agents_lines = len((project_dir / "AGENTS.md").read_text().splitlines())
        if agents_lines > 60:
            issues.append(f"{ptype}: AGENTS.md has {agents_lines} lines (max 60)")
        logger.info("[IMP:8][test][template_files] %s: AGENTS.md = %d lines (DD13)", ptype, agents_lines)

        # .env.platform — инвариант gen_env_platform: ≥8 PLATFORM_* линий
        env_lines = generate_env_platform(
            str(_PROJECT_ROOT / "platform-env.yaml"),
            domain="test.local",
            project_name=f"test-{ptype}",
        )
        plat_count = sum(1 for line in env_lines if line.startswith("PLATFORM_"))
        if plat_count < 8:
            issues.append(f"{ptype}: .env.platform generator produced {plat_count} PLATFORM_* lines (expected ≥8)")
        logger.info(
            "[IMP:8][test][template_files] %s: .env.platform generator = %d PLATFORM_* lines", ptype, plat_count
        )

    assert not issues, "Template contract violations:\n" + "\n".join(f"  - {i}" for i in issues)
    logger.info("[IMP:9][test][template_files] All templates produce Makefile/AGENTS.md/.env.platform contract")
