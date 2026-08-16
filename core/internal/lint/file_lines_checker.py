#!/usr/bin/env python3
# GREP_SUMMARY: file-lines-checker line-limit scan wc-l warning non-blocking max-lines
# STRUCTURE: ▶ parse --max-lines/--core-dir → ○ rglob files (py/sh/yml/yaml/json/md) → ◇ count \n → ○ WARNING if >limit → ⎋ exit 0 (non-blocking)
# region MODULE_CONTRACT
## @purpose  Python-порт check-file-lines.sh (DevPlan 173 W2.2): сканирует core/ на файлы,
##           превышающие лимит строк (default 500) — non-blocking warning (всегда exit 0).
## @scope    Вызывается из core/entrypoints/check-file-lines.sh (суит check-file-lines
##           check-suite.yaml, План 175 W2.1). Сканирует *.py/*.sh/*.yml/*.yaml/*.json/*.md,
##           исключая .venv/, node_modules/, __pycache__/.
## @invariants
##   - Всегда exit 0 — non-blocking по DevPlan 030 AC5
##   - Default max-lines = 500; --max-lines N переопределяет
##   - Счёт строк = количество `\n` (byte-parity с `wc -l`)
##   - Исключения: .venv/, node_modules/, __pycache__/
## @rationale Языковая политика: find-скан + wc-цикл (shell) → Python rglob. Предоставляет
##            видимость роста файлов без блокировки CI.
## @changes  2026-08-16 | DevPlan 173 W2.2 — Created (порт check-file-lines.sh)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger(__name__)

# Repo root = core/internal/lint/../.. = core; корень скана — <repo>/core (PATHS_CORE_DIR канон)
_CORE_DIR = Path(__file__).resolve().parent.parent.parent
_SCAN_EXTENSIONS = (".py", ".sh", ".yml", ".yaml", ".json", ".md")
_EXCLUDED_DIRS = (".venv", "node_modules", "__pycache__")
_DEFAULT_MAX_LINES = 500


# region FUNC_count_lines
def count_lines(path: Path) -> int:
    """Количество `\n` в файле (byte-parity с `wc -l < file`).

    ## @purpose — Ровно семантика wc -l: счёт символов перевода строки.
    ## @io — ⇥ path: Path → ⎋ int
    ## @complexity — O(N) — однопроходное чтение
    """
    return path.read_text(encoding="utf-8", errors="replace").count("\n")


# endregion FUNC_count_lines


# region FUNC_discover_files
def discover_files(core_dir: Path) -> list[Path]:
    """Детерминированный список сканируемых файлов (rglob + сортировка + исключения).

    ## @purpose — Порт find -type f (…) -not -path (…) из check-file-lines.sh.
    ## @io — ⇥ core_dir: Path → ⎋ list[Path] (sorted, только target-расширения)
    ## @complexity — O(F) — rglob дерева
    ## @invariants — расширения _SCAN_EXTENSIONS; исключены _EXCLUDED_DIRS; sorted(key=str)
    """
    files: list[Path] = []
    for path in core_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(excluded in path.parts for excluded in _EXCLUDED_DIRS):
            continue
        if path.suffix in _SCAN_EXTENSIONS:
            files.append(path)
    return sorted(files, key=str)


# endregion FUNC_discover_files


# region FUNC_scan
## @purpose  Прогнать скан: для каждого файла count_lines → WARNING при >max_lines.
## @io       ⇥ core_dir: Path, max_lines: int, count_fn: Callable | None (DI) → ⎋ int (warning count)
## @complexity O(F) — F = файлов
def scan(core_dir: Path, max_lines: int, *, count_fn: Callable[[Path], int] | None = None) -> int:
    """Scan files; returns number of files exceeding max_lines (non-blocking)."""
    counter = count_lines if count_fn is None else count_fn
    warning_count = 0
    for path in discover_files(core_dir):
        try:
            line_count = counter(path)
        except OSError:
            # Файл исчез/недоступен (xdist-race) — пропуск, не роняем non-blocking контракт
            continue
        if line_count > max_lines:
            logger.warning("[IMP:8][check-file-lines][WARNING] %s: %d lines (max %d)", path, line_count, max_lines)
            warning_count += 1
    return warning_count


# endregion FUNC_scan


# region FUNC_main
class _CliArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace-атрибуты Any → ClassVar-аннотации БЕЗ значений)."""

    max_lines: ClassVar[int]
    core_dir: ClassVar[str]


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: `file-lines [--max-lines N]` — всегда exit 0 (non-blocking)."""
    parser = argparse.ArgumentParser(description="Scan files exceeding line limit (non-blocking)")
    parser.add_argument("--max-lines", type=int, default=_DEFAULT_MAX_LINES, help="Line limit (default 500)")
    parser.add_argument("--core-dir", default=str(_CORE_DIR), help="Scan root (default: <repo>/core)")
    args = parser.parse_args(argv, namespace=_CliArgs())

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    max_lines = args.max_lines
    core_dir = Path(args.core_dir)

    if max_lines < 1:
        logger.error("[IMP:9][check-file-lines] ERROR: --max-lines requires a positive integer")
        return 1

    logger.info("[IMP:7][check-file-lines] Scanning files exceeding %d lines...", max_lines)

    warning_count = scan(core_dir, max_lines)

    if warning_count > 0:
        logger.info(
            "[IMP:9][check-file-lines] %d file(s) exceed %d-line limit (non-blocking warning)",
            warning_count,
            max_lines,
        )
    else:
        logger.info("[IMP:9][check-file-lines] All files within %d-line limit", max_lines)

    logger.info("[IMP:9][check-file-lines] Check complete (exit 0 — non-blocking)")
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
