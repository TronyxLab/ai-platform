#!/usr/bin/env python3
# GREP_SUMMARY: grepsummary, md-sh-refs, keywords, validator, scan-all, staged, strangler, git-ls-files
# STRUCTURE: ▶ collect_tracked_files (git ls-files | find) → ○ GREP_SUMMARY lines → ○ extract_keywords(scan) →
#            ◇ validate_keywords_present → ○ .md → extract_sh_refs(plain) → ◇ resolve_sh_ref_scan → ⎋ (errors, count)
# region MODULE_CONTRACT
## @purpose  Единый валидатор GREP_SUMMARY keywords + .sh-ссылок в .md (AC1 DevPlan 106).
##           Strangler-порт lint.sh check_grepsummary()/check_sh_refs_in_md(): два режима —
##           scan-all (git ls-files, ручной/CI) и staged (по файлам, pre-commit). GNU/BSD
##           grep-ветвление заменено единым Python lookbehind-паттерном (P6).
## @scope    Импортируемый API: extract_keywords, validate_keywords_present, extract_sh_refs,
##           resolve_sh_ref_scan, collect_tracked_files, scan_all. CLI: python3 -m ... scan-all → exit 0/1.
## @invariants
##   - Byte-в-байт парсинг keywords: scan-режим strip '#' → '-->' → '<!--', skip пустых и -* / --*;
##     staged-режим без strip и без skip flags (различия §2.3)
##   - Plain .sh-паттерн: (?<!\S)([\w./-]+\.sh)(?!\S) с re.ASCII — канонический GNU-паттерн (AC10)
##   - Skips scan-режима: ^http, ^/opt/, нет '/', содержит '..'
##   - Репозиторий вычисляется самодостаточно: Path(__file__).resolve().parents[3] (zero hardcoded paths)
## @rationale 474 LOC дублирования в entrypoints — крупнейший случай копипасты; манифест заявлял
##            замену без фактического переноса (P7). Python lookbehind портабелен на всех платформах
##            (включая macOS BSD) → BSD-fallback-ветка удалена (подмножество, AC10 не нарушается).
## @changes 2026-07-31 | Created (DevPlan 106 Strangler-Fig)
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("grepsummary_validator")

# ⚠️ TRAP[DECISION] · 2026-07-31 · — · GNU/BSD grep-унификация: единый lookbehind-паттерн в Python
# · Rejected: сохранение двух веток с capability probe (`grep -P ''`)
# · Reason: Python re имеет fixed-width lookbehind на всех платформах (включая macOS BSD);
# ·   GNU-паттерн — строгое подмножество BSD-fallback → меньше потенциальных ложных срабатываний,
# ·   AC10 не нарушается (все ранее проходившие файлы продолжают проходить).
# · Rev: если появится потребность в BSD-расширенном поведении (матч внутри слова) — вернуть
# ·   параметр loose_bsd_match: bool = False.

# 🧐 TRAP[BUG] · 2026-07-11 · SIGPIPE fix · (история из check-doc-headers.sh:63-66)
# · Reason: shell `grep -q` закрывал stdin → echo получал SIGPIPE (141) при pipefail.
# · Fix: grep читает файл напрямую. В Python pipes отсутствуют — прямой файловый I/O, race невозможен.

_GREP_SUMMARY_PREFIXES = ("# GREP_SUMMARY:", "<!-- GREP_SUMMARY:")
_PLAIN_SH_REF_RE = re.compile(r"(?<!\S)([\w./-]+\.sh)(?!\S)", re.ASCII)
_BACKTICK_SH_REF_RE = re.compile(r"`[^`]+\.sh`", re.ASCII)
_EXCLUDED_DIRS = {"node_modules", ".git", "__pycache__"}


# region FUNC__read_text
def _read_text(file: Path) -> str | None:
    """Read file as UTF-8 with replace-errors; None on OSError (binary/unreadable → skip).

    ▶ ┌file┐ → ○ read_text(utf-8, replace) → ◇ OSError? → ⎋ str | None
    """
    try:
        return file.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("[IMP:7][_read_text][warn] cannot read %s: %s", file, e)
        return None


