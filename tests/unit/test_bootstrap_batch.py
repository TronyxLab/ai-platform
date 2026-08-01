#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-bootstrap-batch, bootstrap.sh, --get-many, LOC-150, ci_deploy_key, PLATFORM_CI_DEPLOY_KEY, env-override, node.yaml-SoT, D2, U-52, U-53, code-presence
# STRUCTURE: ┌read core/entrypoints/bootstrap.sh┐ → ◇ 4 scenarios ∋ (1×--get-many / 0×--get / LOC≤150 / no env-override branch) → ⎋ assert code-presence + D2 negative
# region MODULE_CONTRACT
## @purpose  Code-presence tests for bootstrap.sh batch refactoring (DevPlan 116 B3 T5/T6, U-52/U-53):
##           exactly ONE node_yaml --get-many call, ZERO standalone --get calls, file ≤ 150 LOC,
##           and the PLATFORM_CI_DEPLOY_KEY env-override branch absent (D2 — node.yaml single SoT).
## @scope    Static text analysis of core/entrypoints/bootstrap.sh — no execution, no subprocess.
## @invariants
##   - main() recipe contains exactly 1 `--get-many` invocation
##   - No standalone `node_yaml ... --get ` (per-field) invocation remains
##   - bootstrap.sh ≤ 150 LOC (total lines incl. comments — the T5 target)
##   - Env-override branch `CI_DEPLOY_KEY="$PLATFORM_CI_DEPLOY_KEY"` is ABSENT (D2, R5 negative)
## @rationale  U-52: 6 per-field --get → ONE --get-many batch. U-53 (D2): node.yaml единственный
##             источник ci_deploy_key — env-override удалён; TRAP[BUG] 2026-07-17 → RESOLVED.
## @changes  2026-08-01 · Created (DevPlan 116 B3 T5/T6)
# endregion MODULE_CONTRACT
"""

import logging
import re
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP_SH = _PROJECT_ROOT / "core" / "entrypoints" / "bootstrap.sh"


def _bootstrap_content() -> str:
    """Read bootstrap.sh content (empty string if missing — fail-fast via assert in tests)."""
    try:
        return BOOTSTRAP_SH.read_text()
    except FileNotFoundError:
        return ""


def _count_non_comment_lines(content: str) -> int:
    """Count total lines excluding pure-whitespace lines (LOC metric).

    ## @purpose — LOC metric for bootstrap.sh: total physical lines (comments included —
    ##            they are part of the file's contract surface), blank lines excluded.
    ## @io — ⇥ content → ⎋ int
    ## @complexity — O(N)
    """
    return len([ln for ln in content.splitlines() if ln.strip()])


@pytest.mark.unit
class TestBootstrapBatch:
    """Code-presence: bootstrap.sh batch refactoring (DevPlan 116 B3 T5, U-52)."""

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · single --get-many call (DevPlan 116 B3 T5, U-52)
    # · Last fail: 6 per-field `node_yaml --file ... --get` calls (owner_key, ci_deploy_key,
    # ·   domain, context, contexts.0.name + detect-age-key)
    # · Remove if: node.yaml extraction mechanism changes
    def test_single_get_many_call(self):
        """main() recipe contains exactly ONE node_yaml --get-many invocation."""
        content = _bootstrap_content()
        assert content, f"bootstrap.sh not found: {BOOTSTRAP_SH}"

        # Count only lines that actually INVOKE the node_yaml CLI with --get-many
        # (the echo message line mentions --get-many but does not call it)
        get_many_count = sum(
            1
            for line in content.splitlines()
            if not line.strip().startswith("#") and "node_yaml" in line and "--get-many" in line
        )
        assert get_many_count == 1, (
            f"Expected exactly 1 node_yaml --get-many invocation, found {get_many_count} (DevPlan 116 B3 T5)"
        )

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · no standalone --get (DevPlan 116 B3 T5, U-52)
    # · Last fail: 6 standalone `--get` invocations in main()
    # · Remove if: node.yaml extraction mechanism changes
    def test_no_standalone_get_calls(self):
        """No per-field `node_yaml ... --get ` invocation remains in main() recipe."""
        content = _bootstrap_content()
        assert content, f"bootstrap.sh not found: {BOOTSTRAP_SH}"

        # Standalone --get in an executable line (not --get-many, not inside a comment)
        standalone_gets: list[str] = []
        for line_no, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "--get-many" in stripped:
                continue
            if re.search(r"--get(?:\s|$)", stripped):
                standalone_gets.append(f"{line_no}: {stripped}")
        assert not standalone_gets, "Standalone --get call(s) remain in bootstrap.sh:\n" + "\n".join(standalone_gets)

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · LOC ≤ 150 (DevPlan 116 B3 T5, U-52)
    # · Last fail: 178 LOC before batch refactoring
    # · Remove if: bootstrap.sh contract is extended beyond thin-facade scope
    def test_loc_under_150(self):
        """bootstrap.sh ≤ 150 LOC (physical lines, blanks excluded)."""
        content = _bootstrap_content()
        assert content, f"bootstrap.sh not found: {BOOTSTRAP_SH}"

        loc = _count_non_comment_lines(content)
        assert loc <= 150, (
            f"bootstrap.sh is {loc} LOC — target ≤ 150 (DevPlan 116 B3 T5, U-52). "
            "Consolidate further or move logic to Python."
        )


@pytest.mark.unit
class TestBootstrapCiDeployKeySoT:
    """Negative (R5): ci_deploy_key env-override branch absent — node.yaml single SoT (D2)."""

    # 🧪 TRAP[TEST] · 2026-08-01 · NEGATIVE (R5) · env-override branch absent (DevPlan 116 B3 T6, D2, U-53)
    # · Last fail: bootstrap.sh:105-109 had `if [[ -n "${PLATFORM_CI_DEPLOY_KEY:-}" ]]; then
    # ·   CI_DEPLOY_KEY="$PLATFORM_CI_DEPLOY_KEY"` — env override > node.yaml
    # · Remove if: delivery channel for ci_deploy_key changes
    def test_env_override_branch_absent(self):
        """No `CI_DEPLOY_KEY="$PLATFORM_CI_DEPLOY_KEY"` priority branch (D2)."""
        content = _bootstrap_content()
        assert content, f"bootstrap.sh not found: {BOOTSTRAP_SH}"

        # The assignment line that implemented env-priority — must be absent
        assert 'CI_DEPLOY_KEY="$PLATFORM_CI_DEPLOY_KEY"' not in content, (
            "[GATE:FAIL][id:bootstrap_env_override_branch] Env-override branch found in bootstrap.sh — "
            "ci_deploy_key must come ONLY from node.yaml (D2, DevPlan 116 B3 T6, U-53)"
        )

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · node.yaml SoT extraction (DevPlan 116 B3 T6, D2)
    # · Last fail: N/A (new test)
    # · Remove if: delivery channel for ci_deploy_key changes
    def test_ci_deploy_key_in_batch_spec(self):
        """The --get-many spec includes ci_deploy_key:node.ci_deploy_key (node.yaml SoT)."""
        content = _bootstrap_content()
        assert content, f"bootstrap.sh not found: {BOOTSTRAP_SH}"

        assert "ci_deploy_key:node.ci_deploy_key" in content, (
            "ci_deploy_key must be extracted from node.yaml via the --get-many batch spec (D2, U-53)"
        )

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · TRAP[BUG] RESOLVED marker (DevPlan 116 B3 T6)
    # · Last fail: N/A (new test — historical TRAP kept, marked RESOLVED)
    # · Remove if: TRAP lifecycle rules change (B8 allows history inside TRAP)
    def test_trap_bug_resolved_marker(self):
        """The 2026-07-17 ci_deploy_key TRAP[BUG] carries a RESOLVED marker."""
        content = _bootstrap_content()
        assert content, f"bootstrap.sh not found: {BOOTSTRAP_SH}"

        assert "TRAP[BUG]" in content, "Historical TRAP[BUG] must remain (B8 allows history)"
        assert "RESOLVED 2026-08-01" in content, "TRAP[BUG] must be marked RESOLVED 2026-08-01 (B3 T6)"
