#!/usr/bin/env python3
# GREP_SUMMARY: gate collection-floors, allow-no-tests, rc5, false-green, empty-collection, pytest-tier, deny-by-default, REF-0107
# STRUCTURE: ▶ load check-suite.yaml → ⊕ pytest-tier suites → ◇ allow_no_tests? exempt : floor-required
#            → ○ pytest --collect-only -q <selector> per floor → ∑ counts ≥ 1 → ⎋ FAIL при пустой коллекции
# region MODULE_CONTRACT
## @purpose  Collection floors (REF-0107, DevPlan 11 В3): исторически наполненные pytest-сьюты
##           манифеста core/check-suite.yaml обязаны собирать ≥1 тест. Пустая коллекция (rc=5)
##           у них = RED, а не PASS: девять каналов мапят rc=5 в PASS (allow_no_tests-канон) —
##           без этого гейта весь docker-tier/маркерный слой может исчезнуть молча
##           (переименование маркера, опечатка в cmds, glob-регрессия).
## @scope    Все tier==pytest записи check-suite.yaml. Suites с allow_no_tests: true
##           (gates-docker/predeploy-docker/integration) — легитимно пустые, exempt.
##           Deny-by-default: НОВЫЙ pytest-suite без allow_no_tests обязан получить floor
##           в FLOORS (гейт падает с явным repair-указанием) — тихое добавление невозможно.
## @invariants
##   - FLOORS[id] — селектор коллекции, семантически эквивалентный cmds.fast сьюта
##     (маркерное выражение + корень скана); расхождение селектора и cmds ловится самим
##     гейтом (коллекция по чужому селектору не отвечает на исчезновение реального слоя)
##   - Коллекция НЕ исполняет тесты (fixtures не стартуют, Docker не нужен)
##   - rc != 0 или "no tests collected" / count == 0 → FAIL (R4-семантика: пустота ≠ зелень)
## @rationale REF-0107 problem (3): «девять каналов мапят pytest rc=5 (0 collected) в PASS».
##            Floors закрывают класс false-green на уровне состава, не исполнения:
##            allow_no_tests остаётся каноном для ОПЦИОНАЛЬНЫх слоёв, но исторически
##            наполненные слои получают нижнюю границу.
## @changes 2026-08-25 | REF-0107 (DevPlan 11 Волна 3) — Created
# endregion MODULE_CONTRACT

import logging
import re
import shlex
import subprocess
import sys

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

_ROOT = repo_root()
_MANIFEST = _ROOT / "core" / "check-suite.yaml"

_PY = str(_ROOT / ".venv" / "bin" / "python") if (_ROOT / ".venv" / "bin" / "python").is_file() else sys.executable

# Финальная строка `pytest --collect-only -q`: "N tests collected in Xs" / "no tests collected".
_COLLECTED_RE = re.compile(r"(\d+) tests? collected")
_NO_TESTS_RE = re.compile(r"no tests (collected|ran)")


def _pytest_tier_suits(manifest: dict) -> list[dict]:
    """Все tier==pytest записи манифеста (канонический порядок)."""
    return [c for c in manifest.get("checks", []) if c.get("tier") == "pytest"]


# Floor-селекторы: id сьюта → аргументы коллекции (после `python -m pytest`),
# семантика cmds.fast из check-suite.yaml (test_runner --marker M ≡ pytest tests/ -m M).
_FLOORS: dict[str, list[str]] = {
    "gates": ["tests/gates/", "-m", "gate and not requires_docker"],
    "contract": ["tests/", "-m", "contract"],
    # Маркер ai_instructions (underscore, pyproject:140); suite-ID — ai-instructions (hyphen)
    "ai-instructions": ["tests/", "-m", "ai_instructions"],
    "static_audit": ["tests/", "-m", "static_audit"],
    "predeploy": ["tests/", "-m", "predeploy and not requires_docker"],
    "smoke": ["tests/", "-m", "smoke"],
    "component": ["tests/", "-m", "component"],
}


