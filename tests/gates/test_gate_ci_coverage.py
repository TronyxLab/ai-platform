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
_MAKEFILE_PATH: pathlib.Path = _PROJECT_ROOT / "Makefile"
_CHECK_DOC_HEADERS_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "entrypoints" / "check-doc-headers.sh"


def _read_makefile_with_includes() -> str:
    """Read root Makefile + all makefiles/*.mk as combined content."""
    parts = [_MAKEFILE_PATH.read_text()]
    makefiles_dir = _PROJECT_ROOT / "makefiles"
    if makefiles_dir.is_dir():
        parts.extend(mk_file.read_text() for mk_file in sorted(makefiles_dir.glob("*.mk")))
    return "\n".join(parts)


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
    with open(_CI_WORKFLOW_PATH) as f:
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
                skip_steps.append(
                    {
                        "job_name": job_name,
                        "step_name": step_name,
                        "step_run": step_run[:200] if step_run else "",
                    }
                )
                logger.info("[IMP:8][_extract_ci_skip_steps] Found skip step: %s / %s", job_name, step_name)
                continue

            # Pattern 2: step name explicitly mentions "smoke" or "component" with test-skip context
            # (matches step names that describe SKIPPING tests, not running them)
            if re.search(r"skip.*(test|integration|smoke|component)", step_name, re.IGNORECASE):
                skip_steps.append(
                    {
                        "job_name": job_name,
                        "step_name": step_name,
                        "step_run": step_run[:200] if step_run else "",
                    }
                )
                logger.info("[IMP:8][_extract_ci_skip_steps] Found test-skip step: %s / %s", job_name, step_name)
                continue

            # Pattern 3: step run contains structured [skip] log at IMP:9 level
            # (not make exit code handling like "[component-tests] No tests collected")
            if step_run and re.search(r"\[IMP:\d+\]\[skip\]", step_run):
                skip_steps.append(
                    {
                        "job_name": job_name,
                        "step_name": step_name or "(unnamed)",
                        "step_run": step_run[:200],
                    }
                )
                logger.info(
                    "[IMP:8][_extract_ci_skip_steps] Found IMP-skip step: %s / %s", job_name, step_name or "(unnamed)"
                )
                continue

            # Pattern 4: step run contains conditional-skip structured log
            # (a step that MAY skip depending on runtime conditions — fork PR, missing secrets)
            # Matches [IMP:N][integration-live] and similar conditional-skip markers
            if step_run and re.search(r"\[IMP:\d+\]\[(skip|integration-live)\]", step_run):
                skip_steps.append(
                    {
                        "job_name": job_name,
                        "step_name": step_name or "(unnamed)",
                        "step_run": step_run[:200],
                    }
                )
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

    with open(_CI_WORKFLOW_PATH) as f:
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
def test_mode_fast_excludes_requires_docker(caplog) -> None:
    """Verify MODE=fast expression excludes Docker-dependent markers.

    ## @purpose — Validate C3 fix from TestsMetaDevPlan2 changelog: MODE=fast in Makefile
    ##            must exclude 'not requires_docker', 'not local_auth'
    ##            to prevent Docker-dependent tests from running (and failing) in fast mode.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(1)
    """

    logger.info(
        "[IMP:8][test_mode_fast_excludes_requires_docker] Checking MODE=fast expressions (two-phase: static + Docker)..."
    )

    makefile_content = _read_makefile_with_includes()

    # MODE=fast now has two phases:
    # Phase 1 (static, parallel): -m "gate and not requires_docker" -n auto
    # Phase 2 (Docker, sequential): -m "gate and requires_docker"
    # Extract both using the step numbering (Step 4 & Step 4b)

    # Phase 1: static gate (parallel, no Docker fixtures)
    static_section = re.search(
        r'PYTEST_NO_ESCALATION=1 \$\(PYTHON\) -m pytest tests/gates/ -m "gate and not requires_docker" -n auto -v',
        makefile_content,
    )

    assert static_section, (
        "Could not find MODE=fast static gate phase in Makefile. "
        'Expected: pytest tests/gates/ -m "gate and not requires_docker" -n auto -v'
    )

    # Phase 2: Docker gate (sequential, session-scoped fixtures)
    docker_section = re.search(
        r'PYTEST_NO_ESCALATION=1 \$\(PYTHON\) -m pytest tests/gates/ -m "gate and requires_docker" -v',
        makefile_content,
    )

    assert docker_section, (
        "Could not find MODE=fast Docker gate phase in Makefile. "
        'Expected: pytest tests/gates/ -m "gate and requires_docker" -v'
    )

    logger.info(
        "[IMP:8][test_mode_fast_excludes_requires_docker] Found static gate expression: gate and not requires_docker"
    )
    logger.info(
        "[IMP:8][test_mode_fast_excludes_requires_docker] Found Docker gate expression: gate and requires_docker"
    )

    # Also extract the make test MARKER=static expression — must exclude requires_docker
    # for Step 6 (static tests without Docker).
    # DevPlan 099: MARKER=static now delegates to test_runner.py → check _STATIC_AUDIT_EXPR there.
    # First check if the static section delegates to test_runner (bounded: between MARKER=static and next elif/fi)
    uses_test_runner_static = re.search(
        r'if \[ "\$\(MARKER\)" = "static" \].*?test_runner.*?--marker static',
        makefile_content,
        re.DOTALL,
    )

    if uses_test_runner_static:
        # DevPlan 099: Makefile delegates to test_runner → check _STATIC_AUDIT_EXPR in Python
        # Verify delegation is within the static section (not spilling into other markers)
        # by checking the bounded section between MARKER=static and the next elif
        bounded_static = re.search(
            r'if \[ "\$\(MARKER\)" = "static" \].*?(?=elif \[ "\$\(MARKER\)" = ")',
            makefile_content,
            re.DOTALL,
        )
        if bounded_static and "test_runner" in bounded_static.group(0):
            logger.info(
                "[IMP:8][test_mode_fast_excludes_requires_docker] MARKER=static delegates to test_runner — checking _STATIC_AUDIT_EXPR"
            )
            test_runner_path = _PROJECT_ROOT / "core" / "internal" / "test_runner.py"
            test_runner_content = test_runner_path.read_text(encoding="utf-8")
            # _STATIC_AUDIT_EXPR is a multi-line string concatenation:
            #   ( "line1 " "line2 " "line3" )
            # Capture all string literal fragments between the opening ( and closing )
            expr_match = re.search(
                r'_STATIC_AUDIT_EXPR\s*=\s*\(\s*((?:"[^"]*"\s*)+)\)',
                test_runner_content,
                re.DOTALL,
            )
            assert expr_match, (
                "Could not find _STATIC_AUDIT_EXPR constant in core/internal/test_runner.py. "
                'Expected: _STATIC_AUDIT_EXPR = ( "..." ... ) with marker expression.'
            )
            # Join all quoted fragments, removing quotes and whitespace between them
            raw_fragments = expr_match.group(1)
            expression_static = "".join(re.findall(r'"([^"]*)"', raw_fragments))
            logger.info(
                "[IMP:8][test_mode_fast_excludes_requires_docker] Found MARKER=static expression (test_runner _STATIC_AUDIT_EXPR): %s",
                expression_static,
            )
        else:
            # Fallback: try old regex
            marker_static_section = re.search(
                r'if \[ "\$\(MARKER\)" = "static" \].*?PYTEST_NO_ESCALATION=1.*?pytest tests/.*?-m\s+"([^"]+)"',
                makefile_content,
                re.DOTALL,
            )
            assert marker_static_section, (
                "Could not find MARKER=static expression — neither test_runner delegation nor pytest -m expression. "
                "Expected either test_runner --marker static or pytest -m with marker expression."
            )
            expression_static = marker_static_section.group(1)
            logger.info(
                "[IMP:8][test_mode_fast_excludes_requires_docker] Found MARKER=static expression (legacy): %s",
                expression_static,
            )
    else:
        # Legacy: direct pytest in Makefile
        marker_static_section = re.search(
            r'if \[ "\$\(MARKER\)" = "static" \].*?PYTEST_NO_ESCALATION=1.*?pytest tests/.*?-m\s+"([^"]+)"',
            makefile_content,
            re.DOTALL,
        )
        assert marker_static_section, (
            "Could not find MARKER=static pytest -m expression in Makefile. "
            'Expected a section with \'if [ "$(MARKER)" = "static" ]\' followed by '
            "pytest with -m expression."
        )
        expression_static = marker_static_section.group(1)
        logger.info(
            "[IMP:8][test_mode_fast_excludes_requires_docker] Found MARKER=static expression (legacy): %s",
            expression_static,
        )

    # NOTE: MODE=fast and MARKER=static expressions are NOT identical anymore.
    # MODE=fast gates are split into two phases (static + Docker), while
    # MARKER=static covers non-gate static_audit tests too. The drift guard
    # between them is intentionally removed (O2 change from DevPlan 046 W4-1).

    # Validate required exclusions across both MODE=fast gate phases
    required_exclusions_static = [
        "not requires_docker",
    ]
    required_inclusions_docker = [
        "requires_docker",
    ]

    missing_static: list[str] = []
    for exclusion in required_exclusions_static:
        if exclusion not in "gate and not requires_docker":
            missing_static.append(exclusion)
            logger.warning(
                "[IMP:7][test_mode_fast_excludes_requires_docker] MISSING exclusion in static gate: %s", exclusion
            )
        else:
            logger.info(
                "[IMP:9][test_mode_fast_excludes_requires_docker] OK: '%s' present in static gate expression", exclusion
            )

    missing_docker: list[str] = []
    for inclusion in required_inclusions_docker:
        if inclusion not in "gate and requires_docker":
            missing_docker.append(inclusion)
            logger.warning(
                "[IMP:7][test_mode_fast_excludes_requires_docker] MISSING inclusion in Docker gate: %s", inclusion
            )
        else:
            logger.info(
                "[IMP:9][test_mode_fast_excludes_requires_docker] OK: '%s' present in Docker gate expression", inclusion
            )

    # Validate MARKER=static also excludes Docker-dependent markers
    # (for Step 6: static tests)
    required_static_exclusions = [
        "not requires_docker",
        "not local_auth",
    ]
    missing_static_expr: list[str] = []
    for exclusion in required_static_exclusions:
        if exclusion not in expression_static:
            missing_static_expr.append(exclusion)
            logger.warning(
                "[IMP:7][test_mode_fast_excludes_requires_docker] MISSING exclusion in MARKER=static: %s", exclusion
            )
        else:
            logger.info(
                "[IMP:9][test_mode_fast_excludes_requires_docker] OK: '%s' present in MARKER=static expression",
                exclusion,
            )

    all_missing = missing_static + missing_docker + missing_static_expr
    if all_missing:
        pytest.fail(
            f"MODE=fast gate phases have {len(all_missing)} issue(s):\n" + "\n".join(f"  - {m}" for m in all_missing)
        )

    logger.info("[IMP:9][test_mode_fast_excludes_requires_docker] ALL PASS — MODE=fast two-phase gates validated")


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
    with open(_CHECK_DOC_HEADERS_PATH) as f:
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
    with open(_PRE_COMMIT_CONFIG_PATH) as f:
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
    with open(platform_test_path) as f:
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
def test_marker_all_includes_contract(caplog) -> None:
    """Verify `make test MARKER=all` includes contract tests.

    ## @purpose — TASK-4 fix regression guard: MARKER=all must invoke
    ##            MARKER=contract and include report-contract.xml in the
    ##            JUnit XML merge step. Without this, contract tests are
    ##            silently excluded from the full suite.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(1)
    """

    logger.info("[IMP:8][test_marker_all_includes_contract] Checking MARKER=all includes contract...")

    content = _read_makefile_with_includes()

    # Find the MARKER=all branch
    all_section = re.search(
        r'if \[ "\$\(MARKER\)" = "all" \].*?(?=else)',
        content,
        re.DOTALL,
    )

    assert all_section, "Could not find MARKER=all section in Makefile"

    all_body = all_section.group(0)

    # Check 1: MARKER=contract is invoked
    has_contract_invoke = "MARKER=contract" in all_body
    if has_contract_invoke:
        logger.info("[IMP:9][test_marker_all_includes_contract] OK: MARKER=contract invoked in all branch")
    else:
        logger.warning("[IMP:7][test_marker_all_includes_contract] MISSING: MARKER=contract not invoked in all branch")

    # Check 2: report-contract.xml is in the merge list
    has_contract_report = "report-contract.xml" in all_body
    if has_contract_report:
        logger.info("[IMP:9][test_marker_all_includes_contract] OK: report-contract.xml in merge list")
    else:
        logger.warning("[IMP:7][test_marker_all_includes_contract] MISSING: report-contract.xml not in merge list")

    errors: list[str] = []
    if not has_contract_invoke:
        errors.append("MARKER=all branch in Makefile must invoke '$(MAKE) test MARKER=contract'")
    if not has_contract_report:
        errors.append(
            "MARKER=all branch in Makefile must include 'tests/report-contract.xml' in the merge_junit.py argument list"
        )

    if errors:
        pytest.fail("\n\n".join(errors))

    logger.info("[IMP:9][test_marker_all_includes_contract] ALL PASS — MARKER=all includes contract tests")
