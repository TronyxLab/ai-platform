"""Audit-format detector — unified audit writer enforcement (DevPlan 163 W-C).

# GREP_SUMMARY: static audit-format unified-writer shared-audit-logger jsonl free-text-pipe direct-write audit-file U-10
# STRUCTURE: ▶ scan core/**/*.py → ○ regex-паттерны (open write / f.write / write_text + audit-файл;
#            f.write(f'[timestamp]-pipe) → ◇ файл == shared/audit_logger.py? → ⊕ Findings → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор единого audit-writer'а (DevPlan 163 W-C C1; порт
##           tests/gates/test_gate_audit_format.py, DevPlan 116 B11 T2, U-10/D1):
##           0 прямых open(..., "a"/"w")/f.write/write_text на audit-файлы
##           (audit.log/audit.jsonl) вне core/internal/shared/audit_logger.py (allowlist
##           ПУСТ — строгий); 0 free-text pipe-записей (f.write(f'[timestamp] ... | ...').
##           Находки — rule="audit-format" (blocking).
## @scope    Line-скан всех core/**/*.py, кроме shared/audit_logger.py (канонический
##           writer). Тесты вне скоупа (инвентарь тестов — другой гейт).
## @invariants
##   - Allowlist пуст (канон B8 D3 — строгий): любое прямое f.write на audit-файл → RED
##   - shared/audit_logger.py — единственный разрешённый writer (исключение скана)
##   - Паттерны: open(audit.*, write-режим), f.write + audit-файл, write_text + audit-файл,
##     f.write(f'[timestamp] — free-text pipe формат
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale U-10: 3 writer'а с разными форматами ломали observability. D1-консолидация
##            + детектор делают возврат прямых f.write невозможным в быстром слое.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created (порт R2-гейта)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

_SHARED_AUDIT_LOGGER = "core/internal/shared/audit_logger.py"

# Паттерны детекции (строгий allowlist — пуст)
_RE_AUDIT_FILE: re.Pattern[str] = re.compile(r"audit\.(log|jsonl)")
_RE_OPEN_WRITE: re.Pattern[str] = re.compile(
    r"open\s*\([^)]*(?:audit\.(?:log|jsonl)|AUDIT_LOG)[^)]*[\"'](?:a|w|a\+|w\+)[\"']"
)
_RE_FWRITE_AUDIT: re.Pattern[str] = re.compile(r"f\.write\s*\([^)]*(?:audit\.(?:log|jsonl)|AUDIT_LOG)")
_RE_WRITETEXT_AUDIT: re.Pattern[str] = re.compile(r"write_text\s*\([^)]*(?:audit\.(?:log|jsonl)|AUDIT_LOG)")
_RE_PIPE_FORMAT: re.Pattern[str] = re.compile(r"f\.write\s*\(f[\"']\[?\{?\s*ts")


# region FUNC_scan_file
def _scan_file(path: Path, root: Path, changed: set[str] | None) -> list[Finding]:
    """Line-скан одного .py файла на прямые audit-записи вне shared.

    ## @purpose  Правила R2: direct write (open/f.write/write_text + audit-файл) и
    ##           free-text pipe формат.
    ## @io       ⇥ path: Path, root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(L) — строки файла
    """
    rel = path.relative_to(root).as_posix()
    if rel == _SHARED_AUDIT_LOGGER:
        return []
    if changed is not None and rel not in changed:
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    findings: list[Finding] = []
    for lineno, line in enumerate(content.splitlines(), 1):
        if _RE_AUDIT_FILE.search(line) and (
            _RE_OPEN_WRITE.search(line) or _RE_FWRITE_AUDIT.search(line) or _RE_WRITETEXT_AUDIT.search(line)
        ):
            findings.append(_finding(rel, lineno, "direct audit-file write outside shared/audit_logger.py"))
        elif _RE_PIPE_FORMAT.search(line):
            findings.append(_finding(rel, lineno, "free-text pipe audit format (use shared JSONL writer)"))
    return findings


# endregion FUNC_scan_file


# region FUNC_finding
def _finding(file_rel: str, lineno: int, message: str) -> Finding:
    """Собрать Finding с логированием RED.

    ## @purpose  Единая точка создания находки audit-format (DRY внутри детектора).
    ## @io       ⇥ file_rel: str, lineno: int, message: str → ⎋ Finding
    ## @complexity  O(1)
    """
    logger.warning("[IMP:9][audit_format][RED] %s:%d %s", file_rel, lineno, message)
    return Finding(rule="audit-format", file=file_rel, line=lineno, message=message)


# endregion FUNC_finding


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти прямые audit-записи вне shared/audit_logger.py.

    # ▶ ┌core/**/*.py┐ → ○ line-scan (direct write + pipe) → ⊕ Findings → ⎋

    ## @purpose  Главный вход детектора (registry) — правило R2 (DevPlan 116 B11 T2).
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * L) — файлы × строки
    ## @invariants  Сканирует root/core/**/*.py (либо root/**/*.py для probe-деревьев);
    ##              shared/audit_logger.py исключается
    """
    core_dir = root / "core"
    scan_root = core_dir if core_dir.is_dir() else root
    files = sorted(p for p in scan_root.rglob("*.py") if "__pycache__" not in p.parts and p.is_file())
    findings: list[Finding] = []
    for path in files:
        findings.extend(_scan_file(path, root, changed))
    logger.info("[IMP:9][audit_format] Scanned %d file(s), findings=%d", len(files), len(findings))
    if not findings:
        logger.info("[IMP:9][audit_format] PASS: 0 direct audit writes outside shared/audit_logger.py")
    return findings


# endregion FUNC_detect
