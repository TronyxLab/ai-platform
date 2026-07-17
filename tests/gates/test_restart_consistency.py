# GREP_SUMMARY: gate-test, restart-consistency, hard-restart, makefile-audit, down-up-d
# STRUCTURE: ┌extract_make_target() helper┐ → ○ 5 test functions ∋ root Makefile / module.mk / postgres / backup-cron / platform-secrets → ⊕ entrypoint-manifest.yaml audit → ⎋ PASS/FAIL verdict
# region MODULE_CONTRACT
## @purpose  Gate test: verify all restart targets use hard restart (down && up -d), not soft restart (docker compose restart)
## @scope    Root Makefile, core/templates/module.mk, core/modules/*/Makefiles, core/entrypoint-manifest.yaml
## @invariants
##   - Every Makefile restart target must use 'down && up -d', never 'docker compose restart' (soft)
##   - platform-secrets/Makefile uses systemd restart — excluded from Docker restart checks
##   - entrypoint-manifest.yaml lifecycle.restart must describe hard restart mechanism
## @rationale Hard restart (down + up -d) guarantees clean container state recreation vs soft restart
##   which may leave stale network/mount state. Unified semantics across all modules prevents
##   inconsistent restart behaviour. Gate test enforces this as a CI-blocking invariant.
## @usecases
##   AC5 (DevPlan 005): All restart targets = hard restart; gate test passes
# endregion MODULE_CONTRACT

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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


# region FUNC_test_root_makefile_restart_uses_hard
## @purpose  Verify root Makefile restart target uses 'down && up -d', not soft 'restart' command
## @io
##   @input  None (reads Makefile from PROJECT_ROOT)
##   @output None (asserts)
## @complexity O(n) on Makefile
def test_root_makefile_restart_uses_hard():
    """Root Makefile restart target must use 'down && up -d', not 'restart' command."""
    # 🧪 TRAP[TEST] · Regression: soft restart leaks stale network/mount state
    # · Scenario: root Makefile restart target
    # · Last fail: N/A (new test)
    # · Remove if: restart semantics changes to require soft restart
    makefile = PROJECT_ROOT / "Makefile"
    content = makefile.read_text()

    # Find the restart target section
    restart_section = extract_make_target(content, "restart:")
    assert restart_section is not None, "restart target not found in root Makefile"
    print(f"  Root Makefile restart section:\n{restart_section[:300]}")

    # Must contain down && up -d for hard restart
    assert "down" in restart_section and "up -d" in restart_section, (
        f"Root Makefile restart should use 'down && up -d', found: {restart_section[:200]}"
    )

    # Must NOT use soft restart (but allow 'restart:' header itself)
    # The word "restart" may still appear in echo comments — check for actual command usage
    assert not bool(re.search(r"docker\s+compose\s+restart\b", restart_section, re.IGNORECASE)), (
        "Root Makefile restart should NOT use soft 'docker compose restart' command"
    )


# endregion FUNC_test_root_makefile_restart_uses_hard


# region FUNC_test_module_mk_restart_uses_hard
## @purpose  Verify module.mk template restart target uses hard restart
## @io
##   @input  None (reads module.mk from PROJECT_ROOT/core/templates/)
##   @output None (asserts)
## @complexity O(n) on module.mk
def test_module_mk_restart_uses_hard():
    """Module template restart target must use hard restart."""
    # 🧪 TRAP[TEST] · Regression: module template restart target
    # · Scenario: core/templates/module.mk restart target
    # · Last fail: N/A (new test)
    # · Remove if: module template restart semantics changes
    module_mk = PROJECT_ROOT / "core" / "templates" / "module.mk"
    content = module_mk.read_text()

    restart_section = extract_make_target(content, "restart:")
    assert restart_section is not None, "restart target not found in module.mk"

    assert "down" in restart_section and "up -d" in restart_section, (
        f"module.mk restart should use 'down && up -d', found: {restart_section[:200]}"
    )

    assert not bool(re.search(r"\$\{?COMPOSE_CMD\}?\s+restart\b", restart_section)), (
        "module.mk restart should NOT use soft 'restart' command"
    )


# endregion FUNC_test_module_mk_restart_uses_hard


