"""
# GREP_SUMMARY: test dead-code checker DEPRECATED whole-word blame porcelain committer-time mtime fallback threshold boundary tmp_path capsys
# STRUCTURE: ⚡ tmp_path fixtures → call find_marker_files / find_deprecated_lines / get_line_add_timestamp /
#            compute_age_days / check_dead_code / main → assert exclusions, blame-epoch, mtime-fallback,
#            boundary, exit codes + byte-identical output (capsys)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/lint/dead_code_checker.py (DevPlan 109 §8 $TEST_SPEC)
## @scope    find_deprecated_lines (whole-word), find_marker_files (exclusions P3-P5), blame-porcelain
##           parsing, mtime fallback (D7/D9), compute_age_days boundary (P8), check_dead_code/main
##           exit codes, byte-identical output format (P9-P11, AC5)
## @invariants
##   - tmp_path isolated repos — zero hardcoded paths
##   - Direct function calls (native pytest, no subprocess business logic)
##   - LDD trajectory printed + IMP:9 asserted before assertions (Anti-Illusion)
##   - caplog DEBUG: модульная DEBUG-телеметрия (blame/fallback) + INFO control (P11) captured
## @changes 2026-07-31 | Created (DevPlan 109 Strangler-Fig)
# endregion MODULE_CONTRACT
"""

import logging
import os
import time
from pathlib import Path

import pytest

from core.internal.lint.dead_code_checker import (
    SELF_EXCLUSIONS,
    THRESHOLD_DAYS,
    DeadCodeViolation,
    check_dead_code,
    compute_age_days,
    find_deprecated_lines,
    find_marker_files,
    get_line_add_timestamp,
    main,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger("test_dead_code_checker")


def _assert_ldd(caplog) -> None:
    """Print IMP:7-10 trajectory and assert at least one IMP:9 log (LDD protocol)."""
    found = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        msg = getattr(record, "message", "")
        if "[IMP:" in str(msg):
            imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", msg)
            if imp_level >= 9:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 business logic log found"


def _touch_marker(path: Path, text: str, mtime_ts: int) -> None:
    """Write a marker file and pin its mtime (deterministic age control)."""
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime_ts, mtime_ts))


def test_find_deprecated_lines_whole_word(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: \bDEPRECATED\b whole-word — compound _DEPRECATED_PATTERNS НЕ матчится.
    Scenario: файл с _DEPRECATED_PATTERNS (не должен матчиться), "# DEPRECATED: old api" (матч),
    обычная строка; line_num 1-based, текст полный (cut -d: -f2- семантика, P9).
    Last fail: n/a (new test). Remove if: whole-word match семантика изменена намеренно."""
    caplog.set_level(logging.DEBUG)
    f = tmp_path / "sample.py"
    f.write_text("_DEPRECATED_PATTERNS = []\n# DEPRECATED: old api\nnormal = 1\n", encoding="utf-8")

    hits = find_deprecated_lines(f)
    logger.critical("[IMP:9][test] find_deprecated_lines: hits=%s — OK", hits)

    _assert_ldd(caplog)
    assert hits == [(2, "# DEPRECATED: old api")], f"whole-word match broken: {hits}"


def test_find_marker_files_exclusions(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · AC4 regression: exclusions P3 (.venv/.git/.ai root), P4 (node_modules any depth),
    P5 (SELF_EXCLUSIONS 3 файла), extension filter — только eligible .sh/.py.
    Scenario: tmp repo с .venv/, .git/, .ai/, nested node_modules/, 3 self-excluded файла, .sh/.py/.txt.
    Last fail: n/a (new test). Remove if: exclusion set намеренно изменён."""
    caplog.set_level(logging.DEBUG)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "good.sh").write_text("x")
    (root / "good.py").write_text("x")
    (root / "notes.txt").write_text("x")
    for d in (".venv", ".git", ".ai"):
        (root / d).mkdir()
    (root / ".venv" / "x.py").write_text("x")
    (root / ".git" / "y.sh").write_text("x")
    (root / ".ai" / "z.py").write_text("x")
    nested = root / "sub" / "node_modules"
    nested.mkdir(parents=True)
    (nested / "w.py").write_text("x")
    for rel in SELF_EXCLUSIONS:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")

    files = find_marker_files(root, SELF_EXCLUSIONS)
    rels = {os.path.relpath(f, root) for f in files}
    logger.critical("[IMP:9][test] find_marker_files: rels=%s — OK", sorted(rels))

    _assert_ldd(caplog)
    assert rels == {"good.sh", "good.py"}, f"exclusions broken: {rels}"


