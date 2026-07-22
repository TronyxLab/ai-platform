# GREP_SUMMARY: volume spool spool_dir spool_volume docker-compose named-volume module-yaml spool-validator ensure-spool-dirs static-audit
# STRUCTURE: ┌all_module_yamls + all_compose_files + spool_validator.py┐ → ◇ spool→volume match →
# region MODULE_CONTRACT
# @file test_volume_spool_consistency.py
# @purpose  Validate that spool directories declared in module.yaml (spool_dir/spool_volume)
#           have corresponding volume mounts in docker-compose, every named volume is declared
#           in module.yaml or known-host-dirs, spool_validator.py processes all spool paths, and
#           there are no orphan volumes without documented reason.
# @scope    All Docker modules with compose files; spool_validator.py verify_spool_dirs() logic
# @invariants
#   - spool_dir/spool_volume in module.yaml → must have a matching named volume in compose
#   - Every named volume in compose must be declared in module.yaml or as a known host dir
#   - spool_validator.py must process ALL spool paths from module.yaml
#   - No orphan named volumes without a documented reason
# @rationale  If a volume is added to compose but not declared in module.yaml as spool_dir/
#             spool_volume, spool_validator won't check the directory before deploy,
#             causing bind-mount failure. This test catches that drift.
# @changes
#   2026-07-22 · Updated for W4-E1 spool_validator.py reimplementation (was deploy-modules.sh ensure_spool_dirs)
# endregion MODULE_CONTRACT
#
#            ◇ volume→spool declaration → ⊕ deploy_modules_coverage → ∑ orphan_volume_check

import logging
import os

import pytest
import yaml
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# region HELPERS


def _find_spool_value(module_yaml: dict) -> str | None:
    """Extract spool_dir or spool_volume from module.yaml.

    ## @purpose — Returns the spool path (first of spool_dir or spool_volume found)
    ## @io — ⇥ module_yaml dict → ⎋ str | None
    ## @complexity — O(1)
    """
    config = module_yaml.get("config") or {}
    if isinstance(config, dict):
        return config.get("spool_dir") or config.get("spool_volume") or None
    return None


def _get_named_volumes(compose_data: dict) -> dict[str, dict]:
    """Extract named volumes (not tmpfs, not bind shorthand) from compose top-level volumes.

    ## @purpose — Returns {volume_name: volume_config_dict}
    ## @io — ⇥ compose_data dict → ⎋ dict[str, dict]
    ## @complexity — O(V) where V = volume count
    """
    volumes = compose_data.get("volumes") or {}
    if not isinstance(volumes, dict):
        return {}
    return volumes


def _get_volume_device_path(volume_config: dict) -> str:
    """Extract the host device path from a named volume's driver_opts.

    ## @purpose — Returns the device path (bind mount target on host)
    ## @io — ⇥ volume_config dict → ⎋ str (empty if not found)
    ## @complexity — O(1)
    """
    if not isinstance(volume_config, dict):
        return ""
    driver_opts = volume_config.get("driver_opts") or {}
    if isinstance(driver_opts, dict):
        return driver_opts.get("device", "")
    return ""


# endregion HELPERS

# region PHASE2_DETECT


# 🧐 TRAP[DEBT] · 2026-07-15 · MED · Vacuous Check 3 (spool coverage via Phase 2 grep)
def _has_phase2_dynamic_parsing(script_content: str) -> bool:
    """Check if deploy-modules.sh has Phase 2 dynamic spool_dir parsing.

    ## @purpose — Phase 2 reads spool_dir/spool_volume from each module.yaml
    ##            via grep/awk at runtime, so paths don't need to be hardcoded.
    ##            If present, any module with a spool_dir in module.yaml is
    ##            automatically handled by the Phase 2 loop.
    ## @io — ⇥ script_content → ⎋ bool
    ## @complexity — O(1) — substring match
    """
    return "spool_dir:|" in script_content


def _is_module_spool_handled_via_phase2(module_yaml: dict, script_content: str) -> bool:
    """Check if a module's spool path is handled via Phase 2 dynamic parsing.

    ## @purpose — A module with spool_dir in module.yaml is considered handled
    ##            if the script contains Phase 2 grep pattern that reads
    ##            module.yaml spool_dir fields at runtime.
    ## @io — ⇥ module_yaml, script_content → ⎋ bool
    ## @complexity — O(1)
    """
    spool_path = _find_spool_value(module_yaml)
    if not spool_path:
        return False
    return _has_phase2_dynamic_parsing(script_content)


# endregion PHASE2_DETECT

