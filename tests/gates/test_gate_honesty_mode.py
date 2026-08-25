#!/usr/bin/env python3
# GREP_SUMMARY: gate honesty-mode R4 no-service-fail require-docker REQUIRE_HONESTY_MODE marker-xfail-fail CI-fail local-marker DevPlan-119-A3
# STRUCTURE: ▶ monkeypatch _docker_available→False → ◇ REQUIRE_HONESTY_MODE=fail → require_docker_or_fail → ⟦pytest.fail (не skip)⟧ → ◇ marker (default) → ⟦skip⟧ → ◇ CI workflows содержат fail → ⎋ assert
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 119 A3, AUDIT-5 R4-1): честный fail-mode Test Honesty R4.
##           В CI REQUIRE_HONESTY_MODE=fail — отсутствие Docker = FAIL (не skip).
##           Локальная dev-машина (без переменной) — marker (skip). R5 negative:
##           без Docker + fail-mode → FAIL, не skip.
## @scope    tests/_conftest/honesty.py (require_docker_or_fail + mode-dispatch) +
##           .github/workflows/platform-gate-fast.yml + platform-test.yml (env-контракт).
## @invariants
##   - REQUIRE_HONESTY_MODE=fail + Docker недоступен → pytest.fail (R4: NO_SERVICE = FAIL, not skip)
##   - REQUIRE_HONESTY_MODE не задан (локальная машина) → default "marker" → pytest.skip
##   - ВСЕ workflow с pytest (glob .github/workflows/*.yml + templates/*/workflows) объявляют
##     REQUIRE_HONESTY_MODE: fail — deny-by-default (REF-0107): новый pytest-workflow без
##     пина = RED, CI не может выключить honesty добавлением файла
## @rationale R4 (Test Honesty): skip-as-bug-masking запрещён. CI-раннеры имеют Docker —
##            переход marker→fail (D46-C закрыт DevPlan 119 A3). Локально переменная
##            не задаётся → marker через default в _honesty_mode().
## @changes  2026-08-02 | DevPlan 119 A3 — Created (fail-mode в CI, R5 negative)
# endregion MODULE_CONTRACT

import logging

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()


# region TEST_honesty_fail_on_missing_docker
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · fail-mode без Docker → FAIL (DevPlan 119 A3, AUDIT-5 R4-1)
# · Scenario: REQUIRE_HONESTY_MODE=fail + docker недоступен → pytest.fail (не skip)
# · Last fail: N/A (новый negative-тест — исходный вход R4: Docker отсутствует, а тест skip-ится)
# · Remove if: honesty-механизм отменяется
def test_honesty_fail_on_missing_docker(caplog, monkeypatch) -> None:
    """R5 negative: без Docker в fail-mode → FAIL (не skip) (Test Honesty R4)."""
    caplog.set_level(logging.INFO)

    from _conftest import honesty

    monkeypatch.setenv("REQUIRE_HONESTY_MODE", "fail")
    # already-DI (W-H 163): docker_available_fn передаётся напрямую — 0 патчей module-атрибута

    with pytest.raises(pytest.fail.Exception, match=r"\[honesty:fail\]"):
        honesty.require_docker_or_fail(
            "Docker daemon required — R4 negative probe",
            docker_available_fn=lambda: False,
        )

    logger.info("[IMP:9][honesty][R5] PASS: fail-mode without Docker → pytest.fail (не skip)")


# endregion TEST_honesty_fail_on_missing_docker


