#!/usr/bin/env python3

# GREP_SUMMARY: gate debt-registry strangler-closeout TRAP-inventory shell-residual gitignore check-file-lines
# STRUCTURE: ▶ read .ai/debt/001-Strangler-Fig-Closeout.md → ◇ exists? → ∋ extract sections → ◇ 8 SHELL-RESIDUAL entries?
#            → ◇ AD5/AD7 rev-dates? → ◇ LOC>200? → ◇ node-resolver in P2? → ◇ check-file-lines scope → ◇ .gitignore covers .ai/debt/ → ⎋ pass/fail

# region MODULE_CONTRACT
## @purpose  Gate: verify the debt registry `.ai/debt/001-Strangler-Fig-Closeout.md`
##           exists, contains all mandatory sections (SHELL-RESIDUAL, P2-BACKLOG,
##           P3-BACKLOG, TEST-DEBT, ARCH-DECISIONS, TRAP-INVENTORY), each SHELL-RESIDUAL
##           entry has file/LOC/обоснование/rev-дата with LOC>200, ARCH-DECISIONS
##           contains AD5 (2026-10-21) and AD7 (2026-10-22), node-resolver.sh is in
##           P2-BACKLOG, `check-file-lines` does not scan `.ai/`, and `.gitignore`
##           covers `.ai/debt/`. Per DevPlan 111 $TEST_SPEC (AC1-AC7).
## @scope  Static file scanning — no runtime dependencies, no Docker.
##         Checks `.ai/debt/`, `.gitignore`, `core/entrypoints/check-file-lines.sh`.
## @invariants
##   - All test functions use @pytest.mark.gate and @ldd_trajectory
##   - Registry path resolved from test file location (../../.ai/debt/001-Strangler-Fig-Closeout.md)
##   - check-file-lines verified statically (find root = ${PATHS_CORE_DIR}) AND
##     at runtime (bash script exit 0, no `.ai/` in output) — subprocess exempt
##     (shell-entrypoint verification, not business logic)
##   - .gitignore verified for a pattern covering `.ai/debt/`
## @rationale  The registry is the single source of truth for architectural debt
##             post-Strangler-Fig (DevPlan 111). This gate prevents drift: a missing
##             registry, missing sections, or missing rev-dates silently erases debt
##             context between waves. AC1-AC7 enforced as CI gate.
## @changes  CREATED: 2026-07-31 | DevPlan 111 Wave 1 | TASK-A gate tests
def _module_contract():
    pass


# endregion MODULE_CONTRACT


import logging
import pathlib
import re
import subprocess

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_DEBT_REGISTRY = _REPO_ROOT / ".ai" / "debt" / "001-Strangler-Fig-Closeout.md"
_CHECK_FILE_LINES = _REPO_ROOT / "core" / "entrypoints" / "check-file-lines.sh"
_GITIGNORE = _REPO_ROOT / ".gitignore"

# ── Mandatory sections (DevPlan 111 $TEST_SPEC AC2) ───────────────────────────

