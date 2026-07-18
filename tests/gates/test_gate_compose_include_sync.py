# GREP_SUMMARY: gate compose-include-sync discover_modules regeneration idempotent root-compose-include
# STRUCTURE: ▶ discover_modules() → ◇ yaml include match → ◇ update_compose_include noop on committed → ⎋ assert
# region MODULE_CONTRACT
## @purpose  Gate tests for compose include section synchronization with discover_modules()
## @scope    Validates that docker-compose.yml include: section matches discover_modules output
##           and that update_compose_include() is idempotent on the committed file.
##           Also validates root compose uses include: not inline services (absorbed from pluggability_profiles).
## @invariants
##   - include: section paths == discover_modules() output (exact list equality, sorted)
##   - update_compose_include() on a copy of the committed file returns False (no change)
##   - Root compose must NOT have inline services: key (only include:)
## @rationale
##   Q: Почему два теста, а не один текстовый diff?
##   A: Только changed is False — вакуумно-зелёный при regex-роте (доказано багом F5).
##      Только семантическое сравнение — не ловит formatting-дрейф.
##      Пара тестов закрывает оба класса.
## @changes — 2026-07-15 | Created per DevPlan 007 TASK-T2
## @changes — 2026-07-16 | Absorbed test_root_compose_uses_include from pluggability_profiles (T7 merge)
# endregion MODULE_CONTRACT

import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = PROJECT_ROOT / "core" / "modules"
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
DISCOVER_MODULES_PATH = PROJECT_ROOT / "core" / "internal" / "bootstrap" / "discover_modules.py"


def _import_discover_modules():
    """Import discover_modules module using importlib (no sys.path manipulation)."""
    spec = importlib.util.spec_from_file_location("discover_modules", DISCOVER_MODULES_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_include_matches_discovered_modules():
    """include: section in docker-compose.yml == discover_modules() output (sorted)."""
    assert COMPOSE_FILE.exists(), f"docker-compose.yml not found at {COMPOSE_FILE}"

    # Parse actual include from yaml
    with open(COMPOSE_FILE) as f:
        data = yaml.safe_load(f)
    actual_includes = [entry["path"] for entry in data.get("include", [])]

    # Discover modules
    mod = _import_discover_modules()
    expected_modules = mod.discover_modules(MODULES_DIR)

    print(f"[IMP:8][gate] docker-compose.yml include entries: {len(actual_includes)}")
    for p in actual_includes:
        print(f"  - {p}")
    print(f"[IMP:8][gate] discover_modules() entries: {len(expected_modules)}")
    for p in expected_modules:
        print(f"  - {p}")

    assert actual_includes == expected_modules, (
        f"GATE_COMPOSE_INCLUDE_MISMATCH: docker-compose.yml include section differs from "
        f"discover_modules() output.\n"
        f"  In compose: {actual_includes}\n"
        f"  Discovered: {expected_modules}\n"
        f"  Missing from compose: {set(expected_modules) - set(actual_includes)}\n"
        f"  Extra in compose: {set(actual_includes) - set(expected_modules)}"
    )
    print("[IMP:9][gate] PASS: include section matches discover_modules()")


@pytest.mark.gate
def test_regeneration_is_noop_on_committed(tmp_path):
    """update_compose_include() on a copy of the committed file returns False (no change)."""
    assert COMPOSE_FILE.exists(), f"docker-compose.yml not found at {COMPOSE_FILE}"

    # Copy committed file to temp
    compose_copy = tmp_path / "docker-compose.yml"
    shutil.copy2(COMPOSE_FILE, compose_copy)

    # Read original content
    original_content = compose_copy.read_bytes()

    # Run update_compose_include on the copy
    mod = _import_discover_modules()
    modules = mod.discover_modules(MODULES_DIR)
    changed = mod.update_compose_include(compose_copy, modules)

    new_content = compose_copy.read_bytes()

    print(f"[IMP:8][gate] update_compose_include returned changed={changed}")
    print(f"[IMP:8][gate] Original size: {len(original_content)} bytes, New size: {len(new_content)} bytes")

    assert changed is False, (
        "GATE_COMPOSE_INCLUDE_NOOP: update_compose_include() returned True on a copy of the "
        "committed docker-compose.yml. The committed file needs regeneration."
    )
    assert original_content == new_content, (
        "GATE_COMPOSE_INCLUDE_BYTECHANGE: update_compose_include() modified the file content "
        "even though it returned False. Bug in implementation."
    )
    print("[IMP:9][gate] PASS: update_compose_include is idempotent on committed file")


@pytest.mark.gate
def test_root_compose_uses_include():
    """Root docker-compose.yml uses include: (no inline services).

    ## @purpose — Validate pluggability model: root compose must use include: for module
    ##            discovery, not inline services. Absorbed from pluggability_profiles (T7 merge).
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(1)
    """
    assert COMPOSE_FILE.exists(), f"Root compose not found at {COMPOSE_FILE}"
    with open(COMPOSE_FILE) as f:
        data = yaml.safe_load(f)

    has_include = "include" in data
    has_inline_services = "services" in data

    print(f"[IMP:8][gate] Root compose has include: {has_include}")
    print(f"[IMP:8][gate] Root compose has inline services: {has_inline_services}")

    assert has_include, "[IMP:9][gate] FAIL: Root compose does not use include:"
    assert not has_inline_services, "[IMP:9][gate] FAIL: Root compose has inline services (should use include only)"
    print("[IMP:9][gate] PASS: Root compose uses include: with no inline services")