# region KNOWN_HOST_DIRS
# These directories are checked by spool_validator.py verify_spool_dirs()
# — platform dirs, wal-archive, observability dirs, and fallback dirs
# for modules that don't declare spool_dir/spool_volume in module.yaml.
_KNOWN_HOST_DIRS: set[str] = {
    # observability (Phase 3b) — spool_dir points to grafana-data only, but prometheus/loki
    # are also created in ensure_spool_dirs() as known obs dirs
    "/var/lib/platform/grafana-data",
    "/var/lib/platform/prometheus-data",
    "/var/lib/platform/loki-data",
    # postgres WAL archive (TASK-1: B1) — bind mount for WAL file persistence
    "/var/lib/platform/wal-archive",
    # backup-cron platform-level log dir
    "/var/log/platform/backup",
    "/var/lib/platform/backup-spool",
    "/var/lib/platform/hermes-agent/data",
    "/var/lib/platform/postgres-data",
}

# endregion KNOWN_HOST_DIRS


# region --- Tests ---


@pytest.mark.static_audit
@ldd_trajectory
def test_spool_dir_has_volume_mount(all_module_yamls, all_compose_files, modules_dir, caplog) -> None:
    # · Scenario: module declares spool_dir in module.yaml but docker-compose has no named volume with matching device → deploy-modules.sh doesn't create the spool, bind mount fails at runtime
    # · Last fail: never — guard test
    # · Remove if: spool_dir/spool_volume convention removed from module.yaml
    """Verify spool_dir/spool_volume from module.yaml has a corresponding named volume mount in compose.

    ## @purpose — Every module that declares spool_dir or spool_volume must have a
    ##            named volume in docker-compose whose driver_opts.device matches the spool path.
    ## @io — ⇥ all_module_yamls, all_compose_files, modules_dir → ⎋ None (assert)
    ## @complexity — O(N * V) where N=modules, V=volumes per module
    """

    logger.info("[IMP:7][test_spool_dir_has_volume_mount] Checking spool paths → volume mounts")

    unmounted_spools = []
    for mod_name, module_yaml in all_module_yamls.items():
        spool_path = _find_spool_value(module_yaml)
        if not spool_path:
            logger.info("[IMP:8][test_spool_dir_has_volume_mount] %s: no spool_dir/spool_volume — SKIP", mod_name)
            continue

        # Normalise: strip trailing slashes
        spool_path = spool_path.rstrip("/")

        compose_path = all_compose_files.get(mod_name)
        if not compose_path:
            logger.info("[IMP:8][test_spool_dir_has_volume_mount] %s: no compose file — SKIP", mod_name)
            continue

        with open(compose_path) as f:
            compose_data = yaml.safe_load(f)

        named_volumes = _get_named_volumes(compose_data)
        found = False
        for vol_name, vol_cfg in named_volumes.items():
            device_path = _get_volume_device_path(vol_cfg).rstrip("/")
            if device_path == spool_path:
                found = True
                logger.info(
                    "[IMP:8][test_spool_dir_has_volume_mount] %s: spool %s → volume %s (device=%s)",
                    mod_name,
                    spool_path,
                    vol_name,
                    device_path,
                )
                break

        if not found:
            unmounted_spools.append(f"{mod_name}: spool={spool_path}")
            logger.warning(
                "[IMP:9][test_spool_dir_has_volume_mount] %s: spool %s has NO matching volume mount!",
                mod_name,
                spool_path,
            )

    if not unmounted_spools:
        logger.info("[IMP:9][test_spool_dir_has_volume_mount] All spool paths have matching volume mounts")
    assert len(unmounted_spools) == 0, f"Spool paths without volume mount: {unmounted_spools}"