# region TEST_honesty_marker_default_skips
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · локальная dev-машина → marker (skip) (DevPlan 119 A3)
# · Scenario: REQUIRE_HONESTY_MODE не задан (default marker) + docker недоступен → pytest.skip
# · Last fail: N/A (новый тест — фиксирует контракт «локально = marker»)
# · Remove if: honesty-механизм отменяется
def test_honesty_marker_default_skips(caplog, monkeypatch) -> None:
    """Локальная dev-машина (без REQUIRE_HONESTY_MODE) → marker: skip при отсутствии Docker."""
    caplog.set_level(logging.INFO)

    from _conftest import honesty

    monkeypatch.delenv("REQUIRE_HONESTY_MODE", raising=False)
    # already-DI (W-H 163): docker_available_fn передаётся напрямую — 0 патчей module-атрибута

    with pytest.raises(pytest.skip.Exception, match=r"\[honesty:marker\]"):
        honesty.require_docker_or_fail(
            "Docker daemon required — marker probe",
            docker_available_fn=lambda: False,
        )

    logger.info("[IMP:9][honesty][marker] PASS: default marker mode without Docker → pytest.skip")


# endregion TEST_honesty_marker_default_skips


# region TEST_ci_workflows_require_honesty_fail
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · CI workflow env-контракт (DevPlan 119 A3)
# · Scenario: CI-workflow объявляет REQUIRE_HONESTY_MODE: fail
# · Last fail: marker в platform-gate-fast.yml:44 и platform-test.yml:74 (skip-mode на CI)
# · Remove if: REQUIRE_HONESTY_MODE механизм отменяется
#
# REF-0107 (2026-08-25): deny-by-default glob вместо фиксированного списка _WORKFLOWS.
# Прежний гейт покрывал 2 именованных workflow; deploy-project.yml (quality pytest) и
# push-gate.yml остались вне списка → honesty default "marker" = массовый skip на CI.
# Теперь: ЛЮБОЙ workflow, исполняющий pytest (напрямую или через make check/gate/check-diff/
# test-node), ОБЯЗАН нести REQUIRE_HONESTY_MODE: fail — CI не может выключить honesty,
# добавив новый workflow (тихий обход невозможен по построению).
#
# QA R13/G7 (DevPlan 14 T2.G): детектор самозащищён — (а) glob *.yml РАСШИРЕН *.yaml
# (nightly.yaml класс); (б) pin ищется В ТЕЛЕ без комментариев (закомментированный
# «REQUIRE_HONESTY_MODE: fail» больше не satisfies).
#
# 🧐 TRAP[DECISION] · 2026-08-25 · — · Прямой invocation-щель: `python3 -m
# core.internal.check_suite run` на runner'е МИНУС workflow — детекция ненадёжна (любой
# процесс может вызвать pytest напрямую) · Rejected: блокировать прямой invocation
# (нужен для отладки/локального dev) · Reason: остаточный риск задокументирован;
# канон запуска CI — только через workflows, гейты ловят workflow-слой полностью
# · Rev: если появится инцидент прямого запуска на CI — добавить runner-level guard

_PYTEST_CHANNELS = ("pytest", "make gate", "make check", "make test-node", "check-diff")


def _honesty_violations_for(workflow_paths) -> list[str]:
    """QA R13/G7/T2.G: violations для синтетических workflow (R5-негативы + реальное дерево).

    ▶ ┌paths┐ → ○ read+strip-comment-lines → ◇ pytest-канал? → ◇ pin in body? → ⎋ [violations]

    ## @io  ⇥ iterable[Path] → ⎋ список relpath-нарушений
    ## @invariants  Комментарии вырезаются ДО поиска пина; расширения .yml/.yaml равноправны.
    """
    violations: list[str] = []
    for wf in workflow_paths:
        if not wf.is_file():
            continue
        content = wf.read_text(errors="replace")
        body_lines = [ln for ln in content.splitlines() if not ln.strip().startswith("#")]
        body = "\n".join(body_lines)
        if not any(channel in body for channel in _PYTEST_CHANNELS):
            continue
        if "REQUIRE_HONESTY_MODE: fail" not in body:
            violations.append(str(wf))
    return violations


