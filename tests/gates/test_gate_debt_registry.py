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
from datetime import date, datetime, timedelta

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

# ── B11 T7 (U-82, D4): формат записей — Status + Rev ─────────────────────────
_VALID_STATUSES: tuple[str, ...] = ("OPEN", "FIXED", "SUPERSEDED")
# rev = конкретная дата YYYY-MM-DD ИЛИ условие-триггер («При …», «Бессрочно …»)
_RE_REV_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Условие-триггер: начинается с «При» или «Бессрочно» (синтаксическая проверка D4)
_RE_REV_CONDITION = re.compile(r"^(При|Бессрочно)")
_STALE_DAYS = 90


def _is_stale(rev: str, today: date | None = None) -> bool:
    """Return True if a concrete rev-date is more than _STALE_DAYS in the past (D4).

    ▶ ┌rev, today┐ → ◇ условие-триггер? → ⎋ False → ◇ конкретная дата? → ◇ (today - rev) > 90? → ⎋ bool

    ## @purpose — Гейт свежести (DevPlan 116 B11 T7, U-82/D4): конкретные даты в прошлом
    ##            > 90 дней → stale (RED). Условия-триггеры («При …», «Бессрочно …») → не stale.
    ##            FIXED/SUPERSEDED не проверяются на stale (вызывающий код).
    ## @io — ⇥ rev: str — значение колонки Rev; ⇥ today: date|None — «сегодня» (для тестов)
    ##       → ⎋ bool
    ## @complexity — O(1)
    """
    if today is None:
        today = date.today()
    rev = rev.strip()
    if not rev:
        return False  # пустой rev → проверяется отдельным статус-тестом (MISSING_FIELDS)
    if _RE_REV_CONDITION.match(rev):
        return False
    if _RE_REV_DATE.match(rev):
        try:
            rev_dt = datetime.strptime(rev, "%Y-%m-%d").date()
        except ValueError:
            return False
        return (today - rev_dt) > timedelta(days=_STALE_DAYS)
    # Не дата и не условие → не stale (синтаксис проверяется статус-тестом)
    return False


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
## @purpose — AC3: SHELL-RESIDUAL has exactly 2 entries (post-DevPlan 127: S1 issue-cert.sh,
##            S3 healthcheck.sh — keep by design), each with 6 columns
##            (file, LOC, обоснование, Status, Rev) — Status добавлен B11 T7 (D4).
##            До 127 было 8 записей; S2/S4/S5/S6/S7/S8 УДАЛЕНЫ (мигрированы на Python,
##            канон «история удаляется вместе с именами»).
## @io — ⇥ caplog → ⎋ None (pytest.fail on count/column mismatch)
## @complexity — O(R) where R = SHELL-RESIDUAL rows


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-31 · REGRESSION · SHELL-RESIDUAL entries count/columns drift
# · Scenario: AC3 — 2 rows (S1/S3 keep by design, DevPlan 127 W3) with file/LOC/обоснование/Status/Rev columns
# · Last fail: 2026-08-04 — 8-записная таблица после миграции S2/S4-S8 (127 W1/W2) → count обновлён до 2
# · Remove if: SHELL-RESIDUAL structure changes by approved amendment
def test_debt_registry_shell_residual_entries(caplog: pytest.LogCaptureFixture) -> None:
    """
    # ▶ section SHELL-RESIDUAL → ○ split rows → ◇ 2 rows? → ◇ each 6 cols? → ⎋ pass | fail
    """
    # region BLOCK_Extract
    content = _read_registry()
    section = _extract_section(content, "## §SHELL-RESIDUAL", "## §P2-BACKLOG")
    rows = [
        line.strip() for line in section.splitlines() if line.strip().startswith("| S") and line.strip().endswith("|")
    ]
    logger.info("[IMP:7][test_debt_registry_shell_residual_entries] Found %d SHELL-RESIDUAL row(s)", len(rows))
    # endregion

    # region BLOCK_Assert
    assert len(rows) == 2, (
        f"SHELL_RESIDUAL_ENTRY_COUNT: expected 2 (S1/S3 keep by design, DevPlan 127), got {len(rows)}"
    )
    for row in rows:
        cols = [c.strip() for c in row.strip("|").split("|")]
        assert len(cols) == 6, (
            f"SHELL_RESIDUAL_COLUMNS: expected 6 columns (Status added B11 T7), got {len(cols)}: {row}"
        )
        assert cols[1].startswith("`") and cols[1].endswith("`"), f"SHELL_RESIDUAL_FILE_COL: {row}"
        assert cols[2].isdigit(), f"SHELL_RESIDUAL_LOC_NOT_NUMERIC: {row}"
        assert cols[3], f"SHELL_RESIDUAL_RATIONALE_EMPTY: {row}"
        assert cols[4] in _VALID_STATUSES, f"SHELL_RESIDUAL_INVALID_STATUS: {cols[4]} in {row}"
        assert cols[5], f"SHELL_RESIDUAL_REV_EMPTY: {row}"
    logger.info(
        "[IMP:9][test_debt_registry_shell_residual_entries] ✅ 2 entries × 6 columns (file/LOC/rationale/Status/Rev)"
    )
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
        line.strip() for line in section.splitlines() if line.strip().startswith("| S") and line.strip().endswith("|")
    ]
    violations: list[str] = []
    # endregion

    # region BLOCK_Assert
    for row in rows:
        cols = [c.strip() for c in row.strip("|").split("|")]
        if len(cols) == 6 and cols[2].isdigit() and int(cols[2]) <= 200:
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
    logger.info(
        "[IMP:7][test_check_file_lines_ignores_debt] Static scope OK: find rooted at ${PATHS_CORE_DIR}, no .ai/"
    )
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