_REQUIRED_SECTIONS: tuple[str, ...] = (
    "§SHELL-RESIDUAL",
    "§P2-BACKLOG",
    "§P3-BACKLOG",
    "§TEST-DEBT",
    "§ARCH-DECISIONS",
    "§TRAP-INVENTORY",
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _read_registry() -> str:
    """Read the debt registry as text.

    ## @purpose — Load registry content; fail fast with clear message if absent.
    ## @io — ⇥ none → ⎋ str (registry content)
    ## @complexity — O(1)
    """
    if not _DEBT_REGISTRY.exists():
        pytest.fail(f"DEBT_REGISTRY_MISSING: {_DEBT_REGISTRY}")
    return _DEBT_REGISTRY.read_text(encoding="utf-8")


def _extract_section(content: str, header: str, next_header: str | None = None) -> str:
    """Extract a section body between two `## §` headers.

    ## @purpose — Slice registry content by section header markers.
    ## @io — ⇥ content: str, header: str, next_header: str|None → ⎋ str (section body)
    ## @complexity — O(N) over section markers
    """
    start = content.find(header)
    if start == -1:
        return ""
    end = content.find(next_header, start + len(header)) if next_header else len(content)
    if end == -1:
        end = len(content)
    return content[start:end]


# ── Tests ─────────────────────────────────────────────────────────────────────


# region FUNC_test_debt_registry_exists
## @purpose — AC1: registry file exists and is non-empty.
## @io — ⇥ caplog → ⎋ None (pytest.fail if missing/empty)
## @complexity — O(1)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · Debt registry vanished or empty
# · Scenario: AC1 — .ai/debt/001-Strangler-Fig-Closeout.md exists and non-empty
# · Last fail: N/A (preventive)
# · Remove if: debt registry superseded by another mechanism
def test_debt_registry_exists(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ▶ _DEBT_REGISTRY → ◇ exists()? → ◇ len(text)>0 → ⎋ pass | fail:DEBT_REGISTRY_MISSING/EMPTY
    """
    # region BLOCK_Exists
    content = _read_registry()
    logger.info("[IMP:7][test_debt_registry_exists] Registry found: %s", _DEBT_REGISTRY)
    # endregion

    # region BLOCK_NonEmpty
    assert len(content.strip()) > 0, "DEBT_REGISTRY_EMPTY"
    logger.info("[IMP:9][test_debt_registry_exists] ✅ Registry non-empty (%d chars)", len(content))
    # endregion


# endregion FUNC_test_debt_registry_exists


# region FUNC_test_debt_registry_all_sections
## @purpose — AC2: all mandatory sections present.
## @io — ⇥ caplog → ⎋ None (pytest.fail listing missing sections)
## @complexity — O(S) where S = number of required sections


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · Mandatory section removed from registry
# · Scenario: AC2 — SHELL-RESIDUAL, P2-BACKLOG, P3-BACKLOG, TEST-DEBT, ARCH-DECISIONS, TRAP-INVENTORY
# · Last fail: N/A (preventive)
# · Remove if: section list changes by approved DevPlan amendment
def test_debt_registry_all_sections(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ▶ content → ○ for section in _REQUIRED_SECTIONS → ◇ "## "+section in content? → ⊕ missing → ⎋ pass | fail:MISSING_SECTION
    """
    # region BLOCK_Collect
    content = _read_registry()
    missing = [s for s in _REQUIRED_SECTIONS if f"## {s}" not in content]
    logger.info("[IMP:7][test_debt_registry_all_sections] Checking %d required sections", len(_REQUIRED_SECTIONS))
    # endregion

    # region BLOCK_Assert
    if missing:
        logger.error("[IMP:9][test_debt_registry_all_sections] Missing sections: %s", missing)
        pytest.fail(f"MISSING_SECTION: {missing}")
    logger.info("[IMP:9][test_debt_registry_all_sections] ✅ All %d required sections present", len(_REQUIRED_SECTIONS))
    # endregion


# endregion FUNC_test_debt_registry_all_sections


# region FUNC_test_debt_registry_shell_residual_entries
## @purpose — AC3: SHELL-RESIDUAL has exactly 8 entries, each with 5 columns
##            (file, LOC, обоснование, rev-дата).
## @io — ⇥ caplog → ⎋ None (pytest.fail on count/column mismatch)
## @complexity — O(R) where R = SHELL-RESIDUAL rows


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · SHELL-RESIDUAL entries count/columns drift
# · Scenario: AC3 — 8 rows with file/LOC/обоснование/rev-дата columns
# · Last fail: N/A (preventive)
# · Remove if: SHELL-RESIDUAL structure changes by approved amendment
def test_debt_registry_shell_residual_entries(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ▶ section SHELL-RESIDUAL → ○ split rows → ◇ 8 rows? → ◇ each 5 cols? → ⎋ pass | fail
    """
    # region BLOCK_Extract
    content = _read_registry()
    section = _extract_section(content, "## §SHELL-RESIDUAL", "## §P2-BACKLOG")
    rows = [
        line.strip()
        for line in section.splitlines()
        if line.strip().startswith("| S") and line.strip().endswith("|")
    ]
    logger.info("[IMP:7][test_debt_registry_shell_residual_entries] Found %d SHELL-RESIDUAL row(s)", len(rows))
    # endregion

    # region BLOCK_Assert
    assert len(rows) == 8, f"SHELL_RESIDUAL_ENTRY_COUNT: expected 8, got {len(rows)}"
    for row in rows:
        cols = [c.strip() for c in row.strip("|").split("|")]
        assert len(cols) == 5, f"SHELL_RESIDUAL_COLUMNS: expected 5 columns, got {len(cols)}: {row}"
        assert cols[1].startswith("`") and cols[1].endswith("`"), f"SHELL_RESIDUAL_FILE_COL: {row}"
        assert cols[2].isdigit(), f"SHELL_RESIDUAL_LOC_NOT_NUMERIC: {row}"
        assert cols[3], f"SHELL_RESIDUAL_RATIONALE_EMPTY: {row}"
        assert cols[4], f"SHELL_RESIDUAL_REVDATE_EMPTY: {row}"
    logger.info("[IMP:9][test_debt_registry_shell_residual_entries] ✅ 8 entries × 5 columns (file/LOC/rationale/rev-date)")
    # endregion


# endregion FUNC_test_debt_registry_shell_residual_entries


# region FUNC_test_debt_registry_arch_decisions_rev_dates
## @purpose — AC4: ARCH-DECISIONS contains AD5 (rev 2026-10-21) and AD7 (rev 2026-10-22).
## @io — ⇥ caplog → ⎋ None (pytest.fail on missing AD5/AD7 rev-dates)
## @complexity — O(A) where A = ARCH-DECISIONS rows


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · AD5/AD7 rev-dates dropped from ARCH-DECISIONS
# · Scenario: AC4 — AD5 2026-10-21 (language policy), AD7 2026-10-22 (Python-First re-eval)
# · Last fail: N/A (preventive)
# · Remove if: decisions closed/reviewed and entries removed by approved amendment
def test_debt_registry_arch_decisions_rev_dates(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ▶ section ARCH-DECISIONS → ◇ "AD5" row has 2026-10-21? → ◇ "AD7" row has 2026-10-22? → ⎋ pass | fail
    """
    # region BLOCK_Extract
    content = _read_registry()
    section = _extract_section(content, "## §ARCH-DECISIONS", "## §TRAP-INVENTORY")
    ad5_row = next((ln for ln in section.splitlines() if ln.strip().startswith("| AD5 |")), "")
    ad7_row = next((ln for ln in section.splitlines() if ln.strip().startswith("| AD7 |")), "")
    logger.info("[IMP:7][test_debt_registry_arch_decisions_rev_dates] AD5/AD7 rows located")
    # endregion

    # region BLOCK_Assert
    assert "2026-10-21" in ad5_row, f"AD5_REV_DATE_MISSING: expected 2026-10-21 in: {ad5_row}"
    assert "2026-10-22" in ad7_row, f"AD7_REV_DATE_MISSING: expected 2026-10-22 in: {ad7_row}"
    logger.info("[IMP:9][test_debt_registry_arch_decisions_rev_dates] ✅ AD5 rev=2026-10-21, AD7 rev=2026-10-22")
    # endregion


# endregion FUNC_test_debt_registry_arch_decisions_rev_dates


# region FUNC_test_debt_registry_no_trivial_entries
## @purpose — AC3: all SHELL-RESIDUAL entries have LOC > 200 (no trivial entries).
## @io — ⇥ caplog → ⎋ None (pytest.fail listing entries with LOC ≤ 200)
## @complexity — O(R)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · A SHELL-RESIDUAL entry below the 200-LOC threshold
# · Scenario: AC3 — SHELL-RESIDUAL documents scripts >200 LOC excluded from migration
# · Last fail: N/A (preventive)
# · Remove if: threshold revised by language-policy amendment
def test_debt_registry_no_trivial_entries(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ▶ section SHELL-RESIDUAL → ○ parse LOC per row → ◇ all LOC>200? → ⎋ pass | fail:TRIVIAL_ENTRY
    """
    # region BLOCK_Extract
    content = _read_registry()
    section = _extract_section(content, "## §SHELL-RESIDUAL", "## §P2-BACKLOG")
    rows = [
        line.strip()
        for line in section.splitlines()
        if line.strip().startswith("| S") and line.strip().endswith("|")
    ]
    violations: list[str] = []
    # endregion

    # region BLOCK_Assert
    for row in rows:
        cols = [c.strip() for c in row.strip("|").split("|")]
        if len(cols) == 5 and cols[2].isdigit() and int(cols[2]) <= 200:
            violations.append(row)
    if violations:
        logger.error("[IMP:9][test_debt_registry_no_trivial_entries] Trivial entries (LOC<=200): %s", violations)
        pytest.fail(f"TRIVIAL_ENTRY: {violations}")
    logger.info("[IMP:9][test_debt_registry_no_trivial_entries] ✅ All %d entries have LOC > 200", len(rows))
    # endregion


# endregion FUNC_test_debt_registry_no_trivial_entries


# region FUNC_test_debt_registry_node_resolver_in_p2
## @purpose — PM3: node-resolver.sh (not in Brief) is registered in P2-BACKLOG.
## @io — ⇥ caplog → ⎋ None (pytest.fail if absent from P2 section)
## @complexity — O(P)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · node-resolver.sh dropped from P2-BACKLOG
# · Scenario: PM3 — 271-LOC facade candidate (DevPlan 111 D1) must stay tracked
# · Last fail: N/A (preventive)
# · Remove if: node-resolver.sh fully migrated (P2-1 closed)
def test_debt_registry_node_resolver_in_p2(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ▶ section P2-BACKLOG → ◇ "core/lib/node-resolver.sh" in section? → ⎋ pass | fail:NODE_RESOLVER_NOT_IN_P2
    """
    # region BLOCK_Extract
    content = _read_registry()
    section = _extract_section(content, "## §P2-BACKLOG", "## §P3-BACKLOG")
    logger.info("[IMP:7][test_debt_registry_node_resolver_in_p2] P2-BACKLOG section extracted")
    # endregion

    # region BLOCK_Assert
    assert "core/lib/node-resolver.sh" in section, "NODE_RESOLVER_NOT_IN_P2"
    logger.info("[IMP:9][test_debt_registry_node_resolver_in_p2] ✅ node-resolver.sh tracked in P2-BACKLOG (P2-1)")
    # endregion


# endregion FUNC_test_debt_registry_node_resolver_in_p2


# region FUNC_test_check_file_lines_ignores_debt
## @purpose — AC5: `check-file-lines` must NOT scan `.ai/` — verified statically
##            (find root = ${PATHS_CORE_DIR}) and at runtime (exit 0, no `.ai/` output).
## @io — ⇥ caplog → ⎋ None (pytest.fail on scope regression / non-zero exit)
## @complexity — O(1) static + runtime script invocation


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · check-file-lines scope widened to .ai/
# · Scenario: AC5 — script scans only ${PATHS_CORE_DIR} (core/), never repo root
# · Last fail: N/A (preventive)
# · Remove if: check-file-lines intentionally extended to .ai/
def test_check_file_lines_ignores_debt(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ▶ read script → ◇ find "${PATHS_CORE_DIR}" present? → ◇ ".ai" absent from find paths?
    # → ⚡ bash script → ◇ exit 0? → ◇ ".ai/" absent from output? → ⎋ pass | fail
    """
    # region BLOCK_StaticScope
    script = _CHECK_FILE_LINES.read_text(encoding="utf-8") if _CHECK_FILE_LINES.exists() else ""
    assert script, f"CHECK_FILE_LINES_MISSING: {_CHECK_FILE_LINES}"
    assert re.search(r'find "\$\{PATHS_CORE_DIR\}"', script), "CHECK_FILE_LINES_NOT_SCOPED_TO_CORE"
    find_block = script[script.find('find "${PATHS_CORE_DIR}"') :]
    assert ".ai" not in find_block, "CHECK_FILE_LINES_SCANS_DOT_AI"
    logger.info("[IMP:7][test_check_file_lines_ignores_debt] Static scope OK: find rooted at ${PATHS_CORE_DIR}, no .ai/")
    # endregion

    # region BLOCK_Runtime
    result = subprocess.run(
        ["bash", str(_CHECK_FILE_LINES)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
        timeout=120,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, f"CHECK_FILE_LINES_EXIT_NONZERO: {result.returncode}\n{output}"
    assert ".ai/" not in output, f"CHECK_FILE_LINES_OUTPUT_CONTAINS_DOT_AI: {output}"
    logger.info("[IMP:9][test_check_file_lines_ignores_debt] ✅ Runtime exit 0, no .ai/ paths in output")
    # endregion


# endregion FUNC_test_check_file_lines_ignores_debt


# region FUNC_test_gitignore_covers_debt
## @purpose — AC6: `.gitignore` contains a pattern covering `.ai/debt/`.
## @io — ⇥ caplog → ⎋ None (pytest.fail if no covering pattern)
## @complexity — O(L) where L = .gitignore lines


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · .gitignore no longer ignores .ai/debt/
# · Scenario: AC6 — `.ai/*` (or equivalent) present in .gitignore
# · Last fail: N/A (preventive)
# · Remove if: .ai/debt/ deliberately tracked without force-add
def test_gitignore_covers_debt(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ▶ read .gitignore → ○ for line → ◇ re matches (^\\.ai\\*/?$ | ^\\.ai/$ | .*\\.ai/debt/?.*)? → ⎋ pass | fail
    """
    # region BLOCK_Scan
    gitignore = _GITIGNORE.read_text(encoding="utf-8") if _GITIGNORE.exists() else ""
    assert gitignore, f"GITIGNORE_MISSING: {_GITIGNORE}"
    covers = False
    for line in gitignore.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        # `.ai/*` covers `.ai/debt/`; explicit `.ai/` or `.ai/debt/` also acceptable
        if stripped == ".ai/*" or stripped == ".ai/" or stripped.startswith(".ai/debt"):
            covers = True
            logger.info("[IMP:8][test_gitignore_covers_debt] Covering pattern: '%s'", stripped)
            break
    # endregion

    # region BLOCK_Assert
    assert covers, "GITIGNORE_DOES_NOT_COVER_DEBT: no .ai/* or .ai/debt pattern found"
    logger.info("[IMP:9][test_gitignore_covers_debt] ✅ .gitignore covers .ai/debt/ (force-add required)")
    # endregion


# endregion FUNC_test_gitignore_covers_debt
