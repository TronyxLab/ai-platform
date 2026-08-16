#!/usr/bin/env python3
# GREP_SUMMARY: doxygen_checker, doxygen-check, zero-warnings, Doxyfile, CI-gate, graceful-skip, direct-call
# STRUCTURE: ▶ resolve doxygen (skip-if-absent) → ◇ run doxygen Doxyfile → ◇ count 'warning:' → ⊕ exit 0|1
# region MODULE_CONTRACT
## @purpose  Doxygen zero-warnings CI-гейт (DevPlan 097): запускает doxygen Doxyfile,
##           считает строки 'warning:' и роняет exit 1 при предупреждениях или
##           ненулевом exit doxygen. Прямой вызов из core/check-suite.yaml (суит
##           doxygen-check, План 175 W2.1) — заменяет make-таргет doxygen-check.
## @scope    Standalone-скрипт (как dead_code_checker.py): stdlib-only, запускается ФАЙЛОМ.
## @invariants
##   - doxygen отсутствует → WARN + exit 0 (graceful degradation; инвариант enforce-ится
##     на хостах, где doxygen есть — паритет прежнего make-таргета)
##   - exit doxygen ≠ 0 → FAIL (exit 1)
##   - count 'warning:' > 0 → FAIL (exit 1) с числом предупреждений
##   - Временный лог — tmp-файл, удаляется (rm -f) в конце
## @rationale make-таргет doxygen-check (multi-line shell в ci.mk) → Python-порт:
##            языковая политика (бизнес-логика = Python), суиты вызывают инструменты напрямую.
## @changes  2026-08-16 | Created (План 175 W2.1 — doxygen-check → прямой вызов)
# endregion MODULE_CONTRACT

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# region CHECK_LOGIC


def run_doxygen_check() -> int:
    """Run doxygen Doxyfile, count warnings; return 0 (pass) or 1 (fail/skip semantics).

    ## @purpose  Ядро гейта: doxygen absent → 0 (skip); exit≠0 или warnings>0 → 1.
    ## @io       → ⎋ 0/1
    ## @complexity O(1) + время doxygen
    ## @invariants — временный лог в tmp, безусловно удаляется
    """
    if shutil.which("doxygen") is None:
        print(
            "[IMP:7][doxygen-check] doxygen not installed — SKIP (zero-warnings invariant not enforceable on this host)",
            file=sys.stderr,
        )
        return 0

    print("[IMP:7][doxygen-check] Running doxygen Doxyfile (zero-warnings invariant)...", file=sys.stderr)

    # Подготовка каталогов (паритет прежнего make-таргета: .doxygen/.docs/{html,xml}).
    Path(".doxygen/.docs/html").mkdir(parents=True, exist_ok=True)
    Path(".doxygen/.docs/xml").mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w+", suffix=".log", encoding="utf-8", delete=False) as log_fh:
        log_path = log_fh.name
    try:
        with Path(log_path).open("w", encoding="utf-8") as log:
            proc = subprocess.run(["doxygen", "Doxyfile"], stdout=log, stderr=subprocess.STDOUT, check=False)
        count = Path(log_path).read_text(encoding="utf-8", errors="replace").count("warning:")
        if proc.returncode != 0:
            print(f"[IMP:9][doxygen-check] FAIL: doxygen exited {proc.returncode}", file=sys.stderr)
            return 1
        if count != 0:
            print(
                f"[IMP:9][doxygen-check] FAIL: {count} doxygen warning(s) found — DevPlan 097 zero-warnings invariant violated",
                file=sys.stderr,
            )
            return 1
        print("[IMP:9][doxygen-check] PASS: 0 doxygen warnings", file=sys.stderr)
        return 0
    finally:
        Path(log_path).unlink(missing_ok=True)


# endregion CHECK_LOGIC


# region CLI


def main() -> int:
    """CLI: run doxygen check → exit 0/1."""
    return run_doxygen_check()


if __name__ == "__main__":
    sys.exit(main())


# endregion CLI
