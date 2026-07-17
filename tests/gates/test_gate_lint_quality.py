# GREP_SUMMARY: gate linter-parity bash-python name-linter module-lifecycle G1.3 anti-drift
# STRUCTURE: ▶ _make_temp_makefile → _run_bash_linter ∥ _run_python_linter → ◇ diff(results) → ⟦assert no diff⟧
# region MODULE_CONTRACT
## @purpose — Regression test: verify that bash (lint.sh namelint) and Python
##            (test_gate_manifest_integrity name-linter logic) linters give identical results
##            on the same set of test targets. G1.3 requirement.
## @scope — Creates a temporary Makefile with a curated set of test targets
##          (valid canonical, valid module, forbidden, system exceptions, unknown).
##          Runs both linters and compares their outputs.
## @invariants
##   - Both linters must agree on every target category
##   - Diff output on mismatch shows exact disagreement
## @rationale — Two independent linter implementations (bash awk + Python YAML)
##              must produce the same results. If they diverge, the CI gate and
##              pre-commit hook give different feedback, confusing developers.
# endregion MODULE_CONTRACT

import logging
import pathlib
import re
import subprocess

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_MANIFEST_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "entrypoint-manifest.yaml"
_LINT_SH_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "entrypoints" / "lint.sh"
_PATHS_SH_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "lib" / "paths.sh"


# ── Test target catalog ──
_TEST_TARGETS: dict[str, str] = {
    "deploy": "pass",
    "bootstrap-node": "pass",
    "up": "pass",
    "healthcheck": "pass",
    "start": "pass",
    "stop": "pass",
    "logs": "pass",
    "backup": "pass",
    "restore": "pass",
    "push-core": "fail",
    "deploy-node": "fail",
    "help": "pass",
    "venv": "pass",
    "test-foo": "pass",
    "gate-bar": "pass",
    "pre-commit-run": "pass",
    "foobar": "fail",
    "random-task": "fail",
}

_MAKEFILE_TEMPLATE: str = """# Temporary Makefile for parity test
.PHONY: {targets}
"""


def _make_temp_makefile(tmp_path: pathlib.Path, targets: list[str]) -> pathlib.Path:
    """Create a temporary Makefile with the given .PHONY targets.

    ## @purpose — Generate a minimal Makefile for the bash linter to parse.
    ## @io — ⇥ tmp_path: Path, targets: list[str] → ⎋ Path to generated Makefile
    ## @complexity — O(N) where N = number of targets
    """
    mf_path = tmp_path / "Makefile"
    phony_line = " ".join(targets)
    mf_path.write_text(_MAKEFILE_TEMPLATE.format(targets=phony_line))
    logger.info("[IMP:8][_make_temp_makefile] Created temp Makefile with %d targets", len(targets))
    return mf_path


def _run_bash_linter(makefile_path: pathlib.Path, tmp_path: pathlib.Path) -> list[str]:
    """Run lint.sh namelint on a temporary Makefile and return list of FAIL targets.

    ## @purpose — Execute the bash linter in isolation by creating a minimal
    ##            project directory structure that mirrors the real project layout.
    ## @io — ⇥ makefile_path: Path → ⎋ list[str] of target names that FAILED
    ## @complexity — O(N) where N = targets
    """
    temp_root = tmp_path / "project"
    temp_root.mkdir()
    temp_core = temp_root / "core"
    temp_core.mkdir()
    temp_entrypoints = temp_core / "entrypoints"
    temp_entrypoints.mkdir()

    manifest_link = temp_core / "entrypoint-manifest.yaml"
    manifest_link.symlink_to(_MANIFEST_PATH)

    temp_lib = temp_core / "lib"
    temp_lib.mkdir()
    paths_script = temp_lib / "paths.sh"
    paths_script.write_text(_PATHS_SH_PATH.read_text())

    lint_script = temp_entrypoints / "lint.sh"
    lint_script.write_text(_LINT_SH_PATH.read_text())
    lint_script.chmod(0o755)

    temp_makefile = temp_root / "Makefile"
    temp_makefile.write_text(makefile_path.read_text())

    result = subprocess.run(
        ["bash", "core/entrypoints/lint.sh", "namelint"],
        capture_output=True,
        text=True,
        cwd=str(temp_root),
    )

    failed_targets: list[str] = []
    for line in result.stdout.splitlines():
        if "[FAIL]" in line:
            m = re.search(r"'([^']+)'", line)
            if m:
                failed_targets.append(m.group(1))

    logger.info("[IMP:8][_run_bash_linter] Bash linter: %d FAIL, returncode=%d", len(failed_targets), result.returncode)
    return failed_targets