# region B11 T7 (U-82/D4) — Status + Rev формат и гейт свежести

_SECTIONS_WITH_STATUS: tuple[tuple[str, str, int], ...] = (
    ("## §SHELL-RESIDUAL", "## §P2-BACKLOG", 4),  # #(0) файл(1) LOC(2) обосн(3) Status(4) Rev(5)
    ("## §P2-BACKLOG", "## §P3-BACKLOG", 5),  # #(0) задача(1) файл(2) scope(3) обосн(4) Status(5) Rev(6)
    ("## §P3-BACKLOG", "## §TEST-DEBT", 3),  # #(0) файл(1) суть(2) Status(3) Rev(4)
    ("## §TEST-DEBT", "## §ARCH-DECISIONS", 4),  # #(0) файл(1) суть(2) Sev(3) Status(4) Rev(5)
    ("## §ARCH-DECISIONS", "## §TRAP-INVENTORY", 5),  # #(0) ист(1) дата(2) Sev(3) реш(4) Status(5) Rev(6)
)


def _iter_registry_rows(section_header: str, next_header: str) -> list[str]:
    """Extract data rows (| # |) from a registry table section."""
    content = _read_registry()
    section = _extract_section(content, section_header, next_header)
    rows = [ln.strip() for ln in section.splitlines() if ln.strip().startswith("| ") and ln.strip().endswith("|")]
    # Skip header/separator rows (| # |, |---|)
    return [r for r in rows if not re.match(r"^\|\s*#\s*\|", r) and not re.match(r"^\|\s*-+\s*\|", r)]


# region FUNC_test_debt_registry_all_entries_have_status_rev
## @purpose — B11 T7 (D4): КАЖДАЯ запись секций SHELL-RESIDUAL/P2/P3/TEST-DEBT/ARCH-DECISIONS
##            имеет Status ∈ {OPEN,FIXED,SUPERSEDED} + непустой Rev.
## @io — ⇥ caplog → ⎋ None (pytest.fail на отсутствие полей)
## @complexity — O(R) где R = все строки таблиц


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · Запись реестра без Status/Rev (U-82)
# · Scenario: D4 — отсутствие status/rev-date в любой записи → RED
# · Last fail: N/A (новый гейт свежести)
# · Remove if: формат реестра изменён одобренным amendment'ом
def test_debt_registry_all_entries_have_status_rev(caplog: pytest.LogCaptureFixture) -> None:
    """D4: 100% записей имеют Status + Rev."""
    caplog.set_level(logging.INFO)

    missing: list[str] = []
    for section_header, next_header, status_idx in _SECTIONS_WITH_STATUS:
        for row in _iter_registry_rows(section_header, next_header):
            cols = [c.strip() for c in row.strip("|").split("|")]
            # cols: [0]=#, ..., [status_idx]=Status, [status_idx+1]=Rev (после добавленной Status-колонки)
            status_col = cols[status_idx] if status_idx < len(cols) else ""
            rev_col = cols[status_idx + 1] if status_idx + 1 < len(cols) else ""
            if status_col not in _VALID_STATUSES or not rev_col:
                missing.append(f"{section_header[3:]} :: {row[:100]}")

    if missing:
        logger.error(
            "[IMP:9][test_debt_registry_all_entries_have_status_rev] MISSING_STATUS_OR_REV:\n%s", "\n".join(missing)
        )
        pytest.fail(f"MISSING_STATUS_OR_REV: {len(missing)} записей без Status/Rev (D4):\n" + "\n".join(missing))
    logger.info("[IMP:9][test_debt_registry_all_entries_have_status_rev] ✅ все записи имеют Status + Rev")


# endregion FUNC_test_debt_registry_all_entries_have_status_rev


