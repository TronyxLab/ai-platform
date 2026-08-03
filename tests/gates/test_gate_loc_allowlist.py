#!/usr/bin/env python3
# GREP_SUMMARY: gate loc-allowlist state-machine reconciler project-adopter line-count srp boundary
# STRUCTURE: ▶ wc -l 3 монолитов → ◇ state_machine ≤1200 │ ◇ reconciler ≤800 │ ◇ project_adopter ≤600 → ∖ (allowlist=лимиты) → ⎋ превышение → RED
# region MODULE_CONTRACT
## @purpose  Gate test (DevPlan 116 B9 T6.2): LOC-гейт на пост-декомпозиционные монолиты.
##           Превышение лимита → RED. Обоснование: acceptance B9 (1)(2)(5) —
##           state_machine = оркестрация (persistence/I/O/CLI вынесены), reconciler = оркестратор
##           (8 доменов + infra вынесены), project_adopter = оркестрация (compose/vhost вынесены).
## @scope    Проверяет 3 файла: lifecycle/state_machine.py (≤1200), converge/reconciler.py (≤800),
##           scaffold/project_adopter.py (≤600). Остальные новые модули — под дефолтным
##           check-file-lines 500 (non-blocking warning).
## @invariants
##   - Лимиты — константы ALLOWLIST (единый источник; правка = осознанное решение Architect)
##   - wc -l считает физические строки (включая комментарии/бланки)
## @rationale LOC-гейт фиксирует SRP-границу: монолит не должен ре-расти после декомпозиции (B9).
## @changes  2026-08-01 · Created (B9 T6.2)
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

PLATFORM_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent

# Allowlist (лимиты LOC): state_machine/reconciler/project_adopter после SRP-декомпозиции (B9)
# project_adopter 600 → 650 (DevPlan 133 W1, 2026-08-03): +gen_project_platform_md (AI-PLATFORM.md,
# контракт проекта) — осознанное расширение оркестратора, лимит поднят Architect-планом.
ALLOWLIST: dict[str, int] = {
    "core/internal/bootstrap/lifecycle/state_machine.py": 1200,
    "core/internal/bootstrap/converge/reconciler.py": 800,
    "core/internal/scaffold/project_adopter.py": 650,
}


@ldd_trajectory
@pytest.mark.gate
# 🧪 TRAP[TEST] · Gate invariant · LOC-лимиты пост-декомпозиционных монолитов (B9 T6.2)
# · Scenario: state_machine ≤1200 / reconciler ≤800 / project_adopter ≤600 (wc -l)
# · Last fail: N/A (new gate, B9)
# · Remove if: LOC-гейт заменён иным механизмом контроля размера модулей
def test_gate_loc_allowlist(caplog):
    """Gate: 3 монолита под LOC-лимитами (state_machine ≤1200, reconciler ≤800, project_adopter ≤600)."""
    caplog.set_level(logging.INFO)
    failures: list[str] = []

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for rel_path, limit in ALLOWLIST.items():
        abs_path = PLATFORM_ROOT / rel_path
        if not abs_path.is_file():
            failures.append(f"{rel_path}: file not found")
            logger.error("[IMP:9][gate][loc] %s: FILE MISSING", rel_path)
            continue
        lines = len(abs_path.read_text(encoding="utf-8").splitlines())
        status = "OK" if lines <= limit else "RED"
        logger.info("[IMP:9][gate][loc] %s: %d LOC (limit %d) → %s", rel_path, lines, limit, status)
        if lines > limit:
            failures.append(f"{rel_path}: {lines} LOC > limit {limit}")
    print("--- END LDD TRAJECTORY ---")

    assert not failures, "LOC-гейт RED:\n" + "\n".join(failures)
