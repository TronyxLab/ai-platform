# GREP_SUMMARY: gate project-context consistency d2 context-field removal post-d2-validation
# STRUCTURE: ▶ glob projects/*/*/ai-platform.yaml → ○ for each: extract context from path → ◇ check no "context" field in YAML → ⊕ assert all clean → ⎋ PASS/FAIL
# region MODULE_CONTRACT
## @purpose  D2 gate — validate context consistency: all projects derive context from directory path,
##           not from a `context:` field in YAML (post-D2 removal enforcement).
## @scope    Scans projects/ directory tree for ai-platform.yaml files, checks each for:
##           1. Path-based context derivation (projects/<context>/<project>/)
##           2. Absence of legacy `context:` field in the YAML body
## @invariants
##   - projects/ directory may not exist (dev environment) → skip gracefully
##   - Each ai-platform.yaml must NOT contain a `context:` field (post-D2)
##   - Path structure must be projects/<context>/<project>/ai-platform.yaml (3 levels)
## @rationale  D2 enforcement gate: after removing `context` from schema, writers, and templates,
##             this gate prevents re-introduction by validating all existing and future projects.
##             Context is now exclusively derived from filesystem path.
## @usecases
##   - make gate MODE=fast → validates all registered projects have no legacy context field
##   - CI pipeline → blocks merge if any project still has context: in YAML
## @changes — 2026-07-20 | Created per DevPlan 020 Task 5.1
# endregion MODULE_CONTRACT

import glob
import logging
import os

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory

_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PROJECTS_DIR = os.path.join(_PROJECT_ROOT, "projects")

_logger = logging.getLogger(__name__)


# region FUNC_test_project_context_consistency
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-20 · REGRESSION · D2 context removal enforcement
# · Last fail: N/A (preventive)
# · Remove if: projects/ directory structure fundamentally changes (not just field removal)
def test_project_context_consistency(caplog) -> None:
    """Validate all projects have consistent context derivation and no legacy context field.

    ## @purpose — D2 enforcement gate: verify context is derived from path, not YAML body.
    ## @io — ⎋ None. Assert: all projects pass context consistency checks.
    ## @complexity — O(N * M) where N = yaml files, M = avg file size
    """
    # region BLOCK_CheckProjectsDir
    if not os.path.isdir(_PROJECTS_DIR):
        _logger.info("[IMP:7][gate][context] Projects directory not found: %s — skip (dev environment)", _PROJECTS_DIR)
        pytest.skip("No projects/ directory — dev environment")
    # endregion

    # region BLOCK_FindYamls
    yaml_pattern = os.path.join(_PROJECTS_DIR, "*", "*", "ai-platform.yaml")
    yaml_files = glob.glob(yaml_pattern)
    _logger.info("[IMP:8][gate][context] Glob pattern: %s → %d files", yaml_pattern, len(yaml_files))

    if not yaml_files:
        _logger.info("[IMP:7][gate][context] No ai-platform.yaml files found in projects/ — skip")
        pytest.skip("No project configs found")
    # endregion

    # region BLOCK_ValidateEach
    issues: list[str] = []

    for yaml_path in sorted(yaml_files):
        abs_path = os.path.realpath(yaml_path)

        # Extract context from path: projects/<context>/<project>/ai-platform.yaml
        context_from_path = os.path.basename(os.path.dirname(os.path.dirname(abs_path)))
        _logger.info(
            "[IMP:7][gate][context] %s → context_from_path=%s",
            os.path.relpath(yaml_path, _PROJECTS_DIR),
            context_from_path,
        )

        # Verify path structure: 3 levels deep under projects/
        rel_path = os.path.relpath(yaml_path, _PROJECTS_DIR)
        path_parts = rel_path.split(os.sep)
        if len(path_parts) != 3:
            issues.append(f"{yaml_path}: unexpected nesting (expected 3 levels, got {len(path_parts)})")
            _logger.error("[IMP:9][gate][context] NESTING: %s → parts=%s", yaml_path, path_parts)

        # Read YAML and check for legacy context field
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
        except Exception as exc:
            issues.append(f"{yaml_path}: cannot parse YAML: {exc}")
            _logger.error("[IMP:9][gate][context] PARSE FAIL: %s — %s", yaml_path, exc)
            continue

        if data is None:
            issues.append(f"{yaml_path}: empty YAML file")
            _logger.error("[IMP:9][gate][context] EMPTY: %s", yaml_path)
            continue

        if "context" in data:
            issues.append(f"{yaml_path}: contains legacy 'context: {data['context']}' field — D2 requires removal")
            _logger.error("[IMP:9][gate][context] LEGACY FIELD: %s → context=%s", yaml_path, data["context"])

    # endregion

    # region BLOCK_Report
    if issues:
        for issue in issues:
            _logger.error("[IMP:9][gate][context] FAIL: %s", issue)
        pytest.fail(f"Context consistency issues found ({len(issues)}):\n" + "\n".join(issues))

    _logger.info(
        "[IMP:9][gate][context] All %d projects pass context consistency check — no legacy context fields",
        len(yaml_files),
    )
    # endregion


# endregion FUNC_test_project_context_consistency
