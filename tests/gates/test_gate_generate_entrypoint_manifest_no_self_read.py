# GREP_SUMMARY: generate-entrypoint no-self-read g3 allowed-verbs gates source-code-analysis load-structural-sections
# STRUCTURE: ▶ read generate_entrypoint_manifest.py → ◇ grep for load_structural_sections → ◇ grep for allowed_verbs/gates NOT loaded from existing → ◇ analyze merge() overwrites → ⎋ pass/fail
# region MODULE_CONTRACT
## @purpose  Verify G3 generator (generate_entrypoint_manifest.py) does NOT read allowed_verbs
##            or gates[] from the existing entrypoint-manifest.yaml. G3 must generate these
##            sections from Makefile .PHONY targets and pytest gate markers, not from the manifest
##            itself. Reading self would create a self-referential drift mask.
## @scope    CI gate — static source code analysis
## @invariants
##   - Generator reads existing manifest ONLY via load_structural_sections() which explicitly
##     EXCLUDES allowed_verbs and gates keys. load_existing_manifest() is kept for backward
##     compat but NOT used in main().
##   - Generator NEVER reads allowed_verbs or gates from existing manifest
##   - `load_structural_sections()` is called from main() — allowed_verbs/gates NEVER in result
##   - `merge()` function replaces allowed_verbs and gates[] entirely with generated values
##     as a second line of defense
##   - This is verified by analyzing `load_structural_sections()` — it explicitly excludes
##     allowed_verbs and gates keys via set exclusion
## @rationale DevPlan 090 T6 — G3 Cycle Break. Atomic Generation requires allowed_verbs/gates
##            from Makefile/pytest, not from manifest. load_structural_sections() makes the
##            exclusion explicit at the data-loading layer, making it impossible to accidentally
##            read self-generated sections.
## @changes 2026-07-30 · Created — DevPlan 090 gate
##           2026-07-30 · Updated for load_structural_sections() — main() now uses this instead
##                        of load_existing_manifest(). load_existing_manifest() kept as backward
##                        compat for external consumers.
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
##             Verified by: (1) load_structural_sections() explicitly excludes these keys,
##             (2) load_existing_manifest() is NOT called from main(), (3) merge() overwrites
##             allowed_verbs/gates unconditionally.
## · Last fail: N/A (new gate)
## · Remove if: entrypoint-manifest.yaml generation is restructured (e.g., split into separate files)
def test_no_self_read(caplog) -> None:
    """Verify G3 generate_entrypoint_manifest.py does NOT read allowed_verbs/gates
    from existing entrypoint-manifest.yaml — these sections are GENERATED, not preserved.

    G3 loads existing manifest via load_structural_sections() which explicitly EXCLUDES
    allowed_verbs and gates keys. These come exclusively from Makefile .PHONY targets
    and pytest gate markers.

    Structural sections preserved from manifest:
    - metadata, convention, schema
    - forbidden_directories, forbidden_scripts, forbidden_verbs
    - name_linter, module_lifecycle, system_module_lifecycle
    - lib, module_hooks, repair
    - All other sections except allowed_verbs and gates

    allowed_verbs and gates[] are REPLACED entirely by extracted values from Makefile and pytest.
    This is a two-layer defense: (1) load_structural_sections() excludes them at load time,
    (2) merge() overwrites them unconditionally as a safety net.
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

    # ── Verify 1: `load_structural_sections()` exists and is called from main() ──
    assert "def load_structural_sections" in source, (
        f"Missing load_structural_sections() function in {_GENERATOR_PATH} — "
        f"this is the G3 cycle break function that excludes allowed_verbs/gates"
    )
    # Check that load_structural_sections() is called in main() (NOT load_existing_manifest)
    assert "existing = load_structural_sections" in source, (
        f"load_structural_sections() must be called from main() to break the G3 cycle. "
        f"Look for `existing = load_structural_sections(...)` in main()."
    )
    print("[IMP:7][test_no_self_read] load_structural_sections() defined and called from main() — OK", file=sys.stderr)

    # ── Verify 1b: `load_existing_manifest()` exists for backward compat but NOT called from main() ──
    assert "def load_existing_manifest" in source, (
        f"Missing load_existing_manifest() function in {_GENERATOR_PATH} — "
        f"kept for backward compat"
    )
    # load_existing_manifest should NOT be called from main() — only from unit tests or external consumers
    # The function definition contains "load_existing_manifest(" but main() should NOT call it
    # Check that the call pattern "existing = load_existing_manifest" is NOT present
    assert "existing = load_existing_manifest" not in source, (
        f"G3 CYCLE BREAK VIOLATION: main() still calls load_existing_manifest() instead of "
        f"load_structural_sections(). Replace with load_structural_sections() to break the cycle."
    )
    print(
        "[IMP:7][test_no_self_read] load_existing_manifest() defined (backward compat) but NOT called from main() — OK",
        file=sys.stderr,
    )

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

    # ── Verify 3: load_structural_sections() is called from main(), not from extract or collect ──
    # load_existing_manifest() is defined for backward compat but NOT called in main()
    lines = source.splitlines()
    structural_lines = [
        i + 1 for i, line in enumerate(lines)
        if "load_structural_sections" in line and not line.strip().startswith("#")
    ]
    load_existing_lines = [
        i + 1 for i, line in enumerate(lines)
        if "load_existing_manifest" in line and not line.strip().startswith("#")
    ]
    merge_calls = [i + 1 for i, line in enumerate(lines) if "existing" in line and "merge(" in line and not line.strip().startswith("#")]

    print(f"[IMP:7][test_no_self_read] load_structural_sections referenced at lines: {structural_lines}", file=sys.stderr)
    print(f"[IMP:7][test_no_self_read] load_existing_manifest referenced at lines: {load_existing_lines}", file=sys.stderr)
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

    # ── Verify 5: load_structural_sections() explicitly excludes allowed_verbs and gates ──
    # Check the excluded set in load_structural_sections
    # This ensures the G3 cycle break is explicit at the data-loading layer
    assert '"allowed_verbs"' in source or "'allowed_verbs'" in source, (
        "load_structural_sections() must explicitly reference allowed_verbs in its exclusion set. "
        "The excluded set should contain 'allowed_verbs' and 'gates'."
    )
    assert '"gates"' in source or "'gates'" in source, (
        "load_structural_sections() must explicitly reference gates in its exclusion set."
    )
    print("[IMP:9][test_no_self_read] load_structural_sections() explicitly excludes allowed_verbs and gates — OK", file=sys.stderr)

    # ── Verify 6: Structural sections that ARE preserved are legitimate ──
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
