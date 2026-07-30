# GREP_SUMMARY: yaml-deterministic byte-identical output deterministic-generation reproducibility
# STRUCTURE: ▶ import generator modules → ◇ run generate() twice with identical inputs → ⊕ byte-compare outputs → ⎋ pass/fail per generator
# region MODULE_CONTRACT
## @purpose  Verify that all YAML manifest generators produce byte-identical output on
##           two consecutive runs with identical inputs. Non-deterministic output breaks
##           `make check-manifests` (git diff) and CI gates.
## @scope    CI gate — imports and calls generator functions directly (native pytest, no subprocess)
## @invariants
##   - Each generator is called twice with identical read-only inputs
##   - Outputs are written to tmp_path (never modify source files)
##   - Comparison is byte-level (hash or direct str comparison)
##   - Test covers: generate_secrets_manifest, generate_platform_env, generate_entrypoint_manifest
##   - Input files are read from the real project (read-only)
## @rationale DevPlan 090 — Deterministic Generation. Non-deterministic output is the #1 cause
##            of phantom drift in `make check-manifests`. If a generator produces different output
##            on each run, git diff shows false positives, and CI gates fail randomly.
## @changes 2026-07-30 · Created — DevPlan 090 gate
# endregion MODULE_CONTRACT

import importlib
import logging
import os
import sys
import tempfile
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

_PROJECT_ROOT: str = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)

# ── Generator info: (module_path, function_name, kwargs_builder) ──
# kwargs_builder(tmp_path) returns dict of kwargs for the generate function
_GENERATORS: list[dict] = []


def _build_secrets_kwargs(tmp_path: Path) -> dict:
    """Build kwargs for generate_secrets_manifest.generate()."""
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "core", "internal", "scripts"))
    import generate_secrets_manifest as gsm  # type: ignore[import-untyped]

    secret_defs = gsm.load_secret_definitions(
        Path(_PROJECT_ROOT) / "core" / "secret-definitions.yaml"
    )
    modules = gsm.load_module_yamls(
        Path(_PROJECT_ROOT) / "core" / "modules"
    )
    return {"secret_defs": secret_defs, "modules": modules}


def _build_platform_env_kwargs(tmp_path: Path) -> dict:
    """Build kwargs for generate_platform_env functions."""
    # We test generate_platform_env_yaml and generate_smoke_env_py + generate_helpers_py
    return {"tmp_path": tmp_path}


def _build_entrypoint_kwargs(tmp_path: Path) -> dict:
    """Build kwargs for generate_entrypoint_manifest functions."""
    # Test extract_phony_targets + merge with empty existing
    return {"tmp_path": tmp_path}


