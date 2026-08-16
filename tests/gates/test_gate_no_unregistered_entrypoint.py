# GREP_SUMMARY: gate anti-drift entrypoint-manifest shebang-validation makefile-targets unregistered CI TASK-5G1
# STRUCTURE: ▶ Load manifest → ⊕ manifest_paths ∪ exception_patterns ⚡ glob(core/**/*.sh) → ○ for each shebang: ◇ in_exception? → ⊗ in_manifest? → ⎋ PASS|FAIL-fast ‖ ▷ glob(core/modules/*/Makefile) → extract real targets → ○ each: ◇ in_allowed_verbs? → ◇ lifecycle_exception? → ⎋ PASS|FAIL-fast

# region MODULE_CONTRACT
## @purpose  Anti-drift CI gate (TASK-5G1): validates that every shebang script under core/
##            is registered in core/entrypoint-manifest.yaml or is a documented exception;
##            every module Makefile target uses an allowed verb.
##            (forbidden-тройка упразднена DevPlan 171 W3.3 — namelint покрывает новые таргеты)
## @scope    All .sh shebang files under core/ (excluding node_modules, .venv, __pycache__),
##           all core/modules/*/Makefile targets.
## @invariants
##   - Every shebang script in core/entrypoints/ and core/internal/ must appear in manifest delegates_to
##   - core/lib/*.sh — documented exception (library files, not entrypoints)
##   - core/modules/*/healthcheck.sh — documented exception (module healthchecks)
##   - core/modules/*/install.sh — documented exception (module installers)
##   - core/bootstrap/systemd/*.sh — documented exception (system service files)
##   - core/modules/hermes-agent/build/scripts/*.sh — documented exception (s6 overlay)
##   - core/modules/hermes-agent/context/scripts/*.sh — documented exception (context init)
##   - Every module Makefile target must be in allowed_verbs or lifecycle exceptions (start, stop, restart, status, logs)
##   - Fail-fast on first violation — pytest.fail() with diagnostic message
## @rationale Prevents drift between the canonical operations registry
##            (entrypoint-manifest.yaml) and the actual filesystem. Without this gate,
##            new entrypoints or Makefile targets can be created without updating the
##            manifest, breaking CI gates that rely on manifest parity.
##            The fail-fast approach ensures the FIRST unregistered item is fixed,
##            not buried in a long error list (TASK-5G1).
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re

import pytest
import yaml

from tests.conftest import ldd_trajectory
from tests.helpers.makefile_parser import extract_makefile_targets

logger = logging.getLogger(__name__)

# ── Paths relative to project root ──
_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_MANIFEST_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "entrypoint-manifest.yaml"
_CORE_DIR: pathlib.Path = _PROJECT_ROOT / "core"

# Структурные зоны shebang-скана (DevPlan 171 W3.4): регистрация в манифесте ТОЛЬКО
# для канонических entrypoints (core/entrypoints/), pre-commit hooks (core/internal/hooks/)
# и .github. Файлы вне этих зон НЕ требуют регистрации (категорийное правило
# «расположение > перечень», замена _SHEBANG_EXCEPTION_PATTERNS + _EXCLUDE_DIRS).
_SCAN_ROOTS: tuple[str, ...] = ("core/entrypoints", "core/internal/hooks", ".github")

# Module lifecycle targets are now read from manifest `module_lifecycle` section.
# See _load_module_lifecycle() below.
# This replaces the old _MAKEFILE_LIFECYCLE_EXCEPTIONS hardcoded set (G1.2).


# region HELPERS


def _extract_manifest_script_paths(manifest: dict) -> set[str]:
    """Extract all ``core/…file.sh`` paths from ``delegates_to`` fields in the manifest.

    ## @purpose  Parse the YAML manifest and collect every registered script path
    ##            that appears in any ``delegates_to`` string. Paths may be chained
    ##            with "→" (e.g. "A.sh → B.sh → C.sh").
    ## @io        ⇥ manifest: dict parsed from entrypoint-manifest.yaml
    ##            ⎋ set[str] of unique relative script paths
    ## @complexity  O(N × M) where N = manifest group count, M = paths per delegates_to
    ## @invariants
    ##   - Skips non-list groups (allowed_verbs)
    ##   - Paths match the regex ``core/[\\w./-]+\\.sh``
    ##   - Relative paths are stored as-is (e.g. ``core/entrypoints/deploy.sh``)
    ##   - Duplicates are collapsed by the set
    """
    paths: set[str] = set()
    path_re = re.compile(r"core/[\w./-]+\.sh")

    for group, entries in manifest.items():
        if not isinstance(entries, list):
            continue
        if group in {"allowed_verbs"}:
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            delegates_to = entry.get("delegates_to", "")
            if not isinstance(delegates_to, str):
                continue
            for match in path_re.finditer(delegates_to):
                paths.add(match.group())

    logger.info("[IMP:8][GATE1][extract] Extracted %d script paths from manifest", len(paths))
    return paths