# region FUNC_test_no_soft_restart_in_docker_makefiles
## @purpose  Verify no Docker-service Makefile uses 'docker compose restart' (soft)
## @io
##   @input  None (scans core/modules/*/Makefile)
##   @output None (asserts)
## @complexity O(n*m) where n=makefiles, m=lines per file
def test_no_soft_restart_in_docker_makefiles():
    """No Docker-service Makefile should use 'docker compose restart' (soft)."""
    # 🧪 TRAP[TEST] · Regression: soft restart in module Makefiles
    # · Scenario: all core/modules/*/Makefile restart targets
    # · Last fail: N/A (new test)
    # · Remove if: any module legitimately needs soft restart
    makefiles = list(PROJECT_ROOT.glob("core/modules/*/Makefile"))
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
                    violations.append(str(mf.relative_to(PROJECT_ROOT)))
                    # Also show context for debugging
                    print(f"  SOFT restart in {mf.relative_to(PROJECT_ROOT)}: {restart_sec[:150]}")

    assert len(violations) == 0, f"Found {len(violations)} Makefiles using soft restart: {violations}"


# endregion FUNC_test_no_soft_restart_in_docker_makefiles


# region FUNC_test_manifest_restart_mechanism
## @purpose  Verify entrypoint-manifest.yaml describes restart as hard restart
## @io
##   @input  None (reads entrypoint-manifest.yaml)
##   @output None (asserts)
## @complexity O(1) YAML parse + string search
def test_manifest_restart_mechanism():
    """entrypoint-manifest.yaml must describe restart as hard restart."""
    # 🧪 TRAP[TEST] · Regression: manifest restart mechanism drift
    # · Scenario: entrypoint-manifest.yaml lifecycle.restart delegates_to
    # · Last fail: N/A (new test)
    # · Remove if: manifest restart mechanism intentionally reverts to soft
    manifest_path = PROJECT_ROOT / "core" / "entrypoint-manifest.yaml"
    content = manifest_path.read_text()

    # Should mention 'down' for restart
    assert "down" in content, "entrypoint-manifest.yaml restart should mention 'down' for hard restart"
    assert "up -d" in content, "entrypoint-manifest.yaml restart should mention 'up -d'"

    import yaml

    manifest = yaml.safe_load(content)
    lifecycle = manifest.get("lifecycle", [])
    restart_entry = None
    for entry in lifecycle:
        if entry.get("make_target") == "restart":
            restart_entry = entry
            break

    assert restart_entry is not None, "restart entry not found in lifecycle"
    assert "down" in restart_entry.get("delegates_to", ""), (
        f"restart delegates_to should contain 'down', got: {restart_entry.get('delegates_to')}"
    )
    assert "up -d" in restart_entry.get("delegates_to", ""), (
        f"restart delegates_to should contain 'up -d', got: {restart_entry.get('delegates_to')}"
    )
    assert (
        "restart services" not in restart_entry.get("description", "").lower()
        or "hard" in restart_entry.get("description", "").lower()
    ), f"restart description should mention 'hard', got: {restart_entry.get('description')}"


# endregion FUNC_test_manifest_restart_mechanism


# region FUNC_test_platform_secrets_excluded
## @purpose  platform-secrets uses systemd restart, not Docker — verify it's NOT flagged as Docker soft restart
## @io
##   @input  None (reads platform-secrets/Makefile)
##   @output None (asserts)
## @complexity O(n) on Makefile
def test_platform_secrets_excluded():
    """platform-secrets Makefile uses systemd restart, not Docker — should NOT be flagged."""
    # 🧪 TRAP[TEST] · Regression: false positive on platform-secrets
    # · Scenario: platform-secrets/Makefile restart (systemd, not Docker)
    # · Last fail: N/A (new test)
    # · Remove if: platform-secrets migrates to Docker compose restart
    ps_makefile = PROJECT_ROOT / "core" / "modules" / "platform-secrets" / "Makefile"
    if ps_makefile.exists():
        content = ps_makefile.read_text()
        # May have 'restart: stop start' (systemd pattern) — that's fine, not Docker
        # Just verify the file exists and is parseable
        assert "restart:" in content, "platform-secrets Makefile should have restart target"
        # It should NOT use docker compose restart
        if "restart:" in content:
            restart_sec = extract_make_target(content, "restart:")
            has_docker = bool(re.search(r"docker\s+compose\s+restart\b", restart_sec or ""))
            assert not has_docker, "platform-secrets should NOT use docker compose restart"


# endregion FUNC_test_platform_secrets_excluded
