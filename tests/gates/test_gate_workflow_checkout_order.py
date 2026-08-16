# GREP_SUMMARY: gate workflow-checkout-order composite-action local-action checkout-before-uses drift CICD-order
# STRUCTURE: ▶ scan .github/workflows/*.yml → ◇ find uses: ./.github/actions/* → ◇ verify actions/checkout precedes → ◇ assert ordering
# region MODULE_CONTRACT
## @purpose — Gate test: verify that `uses: ./.github/actions/*` (local composite actions)
##            always appear AFTER `uses: actions/checkout` in workflow YAML files.
## @scope — Parses all .github/workflows/*.yml files for step ordering violations.
## @invariants
##   - Any step using `uses: ./.github/actions/<name>` MUST have a preceding
##     `uses: actions/checkout` step in the same job
##   - Composite actions (.github/actions/) are LOCAL — they require checkout
##   - Standard actions (actions/checkout, docker/setup-buildx-action, etc.)
##     don't require local checkout and can appear anywhere
## @rationale — P0 incident 2026-07-23: sha-resolve composite action before checkout
##            broke core-deploy and mirror workflows (build-platform удалён DevPlan 002).
##            The same bug was fixed for setup-platform (commit e8ad2a9) but wasn't caught
##            systematically for other local actions. This gate prevents recurrence.
## @see F2/F3/F4 fixes in core-deploy.yml, mirror.yml
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_WORKFLOW_DIR: pathlib.Path = repo_root() / ".github" / "workflows"

# Pattern to detect local composite action usage
_LOCAL_ACTION_PATTERN = re.compile(r"\./\.github/actions/")

# Standard checkout action patterns
_CHECKOUT_PATTERN = re.compile(r"actions/checkout@")

# Allowlisted files (workflows that intentionally use local actions before checkout)
# None currently — this is always a bug
_ALLOWLISTED_WORKFLOWS: set[str] = set()


def _get_step_uses(step: dict) -> str:
    """Extract the 'uses' field from a workflow step dict."""
    return step.get("uses", "")


def _scan_workflows_for_ordering() -> list[tuple[str, str, str]]:
    """Scan workflow YAML files for local actions appearing before checkout.

    ## @purpose — Detect steps that use local composite actions (.github/actions/)
    ##            without a preceding checkout step in the same job.
    ## @io — ⎋ list[(workflow_file, job_name, action_name)]
    ## @complexity — O(F * J * S) where F=workflow files, J=jobs, S=steps
    """
    findings: list[tuple[str, str, str]] = []

    for wf_file in sorted(_WORKFLOW_DIR.glob("*.yml")):
        wf_name = wf_file.name

        if wf_name in _ALLOWLISTED_WORKFLOWS:
            logger.info("[IMP:8][scan][allowlisted] Skipping %s", wf_name)
            continue

        try:
            with pathlib.Path(wf_file).open(encoding="utf-8") as f:
                workflow = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("[IMP:7][scan] Cannot parse %s: %s", wf_name, exc)
            continue

        if not isinstance(workflow, dict):
            continue

        jobs = workflow.get("jobs", {})
        if not isinstance(jobs, dict):
            continue

        for job_name, job_config in jobs.items():
            if not isinstance(job_config, dict):
                continue

            steps = job_config.get("steps", [])
            if not isinstance(steps, list):
                continue

            has_checkout = False

            for step in steps:
                if not isinstance(step, dict):
                    continue

                uses = _get_step_uses(step)

                # Check if this is a checkout step
                if _CHECKOUT_PATTERN.search(uses):
                    has_checkout = True
                    continue

                # Check if this is a local action
                if _LOCAL_ACTION_PATTERN.search(uses):
                    if not has_checkout:
                        # Extract action name for the error message
                        action_name = uses.split("/")[-1] if "/" in uses else uses
                        findings.append((wf_name, job_name, action_name))
                        logger.warning(
                            "[IMP:7][scan][order-violation] %s/%s: '%s' uses local action '%s' BEFORE checkout",
                            wf_name,
                            job_name,
                            step.get("name", "(unnamed)"),
                            uses,
                        )
                    else:
                        logger.info(
                            "[IMP:8][scan][ok] %s/%s: local action '%s' after checkout",
                            wf_name,
                            job_name,
                            uses,
                        )

    return findings


@pytest.mark.gate
def test_local_actions_after_checkout(caplog):
    """Verify local composite actions always appear after checkout in workflow files.

    ## @purpose — Prevent P0 CI bug: local composite actions (.github/actions/*)
    ##            require a checked-out repository. If they appear before
    ##            actions/checkout in the steps list, the workflow fails with
    ##            "Can't find 'action.yml' under .../.github/actions/<name>".
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(F * J * S) deferred to _scan_workflows_for_ordering()
    """
    caplog.set_level(logging.INFO)
    findings = _scan_workflows_for_ordering()

    if findings:
        detail_lines = [
            f"  {wf}/{job} → .github/actions/{action} (no checkout before)" for wf, job, action in sorted(findings)
        ]
        logger.error(
            "[IMP:9][gate][checkout-order] ⛔ Found %d local action(s) before checkout",
            len(findings),
        )
        pytest.fail(
            f"Found {len(findings)} local composite action(s) used before checkout.\n"
            f"Local actions (.github/actions/*) require actions/checkout to run first.\n"
            f"Reorder steps: move actions/checkout BEFORE any .github/actions/* usage.\n"
            f"See F2/F3/F4 fixes (core-deploy, mirror) as reference.\n\n" + "\n".join(detail_lines)
        )

    logger.info("[IMP:9][gate][checkout-order] ✅ All local actions appear after checkout in workflow files")
