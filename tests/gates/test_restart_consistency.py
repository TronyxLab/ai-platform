# GREP_SUMMARY: gate-test, restart-consistency, soft-restart, restart-hard, makefile-audit
# STRUCTURE: ┌extract_make_target() helper┐ → ○ 5 test functions ∋ root Makefile / module.mk / postgres / backup-cron / platform-secrets → ⊕ entrypoint-manifest.yaml audit → ⎋ PASS/FAIL verdict
# region MODULE_CONTRACT
## @purpose  Gate test: verify restart is soft (stop+start) and restart-hard uses --force-recreate
## @scope    Root Makefile, core/templates/module.mk, core/modules/*/Makefiles, core/entrypoint-manifest.yaml
## @invariants
##   - Root Makefile restart must be soft (stop + start), never 'down && up -d'
##   - module.mk provides restart-hard target with --force-recreate for hard semantics
##   - platform-secrets/Makefile uses systemd restart — excluded from Docker restart checks
##   - entrypoint-manifest.yaml lifecycle.restart must describe soft restart mechanism
## @rationale D4 → A: restart unified to soft (Makefile.common). Hard variant renamed to restart-hard.
##   Soft restart preserves containers (network/mount state), hard restart needs explicit --force-recreate.
## @usecases
##   AC (DevPlan 011 T4): grep -rn '^restart:' core/ shows one (soft) semantics
# endregion MODULE_CONTRACT

import logging
import re

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)


# region FUNC_extract_make_target
## @purpose  Extract body of a make target from Makefile content (preserving indentation)
## @io
##   @input  content: str — full Makefile content
##   @input  target: str — target name with colon, e.g. "restart:"
##   @output str|None — extracted target body, or None if not found
## @complexity O(n) linear scan
def extract_make_target(content: str, target: str) -> str | None:
    """Extract the body of a make target from Makefile content."""
    lines = content.split("\n")
    in_target = False
    target_lines = []
    for line in lines:
        if line.strip().startswith(target):
            in_target = True
            target_lines.append(line)
        elif in_target:
            # Lines starting with tab or 4 spaces are part of the target
            if line.startswith(("\t", "    ", "\t@", "    @")) or line.strip() == "":
                target_lines.append(line)
            else:
                break
    return "\n".join(target_lines) if target_lines else None


# endregion FUNC_extract_make_target


# region FUNC_test_root_makefile_restart_is_soft
## @purpose  Verify root Makefile restart target uses soft restart (stop + start)
## @io
##   @input  None (reads Makefile from repo_root())
##   @output None (asserts)
## @complexity O(n) on Makefile
@ldd_trajectory
def test_root_makefile_restart_is_soft(caplog):
    """Root Makefile restart target must use 'stop && start', not 'down && up -d'."""
    # 🧪 TRAP[TEST] · Regression: root Makefile restart should be soft per D4→A
    # · Scenario: root Makefile restart target
    # · Last fail: N/A (D4→A converged semantics)
    # · Remove if: restart semantics changes to require hard restart
    makefile = repo_root() / "Makefile"
    content = makefile.read_text()
    # restart target is now in makefiles/modules.mk after include-split
    modules_mk = repo_root() / "makefiles" / "modules.mk"
    if modules_mk.is_file():
        content += "\n" + modules_mk.read_text()

    # Find the restart target section
    restart_section = extract_make_target(content, "restart:")
    assert restart_section is not None, "restart target not found in Makefile/makefiles/modules.mk"
    print(f"  Root Makefile restart section:\n{restart_section[:300]}")

    # Must contain stop && start for soft restart
    assert "stop" in restart_section and "start" in restart_section, (
        f"Root Makefile restart should use 'stop && start', found: {restart_section[:200]}"
    )
    assert "down" not in restart_section and "up -d" not in restart_section, (
        f"Root Makefile restart should NOT use 'down && up -d', found: {restart_section[:200]}"
    )
    logger.info("[IMP:9][gate][restart] Root Makefile uses soft restart (stop && start) ✓")

    # Must NOT use hard restart (down + up -d)
    assert not bool(re.search(r"docker\s+compose\s+down\b", restart_section, re.IGNORECASE)), (
        "Root Makefile restart should NOT use 'docker compose down' command"
    )
    logger.info("[IMP:9][gate][restart] Root Makefile: no hard restart command found ✓")


