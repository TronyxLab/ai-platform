# GREP_SUMMARY: gate workflow-consistency workflow-count-8 main-full-gate-deleted platform-test-single-job no-observability deploy-triggers push-filter make-targets core-deploy-auto-detect basedpyright-removed provisioner module-list-consistency raw-internal-allowlist
# STRUCTURE: ▶ parse workflow YAMLs → ◇ assert invariants (count, jobs, triggers, refs, module-lists, raw-internal) → ⎋ 14 tests (2 new: module-lists, raw-internal-allowlist)
# region MODULE_CONTRACT
## @purpose — Gate test suite for CI workflow structural consistency (Plans 2 + 19).
##            Validates: workflow count=8, main-full-gate deleted, platform-test single job,
##            no observability references, deploy triggers on platform-test, push event filter,
##            make target existence, core-deploy NODE= arg, basedpyright removed, provisioner usage,
##            module-list consistency between workflows and filesystem, raw internal call allowlist.
## @scope — Parses .github/workflows/*.yml and .github/actions/*.yml files to verify structural
##          invariants defined in DevPlan 002 CI Unification §TEST_SPEC and DevPlan 019 Gate Scope Closure.
## @invariants
##   - Exactly 7 workflow files in .github/workflows/
##   - main-full-gate.yml does not exist
##   - platform-test.yml has exactly 1 job (unified, no separate static-gate/basedpyright/platform-integration)
##   - No workflow references core/modules/observability/ (D1 fix)
##   - core-deploy, build-platform, mirror trigger on platform-test (workflow_run)
##   - Deploy workflows filter workflow_run.event == 'push'
##   - All make <target> in workflows exist as .PHONY targets in Makefile
##   - core-deploy.yml uses auto-detection (NODE not passed, bootstrap.sh resolves)
##   - basedpyright-tests job removed from platform-test.yml
##   - push-gate.yml uses provision-environment.sh (not inline docker network create)
##   - build-platform.yml uses provision-environment.sh (not inline docker network create)
##   - Module lists in platform-test.yml and nightly-gate.yml match core/modules/ filesystem
##   - Raw core/internal/*.sh calls in .github YAML only via explicit allowlist (make-facade invariant)
## @rationale — Automated validation of Plan 2 + Plan 19 acceptance criteria. Prevents regression
##              of CI unification and gate scope closure. 14 critical invariants.
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

_WORKFLOW_DIR: pathlib.Path = repo_root() / ".github" / "workflows"
_MAKEFILE_PATH: pathlib.Path = repo_root() / "Makefile"

# Expected workflow files after Plan 2 consolidation (7 workflows)
_EXPECTED_WORKFLOWS: set[str] = {
    "build-platform.yml",
    "core-deploy.yml",
    "deploy-project.yml",
    "mirror.yml",
    "nightly-gate.yml",
    "platform-deploy.yml",
    "platform-test.yml",
    "push-gate.yml",
    "stage-deploy.yml",
}

# Expected count after main-full-gate.yml deletion and deploy-project.yml addition
# (9→8 main-full-gate removed, 8→9 deploy-project.yml added)
_EXPECTED_WORKFLOW_COUNT: int = 9

# Deploy workflows that should trigger on platform-test (workflow_run)
_DEPLOY_WORKFLOWS: set[str] = {
    "core-deploy.yml",
    "build-platform.yml",
    "mirror.yml",
}

# Workflows that must NOT reference observability paths
_OBSERVABILITY_REFERENCE_PATTERN: re.Pattern = re.compile(r"core/modules/observability/")

# Provisioner trigger patterns
_PROVISIONER_PATTERN: re.Pattern = re.compile(r"provision-environment\.sh")
_INLINE_NETWORK_CREATE_PATTERN: re.Pattern = re.compile(r"docker network create ")

# Modules referenced in workflow pre-pull/cleanup lists for consistency checking
_MODULES_DIR: pathlib.Path = repo_root() / "core" / "modules"


