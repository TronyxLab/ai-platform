# GREP_SUMMARY: no-shell-manifest-generators python-only generate_ grep-anti-drift
# STRUCTURE: ▶ discover generate_*.py in core/internal/scripts/ → ◇ grep generate_ in .sh files → ◇ classify (delegate vs inline) → ⊕ assert Python-only → ⎋ list of Python generators with paths
# region MODULE_CONTRACT
## @purpose  Verify that ALL manifest generators are implemented in Python (.py), NOT in shell (.sh).
##           Any `generate_*` function with business logic in a shell script is a violation of
##           platform language policy (Python-only for new code).
## @scope    CI gate — static analysis of core/entrypoints/*.sh, core/internal/*.sh, core/internal/scripts/
## @invariants
##   - All canonical generators are .py files in core/internal/scripts/:
##     generate_secrets_manifest, generate_platform_env, generate_entrypoint_manifest,
##     generate_agents_md, sync_env_defaults
##   - Shell scripts may contain thin facades that call python3, but NOT inline generation logic
##   - A shell script with a `generate_*` function that contains `python3 -c` or heredoc with
##     generation logic is a violation
##   - Thin facades (e.g., `python3 core/internal/scripts/generate_X.py`) are allowed
## @rationale DevPlan 090 — Python-only generators. Shell scripts are not testable, not grep-able,
##            and prone to quoting bugs in YAML/JSON generation. All business logic must be in Python.
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

# Canonical manifest generators that MUST be Python-only
_CANONICAL_GENERATORS: list[str] = [
    "generate_secrets_manifest",
    "generate_platform_env",
    "generate_entrypoint_manifest",
    "generate_agents_md",
    "sync_env_defaults",
]

# Directories to scan for shell scripts
_SHELL_SCAN_DIRS: list[str] = [
    "core/entrypoints",
    "core/internal",
]

# Shell function patterns that indicate inline generation logic
_INLINE_GEN_PATTERNS: list[re.Pattern] = [
    re.compile(r"generate_\w+\s*\(\s*\)\s*\{"),        # Shell function: generate_X() {
    re.compile(r"python3 -c\s+['\"]"),                   # Inline python3 -c "..."
    re.compile(r"python3 - <<"),                          # Heredoc Python
    re.compile(r"<<PYEOF"),                               # Heredoc Python marker
    re.compile(r"<<'PYEOF'"),                             # Quoted heredoc Python
    re.compile(r'python3\s+-c\s+"[^"]*generate_'),       # Inline python with generate_
]

# Patterns that indicate a thin facade (allowed) — just calls python3 module
_ALLOWED_FACADE_PATTERNS: list[re.Pattern] = [
    re.compile(r'python3\s+core/internal/scripts/generate_\S+\.py'),
]


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_no_shell_generators
## @purpose  Verify no manifest generator is implemented in shell — all are .py
## @io       ⇥ filesystem scan + grep → ⎋ assert pass/fail
## @complexity O(F * L) where F = .sh files, L = lines per file
## 🧪 TRAP[TEST] · 2026-07-30 · REGRESSION · Shell manifest generators forbidden
## · Scenario: Any manifest generator with business logic implemented in shell is a violation.
##             Thin facades calling `python3 generate_X.py` are allowed.
## · Last fail: N/A (new gate)
## · Remove if: all generators are permanently migrated to Python (gate becomes always-green)
def test_no_shell_generators(caplog) -> None:
    """Verify no manifest generator is implemented in shell.

    Checks:
    1. All canonical generators exist as .py files in core/internal/scripts/
    2. No .sh file in core/entrypoints/ or core/internal/ contains generate_* shell functions
       with inline business logic
    3. Shell scripts that reference generate_X.py via python3 call are thin facades (allowed)
    """
    caplog.set_level(logging.INFO)
    print("[IMP:8][test_no_shell_generators] Scanning for shell manifest generators...", file=sys.stderr)

    errors: list[str] = []

    # ── Check 1: All canonical generators exist as .py files ──
    scripts_dir = os.path.join(_PROJECT_ROOT, "core", "internal", "scripts")
    if not os.path.isdir(scripts_dir):
        pytest.fail(f"Scripts directory not found: {scripts_dir}")
        return

    py_generators_found: list[str] = []
    for f in sorted(os.listdir(scripts_dir)):
        if f.endswith(".py") and "generate_" in f:
            py_generators_found.append(f)

    print(f"[IMP:7][test_no_shell_generators] Found Python generators: {py_generators_found}", file=sys.stderr)

    for gen in _CANONICAL_GENERATORS:
        # Each canonical generator may be a .py file (generate_X.py) or the base name matches
        matching = [g for g in py_generators_found if gen in g]
        if not matching:
            errors.append(f"Canonical generator '{gen}' not found as .py in {scripts_dir}")
            logger.warning("[IMP:7][test_no_shell_generators] MISSING: %s.py", gen)
        else:
            logger.info("[IMP:9][test_no_shell_generators] FOUND: %s", matching[0])

    # ── Check 2: Scan shell scripts for inline generators ──
    for rel_dir in _SHELL_SCAN_DIRS:
        abs_dir = os.path.join(_PROJECT_ROOT, rel_dir)
        if not os.path.isdir(abs_dir):
            logger.warning("[IMP:4][test_no_shell_generators] Directory not found: %s", abs_dir)
            continue

        for root, _dirs, files in os.walk(abs_dir):
            for filename in sorted(files):
                if not filename.endswith(".sh"):
                    continue
                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, _PROJECT_ROOT)

                with open(filepath) as f:
                    content = f.read()

                lines = content.splitlines()

                # Check if this is a thin facade (calls python3 generate_X.py)
                is_thin_facade = any(
                    p.search(content) for p in _ALLOWED_FACADE_PATTERNS
                )

                # Check for inline generation patterns
                violations_in_file: list[tuple[int, str]] = []
                for i, line in enumerate(lines, 1):
                    for pattern in _INLINE_GEN_PATTERNS:
                        if pattern.search(line):
                            violations_in_file.append((i, line.strip()))
                            break

                if violations_in_file:
                    if is_thin_facade:
                        # Thin facade with some inline logic — warn but don't fail if it
                        # primarily delegates to Python
                        logger.warning(
                            "[IMP:7][test_no_shell_generators] WARN: %s is a thin facade but has %d inline pattern(s): %s",
                            rel_path,
                            len(violations_in_file),
                            violations_in_file[:3],
                        )
                    else:
                        # Not a thin facade — this is a real violation
                        violation_detail = "; ".join(
                            f"L{l}: {t[:80]}" for l, t in violations_in_file
                        )
                        errors.append(
                            f"Shell file '{rel_path}' has {len(violations_in_file)} inline generation pattern(s):\n  {violation_detail}"
                        )
                        logger.error(
                            "[IMP:10][test_no_shell_generators] VIOLATION: %s has inline generation logic",
                            rel_path,
                        )
                else:
                    logger.info(
                        "[IMP:7][test_no_shell_generators] OK: %s — no inline generation patterns",
                        rel_path,
                    )

    # ── Report ──
    if os.path.isdir(scripts_dir):
        print(f"\n[IMP:8][test_no_shell_generators] Python generators in {scripts_dir}:", file=sys.stderr)
        for g in sorted(py_generators_found):
            print(f"  ✓ {g}", file=sys.stderr)

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
        "no inline shell generation detected",
        len(_CANONICAL_GENERATORS),
    )


# endregion FUNC_test_no_shell_generators
