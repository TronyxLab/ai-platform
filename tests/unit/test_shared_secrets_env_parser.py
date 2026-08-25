#!/usr/bin/env python3
# GREP_SUMMARY: test secrets_env_parser strict unparsable lines fatal merge-guard backward-compat tmp_path LDD
# STRUCTURE: ▶ fixture(secrets.env) → ◇ strict? → ⊕ parse → ◇ garbage-строки → ConfigValidationError | ⎋ dict
# region MODULE_CONTRACT
## @purpose  QA R5 (DevPlan 14 T2.A): strict-режим shared secrets_env_parser — непустые
##           не-комментарий строки без валидного key=value → ConfigValidationError со списком
##           строк; backward-compat матрица легитимных форм при strict=False.
## @scope    tests/unit/ — module under test: core/internal/shared/secrets_env_parser.py
## @invariants
##   - strict=True + garbage → исключение, БАЙТЫ файла до/после идентичны (parse read-only)
##   - strict=False: comments/blank/export/inline-#/quoted парсятся штатно (матрица)
##   - P0 partial-parse вход («1 валидная + garbage») детектируется (R5 negative)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from _conftest.ldd import ldd_trajectory

from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.secrets_env_parser import parse

logger = logging.getLogger(__name__)


def _write_secrets(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "secrets.env"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("# только комментарий\n\n", {}, id="comments-blank"),
        pytest.param("export FOO=bar\n", {"FOO": "bar"}, id="export-prefix"),
        pytest.param('QUOTED="hello world" # tail comment\n', {"QUOTED": "hello world"}, id="quoted-inline-hash"),
        pytest.param("PLAIN=value # not-a-tag\n", {"PLAIN": "value"}, id="inline-comment-unquoted"),
    ],
)
@ldd_trajectory
def test_strict_false_backward_compat_matrix(
    content: str,
    expected: dict[str, str],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """strict=False (default): легитимные формы парсятся штатно — потребители не тронуты."""
    caplog.set_level(logging.DEBUG)
    path = _write_secrets(tmp_path, content)

    result = parse(str(path))

    assert result == expected, f"backward-compat сломан на входе {content!r}: {result!r}"
    logger.info("[IMP:9][test][parser] legacy form OK: %s", list(expected))


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R5/T2.A — P0 partial-parse fail-closed
# · Scenario: «1 валидная строка + garbage» — legacy-парсер глотал garbage → merge/persist
#   перезаписывал файл, теряя нераспарсенные операторские секреты (REGRESSIONS.md R5)
# · Last fail: 2026-08-25 — parse() не имел строгого режима; merge-guard ловил только случай
#   «0 записей из непустого файла», но НЕ частичный парсинг
# · Remove if: merge-path перестанет использовать strict-preflight
@ldd_trajectory
def test_strict_unparsable_lines_fatal(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """strict=True + «1 валидная + garbage» → ConfigValidationError; байты файла нетронуты."""
    caplog.set_level(logging.DEBUG)
    original = "GOOD_KEY=good-value\ngarbage line without equals\nANOTHER=ok\n"
    path = _write_secrets(tmp_path, original)
    bytes_before = path.read_bytes()

    with pytest.raises(ConfigValidationError) as exc_info:
        parse(str(path), strict=True)

    assert "line(s) [2]" in str(exc_info.value), f"ожидается номер garbage-строки: {exc_info.value}"
    assert path.read_bytes() == bytes_before, "R5 FAIL: parse обязан быть read-only"
    logger.info("[IMP:9][test][parser] strict fatal on line 2, file bytes intact")

    # Тот же вход при strict=False (default) — legacy skip-garbage семантика сохранена
    result_legacy = parse(str(path))
    assert result_legacy == {"GOOD_KEY": "good-value", "ANOTHER": "ok"}
    logger.info("[IMP:9][test][parser] default mode still skips garbage: %d entries", len(result_legacy))


# 🧪 TRAP[TEST] · 2026-08-25 · POSITIVE · strict на чистом файле — no-op
# · Regression: защита от false-positive strict на легитимных формах
# · Last fail: N/A (preventive)
# · Remove if: strict-режим удаляется
@ldd_trajectory
def test_strict_clean_file_passes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """strict=True на валидном файле (комментарии+blank+export+quotes) → обычный dict."""
    caplog.set_level(logging.DEBUG)
    clean = '# header comment\n\nA=1\nexport B=two words\n# mid comment\nC="quoted # hash"\nD=\n'
    path = _write_secrets(tmp_path, clean)

    result = parse(str(path), strict=True)

    assert result == {"A": "1", "B": "two words", "C": "quoted # hash", "D": ""}
    logger.info("[IMP:9][test][parser] strict passes clean file: %d entries", len(result))
