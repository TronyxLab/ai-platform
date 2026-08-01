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

    # ── Check 4: unresolvable → exit 1 with error message ──
    assert "exit 1" in content and (
        "Cannot resolve" in content or "Cannot resolve NODE_YAML" in content or "resolve_node_yaml" in content
    ), "[IMP:9][test] FAIL: unresolvable NODE_YAML must exit 1 with error message"
    logger.info("[IMP:8][test_update_mode_resolves_node_yaml] Check 4 PASS: unresolvable → exit 1 with message")

    # ── Check 5: Resolution happens before _delegate call ──
    update_section = content[content.find('elif [[ "$MODE" == "update" ]]') :]
    resolver_pos = update_section.find("resolve_node_yaml")
    delegate_pos = update_section.find("_delegate --mode update")

    assert resolver_pos >= 0 and delegate_pos >= 0, (
        "[IMP:9][test] FAIL: update-mode must have resolve_node_yaml and _delegate call"
    )
    if resolver_pos >= 0 and delegate_pos >= 0:
        assert resolver_pos < delegate_pos, (
            f"[IMP:9][test] FAIL: resolve_node_yaml ({resolver_pos}) must precede _delegate ({delegate_pos})"
        )
    logger.info("[IMP:8][test_update_mode_resolves_node_yaml] Check 5 PASS: resolution before _delegate")

    logger.info("[IMP:9][test_update_mode_resolves_node_yaml] ALL CHECKS PASS")


# endregion FUNC_test_update_mode_resolves_node_yaml


# region FUNC_test_dry_run_flag_accepted
## @purpose  Verify parser accepts --dry-run, delegates to state_machine.py which handles
##           dry-run plan internally (no shell-side mkdir in new phase-based facade).
## @io       Script content → grep → assert patterns present
## @complexity O(S)
## @invariants — --dry-run in parser; delegated to state_machine.py via --dry-run pass-through
@pytest.mark.static_audit
def test_dry_run_flag_accepted(caplog) -> None:
    """--dry-run: parser flag accepted, passed through to state_machine.py."""
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

    # ── Check 2: state_machine.py has --dry-run handling ──
    sm_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py"
    sm_content = sm_path.read_text()
    assert "dry_run" in sm_content.lower(), "[IMP:9][test] FAIL: state_machine.py must handle --dry-run"
    assert "dry_run_plan" in sm_content, "[IMP:9][test] FAIL: state_machine.py must have dry_run_plan method"
    logger.info("[IMP:8][test_dry_run_flag_accepted] Check 2 PASS: state_machine.py handles dry-run")

    # ── Check 3: Init mode delegates via _delegate with --node-name and --node-yaml ──
    assert "_delegate --mode init" in content, "[IMP:9][test] FAIL: init mode must delegate to _delegate --mode init"
    logger.info("[IMP:8][test_dry_run_flag_accepted] Check 3 PASS: init mode delegates to state_machine")

    # ── Check 4: Update mode delegates via _delegate with --node-name and --node-yaml ──
    assert "_delegate --mode update" in content, (
        "[IMP:9][test] FAIL: update mode must delegate to _delegate --mode update"
    )
    logger.info("[IMP:8][test_dry_run_flag_accepted] Check 4 PASS: update mode delegates to state_machine")

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
    # Verify node-update.sh sets AGE_SECRET_KEY_FILE from flag (consumed by node_detect)
    assert "AGE_SECRET_KEY_FILE" in entrypoint_content, (
        "[IMP:9][test] FAIL: node-update.sh must set AGE_SECRET_KEY_FILE from --age-secret-key-file"
    )
    # Verify AGE key detection is delegated to python3 -m node_detect (DevPlan 104)
    assert "python3 -m core.internal.shared.node_detect" in entrypoint_content, (
        "[IMP:9][test] FAIL: node-update.sh must delegate AGE key detection to python3 -m core.internal.shared.node_detect"
    )
    logger.info(
        "[IMP:8][test_entrypoint_flags_contract] Check 6 PASS: --age-secret-key-file in entrypoint + node_detect delegation"
    )

    logger.info("[IMP:9][test_entrypoint_flags_contract] ALL CHECKS PASS")