def _collect_count(args: list[str]) -> tuple[int, str]:
    """Запустить pytest --collect-only -q; вернуть (count, diag).

    ## @io ⇥ args → ⎋ (число собранных тестов, диагностическая строка)
    ## @invariants rc != 0 → count=0 (диагностика содержит stderr-tail); fixtures не стартуют.
    """
    cmd = [_PY, "-m", "pytest", "--collect-only", "-q", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(_ROOT), check=False)
    tail = (proc.stdout or "").strip().splitlines()
    last = tail[-1] if tail else ""
    match = _COLLECTED_RE.search(last)
    if proc.returncode == 0 and match is not None:
        return int(match.group(1)), last.strip()
    if _NO_TESTS_RE.search(proc.stdout or ""):
        return 0, "no tests collected"
    return 0, f"rc={proc.returncode}: {last}"


# region TEST_collection_floors
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · REF-0107 floors: пустая коллекция ≠ PASS
# · Scenario: маркер-переименование/опечатка cmds обнуляет слой → rc=5 → каналы мапят в PASS
# · Last fail: live `--only exception_patterns` vs детектор `exception-patterns` ([skip]×14,
#   PASS без проверок) — тот же класс обманщика на составе, не на детекции
# · Remove if: allow_no_tests-механизм заменяется структурным учётом состава
def test_collection_floors(caplog) -> None:
    """Исторически наполненные pytest-сьюты собирают ≥1 тест; новые без allow_no_tests требуют floor."""
    caplog.set_level(logging.INFO)
    manifest = load_yaml(_MANIFEST)
    suits = _pytest_tier_suits(manifest)
    assert suits, "[IMP:10][floors] check-suite.yaml: ни одной tier==pytest записи — манифест сломан?"

    missing_floors, failures = _evaluate_floors(suits, _collect_count)

    assert not missing_floors, (
        "[IMP:10][floors] pytest-сьюты без allow_no_tests и без floor в _FLOORS — "
        f"добавьте floor-селектор или allow_no_tests: true: {missing_floors}"
    )
    assert not failures, (
        "[IMP:10][floors] EMPTY COLLECTION в исторически наполненных сьютах "
        "(rc=5 мапится каналами в PASS — это false-green, REF-0107):\n" + "\n".join(failures)
    )
    logger.info("[IMP:9][floors] PASS: %d floor-сьютов собрали ≥1 тест", len(_FLOORS))


# endregion TEST_collection_floors


# region TEST_layer_below_floor_mutation
def _evaluate_floors(suits: list[dict], collect_fn) -> tuple[list[str], list[str]]:
    """Ядро floors-гейта (QA G3/T2.G): отделено от pytest-обвязки для mutation-негатива.

    ## @io  ⇥ suits (tier==pytest записи), collect_fn(selector) → (count, diag)
    ##      ⎋ (missing_floors, failures)
    """
    failures: list[str] = []
    missing_floors: list[str] = []

    for suit in suits:
        sid = suit.get("id")
        assert isinstance(sid, str) and sid, f"[IMP:10][floors] запись без id: {suit!r}"
        if suit.get("allow_no_tests"):
            continue
        if sid not in _FLOORS:
            missing_floors.append(sid)
            continue
        selector = _FLOORS[sid]
        count, diag = collect_fn(selector)
        if count < 1:
            failures.append(f"{sid}: 0 collected ({diag}; selector: {' '.join(selector)})")
    return missing_floors, failures


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA G3/T2.G — mutation «слой опустел → RED»
# · Scenario: маркер-переименование/опечатка обнуляет коллекцию слоя (count=0) — гейт ОБЯЗАН
#   дать RED по этому сьюту; прежняя проверка была инлайн в позитивном тесте и не имела
#   самонегатива (детектор не доказывал, что ловит пустоту)
# · Last fail: 2026-08-25 (REGRESSIONS.md R13/G7) — mutation-негатив отсутствовал
# · Remove if: floors-механизм заменяется структурным учётом состава
@pytest.mark.gate
@ldd_trajectory
def test_layer_below_floor_red(caplog) -> None:
    """Mutation: каждый не-exempt слой, опустевший до 0 collected, попадает в failures."""
    caplog.set_level(logging.INFO)
    manifest = load_yaml(_MANIFEST)
    suits = _pytest_tier_suits(manifest)

    def empty_collector(_selector: list[str]) -> tuple[int, str]:
        return 0, "no tests collected"

    missing, failures = _evaluate_floors(suits, empty_collector)

    expected_non_exempt = [s["id"] for s in suits if not s.get("allow_no_tests")]
    assert not missing, f"mutation-прогон не должен терять floors: {missing}"
    assert len(failures) == len(expected_non_exempt), (
        f"G3 FAIL: детектор пропустил опустевший слой: failures={failures}, expected {len(expected_non_exempt)} RED"
    )
    assert any(f.startswith("gates:") for f in failures), "gates-слой обязан быть под floor'ом"
    logger.info("[IMP:9][floors][mutation] PASS: %d emptied layer(s) → RED", len(failures))