# endregion FUNC__read_text


# region FUNC_extract_keywords
def extract_keywords(line: str, mode: str = "scan") -> list[str]:
    r"""Extract GREP_SUMMARY keywords from a summary line (порт sed 's/.*GREP_SUMMARY:\s*//' | tr ',' ' ').

    ▶ ┌line, mode┐ → ○ rfind marker → ○ replace ',' ' ' + split → ◇ scan: strip #/-->/<!-- + skip flags → ⎋ keywords

    ## @purpose — Парсинг keywords после последнего 'GREP_SUMMARY:' (байт-в-байт порт двух shell-реализаций).
    ## @io — ⇥ line: str — строка вида '# GREP_SUMMARY: a, b' или '<!-- GREP_SUMMARY: a -->'
    ##       ⇥ mode: 'scan' (strip HTML-маркеры + skip flags) | 'staged' (raw, без strip — §2.3)
    ##       → ⎋ list[str] — keywords (пустые удалены)
    ## @complexity — O(L) — однопроходный парсинг строки
    ## @invariants — scan: strip '#' → '-->' → '<!--' (порядок важен); skip '' и '-*' / '--*';
    ##               staged: маркер '# GREP_SUMMARY:' (с пробелом), без strip и без skip flags
    ## @rationale — lint.sh (scan) и check-doc-headers.sh (staged) используют РАЗНЫЙ парсинг (P1);
    ##               унификация сломала бы поведение — режимы сохраняются байт-в-байт.
    """
    marker = "# GREP_SUMMARY:" if mode == "staged" else "GREP_SUMMARY:"
    idx = line.rfind(marker)
    if idx == -1:
        logger.info("[IMP:6][extract_keywords][warn] marker '%s' not found in line", marker)
        return []
    tokens = line[idx + len(marker) :].replace(",", " ").split()
    if mode == "staged":
        logger.info("[IMP:6][extract_keywords][staged] %d raw keyword(s)", len(tokens))
        return tokens
    cleaned: list[str] = []
    for kw in tokens:
        kw = kw.replace("#", "").replace("-->", "").replace("<!--", "")
        if not kw or kw.startswith("-"):
            continue
        cleaned.append(kw)
    logger.info("[IMP:6][extract_keywords][scan] %d → %d keyword(s)", len(tokens), len(cleaned))
    return cleaned


# endregion FUNC_extract_keywords


# region FUNC_validate_keywords_present
def validate_keywords_present(file: Path, keywords: list[str]) -> list[str]:
    """Validate every keyword appears in file content (case-insensitive literal substring).

    ▶ ┌file, keywords┐ → ○ read text → ○ kw.lower() in text.lower() → ⊕ missing errors → ⎋ list[str]

    ## @purpose — Порт grep -qiF "$kw" "$file": case-insensitive fixed-string substring match.
    ## @io — ⇥ file: Path — файл для проверки; ⇥ keywords: list[str] → ⎋ list[str] — [FAIL]-ошибки
    ## @complexity — O(K * F) — K keywords, F размер файла
    ## @invariants — keyword найден при substring match (НЕ слово-граница); пустой keywords → pass
    ## @rationale — grep -qiF семантика: fixed-string, case-insensitive, substring (не word-match).
    """
    text = _read_text(file)
    if text is None:
        return []
    lowered = text.lower()
    errors: list[str] = []
    for kw in keywords:
        if kw.lower() not in lowered:
            msg = f"[FAIL] GREP_SUMMARY keyword '{kw}' not found in {file}"
            logger.info("[IMP:9][validate_keywords_present][fail] %s", msg)
            errors.append(msg)
    if not errors:
        logger.info(
            "[IMP:9][validate_keywords_present][pass] %d keyword(s) present in %s",
            len(keywords),
            file,
        )
    return errors


# endregion FUNC_validate_keywords_present