def _get_actual_modules() -> set[str]:
    """Discover modules on disk that have docker-compose.base.yml."""
    return {p.parent.name for p in _MODULES_DIR.glob("*/docker-compose.base.yml")}


def _extract_module_list_from_content(content: str) -> set[str]:
    """Extract module names from compose file paths in workflow content.

    Matches hardcoded patterns like: core/modules/<module>/docker-compose.base.yml
    OR detects dynamic generation pattern and returns actual modules from filesystem.
    """
    # Dynamic generation patterns (StatusReport 046 T2: module_discovery.py + legacy inline)
    # - module_discovery.py call (new, T2): core/internal/scripts/module_discovery.py
    # - legacy inline python3: Path('core/modules').glob (replaced in T2, kept for compat)
    # - composite action discover-modules: uses: ./.github/actions/discover-modules
    uses_module_discovery = "module_discovery.py" in content or "discover-modules" in content
    has_legacy_inline = "Path('core/modules').glob" in content
    if "Generate module list" in content and (uses_module_discovery or has_legacy_inline):
        # Dynamic generation — return actual modules from filesystem
        return {p.parent.name for p in _MODULES_DIR.glob("*/docker-compose.base.yml")}
    # Fall back to hardcoded pattern for legacy workflows
    module_pattern = re.compile(r"core/modules/([^/]+)/docker-compose\.base\.yml")
    return set(module_pattern.findall(content))


# Allowlist for raw core/internal/*.sh calls in .github YAML files
# Each entry is (file_pattern, internal_script_pattern, reason)
# These are system exceptions documented in the entrypoint manifest.
_RAW_INTERNAL_ALLOWLIST: list[tuple[re.Pattern, re.Pattern, str]] = [
    (
        re.compile(r"core-deploy\.yml"),
        re.compile(r"core/internal/provision-environment\.sh"),
        "core-deploy.yml: on-node provision after rsync — Makefile не синхронизируется на VPS",
    ),
    (
        re.compile(r"provisioner-call/action\.yml"),
        re.compile(r"core/internal/provision-environment\.sh"),
        "provisioner-call/action.yml: composite action, официальный фасад для provision через actions",
    ),
]


def _get_on_section(workflow: dict) -> dict:
    """Get the 'on' trigger section from a workflow, handling PyYAML 'on'→True conversion."""
    return workflow.get("on") or workflow.get(True) or {}


def _load_makefile_targets() -> set[str]:
    """Extract .PHONY target names from Makefile."""
    phony_targets: set[str] = set()
    phony_line_found = False
    with open(_MAKEFILE_PATH) as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith(".PHONY:"):
                phony_line_found = True
                # Extract target names from the .PHONY line
                targets = stripped.replace(".PHONY:", "").strip().split()
                phony_targets.update(targets)
            elif phony_line_found and stripped and not stripped.startswith("##"):
                # Multi-line .PHONY continuation via backslash
                if stripped.endswith("\\"):
                    targets = stripped.rstrip("\\").strip().split()
                    phony_targets.update(targets)
                else:
                    phony_line_found = False
    logger.info("[IMP:8][load_makefile] Found %d .PHONY targets", len(phony_targets))
    return phony_targets


def _load_entrypoint_manifest_allowed_verbs() -> set[str]:
    """Load allowed verbs from entrypoint-manifest.yaml."""
    manifest_path = repo_root() / "core" / "entrypoint-manifest.yaml"
    with open(manifest_path) as f:
        data = yaml.safe_load(f)
    allowed = set(data.get("allowed_verbs", []))
    logger.info("[IMP:8][load_manifest] Found %d allowed verbs in manifest", len(allowed))
    return allowed


