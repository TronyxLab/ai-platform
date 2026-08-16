"""
# GREP_SUMMARY: check-suite, diagnostic, run-diagnostic, fix-phase, static-checks, pytest-checks, replay, cache-write, fix-gate
# STRUCTURE: ▶ run_diagnostic ┌manifest→validate┐ → ○ run_fix_phase ┌fix-gate + tier=fix, retry-once┐ → ◇ fingerprint (после fix) → ◇ maybe_replay_cached ┌fp+green?┐ → ∥ run_static_checks ┌ThreadPool + sequential┐ → → run_pytest_checks ┌run_pytest_check, строго последовательно┐ → format_report → write_cache → ⎋ 0|1|2
# region MODULE_CONTRACT
## @purpose  Диагностический executor пакета check_suite (`make check`, DevPlan 170 W3 —
##           извлечено из монолита core/internal/check_suite.py): fix-фаза → fingerprint →
##           кэш (replay зелёного прогона) → static-чеки в потоках + pytest-чеки
##           последовательно → отчёт → запись кэша. Кэш только здесь; gate/diff — без кэша.
## @scope    core/internal/check_suite/diagnostic.py — stdlib-only. Потребитель: __init__.py (CLI run).
## @invariants
##   - fingerprint ПОСЛЕ fix-фазы (мутация автоправкой не ломает replay)
##   - Replay только при fingerprint-совпадении И status=green; упавший прогон никогда не реплеится
##   - pytest-чеки строго последовательно (решение b); static-чеки параллельно (workers)
##   - --no-cache / CHECK_CACHE=0 → без чтения и записи кэша
##   - fix-фаза: fix-gate pre-step + tier=fix ПОСЛЕДОВАТЕЛЬНО; fix-gate провал → return 1
## @rationale Извлечение run_diagnostic из монолита: maybe_replay_cached/run_static_checks/
##            run_pytest_checks/write_cache — механическое разделение одного тела (research-A §1);
##            pytest-цикл дедуплицирован в runner.run_pytest_check (семантика 1:1).
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package); 170 private-imports:
##           приватные имена переименованы в публичные (U-07)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import sys
import time
from pathlib import Path

from core.internal import check_suite as cs
from core.internal.check_suite.fingerprint import compute_fingerprint, load_cache, save_cache
from core.internal.check_suite.manifest import Manifest, list_checks, validate_manifest
from core.internal.check_suite.models import CheckOutcome, CheckSpec
from core.internal.check_suite.report import CheckPayload, CheckReportDict, format_report
from core.internal.check_suite.runner import run_pytest_check, run_retry_once

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 6

# ⚠️ TRAP[DECISION] · 2026-08-02 · — · fix-gate — built-in pre-step диагностики, НЕ запись манифеста
# · Rejected: добавить fix-gate как tier=fix запись в check-suite.yaml
# · Reason: DevPlan 120 §3.1 манифест содержит только pre-commit в tier=fix; fix-gate (мутирующая
#   автофаза) — преемник Phase 1, выполняется ДО чеков манифеста и ДО
#   fingerprint (иначе автоправка ломала бы replay). Состав проверок (диагностика) — манифест;
#   автофикс-фаза — оркестрационная преамбула executor'а, не «проверка».
# · Rev: если появятся дополнительные fix-фазы (>1) — перенести их в манифест как tier=fix записи.
_FIX_GATE_PRE_STEP = "make fix-gate"


# region RUN_DIAGNOSTIC


# region FUNC_run_fix_phase
## @purpose  Диагностическая fix-фаза (Phase 1+2): fix-gate pre-step + tier=fix
##           чеки манифеста ПОСЛЕДОВАТЕЛЬНО. pre-commit имеет retry-once (автоправка гигиены).
##           fix-gate провал → остальные фазы не запускаются (среда не чиста).
## @io       ⇥ manifest: dict, root: Path, env: dict → (list[CheckOutcome], bool)
##           (результаты, fix_ok)
## @complexity O(F) где F = fix-чеки
def _run_fix_phase(manifest: Manifest, root: Path, env: dict[str, str]) -> tuple[list[CheckOutcome], bool]:
    """Run the sequential auto-fix phase: fix-gate pre-step + tier=fix manifest checks."""
    results: list[CheckOutcome] = []
    print("[IMP:7][check] Fix phase: make fix-gate (auto-fix, timeout=120s)...", file=sys.stderr)
    fix_gate = cs.run_cmd(_FIX_GATE_PRE_STEP, 120, env, root)  # late-binding: DI-HYG
    results.append(fix_gate)
    if not fix_gate.passed:
        print(f"[IMP:9][check] fix-gate FAILED (exit {fix_gate.exit_code})", file=sys.stderr)
        return results, False

    for spec in list_checks(manifest, gate_mode=None):
        if spec.tier != "fix":
            continue
        cmd_str = spec.resolve_command(gate_mode=None)
        if not cmd_str:
            continue
        print(f"[IMP:7][check] Fix phase: {spec.id} (tier=fix, timeout={spec.timeout}s)...", file=sys.stderr)
        r = cs.run_cmd(cmd_str, spec.timeout, env, root)  # late-binding: DI-HYG
        # Retry-once: pre-commit автоправляет гигиену (trailing-whitespace, end-of-file-fixer)
        r = run_retry_once(
            r,
            cmd_str,
            spec.timeout,
            env,
            root,
            retry_condition=lambda _out: True,
            retry_message=f"[IMP:8][check] {spec.id} had issues — re-running to apply auto-fixes...",
            mark_auto_fixed=True,
            keep_first_on_failure=True,
        )
        results.append(r)
    return results, True


# endregion FUNC_run_fix_phase


# region FUNC_maybe_replay_cached
## @purpose  Попытка replay зелёного прогона из кэша (извлечено из run_diagnostic, DevPlan 170 W3):
##           fingerprint-совпадение И status=green → печать отчёта (или JSON) → True.
## @io       ⇥ fp: str | None, cache_file: Path | None, json_output: bool → bool (replayed)
## @complexity O(1) — чтение одного JSON
def _maybe_replay_cached(fp: str | None, cache_file: Path | None, json_output: bool) -> bool:
    """Attempt cache replay; returns True when the run is replaced by a cached green report."""
    if fp is None or cache_file is None:
        return False
    cached = load_cache(cache_file)
    if not (cached and cached.get("fingerprint") == fp and cached.get("status") == "green"):
        return False
    print("[IMP:7][check] Fingerprint совпал — replay зелёного прогона (кэш)", file=sys.stderr)
    if json_output and isinstance(cached.get("checks"), list):
        # W11-G4: кэш — CheckCacheDict (total=False, ключи опциональны); checks — guarded isinstance
        cached_checks: list[CheckPayload] = cached.get("checks") or []
        print(
            json.dumps(
                {
                    "status": "green",
                    "replayed": True,
                    **{
                        k: cached[k]  # pyright: ignore[reportTypedDictNotRequiredAccess] W11-G4: k — строка из фиксированного набора, guarded `if k in cached`
                        for k in ("total_checks", "passed", "auto_fixed", "failed", "duration_ms")
                        if k in cached
                    },
                    "checks": cached_checks,
                },
                indent=2,
            )
        )
    else:
        print(cached.get("report", "(кэш без отчёта)"))
    return True


# endregion FUNC_maybe_replay_cached


# region FUNC_run_static_checks
## @purpose  Static-фаза диагностики (извлечено из run_diagnostic, DevPlan 170 W3):
##           параллельные (ThreadPoolExecutor, workers) + sequential чеки ПОСЛЕ
##           параллельной фазы (doxygen-флак — см. TRAP). Тайминги печатаются до запуска.
## @io       ⇥ static_checks: list[CheckSpec], env: dict, root: Path, workers: int
##           → ⎋ list[CheckOutcome]
## @complexity O(C * t) где C = чеки, t = время исполнения
# ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — doxygen-check флакал в параллели
# · Symptom: make check/gate периодически падали «46 doxygen warning(s)» (unexpanded alias),
# ·   standalone doxygen = 0 warning'ов. Поймано в check: doxygen в ThreadPoolExecutor
# ·   параллельно с static_audit (pytest, 300s) — pytest мутирует tests/ (__pycache__,
# ·   report-файлы) пока doxygen парсит → lexer doxygen 1.17.0 (flex push-back overflow,
# ·   TRAP Doxyfile:53) → «Found unexpanded alias» в последующих docstring'ах.
# · Fix: sequential:true чеки (doxygen-check) исполняются ПОСЛЕ параллельной static-фазы,
# ·   до pytest-фазы. Плюс unique log в ci.mk (коллизия /tmp/doxygen-check.log при двух gate).
# · Rev: если doxygen обновится (flex-fix) — sequential можно снять.
def _run_static_checks(
    static_checks: list[CheckSpec],
    env: dict[str, str],
    root: Path,
    workers: int,
) -> list[CheckOutcome]:
    """Run parallel (threads) + sequential static checks; returns outcomes in completion order."""
    static_parallel = [s for s in static_checks if not s.sequential and s.resolve_command(None)]
    static_sequential = [s for s in static_checks if s.sequential and s.resolve_command(None)]
    static_results: list[CheckOutcome] = []
    # Тайминги параллельной static-фазы печатаются ОДНОЙ строкой до запуска (DevPlan 157 W1 T2)
    print(
        "[IMP:7][check] static steps: "
        + ", ".join(f"{s.id}(timeout={s.timeout}s)" for s in static_parallel)
        + " (параллельно)",
        file=sys.stderr,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(cs.run_cmd, s.resolve_command(None) or "", s.timeout, env, root): s.id
            for s in static_parallel
        }
        for future in concurrent.futures.as_completed(futures):
            cid = futures[future]
            try:
                outcome = future.result()
                static_results.append(outcome)
                print(f"[IMP:8][check] done {cid} ({outcome.duration_ms:.0f} ms)", file=sys.stderr)
            # ruff: ignore[BLE001] — thread-pool future.result: любой сбой воркера → CheckOutcome, не краш
            except Exception as exc:  # noqa: EXC — best-effort thread-pool wrapper, must not crash
                static_results.append(CheckOutcome(name=cid, exit_code=1, stderr=f"Internal error: {exc}"))
                print(f"[IMP:8][check] done {cid} (0 ms, internal error)", file=sys.stderr)
    for spec in static_sequential:
        print(
            f"[IMP:7][check] {spec.id} (sequential, timeout={spec.timeout}s, после параллельной static-фазы)...",
            file=sys.stderr,
        )
        outcome = cs.run_cmd(spec.resolve_command(None) or "", spec.timeout, env, root)
        static_results.append(outcome)
        print(f"[IMP:8][check] done {spec.id} ({outcome.duration_ms:.0f} ms)", file=sys.stderr)
    return static_results


# endregion FUNC_run_static_checks


# region FUNC_run_pytest_checks
## @purpose  Pytest-фаза диагностики (извлечено из run_diagnostic, DevPlan 170 W3):
##           строго последовательно (решение b), каждый чек через runner.run_pytest_check
##           (xdist + docker-лок + allow_no_tests).
## @io       ⇥ pytest_checks: list[CheckSpec], env: dict, root: Path → ⎋ list[CheckOutcome]
## @complexity O(C * t) где C = чеки, t = время исполнения
def _run_pytest_checks(pytest_checks: list[CheckSpec], env: dict[str, str], root: Path) -> list[CheckOutcome]:
    """Run pytest checks strictly sequentially via the deduped run_pytest_check cycle."""
    results: list[CheckOutcome] = []
    for spec in pytest_checks:
        cmd_str = spec.resolve_command(None)
        if not cmd_str:
            continue
        print(
            f"[IMP:7][check] pytest: {spec.id} (sequential, timeout={spec.timeout}s, xdist={spec.xdist})...",
            file=sys.stderr,
        )
        r = run_pytest_check(spec, cmd_str, spec.timeout, env, root, log_tag="gate")
        print(f"[IMP:8][gate] done {spec.id} ({r.duration_ms:.0f} ms)", file=sys.stderr)
        results.append(r)
    return results


# endregion FUNC_run_pytest_checks


# region FUNC_write_cache
## @purpose  Запись кэш-JSON после диагностического прогона (извлечено из run_diagnostic,
##           DevPlan 170 W3): status failed тоже пишется (упавший прогон не реплеится).
## @io       ⇥ fp: str | None, cache_file: Path | None, report_dict: dict,
##             report_str: str, total_ms: float → None
## @complexity O(1)
def _write_cache(
    fp: str | None,
    cache_file: Path | None,
    report_dict: CheckReportDict,
    report_str: str,
    total_ms: float,
) -> None:
    """Persist the diagnostic run cache (green AND failed — failed never replayed)."""
    if fp is None or cache_file is None:
        return
    from core.internal.check_suite.fingerprint import CheckCacheDict

    cache_data: CheckCacheDict = {
        "fingerprint": fp,
        "status": report_dict["status"],
        "duration_ms": total_ms,
        "report": report_str,
        "checks": report_dict["checks"],
    }
    save_cache(cache_file, cache_data)
    logger.info("[IMP:8][check][cache] cache written (status=%s)", report_dict["status"])


# endregion FUNC_write_cache


# region FUNC_run_diagnostic
## @purpose  Диагностический executor (`make check`): fix-фаза → fingerprint → кэш (replay
##           зелёного прогона) → static-чеки в потоках + pytest-чеки последовательно → отчёт
##           → запись кэша. Кэш только здесь; gate/diff — без кэша.
## @io       ⇥ root: Path, no_fix: bool, json_output: bool, workers: int, no_cache: bool,
##             verbose: bool → int (0 зелёный, 1 провалы)
## @complexity O(C * t) где C = чеки, t = время исполнения
## @invariants
##   - fingerprint ПОСЛЕ fix-фазы (мутация автоправкой не ломает replay)
##   - Replay только при fingerprint-совпадении И status=green; упавший прогон никогда не реплеится
##   - pytest-чеки строго последовательно (решение b); static-чеки параллельно
##   - --no-cache / CHECK_CACHE=0 → без чтения и записи кэша
def run_diagnostic(
    root: Path,
    no_fix: bool = False,
    json_output: bool = False,
    workers: int = DEFAULT_MAX_WORKERS,
    no_cache: bool = False,
    verbose: bool = False,
) -> int:
    """Diagnostic executor: fix phase → fingerprint cache → parallel static + sequential pytest."""
    start = time.monotonic()
    manifest = cs.load_manifest(root)  # late-binding: DI-HYG
    errors = validate_manifest(manifest)
    if errors:
        print(f"[IMP:10][check] Manifest invalid ({len(errors)} error(s)):\n" + "\n".join(f"  - {e}" for e in errors))
        return 2

    env = os.environ.copy()
    env.setdefault("PYTEST_NO_ESCALATION", "1")

    outcomes: list[CheckOutcome] = []
    # ── Fix phase (sequential, мутирует файлы) ──
    if not no_fix:
        fix_results, fix_ok = _run_fix_phase(manifest, root, env)
        outcomes.extend(fix_results)
        if not fix_ok:
            total_ms = (time.monotonic() - start) * 1000
            report_str, _ = format_report(outcomes, total_ms, json_output=json_output)
            print(report_str)
            return 1

    # ── Fingerprint ПОСЛЕ fix-фазы (DevPlan §3.4 п.3) ──
    cache_disabled = no_cache or os.environ.get("CHECK_CACHE") == "0"
    fp = None if cache_disabled else compute_fingerprint(root)
    cache_file = None if cache_disabled else cs.cache_path(root)  # late-binding: DI-HYG

    if _maybe_replay_cached(fp, cache_file, json_output):
        return 0

    diagnostic_checks = list_checks(manifest, gate_mode=None)
    static_checks = [s for s in diagnostic_checks if s.tier == "static"]
    pytest_checks = [s for s in diagnostic_checks if s.tier == "pytest"]

    # ── static: параллельно в потоках; pytest: строго последовательно (решение b) ──
    outcomes.extend(_run_static_checks(static_checks, env, root, workers))
    outcomes.extend(_run_pytest_checks(pytest_checks, env, root))

    total_ms = (time.monotonic() - start) * 1000
    report_str, report_dict = format_report(outcomes, total_ms, json_output=json_output)

    if verbose and not json_output:
        for r in outcomes:
            if not r.passed and not r.passed_no_tests:
                report_str += f"\n\n=== FULL OUTPUT: {r.name} ===\n"
                report_str += r.stdout + "\n" + r.stderr

    print(report_str)

    # ── Запись кэша (status failed тоже пишется — упавший прогон не реплеится) ──
    _write_cache(fp, cache_file, report_dict, report_str, total_ms)

    return 0 if report_dict["status"] == "green" else 1


# endregion FUNC_run_diagnostic

# endregion RUN_DIAGNOSTIC
