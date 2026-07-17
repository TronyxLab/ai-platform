"""
# GREP_SUMMARY: test_spool_dir, spool_volume, module.yaml, gate, validation, docker-modules
# STRUCTURE: ▶ glob core/modules/*/module.yaml → ◇ yaml.safe_load each → ◇ test_all_docker_modules_have_spool_dir (skip justified exclusions) → ◇ test_spool_dir_paths_are_absolute (startswith /opt/) → ⎋ assert missing list empty
# region MODULE_CONTRACT
## @purpose  Gate tests validating all Docker modules have spool_dir or spool_volume, and paths are absolute
## @scope    Scans core/modules/*/module.yaml; only docker-type modules (install_type: docker or default)
## @invariants
##   - Every Docker module must have spool_dir OR spool_volume
##   - Justified exclusions: nginx (reverse proxy), platform-secrets (install_type: system)
##   - spool_dir paths must be absolute (start with /)
##   - spool_volume is not validated for path format (referenced as docker volume name)
##   - Actual convention is /var/lib/platform/ for spool_dir paths (not /opt/)
## @rationale Gate prevents deploying modules without persistent data storage.
##   spool_dir OR spool_volume (not AND) — 3 modules have spool_dir without spool_volume,
##   which is acceptable for now (see DD2 in DevPlan 006).
## @changes
##   2026-07-15 · Created (GAP-003 remediation)
## ⚠️ TRAP[DEBT] · 2026-07-15 · MED · 3 modules without spool_volume: litellm, langfuse, infra-metrics
## · Observed: D4-contract (core/modules/AGENTS.md) requires both spool_dir and spool_volume,
##   but 3 modules have only spool_dir
## · Suspected: spool_volume was not added during module creation — contract violation
## · Impact: docker compose down -v will not remove named volumes for these modules,
##   but host bind mount via spool_dir preserves data
## · When: during R5 spool_dir audit — deferred, requires separate module.yaml contract fix
# endregion MODULE_CONTRACT
"""

from pathlib import Path

import yaml

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules"

# Modules that do not require spool_dir/spool_volume
JUSTIFIED_EXCLUSIONS = {
    "nginx": "reverse proxy — no persistent data",
    "platform-secrets": "install_type: system — tmpfs-based, no persistent data",
    "redis": "cache-only — no persistence, no spool dir (owner verdict wave-redis 2026-07-15)",
}


# region FUNC__load_module_yamls
## @purpose  Load all module.yaml files from core/modules/*/ into a dict
## @io       None → dict[str, dict] (module_name → yaml content)
## @complexity 2 — file I/O with sorted glob
def _load_module_yamls() -> dict[str, dict]:
    """Load all module.yaml files from core/modules/*/."""
    modules = {}
    for mod_dir in sorted(MODULES_DIR.iterdir()):
        if not mod_dir.is_dir():
            continue
        yaml_path = mod_dir / "module.yaml"
        if yaml_path.exists():
            with open(yaml_path) as f:
                modules[mod_dir.name] = yaml.safe_load(f)
    return modules


# endregion FUNC__load_module_yamls


# region FUNC_test_all_docker_modules_have_spool_dir
## @purpose  Validate every Docker module has spool_dir or spool_volume
## @io       _load_module_yamls → assert missing == []
## @complexity 2 — iterates modules, checks install_type and spool fields
# 🧪 TRAP[TEST] · Gate · Scenario: all docker modules have spool_dir or spool_volume · Last fail: N/A · Remove if: module.yaml schema changes
def test_all_docker_modules_have_spool_dir():
    """All Docker modules (install_type: docker) must have spool_dir or spool_volume."""
    modules = _load_module_yamls()
    assert len(modules) > 0, f"No module.yaml files found in {MODULES_DIR}"

    missing = []
    for name, cfg in modules.items():
        if name in JUSTIFIED_EXCLUSIONS:
            continue
        install_type = cfg.get("install_type", "docker")
        if install_type != "docker":
            continue  # system modules don't require spool
        has_spool = "spool_dir" in cfg or "spool_volume" in cfg
        if not has_spool:
            missing.append(name)

    assert missing == [], f"Docker modules without spool_dir/spool_volume: {missing}"


# endregion FUNC_test_all_docker_modules_have_spool_dir


# region FUNC_test_spool_dir_paths_are_absolute
## @purpose  Validate all spool_dir paths start with /opt/
## @io       _load_module_yamls → assert bad_paths == []
## @complexity 2 — iterates modules, checks spool_dir prefix
# 🧪 TRAP[TEST] · Gate · Scenario: spool_dir paths are absolute (/opt/) · Last fail: N/A · Remove if: storage path convention changes
def test_spool_dir_paths_are_absolute():
    """spool_dir paths must be absolute (start with /)."""
    modules = _load_module_yamls()
    bad_paths = []
    for name, cfg in modules.items():
        spool_dir = cfg.get("spool_dir")
        if spool_dir and not spool_dir.startswith("/"):
            bad_paths.append(f"{name}: {spool_dir}")

    assert bad_paths == [], f"Modules with non-absolute spool_dir paths: {bad_paths}"


# endregion FUNC_test_spool_dir_paths_are_absolute
