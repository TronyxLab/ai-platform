#!/usr/bin/env python3
# GREP_SUMMARY: preflight, deprecated, facade, check-suite, diagnostic-executor, legacy-CLI
# STRUCTURE: ▶ argparse (старые флаги) → ⊕ делегирование check_suite.run_diagnostic → ⎋ exit code
# region MODULE_CONTRACT
## @purpose  ТОНКИЙ DEPRECATED-фасад над core/internal/check_suite.py (DevPlan 120 §3.2, Wave 1).
##           Прежний preflight (3 фазы с hardcoded-списками проверок) ПОЛНОСТЬЮ вытеснен
##           единым SoT-манифестом core/check-suite.yaml; старые CLI-флаги сохраняются для
##           обратной совместимости (прецедент: compose-safe-up). Канонический таргет — `make check`.
## @scope    Только маппинг CLI: --skip-fix → --no-fix, --json, --workers, --verbose.
##           НИКАКОЙ бизнес-логики проверок здесь — она в check_suite.py + манифесте.
## @invariants
##   - Файл — фасад <150 LOC: парсинг флагов + один вызов check_suite.run_diagnostic
##   - Старые флаги работают без изменений (обратная совместимость CLI)
##   - Exit code = exit code check_suite (0 зелёный, 1 провалы, 2 конфигурация)
##   - `python3 -m core.internal.preflight` больше НЕ содержит hardcoded-списков проверок
## @rationale DevPlan 120: preflight-таргет удалён (DevPlan 138 W1) — модуль-фасад
##            сохранён для обратной совместимости CLI (python3 -m core.internal.preflight,
##            прецедент §6.3); нейминг-миграция (AC-5) переводит документацию на
##            `make check`/`make check-diff`; phantom-refs гейт банит make-литералы
##            удалённых таргетов (DevPlan 138).
## @changes 2026-08-02 | DevPlan 120 Wave 1 — переписан как тонкий фасад (был: 572 LOC
##           параллельного executor'а с 3 hardcoded-фазами)
# endregion MODULE_CONTRACT

from __future__ import annotations

# region IMPORTS
import argparse
import logging
import sys
from pathlib import Path

from core.internal.check_suite import run_diagnostic

# endregion IMPORTS

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_MAX_WORKERS = 6


# region FUNC_main
## @purpose  Deprecated-фасад CLI: прежние флаги (--json/--workers/--skip-fix/--verbose)
##           маппятся на diagnostic-executor check_suite (run --mode diagnostic).
## @io       ⇥ sys.argv → ⎋ int (0/1/2 — exit code check_suite)
## @complexity O(1) + время диагностического прогона
def main() -> int:
    """Deprecated facade CLI — maps legacy preflight flags onto the check-suite executor."""
    parser = argparse.ArgumentParser(
        description="DEPRECATED — use `make check` (core/check-suite.yaml executor). "
        "Facade сохраняет прежние флаги preflight.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON report.")
    parser.add_argument("--workers", type=int, default=_DEFAULT_MAX_WORKERS, help="Static-check workers.")
    parser.add_argument("--skip-fix", action="store_true", help="Skip fix phase (fix-gate + tier=fix).")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output for failed checks.")

    args = parser.parse_args()
    logger.info("[IMP:7][preflight][facade] deprecated facade → check_suite.run_diagnostic")
    print("[IMP:7][preflight] DEPRECATED — use `make check` (экс-preflight, DevPlan 120)", file=sys.stderr)
    return run_diagnostic(
        _PROJECT_ROOT,
        no_fix=args.skip_fix,
        json_output=args.json,
        workers=args.workers,
        no_cache=False,
        verbose=args.verbose,
    )


# endregion FUNC_main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