# endregion FUNC_test_root_makefile_restart_is_soft


# region FUNC_test_module_mk_restart_hard_exists
## @purpose  Verify module.mk has restart-hard target (hard restart with --force-recreate);
##           regular restart is inherited soft from Makefile.common
## @io
##   @input  None (reads module.mk from repo_root()/core/templates/)
##   @output None (asserts)
## @complexity O(n) on module.mk
@ldd_trajectory
def test_module_mk_restart_hard_exists(caplog):
    """Module template must have restart-hard target with --force-recreate."""
    # 🧪 TRAP[TEST] · Regression: module template restart-hard target removed
    # · Scenario: core/templates/module.mk restart-hard target
    # · Last fail: N/A (D4→A renamed hard restart to restart-hard)
    # · Remove if: module template restart-hard semantics changes
    module_mk = repo_root() / "core" / "templates" / "module.mk"
    content = module_mk.read_text()

    # Regular restart should NOT be overridden in module.mk (inherited soft from Makefile.common)
    restart_section = extract_make_target(content, "restart:")
    assert restart_section is None, "module.mk should NOT override restart (inherited soft from Makefile.common)"
    logger.info("[IMP:9][gate][restart] module.mk: restart NOT overridden (soft from Makefile.common) ✓")

    # restart-hard must exist with --force-recreate
    restart_hard_section = extract_make_target(content, "restart-hard:")
    assert restart_hard_section is not None, (
        "restart-hard target not found in module.mk. Expected hard restart with --force-recreate."
    )
    assert "--force-recreate" in restart_hard_section, (
        f"module.mk restart-hard should use '--force-recreate', found: {restart_hard_section[:200]}"
    )
    assert "down" in restart_hard_section and "up -d" in restart_hard_section, (
        f"module.mk restart-hard should use 'down && up -d', found: {restart_hard_section[:200]}"
    )
    logger.info("[IMP:9][gate][restart] module.mk: restart-hard found with --force-recreate ✓")


# endregion FUNC_test_module_mk_restart_hard_exists


# region FUNC_test_no_soft_restart_in_docker_makefiles
## @purpose  Verify no Docker-service Makefile uses 'docker compose restart' (soft)
## @io
##   @input  None (scans core/modules/*/Makefile)
##   @output None (asserts)
## @complexity O(n*m) where n=makefiles, m=lines per file
@ldd_trajectory
def test_no_soft_restart_in_docker_makefiles(caplog):
    """No Docker-service Makefile should use 'docker compose restart' (soft)."""
    # 🧪 TRAP[TEST] · Regression: soft restart in module Makefiles
    # · Scenario: all core/modules/*/Makefile restart targets
    # · Last fail: N/A (new test)
    # · Remove if: any module legitimately needs soft restart
    makefiles = list(repo_root().glob("core/modules/*/Makefile"))
    # Exclude platform-secrets (uses systemd, not Docker)
    makefiles = [m for m in makefiles if "platform-secrets" not in str(m)]

    violations = []
    for mf in makefiles:
        content = mf.read_text()
        # Look for soft restart pattern in the restart target
        if "restart:" in content:
            restart_sec = extract_make_target(content, "restart:")
            if restart_sec:
                # Parse COMPOSE_CMD for command
                has_soft = bool(re.search(r"\$\{?COMPOSE_CMD\}?\s+restart\b", restart_sec))
                has_soft = has_soft or bool(re.search(r"docker\s+compose\s+restart\b", restart_sec))
                if has_soft:
                    violations.append(str(mf.relative_to(repo_root())))
                    # Also show context for debugging
                    print(f"  SOFT restart in {mf.relative_to(repo_root())}: {restart_sec[:150]}")

    checked = len(makefiles)
    logger.info("[IMP:9][gate][restart] Checked %d Docker module Makefiles for soft restart", checked)
    assert len(violations) == 0, f"Found {len(violations)} Makefiles using soft restart: {violations}"
    logger.info("[IMP:9][gate][restart] All %d module Makefiles use hard restart ✓", checked)