# region FUNC_extract_sh_refs
def extract_sh_refs(text: str, backtick_only: bool = False) -> list[str]:
    r"""Extract .sh references: plain GNU-pattern tokens or backtick-only refs.

    ▶ ┌text, backtick_only┐ → ◇ plain: (?<!\S)([\w./-]+\.sh)(?!\S) | backtick: `[^`]+\.sh` → strip ` → ⎋ refs

    ## @purpose — Единая экстракция .sh-ссылок (замена GNU/BSD grep-ветвления, P6).
    ## @io — ⇥ text: str; ⇥ backtick_only: bool — True = только `` `ref.sh` `` (sorted set);
    ##       False = plain-токены (порядок, дубликаты сохраняются) → ⎋ list[str] — ссылки без обрамления
    ## @complexity — O(T) — один regex-проход по тексту
    ## @invariants — plain: re.ASCII (\\w = [a-zA-Z0-9_]); backtick: strip всех '`' + sort -u
    ##               (порт tr -d '`' | sort -u)
    ## @rationale — lint.sh использует plain-токены, check-doc-headers.sh — только backtick (§2.3);
    ##               параметризация сохраняет оба поведения байт-в-байт.
    """
    if backtick_only:
        refs = sorted({m.replace("`", "") for m in _BACKTICK_SH_REF_RE.findall(text)})
        logger.info("[IMP:6][extract_sh_refs][backtick] %d unique ref(s)", len(refs))
        return refs
    refs = _PLAIN_SH_REF_RE.findall(text)
    logger.info("[IMP:6][extract_sh_refs][plain] %d ref(s)", len(refs))
    return refs


# endregion FUNC_extract_sh_refs


# region FUNC_resolve_sh_ref_scan
def resolve_sh_ref_scan(repo_root: Path, ref: str) -> bool:
    """Resolve plain-mode .sh ref: skip conditions + repo_root/ref OR cwd-relative ref.

    ▶ ┌repo_root, ref┐ → ◇ skips (^http | ^/opt/ | нет '/' | содержит '..') → ◇ (repo_root/ref).is_file() | Path(ref).is_file() → ⎋ bool

    ## @purpose — Порт check_sh_refs_in_md resolve-логики lint.sh:66-78.
    ## @io — ⇥ repo_root: Path — корень репозитория; ⇥ ref: str — .sh-ссылка → ⎋ bool (True = skip или файл существует)
    ## @complexity — O(1) — до двух проверок файловой системы
    ## @invariants — skip-условия возвращают True (не ошибка): ^http, ^/opt/, без '/', содержит '..'
    ## @rationale — shell резолвил $PLATFORM_ROOT/$ref ИЛИ $ref (cwd = PLATFORM_ROOT после cd фасада);
    ##               Python: repo_root/ref + cwd-fallback — идентично при фасадном cd.
    """
    if ref.startswith(("http", "/opt/")) or "/" not in ref or ".." in ref:
        logger.info("[IMP:6][resolve_sh_ref_scan][skip] ref='%s' (skip-условие)", ref)
        return True
    if (repo_root / ref).is_file() or Path(ref).is_file():
        logger.info("[IMP:6][resolve_sh_ref_scan][ok] ref='%s' resolves", ref)
        return True
    logger.info("[IMP:9][resolve_sh_ref_scan][fail] ref='%s' not found", ref)
    return False


# endregion FUNC_resolve_sh_ref_scan


