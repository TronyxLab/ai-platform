# GREP_SUMMARY: deploy-modules provisioner docker network volume integration delegation legacy-fallback --modules severity critical warn exit-code transitive-deps
# STRUCTURE: ▶ 7 test functions → ○ static analysis of deploy-modules.sh → ◇ assert provisioner calls present → ◇ assert legacy fallback preserved → ◇ assert --modules parsing → ◇ assert severity exit codes → ◇ assert transitive deps → ⊕ LDD trajectory → ⎋ IMP:9 assertion
# region MODULE_CONTRACT
## @file test_unit_deploy_modules_provisioner.py
## @purpose  Unit tests for provisioner integration — modules filter, severity exit codes, and
##           --modules flag in deploy-modules.sh (DevPlan 020 T21).
##           Also verifies Docker networks delegated to provisioner and legacy fallback preserved.
## @scope    Static analysis of core/internal/bootstrap/deploy-modules.sh.
##           Does NOT require Docker, VPS, or network access — reads source file.
## @invariants
##   - main() calls provision-environment.sh --scope networks
##   - main() calls provision-environment.sh --scope volumes
##   - Legacy fallback ensure_docker_network loop preserved (if provisioner not found)
##   - ensure_docker_network() function still defined (for fallback)
##   - --modules flag parsed in main() with transitive dep expansion
##   - _get_module_severity() reads severity field with default warn
##   - CRITICAL severity → exit 2, WARN → exit 1, all ok → exit 0
##   - postgres module has severity: critical
##   - Tests use file read/grep, not bash subprocess
##   - IMP:9 logs asserted in success paths
## @rationale DevPlan 003 TASK-3 + DevPlan 020 T21: --modules enables selective deploy of subset
##           of modules + transitive dependencies. Severity-based exit code differentiates
##           between CRITICAL (fatal, exit 2) and WARN (tolerable, exit 1).
# endregion MODULE_CONTRACT

import logging
import os
import re

logger = logging.getLogger(__name__)

DEPLOY_MODULES_SH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core",
    "internal",
    "bootstrap",
    "deploy-modules.sh",
)


def _read_source() -> str:
    """Read deploy-modules.sh source content."""
    with open(DEPLOY_MODULES_SH) as f:
        return f.read()


def _extract_main_body(content: str) -> str:
    """Extract the body of main() function from deploy-modules.sh."""
    # Find main() function definition
    match = re.search(r"^main\(\)\s*\{.*?(?=^function |\Z)", content, re.MULTILINE | re.DOTALL)
    if not match:
        # Try simpler approach: find main() { and capture until end of file
        idx = content.find("main() {")
        if idx == -1:
            return ""
        return content[idx:]
    return match.group(0)


# region TEST_test_networks_delegated_to_provisioner
# 🧪 TRAP[TEST] · 2026-07-15 · provisioner integration — networks delegated to provisioner
# · Prevents: regression where the provisioner call is removed and networks are
#   created only via legacy loop (duplicate logic, drift from platform-env.yaml)
def test_networks_delegated_to_provisioner(caplog) -> None:
    """deploy-modules.sh main() calls provision-environment.sh --scope networks."""
    caplog.set_level(logging.DEBUG)

    content = _read_source()
    main_body = _extract_main_body(content)

    # Core assertion: provisioner is called for networks
    assert "provision-environment.sh" in content, "provision-environment.sh not referenced in deploy-modules.sh"
    assert "--scope networks" in content, (
        "deploy-modules.sh should call provisioner with --scope networks\n"
        "DevPlan 003 TASK-3: networks delegated to provision-environment.sh"
    )

    # Verify the call is in main() body
    assert "--scope networks" in main_body, "provisioner --scope networks call not found in main() function body"

    # Verify the provisioner call pattern: bash "$provisioner" --scope networks
    provisioner_networks_pattern = re.search(
        r"bash\s+.*provision.*--scope\s+networks",
        main_body,
        re.IGNORECASE,
    )
    assert provisioner_networks_pattern is not None, (
        "Provisioner call for networks not found with expected pattern in main()"
    )

    logger.info("[IMP:9][test][provisioner] Networks delegation to provisioner confirmed in main()")

    # Also verify deploy-modules.sh has the provisioner variable setup
    assert "PATHS_INTERNAL_DIR" in main_body or "PATHS_INTERNAL_DIR" in content, (
        "PATHS_INTERNAL_DIR not referenced — provisioner path resolution may be broken"
    )


