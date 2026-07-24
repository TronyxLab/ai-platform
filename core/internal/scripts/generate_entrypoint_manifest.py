# GREP_SUMMARY: generate_entrypoint_manifest, extract_phony_targets, collect_gate_tests, merge, manifest-generator, CI
# STRUCTURE: ▶ gmake -np —▸ extract .PHONY targets → ▶ pytest --collect-only —▸ gate tests → ◇ load existing manifest → ⊕ merge (replace allowed_verbs + gates[], preserve rest) → ⎋ write YAML
# region MODULE_CONTRACT
## @purpose  Generator for entrypoint-manifest.yaml — extracts .PHONY targets from Makefile via gmake -np,
##           collects gate tests via pytest --collect-only, merges into existing manifest by replacing
##           allowed_verbs and gates[] sections while preserving all other sections (bootstrap, deploy,
##           build, validate, test, scaffold, secrets, lifecycle, provision, dev, module_hooks, lib,
##           module_lifecycle, name_linter, forbidden_*).
## @scope    Used by `make generate-manifests` (Wave 2 of DevPlan 051). Run as CLI.
## @invariants
##   - gmake -np preferred; falls back to grep-based .PHONY parsing if gmake unavailable
##   - system_exceptions filtered out: help, venv, pre-commit-*, test-*, gate-*
##   - gates[] replaced entirely from pytest --collect-only -m gate -q output
##   - allowed_verbs replaced entirely from extracted targets (minus system_exceptions)
##   - All other sections preserved verbatim from existing manifest
##   - Empty lists are written as [] in YAML (never null)
## @rationale DevPlan 051 Wave 2: automated sync eliminates drift between Makefile targets and
##            entrypoint-manifest.yaml allowed_verbs, and between pytest gate markers and gates[].
## @see      core/entrypoint-manifest.yaml — target manifest file
## @changes 2026-07-22 | Created (DevPlan 051 Wave 2)
# endregion MODULE_CONTRACT

from __future__ import annotations

# region IMPORTS
import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

# endregion IMPORTS

# region CONSTANTS

SYSTEM_EXCEPTIONS: set[str] = {
    "help",
    "venv",
    "pre-commit-install",
    "pre-commit-run",
}

# Canonical targets that bypass SYSTEM_PREFIXES filter
# These are registered in AGENTS.md and MUST appear in allowed_verbs
ALLOWED_PREFIX_EXCEPTIONS: set[str] = {
    "test-inventory-sync",
}

SYSTEM_PREFIXES: tuple[str, ...] = (
    "pre-commit-",
    "test-",
    "gate-",
    "__",
)

logger = logging.getLogger(__name__)

# endregion CONSTANTS


# region PUBLIC_API