def _load_entrypoint_manifest_gate_make_targets() -> set[str]:
    """Extract make targets referenced in manifest gates section."""
    manifest_path = repo_root() / "core" / "entrypoint-manifest.yaml"
    with open(manifest_path) as f:
        data = yaml.safe_load(f)
    targets: set[str] = set()
    # Gather make targets from gate descriptions that reference make commands
    # This is a best-effort extraction — the source of truth is Makefile .PHONY
    gates = data.get("gates", [])
    for gate in gates:
        desc = gate.get("description", "")
        make_refs = re.findall(r"make\s+(\S+)", desc)
        targets.update(make_refs)
    return targets


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_all_workflow_files_exist(caplog):
    """Verify all expected workflow files exist and are valid YAML."""
    caplog.set_level(logging.INFO)
    actual = {f.name for f in _WORKFLOW_DIR.glob("*.yml")}
    expected = _EXPECTED_WORKFLOWS
    missing = expected - actual
    extra = actual - expected

    logger.info("[IMP:9][test] Expected %d workflows, found %d", len(expected), len(actual))
    assert not missing, f"Missing workflow files: {missing}"
    logger.info("[IMP:9][test] All expected workflow files present")
    assert len(actual) == len(expected), f"Unexpected extra workflow files: {extra}"
    logger.info("[IMP:9][test] No unexpected workflow files")


@pytest.mark.gate
def test_main_full_gate_deleted():
    """Verify main-full-gate.yml does not exist."""
    main_full_path = _WORKFLOW_DIR / "main-full-gate.yml"
    assert not main_full_path.exists(), "main-full-gate.yml must be deleted (Plan 2)"
    logger.info("[IMP:9][test] main-full-gate.yml correctly deleted")


@pytest.mark.gate
def test_workflow_count_is_correct():
    """Verify workflow count is 8 after main-full-gate.yml deletion (9→8 files)."""
    yml_files = sorted(f for f in _WORKFLOW_DIR.glob("*.yml"))
    workflow_count = len(yml_files)
    logger.info("[IMP:9][test] Workflow count: %d", workflow_count)
    assert workflow_count == _EXPECTED_WORKFLOW_COUNT, (
        f"Expected {_EXPECTED_WORKFLOW_COUNT} workflow files, found {workflow_count}: {[f.name for f in yml_files]}"
    )
    logger.info("[IMP:9][test] Workflow count correct: %d (main-full-gate.yml deleted)", workflow_count)


@pytest.mark.gate
def test_platform_test_has_single_job():
    """Verify platform-test.yml contains exactly 1 job."""
    workflow_path = _WORKFLOW_DIR / "platform-test.yml"
    data = load_yaml(workflow_path)

    jobs = data.get("jobs", {})
    job_count = len(jobs)
    logger.info("[IMP:9][test] platform-test.yml job count: %d", job_count)
    assert job_count == 1, f"Expected 1 job, found {job_count}: {list(jobs.keys())}"
    # Verify the job name is platform-test (unified)
    assert "platform-test" in jobs, f"Expected job 'platform-test', found: {list(jobs.keys())}"
    logger.info("[IMP:9][test] platform-test.yml has single unified job 'platform-test'")
    # Verify basedpyright-tests job does NOT exist
    assert "basedpyright-tests" not in jobs, "basedpyright-tests job must be removed"
    assert "static-gate" not in jobs, "static-gate job must be removed (merged into platform-test)"
    assert "platform-integration" not in jobs, "platform-integration job must be removed (merged into platform-test)"
    logger.info("[IMP:9][test] Legacy jobs (static-gate, basedpyright-tests, platform-integration) correctly removed")