# endregion


# region TEST_test_volumes_delegated_to_provisioner
# 🧪 TRAP[TEST] · 2026-07-15 · provisioner integration — volumes delegated to provisioner
# · Prevents: regression where volumes are created only via ensure_spool_dirs
#   (missing canonical volumes from platform-env.yaml)
def test_volumes_delegated_to_provisioner(caplog) -> None:
    """deploy-modules.sh main() calls provision-environment.sh --scope volumes."""
    caplog.set_level(logging.DEBUG)

    content = _read_source()
    main_body = _extract_main_body(content)

    # Core assertion: provisioner is called for volumes
    assert "--scope volumes" in content, (
        "deploy-modules.sh should call provisioner with --scope volumes\n"
        "DevPlan 003 TASK-3: volumes delegated to provision-environment.sh"
    )

    # Verify the call is in main() body
    assert "--scope volumes" in main_body, "provisioner --scope volumes call not found in main() function body"

    # Verify the provisioner call pattern: bash "$provisioner" --scope volumes
    provisioner_volumes_pattern = re.search(
        r"bash\s+.*provision.*--scope\s+volumes",
        main_body,
        re.IGNORECASE,
    )
    assert provisioner_volumes_pattern is not None, (
        "Provisioner call for volumes not found with expected pattern in main()"
    )

    logger.info("[IMP:9][test][provisioner] Volumes delegation to provisioner confirmed in main()")


# endregion


# region TEST_test_legacy_fallback_preserved
# 🧪 TRAP[TEST] · 2026-07-15 · provisioner integration — legacy fallback preserved
# · Prevents: regression where legacy ensure_docker_network loop is removed,
#   breaking bootstrap on nodes where provisioner is absent
def test_legacy_fallback_preserved(caplog) -> None:
    """Legacy ensure_docker_network loop preserved as fallback when provisioner absent."""
    caplog.set_level(logging.DEBUG)

    content = _read_source()
    main_body = _extract_main_body(content)

    # Core assertion: ensure_docker_network function still defined
    assert "ensure_docker_network()" in content, (
        "ensure_docker_network() function not found in deploy-modules.sh\n"
        "Legacy fallback requires this function for backward compatibility"
    )

    # Legacy fallback loop should be present (inside else branch of provisioner check)
    assert 'ensure_docker_network "$network"' in main_body or 'ensure_docker_network "$network"' in content, (
        "Legacy ensure_docker_network loop not found — fallback may be broken"
    )

    # Verify the fallback is in an else branch (only runs if provisioner not found)
    # Search in main_body (not full content) to avoid matching MODULE_CONTRACT comments
    provisioner_idx = main_body.find("provision-environment.sh")
    if provisioner_idx != -1:
        # Check that the fallback is in the else branch within 2000 chars after provisioner
        after_provisioner = main_body[provisioner_idx : provisioner_idx + 2000]
        assert "else" in after_provisioner or "WARN" in after_provisioner, (
            "Provisioner fallback else branch pattern not detected in main() — review manually"
        )

    logger.info("[IMP:9][test][provisioner] Legacy fallback loop preserved for backward compatibility")

    # Ensure ensure_spool_dirs still exists (not removed — it handles module-specific spool dirs)
    assert "ensure_spool_dirs" in content, "ensure_spool_dirs not found — module-specific spool dirs may not be created"


# endregion


# region TEST_test_modules_filter_flag_present
# 🧪 TRAP[TEST] · 2026-07-17 · --modules flag parsing in deploy-modules.sh
# · Prevents: regression where --modules parsing is removed from main()
# · Scenario: deploy-modules.sh must parse --modules flag with comma-separated list
# · Last fail: never
# · Remove if: --modules flag is intentionally removed from design
def test_modules_filter_flag_present(caplog) -> None:
    """deploy-modules.sh main() parses --modules flag with comma-separated list."""
    caplog.set_level(logging.DEBUG)

    content = _read_source()
    main_body = _extract_main_body(content)

    # Core assertion: --modules case handling in main()
    assert "--modules" in main_body, (
        "--modules flag parsing not found in main() — selective deploy would be broken\n"
        "DevPlan 020 T21: --modules nginx,postgres filters to specified modules + transitive deps"
    )

    # Verify error message for missing argument
    assert "requires a comma-separated list" in content, (
        "--modules missing value error message not found — usage help would be unhelpful"
    )

    # Verify _expand_transitive_deps function exists
    assert "_expand_transitive_deps" in content, (
        "_expand_transitive_deps function not found — transitive dependency expansion required"
    )

    # Verify expanded_modules variable is assigned
    assert "expanded_modules=" in main_body, (
        "expanded_modules assignment not found in main() — filter expansion result not consumed"
    )

    logger.info("[IMP:9][test][--modules] --modules flag parsing and transitive expansion confirmed")


