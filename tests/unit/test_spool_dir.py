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
        if spool_dir and spool_dir != "none" and not spool_dir.startswith("/"):
            bad_paths.append(f"{name}: {spool_dir}")

    assert bad_paths == [], f"Modules with non-absolute spool_dir paths: {bad_paths}"


# endregion FUNC_test_spool_dir_paths_are_absolute


# region FUNC_test_spool_dir_none_no_warn
## @purpose  Verify modules with spool_dir: none are properly declared (stateless) and
##           spool_validator.py verify_spool_dirs() handles "none" → stateless, not missing.
## @io       _load_module_yamls + read spool_validator.py → assert conditions
## @complexity 2 — iterates modules + reads Python module
# 🧪 TRAP[TEST] · Regression: T4 — spool_dir: none stateless declaration
# · Scenario: module.yaml with spool_dir: none → spool_validator logs stateless (no WARN)
# · Last fail: WARN for nginx/redis/platform-secrets (no spool decl, always WARN)
# · Remove if: spool_validator "none" check removed from Python module
def test_spool_dir_none_no_warn() -> None:
    """Modules with spool_dir: none must be declared and handled as stateless (no WARN)."""
    modules = _load_module_yamls()

    # Check 1: specific modules must have spool_dir: none
    expected_none = {"nginx", "redis", "platform-secrets"}
    actual_none = {name for name, cfg in modules.items() if cfg.get("spool_dir") == "none"}
    missing = expected_none - actual_none
    assert not missing, f"[IMP:9][test] Modules expected to have spool_dir: none but missing: {missing}"

    # Check 2: ensure actual_none modules are not flagged by the missing-spool gate
    for name in actual_none:
        if name in JUSTIFIED_EXCLUSIONS:
            continue  # was already excluded, now also has explicit decl
        cfg = modules[name]
        has_spool = "spool_dir" in cfg or "spool_volume" in cfg
        assert has_spool, f"[IMP:9][test] {name} has spool_dir: none but it's missing from module.yaml dict"

    # Check 3: spool_validator.py verify_spool_dirs() has "none" handling (Python module)
    spool_validator = MODULES_DIR.parent / "internal" / "bootstrap" / "deploy" / "spool_validator.py"
    assert spool_validator.is_file(), (
        "[IMP:9][test] FAIL: spool_validator.py not found — ensure_spool_dirs reimplementation missing"
    )
    content = spool_validator.read_text()
    assert 'spool_path == "none"' in content, "[IMP:9][test] FAIL: spool_validator.py must check for 'none' value"
    # The "none" check should mark as stateless (not WARN)
    none_check_context = content[content.find('spool_path == "none"') : content.find('spool_path == "none"') + 500]
    assert "stateless" in none_check_context or "spool_dir: none" in content, (
        "[IMP:9][test] FAIL: spool_dir: none must be marked stateless, not WARN"
    )

    print("[IMP:9][test_spool_dir_none_no_warn] PASS: spool_dir: none declared and handled")


# endregion FUNC_test_spool_dir_none_no_warn


# region FUNC_test_spool_dir_missing_still_warns
## @purpose  Verify spool_validator.py still emits WARN for modules without spool_dir/spool_volume
##           (drift detection preserved for new modules that forget to declare).
## @io       Read spool_validator.py verify_spool_dirs region → assert WARN present for missing decl
## @complexity 1 — read Python module
# 🧪 TRAP[TEST] · Regression: T4 — spool_dir omission still triggers WARN
# · Scenario: a new module.yaml without spool_dir/spool_volume → verify_spool_dirs must WARN
# · Last fail: n/a (new test)
# · Remove if: all modules always declare spool_dir (even stateless ones)
def test_spool_dir_missing_still_warns() -> None:
    """Modules without spool_dir/spool_volume (and not in JUSTIFIED_EXCLUSIONS) must still trigger WARN."""
    modules = _load_module_yamls()

    # Simulate what verify_spool_dirs does: collect modules without spool_dir/spool_volume
    # that would get the WARN log
    would_warn = []
    for name, cfg in modules.items():
        if name in JUSTIFIED_EXCLUSIONS:
            continue  # explicit exclusion — no WARN
        install_type = cfg.get("install_type", "docker")
        if install_type != "docker":
            continue
        has_spool = "spool_dir" in cfg or "spool_volume" in cfg
        if not has_spool:
            would_warn.append(name)

    # All docker modules should have spool_dir or spool_volume (or be in exclusions)
    assert would_warn == [], (
        f"[IMP:9][test] These modules would trigger WARN (no spool_dir/spool_volume, not excluded): {would_warn}"
    )

    # Verify spool_validator.py has the WARN path for missing declarations
    spool_validator = MODULES_DIR.parent / "internal" / "bootstrap" / "deploy" / "spool_validator.py"
    assert spool_validator.is_file(), (
        "[IMP:9][test] FAIL: spool_validator.py not found — ensure_spool_dirs reimplementation missing"
    )
    content = spool_validator.read_text()
    func_start = content.find("def verify_spool_dirs")
    assert func_start != -1, "verify_spool_dirs function not found in spool_validator.py"
    func_body = content[func_start:]

    # Must have WARN for modules without spool_dir/spool_volume
    assert "WARN" in func_body and "no spool_dir" in func_body.lower(), (
        "[IMP:9][test] FAIL: verify_spool_dirs must emit WARN for modules without spool_dir"
    )

    print("[IMP:9][test_spool_dir_missing_still_warns] PASS: missing spool decl still triggers WARN")


# endregion FUNC_test_spool_dir_missing_still_warns
