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
_DEPLOY_PROJECT_YML: pathlib.Path = (
    _PROJECT_ROOT / ".github" / "workflows" / "deploy-project.yml"
)

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

        assert len(lines) <= 40, (
            f"{template_name}: {len(lines)} lines (max 40)"
        )
        assert non_comment <= 15, (
            f"{template_name}: {non_comment} non-comment lines (max 15)"
        )
        assert (
            "__ORG_NAME__/ai-platform/.github/workflows/deploy-project.yml" in content
        ), (
            f"{template_name}: missing __ORG_NAME__/ai-platform/.github/workflows/deploy-project.yml reference"
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
            if "./.github/actions/resolve-node" in content:
                found_issues.append(str(relative))
                logger.info(
                    "[IMP:8][test][no_resolve_node] Found resolve-node reference in %s",
                    relative,
                )

    assert not found_issues, (
        f"resolve-node action still referenced in: {found_issues}"
    )
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
        f"{_DEPLOY_PROJECT_YML}: 'on' must include 'workflow_call'. "
        f"Found keys: {list(on_section.keys())}"
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
    assert project_name_input.get("required", False) is True, (
        "Input 'project_name' must be required"
    )

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

    for tpl_deploy in _get_template_deploy_ymls():
        search_files.append(tpl_deploy)

    found_issues: list[str] = []
    for yml_file in search_files:
        relative = yml_file.relative_to(_PROJECT_ROOT)
        content = yml_file.read_text()
        # Only flag actual usage patterns (node_configs_token in workflow context),
        # not comments explaining deprecation
        usage_patterns = ["\${{", "NODE_CONFIGS_TOKEN"] if True else []
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

    assert not found_issues, (
        f"NODE_CONFIGS_TOKEN usage found in:\n" + "\n".join(f"  - {i}" for i in found_issues)
    )
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

    logger.info(
        "[IMP:9][test][uses_org_variable] deploy-project.yml uses NODE_HOST_MAP and no hardcoded org"
    )


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
def test_template_has_env_platform_makefile_agents(caplog) -> None:
    """Each template must have .env.platform, Makefile, AGENTS.md; AGENTS.md ≤60 lines.

    ── Scenario: Per template dir, check required files and AGENTS.md line count ──
    """
    template_dirs = _get_template_dirs()
    assert template_dirs, f"No template directories found under {_TEMPLATES_DIR}"

    required_files = [".env.platform", "Makefile", "AGENTS.md"]
    # Exclude template-context — it is a context template, not a project template
    # and does not require .env.platform/Makefile/AGENTS.md
    excluded_templates = {"template-context"}
    issues: list[str] = []

    for template_dir in template_dirs:
        tpl_name = template_dir.name
        if tpl_name in excluded_templates:
            logger.info("[IMP:7][test][template_files] %s: excluded from project template check", tpl_name)
            continue

        for req_file in required_files:
            file_path = template_dir / req_file
            if not file_path.exists():
                issues.append(f"{tpl_name}: missing required file '{req_file}'")
                logger.info("[IMP:8][test][template_files] %s: MISSING %s", tpl_name, req_file)
            elif req_file == "AGENTS.md":
                agents_lines = len(file_path.read_text().splitlines())
                logger.info(
                    "[IMP:7][test][template_files] %s: AGENTS.md = %d lines",
                    tpl_name,
                    agents_lines,
                )
                if agents_lines > 60:
                    issues.append(
                        f"{tpl_name}: AGENTS.md has {agents_lines} lines (max 60)"
                    )
            else:
                logger.info("[IMP:7][test][template_files] %s: %s present", tpl_name, req_file)

    assert not issues, f"Template contract violations:\n" + "\n".join(f"  - {i}" for i in issues)
    logger.info("[IMP:9][test][template_files] All templates have required files, AGENTS.md ≤60 lines")
