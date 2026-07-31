# GREP_SUMMARY: gate linter-parity namelint make-target manifest G1.3 anti-drift facade-delegation
# STRUCTURE: ▶ facade delegation (lint.sh→python3 -m) → ▶ validate_make_target_names(real repo) →
#            ▶ _make_temp_lint_repo (manifest symlink + Makefile + makefiles/*.mk) → validate_make_target_names(temp) ∥
#            _run_python_linter (spec oracle) → ◇ diff(module, oracle, expected) → ⟦assert no diff⟧
# region MODULE_CONTRACT
## @purpose — Regression test: verify the namelint contract — make-target names from Makefile +
##            makefiles/*.mk validated against entrypoint-manifest.yaml by the unified Python
##            implementation (core.internal.lint.doc_header_validator.validate_make_target_names,
##            DevPlan 106). lint.sh namelint is a thin facade delegating to the SAME module → the
##            pre-commit hook and CI gate give identical feedback by construction (G1.3).
## @scope — (1) facade parity: lint.sh delegates namelint to the module under test; (2) production
##          contract: real repo targets all pass against the manifest; (3) classification parity:
##          temp repo root with curated .PHONY targets split across Makefile and makefiles/*.mk →
##          module output equals an independent spec oracle on every target category.
## @invariants
##   - Module output must equal the spec oracle on every target category: forbidden/unknown → FAIL,
##     allowed/module_lifecycle/system-exception/system-prefix → pass
##   - Temp repo is self-contained: manifest symlink + Makefile + makefiles/*.mk. A Makefile outside
##     the repo root is NOT validated — Python parses repo_root/Makefile + makefiles/*.mk (documented
##     DevPlan 106 behavior, QA P2 root cause)
## @rationale — After DevPlan 106, bash awk and Python YAML linters are ONE implementation
##              (Strangler-Fig). The parity test is reoriented: compare the production implementation
##              against an independent spec oracle instead of comparing two now-identical code paths.
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest
import yaml

from core.internal.lint.doc_header_validator import validate_make_target_names
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_MANIFEST_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "entrypoint-manifest.yaml"
_LINT_SH_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "entrypoints" / "lint.sh"


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


def _make_temp_lint_repo(tmp_path: pathlib.Path, targets: list[str]) -> pathlib.Path:
    """Create a temp repo root: manifest symlink + Makefile + makefiles/*.mk with .PHONY targets.

    ## @purpose — Build a self-contained repo root for validate_make_target_names: manifest is
    ##            symlinked (same Source of Truth as production), targets are split between the
    ##            root Makefile and makefiles/*.mk to verify BOTH are scanned (lint.sh:114-126 port).
    ## @io — ⇥ tmp_path: Path, targets: list[str] → ⎋ Path to temp repo root
    ## @complexity — O(N) where N = number of targets
    """
    temp_root = tmp_path / "repo"
    (temp_root / "core").mkdir(parents=True)
    manifest_link = temp_root / "core" / "entrypoint-manifest.yaml"
    manifest_link.symlink_to(_MANIFEST_PATH)

    half = len(targets) // 2
    (temp_root / "Makefile").write_text(f".PHONY: {' '.join(targets[:half])}\n")
    mk_dir = temp_root / "makefiles"
    mk_dir.mkdir()
    (mk_dir / "split.mk").write_text(f".PHONY: {' '.join(targets[half:])}\n")
    logger.info(
        "[IMP:8][_make_temp_lint_repo] temp repo: %d target(s) across Makefile + makefiles/split.mk",
        len(targets),
    )
    return temp_root


def _flagged_targets(errors: list[str]) -> set[str]:
    """Extract FAIL target names from validate_make_target_names error messages.

    ## @purpose — Parse "[FAIL] Target '<name>' ..." messages → set of flagged names.
    ## @io — ⇥ errors: list[str] → ⎋ set[str] of flagged target names
    ## @complexity — O(E) where E = number of errors
    """
    flagged: set[str] = set()
    for err in errors:
        m = re.search(r"Target '([^']+)'", err)
        if m:
            flagged.add(m.group(1))
    return flagged


