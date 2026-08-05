#!/usr/bin/env python3
# GREP_SUMMARY: test-hermes-images hermes-workflow L1 L2 bare-tag ghcr pull docker-tag D18 regression DevPlan-136
# STRUCTURE: ▶ mock docker_ops + docker_compose → ◇ test_bare_tag_after_pull (позитив D18) → ◇ test_no_bare_tag_l2_fail (negative R5) → ◇ test_tag_before_build_order → ◇ test_pull_fail_build_l1_source → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/deploy/hermes_workflow.py — handle_hermes_agent
##           bare-tag после GHCR pull (DevPlan 136 W1 T1.5, D18, фикс 4c86c3b).
##           L2 Dockerfile: FROM hermes-agent-base:latest (bare tag) — pulled образ носит полное имя
##           ghcr.io/...; БЕЗ локального bare-тега L2 build падает (Docker Hub pull attempt).
## @scope    Pure unit — native imports, все docker-вызовы мокаются (docker_ops/docker_compose),
##           никакого реального docker/pull/network. tmp_path не нужен (файлов нет) — env+mocks.
## @invariants
##   - После успешного GHCR pull L1 docker_tag(ghcr-img, "hermes-agent-base:latest") ОБЯЗАН вызываться
##   - docker_tag вызывается ДО L1→L2 build (порядок фикса)
##   - Negative R5: docker_tag=False (bare-tag не создан) → WARN + L2 build FAIL → return False
##   - L1 pull fail → build из source (docker-compose.base.yml), tag НЕ вызывается
##   - Каждый успешный сценарий: IMP:9 лог (l1_pulled / built)
## @rationale DevPlan 136 W1 T1.5: D18-фикс 4c86c3b (L1 bare-tag после GHCR pull) без регресс-теста —
##            R5 anti-survivorship: тест на ТОЧНЫЙ вход бага (pull успешен → bare-tag отсутствует).
## @changes  2026-08-05 | DevPlan 136 W1 T1.5 — Created (D18 regression tests)
## @note      DevPlan-путь tests/unit/test_hermes_images.py занят тестами ДРУГОГО модуля
##            (core/internal/build/hermes_images.py, DevPlan 118 E8) + duplicate-basename
##            (и с tests/unit/test_hermes_workflow.py) ломает pytest-коллекцию — D18-тесты
##            в tests/test_hermes_l1_bare_tag.py
##            (расхождение DevPlan↔код задокументировано в coverage-matrix-d1-d23.md).
# endregion MODULE_CONTRACT

import logging
from types import SimpleNamespace

import pytest

from core.internal.bootstrap.deploy import hermes_workflow

logger = logging.getLogger(__name__)

# ── Каноничные константы теста (изолированы от env) ──
_ORG = "ghcr.io/testorg"
_BASE = "hermes-agent-base"
_BARE_TAG = f"{_BASE}:latest"
_GHCR_L1 = f"{_ORG}/{_BASE}:latest"
_COMPOSE_ARGS = ["-f", "docker-compose.yml"]


def _config_result(images: list[str]) -> SimpleNamespace:
    """Заглушка docker compose config --images (returncode 0 + stdout со списком образов)."""
    return SimpleNamespace(returncode=0, stdout="\n".join(images) + "\n")


def _patch_docker(monkeypatch: pytest.MonkeyPatch, *, pull: bool, tag: bool, build: bool) -> dict:
    """Замокать docker_ops/docker_compose вызовы handle_hermes_agent; вернуть захваченные аргументы.

    ## @purpose — Единая фабрика моков для D18-сценариев: pull/tag/build управляются флагами;
    ##            tag_args/build_order фиксируют вызовы для order-assertion.
    ## @io — ⇥ monkeypatch, pull: bool, tag: bool, build: bool → ⎋ dict (tag_args, build_order, calls)
    ## @complexity — O(1)
    """
    state: dict = {"tag_args": [], "order": []}

    monkeypatch.setattr(hermes_workflow, "GHCR_ORG", _ORG)
    monkeypatch.setattr(hermes_workflow.docker_ops, "docker_image_inspect_exists", lambda img, timeout=0: False)
    monkeypatch.setattr(hermes_workflow.docker_ops, "docker_pull", lambda img, timeout=0: pull)

    def _tag(src: str, dst: str) -> bool:
        state["tag_args"].append((src, dst))
        state["order"].append("tag")
        return tag

    monkeypatch.setattr(hermes_workflow.docker_ops, "docker_tag", _tag)
    monkeypatch.setattr(
        hermes_workflow,
        "_shared_docker_compose_config",
        lambda compose_dir, compose_args=None, flags=None: _config_result([_GHCR_L1, f"{_ORG}/{_BASE}-context:latest"]),
    )
    monkeypatch.setattr(hermes_workflow, "_shared_check_image_exists", lambda img: False)

    def _build(compose_dir, timeout=0, compose_args=None, flags=None) -> bool:
        state["order"].append("build")
        return build

    monkeypatch.setattr(hermes_workflow, "_shared_docker_compose_build", _build)
    return state


