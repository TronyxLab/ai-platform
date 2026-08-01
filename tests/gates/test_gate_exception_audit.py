# GREP_SUMMARY: gate exception-audit hardcoded-targets entrypoint-manifest G1.2 anti-drift
# STRUCTURE: ▶ glob tests/gates/test_gate_*.py → ○ _scan_file_for_hardcoded_sets ◇ (is_target_set?) → ⊕ results → ⟦assert 0 violations⟧
# region MODULE_CONTRACT
## @purpose — Gate audit: verify no gate test file contains hardcoded target sets
##            that should be read from core/entrypoint-manifest.yaml. G1.2 requirement.
## @scope — Scans all tests/gates/test_gate_*.py files for set literals containing
##          make target names (strings matching [a-z][a-z-]+ pattern). Excludes
##          legitimate non-target sets (_DEPRECATED_PATTERNS, _EXCLUDED_DIRS, _SCAN_EXTENSIONS,
##          _EXCEPTION_FILES, _SHEBANG_EXCEPTION_PATTERNS, _EXCLUDE_DIRS).
## @invariants
##   - No gate test file may define a set literal with 3+ string elements that look like make targets
##   - Exceptions: explicitly named constants that are not target lists
##   - FAIL with file path and set contents for each violation
## @rationale — Prevents drift regression: if a new hardcoded target set is introduced,
##              this gate catches it at commit time (G1.2 Epic 1).
# endregion MODULE_CONTRACT

import ast
import logging
import pathlib
import re

import pytest

from tests.conftest import ldd_trajectory

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_GATES_DIR: pathlib.Path = _PROJECT_ROOT / "tests" / "gates"

# Target-like pattern: lowercase words with hyphens, at least 2 chars
_TARGET_PATTERN: re.Pattern = re.compile(r"^[a-z][a-z0-9-]+$")

# Known non-target set names that are allowed (not make targets)
_ALLOWED_NON_TARGET_SETS: set[str] = {
    "_DEPRECATED_PATTERNS",
    "_EXCLUDED_DIRS",
    "_SCAN_EXTENSIONS",
    "_EXCEPTION_FILES",
    "_SHEBANG_EXCEPTION_PATTERNS",
    "_EXCLUDE_DIRS",
    "_SCAN_SPECIFIC",
    "_CONVENIENCE_TARGETS",
    "_MODULE_SCOPED_VERBS",
    "env_dependent",
    "required_fields",  # test_gate_deploy_paths.py:151 — field validation set, not make targets
    "_CRITICAL_15S",  # test_gate_healthcheck_intervals.py — healthcheck interval классы (D4), не make targets
    "_SERVICES_30S",  # test_gate_healthcheck_intervals.py — healthcheck interval классы (D4), не make targets
    "_BACKGROUND_60S",  # test_gate_healthcheck_intervals.py — healthcheck interval классы (D4), не make targets
    "_DOCKER_SSH_MARKERS",  # test_gate_timeout_literals.py — docker/ssh/healthcheck домен-маркеры, не make targets
    "_WORKFLOW_ALLOWED_VERBS",  # test_gate_deploy_channel.py — forced-command verbs CI-канала {ping,receive,verify}
    #   (DevPlan 116 B1 T10). Это SSH-verbs, НЕ make-таргеты; authoritative source — CANONICAL_VERBS
    #   из core/internal/shared/verbs.py (подмножество канала). В entrypoint-manifest.yaml verb-словарь
    #   forced-command не хранится (он в shared/verbs.py), поэтому чтение из манифеста невозможно.
    "_STATEFUL_MODULES",  # test_gate_make_contract.py — D1-матрица stateful-модулей (postgres/backup-cron/hermes-agent,
    #   DevPlan 116 B7). Это ИМЕНА МОДУЛЕЙ, не make-таргеты. Authoritative source — DevPlan/AGENTS.md (контракт D1);
    #   в entrypoint-manifest.yaml stateful-маркировки нет, поэтому чтение из манифеста невозможно.
}

logger = logging.getLogger(__name__)


def _is_target_set(elements: list[str]) -> bool:
    """Check if a list of string elements looks like a set of make target names.

    ## @purpose — Heuristic: if 3+ elements match the target pattern, it's likely a target set.
    ## @io — ⇥ elements: list[str] → ⎋ bool
    ## @complexity — O(N) where N = number of elements
    """
    if len(elements) < 3:
        return False
    target_count = sum(1 for e in elements if _TARGET_PATTERN.match(e))
    return target_count >= 3


