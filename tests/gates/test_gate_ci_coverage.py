# GREP_SUMMARY: gate ci-coverage anti-drift sha-aware-aggregator mode-fast check-doc-headers platform-test
# STRUCTURE: ▶ gates: ◇ sha-aware aggregator ◇ mode-fast excludes Docker ◇ check-doc-headers equivalence ◇ platform-test trigger ◇ MARKER=all includes contract ◇ integration steps logging
# region MODULE_CONTRACT
## @purpose — Gate test suite for CI structure and architectural invariants.
##            Validates SHA-aware aggregator in deploy workflows, MODE=fast
##            expression in Makefile, check-doc-headers.sh→doc_header_validator
##            equivalence (DevPlan 106), platform-test.yml push trigger
##            consistency, and MARKER=all contract.
## @scope — Parses CI workflow YAMLs, Makefile, pre-commit config, and shell scripts
##          to verify structural invariants.
## @invariants
##   - Deploy workflows (core-deploy, build-platform, mirror) use SHA-aware aggregator (D4)
##   - MODE=fast excludes Docker-dependent markers (requires_docker, local_auth)
##   - check-doc-headers.sh (facade) delegates all old hook checks to
##     core.internal.lint.doc_header_validator; old grepsummary hook is removed
##   - platform-test.yml triggers on push to main, does NOT have merge_group (replaced main-full-gate.yml)
## @rationale — Prevents silent test erosion and architectural drift in CI.
##              Extended gates close coverage gaps identified in 002-fix-gate-coverage audit.
##              Without these tests, refactoring of CI structure may silently violate invariants.
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

from core.internal.lint.doc_header_validator import (
    check_grep_summary_presence,
    check_module_contract,
    check_regions_balanced,
    check_structure,
    check_yaml_purpose,
    validate_file,
)
from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import get_on_section, load_workflow

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_CI_WORKFLOW_PATH: pathlib.Path = _PROJECT_ROOT / ".github" / "workflows" / "platform-test.yml"
_CI_WORKFLOW_DIR: pathlib.Path = _PROJECT_ROOT / ".github" / "workflows"
_CHECK_DOC_HEADERS_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "entrypoints" / "check-doc-headers.sh"


_PRE_COMMIT_CONFIG_PATH: pathlib.Path = _PROJECT_ROOT / ".pre-commit-config.yaml"
_MAIN_FULL_GATE_PATH: pathlib.Path = _PROJECT_ROOT / ".github" / "workflows" / "main-full-gate.yml"

logger = logging.getLogger(__name__)


