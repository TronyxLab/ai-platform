"""agent_check — L1-статический сигнал агента на изменённых файлах (DevPlan 163 W-E).

# GREP_SUMMARY: agent-check l1-signal agent-loop ruff basedpyright static-cli advisory bespoke doc-headers fp-registry json exit-code make-target DevPlan-163
# STRUCTURE: ▶ git changed (py/sh/mk) → ⊕ ruff blocking (current select) → ⊕ ruff advisory (SLF/FBT/ARG/C90 × fp-verdict)
#            → ⊕ basedpyright file-mode (--level error) → ⊕ static check --changed (W-C subprocess)
#            → ⊕ bespoke doc-headers (py+sh) → ⊕ dedupe → ⊕ json|human report (stdout) → ⎋ exit 0/1
"""
# region MODULE_CONTRACT
## @purpose  Первый sub-5s детерминированный сигнал для agent-loop (метрика DevPlan 163 §10:
##           637 s static_audit → <5 s). `python3 -m core.internal.agent_check [--json]` —
##           прогон всех быстрых статических гарантий по ИЗМЕНЁННЫМ файлам
##           (git diff --name-only HEAD + untracked): ruff blocking (текущий ruff.toml),
##           advisory-правила (SLF/FBT/ARG/C90, вердикт из fp_registry.yaml), basedpyright
##           (файловый режим, --level error), static check --changed (W-C пакет), bespoke
##           doc-headers. exit 0 = чисто; exit 1 = blocking-нарушения; advisory не блокирует.
## @scope    Фасад пакета + CLI main (T3.2): реэкспорт публичного API (исполнение — в runners.py,
##           данные/типы — в types.py) и argparse-обвязка `[--json] [--root]`. Логи [IMP:*]
##           идут ТОЛЬКО в stderr; stdout — чистый отчёт (JSON с --json, иначе human) —
##           машиночитаемая конвенция.
## @invariants
##   - stdout: ровно один отчёт (JSON при --json, иначе human); логи — только stderr
##   - exit 0 = нет blocking-находок; exit 1 = есть blocking; advisory/off — не влияют
##   - Пустой diff / changed без .py/.sh → exit 0 (allow_no_tests-семантика T1.3)
##   - git-сбой → WARNING + пустой changed-набор (не блокирует сигнал; полную гарантию
##     дают make check / pre-commit / gate — L2+ слои)
##   - Вердикт advisory-правил — из core/internal/agent_check/fp_registry.yaml
##     (verdict: blocking | advisory | off); файл отсутствует → все advisory (WARNING)
##   - Прогон только по changed (N файлов = N-список в одном вызове тула)
##   - Дедупликация находок по (rule, file, line, message) — правило, ставшее blocking
##     в fp_registry, не дублируется между ruff-шагом и advisory-шагом
##   - DI-HYG (DevPlan 163 §5): env-чтения ТОЛЬКО через параметр environ — никаких
##     скрытых os.environ внутри функций
##   - Инфраструктурный сбой шага (тул не найден / не-JSON вывод) → видимая находка
##     rule="agent-check-infra" severity=error (fail-visible, конституция §4)
## @rationale Инверсия слоёв (01-Brief §2): статика жила в медленном pytest-слое (~3-5 мин),
##            агент ждал 637 s до первого сигнала. agent-check консолидирует быстрые
##            гарантии (ruff changed + basedpyright changed + static changed + bespoke)
##            в один вызов <5 s — L1 агент-цикла (L0 редактор → L1 agent-check →
##            L2 pre-commit → gate/CI). Каждый шаг независим и измеряется (duration_ms) —
##            телеметрия agent-loop (G4.2, files/agent_loop_metrics.jsonl).
##            name-linter-семантика (make-таргеты) НЕ дублируется: статический детектор
##            verb-register (W-C) покрывает Makefile↔manifest parity — agent-check
##            выполняет его через static check --changed.
##            yaml в импортах — существующая проектная зависимость (pyyaml>=6.0,
##            прецедент core/internal/static/*): W-E не вводит НИ ОДНОЙ новой зависимости.
##            T3.2 (1787342045763): декомпозиция 1092 LOC (прецедент check_suite W3) —
##            данные → types.py, исполнение → runners.py; __init__ сохраняет CLI+facade
##            (паритет check_suite/verify_sweep __init__); публичный контракт и
##            `python -m core.internal.agent_check` без изменений. Реэкспортируются ТОЛЬКО
##            публичные имена (детектор static private-imports; приватные хелперы остаются
##            в home-модулях runners.py/types.py — путь core.internal.agent_check.runners._X).
## @changes 2026-08-13 | DevPlan 163 W-E E1-E4 — Created
## @changes 2026-08-22 | T3.2 (1787342045763) — декомпозиция: типы → types.py, исполнение →
##           runners.py; __init__ = фасад (re-exports публичных имён) + CLI main + human-отчёт
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-08-15 · — · agent_check.py → пакет agent_check/__init__.py (170 W10-C)
# · Rejected: оставить namespace-пакет agent_check/ (fp_registry.yaml как data-папка БЕЗ __init__.py)
# · Reason: коллизия файл+пакет (agent_check.py + agent_check/) снята переносом модуля в __init__.py;
# ·   `python3 -m core.internal.agent_check` идёт через пакетный __main__.py (делегат на main()),
# ·   импорты `from core.internal import agent_check` и `core.internal.agent_check` — на пакет;
# ·   fp_registry.yaml остаётся data-файлом рядом с __init__.py (_FP_REGISTRY_REL неизменен);
# ·   внутренности НЕ декомпозированы (перенос без изменений логики).
# · Rev: если fp_registry перерастёт в код (бизнес-логика вердиктов) — декомпозиция пакета
# ·   на подмодули (следующий план, вне скоупа W10-C).

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from core.internal.agent_check.types import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    AdvisoryEntryDict,
    CheckEntryDict,
)

