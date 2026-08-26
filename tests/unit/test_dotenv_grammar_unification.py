# GREP_SUMMARY: dotenv-grammar-unification quoted-hash identical parsers env-reader secrets-env-parser export-shell AI-0055
# STRUCTURE: ▶ матрица строк (quoted-#, export, пустые) → ◇ get_env_value == parse()[K] == extract(export_shell) → ⎋ одна грамматика
# region MODULE_CONTRACT
## @purpose  AI-0055 (DevPlan 17 T5.5): одна dotenv-грамматика на все пути чтения —
##           env_reader.get_env_value, secrets_env_parser.parse/export_shell и
##           decrypt_secrets._yaml_to_env разбирают одну строку ИДЕНТИЧНО
##           (кавычки снимаются, unquoted-# режется, export-prefix поддержан).
## @scope    tests/unit: characterization-матрица над tmp_path; без subprocess.
## @invariants
##   - 'FOO="bar #x"' → bar #x у всех трёх путей (кавычка защищает #)
##   - 'FOO=bar #x' → bar (unquoted-# начинает комментарий)
##   - decrypt_secrets._yaml_to_env('FOO: "bar #x"') → FOO='bar #x'
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.secrets.decrypt_secrets import _yaml_to_env
from core.internal.shared import env_reader, secrets_env_parser

logger = logging.getLogger(__name__)

# Матрица characterization: (строка, ожидаемое значение FOO)
_CASES: list[tuple[str, str]] = [
    ('FOO="bar #x"', "bar #x"),  # кавычка защищает #
    ("FOO='bar #x'", "bar #x"),
    ("FOO=bar #x", "bar"),  # unquoted-# — комментарий
    ("FOO=bar", "bar"),
    ("export FOO=via_export", "via_export"),
    ("FOO=", ""),
]

_YAML_CASES: list[tuple[str, str]] = [
    ('FOO: "bar #x"', "bar #x"),
    ("FOO: bar # comment", "bar"),
    ("FOO: plain", "plain"),
]


def _write(tmp_path: Path, content: str) -> str:
    f = tmp_path / "secrets.env"
    f.write_text(content + "\n", encoding="utf-8")
    return str(f)


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · одна грамматика разбора env-строк (AI-0055)
# · Regression: три грамматики расходились — env_reader возвращал RAW со кавычками
#   (FOO="bar #x" → '"bar #x"'), parser срезал кавычки/комментарии, _yaml_to_env имел
#   свою re-имплементацию quote-strip — один файл читался по-разному разными путями
# · Scenario: для каждой строки матрицы: get_env_value == parse()[FOO] ==
#   значение из export_shell; yaml-ветка совпадает для key:value входа
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0055)
# · Remove if: env-грамматика переезжает в stdlib-парсер с одним ридером
@pytest.mark.parametrize(("line", "expected"), _CASES)
def test_quoted_hash_identical_across_parsers(tmp_path: Path, line: str, expected: str) -> None:
    """Одна строка → одно значение во всех трёх путях."""
    path = _write(tmp_path, line)

    via_reader = env_reader.get_env_value(Path(path), "FOO")
    via_parse = secrets_env_parser.parse(path).get("FOO")
    shell_out = secrets_env_parser.export_shell(path)
    via_export = ""
    for out_line in shell_out.splitlines():
        parsed = secrets_env_parser.parse_line(out_line.removeprefix("export "))
        if parsed is not None and parsed[0] == "FOO":
            via_export = parsed[1]

    print(f"[IMP:8][grammar] {line!r}: reader={via_reader!r} parse={via_parse!r} export={via_export!r}")
    assert via_reader == expected, f"env_reader: {line!r} → {via_reader!r}, ожидалось {expected!r}"
    assert via_parse == expected, f"parse(): {line!r} → {via_parse!r}, ожидалось {expected!r}"
    assert via_export == expected, f"export_shell: {line!r} → {via_export!r}, ожидалось {expected!r}"
    logger.info("[IMP:8][test] grammar identical for %r", line)


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · yaml-ветка через канон-грамматику (AI-0055)
# · Regression: _yaml_to_env держал собственный quote-strip — расхождение с каноном
# · Scenario: 'FOO: "bar #x"' → FOO='bar #x'; 'FOO: bar # comment' → FOO='bar';
#   многострочный вход обрабатывается построчно, итог заканчивается \n
# · Last fail: охранник миграции T5.5 (DevPlan 17)
# · Remove if: SOPS-decrypt переходит на прямой YAML-дамп без env-конвертации
@pytest.mark.parametrize(("yaml_line", "expected"), _YAML_CASES)
def test_yaml_branch_uses_canon_grammar(yaml_line: str, expected: str) -> None:
    out = _yaml_to_env(yaml_line)
    print(f"[IMP:8][grammar-yaml] {yaml_line!r} → {out!r}")
    assert out == f"FOO='{expected}'\n"


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · многострочность и last-match семантика
# · Regression: env_reader документирует «последнее вхождение» — унификация обязана
#   сохранить это (dict-перезапись в parse() даёт то же)
# · Scenario: дублирующийся ключ — побеждает последний; комментарии/пустые пропущены
# · Last fail: контрсценарий-охранник T5.5 (DevPlan 17)
# · Remove if: вместе с test_quoted_hash_identical_across_parsers
def test_last_match_and_multiline(tmp_path: Path) -> None:
    content = (
        "# header comment\n"
        "\n"
        'FOO="first"\n'
        "BAR=x\n"
        "FOO=second # wins"
    )
    path = _write(tmp_path, content)
    assert env_reader.get_env_value(Path(path), "FOO") == "second"
    assert secrets_env_parser.parse(path)["FOO"] == "second"
    assert secrets_env_parser.parse(path)["BAR"] == "x"
    logger.critical("[IMP:9][test] last-match semantics preserved across paths — OK (AI-0055)")