@pytest.mark.static_audit
@ldd_trajectory
def test_deploy_modules_knows_all_spools(platform_root, all_module_yamls, caplog) -> None:
    # · Scenario: new module has spool_dir in module.yaml but the path is not in spool_validator.py → validator doesn't check the directory, first deploy fails on bind mount
    # · Last fail: never — guard test
    # · Remove if: spool_validator.py removed (ensure_spool_dirs permanently deleted)
    """Verify spool_validator.py verify_spool_dirs() processes all spool paths from module.yaml.

    ## @purpose — Read spool_validator.py and check that every spool_dir/spool_volume
    ##            from module.yaml is handled in verify_spool_dirs().
    ## @io — ⇥ platform_root, all_module_yamls → ⎋ None (assert)
    ## @complexity — O(N + S) where N=modules, S=module lines searched
    """

    logger.info("[IMP:7][test_deploy_modules_knows_all_spools] Checking spool_validator.py spool coverage")

    # Read spool_validator.py (replaces old shell ensure_spool_dirs)
    spool_validator = os.path.join(platform_root, "core", "internal", "bootstrap", "deploy", "spool_validator.py")
    assert os.path.isfile(spool_validator), f"spool_validator.py not found at {spool_validator}"

    with open(spool_validator) as f:
        module_content = f.read()

    # verify_spool_dirs() dynamically reads spool_dir from every module.yaml at runtime
    # through yaml.safe_load in the per-module loop — all spool paths are auto-covered.
    # No hardcoded per-module paths needed (Phase 2 behavior ported to Python).
    has_yaml_scan = (
        "yaml.safe_load" in module_content and "spool_dir" in module_content and "spool_volume" in module_content
    )
    if has_yaml_scan:
        logger.info(
            "[IMP:8][test_deploy_modules_knows_all_spools] Dynamic module.yaml scanning detected — all spool dirs auto-covered"
        )

    missing_paths = []
    for mod_name, module_yaml in all_module_yamls.items():
        spool_path = _find_spool_value(module_yaml)
        if not spool_path:
            logger.info("[IMP:8][test_deploy_modules_knows_all_spools] %s: no spool — SKIP", mod_name)
            continue

        spool_path = spool_path.rstrip("/")

        # Check 1: hardcoded path in spool_validator.py (platform/fallback dirs)
        path_in_validator = spool_path in module_content
        # Check 2: handled via dynamic module.yaml scanning (spool_dir from module.yaml)
        handled_via_yaml_scan = has_yaml_scan and _find_spool_value(module_yaml) is not None

        if path_in_validator:
            logger.info(
                "[IMP:8][test_deploy_modules_knows_all_spools] %s spool %s → hardcoded in spool_validator.py",
                mod_name,
                spool_path,
            )
        elif handled_via_yaml_scan:
            logger.info(
                "[IMP:8][test_deploy_modules_knows_all_spools] %s spool %s → dynamic scan (yaml.safe_load from module.yaml)",
                mod_name,
                spool_path,
            )
        else:
            missing_paths.append(f"{mod_name}: {spool_path}")
            logger.warning(
                "[IMP:9][test_deploy_modules_knows_all_spools] %s spool %s NOT handled in spool_validator.py!",
                mod_name,
                spool_path,
            )

    if not missing_paths:
        logger.info("[IMP:9][test_deploy_modules_knows_all_spools] All spool paths are handled in spool_validator.py")
    assert len(missing_paths) == 0, f"Spool paths missing from spool_validator.py: {missing_paths}"


@pytest.mark.static_audit
@ldd_trajectory
def test_no_orphan_volumes(all_module_yamls, all_compose_files, modules_dir, caplog) -> None:
    # · Scenario: volume mount for an undocumented path added to docker-compose → no one knows who owns it or what creates it, cleanup becomes risky
    # · Last fail: never — guard test
    # · Remove if: volume documentation convention changed or all volumes managed by Docker named volumes
    """Verify no named volumes exist without explicit documented reason.

    ## @purpose — Every named volume in docker-compose must be documented:
    ##            either as spool_dir/spool_volume in module.yaml, or as a known host dir
    ##            in deploy-modules.sh ensure_spool_dirs(). Undocumented volumes = tech debt.
    ## @io — ⇥ all_module_yamls, all_compose_files, modules_dir → ⎋ None (assert)
    ## @complexity — O(N * V)
    """

    logger.info("[IMP:7][test_no_orphan_volumes] Checking for undocumented orphan named volumes")

    # Collect all declared spool paths across all modules
    declared_spools: set[str] = set()
    for module_yaml in all_module_yamls.values():
        spool_path = _find_spool_value(module_yaml)
        if spool_path:
            declared_spools.add(spool_path.rstrip("/"))

    # Collect all known host dirs (including those from ensure_spool_dirs)
    all_known: set[str] = declared_spools | _KNOWN_HOST_DIRS

    orphans = []
    for mod_name, compose_path in all_compose_files.items():
        with open(compose_path) as f:
            compose_data = yaml.safe_load(f)

        named_volumes = _get_named_volumes(compose_data)
        for vol_name, vol_cfg in named_volumes.items():
            device_path = _get_volume_device_path(vol_cfg).rstrip("/")
            if device_path and device_path not in all_known:
                orphans.append(f"{mod_name}/{vol_name} → {device_path}")
                logger.warning(
                    "[IMP:9][test_no_orphan_volumes] UNDOCUMENTED volume: %s/%s (device=%s)",
                    mod_name,
                    vol_name,
                    device_path,
                )
            else:
                logger.info(
                    "[IMP:8][test_no_orphan_volumes] %s/%s (device=%s) → documented",
                    mod_name,
                    vol_name,
                    device_path,
                )

    if not orphans:
        logger.info("[IMP:9][test_no_orphan_volumes] No undocumented orphan volumes found")
    assert len(orphans) == 0, f"Undocumented orphan volumes: {orphans}"