@pytest.mark.gate
@ldd_trajectory
def test_ci_workflows_require_honesty_fail(caplog) -> None:
    """Все workflow с pytest объявляют REQUIRE_HONESTY_MODE: fail (R4, deny-by-default glob)."""
    caplog.set_level(logging.INFO)
    workflows_dir = ROOT / ".github" / "workflows"
    # Glob покрывает шаблонные payload-workflows проектов (templates/*/…/deploy.yml)
    # И расширение .yaml (nightly.yaml класс — QA R13/G7/T2.G).
    candidates = sorted(workflows_dir.glob("*.yml")) + sorted(ROOT.glob("templates/*/.github/workflows/*.yaml"))
    candidates = sorted(set(candidates) | set(ROOT.glob("templates/*/.github/workflows/*.yml")))
    assert candidates, "[IMP:10][honesty] ни одного workflow не найдено — репозиторий сломан?"

    violations = _honesty_violations_for(candidates)

    assert not violations, (
        f"[IMP:10][honesty] workflows с pytest без REQUIRE_HONESTY_MODE: fail "
        f"(R4 NO_SERVICE = FAIL на CI; deny-by-default, REF-0107): {violations}"
    )

    logger.info(
        "[IMP:9][honesty][ci] PASS: все %d workflow с pytest объявляют REQUIRE_HONESTY_MODE: fail",
        len(candidates),
    )


# endregion TEST_ci_workflows_require_honesty_fail


# ═══════════════════════════════════════════════════════════════════
# R5 self-negatives детектора (QA R13/G7/T2.G)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R13/G7 — закомментированный пин ≠ пин
# · Scenario: workflow содержит только «# REQUIRE_HONESTY_MODE: fail» (комментарий) при
#   активном pytest-канале — прежний поиск пина по ПОЛНОМУ тексту удовлетворялся комментарием
#   и пропускал обманщика
# · Last fail: 2026-08-25 — strip-comments применялся к channel-детекту, но НЕ к pin-поиску
# · Remove if: pin переезжает в структурный формат (env-манифест с валидацией)
def test_commented_pin_does_not_satisfy(tmp_path, caplog) -> None:
    """Комментарий с пином + pytest-канал → violation (пин обязан быть живой строкой)."""
    caplog.set_level(logging.INFO)
    wf = tmp_path / "sneaky.yml"
    wf.write_text(
        "# REQUIRE_HONESTY_MODE: fail  ← обманка в комментарии\njobs:\n  t:\n    steps:\n      - run: make check\n",
        encoding="utf-8",
    )
    violations = _honesty_violations_for([wf])
    logger.info("[IMP:9][honesty][negative] commented-pin violations=%d", len(violations))
    assert violations, "R13 FAIL: закомментированный пин удовлетворил детектор"
    logger.info("[IMP:9][honesty][negative] PASS: commented pin rejected")


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R13/G7 — .yaml-расширение покрыто
# · Scenario: nightly.yaml (расширение .yaml) с pytest и БЕЗ пина — прежний glob *.yml
#   его не видел вовсе
# · Last fail: 2026-08-25 — glob покрывал только *.yml
# · Remove if: все workflow мигрируют на единое расширение с гейтом-эквалайзером
def test_yaml_extension_workflow_scanned(tmp_path, caplog) -> None:
    """.yaml workflow: без пина → violation; с пином → чисто (детектор видит расширение)."""
    caplog.set_level(logging.INFO)
    no_pin = tmp_path / "nightly.yaml"
    no_pin.write_text("jobs:\n  n:\n    steps:\n      - run: pytest tests/\n", encoding="utf-8")
    violations = _honesty_violations_for([no_pin])
    assert violations, "R13 FAIL: .yaml workflow выпал из скана"

    with_pin = tmp_path / "nightly-ok.yaml"
    with_pin.write_text(
        "env:\n  REQUIRE_HONESTY_MODE: fail\njobs:\n  n:\n    steps:\n      - run: pytest tests/\n",
        encoding="utf-8",
    )
    assert _honesty_violations_for([with_pin]) == [], "честный .yaml workflow не должен нарушать"
    logger.info("[IMP:9][honesty][negative] PASS: .yaml extension scanned both ways")