logger = logging.getLogger(__name__)

# Корень репозитория: core/internal/agent_check/__init__.py → parents[0]=agent_check,
# [1]=internal, [2]=core, [3]=repo. БЛИЗНЕЦ _REPO_ROOT в runners.py (run_static cwd) — та же
# формула parents[3]; дублирование тривиальной константы вместо приватного межмодульного
# импорта (детектор static private-imports запрещает from-import _имени).
_REPO_ROOT = Path(__file__).resolve().parents[3]

# region RE_EXPORTS
# Публичный контракт пакета (path-preserving, T3.2): `from core.internal.agent_check import X`
# работает для каждого ПУБЛИЧНОГО имени монолита __init__.py. Исполнение — в runners.py,
# данные — в types.py; F401 для __init__ игнорируется (ruff.toml "**/__init__.py" =
# unused-import) — реэкспорты валидны без __all__. Приватные хелперы монолита (перечислены в
# MODULE_CONTRACT @rationale) остаются в home-модулях: core.internal.agent_check.runners._X.
from core.internal.agent_check.runners import (
    check_doc_headers,
    load_fp_registry,
    run,
    run_basedpyright,
    run_ruff,
    run_static,
)
from core.internal.agent_check.types import (
    VERDICTS,
    AgentCheckReport,
    AgentFinding,
    AgentFindingDict,
    BasedpyrightDiagDict,
    BasedpyrightOutputDict,
    ChangedFiles,
    ChangedFilesDict,
    ChecksDict,
    FpRegistryDict,
    FpRegistryEntryDict,
    RuffItemDict,
    StaticFindingDict,
    StaticOutputDict,
    SummaryDict,
)

# endregion RE_EXPORTS


# region CLI


# region FUNC__human_report
def _human_report(report: AgentCheckReport) -> str:
    """Человекочитаемый отчёт (stdout при отсутствии --json).

    # ▶ ┌report┐ → ○ headline (PASS/FAIL) → ○ секции checks → ○ blocking findings → ○ summary → ⎋ str

    ## @purpose  Быстрый взгляд агента: PASS/FAIL + количество по шагам + список blocking.
    ##           Живёт в CLI-слое (__init__.py) — единственный потребитель main(); T3.2.
    ## @io       ⇥ report: dict[str, Any] → ⎋ str
    ## @complexity  O(N)
    """
    summary = report["summary"]
    changed = report["changed"]
    head = "PASS" if summary["clean"] else "FAIL"
    summary_line = (
        f"agent-check: {head} — {summary['blocking']} blocking / {summary['advisory']} advisory "
        f"on {changed['total']} changed file(s) [{summary['duration_ms']:.0f} ms]"
    )
    lines = [summary_line]
    for name, entry in report["checks"].items():
        if name == "advisory":
            # W11-G4: checks-секция — TypedDict-union; advisory-ветка сужается cast'ом
            adv = cast(AdvisoryEntryDict, entry)
            lines.append(f"  advisory:      info ({len(adv['findings'])}) verdicts={adv['verdicts']}")
        else:
            ce = cast(CheckEntryDict, entry)
            lines.append(f"  {name:<14} {ce['status'].upper():<4} ({len(ce['findings'])})")
    findings = report["findings"]
    if findings:
        lines.append("findings:")
        for f in findings:
            sev = f["severity"]
            fix = " [autofix]" if f["fixable"] else ""
            location = f["file"] if f["line"] <= 0 else f"{f['file']}:{f['line']}"
            lines.append(f"  {location} [{f['rule']}]{fix} ({sev}/{f['source']}) {f['message']}")
    lines.append(
        f"summary: blocking={summary['blocking']} advisory={summary['advisory']} total={summary['total']} clean={summary['clean']}"
    )
    return "\n".join(lines)


# endregion FUNC__human_report


# region FUNC_main
def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """CLI-точка входа: `python3 -m core.internal.agent_check [--json] [--root <path>]`.

    # ▶ parse_args → ○ run(root, environ, json_mode) → ◇ json_mode? json | human → ⎋ exit code

    ## @purpose  Связать argparse → run → отчёт → exit code. Логи — stderr, отчёт — stdout
    ##           (машиночитаемая конвенция). Возвращает int (тестируемо без sys.exit).
    ## @io       ⇥ argv: list[str] | None, environ: Mapping[str, str] | None → ⎋ int
    ## @complexity  ∑ run
    ## @invariants  DI: environ читается ровно один раз (os.environ) в main — ниже только
    ##              параметр; sys.exit — только в __main__ блоке (exit-code-контракт core)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if environ is None:
        environ = dict(os.environ)

    parser = argparse.ArgumentParser(
        prog="python3 -m core.internal.agent_check",
        description="L1-статический сигнал агента (<5 s) на изменённых файлах (DevPlan 163 W-E)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON report on stdout (agent-check contract T3.1)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_REPO_ROOT,
        help=f"Repository root (default: {_REPO_ROOT})",
    )
    args = parser.parse_args(argv)
    # W11-G4: argparse Namespace — нетипизированные атрибуты → cast на границе CLI
    root: Path = cast(Path, args.root).resolve()
    json_flag = cast(bool, args.json)
    logger.info("[IMP:8][cli] agent-check root=%s json=%s", root, json_flag)
    exit_code, report = run(root, environ)
    output = json.dumps(report, ensure_ascii=False, indent=2) if json_flag else _human_report(report)
    print(output)
    logger.info("[IMP:9][cli] exit=%d summary=%s", exit_code, report["summary"])
    return exit_code


# endregion FUNC_main

# endregion CLI


if __name__ == "__main__":
    sys.exit(main())
