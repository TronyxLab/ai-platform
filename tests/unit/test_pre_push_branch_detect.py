# GREP_SUMMARY: test pre_push_branch_detect detect_branch stdin remote-ref refs/heads deleted-branch release main feature env-fallback git-fallback unknown R5-negative LDD
# STRUCTURE: ▶ detect_branch(lines) → ◇ feature/main/release/deleted-branch/tags-ignore/no-newline → ◇ main() stdin→env→git→unknown (capsys+DI) → ⎋ LDD IMP:7
# region MODULE_CONTRACT
## @purpose  Unit tests for pre_push_branch_detect.py (DevPlan 170 W9-F2) — detect_branch() чистая
##           функция + main() fallback-цепочка (stdin → PRE_COMMIT_REMOTE_BRANCH env → git HEAD →
##           "unknown"). Закрывают 2 TRAP[BUG] pre-push-gate.sh: 2026-08-13 (while read no-\n —
##           splitlines() корректен, тест финальной строки без \n) и 2026-08-06 (paths.sh — Python
##           модуль не source'ит shell-библиотеки).
## @scope    14 тестов. Чистые функции — native imports, 0 subprocess (git fallback через git_fn DI).
##           capsys для stdout-проверок main(). caplog LDD-траектория.
## @invariants — detect_branch: первый refs/heads/* wins; tags/короткие строки игнорируются;
##              deleted-branch (local ref "(delete)") детектится по remote ref
##              — main(): stdin_lines/environ/git_fn DI-швы (prod-дефолты не меняются)
##              — R5-negative: пустой stdin → корректный дефолт ("unknown")
##              — Каждый тест-путь несёт # 🧪 TRAP[TEST] (QA §MARKUP)
## @rationale Branch-detect — единственная ветвящаяся логика hybrid hook'а (D4): unit-покрытие
##            всех форматов stdin исключает регрессию main/release-gating (TRAP[BUG] 2026-08-13
##            найден именно unit-прогоном детекции).
## @changes  2026-08-15 | Created (DevPlan 170 W9-F2)
## @usecases pytest tests/unit/test_pre_push_branch_detect.py -v
# endregion MODULE_CONTRACT

import logging

import pytest
from _conftest.ldd import _print_ldd_trajectory

from core.internal.lint import pre_push_branch_detect as ppbd

pytestmark = pytest.mark.static_audit

# Канонический формат строки pre-push stdin (git): <local ref> <local sha> <remote ref> <remote sha>
_FEATURE_LINE = "refs/heads/feat/x abc123 refs/heads/feat/x def456"
_MAIN_LINE = "refs/heads/main abc123 refs/heads/main def456"
_RELEASE_LINE = "refs/heads/release/1.2 abc123 refs/heads/release/1.2 def456"
_ZERO_SHA = "0000000000000000000000000000000000000000"