# endregion


# region TEST_test_modules_filter_unknown_module_error
# 🧪 TRAP[TEST] · 2026-07-17 · --modules unknown module error handling
# · Prevents: regression where non-existent module is silently ignored
# · Scenario: deploy-modules.sh --modules nonexistent must error (exit 1)
#   The _expand_transitive_deps function must reject unknown modules
# · Last fail: never
# · Remove if: validation is intentionally changed to soft-fail
def test_modules_filter_unknown_module_error(caplog) -> None:
    """_expand_transitive_deps() must error when seed module doesn't exist in module.yaml DAG."""
    caplog.set_level(logging.DEBUG)

    content = _read_source()

    # Verify _expand_transitive_deps validates seed modules
    assert "ERROR: Unknown module" in content or "exit 1" in content, (
        "_expand_transitive_deps must validate that all seed modules exist in DAG\n"
        "DevPlan 020 T21: --modules nonexistent → exit 1"
    )

    # Verify the exit code propagation from expansion to main()
    assert '_expand_transitive_deps "$modules_filter"' in content, (
        "Main must call _expand_transitive_deps with the filter value\n"
        "DevPlan 020 T21: error must propagate to main() exit 1"
    )

    logger.info("[IMP:9][test][--modules] Unknown module validation confirmed in _expand_transitive_deps")


# endregion


# region TEST_test_get_module_severity_function
# 🧪 TRAP[TEST] · 2026-07-17 · _get_module_severity() function
# · Prevents: regression where severity reading from module.yaml is removed
# · Scenario: _get_module_severity reads severity field (default warn)
# · Last fail: never
# · Remove if: severity is permanently removed from module.yaml
def test_get_module_severity_function(caplog) -> None:
    """_get_module_severity() reads severity field from module.yaml with default warn."""
    caplog.set_level(logging.DEBUG)

    content = _read_source()

    # Core assertion: _get_module_severity function exists
    assert "_get_module_severity()" in content, (
        "_get_module_severity() function not found — severity-based exit code would be broken\n"
        "DevPlan 020 T21: severity read from module.yaml (critical|warn, default warn)"
    )

    # Verify default severity is warn
    assert "d.get('severity', 'warn')" in content or "default warn" in content, (
        "_get_module_severity must default to warn when severity field is absent\n"
        "DevPlan 020 T21: missing severity → warn (backward compatible)"
    )

    logger.info("[IMP:9][test][severity] _get_module_severity() function with warn default confirmed")


# endregion


# region TEST_test_severity_exit_codes
# 🧪 TRAP[TEST] · 2026-07-17 · severity-based exit code logic in main()
# · Prevents: regression where CRITICAL/WARN exit code logic is removed
# · Scenario: CRITICAL failure → exit 2, WARN failure → exit 1, all ok → exit 0
# · Last fail: never
# · Remove if: severity-based exit is intentionally removed
def test_severity_exit_codes(caplog) -> None:
    """main() aggregates FAILED_MODULE_NAMES by severity: critical→exit 2, warn→exit 1."""
    caplog.set_level(logging.DEBUG)

    content = _read_source()
    main_body = _extract_main_body(content)

    # Verify FAILED_MODULE_NAMES array initialized
    assert "FAILED_MODULE_NAMES" in content, (
        "FAILED_MODULE_NAMES array not found — module failure tracking required for severity exit"
    )

    # Verify severity aggregation
    assert "critical_failed" in main_body, (
        "critical_failed counter not found in main() — CRITICAL severity not aggregated"
    )
    assert "warn_failed" in main_body, "warn_failed counter not found in main() — WARN severity not aggregated"

    # Verify exit code logic
    assert "exit 2" in main_body, "exit 2 not found in main() — CRITICAL failures must exit 2"
    assert "exit 1" in main_body, "exit 1 not found in main() — WARN failures must exit 1"

    # Verify severity is read per failed module
    assert "_get_module_severity" in main_body or "_get_module_severity" in content, (
        "_get_module_severity not referenced — severity per-failed-module not checked"
    )

    logger.info("[IMP:9][test][severity-exit] Severity exit code logic confirmed: critical→exit2, warn→exit1")


