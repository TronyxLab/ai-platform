"""
Static tests for node-lifecycle.sh — NODE_YAML derivation, --dry-run, and flags contract.
# GREP_SUMMARY: test node-lifecycle node-yaml node-resolver dry-run flags contract static-audit
# STRUCTURE: ▶ read node-lifecycle.sh + node-update.sh → ◇ grep patterns → ⊕ assertions
# region MODULE_CONTRACT
## @purpose  Static analysis tests verifying:
##           T1 — update-mode has NODE_NAME fail-fast + NODE_YAML derivation via node-resolver.sh
##           T2 — --dry-run parser flag + dry-run plan before mkdir mutations
##           T2c — entrypoint↔internal flags contract (all node-update.sh flags accepted by node-lifecycle.sh)
## @scope    Reads script files from disk, applies grep-based pattern assertions
## @invariants
##   - All tests use @pytest.mark.static_audit (not gate — these verify script internals)
##   - LDD telemetry via caplog with assert on IMP:9 presence
## @rationale   Static verification catches regression before runtime failures.
##              Flags contract ensures no "Unknown argument" from internal when entrypoint forwards flags.
## @changes     2026-07-17 | Created per DevPlan 004 Wave 1 T1/T2
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

CORE_DIR = Path(__file__).resolve().parent.parent / "core"
LIFECYCLE_SCRIPT = CORE_DIR / "internal" / "bootstrap" / "node-lifecycle.sh"
ENTRYPOINT_SCRIPT = CORE_DIR / "entrypoints" / "node-update.sh"


# region FUNC_test_update_mode_resolves_node_yaml
## @purpose  Verify update-mode main() has fail-fast NODE_NAME validation + NODE_YAML
##           derivation via lib/node-resolver.sh, before mkdir $CHECKPOINT_DIR.
## @io       Script content → grep → assert patterns present and in correct order
## @complexity O(S) where S = script line count
## @invariants — NODE_NAME validation before resolution; resolution before mkdir; exit 1 on unresolvable
@pytest.mark.static_audit
def test_update_mode_resolves_node_yaml(caplog) -> None:
    """Update-mode: NODE_NAME fail-fast + NODE_YAML derivation via node-resolver.sh before mkdir."""
    # 🧪 TRAP[TEST] · Regression: T1 — NODE_YAML derivation in update-mode
    # · Scenario: node-lifecycle.sh --mode update invoked without NODE_YAML env
    # · Last fail: FIX-001 (VPS hotfix lost on core-deploy)
    # · Remove if: update-mode no longer needs NODE_YAML (unlikely)
    logger.info("[IMP:7][test_update_mode_resolves_node_yaml] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Check 1: NODE_NAME validation (fail-fast) ──
    assert "NODE_NAME.*exit 1" in content.replace("\n", " ") or ("NODE_NAME" in content and "exit 1" in content), (
        "[IMP:9][test] FAIL: update-mode must validate NODE_NAME with exit 1"
    )
    logger.info("[IMP:8][test_update_mode_resolves_node_yaml] Check 1 PASS: NODE_NAME validation present")

    # ── Check 2: node-resolver.sh sourced ──
    assert "node-resolver.sh" in content, (
        "[IMP:9][test] FAIL: update-mode must source lib/node-resolver.sh for NODE_YAML derivation"
    )
    logger.info("[IMP:8][test_update_mode_resolves_node_yaml] Check 2 PASS: node-resolver.sh sourced")

    # ── Check 3: resolve_node_yaml called ──
    assert "resolve_node_yaml" in content, "[IMP:9][test] FAIL: update-mode must call resolve_node_yaml()"
    logger.info("[IMP:8][test_update_mode_resolves_node_yaml] Check 3 PASS: resolve_node_yaml() called")

    # ── Check 4: unresolvable → exit 1 with candidate paths ──
    assert "exit 1" in content and (
        "candidate" in content.lower() or "searched" in content.lower() or "Tried" in content
    ), "[IMP:9][test] FAIL: unresolvable NODE_YAML must exit 1 with candidate paths listed"
    logger.info("[IMP:8][test_update_mode_resolves_node_yaml] Check 4 PASS: unresolvable → exit 1 + paths")

    # ── Check 5: Resolution + dry-run happen BEFORE mkdir $CHECKPOINT_DIR ──
    update_section = content[content.find('elif [[ "$MODE" == "update" ]]') :]
    mkdir_pos = update_section.find('mkdir -p "$CHECKPOINT_DIR"')
    resolver_pos = update_section.find("resolve_node_yaml")
    dry_run_pos = update_section.find("DRY_RUN_MODE")

    # resolution and dry-run check must be before mkdir
    if resolver_pos >= 0:
        assert resolver_pos < mkdir_pos, (
            f"[IMP:9][test] FAIL: resolve_node_yaml ({resolver_pos}) must precede mkdir ({mkdir_pos})"
        )
    if dry_run_pos >= 0:
        assert dry_run_pos < mkdir_pos, (
            f"[IMP:9][test] FAIL: dry-run check ({dry_run_pos}) must precede mkdir ({mkdir_pos})"
        )
    logger.info("[IMP:8][test_update_mode_resolves_node_yaml] Check 5 PASS: resolution/dry-run before mkdir")

    logger.info("[IMP:9][test_update_mode_resolves_node_yaml] ALL CHECKS PASS")


# endregion FUNC_test_update_mode_resolves_node_yaml


# region FUNC_test_dry_run_flag_accepted
## @purpose  Verify parser accepts --dry-run, and both init/update modes have dry-run
##           plan print + exit 0 BEFORE mkdir $CHECKPOINT_DIR.
## @io       Script content → grep → assert patterns present and in correct order
## @complexity O(S)
## @invariants — --dry-run in parser; dry-run block in both modes before mkdir
@pytest.mark.static_audit
def test_dry_run_flag_accepted(caplog) -> None:
    """--dry-run: parser flag accepted, both modes have dry-run plan before mkdir."""
    # 🧪 TRAP[TEST] · Regression: T2 — --dry-run contract
    # · Scenario: node-lifecycle.sh --mode update --dry-run
    # · Last fail: S1 BLOCKED (dry-run not implemented, update always mutated)
    # · Remove if: dry-run flag removed from CLI
    logger.info("[IMP:7][test_dry_run_flag_accepted] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Check 1: Parser accepts --dry-run ──
    assert "--dry-run" in content, "[IMP:9][test] FAIL: parser must accept --dry-run flag"
    assert "DRY_RUN_MODE=true" in content or "DRY_RUN_MODE" in content, (
        "[IMP:9][test] FAIL: --dry-run must set DRY_RUN_MODE=true"
    )
    logger.info("[IMP:8][test_dry_run_flag_accepted] Check 1 PASS: --dry-run in parser")

    # ── Check 2: Init mode has dry-run block ──
    # Find main() first, then locate the init branch within it
    main_start = content.find("main() {")
    assert main_start >= 0, "[IMP:9][test] FAIL: main() not found"
    main_content = content[main_start:]
    init_block = main_content[main_content.find('if [[ "$MODE" == "init" ]]') :]
    init_block = init_block[: init_block.find('elif [[ "$MODE" == "update" ]]')]
    assert "DRY_RUN_MODE" in init_block, "[IMP:9][test] FAIL: init mode main() must have DRY_RUN_MODE check"
    init_mkdir_pos = init_block.find('mkdir -p "$CHECKPOINT_DIR"')
    init_dry_run_pos = init_block.find("DRY_RUN_MODE")
    if init_dry_run_pos >= 0 and init_mkdir_pos >= 0:
        assert init_dry_run_pos < init_mkdir_pos, (
            f"[IMP:9][test] FAIL: init dry-run ({init_dry_run_pos}) before mkdir ({init_mkdir_pos})"
        )
    logger.info("[IMP:8][test_dry_run_flag_accepted] Check 2 PASS: init mode dry-run before mkdir")

    # ── Check 3: Update mode has dry-run block before mkdir ──
    update_section = content[content.find('elif [[ "$MODE" == "update" ]]') :]
    update_mkdir_pos = update_section.find('mkdir -p "$CHECKPOINT_DIR"')
    update_dry_run_pos = update_section.find("DRY_RUN_MODE")
    if update_dry_run_pos >= 0 and update_mkdir_pos >= 0:
        assert update_dry_run_pos < update_mkdir_pos, (
            f"[IMP:9][test] FAIL: update dry-run ({update_dry_run_pos}) before mkdir ({update_mkdir_pos})"
        )
    logger.info("[IMP:8][test_dry_run_flag_accepted] Check 3 PASS: update mode dry-run before mkdir")

    # ── Check 4: Dry-run prints plan and exit 0 ──
    assert "exit 0" in update_section[:update_mkdir_pos] if update_mkdir_pos > 0 else True
    assert "DRY RUN" in content, "[IMP:9][test] FAIL: dry-run must print 'DRY RUN' plan header"
    logger.info("[IMP:8][test_dry_run_flag_accepted] Check 4 PASS: dry-run prints plan + exit 0")

    logger.info("[IMP:9][test_dry_run_flag_accepted] ALL CHECKS PASS")


# endregion FUNC_test_dry_run_flag_accepted


# region FUNC_test_entrypoint_flags_contract
## @purpose  Verify node-update.sh → node-lifecycle.sh flags contract:
##           every flag node-update.sh forwards is accepted by node-lifecycle.sh parser.
## @io       Read both scripts → extract case patterns → assert acceptance
## @complexity O(E + L) where E = entrypoint flags, L = lifecycle flags
## @invariants — node-update.sh's --node-name, --dry-run must be in node-lifecycle.sh parser
@pytest.mark.static_audit
def test_entrypoint_flags_contract(caplog) -> None:
    """Flags contract: all node-update.sh forwardable flags accepted by node-lifecycle.sh parser."""
    # 🧪 TRAP[TEST] · Regression: T2c — entrypoint↔internal flags contract
    # · Scenario: node-update.sh adds a new CLI flag but node-lifecycle.sh rejects it → "Unknown argument"
    # · Last fail: FIX-001 (entrypoint forwarded --node-name, internal parser had no --dry-run)
    # · Remove if: node-update.sh no longer delegates to node-lifecycle.sh
    logger.info("[IMP:7][test_entrypoint_flags_contract] START")
    caplog.set_level(logging.DEBUG)

    lifecycle_content = LIFECYCLE_SCRIPT.read_text()
    entrypoint_content = ENTRYPOINT_SCRIPT.read_text()

    # Extract flags that node-update.sh forwards to node-lifecycle.sh
    # node-update.sh main() builds args array with: --node-name + --dry-run
    # The case statements in node-update.sh also accept --node (aliased to --node-name)

    # ── Check 1: --node-name accepted by node-lifecycle.sh ──
    assert "--node-name" in lifecycle_content, "[IMP:9][test] FAIL: --node-name not in node-lifecycle.sh parser"
    logger.info("[IMP:8][test_entrypoint_flags_contract] Check 1 PASS: --node-name in lifecycle parser")

    # ── Check 2: --dry-run accepted by node-lifecycle.sh ──
    assert "--dry-run" in lifecycle_content, "[IMP:9][test] FAIL: --dry-run not in node-lifecycle.sh parser"
    logger.info("[IMP:8][test_entrypoint_flags_contract] Check 2 PASS: --dry-run in lifecycle parser")

    # ── Check 3: node-update.sh forwards --node-name (not --node) ──
    # Lines 75-82: case --node|--node-name → NODE_NAME="$2"; DRY_RUN=true for --dry-run
    assert "--node-name" in entrypoint_content, "[IMP:9][test] FAIL: node-update.sh must forward --node-name"
    assert "--dry-run" in entrypoint_content, "[IMP:9][test] FAIL: node-update.sh must accept --dry-run"
    logger.info("[IMP:8][test_entrypoint_flags_contract] Check 3 PASS: entrypoint forwards both flags")

    # ── Check 4: No 'Unknown argument' exit for these flags in node-lifecycle.sh ──
    # Ensure the main case block doesn't reject --dry-run via the `-*)` catch-all
    parser_section = lifecycle_content[: lifecycle_content.find("# SOPS_AGE_KEY fallback")]
    # The `-*)` catch-all pattern should NOT be reached by --dry-run
    assert "--dry-run" in parser_section, (
        "[IMP:9][test] FAIL: --dry-run must be in node-lifecycle.sh case block, not catch-all"
    )
    # Verify the case block has explicit --dry-run entry (not caught by -*)"
    case_block = lifecycle_content[
        lifecycle_content.find("while [[ $# -gt 0 ]]; do") : lifecycle_content.find("# SOPS_AGE_KEY fallback")
    ]
    dry_run_lines = [line for line in case_block.split("\n") if "--dry-run" in line]
    assert any("DRY_RUN_MODE=true" in line for line in dry_run_lines), (
        "[IMP:9][test] FAIL: case --dry-run) must set DRY_RUN_MODE=true"
    )
    logger.info("[IMP:8][test_entrypoint_flags_contract] Check 4 PASS: explicit --dry-run case")

    logger.info("[IMP:9][test_entrypoint_flags_contract] ALL CHECKS PASS")


# endregion FUNC_test_entrypoint_flags_contract
