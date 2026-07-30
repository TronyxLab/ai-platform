# GREP_SUMMARY: no-shell-manifest-generators python-only generate_ grep-anti-drift
# STRUCTURE: ▶ verify all canonical generators exist as .py → ◇ shell scripts with generator-name shell functions? → ⊕ detect generator logic in shell → ⎋ pass/fail
# region MODULE_CONTRACT
## @purpose  Verify that ALL manifest generators are implemented in Python (.py), NOT in shell (.sh).
##           This gate checks specifically for MANIFEST GENERATORS (the ones called by
##           `make generate-manifests`), not for all inline python3 in shell scripts.
##           Non-generator scripts with legacy inline python3 are tracked separately.
## @scope    CI gate — static analysis of core/entrypoints/, core/internal/, core/internal/scripts/
## @invariants
##   - All canonical generators are .py files in core/internal/scripts/:
##     generate_secrets_manifest.py, generate_platform_env.py, generate_entrypoint_manifest.py,
##     generate_agents_md.py, sync_env_defaults.py
##   - No shell script in core/entrypoints/ or core/internal/ defines a shell function
##     named after a canonical generator (e.g., `generate_secrets_manifest() {`)
##   - No shell script directly implements manifest generation logic (generator functions
##     are ONLY in Python files). Thin facades calling `python3 generate_X.py` are allowed.
##   - Legacy inline python3 in non-generator scripts (install-docker, validate, project-list,
##     etc.) are out of scope — tracked as tech debt, not this gate.
## @rationale DevPlan 090 — Python-only generators. Manifest generators must be testable,
##            grep-able Python modules, not shell scripts prone to quoting bugs.
## @changes 2026-07-30 · Created — DevPlan 090 gate
# endregion MODULE_CONTRACT

import logging
import os
import re
import sys

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

_PROJECT_ROOT: str = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)

# Canonical manifest generators that MUST be Python-only.
# Each entry: (name, expected_py_file)
_CANONICAL_GENERATORS: list[tuple[str, str]] = [
    ("generate_secrets_manifest", "generate_secrets_manifest.py"),
    ("generate_platform_env", "generate_platform_env.py"),
    ("generate_entrypoint_manifest", "generate_entrypoint_manifest.py"),
    ("generate_agents_md", "generate_agents_md.py"),
    # sync_env_defaults produces .env.example from SoT — Python-only
    ("sync_env_defaults", "sync_env_defaults.py"),
]

# Patterns to detect if a shell script directly implements a manifest generator
# (i.e., defines a shell function with the same name as a canonical generator)
_SHELL_GENERATOR_FUNC_PATTERN: re.Pattern = re.compile(
    r"(?:"
    + "|".join(re.escape(name) for name, _ in _CANONICAL_GENERATORS)
    + r")\s*\(\s*\)\s*\{"
)

