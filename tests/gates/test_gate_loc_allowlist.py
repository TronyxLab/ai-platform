#!/usr/bin/env python3
# GREP_SUMMARY: gate loc-allowlist state-machine reconciler project-adopter test-giants line-count srp boundary negative
# STRUCTURE: ▶ wc -l монолитов (core + tests) → ◇ ALLOWLIST лимиты │ ◇ TEST_ALLOWLIST гиганты → ⊕ _check_loc (чистый детектор) → ◇ negative R5 (превышение → RED) → ⎋ превышение → RED
# region MODULE_CONTRACT
## @purpose  Gate test (DevPlan 116 B9 T6.2 + 172 W3.3): LOC-гейт на пост-декомпозиционные
##           монолиты + тестовые гиганты. Превышение лимита → RED. Обоснование: acceptance
##           B9 (1)(2)(5) — state_machine = оркестрация (persistence/I/O/CLI вынесены),
##           reconciler = оркестратор (8 доменов + infra вынесены), project_adopter =
##           оркестрация (compose/vhost вынесены). Тестовые гиганты (172 W3.3, Brief H10):
##           лимиты с headroom ~5-8% над текущим размером; декомпозиция
##           module_domains_static/chaos — зарегистрированный follow-up (дорожка б).
## @scope    Проверяет core-монолиты (3) + тестовые гиганты (6). Остальные модули — под
##           дефолтным check-file-lines 500 (non-blocking warning).
## @invariants
##   - Лимиты — константы ALLOWLIST/TEST_ALLOWLIST (единый источник; правка = осознанное
##     решение Architect с обоснованием в коммите)
##   - wc -l считает физические строки (включая комментарии/бланки)
##   - Детектор — чистая функция _check_loc(path, limit) (testable, R5-negative)
## @rationale LOC-гейт фиксирует SRP-границу: монолит не должен ре-расти после декомпозиции (B9).
##            W3.3: тестовые гиганты 62-102KB были ВНЕ гейта (ALLOWLIST молчал про tests/) —
##            дыра закрыта.
## @changes  2026-08-01 · Created (B9 T6.2)
##            2026-08-12 · DevPlan 157 W2 T3 — сообщение: величина превышения (+N) + [REPAIR]-строка
##            2026-08-15 · DevPlan 172 W3.3 — +TEST_ALLOWLIST (6 гигантов) + _check_loc
##                      экстракция + R5-negative (тринити сохранена: файл/маркер/manifest)
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
    # 650 → 700 (DevPlan 001 T5.4, 2026-08-16): +_sync_instructions (Step 5b) + _detect_project_type —
    # .kilo/-синк из живого канона при adopt; декомпозиция adopter — follow-up вне скоупа
    # 700 → 780 (DevPlan 16 T2.D, 2026-08-25): P1-16 неинтерактивная деградация —
    # _prompt_yes_no (TTY-guard, NonInteractiveBlocked с состоянием+rollback-hint) + --yes.
    # Осознанное решение владельца лимита; следующая декомпозиция — вынос промпт-машины
    # в scaffold_helpers при первом же touch сверх 780.
    "core/internal/scaffold/project_adopter.py": 780,
}

# Test giants (DevPlan 172 W3.3, Brief H1/H10): лимиты = текущий размер + headroom 5-8%.
# Декомпозиция (дорожка б) — зарегистрированный follow-up:
#   - module_domains_static → per-domain файлы (49 тестов по 7+ доменам)
#   - chaos_resilience → per-scenario файлы (T01-T12 уже отдельные функции)
TEST_ALLOWLIST: dict[str, int] = {
    "tests/e2e/test_chaos_resilience.py": 1900,  # T1-T12 chaos-сьют (DevPlan 165)
    "tests/unit/test_module_domains_static.py": 1750,  # 49 тестов × 7+ доменов
    "tests/unit/test_state_machine.py": 1650,  # characterization state_machine (B9)
    "tests/unit/test_no_hardcoded_credentials.py": 1450,  # predeploy regex-sweep
    "tests/gates/test_gate_manifest_integrity.py": 1250,  # manifest trinity integrity
    # 1250 → 1300 (DevPlan 006 W4/W5, 2026-08-17): +run_subprocess_streaming-миграция
    # (streaming-канон, _run_docker_smoke/_generate_dev_certs_smoke/_rm_stale) + SMOKE_NO_DOCKER/
    # SMOKE_MODULES рычаги быстрой итерации (W5 bisect) — осознанное расширение фикстурного
    # каркаса, декомпозиция _conftest/compose.py — follow-up вне скоупа 006
    "tests/_conftest/compose.py": 1300,  # docker-fixture каркас (xdist-критичный)
}


