#!/usr/bin/env python3
# GREP_SUMMARY: dead-code, DEPRECATED, git-blame, committer-time, mtime, age, whole-word, lint
# STRUCTURE: ▶ walk root → ⊕ filter (*.sh/*.py, excl) → ○ grep \bDEPRECATED\b → ○ git blame -L → ◇ age>30 ? → ⊕ report → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  CI gate: detect DEPRECATED markers older than 30 days in project .sh/.py files.
##           Strangler-порт core/entrypoints/check-dead-code.sh (86 LOC) → pure-stdlib Python CLI
##           (DevPlan 109). Байт-идентичный вывод и exit-code к оригиналу (parity P1-P12).
## @scope    Импортируемый API: find_marker_files, find_deprecated_lines, get_line_add_timestamp,
##           compute_age_days, check_dead_code, _print_report, main. CLI: python3 -m ... → exit 0/1.
##           Только stdlib: argparse, os, re, subprocess, pathlib, dataclasses, sys, logging, time.
## @invariants
##   - Marker match: re \bDEPRECATED\b — whole-word (compound _DEPRECATED_PATTERNS НЕ матчится, P1)
##   - Exclusions: .venv/.git/.ai root-level only (P3), node_modules any depth (P4),
##     SELF_EXCLUSIONS — 3 файла (фасад + модуль + unit-тест, D3 — самозащита от self-flagging)
##   - Age source: git blame -L L,L --porcelain → committer-time (P6); пусто/ошибка → os.path.getmtime (P7/D9)
##   - Возраст: (now - ts) // 86400; violation iff age_days > threshold (СТРОГО больше, P8)
##   - Output: stdout [IMP:7]/[IMP:10] per-marker (P9/P10), stderr control [IMP:8]/[IMP:9]/[IMP:10] (P11)
##   - Exit: 0 clean / 1 violations — passthrough через shell-фасад (P12)
## @rationale Dead code misleads agents — они читают его как source of truth (RC-4).
##            Git-blame porcelain в awk/grep хрупок и нечитаем; Python subprocess + regex надёжен (R1).
##            Фасад сохраняет путь ⇒ zero ripple на Makefile/manifest/AGENTS.md/contract/gate тесты (R3).
## @changes 2026-07-31 | Created (DevPlan 109 Strangler-Fig)
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("dead_code_checker")
logger.setLevel(logging.DEBUG)

# Порог по умолчанию (дней). Совпадает с константой оригинала (30).
THRESHOLD_DAYS = 30
# Whole-word DEPRECATED — эквивалент grep -w "DEPRECATED" (P1): \b не матчится внутри _DEPRECATED_PATTERNS.
DEPRECATED_RE = re.compile(r"\bDEPRECATED\b")
# D3: модуль, его фасад и unit-тест содержат литерал "DEPRECATED" по назначению —
# без исключения checker флагал бы собственную реализацию после 30 дней (self-referential trap).
SELF_EXCLUSIONS = frozenset(
    {
        "core/entrypoints/check-dead-code.sh",
        "core/internal/lint/dead_code_checker.py",
        "tests/unit/test_dead_code_checker.py",
    }
)
# Root-level только (P3-асимметрия): startswith("<dir>/") — как -not -path "$ROOT/.venv/*".
EXCLUDE_ROOT = (".venv", ".git", ".ai")
# Any depth (P4): "node_modules" in rel.parts — как -not -path "*/node_modules/*".
EXCLUDE_ANY = ("node_modules",)
# Таймаут одного git blame subprocess — git absence/зависание не должны ронять gate (D7/D9).
_BLAME_TIMEOUT_SECONDS = 30

# 🧐 TRAP[DECISION] · 2026-07-31 · — · Per-line git blame -L L,L (parity) вместо whole-file batching
# · Rejected: git blame --porcelain <file> однократно → построение line→committer-time карты
# · Reason: D6 — per-line идентичен оригиналу; 18 hits = 18 subprocess ≈ negligible. Whole-file
# ·   batching усложнил бы разбор porcelain-диапазонов без текущей выгоды.
# · Rev: если DEPRECATED-маркеров станет >200 — перейти на whole-file blame batching.

