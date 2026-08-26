#!/usr/bin/env python3
# GREP_SUMMARY: env-reader, shared, dotenv, env-file, get-value, last-match, make-facade, W2.3
# STRUCTURE: ▶ get_env_value(file, var_name) → ◇ CLI get VAR [--file .env] → ⎋ value | "" (exit 0 always, missing → empty)
# region MODULE_CONTRACT
## @purpose  Чтение значений из .env-файла для make-рецептов (DevPlan 172 W2.3).
##           Заменяет 4 inline-реализации `grep -E '^VAR=' file | tail -n1 | cut -d= -f2-`
##           в makefiles/helpers.mk (dev-certs/dev-metrics/provision-llm) и
##           makefiles/dev.mk (dev-hosts) — бизнес-логика чтения env вынесена из make
##           в Python (языковая политика: shell — тонкий фасад).
## @scope    make-рецепты dev-целей; модуль работает на dev-машине (macOS, $(PYTHON) venv)
##           и на ноде (python3, /opt/platform/core) — stdlib-only, без внешних зависимостей.
## @invariants
##   1. Семантика совпадает с shell-оригиналом: LAST match по строкам `VAR=...` побеждает
##      (grep + tail -n1), значение = всё после ПЕРВОГО `=` (cut -d= -f2- — знаки `=`
##      в значении не режутся).
##   2. Отсутствие файла/переменной → пустой вывод и exit 0 (НЕ ошибка): make-цепочки
##      полагаются на пустое значение для fallback `$${VAR:-default}`.
##   3. Строки с `export `, пустые строки и комментарии (#) пропускаются.
##   4. Никаких env-чтений на import-time; модуль — leaf (не импортирует bootstrap/deploy).
##   5. Значение печатается без трактовки — секреты не логируются (вывод идёт только
##      в командную подстановку make, IMP:8-лог содержит имя переменной, не значение).
## @rationale Языковая политика AGENTS.md: inline-логика в shell — сигнал к извлечению.
##            4 копии одного grep/cut-паттерна дрейфуют при любой правке формата .env;
##            единый Python-модуль даёт 1 точку семантики + unit-тест.
## @changes  2026-08-15 | DevPlan 172 W2.3 — Created
## @usecases
##   - make dev-metrics → env_reader get STATUS_METRICS_JSON --file .env
##   - make dev-hosts → env_reader get NODE_NAME --file .env (пусто → default)
## @links    CONSUMERS(makefiles/helpers.mk, makefiles/dev.mk), RELATED(core/internal/shared/env_requires.py)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from core.internal.shared import secrets_env_parser

logger = logging.getLogger(__name__)

# ⚠️ TRAP[DECISION] · 2026-08-15 · — · Missing var/file → пустой вывод, exit 0 (не 2)
# · Rejected: exit 1/2 на отсутствие переменной (fail-fast argparse-стиль)
# · Reason: make-цепочки строят fallback как `$${VAR:-$$(env_reader get VAR ...)}` —
# ·   пустое значение — легитимный сигнал «нет в .env», make подставляет default.
# ·   Ошибка на отсутствие сломала бы все 4 dev-цепи (dev-certs/dev-metrics/dev-hosts/
# ·   provision-llm) без выигрыша: обязательность проверяют recipe-level :? checks.
# · Rev: если появится потребитель, которому нужен строгий режим — добавить --required.

# AI-0055 (DevPlan 17 T5.5): локальная _LINE_RE удалена — канон
# secrets_env_parser.parse_line (кавычки + unquoted-# + export-prefix).
# lenient-фасад: отсутствующий файл/переменная → "" (TRAP[DECISION] ниже),
# strict не пробрасывается — потребители make-цепей ждут пустую подстановку.
_parse_line = secrets_env_parser.parse_line


# region FUNC_get_env_value
## @purpose  Прочитать значение переменной из env-файла (последнее вхождение).
## @io       ⇥ env_file: Path, var_name: str
##           ⎋ str — значение ("" если файл/переменная не найдены)
## @complexity 1 — линейный проход по файлу
def get_env_value(env_file: Path, var_name: str) -> str:
    """Last-match value of `var_name` in env_file; "" when missing (shell grep/tail/cut parity)."""
    try:
        lines = _read_lines(env_file)
    except (OSError, UnicodeDecodeError):
        # Оригинал: `grep ... 2>/dev/null` — отсутствующий/битый файл = «нет значения»
        return ""
    result = ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = _parse_line(line)
        if parsed is not None and parsed[0] == var_name:
            result = parsed[1]
    return result


def _read_lines(env_file: Path) -> list[str]:
    """Прочитать строки env-файла (I/O изолирован от парсинга)."""
    with env_file.open(encoding="utf-8") as f:
        return f.readlines()


# endregion FUNC_get_env_value


# region DATACLASS_CliArgs
@dataclass
class _CliArgs:
    """Типизированная граница argparse.Namespace CLI (W11, DevPlan 170)."""

    var_name: str
    file: Path


# endregion DATACLASS_CliArgs


# region FUNC_build_parser
def build_parser() -> argparse.ArgumentParser:
    """Argparse CLI: `env_reader get VAR [--file .env]`."""
    parser = argparse.ArgumentParser(
        prog="env_reader",
        description="Read a value from an env file (last match wins, empty output when missing).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    get_cmd = sub.add_parser("get", help="Print value of VAR from env file (empty when missing)")
    get_cmd.add_argument("var_name", metavar="VAR", help="Variable name to read")
    get_cmd.add_argument("--file", default=".env", help="Env file path (default: .env)")
    return parser


# endregion FUNC_build_parser


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entry: get — печать значения (exit 0 всегда)."""
    args_ns = build_parser().parse_args(argv)
    args = _CliArgs(var_name=cast(str, args_ns.var_name), file=Path(cast(str, args_ns.file)))
    value = get_env_value(args.file, args.var_name)
    if value:
        # Без trailing newline не печатаем НИЧЕГО (пустой вывод — контракт fallback-цепей)
        sys.stdout.write(value + "\n")
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    raise SystemExit(main())