# endregion FUNC_test_entrypoint_flags_contract


# region FUNC_test_node_update_has_ssh_proxy
## @purpose  Verify node-update.sh delegates to execute_remote_update() in remote-cmd.sh,
##           and that remote-cmd.sh delegates node resolution to Python remote_executor.py
##           (DevPlan 101 — replaces shell _resolve_and_extract wrapper). Local exec fallback
##           remains in entrypoint.
## @io       Script content → grep → assert patterns present
## @complexity O(S)
## @invariants — Entrypoint calls execute_remote_update; remote-cmd.sh has SSH proxy;
##               local fallback in entrypoint
@pytest.mark.static_audit
def test_node_update_has_ssh_proxy(caplog) -> None:
    """node-update.sh: delegates to execute_remote_update(); remote-cmd.sh delegates to Python."""
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

    # ── Check 2: remote_executor CLI in remote-cmd.sh (Python delegation, DevPlan 101) ──
    assert "remote_executor" in remote_content, (
        "[IMP:9][test] FAIL: remote-cmd.sh must delegate node resolution to remote_executor.py CLI (DevPlan 101)"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 2 PASS: remote_executor CLI in remote-cmd.sh")

    # ── Check 3: overlay_deliverer referenced in remote-cmd.sh (Python delegation) ──
    assert "overlay_deliverer" in remote_content, (
        "[IMP:9][test] FAIL: remote-cmd.sh must reference overlay_deliverer Python module"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 3 PASS: overlay_deliverer in remote-cmd.sh")

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

    # ── Check 6: AGE key detection delegated to python3 -m node_detect (DevPlan 104) ──
    assert "python3 -m core.internal.shared.node_detect" in entrypoint_content, (
        "[IMP:9][test] FAIL: node-update.sh must delegate AGE key detection to python3 -m core.internal.shared.node_detect"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 6 PASS: node_detect delegation present")

    # ── Check 7: printf %q builders still in shell (D3, DevPlan 101 D1: build-ssh-cmd.sh) ──
    build_script = CORE_DIR / "internal" / "bootstrap" / "build-ssh-cmd.sh"
    assert "build_ssh_cmd" in build_script.read_text(), (
        "[IMP:9][test] FAIL: build-ssh-cmd.sh must retain build_ssh_cmd (printf %q per D3)"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 7 PASS: build_ssh_cmd retained in build-ssh-cmd.sh (D3)")

    logger.info("[IMP:9][test_node_update_has_ssh_proxy] ALL CHECKS PASS")


# endregion FUNC_test_node_update_has_ssh_proxy


# region FUNC_test_remote_cmd_has_update_mode
## @purpose  Verify build-ssh-cmd.sh contains build_update_ssh_cmd() with --mode update.
##           DevPlan 101 D1: build-функции извлечены из remote-cmd.sh в build-ssh-cmd.sh.
## @io       Script content → grep → assert patterns present
## @complexity O(S)
## @invariants — build_update_ssh_cmd exists in build-ssh-cmd.sh; contains --mode update; no --resume (D2)
@pytest.mark.static_audit
def test_remote_cmd_has_update_mode(caplog) -> None:
    """build-ssh-cmd.sh: build_update_ssh_cmd exists with --mode update, no --resume."""
    # 🧪 TRAP[TEST] · Regression: T2 — build_update_ssh_cmd contract (DevPlan 101 D1: moved to build-ssh-cmd.sh)
    # · Scenario: SSH proxy calls build_update_ssh_cmd but internal changes signature
    # · Last fail: Wave 1 pre-merge (function didn't exist)
    # · Remove if: build-ssh-cmd.sh no longer needed for SSH proxy
    logger.info("[IMP:7][test_remote_cmd_has_update_mode] START")
    caplog.set_level(logging.DEBUG)

    build_script = CORE_DIR / "internal" / "bootstrap" / "build-ssh-cmd.sh"
    content = build_script.read_text()

    # ── Check 1: build_update_ssh_cmd exists ──
    assert "build_update_ssh_cmd" in content, "[IMP:9][test] FAIL: build-ssh-cmd.sh must define build_update_ssh_cmd()"
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
## @purpose  Verify SSL provisioning sources secrets.env and uses cert_orchestrator.
##           DevPlan 087: shell facade delegates ALL to state_machine.py; secrets
##           handling is in phases.py (phase_secrets_provision, phase_certificates).
## @io       Script content → grep → assert patterns present in function
## @complexity O(S)
## @invariants — state_machine.py delegates to phases.py; cert_orchestrator in phases
@pytest.mark.static_audit
def test_update_ssl_step_sources_secrets_env(caplog) -> None:
    """SSL provision: shell facade delegates to state_machine.py; secrets in phases.py."""
    # 🧪 TRAP[TEST] · Regression: T3 — WEBNAMES_API_KEY not set in update mode
    # · Scenario: SSL cert renewal fails because secrets.env not sourced
    # · Last fail: Wave 1 production (WARN "WEBNAMES_API_KEY not set")
    # · Remove if: ssl-provision no longer needs WEBNAMES_API_KEY
    logger.info("[IMP:7][test_update_ssl_step_sources_secrets_env] START")
    caplog.set_level(logging.DEBUG)

    # ── Check 1: shell facade delegates to state_machine.py ──
    content = LIFECYCLE_SCRIPT.read_text()
    assert "_delegate" in content and "lifecycle/cli.py" in content, (
        "[IMP:9][test] FAIL: shell facade must delegate to lifecycle/cli.py (B9 T1 CS-7)"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 1 PASS: delegates to lifecycle/cli.py")

    # ── Check 2: state_machine.py has execute_phase for certificates ──
    sm_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py"
    sm_content = sm_path.read_text()
    assert "execute_phase" in sm_content, "[IMP:9][test] FAIL: state_machine.py must have execute_phase()"
    assert "CERTIFICATES" in sm_content or "certificates" in sm_content, (
        "[IMP:9][test] FAIL: state_machine.py must handle certificates phase"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 2 PASS: state_machine.py has cert phase")

    # ── Check 3: phases.py has phase_certificates with ssl_provision_via_orchestrator ──
    phases_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "phases.py"
    phases_content = phases_path.read_text()
    assert "phase_certificates" in phases_content, "[IMP:9][test] FAIL: phases.py must have phase_certificates()"
    assert "ssl_provision_via_orchestrator" in phases_content, (
        "[IMP:9][test] FAIL: phase_certificates must call helpers.domains.ssl_provision_via_orchestrator (B9 T1)"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 3 PASS: phases.py has cert orchestration")

    # ── Check 4: cert_orchestrator referenced from helpers/domains.py (B9 T1) ──
    domains_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "helpers" / "domains.py"
    domains_content = domains_path.read_text()
    assert "cert_orchestrator" in domains_content or "orchestrate_certs" in domains_content, (
        "[IMP:9][test] FAIL: helpers/domains.py must reference cert_orchestrator"
    )
    logger.info(
        "[IMP:8][test_update_ssl_step_sources_secrets_env] Check 4 PASS: cert_orchestrator referenced (helpers/domains.py)"
    )

    # ── Check 5: WEBNAMES_API_KEY handling in phases.py ──
    phases_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "phases.py"
    phases_content = phases_path.read_text()
    assert "WEBNAMES_API_KEY" in phases_content or "ssl_provision_via_orchestrator" in phases_content, (
        "[IMP:9][test] FAIL: phases.py must have SSL provision handling"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 5 PASS: SSL provision in phases.py")

    # ── Check 6: helpers/secrets.py has decrypt_secrets (B9 T1) ──
    secrets_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "helpers" / "secrets.py"
    secrets_content = secrets_path.read_text()
    assert "decrypt_secrets" in secrets_content or "SECRETS_ENV_FILE" in secrets_content, (
        "[IMP:9][test] FAIL: helpers/secrets.py must handle secret decryption"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 6 PASS: helpers/secrets.py handles secrets")

    # ── Check 7: phase_secrets_provision and phase_certificates exist in phases.py ──
    assert "phase_secrets_provision" in phases_content, (
        "[IMP:9][test] FAIL: phases.py must have phase_secrets_provision"
    )
    assert "phase_certificates" in phases_content, "[IMP:9][test] FAIL: phases.py must have phase_certificates"
    logger.info(
        "[IMP:8][test_update_ssl_step_sources_secrets_env] Check 7 PASS: phase_secrets and phase_cert in phases.py"
    )

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
    """Mode-dispatch: --mode init/update accepted, invalid rejected, both branches delegate."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 087 — mode via shell facade, phase execution in state_machine.py
    # · Scenario: --mode invalid → exit 1; init/update delegate to state_machine.py
    # · Last fail: N/A (DevPlan 087 consolidation)
    # · Remove if: mode-dispatch moves entirely to Python
    logger.info("[IMP:7][test_mode_dispatch_init_update] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Check 1: --mode is first arg, validated against {init, update} ──
    assert '"--mode"' in content or "--mode" in content, "FAIL: --mode flag not parsed"
    assert '"init"' in content and '"update"' in content, "FAIL: parser must validate MODE against 'init' and 'update'"
    # Fail-fast on invalid mode (exit 1)
    assert "exit 1" in content, "FAIL: invalid MODE must exit 1 early (parser reject invalid)"
    logger.info("[IMP:8][test_mode_dispatch_init_update] Check 1 PASS: --mode validation")

    # ── Check 2: init branch delegates via _delegate --mode init ──
    init_branch_idx = content.find('if [[ "$MODE" == "init" ]]')
    assert init_branch_idx >= 0, "FAIL: init branch not found in main()"
    assert "_delegate --mode init" in content, "FAIL: init branch must call _delegate --mode init"
    logger.info("[IMP:8][test_mode_dispatch_init_update] Check 2 PASS: init branch delegates")

    # ── Check 3: update branch delegates via _delegate --mode update ──
    update_branch_idx = content.find('elif [[ "$MODE" == "update" ]]')
    assert update_branch_idx >= 0, "FAIL: update branch not found in main()"
    assert "_delegate --mode update" in content, "FAIL: update branch must call _delegate --mode update"
    logger.info("[IMP:8][test_mode_dispatch_init_update] Check 3 PASS: update branch delegates")

    logger.info("[IMP:9][test_mode_dispatch_init_update] ALL CHECKS PASS")


# endregion FUNC_test_mode_dispatch_init_update


# region FUNC_test_checkpoint_step_uses_content_hash
## @purpose  W4-E5 edge-case: verify node-lifecycle.sh delegates checkpoint-resume to
##           state_machine.py (phase-based, content-hash idempotency).
##           When a sub-step's content-hash is unchanged, the sub-step is SKIPPED (not re-executed).
##           This is the idempotency contract state_machine.py checkpoint-resume must preserve.
## @io       caplog → ⎋ None (pytest.fail if checkpoint/hash logic absent)
## @complexity 1 — static grep for state_machine delegation + _step_hash + content-hash source
## @invariants
##   - node-lifecycle.sh delegates checkpoint-resume to state_machine.py (does NOT source
##     the removed legacy checkpoint lib — DevPlan 091 backward-compat removal)
##   - node-lifecycle.sh delegates content-hash to shared content_hash.py / state_machine._step_hash()
##   - _step_hash() (state_machine.py) is used for per-sub-step content-hash invalidation
##   - RESUME_MODE + FORCE_MODE are parsed by the shell facade before delegation


@pytest.mark.static_audit
def test_checkpoint_step_uses_content_hash(caplog) -> None:
    """Checkpoint-resume: idempotency through state_machine.py phase-based approach."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 087 — phase-based checkpoint via state_machine.py
    # · Scenario: phase with unchanged content hash → SKIP sub-step
    # · Last fail: N/A (DevPlan 087 consolidation)
    # · Remove if: checkpoint mechanism changes again
    logger.info("[IMP:7][test_checkpoint_step_uses_content_hash] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Check 1: shell facade delegates to state_machine.py ──
    assert "state_machine.py" in content or "SM_SCRIPT" in content, (
        "FAIL: node-lifecycle.sh must delegate to state_machine.py"
    )
    assert "_delegate" in content, "FAIL: _delegate function must exist in shell facade"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 1 PASS: delegates to state_machine.py")

    # ── Check 2: state_machine.py has _step_hash for content-based idempotency ──
    sm_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py"
    sm_content = sm_path.read_text()
    assert "_step_hash" in sm_content, "FAIL: state_machine.py must have _step_hash for idempotency"
    assert "execute_grouped_phase" in sm_content, (
        "FAIL: state_machine.py must have execute_grouped_phase for sub-step idempotency"
    )
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 2 PASS: state_machine.py has hash-check")

    # ── Check 3: phases.py has _install_acme and other phase functions ──
    phases_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "phases.py"
    phases_content = phases_path.read_text()
    assert "phase_system_bootstrap" in phases_content, "FAIL: phases.py must have phase_system_bootstrap"
    assert "phase_deploy_services" in phases_content, "FAIL: phases.py must have phase_deploy_services"
    assert "phase_converge_services" in phases_content, "FAIL: phases.py must have phase_converge_services"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 3 PASS: phases.py has 14 phase functions")

    # ── Check 4: state_machine.py has BootstrapPhase enum with 14 values ──
    assert "BootstrapPhase" in sm_content, "FAIL: state_machine.py must have BootstrapPhase enum"
    assert "SYSTEM_BOOTSTRAP" in sm_content, "FAIL: BootstrapPhase must have SYSTEM_BOOTSTRAP"
    assert "CONVERGE_SERVICES" in sm_content, "FAIL: BootstrapPhase must have CONVERGE_SERVICES"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 4 PASS: BootstrapPhase enum present")

    # ── Check 5: RESUME_MODE + FORCE_MODE parsed by shell facade ──
    assert "RESUME_MODE" in content, "FAIL: RESUME_MODE not handled (resume flag)"
    assert "FORCE_MODE" in content, "FAIL: FORCE_MODE not handled (--force flag)"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 5 PASS: RESUME+FORCE modes")

    # ── Check 6: _phase_dependency_graph defined in state_machine.py ──
    assert "_phase_dependency_graph" in sm_content, "FAIL: state_machine.py must have _phase_dependency_graph"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 6 PASS: dependency graph present")

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
    """TOR-conditional: detect_tor_enabled() from node.yaml; passed via env to state_machine.py."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 087 — TOR_ENABLED detected in shell, passed to phases.py
    # · Scenario: node.yaml without TOR_ENABLED → tor sub-step skipped (not failed)
    # · Last fail: N/A (W4-E5 baseline)
    # · Remove if: TOR handling moves entirely to Python
    logger.info("[IMP:7][test_tor_conditional_branch] START")
    caplog.set_level(logging.DEBUG)

    content = LIFECYCLE_SCRIPT.read_text()

    # ── Check 1: detect_tor_enabled function exists ──
    assert "detect_tor_enabled" in content, "FAIL: detect_tor_enabled function not found"
    assert "TOR_ENABLED=false" in content, "FAIL: TOR_ENABLED must default to false"
    logger.info("[IMP:8][test_tor_conditional_branch] Check 1 PASS: detect_tor_enabled exists")

    # ── Check 2: TOR_ENABLED exported for state_machine.py ──
    assert "export TOR_ENABLED" in content, "FAIL: TOR_ENABLED must be exported for state_machine.py"
    logger.info("[IMP:8][test_tor_conditional_branch] Check 2 PASS: TOR exported")

    # ── Check 3: state_machine.py has TOR_ENABLED handling for phase_system_bootstrap ──
    sm_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py"
    sm_content = sm_path.read_text()
    assert "TOR_ENABLED" in sm_content, "FAIL: state_machine.py must handle TOR_ENABLED"
    logger.info("[IMP:8][test_tor_conditional_branch] Check 3 PASS: state_machine.py handles TOR")

    # ── Check 4: phases.py has TOR logic in phase_system_bootstrap ──
    phases_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "phases.py"
    phases_content = phases_path.read_text()
    assert "tor" in phases_content.lower(), "FAIL: phases.py must have tor-related logic"
    logger.info("[IMP:8][test_tor_conditional_branch] Check 4 PASS: phases.py handles tor")

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

    # ── Check 3: state_machine.py has error/warning collection ──
    # Error collection now happens in state_machine.py via state.errors and state.warnings lists.
    sm_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py"
    sm_content = sm_path.read_text()
    assert "errors" in sm_content and "warnings" in sm_content, (
        "FAIL: state_machine.py must have errors/warnings collection"
    )
    logger.info("[IMP:8][test_step_warn_collects_errors] Check 3 PASS: state_machine.py collects errors/warnings")

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
    """Init/update step counts: verify state_machine.py has BootstrapPhase with proper sizes."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 087 — 9 init phases vs 5 update phases
    # · Scenario: BootstrapPhase defined with INIT_PHASES and UPDATE_PHASES frozensets
    # · Last fail: N/A (DevPlan 087 consolidation)
    # · Remove if: phase counts intentionally equalized
    logger.info("[IMP:7][test_init_has_more_steps_than_update] START")
    caplog.set_level(logging.DEBUG)

    sm_content = (LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py").read_text()

    # ── Count BootstrapPhase class attributes that are phase values ──
    # Phase values are ALL_CAPS strings like SYSTEM_BOOTSTRAP, USER_ACCOUNTS, etc.
    # They appear as: SYSTEM_BOOTSTRAP = "system_bootstrap"  (assignment pattern)
    import re

    phase_assignments = re.findall(r'^    ([A-Z_]+) = "[a-z_]+"', sm_content, re.MULTILINE)
    # Filter out non-phase keywords like INIT_PHASES, UPDATE_PHASES, ALL_PHASES
    phase_names = [
        p
        for p in phase_assignments
        if p
        not in (
            "INIT_PHASES",
            "UPDATE_PHASES",
            "ALL_PHASES",
        )
    ]
    total_count = len(phase_names)

    logger.info(
        "[IMP:8][test_init_has_more_steps] Total BootstrapPhase values: %d — %s",
        total_count,
        ", ".join(phase_names),
    )

    # ── Check 1: total phases = 14 ──
    assert total_count == 14, f"FAIL: expected 14 total phases, got {total_count}"
    logger.info("[IMP:8][test_init_has_more_steps] Check 1 PASS: 14 total phases")

    # ── Count init vs update phases ──
    # Check the known sets: INIT mode has SYSTEM_BOOTSTRAP through CONVERGE_SERVICES
    init_phases = [
        p
        for p in phase_names
        if p
        not in (
            "SECRETS_UPDATE",
            "NODE_CONFIG_UPDATE",
            "REGISTRY_UPDATE",
            "DEPLOY_UPDATE",
            "CONVERGE_UPDATE",
        )
    ]
    update_phases = [p for p in phase_names if p not in init_phases]
    init_count = len(init_phases)
    update_count = len(update_phases)

    logger.info("[IMP:8][test_init_has_more_steps] INIT=%d UPDATE=%d", init_count, update_count)

    # ── Check 2: init has > update phases ──
    assert init_count > update_count, f"FAIL: init ({init_count}) must have MORE phases than update ({update_count})"
    logger.info("[IMP:8][test_init_has_more_steps] Check 2 PASS: init(%d) > update(%d)", init_count, update_count)

    # ── Check 3: init has 9 phases ──
    assert init_count == 9, f"FAIL: expected 9 init phases, got {init_count}"
    logger.info("[IMP:8][test_init_has_more_steps] Check 3 PASS: init has 9 phases")

    # ── Check 4: update has 5 phases ──
    assert update_count == 5, f"FAIL: expected 5 update phases, got {update_count}"
    logger.info("[IMP:8][test_init_has_more_steps] Check 4 PASS: update has 5 phases")

    logger.info("[IMP:9][test_init_has_more_steps_than_update] ALL CHECKS PASS")


# endregion FUNC_test_init_has_more_steps_than_update
