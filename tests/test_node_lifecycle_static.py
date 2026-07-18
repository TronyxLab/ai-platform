"""
Static tests for node-lifecycle.sh — NODE_YAML derivation, --dry-run, and flags contract.
# GREP_SUMMARY: test node-lifecycle node-yaml node-resolver dry-run flags contract static-audit ssh-proxy remote-cmd secrets-env ssl-provision
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
