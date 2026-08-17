"""
# GREP_SUMMARY: check-suite, gate, run-gate, gate-modes, fail-fast, accumulate, junit-merge, cleanup-reports, non-blocking
# STRUCTURE: ▶ run_gate ┌mode∈{fast,full,ci-docker}→manifest→validate┐ → _cleanup_reports → ○ loop steps: _run_gate_step ┌skip-precommit|resolve|xdist|project|docker-lock|pre-commit-retry┐ → _handle_step_failure ┌non_blocking|FAIL-отчёт┐ → ◇ fast?→break → junit-merge (full/ci-docker) → format_report → ⎋ 0|1|2
# region MODULE_CONTRACT
## @purpose  Канонический gate-executor пакета check_suite (`make gate MODE=fast|full|ci-docker`,
##           DevPlan 170 W3 — извлечено из монолита core/internal/check_suite.py): шаги из
##           манифеста по gate_modes в каноническом порядке; fast — fail-fast, full/ci-docker —
##           accumulate + junit-merge; БЕЗ кэша (арбитр всегда честный прогон).
## @scope    core/internal/check_suite/gate.py — stdlib-only. Потребитель: __init__.py (CLI run).
## @invariants
##   - Порядок шагов = порядок манифеста (паритет ci.mk — golden-тест consistency-гейта)
##   - allow_no_tests (rc=5) → PASS; non_blocking → провал не роняет gate и не стопит fast
##   - SKIP_PRECOMMIT → pre-commit шаг пропускается (паритет ci.mk SKIP_PRECOMMIT=1)
##   - PROJECT → -k только для прямых pytest-команд project_filter-чеков
##   - fail-fast: первый НЕ-non_blocking провал → exit 1 (последующие шаги не выполняются)
##   - accumulate: все шаги выполняются; exit 1 при любом провале
##   - junit-merge: full → contract/static_audit/predeploy/smoke/component; ci-docker →
##     predeploy-docker/smoke/component (порядок и состав паритетны ci.mk)
## @rationale Извлечение run_gate из монолита: _run_gate_step/_handle_step_failure —
##            механическое разделение тела цикла (research-A §1); pytest-цикл дедуплицирован
##            в runner.run_pytest_check, pre-commit retry — в runner.run_retry_once (семантика 1:1).
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package); 170 private-imports:
##           приватные имена переименованы в публичные (U-07)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from pathlib import Path

from core.internal import check_suite as cs
from core.internal.check_suite import PROJECT_ROOT, VALID_GATE_MODES
from core.internal.check_suite.manifest import list_checks, validate_manifest
from core.internal.check_suite.models import CheckOutcome, CheckSpec
from core.internal.check_suite.report import format_report
from core.internal.check_suite.runner import run_pytest_check, run_retry_once
from core.internal.shared.subprocess_io import run_subprocess_streaming

logger = logging.getLogger(__name__)


# region RUN_GATE


# region FUNC_cleanup_reports
## @purpose  Удаление старых tests/report*.xml перед прогоном gate (паритет ci.mk: rm -f).
## @io       ⇥ root: Path → None
## @complexity O(R) где R = report-файлы
def _cleanup_reports(root: Path) -> None:
    """Remove stale JUnit reports before a gate run (parity with ci.mk)."""
    reports_dir = root / "tests"
    if reports_dir.is_dir():
        for p in reports_dir.glob("report*.xml"):
            with contextlib.suppress(OSError):
                p.unlink()


# endregion FUNC_cleanup_reports


# region FUNC_merge_junit
## @purpose  junit-merge через tests/merge_junit.py (DevPlan §3.6: reuse существующего
##           механизма, НЕ новая агрегация): существующие junit-файлы чеков → tests/report.xml.
##           Missing-файлы merge_junit пропускает сам; отсутствие всех → warn без fail.
## @io       ⇥ root: Path, junit_paths: list[str] (в каноническом порядке) → None
## @complexity O(M * T) где M = файлы, T = тесткейсы
def _merge_junit(root: Path, junit_paths: list[str]) -> None:
    """Merge existing JUnit reports via tests/merge_junit.py (reuse, DevPlan §3.6)."""
    existing = [str(root / p) for p in junit_paths if (root / p).is_file()]
    if not existing:
        logger.warning("[IMP:7][gate][merge] Нет JUnit-отчётов для merge — пропуск")
        return
    # merge_junit.py — инфраструктура платформы: ищем в tests/ корня прогона, фолбэк — tests/ платформы
    merge_script = root / "tests" / "merge_junit.py"
    if not merge_script.is_file():
        merge_script = PROJECT_ROOT / "tests" / "merge_junit.py"
    out = root / "tests" / "report.xml"
    proc = run_subprocess_streaming(
        [sys.executable, str(merge_script), *existing, "-o", str(out)],
        timeout=120,
        cwd=str(root),
        stream=False,
        heartbeat=0,
    )
    if proc.returncode != 0:
        print(
            f"[IMP:9][gate][merge] JUnit merge FAILED (exit {proc.returncode}): {proc.stderr[-500:]}", file=sys.stderr
        )


# endregion FUNC_merge_junit


# region FUNC_run_gate_step
## @purpose  Один шаг gate-цикла (извлечено из run_gate, DevPlan 170 W3): skip-precommit,
##           резолв команды, xdist+project-фильтр+docker-лок+allow_no_tests через
##           run_pytest_check, pre-commit retry-once (DevPlan 124). None = шаг пропущен.
## @io       ⇥ spec: CheckSpec, i: int, total: int, gate_mode: str, env: dict, root: Path,
##             project: str | None, skip_precommit: bool → ⎋ CheckOutcome | None
## @complexity O(1) + время subprocess
def _run_gate_step(
    spec: CheckSpec,
    i: int,
    total: int,
    gate_mode: str,
    env: dict[str, str],
    root: Path,
    project: str | None,
    skip_precommit: bool,
) -> CheckOutcome | None:
    """Run one gate step (skip/resolve/execute); None when the step is skipped."""
    if spec.id == "pre-commit" and skip_precommit:
        print(f"[IMP:7][gate] Step {i}/{total}: pre-commit SKIPPED (SKIP_PRECOMMIT=1)", file=sys.stderr)
        return None
    cmd_str = spec.resolve_command(gate_mode)
    if not cmd_str:
        print(f"[IMP:9][gate] Step {i}/{total}: {spec.id} — команда не найдена (пропуск)", file=sys.stderr)
        return None
    print(f"[IMP:7][gate] Step {i}/{total}: {spec.id} (timeout={spec.timeout}s)...", file=sys.stderr)
    r = run_pytest_check(spec, cmd_str, spec.timeout, env, root, project=project, log_tag="gate")
    # DevPlan 124 (решение пользователя 2026-08-03): pre-commit-шаг — retry-once при
    # «files were modified by this hook». Механизм флейка: pre-commit сверяет git-статус
    # до/после КАЖДОГО хука; параллельная сессия (`git add -A` + commit в том же worktree,
    # прецедент 2026-08-03 — RC-сессия коммитила во время gate-прогонов) меняет индекс во
    # время исполнения хука → ложный «files were modified» (2/3 gate-фейлов; standalone —
    # 0 фейлов). Retry-once отличает транзиент (повтор проходит) от реальной модификации
    # хуком (повтор тоже падает — gate честно RED).
    if spec.id == "pre-commit":
        r = run_retry_once(
            r,
            cmd_str,
            spec.timeout,
            env,
            root,
            docker_lock=spec.docker,
            retry_condition=lambda out: "files were modified by this hook" in (out.stdout or ""),
            retry_message=(
                "[IMP:8][gate] pre-commit: 'files were modified' — транзиентная гонка с параллельной "
                "git-операцией, retry-once (DevPlan 124)"
            ),
        )
    return r


# endregion FUNC_run_gate_step


# region FUNC_handle_step_failure
## @purpose  Обработка провала шага gate (извлечено из run_gate, DevPlan 170 W3):
##           non_blocking → blocked-маркировка; иначе gate_failed=True + FAIL-отчёт
##           (stdout приоритетнее stderr — TRAP ниже). Возвращает обновлённый gate_failed.
## @io       ⇥ spec: CheckSpec, r: CheckOutcome, gate_failed: bool → bool
## @complexity O(1)
# ⚠️ TRAP[BUG] 2026-08-03 · stdout pytest вытеснялся скипами из stderr
# · Symptom: gate-fast CI «gates exit 1» без деталей — FAILED-строки pytest
# ·   не видны (conftest automatic_skip_gate логирует 16 скипов в stderr;
# ·   прежний выбор (r.stderr or r.stdout) показывал только хвост скипов).
# · Fix: приоритет stdout (pytest short summary с FAILED), stderr — fallback.
def _handle_step_failure(spec: CheckSpec, r: CheckOutcome, gate_failed: bool) -> bool:
    """Mark non_blocking failures / report blocking failures; returns updated gate_failed."""
    if not r.passed and not r.passed_no_tests:
        if spec.non_blocking:
            r.blocked = True
            print(f"[IMP:8][gate] {spec.id}: провал НЕ блокирует gate (non_blocking)", file=sys.stderr)
        else:
            gate_failed = True
            print(f"[IMP:9][gate] FAIL: {spec.id} (exit {r.exit_code})", file=sys.stderr)
            print(((r.stdout or r.stderr) or "")[-3000:], file=sys.stderr)
    return gate_failed


# endregion FUNC_handle_step_failure


# region FUNC_run_gate
## @purpose  Канонический gate-executor (`make gate MODE=fast|full|ci-docker`): шаги из
##           манифеста по gate_modes в каноническом порядке; fast — fail-fast, full/ci-docker —
##           accumulate + junit-merge; БЕЗ кэша (арбитр всегда честный прогон).
## @io       ⇥ root: Path, gate_mode: str, project: str | None, skip_precommit: bool → int
## @complexity O(C * t) где C = шаги, t = время исполнения
## @invariants
##   - Порядок шагов = порядок манифеста (паритет ci.mk — golden-тест consistency-гейта)
##   - allow_no_tests (rc=5) → PASS; non_blocking → провал не роняет gate и не стопит fast
##   - SKIP_PRECOMMIT → pre-commit шаг пропускается (паритет ci.mk SKIP_PRECOMMIT=1)
##   - PROJECT → -k только для прямых pytest-команд project_filter-чеков
##   - fail-fast: первый НЕ-non_blocking провал → exit 1 (последующие шаги не выполняются)
##   - accumulate: все шаги выполняются; exit 1 при любом провале
##   - junit-merge: full → contract/static_audit/predeploy/smoke/component; ci-docker →
##     predeploy-docker/smoke/component (порядок и состав паритетны ci.mk)
def run_gate(
    root: Path,
    gate_mode: str,
    project: str | None = None,
    skip_precommit: bool = False,
) -> int:
    """Canonical gate executor: manifest-ordered steps, fail-fast/accumulate, no cache."""
    if gate_mode not in VALID_GATE_MODES:
        print(
            f"[IMP:10][gate] ERROR: Unknown MODE={gate_mode!r}. Valid values: {', '.join(VALID_GATE_MODES)}",
            file=sys.stderr,
        )
        return 2

    start = time.monotonic()
    manifest = cs.load_manifest(root)  # late-binding: DI-HYG
    errors = validate_manifest(manifest)
    if errors:
        print(f"[IMP:10][gate] Manifest invalid ({len(errors)} error(s)):\n" + "\n".join(f"  - {e}" for e in errors))
        return 2

    steps = list_checks(manifest, gate_mode=gate_mode)
    _cleanup_reports(root)
    env = os.environ.copy()
    env.setdefault("PYTEST_NO_ESCALATION", "1")

    print(
        f"[IMP:7][gate] MODE={gate_mode} — {len(steps)} шагов из core/check-suite.yaml (без кэша)...", file=sys.stderr
    )
    outcomes: list[CheckOutcome] = []
    gate_failed = False
    for i, spec in enumerate(steps, 1):
        r = _run_gate_step(spec, i, len(steps), gate_mode, env, root, project, skip_precommit)
        if r is None:
            continue
        outcomes.append(r)
        gate_failed = _handle_step_failure(spec, r, gate_failed)
        if gate_failed and gate_mode == "fast":
            break  # fail-fast: первый блокирующий провал стопит fast-режим

    if gate_mode in {"full", "ci-docker"}:
        junit_paths = [s.junit for s in steps if s.junit]
        _merge_junit(root, junit_paths)

    total_ms = (time.monotonic() - start) * 1000
    report_str, report_dict = format_report(outcomes, total_ms)
    print(report_str)

    if report_dict["status"] == "green":
        print(f"[IMP:9][gate] Gate: ALL PASS (MODE={gate_mode})", file=sys.stderr)
        return 0
    print(f"[IMP:9][gate] Gate: FAILURES DETECTED (MODE={gate_mode}) — см. FAIL-секции выше", file=sys.stderr)
    return 1


# endregion FUNC_run_gate

# endregion RUN_GATE
