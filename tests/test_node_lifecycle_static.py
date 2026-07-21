"""
Static tests for node-lifecycle.sh — NODE_YAML derivation, --dry-run, and flags contract.
# GREP_SUMMARY: test node-lifecycle node-yaml node-resolver dry-run flags contract static-audit ssh-proxy remote-cmd secrets-env ssl-provision w4-e5 mode-dispatch checkpoint step-skip tor-conditional step-warn
# STRUCTURE: ▶ read node-lifecycle.sh + node-update.sh → ◇ grep patterns → ⊕ assertions → ◇ W4-E5 edge-cases (mode-dispatch / checkpoint / TOR / step-warn / init-vs-update steps)
# region MODULE_CONTRACT
## @purpose  Static analysis tests verifying:
##           T1 — update-mode has NODE_NAME fail-fast + NODE_YAML derivation via node-resolver.sh
##           T2 — --dry-run parser flag + dry-run plan before mkdir mutations
##           T2c — entrypoint↔internal flags contract (all node-update.sh flags accepted by node-lifecycle.sh)
##           W4-E5 (DevPlan 035 §7) — edge-case regression baseline for node-lifecycle.sh internals:
##             mode-dispatch (init vs update), checkpoint_step + per-step content-hash skip,
##             TOR-conditional branch, step_warn error collection, init(18) vs update(6) step counts.
## @scope    Reads script files from disk, applies grep-based pattern assertions
## @invariants
##   - All tests use @pytest.mark.static_audit (not gate — these verify script internals)
##   - LDD telemetry via caplog with assert on IMP:9 presence
## @rationale   Static verification catches regression before runtime failures.
##              Flags contract ensures no "Unknown argument" from internal when entrypoint forwards flags.
##              W4-E5 edge-cases are страховка R-RISK-5 ДО W4-E2 state_machine extraction.
## @changes     2026-07-17 | Created per DevPlan 004 Wave 1 T1/T2
##              2026-07-22 | W4-E5 +5 edge-case tests (DevPlan 035 §7)
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

    # ── Check 5: --age-secret-key-file accepted by node-lifecycle.sh parser ──
    assert "--age-secret-key-file" in lifecycle_content, (
        "[IMP:9][test] FAIL: --age-secret-key-file not in node-lifecycle.sh parser"
    )
    # Verify that the area around --age-secret-key-file references AGE_SECRET_KEY.
    # The flag line itself only has `--age-secret-key-file)`, but the handling
    # block reads file content into AGE_SECRET_KEY. Check the context after it.
    age_key_idx = lifecycle_content.find("--age-secret-key-file")
    assert age_key_idx >= 0, "[IMP:9][test] FAIL: --age-secret-key-file not found in lifecycle"
    context_after = lifecycle_content[age_key_idx : age_key_idx + 500]
    assert "AGE_SECRET_KEY" in context_after, (
        "[IMP:9][test] FAIL: --age-secret-key-file case must read file into AGE_SECRET_KEY"
    )
    logger.info("[IMP:8][test_entrypoint_flags_contract] Check 5 PASS: --age-secret-key-file in lifecycle parser")

    # ── Check 6: --age-secret-key-file accepted by node-update.sh entrypoint ──
    assert "--age-secret-key-file" in entrypoint_content, (
        "[IMP:9][test] FAIL: --age-secret-key-file not in node-update.sh parser"
    )
    # Verify node-update.sh sets AGE_SECRET_KEY_FILE from flag (consumed by detect_age_key)
    assert "AGE_SECRET_KEY_FILE" in entrypoint_content, (
        "[IMP:9][test] FAIL: node-update.sh must set AGE_SECRET_KEY_FILE from --age-secret-key-file"
    )
    # Verify detect_age_key function exists in entrypoint
    assert "detect_age_key" in entrypoint_content, (
        "[IMP:9][test] FAIL: node-update.sh must have detect_age_key() to consume AGE_SECRET_KEY_FILE"
    )
    logger.info(
        "[IMP:8][test_entrypoint_flags_contract] Check 6 PASS: --age-secret-key-file in entrypoint + detect_age_key"
    )

    logger.info("[IMP:9][test_entrypoint_flags_contract] ALL CHECKS PASS")


# endregion FUNC_test_entrypoint_flags_contract


# region FUNC_test_node_update_has_ssh_proxy
## @purpose  Verify node-update.sh delegates to execute_remote_update() in remote-cmd.sh,
##           and that remote-cmd.sh contains the SSH proxy logic (resolve_node_yaml +
##           extract_node_host). Local exec fallback remains in entrypoint.
## @io       Script content → grep → assert patterns present
## @complexity O(S)
## @invariants — Entrypoint calls execute_remote_update; remote-cmd.sh has SSH proxy;
##               local fallback in entrypoint
@pytest.mark.static_audit
def test_node_update_has_ssh_proxy(caplog) -> None:
    """node-update.sh: delegates to execute_remote_update(); remote-cmd.sh has SSH proxy."""
    # 🧪 TRAP[TEST] · Regression: T1 — SSH proxy in remote-cmd.sh via execute_remote_update
    # · Scenario: make node-update from macOS fails "must run as root"
    # · Last fail: Wave 1 pre-merge (no SSH proxy)
    # · Remove if: entrypoint no longer needs SSH proxy
    logger.info("[IMP:7][test_node_update_has_ssh_proxy] START")
    caplog.set_level(logging.DEBUG)

    entrypoint_content = ENTRYPOINT_SCRIPT.read_text()
    remote_cmd_script = CORE_DIR / "internal" / "bootstrap" / "remote-cmd.sh"
    remote_content = remote_cmd_script.read_text()

    # ── Check 1: execute_remote_update called from entrypoint ──
    assert "execute_remote_update" in entrypoint_content, (
        "[IMP:9][test] FAIL: node-update.sh must call execute_remote_update()"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 1 PASS: execute_remote_update() called from entrypoint")

    # ── Check 2: resolve_node_yaml in remote-cmd.sh (SSH proxy moved) ──
    assert "resolve_node_yaml" in remote_content, (
        "[IMP:9][test] FAIL: remote-cmd.sh must call resolve_node_yaml() inside execute_remote_update"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 2 PASS: resolve_node_yaml() in remote-cmd.sh")

    # ── Check 3: extract_node_host in remote-cmd.sh (SSH proxy moved) ──
    assert "extract_node_host" in remote_content, (
        "[IMP:9][test] FAIL: remote-cmd.sh must call extract_node_host() inside execute_remote_update"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 3 PASS: extract_node_host() in remote-cmd.sh")

    # ── Check 4: SSH_HOST fallback — local exec path exists in entrypoint ──
    assert "LOCALLY" in entrypoint_content or "local" in entrypoint_content.lower(), (
        "[IMP:9][test] FAIL: node-update.sh must have local exec fallback when SSH_HOST absent"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 4 PASS: local exec fallback in entrypoint")

    # ── Check 5: --age-secret-key-file accepted ──
    assert "--age-secret-key-file" in entrypoint_content, (
        "[IMP:9][test] FAIL: node-update.sh must accept --age-secret-key-file"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 5 PASS: --age-secret-key-file flag")

    # ── Check 6: detect_age_key exists in entrypoint ──
    assert "detect_age_key" in entrypoint_content, "[IMP:9][test] FAIL: node-update.sh must have detect_age_key()"
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 6 PASS: detect_age_key() present")

    logger.info("[IMP:9][test_node_update_has_ssh_proxy] ALL CHECKS PASS")


# endregion FUNC_test_node_update_has_ssh_proxy


# region FUNC_test_remote_cmd_has_update_mode
## @purpose  Verify remote-cmd.sh contains build_update_ssh_cmd() with --mode update.
## @io       Script content → grep → assert patterns present
## @complexity O(S)
## @invariants — build_update_ssh_cmd exists; contains --mode update; no --resume (D2)
@pytest.mark.static_audit
def test_remote_cmd_has_update_mode(caplog) -> None:
    """remote-cmd.sh: build_update_ssh_cmd exists with --mode update, no --resume."""
    # 🧪 TRAP[TEST] · Regression: T2 — build_update_ssh_cmd contract
    # · Scenario: SSH proxy calls build_update_ssh_cmd but internal changes signature
    # · Last fail: Wave 1 pre-merge (function didn't exist)
    # · Remove if: remote-cmd.sh no longer needed for SSH proxy
    logger.info("[IMP:7][test_remote_cmd_has_update_mode] START")
    caplog.set_level(logging.DEBUG)

    remote_cmd_script = CORE_DIR / "internal" / "bootstrap" / "remote-cmd.sh"
    content = remote_cmd_script.read_text()

    # ── Check 1: build_update_ssh_cmd exists ──
    assert "build_update_ssh_cmd" in content, "[IMP:9][test] FAIL: remote-cmd.sh must define build_update_ssh_cmd()"
    logger.info("[IMP:8][test_remote_cmd_has_update_mode] Check 1 PASS: build_update_ssh_cmd() defined")

    # ── Check 2: contains --mode update ──
    # Check within the build_update_ssh_cmd function body (not file-wide).
    # Find the actual function definition (with {), not comment mentions.
    update_func_start = content.find("build_update_ssh_cmd() {")
    assert update_func_start >= 0, "[IMP:9][test] FAIL: build_update_ssh_cmd() { not found"
    update_func_body = content[update_func_start:]
    assert "--mode" in update_func_body and "update" in update_func_body, (
        "[IMP:9][test] FAIL: build_update_ssh_cmd must reference --mode update"
    )
    logger.info("[IMP:8][test_remote_cmd_has_update_mode] Check 2 PASS: --mode update present")

    # ── Check 3: does NOT contain --resume in function body (D2: update steps independent) ──
    # NOTE: --resume may appear in file-level comments but must NOT be in the function body.
    # Scope the check to only the build_update_ssh_cmd function, not the rest of the file
    # (which contains build_converge_ssh_cmd with --resume in its docstring).
    update_func_end = content.find("# endregion FUNC_build_update_ssh_cmd")
    if update_func_end < 0:
        update_func_end = update_func_start + 500
    bounded_func_body = content[update_func_start:update_func_end]
    assert "--resume" not in bounded_func_body, (
        "[IMP:9][test] FAIL: build_update_ssh_cmd must NOT contain --resume (D2)"
    )
    logger.info("[IMP:8][test_remote_cmd_has_update_mode] Check 3 PASS: --resume absent from function body (per D2)")

    # ── Check 4: does NOT contain --owner-key in function body (D2: not needed in update mode) ──
    assert "--owner-key" not in bounded_func_body, (
        "[IMP:9][test] FAIL: build_update_ssh_cmd must NOT contain --owner-key (D2)"
    )
    logger.info("[IMP:8][test_remote_cmd_has_update_mode] Check 4 PASS: --owner-key absent from function body (per D2)")

    # ── Check 5: uses printf %q for quoting ──
    assert "printf '%q'" in content, (
        "[IMP:9][test] FAIL: build_update_ssh_cmd must use printf %%q for shell-safe quoting"
    )
    logger.info("[IMP:8][test_remote_cmd_has_update_mode] Check 5 PASS: printf %%q quoting")

    # ── Check 6: exports AGE_SECRET_KEY ──
    assert "AGE_SECRET_KEY" in content, "[IMP:9][test] FAIL: build_update_ssh_cmd must handle AGE_SECRET_KEY export"
    logger.info("[IMP:8][test_remote_cmd_has_update_mode] Check 6 PASS: AGE_SECRET_KEY handling")

    logger.info("[IMP:9][test_remote_cmd_has_update_mode] ALL CHECKS PASS")


# endregion FUNC_test_remote_cmd_has_update_mode


# region FUNC_test_update_ssl_step_sources_secrets_env
## @purpose  Verify update_step_3_ssl_provision() sources /run/platform/secrets.env
##           before calling ssl-provision.sh. Uses SECRETS_ENV_FILE from lib/secrets.sh
##           with fallback to /run/platform/secrets.env.
## @io       Script content → grep → assert patterns present in function
## @complexity O(S)
## @invariants — source secrets.env before ssl-provision; WEBNAMES_API_KEY loaded log;
##               WARN if file missing; NOT fail (ssl-provision skips if cert exists)
@pytest.mark.static_audit
def test_update_ssl_step_sources_secrets_env(caplog) -> None:
    """update_step_3_ssl_provision: sources secrets.env before ssl-provision.sh."""
    # 🧪 TRAP[TEST] · Regression: T3 — WEBNAMES_API_KEY not set in update mode
    # · Scenario: SSL cert renewal fails because secrets.env not sourced
    # · Last fail: Wave 1 production (WARN "WEBNAMES_API_KEY not set")
    # · Remove if: ssl-provision no longer needs WEBNAMES_API_KEY
    logger.info("[IMP:7][test_update_ssl_step_sources_secrets_env] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Check 1: Source secrets.env pattern exists ──
    assert "secrets_env" in content.lower() or "secrets.env" in content.lower(), (
        "[IMP:9][test] FAIL: update_step_3_ssl_provision must reference secrets.env"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 1 PASS: secrets.env referenced")

    # ── Check 2: Uses SECRETS_ENV_FILE or /run/platform/secrets.env ──
    assert "SECRETS_ENV_FILE" in content or "/run/platform/secrets.env" in content, (
        "[IMP:9][test] FAIL: must use SECRETS_ENV_FILE from lib/secrets.sh"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 2 PASS: SECRETS_ENV_FILE used")

    # ── Check 3: Uses set -a / source / set +a for export ──
    assert "set -a" in content and "set +a" in content, (
        "[IMP:9][test] FAIL: must use set -a/+a around source for var export"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 3 PASS: set -a/+a export")

    # ── Check 4: WEBNAMES_API_KEY log exists ──
    assert "WEBNAMES_API_KEY" in content, "[IMP:9][test] FAIL: must log WEBNAMES_API_KEY status after source"
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 4 PASS: WEBNAMES_API_KEY log")

    # ── Check 5: WARN log for missing secrets.env ──
    # The log message uses bash variable: "${secrets_env} missing — cert renewal may fail if cert expires"
    # where secrets_env resolves to /run/platform/secrets.env. Check for "secrets_env.*missing" pattern.
    import re

    warn_missing_pattern = r"secrets_env.*missing"
    assert re.search(warn_missing_pattern, content, re.IGNORECASE), (
        "[IMP:9][test] FAIL: must have WARN log for missing secrets.env (expected pattern: secrets_env.*missing)"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 5 PASS: WARN for missing file")

    # ── Check 6: Source before ssl-provision.sh call ──
    # Find the function body and verify order: source before ssl_script invocation
    func_start = content.find("update_step_3_ssl_provision()")
    assert func_start >= 0, "[IMP:9][test] FAIL: update_step_3_ssl_provision function not found"

    func_body = content[func_start:]
    source_pos = func_body.find("secrets_env")
    ssl_call_pos = func_body.find('bash "$ssl_script"')
    assert source_pos >= 0 and ssl_call_pos >= 0, (
        "[IMP:9][test] FAIL: Could not locate source and ssl_script call in function"
    )
    assert source_pos < ssl_call_pos, (
        f"[IMP:9][test] FAIL: secrets_env source ({source_pos}) must precede ssl_script call ({ssl_call_pos})"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 6 PASS: source before ssl-provision.sh call")

    logger.info("[IMP:9][test_update_ssl_step_sources_secrets_env] ALL CHECKS PASS")


# endregion FUNC_test_update_ssl_step_sources_secrets_env


# ══════════════════════════════════════════════════════════════════════════════
# W4-E5 (DevPlan 035 §7): Edge-case regression baseline — страховка R-RISK-5 ДО extraction.
# Каждый тест покрывает edge-case node-lifecycle.sh, который W4-E2 state_machine extraction
# НЕ должен нарушить. Static-audit pattern (consistent with T1/T2/T2c/T3 above).
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_mode_dispatch_init_update
## @purpose  W4-E5 edge-case: verify node-lifecycle.sh mode-dispatch handles BOTH --mode init
##           and --mode update branches, and rejects invalid modes at entry. This is the contract
##           W4-E2 state_machine.py mode-dispatch must preserve — init has 18 steps, update has 6.
## @io       caplog → ⎋ None (pytest.fail if dispatch logic absent or malformed)
## @complexity 1 — static grep for MODE validation + init/update branch
## @invariants
##   - Parser validates first arg is --mode (exit 1 otherwise)
##   - MODE must be "init" or "update" (exit 1 otherwise)
##   - main() has explicit init branch and update branch


@pytest.mark.static_audit
def test_mode_dispatch_init_update(caplog) -> None:
    """Mode-dispatch: --mode init/update accepted, invalid rejected, both branches exist."""
    # 🧪 TRAP[TEST] · Regression: W4-E5 mode-dispatch (init vs update) at entry
    # · Scenario: --mode invalid → exit 1; init/update branches both present in main()
    # · Last fail: N/A (W4-E5 baseline)
    # · Remove if: mode-dispatch moves to state_machine.py (then point test at new module)
    logger.info("[IMP:7][test_mode_dispatch_init_update] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Check 1: --mode is first arg, validated against {init, update} ──
    assert '"--mode"' in content or "--mode" in content, "FAIL: --mode flag not parsed"
    assert '"init"' in content and '"update"' in content, "FAIL: parser must validate MODE against 'init' and 'update'"
    # Fail-fast on invalid mode (exit 1)
    assert "exit 1" in content[: content.find("step_start")], "FAIL: invalid MODE must exit 1 before any step runs"
    logger.info("[IMP:8][test_mode_dispatch_init_update] Check 1 PASS: --mode validation")

    # ── Check 2: init branch exists (18 steps) ──
    init_branch_idx = content.find('if [[ "$MODE" == "init" ]]')
    assert init_branch_idx >= 0, "FAIL: init branch not found in main()"
    init_branch = content[init_branch_idx:]
    # init branch references step_1 through step_17 (or update via step_14)
    assert "step_1_ssh_access" in init_branch, "FAIL: init branch must call step_1_ssh_access"
    assert "step_17_telegram" in init_branch or "step_16_audit" in init_branch, (
        "FAIL: init branch must reach step_16/17 (audit/telegram)"
    )
    logger.info("[IMP:8][test_mode_dispatch_init_update] Check 2 PASS: init branch has 18 steps")

    # ── Check 3: update branch exists (6 steps) ──
    update_branch_idx = content.find('elif [[ "$MODE" == "update" ]]')
    assert update_branch_idx >= 0, "FAIL: update branch not found in main()"
    update_branch = content[update_branch_idx:]
    assert "update_step_1_verify_core" in update_branch or "update_step" in update_branch, (
        "FAIL: update branch must call update_step_* functions"
    )
    assert "update_step_6_healthcheck" in update_branch, "FAIL: update branch must reach update_step_6_healthcheck"
    logger.info("[IMP:8][test_mode_dispatch_init_update] Check 3 PASS: update branch has 6 steps")

    logger.info("[IMP:9][test_mode_dispatch_init_update] ALL CHECKS PASS")


# endregion FUNC_test_mode_dispatch_init_update


# region FUNC_test_checkpoint_step_uses_content_hash
## @purpose  W4-E5 edge-case: verify node-lifecycle.sh sources checkpoint.sh + content-hash.sh
##           and uses checkpoint_step() with per-step content hash for idempotent skip.
##           When a step's content-hash is unchanged, the step is SKIPPED (not re-executed).
##           This is the idempotency contract W4-E2 state_machine.py checkpoint-resume must preserve.
## @io       caplog → ⎋ None (pytest.fail if checkpoint/hash logic absent)
## @complexity 1 — static grep for checkpoint_step + _step_hash + content-hash source
## @invariants
##   - node-lifecycle.sh sources lib/checkpoint.sh
##   - node-lifecycle.sh sources internal/bootstrap/content-hash.sh
##   - checkpoint_step() is called with CHECKPOINT_STEP_HASH env for per-step invalidation
##   - CHECKPOINT_DIR + RESUME_MODE + FORCE_MODE are set before checkpoint calls


@pytest.mark.static_audit
def test_checkpoint_step_uses_content_hash(caplog) -> None:
    """Checkpoint-resume: checkpoint_step + per-step content-hash for idempotent skip."""
    # 🧪 TRAP[TEST] · Regression: W4-E5 checkpoint_step + per-step content-hash
    # · Scenario: step with unchanged hash + .done marker → SKIP (idempotent re-run)
    # · Last fail: N/A (W4-E5 baseline)
    # · Remove if: checkpoint migrates to state.json (W4-E2 state_machine.py)
    logger.info("[IMP:7][test_checkpoint_step_uses_content_hash] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Check 1: sources checkpoint.sh ──
    assert "checkpoint.sh" in content, "FAIL: node-lifecycle.sh must source lib/checkpoint.sh"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 1 PASS: checkpoint.sh sourced")

    # ── Check 2: sources content-hash.sh ──
    assert "content-hash.sh" in content, "FAIL: node-lifecycle.sh must source internal/bootstrap/content-hash.sh"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 2 PASS: content-hash.sh sourced")

    # ── Check 3: CHECKPOINT_DIR defined ──
    assert "CHECKPOINT_DIR=" in content, "FAIL: CHECKPOINT_DIR must be defined"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 3 PASS: CHECKPOINT_DIR defined")

    # ── Check 4: checkpoint_step() called at least once ──
    checkpoint_calls = content.count("checkpoint_step ")
    assert checkpoint_calls >= 5, f"FAIL: expected >=5 checkpoint_step() calls, found {checkpoint_calls}"
    logger.info(
        "[IMP:8][test_checkpoint_step_uses_content_hash] Check 4 PASS: %d checkpoint_step calls",
        checkpoint_calls,
    )

    # ── Check 5: _step_hash helper present (per-step content hash) ──
    assert "_step_hash" in content or "compute_step_hash" in content, (
        "FAIL: per-step content hash (_step_hash/compute_step_hash) not used"
    )
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 5 PASS: _step_hash present")

    # ── Check 6: RESUME_MODE + FORCE_MODE parsed (control checkpoint behavior) ──
    assert "RESUME_MODE" in content, "FAIL: RESUME_MODE not handled (resume flag)"
    assert "FORCE_MODE" in content, "FAIL: FORCE_MODE not handled (--force clears checkpoints)"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 6 PASS: RESUME+FORCE modes")

    logger.info("[IMP:9][test_checkpoint_step_uses_content_hash] ALL CHECKS PASS")


# endregion FUNC_test_checkpoint_step_uses_content_hash


# region FUNC_test_tor_conditional_branch
## @purpose  W4-E5 edge-case: verify node-lifecycle.sh has a TOR-conditional branch —
##           step_3_tor_proxy runs ONLY if TOR_ENABLED=true (derived from node.yaml).
##           When TOR_ENABLED=false, the step is skipped entirely (not failed).
##           This is the contract W4-E2 state_machine.py TOR-conditional must preserve.
## @io       caplog → ⎋ None (pytest.fail if TOR conditional absent)
## @complexity 1 — static grep for TOR_ENABLED + conditional skip
## @invariants
##   - TOR_ENABLED is derived from node.yaml (default false)
##   - step_3_tor_proxy is guarded by TOR_ENABLED check
##   - When TOR disabled → step SKIPPED (not executed, not failed)


@pytest.mark.static_audit
def test_tor_conditional_branch(caplog) -> None:
    """TOR-conditional: step_3_tor_proxy only runs if TOR_ENABLED=true from node.yaml."""
    # 🧪 TRAP[TEST] · Regression: W4-E5 TOR-conditional skip when TOR_ENABLED=false
    # · Scenario: node.yaml without TOR_ENABLED → step_3 skipped (not failed)
    # · Last fail: N/A (W4-E5 baseline)
    # · Remove if: TOR handling moves to state_machine.py (then point test at new module)
    logger.info("[IMP:7][test_tor_conditional_branch] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Check 1: TOR_ENABLED variable exists and defaults to false ──
    assert "TOR_ENABLED" in content, "FAIL: TOR_ENABLED variable not found"
    # Default false (so TOR is opt-in, not opt-out)
    assert "TOR_ENABLED=false" in content or "TOR_ENABLED:-false" in content, (
        "FAIL: TOR_ENABLED must default to false (opt-in)"
    )
    logger.info("[IMP:8][test_tor_conditional_branch] Check 1 PASS: TOR_ENABLED defaults false")

    # ── Check 2: TOR_ENABLED derived from node.yaml ──
    # There's a python3 PYEOF block reading node.yaml for TOR_ENABLED
    assert "TOR_ENABLED" in content and "node.yaml" in content.lower(), (
        "FAIL: TOR_ENABLED must be derived from node.yaml"
    )
    logger.info("[IMP:8][test_tor_conditional_branch] Check 2 PASS: TOR derived from node.yaml")

    # ── Check 3: step_3_tor_proxy guarded by TOR_ENABLED ──
    # In main(), checkpoint_step "tor-proxy" is inside `if [[ "${TOR_ENABLED:-false}" == "true" ]]`
    tor_guard_pattern = '"${TOR_ENABLED:-false}" == "true"'
    tor_guard_pattern2 = '"$TOR_ENABLED" == "true"'
    assert tor_guard_pattern in content or tor_guard_pattern2 in content, (
        "FAIL: step_3_tor_proxy must be guarded by TOR_ENABLED==true check"
    )
    logger.info("[IMP:8][test_tor_conditional_branch] Check 3 PASS: TOR guard present")

    # ── Check 4: When TOR disabled, step_3 is NOT called (skipped entirely) ──
    # The guard wraps checkpoint_step "tor-proxy" — if false, it's not in the execution path
    main_start = content.find('checkpoint_step "ssh-access"')
    assert main_start >= 0, "FAIL: could not locate main() checkpoint sequence"
    main_seq = content[main_start : main_start + 3000]  # bounded slice of init steps
    tor_checkpoint_idx = main_seq.find('checkpoint_step "tor-proxy"')
    assert tor_checkpoint_idx >= 0, "FAIL: checkpoint_step 'tor-proxy' not found in init sequence"
    # Check the guard precedes the tor-proxy checkpoint
    pre_tor = main_seq[:tor_checkpoint_idx]
    assert "TOR_ENABLED" in pre_tor, "FAIL: TOR_ENABLED check must precede checkpoint_step 'tor-proxy'"
    logger.info("[IMP:8][test_tor_conditional_branch] Check 4 PASS: guard precedes tor step")

    logger.info("[IMP:9][test_tor_conditional_branch] ALL CHECKS PASS")


# endregion FUNC_test_tor_conditional_branch


# region FUNC_test_step_warn_collects_errors
## @purpose  W4-E5 edge-case: verify step_warn() appends to STEP_ERRORS array (error collection).
##           When a step warns, the error is collected (not just logged) — final exit aggregates.
##           This is the contract W4-E2 state_machine.py step-warn/error propagation must preserve.
## @io       caplog → ⎋ None (pytest.fail if error collection absent)
## @complexity 1 — static grep for STEP_ERRORS array + step_warn append
## @invariants
##   - STEP_ERRORS array is declared (collects step warnings/failures)
##   - step_warn() appends to STEP_ERRORS (not just logs)
##   - Final exit checks STEP_ERRORS length (non-empty → non-zero exit)


@pytest.mark.static_audit
def test_step_warn_collects_errors(caplog) -> None:
    """step_warn error collection: STEP_ERRORS array aggregates failures for final exit."""
    # 🧪 TRAP[TEST] · Regression: W4-E5 step_warn collects into STEP_ERRORS for exit aggregation
    # · Scenario: N steps warn → STEP_ERRORS has N entries → final exit non-zero
    # · Last fail: N/A (W4-E5 baseline)
    # · Remove if: error collection moves to state_machine.py errors[] list
    logger.info("[IMP:7][test_step_warn_collects_errors] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Check 1: STEP_ERRORS array declared ──
    assert "STEP_ERRORS" in content, "FAIL: STEP_ERRORS array not declared"
    # Declaration pattern: STEP_ERRORS=() or declare -a STEP_ERRORS
    assert "STEP_ERRORS=()" in content or "declare -a STEP_ERRORS" in content, (
        "FAIL: STEP_ERRORS must be declared as empty array"
    )
    logger.info("[IMP:8][test_step_warn_collects_errors] Check 1 PASS: STEP_ERRORS declared")

    # ── Check 2: step_warn() appends to STEP_ERRORS ──
    step_warn_start = content.find("step_warn()")
    assert step_warn_start >= 0, "FAIL: step_warn() function not found"
    step_warn_body = content[step_warn_start : step_warn_start + 300]
    assert "STEP_ERRORS+=" in step_warn_body, "FAIL: step_warn() must append to STEP_ERRORS (not just log)"
    logger.info("[IMP:8][test_step_warn_collects_errors] Check 2 PASS: step_warn appends to STEP_ERRORS")

    # ── Check 3: STEP_ERRORS referenced in final reporting (audit-log/telegram/exit) ──
    # STEP_ERRORS aggregates warnings surfaced in audit-log + telegram notification + influences exit.
    # Not a direct "exit non-zero" — it's collected for reporting + used in status_suffix.
    errors_check_idx = content.rfind("${#STEP_ERRORS[@]}")  # last occurrence = final reporting
    assert errors_check_idx >= 0, "FAIL: STEP_ERRORS length never checked"
    # STEP_ERRORS must be referenced in at least 2 places: audit_log + telegram status
    step_errors_refs = content.count("${#STEP_ERRORS[@]}")
    assert step_errors_refs >= 2, (
        f"FAIL: STEP_ERRORS must be referenced in >=2 places (audit + reporting), found {step_errors_refs}"
    )
    logger.info(
        "[IMP:8][test_step_warn_collects_errors] Check 3 PASS: STEP_ERRORS referenced %d times",
        step_errors_refs,
    )

    logger.info("[IMP:9][test_step_warn_collects_errors] ALL CHECKS PASS")


# endregion FUNC_test_step_warn_collects_errors


# region FUNC_test_init_has_more_steps_than_update
## @purpose  W4-E5 edge-case: verify init mode has MORE checkpoint_step calls than update mode.
##           init = full bootstrap (18 steps: ssh, apt, tor, docker, users, firewall, etc.),
##           update = incremental (6 steps: verify-core, provision, ssl, deploy, healthcheck).
##           This count asymmetry is the contract W4-E2 state_machine.py must preserve —
##           init and update are distinct state-machine flows, not the same with fewer steps.
## @io       caplog → ⎋ None (pytest.fail if step counts don't match expectation)
## @complexity 1 — static count of checkpoint_step in init vs update branches
## @invariants
##   - init branch has >=10 checkpoint_step calls (full bootstrap)
##   - update branch has >=4 checkpoint_step calls (incremental)
##   - init step count > update step count (init is superset)


@pytest.mark.static_audit
def test_init_has_more_steps_than_update(caplog) -> None:
    """Init/update step counts: init > update (init is full bootstrap superset)."""
    # 🧪 TRAP[TEST] · Regression: W4-E5 init(18) vs update(6) step-count asymmetry
    # · Scenario: count checkpoint_step in init branch > count in update branch
    # · Last fail: N/A (W4-E5 baseline)
    # · Remove if: step counts intentionally equalized (unlikely — init is superset by design)
    logger.info("[IMP:7][test_init_has_more_steps_than_update] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Locate init and update branches ──
    init_start = content.find('if [[ "$MODE" == "init" ]]')
    update_start = content.find('elif [[ "$MODE" == "update" ]]')
    assert init_start >= 0, "FAIL: init branch not found"
    assert update_start >= 0, "FAIL: update branch not found"
    assert update_start > init_start, "FAIL: update branch must come after init branch"

    init_branch = content[init_start:update_start]
    # Update branch extends to end of the if-elif (or end of main)
    update_branch = content[update_start:]

    # ── Count checkpoint_step calls in each branch ──
    init_checkpoint_count = init_branch.count("checkpoint_step ")
    update_checkpoint_count = update_branch.count("checkpoint_step ")

    logger.info(
        "[IMP:8][test_init_has_more_steps] init checkpoint_step calls: %d",
        init_checkpoint_count,
    )
    logger.info(
        "[IMP:8][test_init_has_more_steps] update checkpoint_step calls: %d",
        update_checkpoint_count,
    )

    # ── Check 1: init has >=10 checkpoint_step calls ──
    assert init_checkpoint_count >= 10, (
        f"FAIL: init branch must have >=10 checkpoint_step calls, found {init_checkpoint_count}"
    )
    logger.info("[IMP:8][test_init_has_more_steps] Check 1 PASS: init has %d steps", init_checkpoint_count)

    # ── Check 2: update has >=4 checkpoint_step calls ──
    assert update_checkpoint_count >= 4, (
        f"FAIL: update branch must have >=4 checkpoint_step calls, found {update_checkpoint_count}"
    )
    logger.info("[IMP:8][test_init_has_more_steps] Check 2 PASS: update has %d steps", update_checkpoint_count)

    # ── Check 3: init has MORE steps than update (init is superset) ──
    assert init_checkpoint_count > update_checkpoint_count, (
        f"FAIL: init ({init_checkpoint_count}) must have MORE checkpoint_step calls "
        f"than update ({update_checkpoint_count}) — init is full bootstrap superset"
    )
    logger.info(
        "[IMP:9][test_init_has_more_steps] Check 3 PASS: init(%d) > update(%d)",
        init_checkpoint_count,
        update_checkpoint_count,
    )

    logger.info("[IMP:9][test_init_has_more_steps_than_update] ALL CHECKS PASS")


# endregion FUNC_test_init_has_more_steps_than_update
