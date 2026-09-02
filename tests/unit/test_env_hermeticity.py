"""
# GREP_SUMMARY: test-env-hermeticity, T9, devplan-029, polluter, env-leak, hermetic-fixture, NODE_NAME
# STRUCTURE: ▶ module-level polluter (os.environ["NODE_NAME"]) → ◇ hermetic autouse fixture (delenv) →
#           test_polluter_stripped (RED если fixture снят) → test_deliberate_env_visible → ⎋ 2 pass
# region MODULE_CONTRACT
## @purpose  Unit tests для T9 (DevPlan 029) env-hermeticity autouse fixture
##           (tests/_conftest/env.py::hermetic_platform_env): платформенный env-ключ,
##           протёкший на import (эмуляция e2e-early-dotenv / NODE_NAME-утечки), НЕ виден
##           телу unit-теста; намеренный monkeypatch.setenv — виден.
## @scope    Pure unit tests — 0 subprocess, 0 Docker. Polluter устанавливает env на
##           module-import (до autouse fixture первого теста).
## @invariants
##   - test_polluter_stripped: без hermetic fixture → RED (env протёк в тело теста)
##   - test_deliberate_env_visible: setenv в теле побеждает (fixture чистит только фон)
## @changes 2026-09-02 · DevPlan 029 T9 — created
# endregion MODULE_CONTRACT
"""

import logging
import os

import pytest

logger = logging.getLogger(__name__)

# ── Module-level polluter: эмулирует ранний dotenv-load/import-time env-инжект,
#    который ДОЛЖЕН быть стёрт hermetic fixture до тела каждого unit-теста (T9).
os.environ["NODE_NAME"] = "polluted-at-import"


# region FUNC_test_polluter_stripped
## @purpose  T9 negative: NODE_NAME, протёкший на import, отсутствует в os.environ тела теста
##           (hermetic fixture удалил). Без fixture — RED (класс «NODE_NAME-утечка → ложный
##           зелёный DR-restore», rationale T9).
def test_polluter_stripped() -> None:
    """Import-time platform env pollution is invisible to the test body (T9)."""
    assert "NODE_NAME" not in os.environ, (
        "T9 FAIL: import-time platform env pollution leaked into unit test body (hermetic fixture missing)"
    )
    logger.info("[IMP:9][test_env_hermeticity] polluter stripped — hermetic env OK")


# endregion FUNC_test_polluter_stripped


# region FUNC_test_deliberate_env_visible
## @purpose  Намеренный monkeypatch.setenv в теле теста побеждает (fixture отработал ДО тела) —
##           env-контракт остаётся явным для тестов, которым ключ реально нужен.
def test_deliberate_env_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deliberate monkeypatch.setenv inside the test is visible to the body (T9)."""
    assert "NODE_NAME" not in os.environ, "background pollution должен быть стёрт до тела"
    monkeypatch.setenv("NODE_NAME", "intentional-node")
    assert os.environ["NODE_NAME"] == "intentional-node", "setenv в теле обязан побеждать"
    logger.info("[IMP:9][test_env_hermeticity] deliberate env visible — OK")


# endregion FUNC_test_deliberate_env_visible
