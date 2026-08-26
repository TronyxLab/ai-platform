#!/usr/bin/env python3
# GREP_SUMMARY: generated-check, check-generated, full-diff, unified-diff, drift-diagnostics, manifests, CI-selfdiagnostics
# STRUCTURE: ▶ ┌path+content┐ → ◇ file exists? → ⎋ 1 │ ◇ byte-equal? → ⎋ 0 │ ⊕ ПОЛНЫЙ unified_diff → stderr → ⎋ 1
# region MODULE_CONTRACT
## @purpose  Единый хелпер --check-режима генераторов манифестов (AI-0063, DevPlan 17 T2.3):
##           сравнивает сгенерированный контент с файлом на диске и при расхождении печатает
##           ПОЛНЫЙ unified diff в stderr (P-14: первые 20 строк скрывали источник divergence).
## @scope    Все генераторы GENERATED-артефактов (secrets-manifest, platform-env, .env.example,
##           requirements.txt, AGENTS.md, entrypoint-manifest, litellm-config).
## @invariants
##   - rc: 0 = match, 1 = divergence/missing file (exit-code семантика --check сохранена)
##   - Печать diff ТОЛЬКО при расхождении; файл никогда не пишется
##   - Полный diff БЕЗ обрезки (DIFF_LINES_MAX больше не режет диагностику)
##   - Читает файл как текст UTF-8
## @rationale Q: почему один хелпер? A: 7 копий diff-диагностики дрейфовали — P-14 (полный diff)
##            был применён только в entrypoint-manifest; остальные 6 сайтов резали [:20] строк,
##            что делало RED check-manifests неотладибельным в CI.
## @changes  2026-08-26 | DevPlan 17 T2.3 — создан; мигрированы 7 сайтов
# endregion MODULE_CONTRACT

from __future__ import annotations

import difflib
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["check_generated"]


# region FUNC_check_generated
def check_generated(path: str | Path, content: str, *, to_label: str = "generated") -> int:
    """Compare generated ``content`` against the file at ``path`` (byte-level).

    ▶ ┌content + path┐ → ◇ is_file()? → ⎋ 1 (loud) │ ◇ equal? → ⎋ 0 │ ⊕ FULL unified_diff → stderr → ⎋ 1

    ## @purpose  Единственная точка diff-диагностики для --check генераторов (AI-0063):
    ##            полная печать расхождения вместо среза первых 20 строк.
    ## @io       ⇥ path: существующий файл, content: сгенерированная строка,
    ##              to_label: подпись generated-стороны (default «generated»)
    ##           → ⎋ int: 0=match, 1=diverges или файл отсутствует
    ## @complexity O(N) где N = размер файлов
    ## @invariants
    ##   - Никогда не пишет на диск (чистая диагностика)
    ##   - Diff печатается в stderr целиком (без [:20]-среза)
    ##   - Отсутствие файла — loud IMP:1 ошибка + rc 1 (не молчаливый pass)
    """
    file_path = Path(path)
    logger.info("[IMP:7][check_generated][START] Checking against %s", file_path)

    if not file_path.is_file():
        logger.error("[IMP:1][check_generated][MISSING] File not found: %s", file_path)
        print(f"[IMP:1][check_generated] File not found: {file_path} — cannot check", file=sys.stderr)
        return 1

    existing = file_path.read_text(encoding="utf-8")
    if content == existing:
        logger.info("[IMP:9][check_generated][OK] Content matches %s", file_path)
        return 0

    logger.warning("[IMP:6][check_generated][DIVERGE] Content differs from %s", file_path)
    print(f"[IMP:6][check_generated] Divergence in {file_path.name}:", file=sys.stderr)
    diff_lines = list(
        difflib.unified_diff(
            existing.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"{file_path.name} (file)",
            tofile=to_label,
        )
    )
    # P-14: полный diff — CI-самодиагностика; обрезка скрывала источник расхождения
    for line in diff_lines:
        print(line, end="", file=sys.stderr)
    if not diff_lines:
        # байт-различие без построчного diff (например, trailing newline) — сообщим явно
        print(
            f"[IMP:6][check_generated] Files differ without line-level diff "
            f"(trailing whitespace/newline?) — {file_path.name}",
            file=sys.stderr,
        )
    return 1


# endregion FUNC_check_generated
