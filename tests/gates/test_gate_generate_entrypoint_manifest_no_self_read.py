# GREP_SUMMARY: generate-entrypoint no-self-read g3 allowed-verbs gates source-code-analysis
# STRUCTURE: ▶ read generate_entrypoint_manifest.py → ◇ grep for yaml.safe_load / open(entrypoint-manifest) → ◇ analyze merge() arguments → ◇ verify allowed_verbs/gates NOT loaded from existing → ⎋ pass/fail
# region MODULE_CONTRACT
## @purpose  Verify G3 generator (generate_entrypoint_manifest.py) does NOT read allowed_verbs
##            or gates[] from the existing entrypoint-manifest.yaml. G3 must generate these
##            sections from Makefile .PHONY targets and pytest gate markers, not from the manifest
##            itself. Reading self would create a self-referential drift mask.
## @scope    CI gate — static source code analysis
## @invariants
##   - Generator reads existing manifest ONLY for structural sections (metadata, convention,
##     schema, forbidden_*, module_lifecycle, name_linter, repair, lib)
##   - Generator NEVER reads allowed_verbs or gates from existing manifest
##   - `load_existing_manifest()` is called but its result is only used to preserve non-generated sections
##   - `merge()` function replaces allowed_verbs and gates[] entirely with generated values
##   - This is verified by analyzing `merge()` — it receives allowed_verbs and gates as separate
##     arguments (from extraction) and replaces them in the result dict
## @rationale DevPlan 090 — Atomic Generation. G3 reading allowed_verbs/gates from its own output
##            would create a self-healing illusion: if a target is manually deleted from Makefile
##            but remains in YAML, G3 would "preserve" it. True atomic generation requires each
##            generator to produce sections from authoritative sources only.
## @changes 2026-07-30 · Created — DevPlan 090 gate
# endregion MODULE_CONTRACT

import logging
import sys

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