# ── Helper: compute SHA256 of a string ──
import hashlib


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_secrets_manifest_deterministic
## @purpose  Verify generate_secrets_manifest is byte-identical on 2 runs
## 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · Non-deterministic secrets-manifest output
## · Scenario: Run generate() twice with same inputs; outputs must be byte-identical
## · Last fail: N/A (new gate)
## · Remove if: secrets manifest generation is superseded
def test_secrets_manifest_deterministic(tmp_path, caplog) -> None:
    """Verify generate_secrets_manifest.generate() produces byte-identical output
    on two consecutive runs with identical inputs."""
    caplog.set_level(logging.INFO)
    print("[IMP:8][test_secrets_manifest_deterministic] Testing secrets manifest determinism...", file=sys.stderr)

    # Import the module
    scripts_dir = os.path.join(_PROJECT_ROOT, "core", "internal", "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        import generate_secrets_manifest as gsm  # type: ignore[import-untyped]
    except ImportError as e:
        pytest.fail(f"Cannot import generate_secrets_manifest: {e}")
        return

    # Run twice with identical inputs
    secret_defs = gsm.load_secret_definitions(
        Path(_PROJECT_ROOT) / "core" / "secret-definitions.yaml"
    )
    modules = gsm.load_module_yamls(
        Path(_PROJECT_ROOT) / "core" / "modules"
    )

    print(f"[IMP:7][test_secrets_manifest_deterministic] Loaded {len(secret_defs)} secret defs, {len(modules)} modules", file=sys.stderr)

    # Run 1
    result1 = gsm.generate(secret_defs, modules)

    # Run 2
    result2 = gsm.generate(secret_defs, modules)

    # Dump to YAML strings
    import yaml
    yaml_str1 = yaml.dump(result1, default_flow_style=False, sort_keys=False)
    yaml_str2 = yaml.dump(result2, default_flow_style=False, sort_keys=False)

    hash1 = _sha256(yaml_str1)
    hash2 = _sha256(yaml_str2)

    print(f"[IMP:7][test_secrets_manifest_deterministic] Run 1 hash: {hash1}", file=sys.stderr)
    print(f"[IMP:7][test_secrets_manifest_deterministic] Run 2 hash: {hash2}", file=sys.stderr)

    assert hash1 == hash2, (
        f"generate_secrets_manifest.generate() produced DIFFERENT output on 2nd run!\n"
        f"Run 1 SHA256: {hash1}\n"
        f"Run 2 SHA256: {hash2}\n\n"
        f"This indicates non-deterministic generation. Common causes:\n"
        f"  - Unordered dict iteration (use sorted() or OrderedDict)\n"
        f"  - Timestamps or random values in output\n"
        f"  - File modification time dependence"
    )

    # Also verify output is valid YAML by writing to file and re-reading
    out1 = tmp_path / "run1.yaml"
    out2 = tmp_path / "run2.yaml"
    out1.write_text(yaml_str1)
    out2.write_text(yaml_str2)

    assert out1.read_bytes() == out2.read_bytes(), (
        f"Byte-level comparison FAILED for secrets manifest!\n"
        f"Run 1 ({len(out1.read_bytes())} bytes) != Run 2 ({len(out2.read_bytes())} bytes)"
    )

    logger.info(
        "[IMP:9][test_secrets_manifest_deterministic] PASS — secrets manifest is deterministic (SHA256: %s)",
        hash1,
    )


# endregion FUNC_test_secrets_manifest_deterministic


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_platform_env_deterministic
## @purpose  Verify generate_platform_env helper functions are byte-identical on 2 runs
## 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · Non-deterministic platform-env helpers output
## · Scenario: Run generate_smoke_env_py() and generate_helpers_py() twice; outputs must be identical
## · Last fail: N/A (new gate)
## · Remove if: platform env generation is superseded
def test_platform_env_deterministic(tmp_path, caplog) -> None:
    """Verify generate_platform_env helper functions produce byte-identical output
    on two consecutive runs with identical inputs."""
    caplog.set_level(logging.INFO)
    print("[IMP:8][test_platform_env_deterministic] Testing platform env determinism...", file=sys.stderr)

    scripts_dir = os.path.join(_PROJECT_ROOT, "core", "internal", "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        import generate_platform_env as gpe  # type: ignore[import-untyped]
    except ImportError as e:
        pytest.fail(f"Cannot import generate_platform_env: {e}")
        return

    # Load real ci_defaults for deterministic test data
    secret_defs_path = Path(_PROJECT_ROOT) / "core" / "secret-definitions.yaml"
    ci_defaults = gpe.load_ci_defaults(secret_defs_path)

    print(f"[IMP:7][test_platform_env_deterministic] Loaded {len(ci_defaults)} ci_defaults", file=sys.stderr)

    # Test generate_smoke_env_py determinism
    py1 = gpe.generate_smoke_env_py(ci_defaults)
    py2 = gpe.generate_smoke_env_py(ci_defaults)

    hash1 = _sha256(py1)
    hash2 = _sha256(py2)
    print(f"[IMP:7][test_platform_env_deterministic] smoke_env Run 1 hash: {hash1}", file=sys.stderr)
    print(f"[IMP:7][test_platform_env_deterministic] smoke_env Run 2 hash: {hash2}", file=sys.stderr)

    assert hash1 == hash2, (
        f"generate_smoke_env_py() produced DIFFERENT output on 2nd run!\n"
        f"Run 1 SHA256: {hash1}\n"
        f"Run 2 SHA256: {hash2}"
    )

    # Test generate_helpers_py determinism
    hp1 = gpe.generate_helpers_py(ci_defaults)
    hp2 = gpe.generate_helpers_py(ci_defaults)

    hash_hp1 = _sha256(hp1)
    hash_hp2 = _sha256(hp2)
    print(f"[IMP:7][test_platform_env_deterministic] helpers Run 1 hash: {hash_hp1}", file=sys.stderr)
    print(f"[IMP:7][test_platform_env_deterministic] helpers Run 2 hash: {hash_hp2}", file=sys.stderr)

    assert hash_hp1 == hash_hp2, (
        f"generate_helpers_py() produced DIFFERENT output on 2nd run!\n"
        f"Run 1 SHA256: {hash_hp1}\n"
        f"Run 2 SHA256: {hash_hp2}"
    )

    # Test platform-env.yaml determinism
    infra = gpe.load_infra(Path(_PROJECT_ROOT) / "core" / "platform-infra.yaml")
    profiles = gpe.discover_profiles(Path(_PROJECT_ROOT) / "core" / "modules")

    # Fixed port mappings for determinism test
    port_mappings = gpe.scan_compose_ports(Path(_PROJECT_ROOT) / "core" / "modules")
    test_ports = gpe.scan_test_ports(Path(_PROJECT_ROOT) / "core" / "modules")
    non_secret = {k: str(v) for k, v in infra.get("env_defaults", {}).items()}
    merged_env_defaults = {**non_secret, **ci_defaults}

    yaml1 = gpe.generate_platform_env_yaml(
        infra=infra,
        profiles=profiles,
        port_mappings=port_mappings,
        test_ports=test_ports,
        env_defaults=merged_env_defaults,
    )
    yaml2 = gpe.generate_platform_env_yaml(
        infra=infra,
        profiles=profiles,
        port_mappings=port_mappings,
        test_ports=test_ports,
        env_defaults=merged_env_defaults,
    )

    hash_y1 = _sha256(yaml1)
    hash_y2 = _sha256(yaml2)
    print(f"[IMP:7][test_platform_env_deterministic] platform-env Run 1 hash: {hash_y1}", file=sys.stderr)
    print(f"[IMP:7][test_platform_env_deterministic] platform-env Run 2 hash: {hash_y2}", file=sys.stderr)

    assert hash_y1 == hash_y2, (
        f"generate_platform_env_yaml() produced DIFFERENT output on 2nd run!\n"
        f"Run 1 SHA256: {hash_y1}\n"
        f"Run 2 SHA256: {hash_y2}"
    )

    logger.info(
        "[IMP:9][test_platform_env_deterministic] ALL PASS — smoke_env + helpers + platform-env are deterministic"
    )


# endregion FUNC_test_platform_env_deterministic


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_entrypoint_manifest_deterministic
## @purpose  Verify generate_entrypoint_manifest merge() is byte-identical on 2 runs
## 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · Non-deterministic entrypoint-manifest output
## · Scenario: Run extract_phony_targets() + merge() twice; outputs must be identical
## · Last fail: N/A (new gate)
## · Remove if: entrypoint manifest generation is superseded
def test_entrypoint_manifest_deterministic(tmp_path, caplog) -> None:
    """Verify generate_entrypoint_manifest merge() produces byte-identical output
    on two consecutive runs with identical inputs."""
    caplog.set_level(logging.INFO)
    print("[IMP:8][test_entrypoint_manifest_deterministic] Testing entrypoint manifest determinism...", file=sys.stderr)

    scripts_dir = os.path.join(_PROJECT_ROOT, "core", "internal", "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        import generate_entrypoint_manifest as gem  # type: ignore[import-untyped]
    except ImportError as e:
        pytest.fail(f"Cannot import generate_entrypoint_manifest: {e}")
        return

    # Test merge() determinism with fixed inputs
    allowed_verbs = ["deploy", "build", "test", "lint", "validate"]
    gates = [
        {"id": "dag-acyclic", "test_file": "test_gate_manifest_dag_acyclic.py", "description": "DAG acyclicity gate"},
        {"id": "no-self-read", "test_file": "test_gate_generate_entrypoint_manifest_no_self_read.py", "description": "G3 no-self-read gate"},
    ]
    existing = {
        "forbidden_directories": ["core/scripts/e2e", "core/scripts"],
        "forbidden_scripts": ["dev.sh", "platform-push.sh"],
        "forbidden_verbs": ["push-core", "deploy-node"],
        "module_lifecycle": ["build", "up", "down", "start", "stop", "restart", "status", "logs", "backup"],
    }

    result1 = gem.merge(allowed_verbs, gates, existing)
    result2 = gem.merge(allowed_verbs, gates, existing)

    import yaml
    yaml_str1 = yaml.dump(result1, default_flow_style=False, sort_keys=False)
    yaml_str2 = yaml.dump(result2, default_flow_style=False, sort_keys=False)

    hash1 = _sha256(yaml_str1)
    hash2 = _sha256(yaml_str2)

    print(f"[IMP:7][test_entrypoint_manifest_deterministic] Run 1 hash: {hash1}", file=sys.stderr)
    print(f"[IMP:7][test_entrypoint_manifest_deterministic] Run 2 hash: {hash2}", file=sys.stderr)

    assert hash1 == hash2, (
        f"merge() produced DIFFERENT output on 2nd run!\n"
        f"Run 1 SHA256: {hash1}\n"
        f"Run 2 SHA256: {hash2}"
    )

    # Also test extract_phony_targets determinism (calls make -np)
    import tempfile
    import subprocess

    # We test with a fixed Makefile to avoid CI variance
    test_makefile_dir = tmp_path / "makefile_test"
    test_makefile_dir.mkdir()
    test_makefile = test_makefile_dir / "Makefile"
    test_makefile.write_text(
        ".PHONY: deploy build test lint validate\n"
        "deploy:\n\techo deploy\n"
        "build:\n\techo build\n"
        "test:\n\techo test\n"
        "lint:\n\techo lint\n"
        "validate:\n\techo validate\n"
    )

    # Find gmake or make
    gmake_path = "gmake"
    try:
        subprocess.run([gmake_path, "--version"], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        gmake_path = "make"

    targets1 = gem.extract_phony_targets(str(test_makefile_dir), gmake_path)
    targets2 = gem.extract_phony_targets(str(test_makefile_dir), gmake_path)

    assert targets1 == targets2, (
        f"extract_phony_targets() returned different results!\n"
        f"Run 1: {targets1}\n"
        f"Run 2: {targets2}"
    )
    print(f"[IMP:9][test_entrypoint_manifest_deterministic] extract_phony_targets: stable — {len(targets1)} targets", file=sys.stderr)

    logger.info(
        "[IMP:9][test_entrypoint_manifest_deterministic] PASS — merge() + extract_phony_targets are deterministic"
    )


# endregion FUNC_test_entrypoint_manifest_deterministic
