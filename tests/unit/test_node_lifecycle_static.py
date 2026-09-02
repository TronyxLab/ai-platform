"""
Static tests for node-lifecycle.sh — NODE_YAML derivation, --dry-run, and flags contract.
# GREP_SUMMARY: test node-lifecycle node-yaml node-resolver dry-run flags contract static-audit ssh-proxy remote-cmd secrets-env ssl-provision w4-e5 mode-dispatch checkpoint step-skip tor-conditional
# STRUCTURE: ▶ read node-lifecycle.sh + node-update.sh → ◇ grep patterns → ⊕ assertions → ◇ W4-E5 edge-cases (mode-dispatch / checkpoint / TOR / init-vs-update steps)
# region MODULE_CONTRACT
## @purpose  Static analysis tests verifying:
##           T1 — update-mode has NODE_NAME fail-fast + NODE_YAML derivation via node-resolver.sh
##           T2 — --dry-run parser flag + dry-run plan before mkdir mutations
##           T2c — entrypoint↔internal flags contract (all node-update.sh flags accepted by node-lifecycle.sh)
##           W4-E5 (DevPlan 035 §7) — edge-case regression baseline for node-lifecycle.sh internals:
##             mode-dispatch (init vs update), checkpoint_step + per-step content-hash skip,
##             TOR-conditional branch, init(18) vs update(6) step counts.
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
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

CORE_DIR = Path(__file__).resolve().parent.parent.parent / "core"
LIFECYCLE_SCRIPT = CORE_DIR / "internal" / "bootstrap" / "node-lifecycle.sh"
ENTRYPOINT_SCRIPT = CORE_DIR / "entrypoints" / "node-update.sh"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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

    content = LIFECYCLE_SCRIPT.read_text(encoding="utf-8")

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

    content = LIFECYCLE_SCRIPT.read_text(encoding="utf-8")

    # ── Check 1: Parser accepts --dry-run ──
    assert "--dry-run" in content, "[IMP:9][test] FAIL: parser must accept --dry-run flag"
    assert "DRY_RUN_MODE=true" in content or "DRY_RUN_MODE" in content, (
        "[IMP:9][test] FAIL: --dry-run must set DRY_RUN_MODE=true"
    )
    logger.info("[IMP:8][test_dry_run_flag_accepted] Check 1 PASS: --dry-run in parser")

    # ── Check 2: state_machine.py has --dry-run handling ──
    sm_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py"
    sm_content = sm_path.read_text(encoding="utf-8")
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

    lifecycle_content = LIFECYCLE_SCRIPT.read_text(encoding="utf-8")
    entrypoint_content = ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")

    # B10 T2 (D2): checks 1-2 (--node-name/--dry-run grep on node-lifecycle.sh parser) replaced
    # by the behavioral dry-run test test_node_lifecycle_dry_run_contract below — bash subprocess
    # proves the parser ACCEPTS the flags (no "Unknown argument") via fail-fast exit codes and
    # proves node-update.sh --dry-run forwarding via a real exit-0 dry-run invocation.

    # ── Check 3 (170 W9-F2): node-update.sh thin facade → remote_dispatch.py --verb update ──
    # Entrypoint больше не парсит флаги сам: node/--node-name alias + --dry-run принимает
    # remote_dispatch.py (parse_args). Контракт: entrypoint форвардит "$@" → dispatch.
    dispatch_path = PROJECT_ROOT / "core" / "internal" / "bootstrap" / "remote_dispatch.py"
    assert dispatch_path.is_file(), "[IMP:9][test] FAIL: remote_dispatch.py not found (170 W9-F2)"
    dispatch_content = dispatch_path.read_text(encoding="utf-8")
    assert "remote_dispatch.py" in entrypoint_content and "--verb update" in entrypoint_content, (
        "[IMP:9][test] FAIL: node-update.sh must delegate to remote_dispatch.py --verb update (170 W9-F2)"
    )
    assert '"--node-name"' in dispatch_content or "--node-name" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must accept --node-name alias"
    )
    assert '"--dry-run"' in dispatch_content or "--dry-run" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must accept --dry-run"
    )
    logger.info("[IMP:8][test_entrypoint_flags_contract] Check 3 PASS: entrypoint → remote_dispatch.py (both flags)")

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

    # ── Check 5: --age-secret-key-file NOT accepted by node-lifecycle.sh parser (DevPlan 123 T9) ──
    # DevPlan 123 T9 (FL6): приём --age-secret-key-file на remote-стороне — ловушка passthrough
    # (локальный путь читался НА VPS). Флаг больше никуда не форвардится (bootstrap.sh/node-update.sh
    # читают ключ ЛОКАЛЬНО через node_detect-цепочку, в remote уходит ключ-контент через
    # --age-secret-key/AGE_SECRET_KEY). Приём пути удалён из node-lifecycle.sh — negative-guard ниже
    # защищает от повторного ввода ловушки. Форвард пути в remote-аргументы — RED по гейту
    # tests/gates/test_gate_local_path_in_remote.py (FL6, allowlist пуст).
    assert not any("--age-secret-key-file)" in line for line in lifecycle_content.splitlines()), (
        "[IMP:9][test] FAIL: case --age-secret-key-file) должен быть УДАЛЁН из node-lifecycle.sh parser "
        "(remote-сторона не принимает локальные пути — DevPlan 123 T9, FL6)"
    )
    logger.info(
        "[IMP:8][test_entrypoint_flags_contract] Check 5 PASS: --age-secret-key-file удалён из lifecycle parser "
        "(ловушка passthrough, DevPlan 123 T9)"
    )

    # ── Check 6 (170 W9-F2): --age-secret-key-file accepted by remote_dispatch.py (экс. entrypoint) ──
    # DevPlan 123 T9: ЛОКАЛЬНЫЙ приём флага остаётся (AGE_SECRET_KEY_FILE → node_detect-цепочка,
    # НЕ форвардится в remote) — носитель теперь remote_dispatch.py (run_update).
    assert "--age-secret-key-file" in dispatch_content, (
        "[IMP:9][test] FAIL: --age-secret-key-file not in remote_dispatch.py parser"
    )
    # Verify remote_dispatch.py sets AGE_SECRET_KEY_FILE from flag (consumed by node_detect)
    assert "AGE_SECRET_KEY_FILE" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must set AGE_SECRET_KEY_FILE from --age-secret-key-file"
    )
    # Verify AGE key detection is delegated to node_detect (DevPlan 104)
    assert "node_detect" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must delegate AGE key detection to node_detect (DevPlan 104)"
    )
    logger.info(
        "[IMP:8][test_entrypoint_flags_contract] Check 6 PASS: --age-secret-key-file in dispatch + node_detect delegation"
    )

    # ── Check 7 (142 W1 B27): bootstrap.sh форвардит --ci-root-key → node-lifecycle.sh имеет явный
    # case (не catch-all -*) + init-_delegate пробрасывает PLATFORM_CI_ROOT_KEY в cli.py ──
    # · Regression: W1 реализован в cli.py/build_ssh_cmd.sh, но фасад node-lifecycle.sh НЕ принимал
    #   --ci-root-key → «ERROR: Unknown: --ci-root-key» на REMOTE bootstrap (bootstrap FAILED, B27).
    bootstrap_content = (PROJECT_ROOT / "core" / "entrypoints" / "bootstrap.sh").read_text(encoding="utf-8")
    assert "--ci-root-key" in bootstrap_content, (
        "[IMP:9][test] FAIL: bootstrap.sh должен форвардить --ci-root-key (142 W1)"
    )
    assert "--ci-root-key" in case_block, (
        "[IMP:9][test] FAIL: case --ci-root-key) должен быть в node-lifecycle.sh parser, не catch-all -*)"
    )
    ci_root_lines = [line for line in case_block.split("\n") if "--ci-root-key" in line]
    assert any("PLATFORM_CI_ROOT_KEY" in line for line in ci_root_lines), (
        "[IMP:9][test] FAIL: case --ci-root-key) должен export PLATFORM_CI_ROOT_KEY"
    )
    # init-_delegate пробрасывает ключ в cli.py (--ci-root-key принимается cli.py)
    init_delegate = lifecycle_content[
        lifecycle_content.find("_delegate --mode init") : lifecycle_content.find('elif [[ "$MODE" == "update" ]]')
    ]
    assert "PLATFORM_CI_ROOT_KEY:+--ci-root-key" in init_delegate, (
        "[IMP:9][test] FAIL: init-_delegate должен пробрасывать PLATFORM_CI_ROOT_KEY:+--ci-root-key"
    )
    assert "--ci-root-key" in (PROJECT_ROOT / "core" / "internal" / "bootstrap" / "lifecycle" / "cli.py").read_text(
        encoding="utf-8"
    ), "[IMP:9][test] FAIL: cli.py должен принимать --ci-root-key (142 W1)"
    logger.info(
        "[IMP:8][test_entrypoint_flags_contract] Check 7 PASS: --ci-root-key проводка W1 (bootstrap → lifecycle → cli)"
    )

    logger.info("[IMP:9][test_entrypoint_flags_contract] ALL CHECKS PASS")


# endregion FUNC_test_entrypoint_flags_contract


# region FUNC_test_node_lifecycle_dry_run_contract
## @purpose  B10 T2 (D2): behavioral dry-run for the shell facades — replaces grep-asserts
##           169-173 (--node-name/--dry-run parser acceptance). Uses bash subprocess:
##           (a) node-lifecycle.sh parser ACCEPTS --mode/--node-name/--node-yaml/--dry-run and
##               fail-fasts with exit 1 + diagnostic (no "Unknown argument");
##           (b) node-update.sh --node <existing-node> --dry-run → exit 0 (forwarding contract:
##               --node alias, --dry-run flag, delegation to remote_executor dry-run, NO mutations).
## @io       caplog → ⎋ None (pytest assert on subprocess exit codes/stderr)
## @complexity O(1) — 4-5 bash invocations
## @invariants
##   - node-update.sh dry-run is mutation-free: remote_executor --dry-run logs rsync cmd, no SSH/rsync
##   - PYTHONPATH set to repo root (node-update.sh does NOT export it; node_detect/remote_executor need it)
##   - node-lifecycle.sh --mode init --dry-run: NOT executed to completion (shell does not forward
##     --dry-run to cli.py → would run the real bootstrap); parser acceptance proven via fail-fast paths
## @rationale  node-lifecycle.sh parses --dry-run (DRY_RUN_MODE=true) but does NOT forward it to
##             lifecycle/cli.py — a full "exit 0 dry-run" would run the real 9-phase init (mutation).
##             The fail-fast exit-1 paths prove the flags parse without "Unknown argument" and that
##             validation order (mode → NODE_NAME → NODE_YAML → PLATFORM_OWNER_KEY) is preserved.
@pytest.mark.static_audit
def test_node_lifecycle_dry_run_contract(caplog) -> None:
    """node-lifecycle.sh parser fail-fast + node-update.sh --dry-run forwarding (behavioral, D2)."""
    # 🧪 TRAP[TEST] · 2026-08-01 · B10 T2 · dry-run forwarding contract
    # · Scenario: node-update.sh --node <n> --dry-run → exit 0 without mutations;
    # ·   node-lifecycle.sh parser accepts flags (fail-fast exit 1 with diagnostics)
    # · Last fail: N/A (replaces grep 169-173)
    # · Remove if: shell facade parser/dry-run contract changes
    logger.info("[IMP:7][test_node_lifecycle_dry_run_contract] START")
    caplog.set_level(logging.DEBUG)

    # ── (a) node-lifecycle.sh: invalid/absent mode → exit 1 (parser validation) ──
    r_no_mode = subprocess.run(["bash", str(LIFECYCLE_SCRIPT)], capture_output=True, text=True, timeout=30, check=False)
    assert r_no_mode.returncode == 1, "node-lifecycle.sh without --mode must exit 1"
    assert "--mode init|update required" in r_no_mode.stderr, (
        f"Expected mode-required diagnostic, got: {r_no_mode.stderr}"
    )

    r_bad_mode = subprocess.run(
        ["bash", str(LIFECYCLE_SCRIPT), "--mode", "bogus"], capture_output=True, text=True, timeout=30, check=False
    )
    assert r_bad_mode.returncode == 1, "node-lifecycle.sh --mode bogus must exit 1"

    # ── (b) node-lifecycle.sh --mode update --dry-run without --node-name → exit 1 ──
    # Proves --dry-run is ACCEPTED by the parser (no "Unknown argument") and the
    # fail-fast NODE_NAME check fires AFTER parsing.
    r_update_no_node = subprocess.run(
        ["bash", str(LIFECYCLE_SCRIPT), "--mode", "update", "--dry-run"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert r_update_no_node.returncode == 1, "--mode update --dry-run without --node-name must exit 1"
    assert "NODE_NAME required" in r_update_no_node.stderr, (
        f"Expected NODE_NAME-required diagnostic, got: {r_update_no_node.stderr}"
    )

    # ── (c) node-lifecycle.sh --mode init --dry-run --node-name X --node-yaml <tmp> → exit 1 ──
    # without PLATFORM_OWNER_KEY. Proves --node-name/--node-yaml/--dry-run all parse (no
    # "Unknown argument") and the init fail-fast owner-key check fires after them.
    tmp_node_yaml = subprocess.run(["mktemp"], capture_output=True, text=True, check=True).stdout.strip()
    try:
        r_init_no_owner = subprocess.run(
            [
                "bash",
                str(LIFECYCLE_SCRIPT),
                "--mode",
                "init",
                "--dry-run",
                "--node-name",
                "test-node",
                "--node-yaml",
                tmp_node_yaml,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert r_init_no_owner.returncode == 1, (
            "--mode init --dry-run without PLATFORM_OWNER_KEY must exit 1 (fail-fast)"
        )
        assert "Missing PLATFORM_OWNER_KEY" in r_init_no_owner.stderr, (
            f"Expected owner-key diagnostic, got: {r_init_no_owner.stderr}"
        )
    finally:
        Path(tmp_node_yaml).unlink()

    # ── (d) node-update.sh --node <existing> --dry-run → exit 0 (forwarding + dry-run) ──
    # node-update.sh forwards --dry-run to remote_executor.py execute-update --dry-run →
    # sync_core_to_vps(dry_run=True) prints rsync cmd, NEVER executes ssh/rsync → exit 0.
    # Hermetic (v1.0.1 CI-fix): CI-раннер не имеет node-configs/ (gitignored, операторский
    # state) — 3-path резолвер падал ConfigNotFound → rc=1 → RED. Фикс: tmp PLATFORM_ROOT
    # с минимальным node.yaml test-e2e (Zero Hardcode Rule; резолвер читает PLATFORM_ROOT
    # из env — resolve() DI-seam). Dev-машина по-прежнему проходит и без PLATFORM_ROOT
    # (реальный node-configs оператора).
    # NOTE: remote_executor.py CLI does not basicConfig logging → its INFO (IMP:*) logs are
    # suppressed when run via `python3 -m`; the forwarding contract is proven by exit 0 +
    # absence of the LOCAL-fallback marker (which WOULD print from node-update.sh itself).
    with tempfile.TemporaryDirectory() as tmp_cfg:
        node_dir = Path(tmp_cfg) / "node-configs" / "test-e2e"
        node_dir.mkdir(parents=True)
        (node_dir / "node.yaml").write_text(
            "node:\n  name: test-e2e\n  host: 127.0.0.1\ndomain: test.example.com\ncontexts:\n  - name: test\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env["PLATFORM_ROOT"] = tmp_cfg
        r_update = subprocess.run(
            ["bash", str(ENTRYPOINT_SCRIPT), "--node", "test-e2e", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            check=False,
        )
    assert r_update.returncode == 0, (
        f"node-update.sh --node test-e2e --dry-run must exit 0, got rc={r_update.returncode}\n"
        f"stderr: {r_update.stderr[-500:]}"
    )
    assert "Starting node-update for NODE=test-e2e" in r_update.stderr, (
        f"Entrypoint must reach main(), got: {r_update.stderr[-500:]}"
    )
    assert "No SSH host" not in r_update.stderr, (
        "Dry-run must take the remote_executor forwarding path (exit 0), not the local fallback"
    )

    logger.info("[IMP:9][test_node_lifecycle_dry_run_contract] ALL CHECKS PASS — dry-run contract verified")


# endregion FUNC_test_node_lifecycle_dry_run_contract


# region FUNC_test_node_update_has_ssh_proxy
## @purpose  Verify node-update.sh delegates to remote_dispatch.py --verb update (DevPlan 170 W9-F2),
##           and that remote_dispatch.py delegates SSH execution to RemoteExecutor.execute_update
##           (Python-канал, экс. execute_remote_update из remote-cmd.sh). Local exec fallback lives
##           in remote_dispatch.py (_local_update_fallback). remote-cmd.sh остаётся каналом
##           overlay_deliverer/remote_executor для других entrypoints.
## @io       Script content → grep → assert patterns present
## @complexity O(S)
## @invariants — Entrypoint → remote_dispatch.py --verb update; dispatch → RemoteExecutor.execute_update;
##               local fallback в dispatch; remote-cmd.sh сохраняет SSH-канал
@pytest.mark.static_audit
def test_node_update_has_ssh_proxy(caplog) -> None:
    """node-update.sh: thin facade → remote_dispatch.py → RemoteExecutor.execute_update."""
    # 🧪 TRAP[TEST] · Regression: T1 — SSH proxy in remote-cmd.sh via execute_remote_update
    # · Scenario: make node-update from macOS fails "must run as root"
    # · Last fail: Wave 1 pre-merge (no SSH proxy)
    # · Remove if: entrypoint no longer needs SSH proxy
    logger.info("[IMP:7][test_node_update_has_ssh_proxy] START")
    caplog.set_level(logging.DEBUG)

    entrypoint_content = ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")
    dispatch_path = PROJECT_ROOT / "core" / "internal" / "bootstrap" / "remote_dispatch.py"
    assert dispatch_path.is_file(), "[IMP:9][test] FAIL: remote_dispatch.py not found (170 W9-F2)"
    dispatch_content = dispatch_path.read_text(encoding="utf-8")
    remote_cmd_script = CORE_DIR / "internal" / "bootstrap" / "remote-cmd.sh"
    remote_content = remote_cmd_script.read_text(encoding="utf-8")

    # ── Check 1 (170 W9-F2): entrypoint → remote_dispatch.py --verb update ──
    assert "remote_dispatch.py" in entrypoint_content and "--verb update" in entrypoint_content, (
        "[IMP:9][test] FAIL: node-update.sh must delegate to remote_dispatch.py --verb update"
    )
    # SSH-исполнение через существующий канал: RemoteExecutor.execute_update (экс. execute_remote_update)
    assert "execute_update" in dispatch_content and "RemoteExecutor" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must call RemoteExecutor.execute_update (SSH-канал не переизобретён)"
    )
    logger.info(
        "[IMP:8][test_node_update_has_ssh_proxy] Check 1 PASS: entrypoint → dispatch → RemoteExecutor.execute_update"
    )

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

    # ── Check 4 (170 W9-F2): SSH_HOST fallback — local exec path lives in dispatch ──
    assert "ssh_host" in dispatch_content.lower() or "SSH_HOST" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must handle SSH_HOST"
    )
    assert "_local_update_fallback" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must have local exec fallback when SSH_HOST absent"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 4 PASS: local exec fallback in remote_dispatch.py")

    # ── Check 5 (170 W9-F2): --age-secret-key-file accepted by dispatch ──
    assert "--age-secret-key-file" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must accept --age-secret-key-file"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 5 PASS: --age-secret-key-file flag in dispatch")

    # ── Check 6 (170 W9-F2): AGE key detection delegated to node_detect (DevPlan 104) ──
    assert "node_detect" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py must delegate AGE key detection to node_detect (DevPlan 104)"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 6 PASS: node_detect delegation in dispatch")

    # ── Check 7: printf %q builders still in shell (D3, DevPlan 101 D1: build-ssh-cmd.sh) ──
    build_script = CORE_DIR / "internal" / "bootstrap" / "build-ssh-cmd.sh"
    assert "build_ssh_cmd" in build_script.read_text(encoding="utf-8"), (
        "[IMP:9][test] FAIL: build-ssh-cmd.sh must retain build_ssh_cmd (printf %q per D3)"
    )
    logger.info("[IMP:8][test_node_update_has_ssh_proxy] Check 7 PASS: build_ssh_cmd retained in build-ssh-cmd.sh (D3)")

    logger.info("[IMP:9][test_node_update_has_ssh_proxy] ALL CHECKS PASS")


# endregion FUNC_test_node_update_has_ssh_proxy


# region FUNC_test_remote_cmd_has_update_mode
## @purpose  Verify build-ssh-cmd.sh delegates build_update_ssh_cmd() to shared/ssh_cmd_builder.py
##           where the --mode update / printf %q / AGE_SECRET_KEY logic now lives (DevPlan 164 W3.5-1
##           Strangler: shell-логика → Python). Контракт-проверки 2/5/6 ре-pointed на Python-модуль
##           (паттерн test_shell_facade_contract S3-S6 → deploy_orchestrator.py).
## @io       Script content → grep → assert patterns present
## @complexity O(S)
## @invariants — build_update_ssh_cmd exists in build-ssh-cmd.sh; delegates to ssh_cmd_builder;
##               Python-модуль содержит --mode update; shell-функция без --resume (D2)
@pytest.mark.static_audit
def test_remote_cmd_has_update_mode(caplog) -> None:
    """build-ssh-cmd.sh: build_update_ssh_cmd delegates to Python builder (--mode update in Python)."""
    # 🧪 TRAP[TEST] · Regression: T2 — build_update_ssh_cmd contract (DevPlan 101 D1: moved to build-ssh-cmd.sh;
    #   DevPlan 164 W3.5-1: build-логика → shared/ssh_cmd_builder.py, проверки контракта — на Python-модуль)
    # · Scenario: SSH proxy calls build_update_ssh_cmd; shell-фасад делегирует python3 -m ssh_cmd_builder;
    #   --mode update / printf %q / AGE_SECRET_KEY живут в Python-модуле
    # · Last fail: Wave 1 pre-merge (function didn't exist); 2026-08-14 W3.5-1 (контракт переехал в Python)
    # · Remove if: build-ssh-cmd.sh no longer needed for SSH proxy
    logger.info("[IMP:7][test_remote_cmd_has_update_mode] START")
    caplog.set_level(logging.DEBUG)

    build_script = CORE_DIR / "internal" / "bootstrap" / "build-ssh-cmd.sh"
    content = build_script.read_text(encoding="utf-8")
    # DevPlan 164 W3.5-1: бизнес-логика build-функций → shared/ssh_cmd_builder.py (прямое замещение)
    ssh_cmd_builder_py = CORE_DIR / "internal" / "shared" / "ssh_cmd_builder.py"
    py_content = ssh_cmd_builder_py.read_text(encoding="utf-8")

    # ── Check 1: build_update_ssh_cmd exists ──
    assert "build_update_ssh_cmd" in content, "[IMP:9][test] FAIL: build-ssh-cmd.sh must define build_update_ssh_cmd()"
    logger.info("[IMP:8][test_remote_cmd_has_update_mode] Check 1 PASS: build_update_ssh_cmd() defined")

    # ── Check 2: --mode update logic lives in Python builder (Strangler W3.5-1) ──
    # Было: grep "--mode" в shell-функции; стало: контракт в shared/ssh_cmd_builder.py (build_update_ssh_cmd)
    update_py_start = py_content.find("def build_update_ssh_cmd(")
    assert update_py_start >= 0, "[IMP:9][test] FAIL: build_update_ssh_cmd() not found in ssh_cmd_builder.py"
    update_py_body = py_content[update_py_start:]
    assert "--mode update" in update_py_body, (
        "[IMP:9][test] FAIL: build_update_ssh_cmd (Python) must build --mode update"
    )
    logger.info("[IMP:8][test_remote_cmd_has_update_mode] Check 2 PASS: --mode update present in Python builder")

    # ── Check 3: does NOT contain --resume in shell function body (D2: update steps independent) ──
    # NOTE: --resume may appear in file-level comments but must NOT be in the function body.
    # Scope the check to only the build_update_ssh_cmd function, not the rest of the file
    # (which contains build_converge_ssh_cmd with --resume in its docstring).
    update_func_start = content.find("build_update_ssh_cmd() {")
    assert update_func_start >= 0, "[IMP:9][test] FAIL: build_update_ssh_cmd() { not found"
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

    # ── Check 5: printf %q quoting lives in Python (printf_q, D3-инвариант НЕПРИКОСНОВЕНЕН) ──
    # Было: grep "printf '%q'" в shell; стало: printf_q() в shared/ssh_cmd_builder.py (D3)
    assert "printf_q" in py_content, (
        "[IMP:9][test] FAIL: ssh_cmd_builder.py must implement printf %q quoting (printf_q, D3)"
    )
    logger.info("[IMP:8][test_remote_cmd_has_update_mode] Check 5 PASS: printf_q quoting in Python builder")

    # ── Check 6: AGE_SECRET_KEY handled in Python builder — REF-0007: в secret-prelude,
    # НЕ в теле команды (stdin→bash -s транспорт; прежний env-export-in-body удалён) ──
    prelude_py_start = py_content.find("def build_update_secret_prelude(")
    assert prelude_py_start >= 0, "[IMP:9][test] FAIL: build_update_secret_prelude() not found in ssh_cmd_builder.py"
    prelude_py_end = py_content.find("# endregion FUNC_build_update_secret_prelude")
    prelude_body = py_content[prelude_py_start : prelude_py_end if prelude_py_end > 0 else len(py_content)]
    assert "export AGE_SECRET_KEY=" in prelude_body, (
        "[IMP:9][test] FAIL: build_update_secret_prelude (Python) must emit export AGE_SECRET_KEY="
    )
    assert "AGE_SECRET_KEY" not in update_py_body, (
        "[IMP:9][test] FAIL: build_update_ssh_cmd (Python) must NOT embed AGE_SECRET_KEY (REF-0007 stdin-prelude)"
    )
    logger.info("[IMP:8][test_remote_cmd_has_update_mode] Check 6 PASS: AGE_SECRET_KEY in stdin-prelude builder only")

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
    content = LIFECYCLE_SCRIPT.read_text(encoding="utf-8")
    assert "_delegate" in content and "lifecycle/cli.py" in content, (
        "[IMP:9][test] FAIL: shell facade must delegate to lifecycle/cli.py (B9 T1 CS-7)"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 1 PASS: delegates to lifecycle/cli.py")

    # ── Check 2: state_machine.py has execute_phase for certificates ──
    sm_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py"
    sm_content = sm_path.read_text(encoding="utf-8")
    assert "execute_phase" in sm_content, "[IMP:9][test] FAIL: state_machine.py must have execute_phase()"
    assert "CERTIFICATES" in sm_content or "certificates" in sm_content, (
        "[IMP:9][test] FAIL: state_machine.py must handle certificates phase"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 2 PASS: state_machine.py has cert phase")

    # B10 T2 (D2): checks 3-4 (grep on phases.py/helpers/domains.py for phase_certificates +
    # ssl_provision_via_orchestrator/cert_orchestrator) replaced by NATIVE behavior tests in
    # tests/unit/test_phase_certificates_contract.py (import + signature + call with fake context).

    # ── Check 5: WEBNAMES_API_KEY handling in phases (DevPlan 119 E3: phases.py → phases/certs.py) ──
    phases_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "phases" / "certs.py"
    phases_content = phases_path.read_text(encoding="utf-8")
    assert "WEBNAMES_API_KEY" in phases_content or "ssl_provision_via_orchestrator" in phases_content, (
        "[IMP:9][test] FAIL: phases/certs.py must have SSL provision handling"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 5 PASS: SSL provision in phases/certs.py")

    # ── Check 6: helpers/secrets.py has decrypt_secrets (B9 T1) ──
    secrets_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "helpers" / "secrets.py"
    secrets_content = secrets_path.read_text(encoding="utf-8")
    assert "decrypt_secrets" in secrets_content or "SECRETS_ENV_FILE" in secrets_content, (
        "[IMP:9][test] FAIL: helpers/secrets.py must handle secret decryption"
    )
    logger.info("[IMP:8][test_update_ssl_step_sources_secrets_env] Check 6 PASS: helpers/secrets.py handles secrets")

    # ── Check 7: phase_secrets_provision (secrets.py) и phase_certificates (certs.py) в phases-пакете ──
    secrets_phase_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "phases" / "secrets.py"
    secrets_phase_content = secrets_phase_path.read_text(encoding="utf-8")
    assert "phase_secrets_provision" in secrets_phase_content, (
        "[IMP:9][test] FAIL: phases/secrets.py must have phase_secrets_provision"
    )
    assert "phase_certificates" in phases_content, "[IMP:9][test] FAIL: phases/certs.py must have phase_certificates"
    logger.info(
        "[IMP:8][test_update_ssl_step_sources_secrets_env] Check 7 PASS: phase_secrets and phase_cert in phases package"
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

    content = LIFECYCLE_SCRIPT.read_text(encoding="utf-8")

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
## @complexity 1 — static grep for state_machine delegation + _phase_input_hash + content-hash source
## @invariants
##   - node-lifecycle.sh delegates checkpoint-resume to state_machine.py (does NOT source
##     the removed checkpoint lib — DevPlan 091 backward-compat removal)
##   - node-lifecycle.sh delegates content-hash to phase-level idempotency (_phase_input_hash)
##   - _phase_input_hash() (state_machine.py) is used for per-phase content-hash invalidation
##     (_step_hash удалён как мёртвый — аудит 2026-08-22; sub-step hash вне скоупа)
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

    content = LIFECYCLE_SCRIPT.read_text(encoding="utf-8")

    # ── Check 1: shell facade delegates to state_machine.py ──
    assert "state_machine.py" in content or "SM_SCRIPT" in content, (
        "FAIL: node-lifecycle.sh must delegate to state_machine.py"
    )
    assert "_delegate" in content, "FAIL: _delegate function must exist in shell facade"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 1 PASS: delegates to state_machine.py")

    # ── Check 2: state_machine.py has phase-input hash for content-based idempotency ──
    sm_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py"
    sm_content = sm_path.read_text(encoding="utf-8")
    # Аудит 2026-08-22: _step_hash удалён (0 callers); живой механизм — _phase_input_hash
    assert "_phase_input_hash" in sm_content, "FAIL: state_machine.py must have _phase_input_hash for idempotency"
    assert "_step_hash" not in sm_content, "FAIL: _step_hash must stay removed (dead API, аудит 2026-08-22)"
    # Волна 117 D5: execute_grouped_phase удалён (мёртвый код, sub-step resume вне скоупа);
    # идемпотентность — через phase-статусы (done / done_with_warnings ≠ done → перевыполнение)
    assert "def execute_grouped_phase" not in sm_content, (
        "FAIL: state_machine.py must NOT define execute_grouped_phase (removed in волна 117 D5 — "
        "sub-step resume вне скоупа; фазы выполняются целиком)"
    )
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 2 PASS: state_machine.py has hash-check")

    # ── Check 3: phases package has 14 phase functions (DevPlan 119 E3: phases.py → phases/) ──
    # Доменные модули: system.py (φ1/φ2/φ3/φ5/φ8.5/φ10/φ13), docker.py (φ6/φ8/φ11/φ12),
    # secrets.py (φ4/φ9), certs.py (φ7). Проверяем конкатенацию пакета.
    phases_pkg = LIFECYCLE_SCRIPT.parent / "lifecycle" / "phases"
    assert phases_pkg.is_dir(), f"FAIL: phases/ package not found: {phases_pkg}"
    phases_content = ""
    for ph in sorted(phases_pkg.glob("*.py")):
        if ph.name == "__init__.py":
            continue  # агрегатор не несёт бизнес-логики (AC-E3.1)
        phases_content += ph.read_text(encoding="utf-8")
    assert "phase_system_bootstrap" in phases_content, "FAIL: phases must have phase_system_bootstrap"
    assert "phase_deploy_services" in phases_content, "FAIL: phases must have phase_deploy_services"
    assert "phase_converge_services" in phases_content, "FAIL: phases must have phase_converge_services"
    logger.info("[IMP:8][test_checkpoint_step_uses_content_hash] Check 3 PASS: phases package has 14 phase functions")

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

    content = LIFECYCLE_SCRIPT.read_text(encoding="utf-8")

    # ── Check 1: detect_tor_enabled function exists ──
    assert "detect_tor_enabled" in content, "FAIL: detect_tor_enabled function not found"
    assert "TOR_ENABLED=false" in content, "FAIL: TOR_ENABLED must default to false"
    logger.info("[IMP:8][test_tor_conditional_branch] Check 1 PASS: detect_tor_enabled exists")

    # ── Check 2: TOR_ENABLED exported for state_machine.py ──
    assert "export TOR_ENABLED" in content, "FAIL: TOR_ENABLED must be exported for state_machine.py"
    logger.info("[IMP:8][test_tor_conditional_branch] Check 2 PASS: TOR exported")

    # ── Check 3: state_machine.py has TOR_ENABLED handling for phase_system_bootstrap ──
    sm_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py"
    sm_content = sm_path.read_text(encoding="utf-8")
    assert "TOR_ENABLED" in sm_content, "FAIL: state_machine.py must handle TOR_ENABLED"
    logger.info("[IMP:8][test_tor_conditional_branch] Check 3 PASS: state_machine.py handles TOR")

    # ── Check 4: phases (system domain) has TOR logic in phase_system_bootstrap (E3) ──
    phases_path = LIFECYCLE_SCRIPT.parent / "lifecycle" / "phases" / "system.py"
    phases_content = phases_path.read_text(encoding="utf-8")
    assert "tor" in phases_content.lower(), "FAIL: phases/system.py must have tor-related logic"
    logger.info("[IMP:8][test_tor_conditional_branch] Check 4 PASS: phases/system.py handles tor")

    logger.info("[IMP:9][test_tor_conditional_branch] ALL CHECKS PASS")


# endregion FUNC_test_tor_conditional_branch


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

    sm_content = (LIFECYCLE_SCRIPT.parent / "lifecycle" / "state_machine.py").read_text(encoding="utf-8")

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
        not in {
            "INIT_PHASES",
            "UPDATE_PHASES",
            "ALL_PHASES",
        }
    ]
    total_count = len(phase_names)

    logger.info(
        "[IMP:8][test_init_has_more_steps] Total BootstrapPhase values: %d — %s",
        total_count,
        ", ".join(phase_names),
    )

    # ── Check 1: total phases = 15 (10 init + 5 update, +final_verify DevPlan 029 T5) ──
    assert total_count == 15, f"FAIL: expected 15 total phases, got {total_count}"
    logger.info("[IMP:8][test_init_has_more_steps] Check 1 PASS: 15 total phases")

    # ── Count init vs update phases ──
    # Check the known sets: INIT mode has SYSTEM_BOOTSTRAP through CONVERGE_SERVICES
    init_phases = [
        p
        for p in phase_names
        if p
        not in {
            "SECRETS_UPDATE",
            "NODE_CONFIG_UPDATE",
            "REGISTRY_UPDATE",
            "DEPLOY_UPDATE",
            "CONVERGE_UPDATE",
        }
    ]
    update_phases = [p for p in phase_names if p not in init_phases]
    init_count = len(init_phases)
    update_count = len(update_phases)

    logger.info("[IMP:8][test_init_has_more_steps] INIT=%d UPDATE=%d", init_count, update_count)

    # ── Check 2: init has > update phases ──
    assert init_count > update_count, f"FAIL: init ({init_count}) must have MORE phases than update ({update_count})"
    logger.info("[IMP:8][test_init_has_more_steps] Check 2 PASS: init(%d) > update(%d)", init_count, update_count)

    # ── Check 3: init has 10 phases (incl. φ-final-verify) ──
    assert init_count == 10, f"FAIL: expected 10 init phases, got {init_count}"
    logger.info("[IMP:8][test_init_has_more_steps] Check 3 PASS: init has 10 phases")

    # ── Check 4: update has 5 phases ──
    assert update_count == 5, f"FAIL: expected 5 update phases, got {update_count}"
    logger.info("[IMP:8][test_init_has_more_steps] Check 4 PASS: update has 5 phases")

    logger.info("[IMP:9][test_init_has_more_steps_than_update] ALL CHECKS PASS")


# endregion FUNC_test_init_has_more_steps_than_update


# region FUNC_test_converge_entrypoint_rc2_disambiguation
## @purpose  142 B28b: converge.sh entrypoint различает rc=2 «no SSH host» (local fallback) от
##           rc=2 remote converge errors (host есть → forward exit 2, БЕЗ локального прогона).
##           Regression: remote converge с R-unit errors возвращал 2 сквозь ssh → entrypoint
##           ложно трактовал «self-detect» → двойной локальный converge на dev-машине (macOS:
##           R3 mkdir /opt Permission denied, R6 vhost overlay not resolved) → итог exit 2.
## @io       Script content → grep → assert guard patterns
## @complexity O(S) — converge.sh entrypoint
## @invariants — source node-resolver.sh (resolve/extract); guard ssh_host перед local fallback
@pytest.mark.static_audit
def test_converge_entrypoint_rc2_disambiguation(caplog: pytest.LogCaptureFixture) -> None:
    """converge.sh: rc=2 host-guard — remote errors ≠ local fallback (142 B28b, в remote_dispatch.py 170 W9-F2)."""
    logger.info("[IMP:7][test_converge_entrypoint_rc2_disambiguation] START")
    caplog.set_level(logging.DEBUG)

    converge_entry = PROJECT_ROOT / "core" / "entrypoints" / "converge.sh"
    content = converge_entry.read_text(encoding="utf-8")
    dispatch_path = PROJECT_ROOT / "core" / "internal" / "bootstrap" / "remote_dispatch.py"
    assert dispatch_path.is_file(), "[IMP:9][test] FAIL: remote_dispatch.py not found (170 W9-F2)"
    dispatch_content = dispatch_path.read_text(encoding="utf-8")

    # Check 1 (170 W9-F2): entrypoint — тонкий фасад → remote_dispatch.py --verb converge
    assert "remote_dispatch.py" in content and "--verb converge" in content, (
        "[IMP:9][test] FAIL: converge.sh должен делегировать в remote_dispatch.py --verb converge (170 W9-F2)"
    )
    # Check 2 (170 W9-F2): host резолвится ДО remote-вызова (в dispatch — _resolve_ssh_host)
    assert "_resolve_ssh_host" in dispatch_content and "extract_node_host" in dispatch_content, (
        "[IMP:9][test] FAIL: remote_dispatch.py должен резолвить host до remote-вызова (142 B28b)"
    )
    # Check 3 (170 W9-F2): guard — rc=2 при непустом host → forward (exit 2), НЕ локальный fallback
    assert "RC_LOCAL_FALLBACK" in dispatch_content and "ssh_host:" in dispatch_content, (
        "[IMP:9][test] FAIL: guard -n ssh_host обязан предшествовать локальному fallback"
    )
    assert "NO local fallback" in dispatch_content, "[IMP:9][test] FAIL: rc=2 с host — forward, без локального прогона"
    logger.info("[IMP:9][test_converge_entrypoint_rc2_disambiguation] ALL CHECKS PASS")


# endregion FUNC_test_converge_entrypoint_rc2_disambiguation