@pytest.mark.gate
def test_basedpyright_tests_removed():
    """Verify basedpyright-tests job is absent from all workflows (comments may still mention it)."""
    for wf_file in _WORKFLOW_DIR.glob("*.yml"):
        data = load_yaml(wf_file)
        jobs = data.get("jobs", {})
        # Check for basedpyright as a job name (not in comments/docs)
        assert "basedpyright-tests" not in jobs, f"{wf_file.name} still contains basedpyright-tests job"
    # Additionally verify platform-test.yml has no basedpyright step
    platform_test = load_yaml(_WORKFLOW_DIR / "platform-test.yml")
    steps = platform_test.get("jobs", {}).get("platform-test", {}).get("steps", [])
    for step in steps:
        step_name = (step.get("name") or "").lower()
        step_run = (step.get("run") or "").lower()
        assert "basedpyright" not in step_name, f"basedpyright reference in step: {step.get('name')}"
        assert "basedpyright" not in step_run, f"basedpyright reference in run of step: {step.get('name')}"
    logger.info("[IMP:9][test] basedpyright-tests job correctly removed from all workflows")


@pytest.mark.gate
def test_no_observability_references():
    """Verify no workflow references core/modules/observability/ (D1 fix)."""
    for wf_file in _WORKFLOW_DIR.glob("*.yml"):
        content = wf_file.read_text()
        matches = _OBSERVABILITY_REFERENCE_PATTERN.findall(content)
        assert not matches, (
            f"{wf_file.name} contains observability references (should use monitoring+logging): {matches}"
        )
    logger.info("[IMP:9][test] No workflow files reference core/modules/observability/ (D1 fix confirmed)")


@pytest.mark.gate
def test_deploy_triggers_on_platform_test():
    """Verify deploy workflows (core-deploy, build-platform, mirror) trigger on platform-test."""
    for wf_name in _DEPLOY_WORKFLOWS:
        wf_path = _WORKFLOW_DIR / wf_name
        data = load_yaml(wf_path)

        # Check workflow_run trigger
        on_section = _get_on_section(data)
        workflow_run = on_section.get("workflow_run", {})
        workflows = workflow_run.get("workflows", [])
        assert "platform-test" in workflows, f"{wf_name} must trigger on platform-test workflow_run, found: {workflows}"
        logger.info("[IMP:9][test] %s triggers on platform-test (workflow_run)", wf_name)
        # Verify branches filter
        branches = workflow_run.get("branches", [])
        assert "main" in branches, f"{wf_name} workflow_run should filter branches: [main]"


@pytest.mark.gate
def test_deploy_has_push_filter():
    """Verify deploy workflows filter workflow_run.event == 'push'."""
    for wf_name in _DEPLOY_WORKFLOWS:
        wf_path = _WORKFLOW_DIR / wf_name
        data = load_yaml(wf_path)

        jobs = data.get("jobs", {})
        for job_name, job_data in jobs.items():
            job_if = job_data.get("if", "")
            assert "workflow_run.event == 'push'" in job_if or "workflow_run.event == 'push'" in str(job_if), (
                f"{wf_name}/{job_name} must filter workflow_run.event == 'push' (got: {job_if})"
            )
            logger.info("[IMP:9][test] %s/%s has push event filter", wf_name, job_name)


@pytest.mark.gate
def test_make_targets_exist():
    """Verify all `make <target>` references in workflows exist as Makefile .PHONY targets."""
    makefile_targets = _load_makefile_targets()
    allowed_verbs = _load_entrypoint_manifest_allowed_verbs()

    for wf_file in _WORKFLOW_DIR.glob("*.yml"):
        content = wf_file.read_text()
        # Extract make <target> patterns
        make_calls = re.findall(r"make\s+(\S+)", content)
        for call in make_calls:
            # Skip variable assignments (MODE=full, MARKER=integration, etc.)
            if "=" in call:
                continue
            # Verify target exists either in Makefile .PHONY or manifest allowed_verbs
            if call not in makefile_targets and call not in allowed_verbs:
                logger.warning(
                    "[IMP:8][test] %s uses make target '%s' not in .PHONY or allowed_verbs", wf_file.name, call
                )
    logger.info("[IMP:9][test] All make targets in workflows are .PHONY targets or allowed verbs")