def _run_python_linter(test_targets: dict[str, str]) -> list[str]:
    """Run Python linter logic and return list of FAIL targets.

    ## @purpose — Duplicate the Python linter logic using the same manifest data.
    ## @io — ⇥ test_targets: dict[str, str] (target → expected verdict) → ⎋ list[str] of FAIL targets
    ## @complexity — O(N * L) where N = targets, L = lookup time
    """
    with open(_MANIFEST_PATH) as f:
        manifest = yaml.safe_load(f)

    allowed_verbs: set[str] = set(manifest.get("allowed_verbs", []))
    module_lifecycle: set[str] = set(manifest.get("module_lifecycle", []))
    forbidden_verbs: set[str] = set(manifest.get("forbidden_verbs", []))
    name_linter_config: dict = manifest.get("name_linter", {})
    system_exceptions: set[str] = set(name_linter_config.get("system_exceptions", ["help", "venv"]))
    system_prefixes: tuple[str, ...] = tuple(
        name_linter_config.get("system_prefixes", ["test-", "gate-", "pre-commit-"])
    )

    all_allowed: set[str] = allowed_verbs | module_lifecycle | system_exceptions

    failed_targets: list[str] = []
    for target in test_targets:
        if target in forbidden_verbs:
            failed_targets.append(target)
            continue
        if target in all_allowed:
            continue
        if target.startswith(system_prefixes):
            continue
        failed_targets.append(target)

    logger.info(
        "[IMP:8][_run_python_linter] Python linter: %d FAIL out of %d targets", len(failed_targets), len(test_targets)
    )
    return failed_targets


# ── Tests ──


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-10 · gate/linter-parity · G1.3 bash↔Python parity
def test_linter_parity(caplog, tmp_path) -> None:
    """Validate bash and Python linters produce identical results on test targets.

    ## @purpose — G1.3 regression gate: ensures the bash (lint.sh namelint) and
    ##            Python (manifest_integrity name-linter logic) linters agree.
    ## @io — ⎋ None (assert side-effect via pytest.fail on diff)
    ## @complexity — O(N) where N = test targets
    """
    logger.info("[IMP:8][test_linter_parity] === Bash vs Python linter parity check ===")

    target_names = sorted(_TEST_TARGETS.keys())
    mf_path = _make_temp_makefile(tmp_path, target_names)

    bash_fails = _run_bash_linter(mf_path, tmp_path)
    python_fails = _run_python_linter(_TEST_TARGETS)

    bash_only = set(bash_fails) - set(python_fails)
    python_only = set(python_fails) - set(bash_fails)

    expected_fails = sorted(t for t, v in _TEST_TARGETS.items() if v == "fail")
    bash_expected_diff = set(bash_fails) - set(expected_fails)
    python_expected_diff = set(python_fails) - set(expected_fails)
    bash_missed = set(expected_fails) - set(bash_fails)
    python_missed = set(expected_fails) - set(python_fails)

    errors: list[str] = []
    if bash_only:
        errors.append(f"Bash linter flagged targets that Python did not: {sorted(bash_only)}")
    if python_only:
        errors.append(f"Python linter flagged targets that Bash did not: {sorted(python_only)}")
    if bash_expected_diff:
        errors.append(f"Bash linter result differs from expected: {sorted(bash_expected_diff)}")
    if python_expected_diff:
        errors.append(f"Python linter result differs from expected: {sorted(python_expected_diff)}")
    if bash_missed:
        errors.append(f"Bash linter missed expected FAIL targets: {sorted(bash_missed)}")
    if python_missed:
        errors.append(f"Python linter missed expected FAIL targets: {sorted(python_missed)}")

    if errors:
        logger.error("[IMP:9][test_linter_parity] FAIL: %d disagreement(s)", len(errors))
    else:
        logger.info(
            "[IMP:9][test_linter_parity] ALL PASS — bash and Python linters agree on all %d targets", len(target_names)
        )

    assert not errors, "\n".join(errors)