# region FUNC_check_loc
## @purpose  Чистый детектор LOC-лимита (172 W3.3): читает файл, сравнивает с лимитом.
## @io       ⇥ abs_path: Path, limit: int
##           ⎋ tuple[int, bool] — (lines, is_over) ; missing file → (-1, False)
## @complexity 1 — wc -l эквивалент (splitlines)
def _check_loc(abs_path: pathlib.Path, limit: int) -> tuple[int, bool]:
    """Return (line_count, over_limit). Missing file → (-1, False)."""
    if not abs_path.is_file():
        return -1, False
    lines = len(abs_path.read_text(encoding="utf-8").splitlines())
    return lines, lines > limit


# endregion FUNC_check_loc


@ldd_trajectory
@pytest.mark.gate
# 🧪 TRAP[TEST] · Gate invariant · LOC-лимиты пост-декомпозиционных монолитов (B9 T6.2)
# · Scenario: state_machine ≤1200 / reconciler ≤800 / project_adopter ≤650 (wc -l)
# · Last fail: N/A (new gate, B9)
# · Remove if: LOC-гейт заменён иным механизмом контроля размера модулей
def test_gate_loc_allowlist(caplog):
    """Gate: core-монолиты + тестовые гиганты под LOC-лимитами (B9 T6.2 + 172 W3.3)."""
    caplog.set_level(logging.INFO)
    failures: list[str] = []

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for rel_path, limit in {**ALLOWLIST, **TEST_ALLOWLIST}.items():
        abs_path = PLATFORM_ROOT / rel_path
        lines, over = _check_loc(abs_path, limit)
        if lines < 0:
            failures.append(f"{rel_path}: file not found")
            logger.error("[IMP:9][gate][loc] %s: FILE MISSING", rel_path)
            continue
        status = "OK" if not over else "RED"
        logger.info("[IMP:9][gate][loc] %s: %d LOC (limit %d) → %s", rel_path, lines, limit, status)
        if over:
            failures.append(f"{rel_path}: {lines} LOC > limit {limit} (+{lines - limit})")
    print("--- END LDD TRAJECTORY ---")

    assert not failures, (
        "LOC-гейт RED:\n"
        + "\n".join(failures)
        + "\n[REPAIR] Превышение = осознанное решение Architect: поднять лимит в ALLOWLIST/TEST_ALLOWLIST с обоснованием в коммите"
    )


# 🧪 TRAP[TEST] · NEGATIVE (R5) · loc-allowlist — 172 W3.3
# · Last fail: ALLOWLIST проверял только 3 core-монолита; тестовые гиганты (62-102KB)
# ·   были ВНЕ гейта — ре-рост незамечен (Brief H10: "ALLOWLIST молчит про tests/")
# · Remove if: LOC-гейт заменён иным механизмом контроля размера
def test_negative_loc_over_limit_detected(tmp_path: pathlib.Path) -> None:
    """R5 negative: файл с превышением лимита детектируется чистым детектором."""
    big_file = tmp_path / "giant.py"
    big_file.write_text("x = 1\n" * 100, encoding="utf-8")

    lines, over = _check_loc(big_file, 50)

    assert over, "R5 FAIL: детектор пропустил превышение LOC-лимита (100 > 50)"
    assert lines == 100, f"R5 FAIL: неверный подсчёт строк: {lines}"
    logger.info("[IMP:9][test][loc_negative] over-limit файл детектирован: %d > 50", lines)