# 🧐 TRAP[DECISION] · 2026-07-31 · — · propagate=True + per-call stderr handler вместо D8-буквального propagate=False
# · Rejected: модульный StreamHandler с propagate=False (D8 literal)
# · Reason: pytest caplog вешает handler на ROOT logger — записи propagate=False логгеров НЕ доходят
# ·   до caplog (эмпирически проверено). D8-схема сломала бы требование плана "control messages
# ·   captured by caplog in unit tests (LDD telemetry)". propagate=True + per-call StreamHandler
# ·   (%(message)s, attach/remove в main()) даёт byte-identical stderr БЕЗ двойного вывода.
# · Rev: если pytest добавит caplog-capture для propagate=False логгеров — вернуть propagate=False.


# region CLASS_DeadCodeViolation
@dataclass
class DeadCodeViolation:
    """Typed stale-marker record between checker and reporter.

    ▶ ┌rel_path, line_num, age_days, line_text┐ → ⎋ violation

    ## @purpose — typed контракт: stale DEPRECATED marker (age_days > threshold_days).
    ## @io — rel_path: str (repo-relative путь), line_num: int (1-based), age_days: int, line_text: str
    ## @complexity — O(1)
    ## @invariants — age_days > threshold_days по построению (фильтр в check_dead_code, P8)
    ## @rationale — явный dataclass вместо кортежей: самодокументируемый контракт checker→reporter.
    """

    rel_path: str
    line_num: int
    age_days: int
    line_text: str


# endregion CLASS_DeadCodeViolation


# region FUNC_find_marker_files
def find_marker_files(project_root: Path | str, self_exclusions: frozenset[str]) -> list[Path]:
    """Walk root, collect *.sh/*.py, apply exclusions (P2-P5).

    ▶ walk root (os.walk, readdir order = find parity) → ⊕ filter .sh/.py → ◇ root-excl / any-excl / self → ⎋ candidates

    ## @purpose — порт find-обхода оригинала: сбор .sh/.py с фильтрами P3 (.venv/.git/.ai root),
    ##             P4 (node_modules any depth), P5 (self_exclusions).
    ## @io — ⇥ project_root: корень сканирования; ⇥ self_exclusions: rel-пути самозащиты (D3)
    ##       → ⎋ list[Path] — файлы-кандидаты (readdir order, как find на той же ФС)
    ## @complexity — O(F) — F файлов под корнем
    ## @invariants — root-исключения ТОЛЬКО на верхнем уровне (rel.startswith("x/")); node_modules —
    ##               любой глубины; prune dirs = оптимизация, результат идентичен rel-фильтрам
    ## @rationale — readdir order (без sort) воспроизводит порядок find на той же файловой системе,
    ##               сохраняя byte-identical порядок вывода оригинала (AC3).
    """
    root = Path(project_root).resolve()
    candidates: list[Path] = []
    for walk_root, dirs, names in os.walk(root):
        if walk_root == str(root):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_ROOT and d not in EXCLUDE_ANY]
        else:
            dirs[:] = [d for d in dirs if d not in EXCLUDE_ANY]
        for name in names:
            path = Path(walk_root) / name
            if path.suffix not in (".sh", ".py"):
                continue
            rel = os.path.relpath(path, root)
            if any(rel.startswith(f"{d}/") for d in EXCLUDE_ROOT):
                continue
            if "node_modules" in Path(rel).parts:
                continue
            if rel in self_exclusions:
                continue
            candidates.append(path)
    logger.debug("[IMP:7][find_marker_files][scan] %d candidate file(s)", len(candidates))
    return candidates


# endregion FUNC_find_marker_files


