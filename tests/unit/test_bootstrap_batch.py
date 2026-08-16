"""
# GREP_SUMMARY: test-bootstrap-batch, bootstrap.sh, bootstrap_resolver, --get-many, LOC-100, ci_deploy_key, PLATFORM_CI_DEPLOY_KEY, env-override, node.yaml-SoT, D2, U-52, U-53, W9-F1, code-presence
# STRUCTURE: ┌read bootstrap.sh + bootstrap_resolver.py┐ → ◇ 5 scenarios ∋ (1×resolver resolve / 0×--get / spec в resolver / LOC≤100 / no env-override branch) → ⎋ assert code-presence + D2 negative
# region MODULE_CONTRACT
## @purpose  Code-presence tests for bootstrap.sh batch refactoring (DevPlan 116 B3 T5/T6, U-52/U-53;
##           DevPlan 170 W9-F1): exactly ONE bootstrap_resolver resolve call in bootstrap.sh,
##           ZERO standalone node_yaml --get calls, file ≤ 100 LOC (W9-F1 target), the
##           ci_deploy_key:node.ci_deploy_key batch spec lives in bootstrap_resolver.py,
##           and the PLATFORM_CI_DEPLOY_KEY env-override branch absent (D2 — node.yaml single SoT).
## @scope    Static text analysis of core/entrypoints/bootstrap.sh + core/internal/bootstrap/
##           bootstrap_resolver.py — no execution, no subprocess.
## @invariants
##   - main() recipe contains exactly 1 `bootstrap_resolver resolve` invocation
##   - No standalone `node_yaml ... --get ` (per-field) invocation remains in bootstrap.sh
##   - bootstrap.sh ≤ 100 LOC (total lines incl. comments — the 170 W9-F1 target)
##   - Batch spec `ci_deploy_key:node.ci_deploy_key` present in bootstrap_resolver.py (moved)
##   - Env-override branch `CI_DEPLOY_KEY="$PLATFORM_CI_DEPLOY_KEY"` is ABSENT (D2, R5 negative)
## @rationale  U-52: 6 per-field --get → ONE --get-many batch. U-53 (D2): node.yaml единственный
##             источник ci_deploy_key — env-override удалён (DevPlan 116 B3 T6). 170 W9-F1:
##             tab-парсинг вынесен в bootstrap_resolver.py — bootstrap.sh остаётся <100 LOC.
## @changes  2026-08-01 · Created (DevPlan 116 B3 T5/T6)
## @changes  2026-08-14 · DevPlan 166 D3 — test_trap_bug_resolved_marker удалён (отказ от B8-удержания)
## @changes  2026-08-15 · DevPlan 170 W9-F1 — batch-механизм переехал в bootstrap_resolver.py;
##            тесты переведены на новый код-присутствие; LOC-таргет 150 → 100
# endregion MODULE_CONTRACT
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP_SH = _PROJECT_ROOT / "core" / "entrypoints" / "bootstrap.sh"
BOOTSTRAP_RESOLVER_PY = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "bootstrap_resolver.py"


def _read(path: Path) -> str:
    """Read file content (empty string if missing — fail-fast via assert in tests)."""
    try:
        return path.read_text(encoding="utf-8")
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


class TestBootstrapBatch:
    """Code-presence: bootstrap.sh делегирует batch-экстракцию bootstrap_resolver.py (170 W9-F1)."""

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · single bootstrap_resolver resolve call (DevPlan 170 W9-F1)
    # · Scenario: bootstrap.sh содержит РОВНО один вызов `bootstrap_resolver resolve` —
    # ·   замена прежнего node_yaml --get-many (DevPlan 116 B3 T5)
    # · Last fail: N/A (new test — механизм перенесён из bootstrap.sh в resolver)
    # · Remove if: node.yaml extraction mechanism changes
    def test_single_resolver_resolve_call(self):
        """main() recipe contains exactly ONE bootstrap_resolver resolve invocation."""
        content = _read(BOOTSTRAP_SH)
        assert content, f"bootstrap.sh not found: {BOOTSTRAP_SH}"

        resolve_count = sum(
            1
            for line in content.splitlines()
            if not line.strip().startswith("#")
            and "python3 -m core.internal.bootstrap.bootstrap_resolver resolve" in line
        )
        assert resolve_count == 1, (
            f"Expected exactly 1 bootstrap_resolver resolve invocation, found {resolve_count} (170 W9-F1)"
        )

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · batch spec moved to resolver (DevPlan 170 W9-F1)
    # · Scenario: спецификация --get-many (ci_deploy_key:node.ci_deploy_key) живёт в
    # ·   bootstrap_resolver.py, а НЕ в bootstrap.sh — single source парсинга в Python
    # · Last fail: N/A (new test — спека перенесена 170 W9-F1)
    # · Remove if: spec location changes
    def test_batch_spec_in_resolver(self):
        """--get-many batch spec (ci_deploy_key:node.ci_deploy_key) присутствует в bootstrap_resolver.py."""
        resolver_content = _read(BOOTSTRAP_RESOLVER_PY)
        assert resolver_content, f"bootstrap_resolver.py not found: {BOOTSTRAP_RESOLVER_PY}"

        assert "ci_deploy_key:node.ci_deploy_key" in resolver_content, (
            "ci_deploy_key spec должен жить в bootstrap_resolver.py (D2, U-53, 170 W9-F1)"
        )

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · no standalone --get (DevPlan 116 B3 T5, U-52)
    # · Last fail: 6 standalone `--get` invocations in main()
    # · Remove if: node.yaml extraction mechanism changes
    def test_no_standalone_get_calls(self):
        """No per-field `node_yaml ... --get ` invocation remains in main() recipe."""
        content = _read(BOOTSTRAP_SH)
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

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · LOC ≤ 100 (DevPlan 170 W9-F1)
    # · Last fail: 155 LOC before extraction (169 audit); target tightened 150 → 100
    # · Remove if: bootstrap.sh contract is extended beyond thin-facade scope
    def test_loc_under_100(self):
        """bootstrap.sh ≤ 100 LOC (physical lines, blanks excluded — 170 W9-F1 target)."""
        content = _read(BOOTSTRAP_SH)
        assert content, f"bootstrap.sh not found: {BOOTSTRAP_SH}"

        loc = _count_non_comment_lines(content)
        assert loc <= 100, (
            f"bootstrap.sh is {loc} LOC — target ≤ 100 (DevPlan 170 W9-F1). "
            "Consolidate further or move logic to Python."
        )


class TestBootstrapCiDeployKeySoT:
    """Negative (R5): ci_deploy_key env-override branch absent — node.yaml single SoT (D2)."""

    # 🧪 TRAP[TEST] · 2026-08-01 · NEGATIVE (R5) · env-override branch absent (DevPlan 116 B3 T6, D2, U-53)
    # · Last fail: bootstrap.sh:105-109 had `if [[ -n "${PLATFORM_CI_DEPLOY_KEY:-}" ]]; then
    # ·   CI_DEPLOY_KEY="$PLATFORM_CI_DEPLOY_KEY"` — env override > node.yaml
    # · Remove if: delivery channel for ci_deploy_key changes
    def test_env_override_branch_absent(self):
        """No `CI_DEPLOY_KEY="$PLATFORM_CI_DEPLOY_KEY"` priority branch (D2)."""
        content = _read(BOOTSTRAP_SH)
        assert content, f"bootstrap.sh not found: {BOOTSTRAP_SH}"

        # The assignment line that implemented env-priority — must be absent
        assert 'CI_DEPLOY_KEY="$PLATFORM_CI_DEPLOY_KEY"' not in content, (
            "[GATE:FAIL][id:bootstrap_env_override_branch] Env-override branch found in bootstrap.sh — "
            "ci_deploy_key must come ONLY from node.yaml (D2, DevPlan 116 B3 T6, U-53)"
        )

    # 🧪 TRAP[TEST] · 2026-08-15 · REGRESSION · node.yaml SoT extraction (DevPlan 116 B3 T6, D2)
    # · Scenario: batch spec с ci_deploy_key живёт в resolver (python3 -m bootstrap_resolver), а НЕ
    # ·   в env-override — вывод resolver (exit 0) гарантирует node.yaml-источник
    # · Last fail: N/A (new test — W9-F1 перенос)
    # · Remove if: delivery channel for ci_deploy_key changes
    def test_ci_deploy_key_delegates_to_resolver(self):
        """bootstrap.sh извлекает ci_deploy_key через bootstrap_resolver (не env-override)."""
        content = _read(BOOTSTRAP_SH)
        assert content, f"bootstrap.sh not found: {BOOTSTRAP_SH}"

        assert "bootstrap_resolver" in content, (
            "ci_deploy_key должен извлекаться через bootstrap_resolver (node.yaml SoT, D2, U-53, 170 W9-F1)"
        )