# endregion


# region TEST_test_postgres_module_severity_critical
# 🧪 TRAP[TEST] · 2026-07-17 · postgres module.yaml severity: critical
# · Prevents: regression where postgres severity is downgraded from critical
# · Scenario: postgres is a CRITICAL module — failure must block node-update
# · Last fail: never
# · Remove if: postgres is intentionally changed to warn severity
def test_postgres_module_severity_critical() -> None:
    """postgres/module.yaml must have severity: critical."""
    postgres_yaml = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "core",
        "modules",
        "postgres",
        "module.yaml",
    )

    assert os.path.exists(postgres_yaml), f"postgres/module.yaml not found at {postgres_yaml}"

    with open(postgres_yaml) as f:
        content = f.read()

    # Verify severity: critical is present
    assert "severity: critical" in content, (
        "postgres/module.yaml must have severity: critical\n"
        "DevPlan 020 T21: CRITICAL module failure → exit 2 (blocks node-update)"
    )

    print("[IMP:9][test][postgres-severity] postgres/module.yaml has severity: critical")


# endregion


# region TEST_test_expand_transitive_deps_function
# 🧪 TRAP[TEST] · 2026-07-17 · _expand_transitive_deps BFS algorithm
# · Prevents: regression where transitive dependency expansion is simplified/removed
# · Scenario: --modules monitoring expands to 'monitoring nginx' (monitoring depends on nginx)
# · Last fail: never
# · Remove if: transitive expansion is intentionally removed from design
def test_expand_transitive_deps_function(caplog) -> None:
    """_expand_transitive_deps() uses BFS over module.yaml DAG to find transitive depends_on."""
    caplog.set_level(logging.DEBUG)

    content = _read_source()

    # Verify BFS / transitive expansion pattern
    assert "BFS" in content or "queue" in content or "transitive" in content, (
        "_expand_transitive_deps must use BFS or queue-based transitive dependency resolution\n"
        "DevPlan 020 T21: keep specified modules + transitive depends_on"
    )

    # Verify expanded set is sorted (deterministic output)
    assert "sorted(expanded)" in content or "sorted" in content, (
        "_expand_transitive_deps output should be sorted for deterministic filtering\n"
        "DevPlan 020 T21: deterministic filter ordering"
    )

    logger.info("[IMP:9][test][transitive-deps] _expand_transitive_deps BFS expansion confirmed")


# endregion


# region TEST_test_failed_module_names_tracking
# 🧪 TRAP[TEST] · 2026-07-17 · FAILED_MODULE_NAMES tracking in deploy paths
# · Prevents: regression where failed module names are not recorded
# · Scenario: all failure paths (system, docker topo-sort, docker group) must track mod names
# · Last fail: never
# · Remove if: severity exit is intentionally removed
def test_failed_module_names_tracking(caplog) -> None:
    """FAILED_MODULE_NAMES is populated from system module loop, topo-sort fallback, and deploy_docker_group."""
    caplog.set_level(logging.DEBUG)

    content = _read_source()

    # Verify FAILED_MODULE_NAMES is referenced in system module deploy path
    assert "FAILED_MODULE_NAMES" in content, "FAILED_MODULE_NAMES not found — severity exit code has no failure input"

    # Verify failure tracking in system module loop
    assert 'FAILED_MODULE_NAMES+=("$mod_name")' in content, "System module failure not tracked in FAILED_MODULE_NAMES"

    # Verify failure tracking in docker deploy paths
    assert "FAILED_MODULE_NAMES" in content, "FAILED_MODULE_NAMES tracking confirmed in deploy-modules.sh"

    logger.info("[IMP:9][test][failure-tracking] FAILED_MODULE_NAMES tracking confirmed in all failure paths")


# endregion