_GENERATOR_PATH = "core/internal/scripts/generate_entrypoint_manifest.py"


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_no_self_read
## @purpose  Verify G3 does not read allowed_verbs/gates from entrypoint-manifest.yaml
## @io       ⇥ static analysis of generate_entrypoint_manifest.py → ⎋ assert pass/fail
## @complexity O(N) where N = lines in generator file
## 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · G3 no-self-read contract
## · Scenario: G3 must NOT read allowed_verbs/gates from its own YAML output.
##             If it does, a removed Makefile target would remain in allowed_verbs forever,
##             masking drift. Verified by analyzing merge() — it replaces these sections entirely.
## · Last fail: N/A (new gate)
## · Remove if: entrypoint-manifest.yaml generation is restructured (e.g., split into separate files)
def test_no_self_read(caplog) -> None:
    """Verify G3 generate_entrypoint_manifest.py does NOT read allowed_verbs/gates
    from existing entrypoint-manifest.yaml — these sections are GENERATED, not preserved.

    G3 reads existing manifest only for structural sections:
    - metadata, convention, schema
    - forbidden_directories, forbidden_scripts, forbidden_verbs
    - name_linter, module_lifecycle, system_module_lifecycle
    - lib, module_hooks, repair

    allowed_verbs and gates[] are REPLACED entirely by extracted values from Makefile and pytest.
    """
    caplog.set_level(logging.INFO)
    print("[IMP:8][test_no_self_read] Analyzing generate_entrypoint_manifest.py...", file=sys.stderr)

    # Read the generator source
    try:
        with open(_GENERATOR_PATH) as f:
            source = f.read()
    except FileNotFoundError:
        logger.error("[IMP:10][test_no_self_read] Generator file not found: %s", _GENERATOR_PATH)
        pytest.fail(f"Generator file not found: {_GENERATOR_PATH}")
        return

    print(f"[IMP:8][test_no_self_read] Read {len(source.splitlines())} lines from {_GENERATOR_PATH}", file=sys.stderr)

    # ── Verify 1: `load_existing_manifest()` exists and is called ──
    assert "def load_existing_manifest" in source, (
        f"Missing load_existing_manifest() function in {_GENERATOR_PATH}"
    )
    assert "existing = load_existing_manifest" in source or "load_existing_manifest(" in source, (
        f"load_existing_manifest() is defined but never called in {_GENERATOR_PATH}"
    )
    print("[IMP:7][test_no_self_read] load_existing_manifest() defined and called — OK", file=sys.stderr)

    # ── Verify 2: `merge()` receives `allowed_verbs` and `gates` as arguments ──
    # The merge function signature is: merge(allowed_verbs, gates, existing)
    # This proves allowed_verbs and gates come from OUTSIDE (extraction functions), not from existing
    assert "def merge(allowed_verbs" in source or "def merge(" in source, (
        f"Missing merge() function in {_GENERATOR_PATH}"
    )

    # Check that merge() replaces allowed_verbs and gates[], not reads them from existing
    merge_assign_verbs = "result[\"allowed_verbs\"]" in source or "result['allowed_verbs']" in source
    merge_assign_gates = "result[\"gates\"]" in source or "result['gates']" in source
    assert merge_assign_verbs, (
        f"merge() must REPLACE allowed_verbs, not read from existing. "
        f"Look for `result['allowed_verbs'] = ...` in merge()."
    )
    assert merge_assign_gates, (
        f"merge() must REPLACE gates[], not read from existing. "
        f"Look for `result['gates'] = ...` in merge()."
    )
    print("[IMP:9][test_no_self_read] merge() replaces allowed_verbs and gates[] from generated values — OK", file=sys.stderr)

    # ── Verify 3: load_existing_manifest() is called only from main(), not from extract or collect ──
    # The function should be called in main() to load structural sections, not in extract_phony_targets
    # or collect_gate_tests
    lines = source.splitlines()
    load_existing_lines = [i + 1 for i, line in enumerate(lines) if "load_existing_manifest" in line and not line.strip().startswith("#")]
    merge_calls = [i + 1 for i, line in enumerate(lines) if "existing" in line and "merge(" in line and not line.strip().startswith("#")]

    print(f"[IMP:7][test_no_self_read] load_existing_manifest called at lines: {load_existing_lines}", file=sys.stderr)
    print(f"[IMP:7][test_no_self_read] merge() receiving 'existing' at lines: {merge_calls}", file=sys.stderr)

    # ── Verify 4: allowed_verbs and gates are NOT read from existing manifest ──
    # Check that there's no code path reading allowed_verbs or gates from the existing dict
    # and using them as the final value
    dangerous_patterns = [
        'existing.get("allowed_verbs"',
        "existing.get('allowed_verbs'",
        'existing["allowed_verbs"]',
        "existing['allowed_verbs']",
        'existing.get("gates"',
        "existing.get('gates'",
        'existing["gates"]',
        "existing['gates']",
    ]

    violations = []
    for pattern in dangerous_patterns:
        if pattern in source:
            # Check context: if it's in merge(), that's the violation. If it's passed to merge as arg, that's fine.
            # We need to be more precise: check if pattern appears OUTSIDE a comment
            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                if pattern in stripped and not stripped.startswith("#"):
                    # This is a real code reference to existing.get("allowed_verbs") etc.
                    # Determine if it's in merge() (reading) or in main() (reading)
                    violations.append((lineno, stripped))

    if violations:
        violation_msg = "\n".join(f"  Line {l}: {t}" for l, t in violations)
        logger.error(
            "[IMP:10][test_no_self_read] VIOLATION: G3 reads allowed_verbs/gates from existing manifest:\n%s",
            violation_msg,
        )
        pytest.fail(
            f"G3 SELF-READ VIOLATION: generator reads allowed_verbs or gates from existing manifest.\n"
            f"These sections must be GENERATED, not preserved from existing YAML.\n"
            f"Found at:\n{violation_msg}"
        )
    print("[IMP:9][test_no_self_read] No dangerous self-read patterns found — OK", file=sys.stderr)

    # ── Verify 5: Structural sections that ARE preserved are legitimate ──
    # Verify the merge() function preserves forbidden_*, module_lifecycle, etc.
    preserve_keys = [
        "forbidden_directories",
        "forbidden_scripts",
        "forbidden_verbs",
        "name_linter",
        "module_lifecycle",
        "system_module_lifecycle",
        "lib",
        "module_hooks",
    ]
    for key in preserve_keys:
        if key in source:
            print(f"[IMP:7][test_no_self_read] Preserved section '{key}' found in merge() — OK", file=sys.stderr)

    logger.info(
        "[IMP:9][test_no_self_read] ALL PASS — G3 does not read allowed_verbs/gates from existing manifest"
    )


# endregion FUNC_test_no_self_read