# region FUNC_find_deprecated_lines
def find_deprecated_lines(path: Path | str) -> list[tuple[int, str]]:
    """Whole-word DEPRECATED scan per file → [(line_num, line_text)].

    ▶ ┌path┐ → ○ read_text(errors=replace) → ○ enumerate + re \bDEPRECATED\b → ⊕ (line_num, rstrip) → ⎋ hits

    ## @purpose — порт grep -wn "DEPRECATED": whole-word match; возвращается полный текст строки
    ##             (эквивалент cut -d: -f2- — строка целиком после первого двоеточия, P9).
    ## @io — ⇥ path: файл → ⎋ list[tuple[int, str]] — (1-based line_num, полный текст строки)
    ## @complexity — O(L) — L строк файла
    ## @invariants — \bDEPRECATED\b: compound _DEPRECATED_PATTERNS НЕ матчится (P1);
    ##               read_text(errors="replace") — UTF-8/emoji устойчивость (риск-строка 303);
    ##               OSError (битый/удалённый файл) → skip с IMP:6
    ## @rationale — re \b == grep -w (P1); enumerate сохраняет номера строк как grep -n.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("[IMP:6][find_deprecated_lines][skip] cannot read %s: %s", path, e)
        return []
    hits: list[tuple[int, str]] = []
    for line_num, line in enumerate(text.splitlines(), start=1):
        if DEPRECATED_RE.search(line):
            hits.append((line_num, line.rstrip("\n")))
    logger.debug("[IMP:7][find_deprecated_lines][scan] %s: %d hit(s)", path, len(hits))
    return hits


# endregion FUNC_find_deprecated_lines


# ⚠️ TRAP[BUG] · 2026-07-31 · P2 · Original check-dead-code.sh: SIGPIPE под pipefail → silent stat-fallback
# · Symptom: parity-сравнение pre/post capture показало расхождение возрастов (state_machine 0d→1d,
# ·   test_gate_manifest_integrity 8d→14d) для файлов с большой историей коммитов.
# · Root: pre-filter `git log --oneline -- rel | head -1 | grep -q .` — head -1 закрывает pipe раньше,
# ·   git log получает SIGPIPE (141) → pipefail → if-условие FALSE → ветка blame пропускалась →
# ·   fallback на stat mtime. Детерминизм зависел от размера вывода git log (pipe buffer 64KB).
# · Fix: pre-filter удалён (DevPlan D7) — blame вызывается напрямую; пусто/ошибка → mtime. Возраст
# ·   теперь корректен (committer-time для tracked-файлов всегда доступен).
# · Prevention: никогда не пиши `cmd | head -1` в if-условии под `set -o pipefail` (known flaky pattern).


# region FUNC_get_line_add_timestamp
def get_line_add_timestamp(project_root: Path | str, rel: str, line_num: int, mtime: int) -> int:
    """git blame -L L,L --porcelain → committer-time epoch; fallback mtime (D7/D9).

    ▶ ┌root, rel, line, mtime┐ → ○ subprocess git blame → ○ regex ^committer-time → ◇ пусто/ошибка? → mtime → ⎋ epoch

    ## @purpose — порт P6/P7: age source = git blame committer-time; empty/error → os.path.getmtime fallback.
    ## @io — ⇥ project_root: git -C root; ⇥ rel: repo-relative путь; ⇥ line_num: 1-based; ⇥ mtime: готовый fallback
    ##       → ⎋ int — epoch seconds
    ## @complexity — O(1) — один subprocess + один regex
    ## @invariants — FileNotFoundError/TimeoutExpired/rc!=0/пустой stdout/нет заголовка → mtime
    ##               (graceful degradation — отсутствие git не роняет gate, D7); fallback mtime —
    ##               порт BSD stat -f "%m" на stdlib os.path.getmtime (D9, Linux-портируемость)
    ## @rationale — porcelain-поле committer-time — тот же источник, что у оригинала (P6 parity);
    ##               git log pre-filter отброшен (D7) — blame на untracked сам возвращает пусто → mtime.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "blame", "-L", f"{line_num},{line_num}", "--porcelain", rel],
            capture_output=True,
            text=True,
            timeout=_BLAME_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("[IMP:7][get_line_add_timestamp][fallback] git blame unavailable (%s) — mtime", e)
        return mtime
    if proc.returncode != 0 or not proc.stdout.strip():
        logger.debug("[IMP:7][get_line_add_timestamp][fallback] blame rc=%d stdout empty — mtime", proc.returncode)
        return mtime
    match = re.search(r"^committer-time (\d+)", proc.stdout, flags=re.MULTILINE)
    if match is None:
        logger.debug("[IMP:7][get_line_add_timestamp][fallback] no committer-time header — mtime")
        return mtime
    epoch = int(match.group(1))
    logger.debug("[IMP:7][get_line_add_timestamp][blame] %s:%d → committer-time %d", rel, line_num, epoch)
    return epoch


