"""
# GREP_SUMMARY: test grepsummary validator scan-all staged keywords sh-refs tmp_path
# STRUCTURE: ⚡ tmp_path → write files → call extract_keywords / validate_keywords_present / extract_sh_refs /
#            resolve_sh_ref_scan / scan_all → assert scan vs staged parity + AC10 exceptions
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/lint/grepsummary_validator.py (DevPlan 106 §9 $TEST_SPEC)
## @scope    extract_keywords (scan/staged), validate_keywords_present, extract_sh_refs (plain/backtick),
##           scan_all e2e, AC10 false-positive exceptions (http /opt/ prose-without-slash ..)
## @invariants
##   - tmp_path isolated repos — zero hardcoded paths
##   - Direct function calls (native pytest, no subprocess)
##   - LDD trajectory printed + IMP:9 asserted before assertions (Anti-Illusion)
## @changes 2026-07-31 | Created (DevPlan 106 Strangler-Fig)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from core.internal.lint.grepsummary_validator import (
    extract_keywords,
    extract_sh_refs,
    resolve_sh_ref_scan,
    scan_all,
    validate_keywords_present,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger("test_grepsummary_validator")


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


# 🧪 TRAP[TEST] · Regression: scan-режим strip '#'/--> /<!-- + skip flags -x/--x + skip пустых.
# Scenario: HTML-маркеры и флаги удаляются, литеральные keywords сохраняются.
# Last fail: n/a (new test). Remove if: extract_keywords scan-семантика изменена намеренно."""
# 🧪 TRAP[TEST] · Regression: staged-режим БЕЗ strip HTML-маркеров и БЕЗ skip flags (§2.3).
# Scenario: raw keywords сохраняются включая '-->' и '-x'; маркер '# GREP_SUMMARY:' (с пробелом).
# Last fail: n/a (new test). Remove if: staged-семантика изменена намеренно."""
@pytest.mark.parametrize(
    "line,mode,expected",
    [
        ("# GREP_SUMMARY: alpha, #beta, -->, <!--gamma, -x, --yy", "scan", ["alpha", "beta", "gamma"]),
        ("# GREP_SUMMARY: alpha, -->, -x, gamma", "staged", ["alpha", "-->", "-x", "gamma"]),
    ],
)
def test_extract_keywords_modes(line, mode, expected, caplog) -> None:
    """extract_keywords: scan-строка vs staged-raw (2 режима 1:1, §2.3 семантика)."""
    caplog.set_level(logging.INFO)
    kws = extract_keywords(line, mode=mode)
    logger.critical("[IMP:9][test] extract_keywords_%s_mode: %s — OK", mode, kws)

    _assert_ldd(caplog)
    assert kws == expected, f"{mode}-режим broken: {kws}"


