#!/usr/bin/env python3
# GREP_SUMMARY: gate subprocess-io sole run-subprocess single-canon B4 anti-drift lifecycle-removed
# STRUCTURE: ▶ AST/line-скан core/**/*.py → ○ `def run_subprocess` + `subprocess.run(` = ИМПЛЕМЕНТАЦИЯ → ◇ кандидат != shared/subprocess_io.py? → ⟦RED⟧ → ⎋ PASS (1 канон)
# region MODULE_CONTRACT
## @purpose  Gate «единственный run_subprocess» (DevPlan 119 B4, AC-B4.4): реализация канона
##           subprocess-вызова существует ТОЛЬКО в core/internal/shared/subprocess_io.py.
##           Имплементация = `def run_subprocess` + `subprocess.run(` в файле (фактический вызов
##           subprocess). Тонкий делегирующий фасад converge/infra.py (C10: `def run_subprocess`
##           без subprocess.run — обёртка над shared) НЕ является реализацией — не RED.
##           lifecycle/helpers/subprocess_io.py (второй канон) удалён в B4 — дрейф-защита.
## @scope    Сканирует core/**/*.py (рекурсивно). Триединая регистрация: файл + @pytest.mark.gate
##           + entrypoint-manifest.yaml (gates).
## @invariants
##   - Ровно 1 файл содержит `def run_subprocess` И `subprocess.run(` — shared/subprocess_io.py
##   - converge/infra.py (делегация, без subprocess.run в теле) — допустимый фасад (C10)
##   - Любой НОВЫЙ файл с реализацией run_subprocess → RED (anti-drift, R5)
## @rationale B4 (AUDIT-4 D2): второй канон run_subprocess (lifecycle/helpers/subprocess_io.py,
##            default timeout=120 литерал) — источник дрейфа. Удалён; гейт предотвращает
##            реинтродукцию второй реализации.
## @changes  2026-08-02 | DevPlan 119 B4 — Created
# endregion MODULE_CONTRACT

import logging
import re

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE = ROOT / "core"

# Единственный допустимый файл-реализация run_subprocess (канон, C10/B4) — relative to repo root
_CANON_FILE = "core/internal/shared/subprocess_io.py"

_DEF_RUN_SUBPROCESS = re.compile(r"def\s+run_subprocess\s*\(")
_SUBPROCESS_RUN = re.compile(r"subprocess\.run\s*\(")


def _find_run_subprocess_implementations() -> list[str]:
    """Найти файлы-реализации run_subprocess: `def run_subprocess` + `subprocess.run(`.

    ▶ ┌core/**/*.py┐ → ○ line scan → ◇ def run_subprocess ∧ subprocess.run( → ⊕ candidates → ⎋ list
    ## @purpose  Имплементация определяется по НАЛИЧИЮ фактического subprocess.run — тонкий
    ##            делегирующий фасад (converge/infra.py) не содержит его в теле и не RED.
    """
    candidates: list[str] = []
    for p in sorted(_CORE.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if _DEF_RUN_SUBPROCESS.search(text) and _SUBPROCESS_RUN.search(text):
            candidates.append(rel)
    return candidates


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · единственный run_subprocess канон (B4)
# · Scenario: реализация run_subprocess существует только в shared/subprocess_io.py
# · Last fail: lifecycle/helpers/subprocess_io.py — второй канон (AUDIT-4 D2)
# · Remove if: single-canon гейт отменяется
def test_single_run_subprocess_canon(caplog) -> None:
    """Ровно одна реализация run_subprocess в core/ — shared/subprocess_io.py (B4, AC-B4.4)."""
    offenders = [rel for rel in _find_run_subprocess_implementations() if rel != _CANON_FILE]
    if offenders:
        for rel in offenders:
            logger.error("[IMP:10][subprocess_io_sole] %s — вторая реализация run_subprocess", rel)
        pytest.fail(
            f"Обнаружены дополнительные реализации run_subprocess ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}" for rel in offenders)
            + "\n\nЕдинственный канон: core/internal/shared/subprocess_io.py (DevPlan 118 C10 / 119 B4)."
        )
    logger.info("[IMP:9][subprocess_io_sole] PASS: единственная реализация run_subprocess — %s", _CANON_FILE)


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · вторая реализация run_subprocess детектится (B4)
# · Scenario: probe-файл с def run_subprocess + subprocess.run → _find_run_subprocess_implementations ловит
# · Last fail: lifecycle/helpers/subprocess_io.py — второй канон (исходный вход AUDIT-4 D2)
# · Remove if: single-canon гейт отменяется
def test_single_run_subprocess_canon_negative(caplog) -> None:
    """R5 negative: новая реализация run_subprocess (исходный вход B4) детектируется.

    ## @purpose — Anti-survivorship: доказывает, что детектор ловит реинтродукцию второго канона
    ##            (lifecycle/helpers/subprocess_io.py — исходный вход AUDIT-4 D2).
    ## @io — ⎋ None (assert: probe-реализация обнаружена)
    ## @complexity — O(F) — один временный файл
    """
    caplog.set_level(logging.INFO)
    import textwrap

    probe = _CORE / "_gate_probe_subprocess_io.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import subprocess
            def run_subprocess(cmd, *, timeout=120, check=True):
                return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)
            """
        )
    )
    try:
        hits = [rel for rel in _find_run_subprocess_implementations() if "_gate_probe_subprocess_io" in rel]
        assert hits, "R5 FAIL: probe-реализация run_subprocess (исходный вход B4) не обнаружена"
        logger.info("[IMP:9][subprocess_io_sole][R5] PASS: probe %s детектирован как вторая реализация", hits[0])
    finally:
        probe.unlink(missing_ok=True)