# Detect if a shell script calls the generator as a thin facade
_GENERATOR_CALL_PATTERN: re.Pattern = re.compile(
    r"python3\s+core/internal/scripts/(?:"
    + "|".join(re.escape(file) for _, file in _CANONICAL_GENERATORS)
    + r")"
)


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_no_shell_generators
## @purpose  Verify no manifest generator is implemented in shell — all are .py
## @io       ⇥ filesystem scan + grep → ⎋ assert pass/fail
## @complexity O(F * L) where F = .sh files, L = lines per file
## 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · Shell manifest generators forbidden
## · Scenario: Any canonical manifest generator with business logic implemented in shell
##             is a violation. Thin facades calling `python3 generate_X.py` are allowed.
## · Last fail: N/A (new gate)
## · Remove if: all generators are permanently migrated to Python (gate becomes always-green)
def test_no_shell_generators(caplog) -> None:
    """Verify no manifest generator is implemented in shell.

    Checks:
    1. All canonical generators exist as .py files in core/internal/scripts/
    2. No .sh file defines a shell function with a canonical generator name
       (e.g., `generate_secrets_manifest() {`) — that would mean the logic is in shell
    3. If a shell script references a generator, it must be a thin facade call
       (python3 generate_X.py), not inline implementation
    """
    caplog.set_level(logging.INFO)
    print("[IMP:8][test_no_shell_generators] Scanning for shell manifest generators...", file=sys.stderr)

    errors: list[str] = []

    # ── Check 1: All canonical generators exist as .py files ──
    scripts_dir = os.path.join(_PROJECT_ROOT, "core", "internal", "scripts")
    if not os.path.isdir(scripts_dir):
        pytest.fail(f"Scripts directory not found: {scripts_dir}")
        return

    py_files: set[str] = set(os.listdir(scripts_dir))
    print(f"[IMP:7][test_no_shell_generators] Python files in {scripts_dir}: {sorted(py_files)}", file=sys.stderr)

    for gen_name, gen_file in _CANONICAL_GENERATORS:
        if gen_file in py_files:
            logger.info("[IMP:9][test_no_shell_generators] FOUND: %s (%s)", gen_name, gen_file)
        else:
            errors.append(f"Canonical generator '{gen_name}' not found as '{gen_file}' in {scripts_dir}")
            logger.warning("[IMP:7][test_no_shell_generators] MISSING: %s", gen_file)

    # ── Check 2: Scan shell scripts for generator shell functions ──
    shell_dirs = [
        os.path.join(_PROJECT_ROOT, "core", "entrypoints"),
        os.path.join(_PROJECT_ROOT, "core", "internal"),
    ]

    for scan_dir in shell_dirs:
        if not os.path.isdir(scan_dir):
            logger.warning("[IMP:4][test_no_shell_generators] Directory not found: %s", scan_dir)
            continue

        for root, _dirs, files in os.walk(scan_dir):
            for filename in sorted(files):
                if not filename.endswith(".sh"):
                    continue
                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, _PROJECT_ROOT)

                with open(filepath) as f:
                    content = f.read()

                lines = content.splitlines()

                # ── Check 2a: shell function with generator name ──
                func_match = _SHELL_GENERATOR_FUNC_PATTERN.search(content)
                if func_match:
                    # Find which generator name matched
                    matched_name = func_match.group(0).split("(")[0].strip()
                    line_num = next(
                        (i + 1 for i, line in enumerate(lines) if matched_name in line and "()" in line),
                        0,
                    )
                    # This is a real violation: a shell function with generator name
                    # Check if it's also a thin facade (python3 call)
                    is_thin = _GENERATOR_CALL_PATTERN.search(content) is not None
                    if is_thin:
                        logger.info(
                            "[IMP:7][test_no_shell_generators] OK: %s defines %s() as thin facade (calls python3)",
                            rel_path,
                            matched_name,
                        )
                    else:
                        errors.append(
                            f"Shell file '{rel_path}' defines shell function '{matched_name}()' "
                            f"(line {line_num}) which matches a canonical generator name. "
                            f"Generator logic must be in Python, not shell."
                        )
                        logger.error(
                            "[IMP:10][test_no_shell_generators] VIOLATION: %s defines generator shell function '%s'",
                            rel_path,
                            matched_name,
                        )
                    continue

                # ── Check 2b: references a generator without calling the .py ──
                # If a shell script mentions "generate_secrets_manifest" but doesn't
                # call the python3 module, that's suspicious
                for gen_name, gen_file in _CANONICAL_GENERATORS:
                    if gen_name in content or gen_file.replace(".py", "") in content:
                        if _GENERATOR_CALL_PATTERN.search(content):
                            logger.info(
                                "[IMP:7][test_no_shell_generators] OK: %s references %s as thin facade",
                                rel_path,
                                gen_name,
                            )
                        else:
                            errors.append(
                                f"Shell file '{rel_path}' references '{gen_name}' "
                                f"but does NOT call it as a Python thin facade "
                                f"(expected: `python3 core/internal/scripts/{gen_file}`)."
                            )
                            logger.error(
                                "[IMP:10][test_no_shell_generators] VIOLATION: %s references '%s' without python3 call",
                                rel_path,
                                gen_name,
                            )

    # ── Report ──
    if errors:
        error_msg = "\n\n".join(errors)
        logger.error(
            "[IMP:10][test_no_shell_generators] FAILED: %d violation(s)\n%s",
            len(errors),
            error_msg,
        )
        pytest.fail(
            f"SHELL GENERATOR VIOLATION: {len(errors)} shell-based generator(s) detected.\n\n"
            f"All manifest generators must be Python-only (.py). Found shell inline generation:\n"
            f"{error_msg}\n\n"
            f"Fix: extract the generation logic into core/internal/scripts/generate_*.py "
            f"and keep the shell script as a thin facade (python3 call only)."
        )

    logger.info(
        "[IMP:9][test_no_shell_generators] ALL PASS — all %d canonical generators are Python-only, "
        "no shell-based manifest generator detected",
        len(_CANONICAL_GENERATORS),
    )


# endregion FUNC_test_no_shell_generators