# region FUNC_test_handle_hermes_agent_bare_tag_after_pull
## @purpose — D18 позитив: GHCR pull успешен → bare-tag hermes-agent-base:latest создаётся (assert вызова).
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D18 — bare-tag после GHCR pull (4c86c3b)
# · Scenario: docker_pull=True → docker_tag(ghcr.io/.../hermes-agent-base:latest, hermes-agent-base:latest)
# · Last fail: 2026-08-04 — L2 build падал (FROM hermes-agent-base:latest не резолвился на ноде)
# · Remove if: L1-доставка меняется (pull+tag стратегия)
def test_handle_hermes_agent_bare_tag_after_pull(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    """D18: после GHCR pull L1 создаётся bare-tag hermes-agent-base:latest → build OK."""
    caplog.set_level(logging.INFO)
    state = _patch_docker(monkeypatch, pull=True, tag=True, build=True)

    ok = hermes_workflow.handle_hermes_agent(_COMPOSE_ARGS, "/tmp/module", "hermes-agent")

    assert ok is True, "L1 pull + bare-tag + L2 build должен завершиться успешно"
    assert state["tag_args"] == [(_GHCR_L1, _BARE_TAG)], (
        f"D18 regression: bare-tag не создан после pull: {state['tag_args']}"
    )
    assert "[IMP:9][handle_hermes_agent][l1_pulled]" in caplog.text, "IMP:9 l1_pulled лог ожидался"
    assert "[IMP:9][handle_hermes_agent][built]" in caplog.text, "IMP:9 built лог ожидался"

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message and int(record.message.split("[IMP:")[1].split("]")[0]) >= 7:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
    logger.critical("[IMP:9][test] D18 PASS: bare-tag после GHCR pull создан")


# endregion FUNC_test_handle_hermes_agent_bare_tag_after_pull


# region FUNC_test_handle_hermes_agent_no_bare_tag_l2_build_fail
## @purpose — R5 negative (D18): docker_tag=False (bare-tag НЕ создан — точный вход бага) →
##            WARN «Cannot tag L1» + L2 build FAIL → handle возвращает False.
# 🧪 TRAP[TEST] · 2026-08-05 · NEGATIVE (R5) · D18 — без bare-tag L2 build FAIL
# · Scenario: pull=True, tag=False (bare-tag отсутствует) → build=False → return False + WARN
# · Last fail: 2026-08-04 — bare-tag отсутствовал после pull → L2 build «pull access denied» FAIL
# · Remove if: L1-доставка меняется (bare-tag перестаёт быть обязательным)
def test_handle_hermes_agent_no_bare_tag_l2_build_fail(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    """R5 negative (D18): без bare-tag L2 build FAIL → handle_hermes_agent False."""
    caplog.set_level(logging.INFO)
    state = _patch_docker(monkeypatch, pull=True, tag=False, build=False)

    ok = hermes_workflow.handle_hermes_agent(_COMPOSE_ARGS, "/tmp/module", "hermes-agent")

    assert ok is False, "D18 negative: без bare-tag L2 build обязан FAIL (return False)"
    assert state["tag_args"] == [(_GHCR_L1, _BARE_TAG)], "docker_tag обязан вызываться (даже при сбое)"
    assert "Cannot tag L1" in caplog.text, "WARN о неудачном tag ожидался"
    assert "[IMP:10][handle_hermes_agent][build_fail]" in caplog.text, "IMP:10 build_fail лог ожидался"
    logger.critical("[IMP:9][test] D18 NEGATIVE PASS: без bare-tag L2 build FAIL")


# endregion FUNC_test_handle_hermes_agent_no_bare_tag_l2_build_fail


# region FUNC_test_handle_hermes_agent_tag_before_build_order
## @purpose — D18 порядок фикса: docker_tag вызывается ДО L1→L2 build (иначе build не увидит bare-tag).
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D18 — порядок tag → build
# · Scenario: порядок вызовов = [tag, build] (tag первым)
# · Last fail: N/A (порядок — часть фикса 4c86c3b)
# · Remove if: последовательность pull→tag→build меняется
def test_handle_hermes_agent_tag_before_build_order(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    """D18: docker_tag вызывается ДО L1→L2 docker compose build."""
    caplog.set_level(logging.INFO)
    state = _patch_docker(monkeypatch, pull=True, tag=True, build=True)

    ok = hermes_workflow.handle_hermes_agent(_COMPOSE_ARGS, "/tmp/module", "hermes-agent")

    assert ok is True
    assert state["order"] == ["tag", "build"], f"D18: tag обязан идти ДО build, order={state['order']}"
    logger.critical("[IMP:9][test] D18 PASS: порядок tag → build соблюдён")


# endregion FUNC_test_handle_hermes_agent_tag_before_build_order


# region FUNC_test_handle_hermes_agent_pull_fail_build_l1_source
## @purpose — D18 альтернативный путь: pull FAIL → L1 build из source (docker-compose.base.yml),
##            docker_tag НЕ вызывается (tag нужен только pulled-образу).
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D18 — pull fail → build L1 из source
# · Scenario: docker_pull=False → build base_compose → tag НЕ вызывается → L2 build OK
# · Last fail: N/A (альтернативный путь fallback)
# · Remove if: L1 source-build путь меняется
def test_handle_hermes_agent_pull_fail_build_l1_source(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    """D18: pull fail → L1 build из source, bare-tag не требуется (tag не вызывается)."""
    caplog.set_level(logging.INFO)
    state = _patch_docker(monkeypatch, pull=False, tag=False, build=True)

    ok = hermes_workflow.handle_hermes_agent(_COMPOSE_ARGS, "/tmp/module", "hermes-agent")

    assert ok is True, "L1 source-build fallback должен завершиться успешно"
    assert state["tag_args"] == [], "docker_tag НЕ вызывается при source-build (нечему тегать)"
    assert "[IMP:9][handle_hermes_agent][l1_built]" in caplog.text, "IMP:9 l1_built лог ожидался"
    logger.critical("[IMP:9][test] D18 PASS: pull fail → L1 built из source")


# endregion FUNC_test_handle_hermes_agent_pull_fail_build_l1_source