# endregion FUNC_test_no_soft_restart_in_docker_makefiles


# region FUNC_test_manifest_restart_is_soft
## @purpose  Verify entrypoint-manifest.yaml describes restart as soft restart (stop + start)
## @io
##   @input  None (reads entrypoint-manifest.yaml)
##   @output None (asserts)
## @complexity O(1) YAML parse + string search
@ldd_trajectory
def test_manifest_restart_is_soft(caplog):
    """entrypoint-manifest.yaml must describe restart as soft restart (stop + start)."""
    # 🧪 TRAP[TEST] · Regression: manifest restart mechanism drift
    # · Scenario: entrypoint-manifest.yaml lifecycle.restart delegates_to
    # · Last fail: N/A (D4→A converged to soft)
    # · Remove if: manifest restart mechanism intentionally reverts to hard
    manifest_path = repo_root() / "core" / "entrypoint-manifest.yaml"
    content = manifest_path.read_text()

    import yaml

    manifest = yaml.safe_load(content)
    lifecycle = manifest.get("lifecycle", [])
    restart_entry = None
    for entry in lifecycle:
        if entry.get("make_target") == "restart":
            restart_entry = entry
            break

    assert restart_entry is not None, "restart entry not found in lifecycle"
    delegates_to = restart_entry.get("delegates_to", "")
    description = restart_entry.get("description", "").lower()
    assert "stop &&" in delegates_to and "start" in delegates_to, (
        f"restart delegates_to should contain 'stop && start', got: {delegates_to}"
    )
    assert "soft" in description, f"restart description should mention 'soft', got: {restart_entry.get('description')}"
    # Must NOT reference 'up -d' for soft restart
    assert "up -d" not in delegates_to, (
        f"restart delegates_to should NOT contain 'up -d' (that's hard), got: {delegates_to}"
    )

    logger.info("[IMP:9][gate][restart] Manifest restart mechanism: soft restart verified ✓")


# endregion FUNC_test_manifest_restart_is_soft


# region FUNC_test_platform_secrets_excluded
## @purpose  platform-secrets uses systemd restart, not Docker — verify it's NOT flagged as Docker soft restart
## @io
##   @input  None (reads platform-secrets/Makefile)
##   @output None (asserts)
## @complexity O(n) on Makefile
@ldd_trajectory
def test_platform_secrets_excluded(caplog):
    """platform-secrets Makefile includes module-system.mk (systemd restart) — should NOT be flagged as Docker."""
    # 🧪 TRAP[TEST] · Regression: false positive on platform-secrets
    # · Scenario: platform-secrets/Makefile restart (systemd via module-system.mk include, not Docker)
    # · Last fail: N/A (new test)
    # · Remove if: platform-secrets migrates to Docker compose restart
    # · Updated 2026-07-18: now includes module-system.mk instead of module.mk + overrides
    ps_makefile = repo_root() / "core" / "modules" / "platform-secrets" / "Makefile"
    if ps_makefile.exists():
        content = ps_makefile.read_text()
        # Verify file includes module-system.mk (provides systemd restart via template)
        assert "include ../../templates/module-system.mk" in content, (
            "platform-secrets Makefile should include module-system.mk"
        )
        logger.info("[IMP:9][gate][restart] platform-secrets Makefile exists, verifying no Docker restart")
        # Load the included template to verify restart target is provided
        template_path = ps_makefile.parent.parent.parent / "templates" / "module-system.mk"
        if template_path.exists():
            template_content = template_path.read_text()
            assert "restart:" in template_content, "module-system.mk should define restart target"
        # It should NOT reference docker compose restart anywhere
        has_docker = bool(re.search(r"docker\s+compose\s+restart\b", content))
        assert not has_docker, "platform-secrets should NOT use docker compose restart"
        logger.info("[IMP:9][gate][restart] platform-secrets excluded from Docker restart check ✓")
    else:
        logger.info("[IMP:9][gate][restart] platform-secrets Makefile not found (skipped) ✓")


# endregion FUNC_test_platform_secrets_excluded