def test_parse_blame_porcelain_committer_time(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: git blame --porcelain → committer-time epoch извлечение (P6).
    Scenario: статичный porcelain-фикстур (author/committer headers + committer-time + tab-контент) →
    правильный epoch; tab-строка контента не даёт ложного матча.
    Last fail: n/a (new test). Remove if: blame parsing изменён (напр. whole-file batching, D6)."""
    caplog.set_level(logging.DEBUG)

    class FakeProc:
        returncode = 0
        stdout = (
            "a1b2c3 author <a@b> 1785519459 1\n"
            "author Author\n"
            "author-mail <a@b>\n"
            "author-time 1785519459\n"
            "author-tz +0300\n"
            "committer Committer\n"
            "committer-mail <c@d>\n"
            "committer-time 1785519459\n"
            "committer-tz +0300\n"
            "summary line\n"
            "filename f.py\n"
            "\tline content\n"
        )
        stderr = ""

    ts = get_line_add_timestamp(tmp_path, "f.py", 3, 12345, runner=lambda *_, **__: FakeProc())
    logger.critical("[IMP:9][test] blame_committer_time=%s — OK", ts)

    _assert_ldd(caplog)
    assert ts == 1785519459, f"committer-time extraction broken: {ts}"


def test_get_line_add_timestamp_fallback_mtime(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: blame empty/error (rc!=0) → os.path.getmtime fallback (D7/D9).
    Scenario: runner → rc=128 пустой stdout (untracked/не git repo) →
    возвращается переданный mtime; FileNotFoundError/TimeoutExpired путь — тот же fallback.
    Last fail: n/a (new test). Remove if: mtime fallback заменён (напр. всегда-blame)."""
    caplog.set_level(logging.DEBUG)
    f = tmp_path / "untracked.py"
    f.write_text("x")
    mtime = int(Path(f).stat().st_mtime)

    class FakeProc:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    ts = get_line_add_timestamp(tmp_path, "untracked.py", 1, mtime, runner=lambda *_, **__: FakeProc())
    logger.critical("[IMP:9][test] fallback_mtime=%s — OK", ts)

    _assert_ldd(caplog)
    assert ts == mtime, f"mtime fallback broken: {ts} != {mtime}"


def test_compute_age_days_boundary(caplog) -> None:
    """# 🧪 TRAP[TEST] · P8 boundary: 30 дней → НЕ violation (30 > 30 False), 31 → violation,
    0 → OK, negative (clock skew) → OK. Floor division (now - ts) // 86400.
    Scenario: фиксированный now; граница ровно на threshold; сравнение строго больше.
    Last fail: n/a (new test). Remove if: threshold semantics изменены (>= или другой divisor)."""
    caplog.set_level(logging.DEBUG)
    now = 1_785_519_459

    d30 = compute_age_days(now - 30 * 86400, now)
    d31 = compute_age_days(now - 31 * 86400, now)
    d0 = compute_age_days(now, now)
    dneg = compute_age_days(now + 86400, now)

    assert d30 == 30 and d31 == 31 and d0 == 0 and dneg == -1, "floor division broken"
    assert not (d30 > THRESHOLD_DAYS), "30 days must NOT be a violation (strict >)"
    assert d31 > THRESHOLD_DAYS, "31 days must be a violation"
    assert not (d0 > THRESHOLD_DAYS) and not (dneg > THRESHOLD_DAYS), "0/negative must be OK"
    logger.critical("[IMP:9][test] compute_age_days: 30→%s 31→%s 0→%s neg→%s — OK", d30, d31, d0, dneg)
    _assert_ldd(caplog)


def test_check_dead_code_clean_pass(tmp_path: Path, caplog, capsys) -> None:
    """# 🧪 TRAP[TEST] · Regression: clean pass — все маркеры свежие (mtime=now) → пустые violations,
    exit 0, stderr PASS c [IMP:9].
    Scenario: tmp проект с одним DEPRECATED маркером; git отсутствует в tmp (не repo) → mtime fallback;
    main() с project_root=tmp_path (path-injection DI) → return 0.
    Last fail: n/a (new test). Remove if: exit-code/verdict контракт изменён."""
    caplog.set_level(logging.DEBUG)
    fresh = tmp_path / "fresh.py"
    _touch_marker(fresh, "# DEPRECATED: still fresh\n", int(time.time()))

    violations = check_dead_code(tmp_path, threshold_days=THRESHOLD_DAYS)
    rc = main(["--threshold", str(THRESHOLD_DAYS)], project_root=tmp_path)
    out = capsys.readouterr()
    logger.critical("[IMP:9][test] clean_pass: rc=%s violations=%d — OK", rc, len(violations))

    _assert_ldd(caplog)
    assert violations == [], f"fresh markers must not violate: {violations}"
    assert rc == 0, f"clean run must exit 0, got {rc}"
    assert "[IMP:9][check-dead-code] PASS:" in out.err, f"PASS verdict missing: {out.err!r}"
    assert "[IMP:7][check-dead-code] OK: fresh.py:1" in out.out, f"OK line missing: {out.out!r}"


def test_check_dead_code_violation_fail(tmp_path: Path, caplog, capsys) -> None:
    """# 🧪 TRAP[TEST] · Regression: violation — маркер 2 дня от mtime, threshold 0 → 1 violation,
    exit 1, STALE stdout строка присутствует.
    Scenario: tmp проект со stale маркером; main([--threshold 0], project_root=tmp_path) → return 1;
    check_dead_code → ровно 1 DeadCodeViolation с корректными полями.
    Last fail: n/a (new test). Remove if: exit-code/verdict контракт изменён."""
    caplog.set_level(logging.DEBUG)
    stale = tmp_path / "stale.py"
    _touch_marker(stale, "# DEPRECATED: old marker\n", int(time.time()) - 2 * 86400)

    violations = check_dead_code(tmp_path, threshold_days=0)
    rc = main(["--threshold", "0"], project_root=tmp_path)
    out = capsys.readouterr()
    logger.critical("[IMP:9][test] violation_fail: rc=%s violations=%d — OK", rc, len(violations))

    _assert_ldd(caplog)
    assert len(violations) == 1, f"expected 1 violation, got {violations}"
    assert violations[0] == DeadCodeViolation("stale.py", 1, 2, "# DEPRECATED: old marker")
    assert rc == 1, f"violation run must exit 1, got {rc}"
    assert "[IMP:10][check-dead-code] STALE: stale.py:1" in out.out, f"STALE line missing: {out.out!r}"
    assert "[IMP:10][check-dead-code] FAIL: 1 marker(s) exceed 0-day grace period" in out.err


def test_output_format_byte_identical(tmp_path: Path, caplog, capsys) -> None:
    """# 🧪 TRAP[TEST] · AC5: byte-identical формат P9-P11 — STALE (IMP:10 + '  >>> text[:120]'),
    OK (IMP:7, threshold интерполирован), control на stderr (IMP:8 scan, IMP:10 FAIL + Fix, IMP:9 PASS).
    Scenario: fresh.py (mtime=now) + stale.py (mtime 2d ago), threshold 0 → полный pipeline (main);
    сравнение точных строк формата.
    Last fail: n/a (new test). Remove if: output format контракт изменён намеренно."""
    caplog.set_level(logging.DEBUG)
    fresh = tmp_path / "fresh.py"
    _touch_marker(fresh, "# DEPRECATED: fresh marker\n", int(time.time()))
    stale = tmp_path / "stale.py"
    _touch_marker(stale, "# DEPRECATED: old marker\n", int(time.time()) - 2 * 86400)

    rc = main(["--threshold", "0"], project_root=tmp_path)
    out = capsys.readouterr()
    logger.critical("[IMP:9][test] byte_identical: rc=%s — OK", rc)

    _assert_ldd(caplog)
    assert rc == 1
    assert "[IMP:10][check-dead-code] STALE: stale.py:1 — marker is 2 days old (threshold: 0)" in out.out, (
        f"STALE format broken: {out.out!r}"
    )
    assert "  >>> # DEPRECATED: old marker" in out.out, f">>> text[:120] broken: {out.out!r}"
    assert "[IMP:7][check-dead-code] OK: fresh.py:1 — marker is 0d old (within 0d grace)" in out.out, (
        f"OK format broken: {out.out!r}"
    )
    assert "[IMP:8][check-dead-code] Scanning for DEPRECATED markers in .sh and .py files..." in out.err, (
        f"scan-start broken: {out.err!r}"
    )
    assert "[IMP:10][check-dead-code] FAIL: 1 marker(s) exceed 0-day grace period" in out.err
    assert "[IMP:10][check-dead-code] Fix: remove stale markers or update if still active" in out.err