def _is_shebang_file(filepath: str | os.PathLike) -> bool:
    """Check whether *filepath* starts with a Unix shebang (``#!``).

    ## @io    ⇥ filepath → ⎋ bool
    ## @complexity  O(1) — reads only the first byte
    ## @invariants
    ##   - OSError (permission, binary decode) → returns False
    """
    try:
        with pathlib.Path(filepath).open("rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def _load_module_lifecycle() -> set[str]:
    """Read module_lifecycle targets from the manifest YAML.

    ## @purpose — Load the single source of truth for module lifecycle targets
    ##            from core/entrypoint-manifest.yaml. Replaces the old
    ##            _MAKEFILE_LIFECYCLE_EXCEPTIONS hardcoded set (G1.2).
    ## @io — ⎋ set[str] of lifecycle target names
    ## @complexity — O(1) — single file read + YAML parse
    ## @invariants
    ##   - Returns empty set if section is missing or malformed
    ##   - All module lifecycle targets are defined in a flat list under module_lifecycle
    """
    with pathlib.Path(_MANIFEST_PATH).open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    targets: list[str] = manifest.get("module_lifecycle", [])
    result: set[str] = set(targets)
    logger.info("[IMP:8][_load_module_lifecycle] Loaded %d targets from manifest module_lifecycle", len(result))
    return result


def _collect_sh_files() -> list[str]:
    """Return sorted list of ``.sh`` paths inside structural scan roots (DevPlan 171 W3.4).

    ## @io        ⎋ list[str] — paths relative to _PROJECT_ROOT
    ## @complexity  O(F) where F = number of .sh files in scan roots
    ## @invariants — только _SCAN_ROOTS; файлы вне зон не требуют регистрации
    """
    result: list[str] = []
    for root_rel in _SCAN_ROOTS:
        root = _PROJECT_ROOT / root_rel
        if not root.is_dir():
            continue
        for sh_path in sorted(root.rglob("*.sh")):
            if "__pycache__" in sh_path.parts:
                continue
            result.append(os.path.relpath(str(sh_path), str(_PROJECT_ROOT)))
    return result


# endregion HELPERS


# region TEST_ALL_SHEBANG_FILES_IN_MANIFEST
## @purpose  Verify every shebang script in structural scan roots (core/entrypoints/,
##           core/internal/hooks/, .github) is registered in entrypoint-manifest.yaml
##           delegates_to. Файлы вне зон скана не требуют регистрации (DevPlan 171 W3.4).
## @rationale  Prevents new ad-hoc entrypoints/hooks from being added to the codebase
##             without updating the canonical operations registry.


# 🧪 TRAP[TEST] · REGRESSION(GATE-5G1) · SCENARIO(shebang-manifest-parity) · LAST_FAIL(unregistered entrypoint) · REMOVE_IF(all shebangs registered)
@pytest.mark.gate
@ldd_trajectory
def test_all_shebang_files_in_manifest(caplog) -> None:
    """Core check: every shebang file in scan roots must be in manifest delegates_to."""

    # 1. Load manifest
    assert _MANIFEST_PATH.is_file(), f"Manifest not found: {_MANIFEST_PATH}"
    with pathlib.Path(_MANIFEST_PATH).open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    logger.info("[IMP:8][GATE1][shebang] Loaded manifest: %s", _MANIFEST_PATH)

    registered_paths = _extract_manifest_script_paths(manifest)
    logger.info("[IMP:8][GATE1][shebang] %d registered paths", len(registered_paths))

    # 2. Collect .sh files from structural scan roots
    all_sh = _collect_sh_files()
    logger.info("[IMP:8][GATE1][shebang] Found %d .sh files in scan roots %s", len(all_sh), _SCAN_ROOTS)

    # 3. Validate each shebang file
    n_ok: int = 0

    logger.info("[IMP:9][GATE1][shebang] Starting per-file validation — first FAIL stops")

    for rel_path in all_sh:
        abs_path = _PROJECT_ROOT / rel_path
        if not _is_shebang_file(abs_path):
            logger.info("[IMP:7][GATE1][shebang] Skip non-shebang: %s", rel_path)
            continue

        # (a) Check if path is registered in manifest delegates_to
        if rel_path in registered_paths:
            logger.info("[IMP:7][GATE1][shebang] Registered: %s", rel_path)
            n_ok += 1
            continue

        # (b) Fail-fast — first unregistered script
        logger.error("[IMP:9][GATE1][shebang] FAIL: Unregistered script '%s'", rel_path)
        _print_registration_summary(registered_paths)
        pytest.fail(
            f"Unregistered shebang script detected: '{rel_path}'\n"
            f"\n"
            f"This script exists in a scan root ({', '.join(_SCAN_ROOTS)}) but is NOT\n"
            f"listed in manifest delegates_to paths.\n\n"
            f"Required action:\n"
            f"  Add '{rel_path}' to core/entrypoint-manifest.yaml as a delegates_to\n"
            f"  for the appropriate make_target / script entry.\n"
            f"\n"
            f"Fail-fast: fix this script first, then re-run to find others."
        )

    logger.info("[IMP:9][GATE1][shebang] ALL PASS: %d registered", n_ok)


def _print_registration_summary(registered_paths: set[str]) -> None:
    """Print diagnostic summary of registered manifest paths on test failure."""
    print("\n--- Registered manifest delegates_to paths ---", flush=True)
    for p in sorted(registered_paths):
        print(f"  {p}", flush=True)
    print(f"  ({len(registered_paths)} total)", flush=True)
    print(f"--- Scan roots: {', '.join(_SCAN_ROOTS)} ---", flush=True)
    print("--- End ---", flush=True)


# endregion TEST_ALL_SHEBANG_FILES_IN_MANIFEST


# region TEST_ALL_MAKEFILE_TARGETS_IN_ALLOWED_VERBS
## @purpose  Verify every target declared in a module Makefile uses an allowed verb
##           from the manifest or is a documented lifecycle exception.
## @rationale  Makefiles must not introduce ad-hoc verbs that bypass the canonical
##             operations dictionary. Target → verb consistency ensures all operations
##             are traceable in the manifest and CI gates.


# 🧪 TRAP[TEST] · REGRESSION(GATE-5G1) · SCENARIO(makefile-verb-parity) · LAST_FAIL(unregistered make target) · REMOVE_IF(all targets registered in allowed_verbs)
@pytest.mark.gate
@ldd_trajectory
def test_all_makefile_targets_in_allowed_verbs(caplog) -> None:
    """Validate every module Makefile target against manifest allowed_verbs + lifecycle exceptions."""

    # 1. Load allowed_verbs from manifest
    with pathlib.Path(_MANIFEST_PATH).open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    allowed_verbs: set[str] = set(manifest.get("allowed_verbs", []))
    module_lifecycle: set[str] = _load_module_lifecycle()
    all_allowed: set[str] = allowed_verbs | module_lifecycle

    logger.info(
        "[IMP:8][GATE1][makefile] Loaded %d allowed_verbs + %d module_lifecycle = %d total",
        len(allowed_verbs),
        len(module_lifecycle),
        len(all_allowed),
    )

    # 2. Glob module Makefiles
    makefiles = sorted(_PROJECT_ROOT.glob("core/modules/*/Makefile"))
    logger.info("[IMP:8][GATE1][makefile] Found %d module Makefiles", len(makefiles))

    logger.info("[IMP:9][GATE1][makefile] Starting per-Makefile target validation — first FAIL stops")

    for mf in makefiles:
        targets = extract_makefile_targets(str(mf))
        rel_mf = os.path.relpath(str(mf), str(_PROJECT_ROOT))

        if not targets:
            logger.info("[IMP:7][GATE1][makefile] %s: no (non-.PHONY) targets", rel_mf)
            continue

        logger.info("[IMP:7][GATE1][makefile] %s: targets %s", rel_mf, targets)

        for tgt in targets:
            if tgt in all_allowed:
                logger.info("[IMP:7][GATE1][makefile] OK: %-20s → %s", tgt, rel_mf)
            else:
                logger.error("[IMP:9][GATE1][makefile] FAIL: unregistered target '%s' in %s", tgt, rel_mf)
                _print_target_summary(tgt, allowed_verbs, module_lifecycle, rel_mf)
                pytest.fail(
                    f"Unregistered Makefile target: '{tgt}'\n"
                    f"  Makefile: {rel_mf}\n"
                    f"\n"
                    f"Target '{tgt}' is not in:\n"
                    f"  - manifest allowed_verbs ({len(allowed_verbs)} verbs)\n"
                    f"  - manifest module_lifecycle ({len(module_lifecycle)} targets)\n"
                    f"\n"
                    f"Required action:\n"
                    f"  1. Rename the target to an allowed verb, OR\n"
                    f"  2. Add '{tgt}' to allowed_verbs in entrypoint-manifest.yaml, OR\n"
                    f"  3. Add '{tgt}' to module_lifecycle in entrypoint-manifest.yaml "
                    f"(only for module lifecycle targets).\n"
                    f"\n"
                    f"Fail-fast: fix this target first, then re-run."
                )

    logger.info("[IMP:9][GATE1][makefile] ALL PASS: %d Makefiles checked", len(makefiles))


def _print_target_summary(tgt: str, allowed_verbs: set[str], module_lifecycle: set[str], makefile_rel: str) -> None:
    """Print diagnostic summary on Makefile target failure."""
    print(f"\n--- Target '{tgt}' from {makefile_rel} is unregistered ---", flush=True)
    print("  Allowed verbs:", sorted(allowed_verbs), flush=True)
    print("  Module lifecycle:", sorted(module_lifecycle), flush=True)
    print("  Combined allowed:", sorted(allowed_verbs | module_lifecycle), flush=True)
    print("--- End ---", flush=True)


# endregion TEST_ALL_MAKEFILE_TARGETS_IN_ALLOWED_VERBS


# region TEST_NO_SSL_PROVISION_EXCEPTION
## @purpose  R5-negative + зональный контракт (DevPlan 171 W3.4): (1) инжекция
##           незарегистрированного shebang-скрипта в core/entrypoints/ детектируется;
##           (2) файлы вне зон скана не требуют регистрации (бывший ssl-provision
##           exception-класс теперь покрыт категорийным правилом).


# 🧪 TRAP[TEST] · R5-negative · 164-W3-1 · unregistered .sh injected into core/entrypoints/ is caught
# · Original form: deploy.sh воссоздан как незарегистрированный entrypoint → RED
# ·   (ssl-provision-класс: файл удалён, но оставался в exception-списке — мёртвая документация)
# · Scenario: probe-файл core/entrypoints/_gate_probe_<uuid>.sh с shebang → попадает
# ·   в _collect_sh_files() и НЕ в registered_paths → детектор обязан поймать.
@pytest.mark.gate
@ldd_trajectory
def test_unregistered_entrypoint_injection_detected(caplog) -> None:
    """R5-negative: shebang-скрипт, инжектированный в core/entrypoints/, детектируется."""
    import uuid

    with pathlib.Path(_MANIFEST_PATH).open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    registered_paths = _extract_manifest_script_paths(manifest)

    probe_name = f"_gate_probe_{uuid.uuid4().hex[:8]}.sh"
    probe_path = _PROJECT_ROOT / "core" / "entrypoints" / probe_name
    try:
        probe_path.write_text("#!/usr/bin/env bash\necho probe\n", encoding="utf-8")

        collected = _collect_sh_files()
        probe_rel = os.path.relpath(str(probe_path), str(_PROJECT_ROOT))
        assert probe_rel in collected, f"R5 FAIL: probe {probe_rel} not collected by scan roots"
        assert probe_rel not in registered_paths, "R5 precondition: probe must be unregistered"
        logger.critical(
            "[IMP:9][GATE1][r5] R5-negative OK: injected unregistered entrypoint '%s' IS collected and unregistered",
            probe_rel,
        )
    finally:
        probe_path.unlink(missing_ok=True)


@pytest.mark.gate
@ldd_trajectory
def test_files_outside_scan_roots_do_not_require_registration(caplog) -> None:
    """Зональный контракт (DevPlan 171 W3.4): файлы вне _SCAN_ROOTS не требуют регистрации."""
    # core/lib/*.sh — вне зон скана (фасад-библиотеки); в манифесте их может не быть.
    outside_examples = [p for p in ("core/lib/logging.sh", "core/lib/ssh.sh") if (_PROJECT_ROOT / p).is_file()]
    if not outside_examples:
        pytest.skip("No core/lib/*.sh files present to verify zone contract")

    collected = _collect_sh_files()
    for rel in outside_examples:
        assert rel not in collected, (
            f"ZONE_CONTRACT FAIL: '{rel}' is outside _SCAN_ROOTS but was collected — "
            f"files outside scan roots must not require registration"
        )
    logger.critical(
        "[IMP:9][GATE1][zone] Zone contract OK: %d core/lib/*.sh outside scan roots not collected",
        len(outside_examples),
    )


# endregion TEST_NO_SSL_PROVISION_EXCEPTION