# endregion FUNC_get_line_add_timestamp


# region FUNC_compute_age_days
def compute_age_days(timestamp: int, now: int) -> int:
    """(now - ts) // 86400 — integer floor days.

    ▶ ┌ts, now┐ → ○ (now - ts) // 86400 → ⎋ days

    ## @purpose — порт $(( (now - ts) / 86400 )) (P8): integer floor; violation iff > threshold (strict).
    ## @io — ⇥ timestamp: epoch seconds; ⇥ now: int(time.time()) → ⎋ int — полные дни
    ## @complexity — O(1)
    ## @invariants — floor division; negative (clock skew) → отрицательное значение (никогда не violation)
    ## @rationale — P8 parity: bash integer division == Python // для положительных значений.
    """
    return (now - timestamp) // 86400


# endregion FUNC_compute_age_days


# region FUNC_check_dead_code
def check_dead_code(project_root: Path | str, threshold_days: int = THRESHOLD_DAYS) -> list[DeadCodeViolation]:
    """Orchestrator: scan → blame → age → filter; per-marker stdout (P9/P10), violations → return.

    ▶ scan-start (stderr) → find_marker_files → ○ per file: find_deprecated_lines → ○ per hit: blame/mtime →
    ◇ age > T ? → ⊕ violation + STALE | print OK → ⎋ list[DeadCodeViolation]

    ## @purpose — главный конвейер: печатает per-marker строки байт-в-байт как оригинальный while-loop
    ##             и собирает список нарушений (контракт для _print_report и main).
    ## @io — ⇥ project_root; ⇥ threshold_days: default 30 → ⎋ list[DeadCodeViolation] — stale-нарушения
    ## @complexity — O(F * L + H) — F файлов, L строк, H hits (per-hit git blame subprocess)
    ## @invariants — violation iff age_days > threshold_days (СТРОГО, P8); OK/STALE печатаются inline
    ##               в порядке обхода (интерливинг как в оригинале, AC3); unreadable/deleted file → skip;
    ##               stderr-телеметрия на DEBUG — CLI-вывод остаётся byte-identical (P11)
    ## @rationale — inline-печать в порядке скана воспроизводит оригинальный loop (interleaving);
    ##               return list[DeadCodeViolation] — тип, проверяемый unit-тестами (§8 TEST_SPEC).
    """
    logger.info("[IMP:8][check-dead-code] Scanning for DEPRECATED markers in .sh and .py files...")
    root = Path(project_root).resolve()
    now = int(time.time())
    violations: list[DeadCodeViolation] = []
    ok_count = 0
    for file in find_marker_files(root, SELF_EXCLUSIONS):
        rel = os.path.relpath(file, root)
        try:
            mtime = int(os.path.getmtime(file))
        except OSError as e:
            logger.debug("[IMP:6][check_dead_code][skip] cannot stat %s: %s", file, e)
            continue
        for line_num, line_text in find_deprecated_lines(file):
            ts = get_line_add_timestamp(root, rel, line_num, mtime)
            age_days = compute_age_days(ts, now)
            if age_days > threshold_days:
                violations.append(DeadCodeViolation(rel, line_num, age_days, line_text))
                print(
                    f"[IMP:10][check-dead-code] STALE: {rel}:{line_num} — "
                    f"marker is {age_days} days old (threshold: {threshold_days})"
                )
                print(f"  >>> {line_text[:120]}")
            else:
                ok_count += 1
                print(
                    f"[IMP:7][check-dead-code] OK: {rel}:{line_num} — "
                    f"marker is {age_days}d old (within {threshold_days}d grace)"
                )
    logger.debug("[IMP:9][check_dead_code][summary] %d OK marker(s), %d violation(s)", ok_count, len(violations))
    return violations


# endregion FUNC_check_dead_code