# region FUNC_collect_tracked_files
def collect_tracked_files(repo_root: Path) -> list[Path]:
    """Collect tracked files: git ls-files in a git repo, find-fallback otherwise.

    ▶ ┌repo_root┐ → ◇ git rev-parse (git-dir?) → git ls-files | ⎋ else os.walk (exclude node_modules/.git/__pycache__/*.pyc)

    ## @purpose — Порт lint.sh:186-194 (git ls-files | find-fallback).
    ## @io — ⇥ repo_root: Path → ⎋ list[Path] — абсолютные пути к файлам
    ## @complexity — O(F) — F файлов в репозитории
    ## @invariants — find-fallback исключает node_modules/, .git/, __pycache__/, *.pyc
    ## @rationale — git ls-files — канонический список tracked-файлов (тот же, что у lint.sh);
    ##               вне git-репо (CI-артефакты, тесты tmp_path) — рекурсивный обход.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.info("[IMP:7][collect_tracked_files][warn] git ls-files failed: %s — find-fallback", e)
        proc = None
    if proc is not None and proc.returncode == 0:
        files = [repo_root / rel for rel in proc.stdout.splitlines() if rel.strip()]
        logger.info("[IMP:7][collect_tracked_files][git] %d tracked file(s)", len(files))
        return files
    files: list[Path] = []
    for root, dirs, names in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        for name in names:
            if name.endswith(".pyc"):
                continue
            files.append(Path(root) / name)
    files.sort()
    logger.info("[IMP:7][collect_tracked_files][find] %d file(s) found", len(files))
    return files


# endregion FUNC_collect_tracked_files


# region FUNC_scan_all
def scan_all(repo_root: Path) -> tuple[list[str], int]:
    """Full scan: GREP_SUMMARY keywords for all files + plain .sh refs for .md files.

    ▶ ┌repo_root┐ → ○ collect_tracked_files → ○ GREP_SUMMARY lines → ⊕ keyword errors → ○ .md → ⊕ sh-ref errors → ⎋ (errors, file_count)

    ## @purpose — Порт lint.sh grepsummary-режима (строки 196-209): полный прогон по tracked-файлам.
    ## @io — ⇥ repo_root: Path → ⎋ (list[str], int) — (ошибки, число просканированных файлов)
    ## @complexity — O(F * (K + T)) — F файлов, K keywords, T размер текста
    ## @invariants — GREP_SUMMARY строки: ^# GREP_SUMMARY: | ^<!-- GREP_SUMMARY: — все строки файла
    ##               (не только header); не-файлы пропускаются ([ ! -f ] && continue)
    ## @rationale — единая точка входа scan-all для ручного/CI использования (AC5): lint.sh делегирует.
    """
    logger.info("[IMP:7][scan_all][start] scanning repo_root=%s", repo_root)
    errors: list[str] = []
    file_count = 0
    for file in collect_tracked_files(repo_root):
        if not file.is_file():
            continue
        file_count += 1
        text = _read_text(file)
        if text is None:
            continue
        for line in text.splitlines():
            if line.startswith(_GREP_SUMMARY_PREFIXES):
                errors.extend(validate_keywords_present(file, extract_keywords(line, mode="scan")))
        if file.suffix == ".md":
            errors.extend(
                f"[FAIL] .sh reference '{ref}' in {file} -> file not found"
                for ref in extract_sh_refs(text, backtick_only=False)
                if not resolve_sh_ref_scan(repo_root, ref)
            )
    logger.info("[IMP:9][scan_all][result] %d file(s) scanned, %d error(s)", file_count, len(errors))
    return errors, file_count


# endregion FUNC_scan_all


# region FUNC__default_repo_root
def _default_repo_root() -> Path:
    """Resolve repo root: core/internal/lint/xx.py → parents[3] (zero hardcoded paths)."""
    return Path(__file__).resolve().parents[3]


# endregion FUNC__default_repo_root


# region FUNC_build_parser
def build_parser() -> argparse.ArgumentParser:
    """CLI parser: scan-all subcommand."""
    parser = argparse.ArgumentParser(description="GREP_SUMMARY keywords + .sh refs validator (DevPlan 106)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan-all", help="Scan all tracked files for GREP_SUMMARY + .sh refs")
    return parser


# endregion FUNC_build_parser


# region FUNC_main
def main() -> int:
    """CLI entry: scan-all → exit 0/1; [lint.sh] статусы на stdout, IMP-логи на stderr."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    args = build_parser().parse_args()
    repo_root = _default_repo_root()
    if args.command != "scan-all":
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 2
    errors, file_count = scan_all(repo_root)
    if errors:
        for err in errors:
            print(err)
        print(f"[lint.sh] FAILED — {len(errors)} error(s) found")
        logger.info("[IMP:9][lint][grepsummary] FAILED — %d error(s)", len(errors))
        return 1
    print(f"[lint.sh] PASS — all GREP_SUMMARY keywords verified, .sh references valid ({file_count} files)")
    logger.info("[IMP:9][lint][grepsummary] PASS")
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
