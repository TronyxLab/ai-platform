"""
# GREP_SUMMARY: test-llm-provision-lifecycle, llm-keys, provision-llm, cross-layer, state_machine, registry-update, paired-call, T2.5
# STRUCTURE: ▶ static анализ 3 файлов → ◇ deploy-modules.sh: НЕ вызывает provision-llm (cross-layer запрещён) → ◇ phases/docker.py: _registry_step_llm_provision вызывается из phase_registry_update → ◇ _registry_step_llm_provision рендерит config_renderer + провижинит provision-llm.sh → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Регрессионный тест-гейт T2.5 (DevPlan 136 W2, класс C): «provision elsewhere» TRAP[CROSS-LAYER]
##           в deploy-modules.sh ↔ парный вызов llm-keys провижининга в state_machine post-deploy lifecycle.
##           Подтверждает: deploy-modules.sh НЕ вызывает provision-llm.sh (cross-layer internal→entrypoints
##           запрещён), а φ11 registry_update (_registry_step_llm_provision) реально провижинит llm-keys
##           (config_renderer → litellm-config.yml + provision-llm.sh → virtual keys).
## @scope    Статический source-анализ deploy-modules.sh + phases/docker.py (shell/Python фасады
##           не юнит-тестируются subprocess'ом — статический анализ по канону W1 test_deploy_mk_chain).
## @invariants
##   - deploy-modules.sh НЕ содержит 'provision-llm' (internal не вызывает entrypoints — cross-layer gate)
##   - phases/docker.py: _registry_step_llm_provision определён и вызывается из phase_registry_update
##   - _registry_step_llm_provision вызывает config_renderer.py (рендер) и provision-llm.sh (keys)
## @rationale  TRAP[CROSS-LAYER] (deploy-modules.sh:64-65) заявляет, что провижининг llm-keys живёт в
##             state_machine post-deploy lifecycle — тест фиксирует это утверждение кодом (парный вызов).
## @changes    2026-08-05 | Created (DevPlan 136 W2 T2.5)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEPLOY_MODULES_SH = _REPO_ROOT / "core" / "internal" / "bootstrap" / "deploy-modules.sh"
_PHASES_DOCKER_PY = _REPO_ROOT / "core" / "internal" / "bootstrap" / "lifecycle" / "phases" / "docker.py"


# region FUNC_test_deploy_modules_no_llm_provision_cross_layer
## @purpose — deploy-modules.sh НЕ вызывает provision-llm.sh (cross-layer internal→entrypoints запрещён).
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T2.5 — llm-keys НЕ провижинятся из deploy-modules.sh
# · Scenario: deploy-modules.sh не содержит 'provision-llm'; содержит TRAP[CROSS-LAYER] комментарий
#   со ссылкой на state_machine post-deploy lifecycle
# · Last fail: 2026-07-31 — deploy-modules.sh вызывал provision-llm.sh (cross-layer violation, удалён)
# · Remove if: llm-keys провижининг возвращается в deploy-modules.sh (тогда парный вызов в state_machine удалить)
@ldd_trajectory
def test_deploy_modules_no_llm_provision_cross_layer(caplog: pytest.LogCaptureFixture) -> None:
    """T2.5: deploy-modules.sh НЕ вызывает provision-llm (cross-layer internal→entrypoints запрещён)."""
    caplog.set_level(logging.INFO)
    src = _DEPLOY_MODULES_SH.read_text()

    # Активные (некомментарные) строки НЕ содержат provision-llm — cross-layer вызов запрещён.
    # (TRAP[CROSS-LAYER] комментарий упоминает 'provision-llm.sh call REMOVED' — это история, не вызов.)
    active_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert not any("provision-llm" in line for line in active_lines), (
        "T2.5: deploy-modules.sh не должен вызывать provision-llm.sh (cross-layer internal→entrypoints): "
        + ", ".join(line for line in active_lines if "provision-llm" in line)
    )
    assert "TRAP[CROSS-LAYER]" in src, "T2.5: TRAP[CROSS-LAYER] маркер обязан присутствовать"
    assert "state_machine.py" in src, "T2.5: TRAP[CROSS-LAYER] обязан указывать на state_machine.py"

    logger.info(
        "[IMP:9][test][t2.5] deploy-modules.sh: 0 вызовов provision-llm (cross-layer чист) — TRAP[CROSS-LAYER] на месте"
    )


# endregion FUNC_test_deploy_modules_no_llm_provision_cross_layer


# region FUNC_test_state_machine_llm_provision_paired_call
## @purpose — Парный вызов: φ11 registry_update реально провижинит llm-keys через _registry_step_llm_provision.
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T2.5 — парный вызов llm-keys в state_machine
# · Scenario: phases/docker.py содержит _registry_step_llm_provision, вызываемый из phase_registry_update;
#   хелпер рендерит config_renderer.py → litellm-config и вызывает provision-llm.sh
# · Last fail: 2026-08-05 — deploy-modules.sh удалил вызов, требовалось подтвердить парный вызов в lifecycle
# · Remove if: llm-keys провижининг перемещается из φ11 registry_update
@ldd_trajectory
def test_state_machine_llm_provision_paired_call(caplog: pytest.LogCaptureFixture) -> None:
    """T2.5: state_machine post-deploy lifecycle (φ11 registry_update) провижинит llm-keys."""
    caplog.set_level(logging.INFO)
    src = _PHASES_DOCKER_PY.read_text()

    # 1. Хелпер определён и вызывается из phase_registry_update (парный вызов)
    assert "def _registry_step_llm_provision" in src, "T2.5: _registry_step_llm_provision обязан существовать"
    assert "_registry_step_llm_provision(core_dir)" in src, (
        "T2.5: _registry_step_llm_provision обязан вызываться из phase_registry_update"
    )

    # 2. Хелпер реально провижинит: рендер config + virtual keys
    assert "config_renderer.py" in src, "T2.5: llm-keys провижининг рендерит litellm-config (config_renderer.py)"
    assert "provision-llm.sh" in src, "T2.5: llm-keys провижининг вызывает provision-llm.sh (virtual keys)"

    # 3. Документированная связь с TRAP[CROSS-LAYER] (post-deploy lifecycle step)
    assert "LLM virtual keys provisioned" in src, "T2.5: [IMP:9] лог успешного провижининга llm-keys"

    logger.info("[IMP:9][test][t2.5] state_machine φ11 registry_update: парный вызов llm-keys провижининга подтверждён")


# endregion FUNC_test_state_machine_llm_provision_paired_call
