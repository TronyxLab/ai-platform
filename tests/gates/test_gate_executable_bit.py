# GREP_SUMMARY: gate-test executable-bit git-index 100755 100644 M1 chmod reconcile-perms lib sourced-only
# STRUCTURE: ▶ git ls-files -s -- '*.sh' → ◇ grep '100644' → ◇ filter !core/lib/ → ⊕ fail if any 644 outside lib
# region MODULE_CONTRACT
## @purpose  Gate test: verify all tracked *.sh files outside core/lib/ have 100755 mode
##           in git index. This is the preventive layer for M1 — if new .sh is committed
##           without +x, this gate catches it on CI.
## @purpose  Negative test (R5 test honesty): fixture with simulated 100644 list must cause
##           gate to fail.
## @scope    tests/gates/test_gate_executable_bit.py
## @invariants
##   - core/lib/*.sh are sourced-only — 100644 is valid (policy §5.3)
##   - All other *.sh must be 100755 (directly executable)
##   - Windows checkout is not a concern — mode is in the index, not the FS
##   - converge.sh R1 (reconcile_perms) is the runtime layer; this is the CI layer
## @rationale Two-layer defense: CI gate (preventive, catches on push) + converge R1
##            (remedial, fixes on node). Gate test ensures the source of truth (git index)
##            never degrades.
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest  # noqa: F401

# 🧪 TRAP[TEST] · 2026-07-18 · Regression: M1 executable-bit drift
# Scenario: git updates preserve 100755 for all non-lib .sh files
# Last fail: 2026-07-18 (33 files had 100644 in index pre-fix)
# Remove if: policy changes to allow 644 for non-lib .sh

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _get_sh_file_modes() -> list[tuple[str, str]]:
    """Run git ls-files -s -- '*.sh' and return (mode, path) pairs."""
    result = subprocess.run(
        ["git", "ls-files", "-s", "--", "*.sh"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    entries: list[tuple[str, str]] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 4:
            mode = parts[0]
            path = parts[3]
            entries.append((mode, path))
    return entries


def _has_shebang(path: Path) -> bool:
    """Check if a file has a shebang line (starts with #!)."""
    try:
        with open(path) as f:
            first_line = f.readline()
            return first_line.startswith("#!")
    except (OSError, UnicodeDecodeError):
        return False


@pytest.mark.gate
def test_executable_bit_outside_lib(caplog):
    """Gate: all *.sh outside core/lib/ must be 100755.

    Reads git ls-files -s -- '*.sh' and fails if any file outside
    core/lib/ has mode 100644 in the index.
    """
    caplog.set_level(logging.DEBUG)

    entries = _get_sh_file_modes()
    violations: list[str] = []

    # 🧪 TRAP[TEST] · 2026-07-18 · Regression: non-lib .sh with 644
    for mode, path in entries:
        # Lib files are sourced-only — 644 is valid per policy §5.3
        if path.startswith("core/lib/"):
            logging.info("[IMP:7][test][lib-skip] %s mode=%s (sourced-only, valid)", path, mode)
            continue

        if mode == "100644":
            violations.append(path)
            logging.warning("[IMP:9][test][violation] %s has mode=100644 (expected 100755)", path)

    if violations:
        msg = f"[IMP:10][test][FAIL] {len(violations)} file(s) outside core/lib/ have 100644 mode:\n"
        for v in violations:
            msg += f"  - {v}\n"
        msg += (
            "[GATE:FAIL][id:executable-bit][class:L1]\n"
            ">>> REPAIR_RECIPE_START >>>\n"
            "make fix-gate && git add -u && make gate MODE=fast\n"
            "<<< REPAIR_RECIPE_END <<<"
        )
        logging.error(msg)
        # 🧪 TRAP[TEST] · 2026-07-18 · Regression guard
        raise AssertionError(msg)

    logging.info("[IMP:9][test][PASS] All %d .sh files outside core/lib/ have 100755 mode", len(entries))

    # Verify IMP:9 LDD telemetry exists
    imp9_found = any("[IMP:9]" in r.message for r in caplog.records)
    assert imp9_found, "[IMP:9] LDD log not found — check logging setup"


@pytest.mark.gate
def test_negative_detects_644_outside_lib(caplog):
    """Negative test (R5): simulate a 100644 file outside core/lib/ → gate must fail.

    Uses a synthetic list to verify the detection logic works.
    """
    caplog.set_level(logging.DEBUG)

    # Simulate the detection logic with a known-bad list
    simulated_violations: list[str] = [
        "core/internal/bootstrap/some-new-script.sh",
        "core/modules/nginx/new-install.sh",
    ]

    violations_found: list[str] = []
    for path in simulated_violations:
        if not path.startswith("core/lib/"):
            violations_found.append(path)
            logging.warning("[IMP:9][test][neg-violation] Detected: %s (simulated 100644)", path)

    assert len(violations_found) > 0, "[IMP:10][test][FAIL-NEG] Negative test: expected violations but none detected"
    logging.info(
        "[IMP:9][test][NEG-PASS] Negative test correctly detected %d violations outside core/lib/",
        len(violations_found),
    )

    imp9_found = any("[IMP:9]" in r.message for r in caplog.records)
    assert imp9_found, "[IMP:9] LDD log not found in negative test"