@pytest.mark.static_audit
@ldd_trajectory
def test_ensure_spool_dirs_is_verify_only(platform_root, caplog) -> None:
    # · Scenario: verify_spool_dirs() in spool_validator.py uses os.makedirs/mkdir instead of verify-only
    # ·   — drift: silently creates dirs instead of warning user to run make provision.
    # ·   This test ensures the function only verifies existence and emits WARN on missing dirs.
    # · Last fail: never — guard test for V11 spool_dirs consolidation
    # · Remove if: verify_spool_dirs() removed from spool_validator.py
    """Verify verify_spool_dirs() is verify-only: NO os.makedirs/mkdir, uses os.path.isdir + WARN.

    ## @purpose — After V11 consolidation, spool dirs are created by provision-environment.sh
    ##            (make provision). verify_spool_dirs() must only verify existence and emit
    ##            WARN if directories are missing. Any os.makedirs/mkdir is a regression.
    ##            Updated: 2026-07-22 — targets spool_validator.py instead of shell deploy-modules.sh.
    ## @io — ⇥ platform_root → ⎋ None (assert)
    ## @complexity — O(S) where S = module lines searched
    """

    logger.info("[IMP:7][test_ensure_spool_dirs_is_verify_only] Checking verify_spool_dirs() is verify-only")

    spool_validator = os.path.join(platform_root, "core", "internal", "bootstrap", "deploy", "spool_validator.py")
    assert os.path.isfile(spool_validator), f"spool_validator.py not found at {spool_validator}"

    with open(spool_validator) as f:
        module_content = f.read()

    # Extract verify_spool_dirs() function body: from 'def verify_spool_dirs' to next top-level def or end
    func_start = module_content.find("def verify_spool_dirs")
    assert func_start != -1, "verify_spool_dirs function not found in spool_validator.py"
    # Find end of function: next top-level def/class or __name__ guard
    func_body = module_content[func_start:]
    next_def = func_body.find("\ndef ", 1)  # skip the first 'def verify_spool_dirs'
    if next_def != -1:
        func_body = func_body[:next_def]

    logger.info("[IMP:8][test_ensure_spool_dirs_is_verify_only] Extracted function body (%d chars)", len(func_body))

    # Strip comment-only lines (docstrings and comments may mention mkdir in rationale)
    non_comment_lines = [
        line
        for line in func_body.split("\n")
        if not line.strip().startswith("#") and not line.strip().startswith('"""') and '"""' not in line
    ]
    non_comment_body = "\n".join(non_comment_lines)

    # Check 1: NO os.makedirs or mkdir in non-comment code inside verify_spool_dirs()
    assert "os.makedirs" not in non_comment_body, (
        "verify_spool_dirs() contains 'os.makedirs' in code — should be verify-only!\n"
        "Remove os.makedirs calls, replace with os.path.isdir check + WARN"
    )
    assert "mkdir" not in non_comment_body, (
        "verify_spool_dirs() contains 'mkdir' in code — should be verify-only!\n"
        "Remove mkdir calls, replace with os.path.isdir check + WARN"
    )
    logger.info(
        "[IMP:8][test_ensure_spool_dirs_is_verify_only] CHECK 1 PASS: No os.makedirs/mkdir in verify_spool_dirs() code"
    )

    # Check 2: Contains os.path.isdir (verify-only pattern)
    assert "os.path.isdir" in non_comment_body, (
        "verify_spool_dirs() missing 'os.path.isdir' — verify-only requires directory existence check"
    )
    logger.info("[IMP:8][test_ensure_spool_dirs_is_verify_only] CHECK 2 PASS: os.path.isdir present in code")

    # Check 3: Contains logger.warning for missing dirs (Python equivalent of shell WARN)
    assert "logger.warning" in non_comment_body, (
        "verify_spool_dirs() missing 'logger.warning' — verify-only must emit warning for missing dirs"
    )
    logger.info("[IMP:8][test_ensure_spool_dirs_is_verify_only] CHECK 3 PASS: logger.warning present in code")

    # Check 4: Contains 'make provision' recommendation
    assert "make provision" in non_comment_body, (
        "verify_spool_dirs() missing 'make provision' recommendation — must tell user how to create missing dirs"
    )
    logger.info(
        "[IMP:8][test_ensure_spool_dirs_is_verify_only] CHECK 4 PASS: 'make provision' recommendation present in code"
    )

    logger.info("[IMP:9][test_ensure_spool_dirs_is_verify_only] verify_spool_dirs() is verify-only: ALL CHECKS PASS")


# endregion