# region FUNC__print_report
def _print_report(violations: list[DeadCodeViolation], threshold_days: int) -> None:
    """Control lines to stderr: FAIL + Fix hint | PASS (P11 byte-identical).

    ▶ ┌violations, T┐ → ◇ len>0 ? → stderr FAIL + Fix | → stderr PASS → ⎋ None

    ## @purpose — финальный вердикт gate на stderr (logging → stderr handler, caplog-совместим).
    ## @io — ⇥ violations; ⇥ threshold_days → ⎋ None (side-effect: stderr через модульный logger)
    ## @complexity — O(V) — V violations
    ## @invariants — FAIL: "[IMP:10]... FAIL: {n} marker(s) exceed {T}-day grace period" + Fix hint;
    ##               PASS: "[IMP:9]... PASS: All DEPRECATED markers are within {T}-day grace period"
    ## @rationale — control в stderr через модульный logger (D8): byte-identical P11 и caplog-capture
    ##               для LDD-телеметрии (propagate=True — см. TRAP[DECISION] у логгера).
    """
    if violations:
        logger.info(
            "[IMP:10][check-dead-code] FAIL: %d marker(s) exceed %d-day grace period",
            len(violations),
            threshold_days,
        )
        logger.info("[IMP:10][check-dead-code] Fix: remove stale markers or update if still active")
    else:
        logger.info(
            "[IMP:9][check-dead-code] PASS: All DEPRECATED markers are within %d-day grace period",
            threshold_days,
        )


# endregion FUNC__print_report


# region FUNC__attach_stderr_handler
def _attach_stderr_handler() -> logging.StreamHandler:
    """Create a stderr StreamHandler bound to the CURRENT sys.stderr (%(message)s).

    ▶ ┌─┐ → ⊕ StreamHandler(sys.stderr, %(message)s, level INFO) → ⎋ handler

    ## @purpose — D8 stderr-routing: handler на время main(), формат %(message)s (byte-identical P11).
    ## @io — → ⎋ logging.StreamHandler — привязан к текущему sys.stderr
    ## @complexity — O(1)
    ## @invariants — handler создаётся per-call и привязывается к ТЕКУЩЕМУ sys.stderr; pytest capsys
    ##               подменяет sys.stderr per-test и закрывает его после — persistent handler держал бы
    ##               ссылку на закрытый поток (ValueError: I/O operation on closed file);
    ##               attach/remove в main() (try/finally) устраняет закрытый поток
    ## @rationale — per-call handler безопасен под capsys; propagate остаётся True → caplog-capture
    ##               (см. TRAP[DECISION]).
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.debug("[IMP:7][_attach_stderr_handler][add] stderr StreamHandler attached")
    return handler


# endregion FUNC__attach_stderr_handler


# region FUNC__default_project_root
def _default_project_root() -> Path:
    """Resolve repo root from module location (zero hardcoded paths)."""
    return Path(__file__).resolve().parents[3]


# endregion FUNC__default_project_root


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """argparse CLI: --threshold (default 30), --help; exit 0|1.

    ▶ ┌argv┐ → ○ argparse → ○ _attach_stderr_handler → ○ check_dead_code → ○ _print_report → ◇ violations? → ⎋ 0|1

    ## @purpose — точка входа фасада (python3 dead_code_checker.py "$@"); exit-code passthrough (P12).
    ## @io — ⇥ argv: None → sys.argv[1:] → ⎋ int — 0 clean / 1 violations
    ## @complexity — O(total scan)
    ## @invariants — --help → argparse SystemExit(0) с usage (contract help-smoke V6);
    ##               return 1 iff violations; root = _default_project_root() (детерминировано, без hardcode);
    ##               stderr handler attach/remove в try/finally — no leak под pytest capsys
    ## @rationale — argparse даёт --help exit 0 + usage (V6) и --threshold для детерминированных
    ##               unit-тестов (D5) без манипуляции mtime.
    """
    parser = argparse.ArgumentParser(
        prog="dead_code_checker.py",
        description="Detect DEPRECATED markers older than N days in .sh/.py files (DevPlan 109)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=THRESHOLD_DAYS,
        help=f"grace period in days (default: {THRESHOLD_DAYS})",
    )
    args = parser.parse_args(argv)
    handler = _attach_stderr_handler()
    try:
        violations = check_dead_code(_default_project_root(), args.threshold)
        _print_report(violations, args.threshold)
        return 1 if violations else 0
    finally:
        logger.removeHandler(handler)


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