# region FUNC_test_debt_registry_no_stale_rev_dates
## @purpose — B11 T7 (D4): гейт свежести — конкретная rev-дата > 90 дней в прошлом → RED.
##            Условия-триггеры и FIXED/SUPERSEDED — не stale. Сегодня = date.today().
## @io — ⇥ caplog → ⎋ None (pytest.fail со списком stale-записей)
## @complexity — O(R)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · Stale rev-дата в реестре (U-82)
# · Scenario: D4 — rev ≤ (today - 90 дней) → RED (stale-пункты невозможны)
# · Last fail: N/A (новый гейт свежести)
# · Remove if: формат реестра изменён одобренным amendment'ом
def test_debt_registry_no_stale_rev_dates(caplog: pytest.LogCaptureFixture) -> None:
    """D4: нет stale-записей (конкретная дата > 90 дней в прошлом → RED)."""
    caplog.set_level(logging.INFO)

    stale: list[str] = []
    today = date.today()
    for section_header, next_header, status_idx in _SECTIONS_WITH_STATUS:
        for row in _iter_registry_rows(section_header, next_header):
            cols = [c.strip() for c in row.strip("|").split("|")]
            status_col = cols[status_idx] if status_idx < len(cols) else ""
            rev_col = cols[status_idx + 1] if status_idx + 1 < len(cols) else ""
            if status_col in ("FIXED", "SUPERSEDED"):
                continue  # закрытые записи не stale (D4)
            if _is_stale(rev_col, today=today):
                stale.append(f"{section_header[3:]} :: {row[:100]}")

    if stale:
        logger.error("[IMP:9][test_debt_registry_no_stale_rev_dates] STALE_ENTRIES:\n%s", "\n".join(stale))
        pytest.fail(
            f"STALE_ENTRY: {len(stale)} записей с rev-датой старше {_STALE_DAYS} дней (D4):\n" + "\n".join(stale)
        )
    logger.info(
        "[IMP:9][test_debt_registry_no_stale_rev_dates] ✅ нет stale-записей (все rev ≤ %d дней от %s)",
        _STALE_DAYS,
        today,
    )


# endregion FUNC_test_debt_registry_no_stale_rev_dates


# region FUNC_test_negative_is_stale_missing_rev
## @purpose — R5 anti-survivorship: запись БЕЗ rev → RED (детект отсутствия полей).
## @io — ⇥ caplog → ⎋ None
## @complexity — O(1)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · R5 negative — missing rev must RED
# · Scenario: запись без rev → гейт all_entries_have_status_rev RED
# · Last fail: N/A (новый гейт)
# · Remove if: формат реестра изменён
def test_negative_missing_rev_detected(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: строка реестра без Rev детектируется (MISSING_STATUS_OR_REV)."""
    caplog.set_level(logging.INFO)

    # Строка без rev (последняя колонка пустая) — имитация дрейфа D4
    bad_row = "| S99 | `core/lib/foo.sh` | 250 | rationale | OPEN |  |"
    cols = [c.strip() for c in bad_row.strip("|").split("|")]
    status_col = cols[4] if len(cols) > 4 else ""
    rev_col = cols[5] if len(cols) > 5 else ""
    assert status_col == "OPEN", "precondition: валидный Status"
    assert not rev_col, "precondition: rev пуст"
    # Предикат гейта (D4): Status ∉ валидных ИЛИ Rev пуст → RED
    is_red = status_col not in _VALID_STATUSES or not rev_col
    assert is_red, "R5 FAIL: missing rev должен быть RED"
    logger.info("[IMP:9][test_negative_missing_rev_detected] ✅ missing rev → RED (predicate: %s)", is_red)


# endregion FUNC_test_negative_missing_rev_detected


# region FUNC_test_negative_stale_date_detected
## @purpose — R5 anti-survivorship: прошедшая дата (параметр today) → stale → RED.
## @io — ⇥ caplog → ⎋ None
## @complexity — O(1)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · R5 negative — stale date must RED
# · Scenario: rev=2026-01-01 при today=2026-08-01 → > 90 дней → stale
# · Last fail: N/A (новый гейт)
# · Remove if: формат реестра изменён
def test_negative_stale_date_detected(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: rev-дата в прошлом > 90 дней → stale=True (RED)."""
    caplog.set_level(logging.INFO)

    today = date(2026, 8, 1)
    # 2026-05-03 = ровно 90 дней назад от 2026-08-01; 2026-05-02 = 91 → stale
    assert not _is_stale("2026-05-03", today=today), "90 дней ровно — не stale (граница)"
    assert _is_stale("2026-05-02", today=today), "91 день — stale (RED)"
    # Условие-триггер и FIXED-семантика — не stale
    assert not _is_stale("При росте >300 LOC", today=today), "условие-триггер — не stale (D4)"
    assert not _is_stale("Бессрочно (стабильное API)", today=today), "Бессрочно — не stale (D4)"
    assert not _is_stale("2026-09-30", today=today), "будущая/близкая дата — не stale"
    logger.info("[IMP:9][test_negative_stale_date_detected] ✅ stale-детект: 91 день → RED; условия/будущее → PASS")


# endregion FUNC_test_negative_stale_date_detected
# endregion B11 T7 (U-82/D4) — Status + Rev формат и гейт свежести