# endregion TEST_layer_below_floor_mutation


# region TEST_floor_selectors_track_manifest
@pytest.mark.gate
@ldd_trajectory
def test_floor_selectors_track_manifest(caplog) -> None:
    """Селекторы FLOORS синхронны cmds.fast манифеста (drift-детект: корень скана + маркер)."""
    caplog.set_level(logging.INFO)
    manifest = load_yaml(_MANIFEST)

    def _marker_of(tokens: list[str], flag: str) -> str | None:
        return tokens[i + 1] if flag in tokens and (i := tokens.index(flag)) + 1 < len(tokens) else None

    for suit in _pytest_tier_suits(manifest):
        sid = suit.get("id")
        fast_cmd = (suit.get("cmds") or {}).get("fast") or suit.get("cmd") or ""
        if sid not in _FLOORS:
            continue
        floor_args = _FLOORS[sid]
        floor_scan, floor_marker = floor_args[0].rstrip("/"), _marker_of(floor_args, "-m")
        cmd_tokens = shlex.split(fast_cmd)
        if fast_cmd.startswith("pytest"):
            # Прямой pytest: корень скана и маркер cmds.fast обязаны совпадать с floor
            assert floor_scan in fast_cmd, (
                f"[IMP:10][floors] {sid}: floor-корень '{floor_scan}' отсутствует в cmds.fast: {fast_cmd!r}"
            )
            assert _marker_of(cmd_tokens, "-m") == floor_marker, (
                f"[IMP:10][floors] {sid}: маркер floor ({floor_marker!r}) ≠ cmds.fast ({fast_cmd!r})"
            )
        else:
            # test_runner --marker M: M — КЛЮЧЬ реестра test_runner (может отличаться от
            # pytest-маркера: ai-instructions → -m ai_instructions, DevPlan 001 T4.6).
            # Паритет проверяется через РЕЗОЛВ реестра: resolved pytest-маркер == floor.
            from core.internal.test_runner import MARKER_MAP as _registry

            registry_key = _marker_of(cmd_tokens, "--marker")
            assert registry_key is not None, f"[IMP:10][floors] {sid}: cmds.fast без --marker: {fast_cmd!r}"
            assert registry_key in _registry and _registry[registry_key] is not None, (
                f"[IMP:10][floors] {sid}: --marker {registry_key!r} не в реестре test_runner"
            )
            resolved = " ".join(_registry[registry_key])
            # Реестр может расширять базовый маркер исключениями (static_audit: "-m X or
            # (not e2e and …)") — паритет = базовый маркер совпадает и резолв НЕ пустой.
            assert resolved.startswith(f"-m {floor_marker}"), (
                f"[IMP:10][floors] {sid}: реестр резолвит {resolved!r}, floor ожидает '-m {floor_marker}'"
            )
        logger.info("[IMP:8][floors][parity] %s ↔ %r", sid, fast_cmd)
    logger.info("[IMP:9][floors] PASS: floor-селекторы отслеживают манифест")


# endregion TEST_floor_selectors_track_manifest