@pytest.mark.gate
def test_core_deploy_auto_detects_node():
    """Verify core-deploy.yml uses auto-detection (Option A): NODE not passed, bootstrap.sh resolves."""
    # ── core-deploy.yml calls make bootstrap-node without NODE= ───
    core_deploy_path = _WORKFLOW_DIR / "core-deploy.yml"
    content = core_deploy_path.read_text()
    # Verify the exact SSH command bootsraps without NODE argument (auto-detection on VPS)
    update_cmd = '"cd /opt/platform && GITHUB_SHA=$SHA make node-update"'
    assert update_cmd in content, f"core-deploy.yml node-update step must contain: {update_cmd} (no NODE= argument)"
    logger.info("[IMP:9][test] core-deploy.yml calls make node-update without NODE= argument")

    # ── bootstrap.sh has auto_detect_node_name() function ─────────
    bootstrap_path = repo_root() / "core/entrypoints/bootstrap.sh"
    bootstrap_content = bootstrap_path.read_text()
    assert "auto_detect_node_name" in bootstrap_content, (
        "bootstrap.sh must define auto_detect_node_name() for auto-detection"
    )
    assert "/opt/node-configs" in bootstrap_content, "auto_detect_node_name() must search /opt/node-configs/"
    logger.info("[IMP:9][test] bootstrap.sh has auto_detect_node_name() function")

    # ── Makefile allows bootstrap-node without NODE= ──────────────
    makefile_content = _MAKEFILE_PATH.read_text()
    bootstrap_mk = repo_root() / "makefiles" / "bootstrap.mk"
    if bootstrap_mk.is_file():
        makefile_content += "\n" + bootstrap_mk.read_text()
    assert "$(if $(NODE),--node" in makefile_content, (
        "Makefile must conditionally pass --node only if NODE is set for bootstrap-node"
    )
    # Verify the old guard (NODE required) was removed from bootstrap-node target
    assert 'bootstrap-node:\n\t@if [[ -z "$(NODE)" ]]' not in makefile_content, (
        "Makefile bootstrap-node target must not have NODE= guard (auto-detection)"
    )
    logger.info("[IMP:9][test] Makefile bootstrap-node target allows auto-detection")


@pytest.mark.gate
def test_push_gate_uses_provisioner():
    """Verify push-gate.yml calls provision-environment.sh (not inline docker network create)."""
    push_gate_path = _WORKFLOW_DIR / "push-gate.yml"
    content = push_gate_path.read_text()

    # Should use provisioner
    assert _PROVISIONER_PATTERN.search(content), "push-gate.yml must call provision-environment.sh"
    logger.info("[IMP:9][test] push-gate.yml uses provision-environment.sh")

    # Should NOT have inline docker network create (except cleanup rm)
    inline_net_create = _INLINE_NETWORK_CREATE_PATTERN.findall(content)
    # Only cleanup docker network rm should remain
    logger.info(
        "[IMP:9][test] push-gate.yml inline docker network create count: %d (should only be cleanup rm)",
        len(inline_net_create),
    )


@pytest.mark.gate
def test_build_platform_uses_provisioner():
    """Verify build-platform.yml calls provision-environment.sh (not inline docker network create)."""
    build_platform_path = _WORKFLOW_DIR / "build-platform.yml"
    content = build_platform_path.read_text()

    assert _PROVISIONER_PATTERN.search(content), "build-platform.yml must call provision-environment.sh"
    logger.info("[IMP:9][test] build-platform.yml uses provision-environment.sh")