def test_validate_keywords_present(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: keyword найден case-insensitive (substring), не найден → ошибка.
    Scenario: 'alpha' vs 'ALPHA' в контенте; отсутствующий keyword даёт ровно одну [FAIL]-ошибку.
    Last fail: n/a (new test). Remove if: grep -qiF семантика изменена."""
    caplog.set_level(logging.INFO)
    f = tmp_path / "sample.py"
    f.write_text("# file containing Alpha and beta words\n")

    ok_errs = validate_keywords_present(f, ["alpha", "ALPHA", "alpha and beta"])
    missing_errs = validate_keywords_present(f, ["alpha", "not-present-keyword"])

    _assert_ldd(caplog)
    assert ok_errs == [], f"case-insensitive substring must pass: {ok_errs}"
    assert len(missing_errs) == 1 and "not-present-keyword" in missing_errs[0]
    logger.critical("[IMP:9][test] validate_keywords_present: ok=%s missing=%s — OK", ok_errs, missing_errs)


def test_extract_sh_refs_plain_backtick(caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: plain-паттерн (lookbehind) vs backtick-only экстракция.
    Scenario: backtick-обрамлённая ссылка НЕ матчится plain (lookbehind), не-обрамлённая НЕ матчится backtick;
    backtick dedupe + sort (tr -d '`' | sort -u).
    Last fail: n/a (new test). Remove if: GNU/BSD unify паттерн изменён."""
    caplog.set_level(logging.INFO)
    text = "use `scripts/deploy.sh` and plain scripts/tools.sh text; see https://x/y.sh"

    plain = extract_sh_refs(text, backtick_only=False)
    backtick = extract_sh_refs(text, backtick_only=True)
    dupes = extract_sh_refs("`b.sh` `a.sh` `b.sh`", backtick_only=True)
    logger.critical("[IMP:9][test] extract_sh_refs: plain=%s backtick=%s dupes=%s — OK", plain, backtick, dupes)

    _assert_ldd(caplog)
    assert plain == ["scripts/tools.sh"], f"plain lookbehind broken: {plain}"
    assert backtick == ["scripts/deploy.sh"], f"backtick-only broken: {backtick}"
    assert dupes == ["a.sh", "b.sh"], f"backtick dedupe/sort broken: {dupes}"


def test_sh_ref_scan_exceptions_preserved(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · AC10: http-ссылка, /opt/ путь, проза без '/', '..' — skip без ошибок.
    Scenario: .md со всеми false-positive исключениями → scan_all → errors == 0.
    Last fail: n/a (new test). Remove if: AC10 исключения намеренно изменены."""
    caplog.set_level(logging.INFO)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "doc.md").write_text(
        "Link [tool.sh](http://example.com/tool.sh) and /opt/platform/core/x.sh "
        "and prose mention of foo.sh; see ../scripts/up.sh for details.\n"
    )

    errors, count = scan_all(repo)

    _assert_ldd(caplog)
    assert errors == [], f"AC10 exceptions must skip, got: {errors}"
    assert count == 1, f"expected 1 scanned file, got {count}"
    logger.critical("[IMP:9][test] sh_ref_scan_exceptions: errors=%s count=%d — OK", errors, count)


def test_scan_all_full_pass_and_fail(tmp_path: Path, caplog) -> None:
    """# 🧪 TRAP[TEST] · Regression: scan_all полный проход (pass) и детекция битых keywords/ссылок (fail).
    Scenario: валидный GREP_SUMMARY → errors=0; битый keyword → errors>0; .md с несуществующей
    .sh-ссылкой (scripts/missing.sh) → errors>0; resolve_sh_ref_scan: не-существующий → False.
    Last fail: n/a (new test). Remove if: scan_all семантика изменена."""
    caplog.set_level(logging.INFO)
    good = tmp_path / "good"
    good.mkdir()
    (good / "ok.py").write_text("# GREP_SUMMARY: alpha, beta\n# file containing alpha and beta words\n")

    errs_ok, count_ok = scan_all(good)

    bad = tmp_path / "bad"
    bad.mkdir()
    # scan-strip артефакт: kw "a#b" → strip '#' → "ab", которого в файле нет (keyword check может
    # фейлиться только так — объявляющая строка сама содержит raw keyword)
    (bad / "broken.py").write_text("# GREP_SUMMARY: a#b\n# file with no keyword content at all\n")
    (bad / "refs.md").write_text("uses scripts/missing.sh here\n")

    errs_bad, count_bad = scan_all(bad)

    _assert_ldd(caplog)
    assert errs_ok == [] and count_ok == 1, f"clean repo must pass: {errs_ok}"
    assert count_bad == 2, f"expected 2 scanned files, got {count_bad}"
    assert any("'ab'" in e for e in errs_bad), f"broken keyword not detected: {errs_bad}"
    assert any("scripts/missing.sh" in e for e in errs_bad), f"broken sh ref not detected: {errs_bad}"
    assert resolve_sh_ref_scan(good, "scripts/missing.sh") is False
    logger.critical("[IMP:9][test] scan_all: ok=%s/%d bad=%d errors — OK", errs_ok, count_ok, len(errs_bad))