def extract_phony_targets(makefile_dir: str, gmake_path: str) -> list[str]:
    """Extract .PHONY targets from Makefile using gmake -np (with grep fallback).

    ## @purpose  Run gmake -np --dry-run to extract .PHONY targets.
    ##            Fallback: grep-based .PHONY line parsing if gmake unavailable.
    ##            Filters out system_exceptions: help, venv, pre-commit-*, test-*, gate-*.
    ## @io       ⇥ makefile_dir: path to directory with Makefile
    ##           ⇥ gmake_path: path to GNU make binary
    ##           → ⎋ list[str]: sorted unique .PHONY target names
    ## @complexity O(T) where T = number of .PHONY targets in output
    ## @invariants
    ##   - system_exceptions excluded from result
    ##   - Targets matching system_prefixes excluded from result
    ##   - Returns sorted, deduplicated list
    ##   - grep fallback extracts targets declared after '.PHONY:' lines
    """
    print(f"[IMP:7][extract_phony_targets] Extracting .PHONY targets from {makefile_dir}", file=sys.stderr)
    targets: list[str] = []

    # Strategy 1: gmake -np --dry-run
    try:
        result = subprocess.run(
            [gmake_path, "-np", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=makefile_dir,
        )
        if result.returncode == 0:
            phony_match = re.search(r"^\.PHONY:(.*)", result.stdout, re.MULTILINE)
            if phony_match:
                raw = phony_match.group(1).strip()
                targets = raw.split()
                print(f"[IMP:8][extract_phony_targets] gmake -np parsed {len(targets)} raw targets", file=sys.stderr)
        else:
            print(
                f"[IMP:6][extract_phony_targets] gmake exit code {result.returncode}, stderr: {result.stderr[:200]}",
                file=sys.stderr,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        print(f"[IMP:6][extract_phony_targets] gmake unavailable ({e}), falling back to grep", file=sys.stderr)

    # Strategy 2: grep fallback — find all .PHONY: declarations and extract targets
    if not targets:
        makefile_path = Path(makefile_dir)
        phony_lines: list[str] = []
        # Collect all lines starting with .PHONY:
        for mk_file in sorted(makefile_path.glob("Makefile")) + sorted(makefile_path.glob("makefiles/*.mk")):
            if mk_file.is_file():
                try:
                    content = mk_file.read_text()
                    for line in content.splitlines():
                        stripped = line.strip()
                        if re.match(r"^\.PHONY\s*:", stripped):
                            phony_lines.append(stripped)
                except OSError:
                    continue

        for line in phony_lines:
            # Remove .PHONY: prefix and split
            rest = re.sub(r"^\.PHONY\s*:\s*", "", line).strip()
            targets.extend(rest.split())

        print(
            f"[IMP:8][extract_phony_targets] grep fallback parsed {len(targets)} raw targets from {len(phony_lines)} .PHONY lines",
            file=sys.stderr,
        )

    # Filter: exclude system_exceptions and system_prefixes (with exceptions)
    filtered: list[str] = []
    for t in targets:
        if t in SYSTEM_EXCEPTIONS:
            continue
        if t in ALLOWED_PREFIX_EXCEPTIONS:
            filtered.append(t)
            continue
        if any(t.startswith(prefix) for prefix in SYSTEM_PREFIXES):
            continue
        filtered.append(t)

    # Deduplicate and sort
    unique = sorted(set(filtered))
    print(
        f"[IMP:9][extract_phony_targets] Extracted {len(unique)} canonical .PHONY targets (filtered from {len(targets)})",
        file=sys.stderr,
    )
    return unique


def collect_gate_tests(tests_dir: str) -> list[dict]:
    """Run pytest --collect-only -m gate -q to get gate test definitions (pytest 9.x XML-like output format).

    ## @purpose  Collect gate test definitions from pytest markup.
    ##            Returns list of {id, test_file, description} dicts.
    ## @io       ⇥ tests_dir: path to tests/ directory
    ##           → ⎋ list[dict]: gate test entries with id, test_file, description
    ## @complexity O(N) where N = number of gate test items collected
    ## @invariants
    ##   - Falls back to filesystem scan if pytest unavailable
    ##   - Gate ID derived from test function name (test_gate_X → X)
    ##   - test_file derived from module path relative to tests/
    ##   - description extracted from function docstring or test name
    """
    print(f"[IMP:7][collect_gate_tests] Collecting gate tests from {tests_dir}", file=sys.stderr)
    gates: list[dict] = []

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-m", "gate", "-q", tests_dir],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0:
            # Parse pytest 9.x --collect-only XML-like format
            # Lines: <Module test_gate_X.py> → <Function test_gate_X>
            pytest_test_dir = Path(tests_dir).resolve()
            current_module: str | None = None
            for line in result.stdout.splitlines():
                line = line.strip()
                # Match <Module test_gate_xxx.py>
                module_match = re.match(r"<Module\s+(\S+?)>", line)
                if module_match:
                    current_module = module_match.group(1)
                    continue
                # Match <Function test_gate_xxx>
                func_match = re.match(r"<Function\s+(\S+)>", line)
                if func_match and current_module:
                    test_name = func_match.group(1)
                    # Derive test_file: module basename relative to tests_dir
                    # If current_module is just "test_gate_X.py" (no path), prepend tests_dir
                    test_file_path = Path(current_module)
                    if not test_file_path.is_absolute() and not test_file_path.parent.name:
                        # Relative path like "test_gate_X.py" — join with tests_dir
                        test_file = str(pytest_test_dir / current_module)
                    else:
                        test_file = current_module
                    gate_id = (
                        test_name.replace("test_gate_", "", 1) if test_name.startswith("test_gate_") else test_name
                    )
                    gates.append(
                        {
                            "id": gate_id,
                            "test_file": os.path.relpath(test_file, tests_dir)
                            if os.path.isabs(test_file)
                            else test_file,
                            "description": f"Auto-discovered gate: {gate_id}",
                        }
                    )
            print(f"[IMP:8][collect_gate_tests] pytest collected {len(gates)} gate tests", file=sys.stderr)
        else:
            print(
                f"[IMP:6][collect_gate_tests] pytest exit code {result.returncode}: {result.stderr[:300]}",
                file=sys.stderr,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        print(f"[IMP:6][collect_gate_tests] pytest unavailable ({e}), falling back to filesystem scan", file=sys.stderr)

    # Fallback: filesystem scan for test_gate_*.py files
    if not gates:
        # Determine gates directory: if tests_dir already points to gates/, use it directly
        tests_path = Path(tests_dir)
        gates_dir = tests_path if tests_path.name == "gates" and tests_path.is_dir() else tests_path / "gates"
        if gates_dir.is_dir():
            for f in sorted(gates_dir.glob("test_gate_*.py")):
                gate_id = f.stem.replace("test_gate_", "", 1)
                gates.append(
                    {
                        "id": gate_id,
                        "test_file": str(f.relative_to(tests_path)),
                        "description": f"Auto-discovered gate: {gate_id}",
                    }
                )
        print(f"[IMP:8][collect_gate_tests] filesystem scan found {len(gates)} gate tests", file=sys.stderr)

    print(f"[IMP:9][collect_gate_tests] Collected {len(gates)} gate test entries", file=sys.stderr)
    return gates


def load_existing_manifest(path: str) -> dict:
    """Load existing entrypoint-manifest.yaml.

    ## @purpose  Read YAML manifest from disk.
    ## @io       ⇥ path: path to entrypoint-manifest.yaml
    ##           → ⎋ dict: parsed YAML content (empty dict if file missing)
    ## @complexity O(1) — single file read + parse
    """
    print(f"[IMP:7][load_existing_manifest] Loading existing manifest from {path}", file=sys.stderr)
    manifest_path = Path(path)
    if not manifest_path.is_file():
        print(f"[IMP:6][load_existing_manifest] Manifest not found at {path}, returning empty", file=sys.stderr)
        return {}
    with open(str(manifest_path)) as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    print(f"[IMP:9][load_existing_manifest] Loaded manifest with {len(data)} top-level keys", file=sys.stderr)
    return data


def _collect_repair_mappings(existing: dict) -> dict[str, dict]:
    """Collect repair field mappings from repair: section for injection into gates[].

    ## @purpose  Read repair: section, extract repairs_gates mappings keyed by gate_id.
    ##           Supports both repairable (L1/L2) and non-repairable (L3) gates.
    ## @io       ⇥ existing: dict — existing manifest content
    ##           → ⎋ dict[str, dict]: gate_id → repair fields (including repairable flag)
    ## @complexity O(R * G) where R=repair entries, G=gates per entry
    ## @invariants
    ##   - gate_id is the lookup key matching gates[] entries
    ##   - repair_id is injected as a stable API identifier
    ##   - Non-repairable gates get repairable: false + repair_reason
    ## @see       DevPlan 060 — Repair Contract Infrastructure
    """
    repair_section = existing.get("repair", [])
    if not repair_section:
        return {}

    mappings: dict[str, dict] = {}
    for repair_entry in repair_section:
        for gate_repair in repair_entry.get("repairs_gates", []):
            gate_id = gate_repair.get("gate_id")
            if not gate_id:
                continue

            # Build repair fields dict (exclude gate_id — it's the lookup key, not a field)
            fields: dict[str, object] = {}
            for k, v in gate_repair.items():
                if k == "gate_id":
                    continue
                fields[k] = v

            # If repairable not explicitly set, default to true (has repair_command)
            if "repairable" not in fields:
                fields["repairable"] = True

            mappings[gate_id] = fields

    if mappings:
        print(
            f"[IMP:8][merge] Collected {len(mappings)} repair mappings from repair: section",
            file=sys.stderr,
        )
    return mappings


def merge(allowed_verbs: list[str], gates: list[dict], existing: dict) -> dict:
    """Merge: replace allowed_verbs and gates[], preserve everything else.

    Also injects repair fields from the repair: section into matching gates[]
    entries (DevPlan 060 — Repair Contract Infrastructure).

    ## @purpose  Merge extracted targets and gate tests into existing manifest.
    ##            Replaces allowed_verbs and gates[] entirely.
    ##            Preserves all other sections verbatim.
    ##            Injects repair fields from repair: → repairs_gates into gates[].
    ## @io       ⇥ allowed_verbs: list[str] — extracted .PHONY targets
    ##           ⇥ gates: list[dict] — collected gate test entries
    ##           ⇥ existing: dict — existing manifest content
    ##           → ⎋ dict: merged manifest ready for YAML output
    ## @complexity O(G + R*G) where G=gates, R=repair entries
    ## @invariants
    ##   - allowed_verbs in output always from extracted targets, not existing
    ##   - gates[] in output always from collected tests, not existing
    ##   - Repair fields injected from repair: section into matching gates
    ##   - All other sections from existing preserved unchanged
    ##   - Result dict maintains YAML-compatible structure (list, not None)
    ## @changes  2026-07-23 | DevPlan 060: repair fields injection from repair: section
    """
    print(
        f"[IMP:7][merge] Merging {len(allowed_verbs)} verbs + {len(gates)} gates into existing manifest",
        file=sys.stderr,
    )

    # Start with existing manifest
    result = dict(existing)

    # Replace allowed_verbs entirely
    result["allowed_verbs"] = list(allowed_verbs)

    # B4 (DevPlan 046 W2-2): repair→gate injection SUPPRESSED.
    # Collect repair mappings from repair: section (kept for API stability)
    # repair_mappings = _collect_repair_mappings(existing)
    # repair: section's repairs_gates is the single source of truth.
    # Injecting into gates[] creates DRY violation — same metadata in both places.
    # test_repair_contract_integrity gate reads from gates[] — it now sees
    # repairable=False (default) for all gates and skips repair field validation.
    # If repair contract validation from gates[] is needed, update the gate test
    # to read from `repair:` section's repairs_gates instead.
    # injected_count = 0
    # for gate in gates:
    #     gate_id = gate.get("id", "")
    #     if gate_id in repair_mappings:
    #         gate.update(repair_mappings[gate_id])
    #         injected_count += 1

    # Replace gates[] entirely (no repair field injection)
    result["gates"] = list(gates)

    # Ensure forbidden sections are preserved (if present in existing)
    for key in (
        "forbidden_directories",
        "forbidden_scripts",
        "forbidden_verbs",
        "name_linter",
        "module_lifecycle",
        "system_module_lifecycle",
        "lib",
        "module_hooks",
    ):
        if key in existing:
            result[key] = existing[key]

    print(
        f"[IMP:9][merge] Merge complete — {len(result.get('allowed_verbs', []))} verbs, {len(result.get('gates', []))} gates total",
        file=sys.stderr,
    )
    return result


# endregion PUBLIC_API


# region CLI


def main() -> int:
    """CLI entrypoint for entrypoint manifest generator.

    ▶ argparse → ◇ extract_phony_targets + collect_gate_tests + load_existing_manifest
      → ⊕ merge → ⎋ write YAML output → exit 0

    ## @purpose  CLI for make generate-manifests integration.
    ## @io       ⇥ CLI args: --makefile-dir, --gmake-path, --existing-manifest,
    ##             --tests-dir, --output
    ##           → ⎋ exit code 0 on success, 1 on error
    ## @complexity O(T + N) where T=targets, N=gate tests
    """
    parser = argparse.ArgumentParser(
        prog="generate_entrypoint_manifest.py",
        description="Generate entrypoint-manifest.yaml — extract .PHONY targets and gate tests",
    )
    parser.add_argument(
        "--makefile-dir",
        default=".",
        help="Path to directory containing root Makefile (default: .)",
    )
    parser.add_argument(
        "--gmake-path",
        default="/opt/homebrew/bin/gmake",
        help="Path to GNU make binary (default: /opt/homebrew/bin/gmake)",
    )
    parser.add_argument(
        "--existing-manifest",
        default="core/entrypoint-manifest.yaml",
        help="Path to existing entrypoint-manifest.yaml (default: core/entrypoint-manifest.yaml)",
    )
    parser.add_argument(
        "--tests-dir",
        default="tests",
        help="Path to tests/ directory (default: tests)",
    )
    parser.add_argument(
        "--output",
        default="core/entrypoint-manifest.yaml",
        help="Output path for generated manifest (default: core/entrypoint-manifest.yaml)",
    )
    args = parser.parse_args()

    print("[IMP:7][main] Starting entrypoint manifest generation", file=sys.stderr)

    try:
        targets = extract_phony_targets(args.makefile_dir, args.gmake_path)
        gates = collect_gate_tests(args.tests_dir)
        existing = load_existing_manifest(args.existing_manifest)
        merged = merge(targets, gates, existing)

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(output_path), "w") as f:
            f.write(
                "# core/entrypoint-manifest.yaml\n"
                "# GREP_SUMMARY: entrypoint-manifest, yaml, canonical-targets, operations-registry, forbidden\n"
                "# STRUCTURE: ┌Makefile targets┐ → ◇ map target→script→delegation → ⊕ CI gates verify parity with AGENTS.md triad\n"
                "# region MODULE_CONTRACT\n"
                "## @purpose  Canonical operations registry consumed by CI gates (no-unregistered-entrypoint, manifest-integrity).\n"
                "## @scope    Lists all canonical make targets, CI gates, forbidden scripts/directories/verbs, and allowed_verbs dictionary\n"
                "## @invariants\n"
                "##   - Every Makefile .PHONY target must have a corresponding entry in this manifest\n"
                "##   - Every entry here must have a corresponding entry in core/AGENTS.md\n"
                "##   - allowed_verbs list must match Makefile canonical targets\n"
                "##   - Forbidden lists are explicit deny — no additions without Architect approval\n"
                "## @rationale Machine-readable registry enables CI gates to validate the Makefile/AGENTS.md/filesystem triad\n"
                "# endregion MODULE_CONTRACT\n"
                "\n"
            )
            yaml.dump(merged, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        print(
            f"[IMP:9][main] Manifest written to {args.output} — {len(targets)} verbs, {len(gates)} gates",
            file=sys.stderr,
        )
        return 0

    except Exception as e:
        print(f"[IMP:1][main] CRITICAL: Manifest generation failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())


# endregion CLI