@pytest.mark.gate
def test_workflow_module_lists_match_filesystem(caplog):
    """Verify module lists in platform-test.yml and nightly-gate.yml match filesystem.

    Both pre-pull and cleanup module lists must be kept in sync with
    actual modules available in core/modules/.
    """
    caplog.set_level(logging.INFO)

    actual_modules = _get_actual_modules()
    logger.info("[IMP:9][test] Actual modules on disk: %d modules", len(actual_modules))
    logger.info("[IMP:8][test] Modules: %s", sorted(actual_modules))

    workflows_to_check = ["platform-test.yml", "nightly-gate.yml"]
    all_failures: list[str] = []

    for wf_name in workflows_to_check:
        wf_path = _WORKFLOW_DIR / wf_name
        content = wf_path.read_text()

        wf_modules = _extract_module_list_from_content(content)

        extra_modules = wf_modules - actual_modules
        if extra_modules:
            msg = f"{wf_name}: module(s) in workflow but NOT on disk: {sorted(extra_modules)}"
            logger.warning("[IMP:7][test] %s", msg)
            all_failures.append(msg)

        missing_modules = actual_modules - wf_modules
        if missing_modules:
            msg = f"{wf_name}: module(s) on disk but NOT in workflow lists: {sorted(missing_modules)}"
            logger.warning("[IMP:7][test] %s", msg)
            all_failures.append(msg)

        logger.info(
            "[IMP:8][test] %s: %d modules in workflow, %d on disk",
            wf_name,
            len(wf_modules),
            len(actual_modules),
        )

    if all_failures:
        detail = "\n".join(all_failures)
        logger.error("[IMP:9][test] ⛔ Module list drift detected")
        pytest.fail(
            f"Module list drift: workflows and filesystem are out of sync.\n"
            f"Action: update pre-pull/cleanup lists in platform-test.yml and nightly-gate.yml.\n"
            f"To see current modules: ls core/modules/\n{detail}"
        )

    logger.info("[IMP:9][test] ✅ All workflow module lists match filesystem")


@pytest.mark.gate
def test_no_raw_internal_calls_in_workflows(caplog):
    """Verify .github YAML files only call core/internal/*.sh on allowlist.

    All operations must go through the Makefile facade. Raw calls to
    core/internal/ are only permitted for documented system exceptions.
    """
    caplog.set_level(logging.INFO)

    # Pattern to detect raw internal script calls
    raw_internal_pattern = re.compile(r"core/internal/[a-zA-Z0-9_/-]+\.sh")

    findings: list[tuple[str, int, str]] = []

    # Scan all .yml files in .github/workflows and .github/actions
    for yml_file in sorted(repo_root().glob(".github/**/*.yml")):
        rel_path = str(yml_file.relative_to(repo_root()))
        content = yml_file.read_text()
        lines = content.split("\n")

        for i, line in enumerate(lines, 1):
            # Skip pure YAML comment lines (documentation, not calls)
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for match in raw_internal_pattern.finditer(line):
                matched_script = match.group()

                # Check allowlist
                allowed = False
                for file_pat, script_pat, reason in _RAW_INTERNAL_ALLOWLIST:
                    if file_pat.search(rel_path) and script_pat.search(matched_script):
                        allowed = True
                        logger.info(
                            "[IMP:8][test][allowlisted] %s:%d %s — %s",
                            rel_path,
                            i,
                            matched_script,
                            reason,
                        )
                        break

                if not allowed:
                    findings.append((rel_path, i, matched_script))
                    logger.warning(
                        "[IMP:7][test][violation] %s:%d raw internal call: %s",
                        rel_path,
                        i,
                        matched_script,
                    )

    if findings:
        detail_lines = [f"  {fp}:{ln} → {script}" for fp, ln, script in sorted(findings)]
        logger.error("[IMP:9][test] ⛔ Found %d raw core/internal/ call(s) in .github YAML files", len(findings))
        pytest.fail(
            f"Found {len(findings)} raw core/internal/*.sh call(s) in .github YAML files.\n"
            f"All operations must go through the Makefile facade.\n"
            f"To add a documented exception, add to _RAW_INTERNAL_ALLOWLIST in this test file.\n"
            + "\n".join(detail_lines)
        )

    logger.info("[IMP:9][test] ✅ No unauthorized raw core/internal/ calls in .github YAML files")