@pytest.fixture(autouse=True)
def _capture_imp_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Capture INFO-level logs (IMP:7-10) — basicConfig in module sets WARNING."""
    caplog.set_level(logging.INFO)
    yield


# ═══════════════════════════════════════════════════════════════════
# detect_branch — чистая функция
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_detect_branch_feature
# 🧪 TRAP[TEST] · Regression: feature-ветка из remote ref
# · Scenario: push на feature → гибридный hook должен выбрать БЫСТРЫЙ чек
# · Last fail: never (new)
# · Remove if: детекция переехала
@pytest.mark.static_audit
def test_detect_branch_feature() -> None:
    """refs/heads/feat/x → 'feat/x'."""
    assert ppbd.detect_branch([_FEATURE_LINE]) == "feat/x"


# endregion FUNC_test_detect_branch_feature


# region FUNC_test_detect_branch_main
# 🧪 TRAP[TEST] · Regression: main-ветка → ПОЛНЫЙ fast-gate
# · Scenario: push на main должен гейтиться (тривиальный push не проходит мимо gate)
# · Last fail: never (new)
# · Remove if: gating main отменён
@pytest.mark.static_audit
def test_detect_branch_main() -> None:
    """refs/heads/main → 'main'."""
    assert ppbd.detect_branch([_MAIN_LINE]) == "main"


# endregion FUNC_test_detect_branch_main


# region FUNC_test_detect_branch_release
# 🧪 TRAP[TEST] · Regression: release-ветка → ПОЛНЫЙ fast-gate (release* pattern)
# · Scenario: push на release/1.2 должен гейтиться как main
# · Last fail: never (new)
# · Remove if: release-gating отменён
@pytest.mark.static_audit
def test_detect_branch_release() -> None:
    """refs/heads/release/1.2 → 'release/1.2' (префикс release* ловится hook'ом)."""
    assert ppbd.detect_branch([_RELEASE_LINE]) == "release/1.2"


# endregion FUNC_test_detect_branch_release


# region FUNC_test_detect_branch_deleted
# 🧪 TRAP[TEST] · Regression: deleted-branch push (local ref '(delete)') детектится по remote ref
# · Scenario: `git push origin :refs/heads/main` (удаление) — remote ref ВСЁ ЕЩЁ refs/heads/main →
# ·            ветка детектится; удаление main/release гейтится (shell-семантика)
# · Last fail: never (new)
# · Remove if: deletion-семантика изменена
@pytest.mark.static_audit
def test_detect_branch_deleted() -> None:
    """'(delete)' local ref + zero sha → remote ref всё равно даёт 'main'."""
    deleted_line = f"(delete) {_ZERO_SHA} refs/heads/main {_ZERO_SHA}"
    assert ppbd.detect_branch([deleted_line]) == "main"


# endregion FUNC_test_detect_branch_deleted


# region FUNC_test_detect_branch_tags_ignored
# 🧪 TRAP[TEST] · Regression: refs/tags/* игнорируется, следующий refs/heads/* wins
# · Scenario: push с тегом + веткой → выбирается ветка (shell: `refs/heads/` prefix-guard)
# · Last fail: never (new)
# · Remove if: tag-семантика изменена
@pytest.mark.static_audit
def test_detect_branch_tags_ignored() -> None:
    """refs/tags/v1.0 не матчит refs/heads/ → берётся следующая строка с веткой."""
    lines = [f"refs/tags/v1.0 {_ZERO_SHA} refs/tags/v1.0 {_ZERO_SHA}", _FEATURE_LINE]
    assert ppbd.detect_branch(lines) == "feat/x"


# endregion FUNC_test_detect_branch_tags_ignored


# region FUNC_test_detect_branch_short_line_ignored
# 🧪 TRAP[TEST] · Regression: строка <3 токенов игнорируется (shell _remote_ref пуст)
# · Scenario: битые/неполные строки stdin не должны давать ложную ветку
# · Last fail: never (new)
# · Remove if: формат stdin изменён
@pytest.mark.static_audit
def test_detect_branch_short_line_ignored() -> None:
    """'ab' (2 токена) → игнор → ''."""
    assert not ppbd.detect_branch(["ab"])


# endregion FUNC_test_detect_branch_short_line_ignored


# region FUNC_test_detect_branch_no_newline_final_line
# 🧪 TRAP[TEST] · Regression (R5, TRAP[BUG] 2026-08-13): финальная строка БЕЗ \n
# · Scenario: `echo "..." | hook` — bash read при EOF без newline возвращал 1 → while не выполнялся;
# ·            Python splitlines() корректен — детекция работает и на частичной финальной строке
# · Last fail: 2026-08-13 (P1, pre-push-gate.sh — найден unit-прогоном ДО мержа)
# · Remove if: детекция переехала на не-splitlines канал
@pytest.mark.static_audit
def test_detect_branch_no_newline_final_line() -> None:
    """Одна строка без \n (raw stdin "..." без терминатора) → ветка детектится."""
    assert ppbd.detect_branch(["refs/heads/main abc refs/heads/main def"]) == "main"
    assert ppbd.detect_branch(["refs/heads/main abc refs/heads/main def\n"]) == "main"


# endregion FUNC_test_detect_branch_no_newline_final_line


# region FUNC_test_detect_branch_empty
# 🧪 TRAP[TEST] · Regression: пустой stdin → '' (переход к env/git fallback)
# · Scenario: pre-commit always_run без stdin → не должно быть ложной ветки
# · Last fail: never (new)
# · Remove if: fallback-цепочка изменена
@pytest.mark.static_audit
def test_detect_branch_empty() -> None:
    """[] и [пустая строка] → ''."""
    assert not ppbd.detect_branch([])
    assert not ppbd.detect_branch([""])


# endregion FUNC_test_detect_branch_empty


# ═══════════════════════════════════════════════════════════════════
# main — fallback-цепочка (stdin → env → git → unknown)
# ═══════════════════════════════════════════════════════════════════


def _run_main(caplog, stdin_lines=None, environ=None, git_fn=None, argv=None) -> tuple[int, str]:
    """Вызвать ppbd.main с DI-швами и вернуть (rc, stdout)."""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = ppbd.main(argv, stdin_lines=stdin_lines, environ=environ, git_fn=git_fn)
    _print_ldd_trajectory(caplog)
    return rc, buf.getvalue().strip()


# region FUNC_test_main_stdin_priority
# 🧪 TRAP[TEST] · Regression: stdin имеет приоритет над env fallback
# · Scenario: stdin + PRE_COMMIT_REMOTE_BRANCH → побеждает stdin (shell :94 только при пустом)
# · Last fail: never (new)
# · Remove if: fallback-приоритет изменён
@pytest.mark.static_audit
def test_main_stdin_priority(caplog) -> None:
    """stdin feature → env 'main' игнорируется → stdout 'feat/x'."""
    rc, out = _run_main(
        caplog,
        stdin_lines=[_FEATURE_LINE],
        environ={"PRE_COMMIT_REMOTE_BRANCH": "main"},
        git_fn=lambda: "never-called",
    )
    assert rc == 0 and out == "feat/x"


# endregion FUNC_test_main_stdin_priority


# region FUNC_test_main_env_fallback
# 🧪 TRAP[TEST] · Regression: пустой stdin → PRE_COMMIT_REMOTE_BRANCH (pre-commit 4.x)
# · Scenario: hook под pre-commit с env-подсказкой (stdin недоступен) → release гейтится
# · Last fail: never (new)
# · Remove if: env fallback удалён
@pytest.mark.static_audit
def test_main_env_fallback(caplog) -> None:
    """stdin пуст → env PRE_COMMIT_REMOTE_BRANCH='release/x' → stdout 'release/x'; git НЕ вызывается."""
    git_called: list[str] = []
    rc, out = _run_main(
        caplog,
        stdin_lines=[],
        environ={"PRE_COMMIT_REMOTE_BRANCH": "release/x"},
        git_fn=lambda: git_called.append("x") or "never",
    )
    assert rc == 0 and out == "release/x"
    assert git_called == [], "env найден → git fallback не вызывается"


# endregion FUNC_test_main_env_fallback


# region FUNC_test_main_git_fallback
# 🧪 TRAP[TEST] · Regression: пустой stdin + пустой env → git rev-parse HEAD
# · Scenario: ручной вызов hook'а без stdin/env → локальная HEAD-ветка
# · Last fail: never (new)
# · Remove if: git fallback удалён
@pytest.mark.static_audit
def test_main_git_fallback(caplog) -> None:
    """stdin+env пусты → git_fn → stdout ветки."""
    rc, out = _run_main(caplog, stdin_lines=[], environ={}, git_fn=lambda: "main")
    assert rc == 0 and out == "main"


# endregion FUNC_test_main_git_fallback


# region FUNC_test_main_unknown_default
# 🧪 TRAP[TEST] · R5-negative: пустой stdin + пустой env + git fail → корректный дефолт "unknown"
# · Scenario: ВСЕ каналы пусты (pre-commit без stdin, вне git) → 'unknown' (feature-путь, НЕ gate)
# · Last fail: never (new)
# · Remove if: дефолт изменён (не должен стать main/release!)
@pytest.mark.static_audit
def test_main_unknown_default(caplog) -> None:
    """Пустые stdin/env + git_fn → 'unknown' — НИКОГДА не main/release (feature quick-check)."""
    rc, out = _run_main(caplog, stdin_lines=[], environ={}, git_fn=lambda: "unknown")
    assert rc == 0
    assert out == "unknown"
    assert "main" not in out and "release" not in out, "дефолт не должен маскироваться под гейт-ветку"
    assert any("Target branch" in r.message for r in caplog.records)


# endregion FUNC_test_main_unknown_default


# region FUNC_test_main_help
# 🧪 TRAP[TEST] · Regression: --help → usage в stdout, exit 0 (help smoke-контракт)
# · Scenario: help не должен читать stdin/вызывать git
# · Last fail: never (new)
# · Remove if: CLI-контракт изменён
@pytest.mark.static_audit
def test_main_help(caplog) -> None:
    """argv=['--help'] → rc 0, usage в stdout, git НЕ вызывается."""
    git_called: list[str] = []
    rc, out = _run_main(
        caplog, argv=["--help"], stdin_lines=[], environ={}, git_fn=lambda: git_called.append("x") or ""
    )
    assert rc == 0
    assert "Usage:" in out
    assert git_called == []


# endregion FUNC_test_main_help


# region FUNC_test_main_multiline_branch_break
# 🧪 TRAP[TEST] · Regression: несколько refs в stdin — ПЕРВАЯ ветка wins (shell break)
# · Scenario: multi-ref push → целевая ветка = первая refs/heads/*
# · Last fail: never (new)
# · Remove if: break-семантика изменена
@pytest.mark.static_audit
def test_main_multiline_branch_break(caplog) -> None:
    """Несколько строк → первая refs/heads/* (shell break-семантика)."""
    lines = [_MAIN_LINE, _FEATURE_LINE]
    rc, out = _run_main(caplog, stdin_lines=lines, environ={}, git_fn=lambda: "never")
    assert rc == 0 and out == "main"


# endregion FUNC_test_main_multiline_branch_break
