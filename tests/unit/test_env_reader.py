# GREP_SUMMARY: test-env-reader, env_reader, get_env_value, dotenv, last-match, export-line, comment-skip, missing-file
# STRUCTURE: ▶ last-match wins → ◇ export-строки → ◇ comments/empty skip → ◇ = в значении → ◇ missing file/var → ⊕ CLI smoke
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/shared/env_reader.py (DevPlan 172 W2.3) —
##           Python-извлечение .env-чтения из make-рецептов (замена grep/tail/cut ×4).
## @scope    tests/unit/test_env_reader.py; без Docker, без сетевых вызовов.
## @invariants
##   - Тесты используют tmp_path (Zero Hardcode Rule) — никаких реальных .env
##   - Семантика = shell-оригиналу: последнее вхождение, значение после первого `=`,
##     пустая строка при отсутствии файла/переменной (exit 0 контракт make-fallback)
## @rationale W2.3: 4 inline-копии grep|tail|cut дрейфовали; единый Python-модуль
##            требует доказательства паритета семантики (last-match, export-строки).
# endregion MODULE_CONTRACT

import pathlib

import pytest

from core.internal.shared.env_reader import get_env_value


# region FUNC_test_last_match_wins
def test_last_match_wins(tmp_path: pathlib.Path) -> None:
    """Последнее вхождение VAR= побеждает (grep + tail -n1 паритет)."""
    env_file = tmp_path / ".env"
    env_file.write_text("PLATFORM_DOMAIN=first.example\nPLATFORM_DOMAIN=second.example\n", encoding="utf-8")
    assert get_env_value(env_file, "PLATFORM_DOMAIN") == "second.example"


# endregion FUNC_test_last_match_wins


# region FUNC_test_export_lines_parsed
def test_export_lines_parsed(tmp_path: pathlib.Path) -> None:
    """Строки `export VAR=...` читаются как обычные объявления."""
    env_file = tmp_path / ".env"
    env_file.write_text("export NODE_NAME=test-e2e\n", encoding="utf-8")
    assert get_env_value(env_file, "NODE_NAME") == "test-e2e"


# endregion FUNC_test_export_lines_parsed


# region FUNC_test_comments_and_empty_skipped
def test_comments_and_empty_skipped(tmp_path: pathlib.Path) -> None:
    """Комментарии и пустые строки пропускаются, значение не ломается."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\nHTPASSWD_FILE=/tmp/x\n# ANOTHER=ignored\n",
        encoding="utf-8",
    )
    assert get_env_value(env_file, "HTPASSWD_FILE") == "/tmp/x"
    assert not get_env_value(env_file, "ANOTHER")


# endregion FUNC_test_comments_and_empty_skipped


# region FUNC_test_equals_in_value_preserved
def test_equals_in_value_preserved(tmp_path: pathlib.Path) -> None:
    """Знаки `=` внутри значения сохраняются (cut -d= -f2- паритет)."""
    env_file = tmp_path / ".env"
    env_file.write_text("PLATFORM_MASTER_EMAIL=a=b@c.example\n", encoding="utf-8")
    assert get_env_value(env_file, "PLATFORM_MASTER_EMAIL") == "a=b@c.example"


# endregion FUNC_test_equals_in_value_preserved


# region FUNC_test_missing_file_and_var
def test_missing_file_and_var(tmp_path: pathlib.Path) -> None:
    """Отсутствующий файл/переменная → пустая строка (fallback-контракт make)."""
    missing = tmp_path / "no.env"
    assert not get_env_value(missing, "NODE_NAME")
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n", encoding="utf-8")
    assert not get_env_value(env_file, "UNKNOWN_VAR")


# endregion FUNC_test_missing_file_and_var


# region FUNC_test_unrelated_prefix_not_matched
def test_unrelated_prefix_not_matched(tmp_path: pathlib.Path) -> None:
    """VARX= не матчит VAR= (grep '^VAR=' паритет — якорь на =)."""
    env_file = tmp_path / ".env"
    env_file.write_text("NODE_NAMEX=wrong\nNODE_NAME=right\n", encoding="utf-8")
    assert get_env_value(env_file, "NODE_NAME") == "right"


# endregion FUNC_test_unrelated_prefix_not_matched


# region FUNC_test_empty_value_returns_empty_string
def test_empty_value_returns_empty_string(tmp_path: pathlib.Path) -> None:
    """VAR= (без значения) → пустая строка, не исключение."""
    env_file = tmp_path / ".env"
    env_file.write_text("STATUS_METRICS_JSON=\n", encoding="utf-8")
    assert not get_env_value(env_file, "STATUS_METRICS_JSON")


# endregion FUNC_test_empty_value_returns_empty_string


# 🧪 TRAP[TEST] · NEGATIVE (R5) · env_reader_last_match — grep/tail-паритет
# · Last fail: _env_val() возвращал ПЕРВОЕ вхождение — make брал устаревшее значение
# · Remove if: семантика last-match меняется осознанно (канон shell-паритета снят)
def test_last_match_negative_first_value_rejected(tmp_path: pathlib.Path) -> None:
    """R5 negative: first-match вернул бы устаревшее значение — last-match обязателен."""
    env_file = tmp_path / ".env"
    env_file.write_text("PLATFORM_DOMAIN=stale\nPLATFORM_DOMAIN=fresh\n", encoding="utf-8")
    assert get_env_value(env_file, "PLATFORM_DOMAIN") == "fresh"


@pytest.mark.parametrize(
    "content,var,expected",
    [
        ("A=1\n", "A", "1"),
        ("A=1\r\n", "A", "1"),  # CRLF-окончания (macOS/Windows-файлы)
        ("export A=2\n", "A", "2"),
    ],
)
def test_line_endings_and_forms(tmp_path: pathlib.Path, content: str, var: str, expected: str) -> None:
    """CRLF и export-формы — паритет независимо от окончаний строк."""
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    assert get_env_value(env_file, var) == expected