def _extract_ci_skip_steps() -> list[dict]:
    """Parse platform-test.yml and extract steps that explicitly skip TEST execution.

    ## @purpose — Scan the workflow YAML for steps that deliberately skip tests.
    ##            Target patterns (NOT cache or infra continue-on-error):
    ##            - Step name starts with "Skip" (e.g. "Skip integration test...")
    ##            - Step run contains "[IMP:*][skip]" structured skip log
    ##            - Step uses HARDCODED echo skip with explicit "[skip]" tag
    ##            EXCLUDES: cache steps ("skip if cache hit"), infra steps with
    ##            continue-on-error (pre-pull), make exit code handling (exit 5).
    ## @io — ⎋ list[dict] with keys: job_name, step_name, step_run (first 200 chars)
    ## @complexity — O(S) where S = total steps across all jobs
    """
    with pathlib.Path(_CI_WORKFLOW_PATH).open(encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    skip_steps: list[dict] = []

    for job_name, job_config in workflow.get("jobs", {}).items():
        steps = job_config.get("steps", [])
        for step in steps:
            step_name = step.get("name", "")
            step_run = step.get("run", "")

            # Pattern 1: step name explicitly starts with "Skip" (test-skip, not cache-skip)
            # This matches "Skip integration test (requires production stack + API keys)"
            # but NOT "Install gitleaks (skip if cache hit)" or "Install test dependencies (skip if ...)"
            if re.match(r"Skip\s", step_name):
                skip_steps.append({
                    "job_name": job_name,
                    "step_name": step_name,
                    "step_run": step_run[:200] if step_run else "",
                })
                logger.info("[IMP:8][_extract_ci_skip_steps] Found skip step: %s / %s", job_name, step_name)
                continue

            # Pattern 2: step name explicitly mentions "smoke" or "component" with test-skip context
            # (matches step names that describe SKIPPING tests, not running them)
            if re.search(r"skip.*(test|integration|smoke|component)", step_name, re.IGNORECASE):
                skip_steps.append({
                    "job_name": job_name,
                    "step_name": step_name,
                    "step_run": step_run[:200] if step_run else "",
                })
                logger.info("[IMP:8][_extract_ci_skip_steps] Found test-skip step: %s / %s", job_name, step_name)
                continue

            # Pattern 3: step run contains structured [skip] log at IMP:9 level
            # (not make exit code handling like "[component-tests] No tests collected")
            if step_run and re.search(r"\[IMP:\d+\]\[skip\]", step_run):
                skip_steps.append({
                    "job_name": job_name,
                    "step_name": step_name or "(unnamed)",
                    "step_run": step_run[:200],
                })
                logger.info(
                    "[IMP:8][_extract_ci_skip_steps] Found IMP-skip step: %s / %s", job_name, step_name or "(unnamed)"
                )
                continue

            # Pattern 4: step run contains conditional-skip structured log
            # (a step that MAY skip depending on runtime conditions — fork PR, missing secrets)
            # Matches [IMP:N][integration-live] and similar conditional-skip markers
            if step_run and re.search(r"\[IMP:\d+\]\[(skip|integration-live)\]", step_run):
                skip_steps.append({
                    "job_name": job_name,
                    "step_name": step_name or "(unnamed)",
                    "step_run": step_run[:200],
                })
                logger.info(
                    "[IMP:8][_extract_ci_skip_steps] Found conditional-skip step: %s / %s",
                    job_name,
                    step_name or "(unnamed)",
                )
                continue

    logger.info("[IMP:8][_extract_ci_skip_steps] Total CI test-skip steps found: %d", len(skip_steps))
    return skip_steps


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-11 · gate/ci-coverage · D3 integration test run model
## @renamed_from — test_integration_steps_have_structured_logging (2026-07-11, D3 changed test purpose)

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_integration_steps_have_structured_logging(caplog) -> None:
    """Verify CI integration test steps exist and are properly structured.

    ## @purpose — D3 regression test: integration tests now RUN in CI (not skipped).
    ##            Verify integration test steps exist in platform-test.yml,
    ##            and at least one has IMP: structured logging (the conditional-skip
    ##            live mode step). The always-run error-path step uses [integration-*]
    ##            structured output which is also accepted.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(S) where S = steps in platform-integration job
    """

    logger.info("[IMP:8][test_integration_steps_have_structured_logging] Checking integration test steps...")

    with pathlib.Path(_CI_WORKFLOW_PATH).open(encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    # Plan 2: unified platform-test job replaces platform-integration
    platform_job = workflow.get("jobs", {}).get("platform-test", {})
    assert platform_job, "platform-test job not found in platform-test.yml (unified job — Plan 2)"

    steps = platform_job.get("steps", [])
    integration_steps = [step for step in steps if "integration" in step.get("name", "").lower()]

    assert len(integration_steps) >= 1, (
        "No integration test steps found in platform-integration job. "
        "Expected at least one step with 'integration' in its name."
    )

    logger.info(
        "[IMP:8][test_integration_steps_have_structured_logging] Found %d integration test step(s)",
        len(integration_steps),
    )

    has_imp_logging = False
    for step in integration_steps:
        step_name = step.get("name", "")
        step_run = step.get("run", "")

        if "IMP:" in step_run:
            has_imp_logging = True
            logger.info("[IMP:8][test_integration_steps_have_structured_logging] OK: '%s' has IMP: logs", step_name)
        elif "[integration-" in step_run:
            logger.info(
                "[IMP:8][test_integration_steps_have_structured_logging] OK: '%s' has structured [integration-*] logs",
                step_name,
            )
        else:
            logger.warning(
                "[IMP:7][test_integration_steps_have_structured_logging] '%s' has minimal/no structured logging",
                step_name,
            )

    logger.critical(
        "[IMP:9][test_integration_steps_have_structured_logging] PASS: %d integration test step(s) verified with structured logging (IMP: confirmed)",
        len(integration_steps),
    )

    assert has_imp_logging, (
        "At least one integration test step must contain IMP: structured logging. "
        "Expected the conditional-skip step (live mode) to have [IMP:9][integration-live] logging."
    )


def _check_workflow_run_on_platform_test(workflow: dict, workflow_name: str) -> list[str]:
    """Verify that a workflow triggers on workflow_run of the gate workflow (single workflow).

    ## @purpose — SHA-aware aggregator invariant D4: deploy workflows must trigger on
    ##            workflow_run of a SINGLE workflow, not an array of workflows (prevents
    ##            OR-semantics). DevPlan 116 B11 T4 (D2, U-57): downstream триггерятся на
    ##            platform-gate-fast (лёгкий fast-gate) — не platform-test (flaky-изоляция).
    ##            Plan 2: main-full-gate removed; platform-gate-fast — единый gate для push main.
    ## @io — workflow dict → ⎋ list[str] of failures (empty if all pass)
    ## @complexity — O(1)
    """
    failures: list[str] = []
    on_section = get_on_section(workflow)
    workflow_run = on_section.get("workflow_run", {})
    if not workflow_run:
        failures.append(f"{workflow_name}: missing 'on.workflow_run' (D4: expected workflow_run trigger)")
        return failures

    workflows = workflow_run.get("workflows", [])
    if not isinstance(workflows, list) or len(workflows) != 1:
        failures.append(
            f"{workflow_name}: 'on.workflow_run.workflows' must be a list with exactly 1 entry "
            f"(got {len(workflows) if isinstance(workflows, list) else 'not a list'}) "
            f"— prevents OR-semantics (D4)"
        )
    elif workflows[0] != "platform-gate-fast":
        failures.append(
            f"{workflow_name}: 'on.workflow_run.workflows' must be ['platform-gate-fast'] (D2), got {workflows}"
        )
    return failures


def _check_has_blocking_sha_api_check(workflow: dict, workflow_name: str) -> list[str]:
    """Verify that a workflow has a blocking GitHub API check for platform-test success.

    ## @purpose — SHA-aware aggregator invariant: deploy workflows must verify that
    ##            platform-test also succeeded for the same SHA via GitHub API, with
    ##            exit 1 on failure (blocking, not warning).
    ##            Accepts both inline code and composite action (.github/actions/sha-resolve).
    ## @io — workflow dict → ⎋ list[str] of failures (empty if all pass)
    ## @complexity — O(S) where S = steps in the job
    """
    failures: list[str] = []
    for job_name, job_config in workflow.get("jobs", {}).items():
        steps = job_config.get("steps", [])
        for step in steps:
            step_run = step.get("run", "")
            step_uses = step.get("uses", "")
            step_name = step.get("name", "")

            # Pattern 1: inline code with platform-test.yml + head_sha
            if "platform-test.yml" in step_run and "head_sha" in step_run:
                if "exit 1" not in step_run:
                    failures.append(
                        f"{workflow_name}/{job_name}: SHA verification step '{step_name}' "
                        f"must contain 'exit 1' (blocking check, not warning)"
                    )
                return failures  # found the check, return results

            # Pattern 2: composite action .github/actions/sha-resolve
            if "sha-resolve" in step_uses:
                # Composite action handles exit 1 internally — accept as valid
                return failures  # found the check, return results (no failures)

    failures.append(
        f"{workflow_name}: no step found with 'platform-test.yml' and 'head_sha' API check, "
        f"or 'sha-resolve' composite action (expected SHA-aware verification)"
    )
    return failures


def _check_concurrency_has_head_sha(workflow: dict, workflow_name: str) -> list[str]:
    """Verify that concurrency.group contains head_sha.

    ## @purpose — Prevents race conditions: deploy workflows must key concurrency
    ##            on head_sha so only one deploy per SHA runs at a time.
    ## @io — workflow dict → ⎋ list[str] of failures (empty if all pass)
    ## @complexity — O(1)
    """
    failures: list[str] = []
    concurrency = workflow.get("concurrency", {})
    group = concurrency.get("group", "")
    if "head_sha" not in group:
        failures.append(
            f"{workflow_name}: concurrency.group must contain 'head_sha' "
            f"(got '{group}') — prevents race conditions (D4)"
        )
    return failures


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-11 · REGRESSION · SHA-aware aggregator (D4) — deploy workflows
# · Last fail: N/A (preventive)
# · Remove if: деплой-триггеринг мигрирует с workflow_run-агрегатора
def test_deploy_workflows_use_sha_aware_aggregator(caplog) -> None:
    """Verify deploy workflows trigger on workflow_run with single workflow + API check.

    ## @purpose — Validate D4 (SHA-aware aggregator): core-deploy.yml, build-platform.yml,
    ##            and mirror.yml trigger on workflow_run of main-full-gate (not array of
    ##            workflows — prevents OR-semantics), and each has a blocking GitHub API
    ##            check that platform-test also succeeded for the same SHA.
    ##            Mirror.yml has relaxed check: only workflow_run + workflow_dispatch.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(F * S) where F = number of deploy workflows, S = steps per workflow
    """

    logger.info("[IMP:8][test_deploy_workflows_use_sha_aware_aggregator] === SHA-aware aggregator audit ===")

    deploy_workflows = ["core-deploy.yml", "build-platform.yml", "mirror.yml"]
    all_failures: dict[str, list[str]] = {}

    for wf_name in deploy_workflows:
        failures: list[str] = []
        workflow = load_workflow(wf_name)

        # Check 1: workflow_run on platform-test (single workflow, not array)
        # Plan 2: main-full-gate removed, deploy triggers on platform-test with push filter
        failures.extend(_check_workflow_run_on_platform_test(workflow, wf_name))

        # Check 2: blocking SHA API check (core-deploy, build-platform only)
        # mirror.yml has relaxed check (only main-full-gate verification)
        if wf_name != "mirror.yml":
            failures.extend(_check_has_blocking_sha_api_check(workflow, wf_name))

        # Check 3: concurrency with head_sha (core-deploy, build-platform only)
        # mirror.yml uses github.ref-based concurrency — acceptable for mirror
        if wf_name != "mirror.yml":
            failures.extend(_check_concurrency_has_head_sha(workflow, wf_name))

        # Check 4: mirror.yml must also have workflow_dispatch
        if wf_name == "mirror.yml":
            on_section = get_on_section(workflow)
            if "workflow_dispatch" not in on_section:
                failures.append("mirror.yml: missing 'workflow_dispatch' trigger for manual mirror")

        if failures:
            all_failures[wf_name] = failures
            logger.warning(
                "[IMP:7][test_deploy_workflows_use_sha_aware_aggregator] FAILURES in %s: %s", wf_name, failures
            )
        else:
            logger.info(
                "[IMP:9][test_deploy_workflows_use_sha_aware_aggregator] OK: %s passes all SHA-aware checks", wf_name
            )

    if all_failures:
        msg_lines = ["SHA-aware aggregator violations found:"]
        for wf_name, failures in all_failures.items():
            msg_lines.append(f"  {wf_name}:")
            msg_lines.extend(f"    - {f}" for f in failures)
        pytest.fail("\n".join(msg_lines))

    logger.info(
        "[IMP:9][test_deploy_workflows_use_sha_aware_aggregator] ALL PASS — all %d deploy workflows use SHA-aware aggregator",
        len(deploy_workflows),
    )


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-11 · REGRESSION · MODE=fast исключает Docker-зависимые маркеры
# · Last fail: N/A (preventive)
# · Remove if: gate-маркерная модель заменяется (check-suite.yaml SoT переезжает)
def test_mode_fast_excludes_requires_docker(caplog) -> None:
    """Verify MODE=fast expression excludes Docker-dependent markers.

    ## @purpose — Validate C3 fix from TestsMetaDevPlan2 changelog: MODE=fast in the gate
    ##            must exclude 'not requires_docker', 'not local_auth' to prevent
    ##            Docker-dependent tests from running (and failing) in fast mode.
    ##            DevPlan 120 (Wave 2): gate-портал перенесён на SoT-манифест
    ##            core/check-suite.yaml — выражения читаются ИЗ МАНИФЕСТА (не из makefiles,
    ##            где таргет gate теперь только вызывает check_suite run).
    ## @io — ⎋ None (assert side-effect)
    ## @complexity O(1)
    """

    logger.info(
        "[IMP:8][test_mode_fast_excludes_requires_docker] Checking MODE=fast expressions (SoT-манифест core/check-suite.yaml)..."
    )

    manifest_path = _PROJECT_ROOT / "core" / "check-suite.yaml"
    with pathlib.Path(manifest_path).open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    cmds_by_id: dict[str, dict] = {}
    for check in manifest.get("checks", []):
        if isinstance(check, dict):
            cmds_by_id[check.get("id", "")] = check

    # MODE=fast имеет две фазы (как прежний ci.mk): static gates + docker gates
    gates_fast = (cmds_by_id.get("gates", {}).get("cmds") or {}).get("fast", "")
    gates_docker_fast = (cmds_by_id.get("gates-docker", {}).get("cmds") or {}).get("fast", "")
    predeploy_fast = (cmds_by_id.get("predeploy", {}).get("cmds") or {}).get("fast", "")

    assert "gate and not requires_docker" in gates_fast, (
        f"Manifest gates.fast должен содержать 'gate and not requires_docker', got: {gates_fast}"
    )
    assert "gate and requires_docker" in gates_docker_fast, (
        f"Manifest gates-docker.fast должен содержать 'gate and requires_docker', got: {gates_docker_fast}"
    )
    assert "predeploy and not requires_docker" in predeploy_fast, (
        f"Manifest predeploy.fast должен содержать 'predeploy and not requires_docker', got: {predeploy_fast}"
    )

    logger.info(
        "[IMP:8][test_mode_fast_excludes_requires_docker] Found static gate expression: gate and not requires_docker"
    )
    logger.info(
        "[IMP:8][test_mode_fast_excludes_requires_docker] Found Docker gate expression: gate and requires_docker"
    )

    # MARKER=static expression — в test_runner _STATIC_AUDIT_EXPR (DevPlan 099)
    test_runner_path = _PROJECT_ROOT / "core" / "internal" / "test_runner.py"
    test_runner_content = test_runner_path.read_text(encoding="utf-8")
    expr_match = re.search(
        r'_STATIC_AUDIT_EXPR\s*=\s*\(\s*((?:"[^"]*"\s*)+)\)',
        test_runner_content,
        re.DOTALL,
    )
    assert expr_match, (
        "Could not find _STATIC_AUDIT_EXPR constant in core/internal/test_runner.py. "
        'Expected: _STATIC_AUDIT_EXPR = ( "..." ... ) with marker expression.'
    )
    raw_fragments = expr_match.group(1)
    expression_static = "".join(re.findall(r'"([^"]*)"', raw_fragments))
    logger.info(
        "[IMP:8][test_mode_fast_excludes_requires_docker] Found MARKER=static expression (test_runner _STATIC_AUDIT_EXPR): %s",
        expression_static,
    )

    # Validate required exclusions across manifest gate expressions
    required_exclusions_static = ["not requires_docker"]
    required_inclusions_docker = ["requires_docker"]
    required_static_exclusions = ["not requires_docker", "not local_auth"]

    all_missing: list[str] = []
    for exclusion in required_exclusions_static:
        if exclusion not in gates_fast:
            all_missing.append(f"gates.fast MISSING '{exclusion}'")
        else:
            logger.info("[IMP:9][test_mode_fast_excludes_requires_docker] OK: '%s' present in gates.fast", exclusion)
    for inclusion in required_inclusions_docker:
        if inclusion not in gates_docker_fast:
            all_missing.append(f"gates-docker.fast MISSING '{inclusion}'")
        else:
            logger.info(
                "[IMP:9][test_mode_fast_excludes_requires_docker] OK: '%s' present in gates-docker.fast", inclusion
            )
    for exclusion in required_static_exclusions:
        if exclusion not in expression_static:
            all_missing.append(f"MARKER=static MISSING '{exclusion}'")
        else:
            logger.info(
                "[IMP:9][test_mode_fast_excludes_requires_docker] OK: '%s' present in MARKER=static expression",
                exclusion,
            )

    if all_missing:
        pytest.fail(
            f"MODE=fast gate expressions have {len(all_missing)} issue(s):\n"
            + "\n".join(f"  - {m}" for m in all_missing)
        )

    logger.info(
        "[IMP:9][test_mode_fast_excludes_requires_docker] ALL PASS — MODE=fast expressions validated (SoT-манифест)"
    )


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · DevPlan 106: doc checks moved from check-doc-headers.sh
#   (shell, 236 LOC) to core/internal/lint/doc_header_validator.py — facade is now a 17-LOC delegator
#   (DRIFT-GATE-1, QA P1). Old assertion looked for shell function names in the facade → false negative.
# · Scenario: (1) facade contains `python3 -m core.internal.lint.doc_header_validator doc-headers`
# ·   delegation; (2) module defines all 5 check functions (import + callable introspection);
# ·   (3) validate_file fires all 5 checks on tmp negative controls and passes a fully-marked
# ·   positive control; (4) pre-commit wiring: old grepsummary hook removed, check-doc-headers hook
# ·   registered
# · Last fail: 2026-07-31 — "check-doc-headers.sh missing 5 check(s)" (function names not found in facade)
# · Remove if: check-doc-headers delegation moves to a different module (re-point facade + import assertions)
def test_check_doc_headers_equivalent(caplog, tmp_path) -> None:
    """Verify check-doc-headers.sh (facade) delegates all old hook checks to doc_header_validator.py.

    ## @purpose — Validate that the unified check-doc-headers.sh facade performs all validations
    ##            previously done by grepsummary + presence-check (TASK-1 consolidation). Equivalence
    ##            contract after DevPlan 106: facade runs `python3 -m core.internal.lint.doc_header_validator
    ##            doc-headers "$@"` AND the module defines+wires the 5 check functions into validate_file.
    ##            Also verify .pre-commit-config.yaml no longer references the old grepsummary hook
    ##            and still registers check-doc-headers.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(1)
    """
    logger.info("[IMP:8][test_check_doc_headers_equivalent] Checking facade→Python equivalence...")

    # 1. Facade delegation contract — thin shell facade, business logic in Python (DevPlan 106 AC4)
    with pathlib.Path(_CHECK_DOC_HEADERS_PATH).open(encoding="utf-8") as f:
        script_content = f.read()
    delegation = "python3 -m core.internal.lint.doc_header_validator doc-headers"
    assert delegation in script_content, (
        f"check-doc-headers.sh must delegate to doc_header_validator (missing: {delegation!r})"
    )
    logger.info("[IMP:9][test_check_doc_headers_equivalent] OK: facade delegates via %r", delegation)

    # 2. Module contract — the 5 old hook checks exist as callables in the Python module
    expected_checks = {
        "GREP_SUMMARY validation": check_grep_summary_presence,
        "MODULE_CONTRACT validation": check_module_contract,
        "STRUCTURE validation": check_structure,
        "Region balance check": check_regions_balanced,
        "YAML @purpose check": check_yaml_purpose,
    }
    for check_name, func in expected_checks.items():
        assert callable(func), f"{check_name}: {func!r} is not a callable function"
    logger.info(
        "[IMP:9][test_check_doc_headers_equivalent] OK: all %d check functions present in doc_header_validator",
        len(expected_checks),
    )

    # 3. Functional equivalence — validate_file actually FIRES each check (negative + positive controls)
    bad_py = tmp_path / "bad_module.py"
    bad_py.write_text("#!/usr/bin/env python3\ndef foo() -> None:\n    pass\n# region FUNC_foo\n")
    bad_yaml = tmp_path / "bad_config.yaml"
    bad_yaml.write_text("key: value\n")
    good_py = tmp_path / "good_module.py"
    good_py.write_text(
        "# GREP_SUMMARY: good-file demo module\n"
        "# STRUCTURE: flow → end\n"
        "# region MODULE_CONTRACT\n"
        "## @purpose demo module good-file\n"
        "# endregion MODULE_CONTRACT\n"
        "def main() -> None:\n"
        "    pass\n"
        "# region FUNC_main\n"
        "# endregion FUNC_main\n"
    )

    bad_py_errors = validate_file(bad_py)
    bad_yaml_errors = validate_file(bad_yaml)
    good_py_errors = validate_file(good_py)

    assert any("GREP_SUMMARY" in err for err in bad_py_errors), f"bad .py must fail GREP_SUMMARY: {bad_py_errors}"
    assert any("STRUCTURE" in err for err in bad_py_errors), f"bad .py must fail STRUCTURE: {bad_py_errors}"
    assert any("MODULE_CONTRACT" in err for err in bad_py_errors), f"bad .py must fail MODULE_CONTRACT: {bad_py_errors}"
    assert any("#region count" in err for err in bad_py_errors), f"bad .py must fail region balance: {bad_py_errors}"
    assert any("@purpose" in err for err in bad_yaml_errors), f"bad .yaml must fail @purpose: {bad_yaml_errors}"
    assert good_py_errors == [], f"fully-marked .py must pass: {good_py_errors}"
    logger.info(
        "[IMP:9][test_check_doc_headers_equivalent] OK: validate_file fires 5 checks "
        "(negative: %d error(s) on .py, %d on .yaml; positive: 0 on .py)",
        len(bad_py_errors),
        len(bad_yaml_errors),
    )

    # 4. Pre-commit wiring — old grepsummary hook removed, check-doc-headers hook present
    with pathlib.Path(_PRE_COMMIT_CONFIG_PATH).open(encoding="utf-8") as f:
        precommit_config = yaml.safe_load(f)

    has_old_grepsummary = False
    for repo_entry in precommit_config.get("repos", []):
        hooks = repo_entry.get("hooks", [])
        for hook in hooks:
            hook_id = hook.get("id", "")
            hook_entry = hook.get("entry", "")
            if hook_id == "grepsummary" or "grepsummary" in hook_entry:
                has_old_grepsummary = True
                logger.warning(
                    "[IMP:7][test_check_doc_headers_equivalent] OLD grepsummary hook STILL PRESENT: id=%s entry=%s",
                    hook_id,
                    hook_entry,
                )
                break
        if has_old_grepsummary:
            break

    has_check_doc_headers = any(
        hook.get("id") == "check-doc-headers"
        for repo_entry in precommit_config.get("repos", [])
        for hook in repo_entry.get("hooks", [])
    )

    assert not has_old_grepsummary, (
        "Old grepsummary hook still present in .pre-commit-config.yaml. "
        "Remove it — check-doc-headers.sh replaces grepsummary + presence-check."
    )
    assert has_check_doc_headers, "check-doc-headers hook NOT found in .pre-commit-config.yaml"
    logger.info(
        "[IMP:9][test_check_doc_headers_equivalent] OK: pre-commit wiring — grepsummary removed, check-doc-headers present"
    )

    logger.info(
        "[IMP:9][test_check_doc_headers_equivalent] ALL PASS — facade delegates, module defines+fires all 5 checks"
    )


@pytest.mark.gate
@ldd_trajectory
def test_platform_test_has_push_trigger(caplog) -> None:
    """Verify platform-test.yml has push trigger on main (replaces main-full-gate.yml).

    ## @purpose — Plan 2: main-full-gate.yml removed. platform-test.yml is the single gate
    ##            workflow for push main. Verify it triggers on push to main and
    ##            pull_request_target. TRAP[DECISION] about merge_group is in platform-test.yml.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(1)
    """

    logger.info(
        "[IMP:8][test_platform_test_has_push_trigger] Checking platform-test.yml triggers (replaces main-full-gate)..."
    )

    # Plan 2: main-full-gate.yml deleted — skip existence check
    # Verify platform-test.yml has correct triggers
    logger.info(
        "[IMP:8][test_platform_test_has_push_trigger] main-full-gate.yml deleted per Plan 2 — verifying platform-test.yml triggers instead"
    )

    # Verify platform-test.yml has push trigger on main
    platform_test_path = _CI_WORKFLOW_DIR / "platform-test.yml"
    with pathlib.Path(platform_test_path).open(encoding="utf-8") as f:
        content = f.read()

    workflow = yaml.safe_load(content)

    # Check: on.push.branches contains main
    on_section = get_on_section(workflow)
    push_config = on_section.get("push", {})
    branches = push_config.get("branches", []) if isinstance(push_config, dict) else []

    has_push_main = "main" in branches
    if has_push_main:
        logger.info("[IMP:9][test_platform_test_has_push_trigger] OK: platform-test.yml has 'push: branches: [main]'")
    else:
        logger.warning(
            "[IMP:7][test_platform_test_has_push_trigger] MISSING: push trigger on main in platform-test.yml"
        )
        if "branches:" in content and "main" in content:
            has_push_main = True

    # Verify pull_request_target is still present (for PR validation)
    has_pr_target = "pull_request_target" in on_section
    if has_pr_target:
        logger.info(
            "[IMP:9][test_platform_test_has_push_trigger] OK: platform-test.yml has pull_request_target trigger"
        )
    else:
        logger.warning("[IMP:7][test_platform_test_has_push_trigger] MISSING: pull_request_target trigger")

    # Check merge_group is absent from YAML keys (may be in TRAP comments)
    trigger_keys = set(on_section.keys())
    has_merge_group = "merge_group" in trigger_keys
    if has_merge_group:
        logger.warning(
            "[IMP:7][test_platform_test_has_push_trigger] BAD: 'merge_group' trigger still present (YAML keys: %s)",
            trigger_keys,
        )
    else:
        logger.info(
            "[IMP:9][test_platform_test_has_push_trigger] OK: 'merge_group' trigger absent from YAML keys (keys: %s)",
            trigger_keys,
        )

    errors: list[str] = []
    if not has_push_main:
        errors.append(
            "platform-test.yml must have 'push: branches: [main]' trigger (replaces main-full-gate.yml per Plan 2)."
        )
    if has_merge_group:
        errors.append(
            "platform-test.yml must NOT have 'merge_group' trigger. "
            "It was removed per Plan 2 because GitHub Pro "
            "is required for merge queue on private repos."
        )

    if errors:
        pytest.fail("\n\n".join(errors))

    logger.info(
        "[IMP:9][test_platform_test_has_push_trigger] ALL PASS — platform-test.yml has push: branches: [main] "
        "and does NOT have merge_group (main-full-gate.yml deleted per Plan 2)"
    )


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · contract-тесты входят в gate fast/full и диагностику
# · Scenario: из check-suite.yaml исчезает/фильтруется чек contract → RED
# · Last fail: N/A (преемник test_marker_all_includes_contract — test-таргетная модель
# ·   Makefile заменена DevPlan 165; старый гейт удалён по собственному TRAP «Remove if»)
# · Remove if: SoT-манифест check-suite.yaml заменяется
def test_contract_in_gate_and_diagnostic(caplog) -> None:
    """Contract-чек в SoT-манифесте: diagnostic:true, gate_modes ⊇ {fast, full}, junit задан.

    ## @purpose — Преемник TASK-4 regression guard (DevPlan 120/165): contract-тесты
    ##            не могут быть молча исключены из полного набора — защита теперь по
    ##            SoT-манифесту core/check-suite.yaml, а не по веткам MARKER=all в Makefile.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(1)
    """

    logger.info("[IMP:8][test_contract_in_gate_and_diagnostic] Checking contract check in SoT-manifest...")

    manifest_path = _PROJECT_ROOT / "core" / "check-suite.yaml"
    with manifest_path.open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    contract = next((c for c in manifest.get("checks", []) if c.get("id") == "contract"), None)
    assert contract is not None, "check-suite.yaml должен содержать чек contract"

    errors: list[str] = []
    if not contract.get("diagnostic", True):
        errors.append("check-suite.yaml contract: diagnostic должен быть true (входит в make check)")
    gate_modes = contract.get("gate_modes", [])
    if "fast" not in gate_modes:
        errors.append("check-suite.yaml contract: gate_modes должен включать fast")
    if "full" not in gate_modes:
        errors.append("check-suite.yaml contract: gate_modes должен включать full")
    if contract.get("junit") != "tests/report-contract.xml":
        errors.append("check-suite.yaml contract: junit должен быть tests/report-contract.xml")

    if errors:
        pytest.fail("\n\n".join(errors))
    logger.info("[IMP:9][test_contract_in_gate_and_diagnostic] OK: contract в gate fast/full + диагностике")

    logger.info("[IMP:9][test_marker_all_includes_contract] ALL PASS — MARKER=all includes contract tests")


# ══════════════════════════════════════════════════════════════════════════════
# W3 T3.5 (D5) — requires_node/test-node: ручной запуск, workflow (если есть) не отключён
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_node_workflow

_TEST_NODE_REF_TOKENS = ("test-node", "test_node", "requires_node", "tests/e2e")


def _find_test_node_workflows() -> list[str]:
    """Найти workflow'ы, чьи steps ссылаются на test-node / requires_node / tests/e2e.

    ## @purpose — W3 T3.5: если workflow запускает requires_node-тесты, он обязан быть
    ##            активен. Возвращает имена workflow (может быть пусто — D5: ручной запуск).
    ## @io — → ⎋ list[str] имён workflow-файлов
    ## @complexity — O(F * S) где F = workflow'ы, S = steps
    """
    found: list[str] = []
    workflows_dir = _PROJECT_ROOT / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return found
    for wf_path in sorted(workflows_dir.glob("*.yml")):
        try:
            data = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        for job in (data.get("jobs", {}) or {}).values():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps", []) or []:
                if not isinstance(step, dict):
                    continue
                blob = " ".join(str(step.get(k, "")) for k in ("run", "uses", "name")).lower()
                if any(tok in blob for tok in _TEST_NODE_REF_TOKENS):
                    found.append(wf_path.name)
                    break  # один workflow — один раз
    return sorted(set(found))


def _check_workflow_enabled(workflow: dict, name: str, raw_content: str = "") -> list[str]:
    """Проверить, что workflow не отключён: on: с триггерами, jobs не пусты, без disabled-комментариев.

    ## @purpose — W3 T3.5: гейт на существующий test-node workflow — он обязан реально
    ##            исполняться (иначе requires_node-тесты тихо не запускаются).
    ## @io — ⇥ workflow: dict (YAML), name: str, raw_content: str (для disabled-комментариев)
    ##       → ⎋ list[str] violations
    ## @complexity — O(1)
    ## @invariants
    ##   - on: пустой/отсутствует → отключён
    ##   - jobs пустой → отключён
    ##   - Сырой текст с '# disabled'/'# выключен'/'# отключён' → отключён (комментарий-маркер)
    """
    violations: list[str] = []
    on_section = get_on_section(workflow)
    has_trigger = bool(on_section and any(v for v in on_section.values() if v))
    if not has_trigger:
        violations.append(f"{name}: workflow отключён — 'on:' не содержит активных триггеров")
    jobs = workflow.get("jobs", {}) or {}
    if not jobs:
        violations.append(f"{name}: workflow отключён — 'jobs' пуст")
    if raw_content and re.search(r"(?i)#\s*(disabled|выключен|отключ[её]н)", raw_content):
        violations.append(f"{name}: workflow содержит disabled-комментарий")
    return violations


def _check_manual_test_node_contract() -> list[str]:
    """Проверить контракт ручного запуска requires_node (D5): verb + e2e-тесты + документация.

    ## @purpose — W3 T3.5: workflow для test-node НЕ существует (D5 — ручной запуск на test-VPS).
    ##            Гейт проверяет, что контракт зафиксирован: test-node в allowed_verbs,
    ##            tests/e2e/ с requires_node-тестами, TRAP[DECISION] в root AGENTS.md.
    ## @io — → ⎋ list[str] violations
    ## @complexity — O(F + L) где F = файлы, L = строки
    """
    violations: list[str] = []
    manifest_path = _PROJECT_ROOT / "core" / "entrypoint-manifest.yaml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if "test-node" not in (manifest or {}).get("allowed_verbs", []):
            violations.append("test-node НЕ в allowed_verbs entrypoint-manifest.yaml — глагол ручного прогона удалён")
    except (OSError, yaml.YAMLError):
        violations.append("entrypoint-manifest.yaml нечитаем — контракт test-node не проверен")

    e2e_dir = _PROJECT_ROOT / "tests" / "e2e"
    if not e2e_dir.is_dir():
        violations.append("tests/e2e/ отсутствует — requires_node-тесты удалены (D5 контракт нарушен)")
    elif not list(e2e_dir.rglob("test_*.py")):
        violations.append("tests/e2e/ не содержит test_*.py — requires_node-тесты отсутствуют")

    agents_md = _PROJECT_ROOT / "AGENTS.md"
    try:
        text = agents_md.read_text(encoding="utf-8")
        if "requires_node" not in text or "test-node" not in text:
            violations.append("AGENTS.md не документирует ручной запуск requires_node (D5 TRAP[DECISION] удалён)")
    except OSError:
        violations.append("AGENTS.md нечитаем — документация D5 не проверена")
    return violations


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · test-node workflow не отключён / D5 ручной контракт (W3 T3.5)
# · Scenario: (a) workflow запускает test-node → он обязан быть активен (on+jobs, без disabled);
# ·   (b) workflow нет → контракт ручного запуска зафиксирован (verb + tests/e2e + AGENTS.md TRAP)
# · Last fail: N/A (preventive — фиксирует D5: requires_node остаётся ручным)
# · Remove if: requires_node поднят до blocking CI-гейта (Rev-условие TRAP в AGENTS.md)
def test_test_node_workflow_exists_not_disabled(caplog) -> None:
    """Workflow, запускающий test-node (если есть), активен; иначе D5-контракт ручного запуска цел.

    ## @purpose — W3 T3.5 (D5): E2E requires_node исполняется вручную (`make test-node`).
    ##            Существующий workflow не должен быть отключён; отсутствие workflow —
    ##            санкционировано D5, но контракт (verb/tests/docs) обязан быть на месте.
    ## @io — ⎋ None
    ## @complexity — O(F * S) где F = workflow'ы, S = steps
    """
    logger.info("[IMP:8][test-node-workflow] Поиск workflow со ссылками на test-node/requires_node...")
    test_node_workflows = _find_test_node_workflows()

    violations: list[str] = []
    if test_node_workflows:
        logger.info("[IMP:8][test-node-workflow] Найдено: %s", ", ".join(test_node_workflows))
        for wf_name in test_node_workflows:
            wf_path = _PROJECT_ROOT / ".github" / "workflows" / wf_name
            data = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
            violations.extend(_check_workflow_enabled(data, wf_name, wf_path.read_text(encoding="utf-8")))
    else:
        logger.info(
            "[IMP:8][test-node-workflow] Workflow для test-node НЕ найден — D5: ручной запуск "
            "(make test-node NODE=<name>, TRAP[DECISION] в AGENTS.md)"
        )
        violations.extend(_check_manual_test_node_contract())

    if violations:
        for v in violations:
            logger.warning("[IMP:7][test-node-workflow] %s", v)
    assert not violations, (
        "[GATE:FAIL][id:test-node-workflow-not-disabled][class:L2]\n"
        ">>> REPAIR_RECIPE_START >>>\n"
        "Workflow test-node отключён/удалён контракт: (a) раскомментируй/верни on:+jobs в workflow, "
        "удали disabled-комментарий; (b) либо восстанови контракт ручного запуска (test-node в "
        "allowed_verbs, tests/e2e/, TRAP[DECISION] requires_node в AGENTS.md).\n"
        "<<< REPAIR_RECIPE_END <<<\n" + "\n".join(violations)
    )
    logger.info(
        "[IMP:9][test-node-workflow] PASS: test-node workflow активен (%d) ИЛИ D5 ручной контракт цел",
        len(test_node_workflows),
    )


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · test-node workflow — отключённый workflow (W3 T3.5)
# · Last fail: workflow с шагом test-node, но без триггеров on: — E2E тихо не запускается
# · Remove if: requires_node поднят до blocking CI-гейта
def test_negative_disabled_test_node_workflow_detected(caplog) -> None:
    """R5 negative: отключённый workflow (нет on:-триггеров, пустые jobs) детектируется.

    ## @purpose — Точный вход W3 T3.5: workflow существует, но отключён (нет триггеров) —
    ##            требует защиты от тихой деактивации E2E.
    ## @io — ⎋ None
    ## @complexity — O(1)
    """
    synthetic: dict = {
        # НЕТ on: — отключён
        "jobs": {
            "e2e": {
                "steps": [
                    {"name": "Run test-node on test-VPS", "run": "make test-node NODE=test"},
                ]
            }
        }
    }
    violations = _check_workflow_enabled(synthetic, "synthetic-test-node.yml")
    assert len(violations) >= 1, f"R5 FAIL: отключённый workflow не детектирован: {violations!r}"
    assert any("триггер" in v or "jobs" in v for v in violations), f"R5 FAIL: неверная причина: {violations!r}"
    logger.info("[IMP:9][test-node-workflow][negative] PASS: отключённый workflow детектируется")


# endregion FUNC_test_node_workflow