def _scan_file_for_hardcoded_sets(filepath: pathlib.Path) -> list[tuple[str, int, str]]:
    """Scan a Python file for hardcoded set literals containing target names.

    ## @purpose — Parse the AST of a gate test file and find Set nodes whose
    ##            elements are all string literals matching the target pattern.
    ## @io — ⇥ filepath: Path → ⎋ list of (filename, lineno, set_contents)
    ## @complexity — O(N) where N = lines in file
    """
    violations: list[tuple[str, int, str]] = []
    try:
        with open(filepath) as f:
            tree = ast.parse(f.read())
    except SyntaxError:
        logger.warning("[IMP:4][_scan_file] Syntax error parsing %s", filepath.name)
        return violations

    def _is_allowed_target_set(node: ast.AST, tree: ast.AST) -> bool:
        """Check if a set literal/call is assigned to an allowed non-target variable.

        Handles both regular assignments (x = ...) and annotated assignments
        (x: set[str] = ...).
        """
        for parent in ast.walk(tree):
            # Regular assignment: x = {targets}
            if isinstance(parent, ast.Assign):
                for target in parent.targets:
                    if isinstance(target, ast.Name) and target.id in _ALLOWED_NON_TARGET_SETS and parent.value is node:
                        return True
            # Annotated assignment: x: set[str] = {targets}
            if (
                isinstance(parent, ast.AnnAssign)
                and isinstance(parent.target, ast.Name)
                and parent.target.id in _ALLOWED_NON_TARGET_SETS
                and parent.value is node
            ):
                return True
        return False

    for node in ast.walk(tree):
        # Look for set(...) calls
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "set":
            # Check if argument is a list/tuple of string literals
            if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
                elements = [
                    elt.value
                    for elt in node.args[0].elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
                if _is_target_set(elements) and not _is_allowed_target_set(node, tree):
                    violations.append(
                        (
                            filepath.name,
                            node.lineno,
                            f"set({sorted(elements)})",
                        )
                    )

        # Look for {...} set literals (not dicts)
        elif isinstance(node, ast.Set):
            elements = []
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    elements.append(elt.value)
            if _is_target_set(elements) and not _is_allowed_target_set(node, tree):
                violations.append(
                    (
                        filepath.name,
                        node.lineno,
                        f"{{{', '.join(sorted(elements))}}}",
                    )
                )

    return violations


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-10 · gate/exception-audit · G1.2 hardcoded target set detection
def test_no_hardcoded_target_sets_in_gates(caplog) -> None:
    """Verify no gate test file contains hardcoded sets of make target names.

    ## @purpose — G1.2 anti-regression gate: scans all tests/gates/test_gate_*.py
    ##            for set literals containing 3+ target-like string elements.
    ##            All such sets should be read from core/entrypoint-manifest.yaml
    ##            instead.
    ## @io — ⎋ None (assert side-effect via pytest.fail on violations)
    ## @complexity — O(F * N) where F = gate files, N = AST nodes per file
    """
    logger.info("[IMP:8][test_no_hardcoded_target_sets_in_gates] Scanning gate files for hardcoded target sets")

    gate_files = sorted(_GATES_DIR.glob("test_gate_*.py"))
    logger.info("[IMP:8][test_no_hardcoded_target_sets_in_gates] Found %d gate files to scan", len(gate_files))

    all_violations: list[tuple[str, int, str]] = []
    for gf in gate_files:
        violations = _scan_file_for_hardcoded_sets(gf)
        if violations:
            logger.warning(
                "[IMP:7][test_no_hardcoded_target_sets_in_gates] %s: %d violation(s)", gf.name, len(violations)
            )
            all_violations.extend(violations)
        else:
            logger.info("[IMP:9][test_no_hardcoded_target_sets_in_gates] CLEAN: %s", gf.name)

    assert not all_violations, (
        f"Hardcoded target sets found in {len(all_violations)} location(s):\n"
        + "\n".join(f"  {fname}:{lineno} — {content}" for fname, lineno, content in all_violations)
        + "\n\nAll make target sets should be read from core/entrypoint-manifest.yaml"
    )

    logger.info(
        "[IMP:9][test_no_hardcoded_target_sets_in_gates] ALL PASS — no hardcoded target sets in %d gate files",
        len(gate_files),
    )