def _run_python_linter(test_targets: dict[str, str]) -> list[str]:
    """Run the independent spec oracle and return list of FAIL targets.

    ## @purpose — Independent re-implementation of the namelint spec (manifest data only): forbidden
    ##            → FAIL; allowed/module_lifecycle/system_exceptions/system_prefixes → pass; else FAIL.
    ##            Serves as the reference oracle for parity against validate_make_target_names.
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
        "[IMP:8][_run_python_linter] Spec oracle: %d FAIL out of %d targets", len(failed_targets), len(test_targets)
    )
    return failed_targets


# ── Tests ──


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · DevPlan 106: namelint moved from lint.sh (awk) to
#   doc_header_validator.validate_make_target_names — bash facade now exec's the SAME Python module,
#   so bash-vs-python comparison collapsed (DRIFT-GATE-2, QA P2). Temp Makefile was ignored because
#   Python parses repo_root/Makefile + makefiles/*.mk, not the test's temp path.
# · Scenario: (1) lint.sh facade delegates namelint to the module under test; (2) real repo → PASS
# ·   (production contract); (3) temp repo root (manifest symlink + curated .PHONY across Makefile and
# ·   makefiles/*.mk) → module flags exactly forbidden+unknown targets and passes
# ·   allowed/lifecycle/system-exception/system-prefix targets — parity with the independent spec oracle
# · Last fail: 2026-07-31 — "Bash linter missed expected FAIL targets: [deploy-node, foobar, push-core, random-task]"
# · Remove if: namelint moves out of doc_header_validator (re-point import + facade delegation assertion)
def test_linter_parity(caplog, tmp_path) -> None:
    """Validate namelint contract: module output equals spec oracle on every target category.

    ## @purpose — G1.3 regression gate: after DevPlan 106 the bash (lint.sh namelint) and Python
    ##            name-linter are one implementation. Parity is now asserted between the production
    ##            implementation (validate_make_target_names) and an independent spec oracle on a
    ##            curated target catalog, plus the facade-delegation contract that lint.sh runs the
    ##            exact tested module.
    ## @io — ⎋ None (assert side-effect via pytest.fail on diff)
    ## @complexity — O(N) where N = test targets
    """
    logger.info("[IMP:8][test_linter_parity] === namelint contract + spec oracle parity check ===")

    # 1. Facade parity — lint.sh namelint must delegate to the exact module under test
    lint_sh = _LINT_SH_PATH.read_text()
    delegation = "python3 -m core.internal.lint.doc_header_validator namelint"
    assert delegation in lint_sh, f"lint.sh must delegate namelint to doc_header_validator (missing: {delegation!r})"
    logger.info("[IMP:9][test_linter_parity] OK: lint.sh namelint delegates via %r", delegation)

    # 2. Production contract — real repo .PHONY targets all valid against the manifest
    real_errors = validate_make_target_names(_PROJECT_ROOT)
    assert real_errors == [], f"real repo namelint must PASS against manifest: {real_errors}"
    logger.info("[IMP:9][test_linter_parity] OK: real repo namelint PASS (0 errors)")

    # 3. Classification parity — temp repo (Makefile + makefiles/*.mk), curated catalog
    target_names = sorted(_TEST_TARGETS.keys())
    temp_root = _make_temp_lint_repo(tmp_path, target_names)
    module_fails = _flagged_targets(validate_make_target_names(temp_root))
    oracle_fails = set(_run_python_linter(_TEST_TARGETS))
    expected_fails = {t for t, v in _TEST_TARGETS.items() if v == "fail"}

    errors: list[str] = []
    if module_fails != expected_fails:
        errors.append(
            f"Module linter result differs from expected: module={sorted(module_fails)} expected={sorted(expected_fails)}"
        )
    if oracle_fails != expected_fails:
        errors.append(
            f"Spec oracle result differs from expected: oracle={sorted(oracle_fails)} expected={sorted(expected_fails)}"
        )
    if module_fails != oracle_fails:
        errors.append(
            f"Parity violation — module vs spec oracle disagree: "
            f"module={sorted(module_fails)} oracle={sorted(oracle_fails)}"
        )

    if errors:
        logger.error("[IMP:9][test_linter_parity] FAIL: %d disagreement(s)", len(errors))
    else:
        logger.info(
            "[IMP:9][test_linter_parity] ALL PASS — module==oracle==expected on %d targets (FAIL: %s)",
            len(target_names),
            sorted(module_fails),
        )

    assert not errors, "\n".join(errors)
