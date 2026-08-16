"""
# GREP_SUMMARY: test-deploy-modules-facade, provision, networks, volumes, proxy-net, fallback, ||-true, FAIL, T2.3, T2.4, latent-class-C, latent-class-F
# STRUCTURE: ▶ static source-анализ deploy-modules.sh → ◇ T2.4: НЕТ `|| true` на provision (FAIL [IMP:10] + exit 1) → ◇ T2.3: fallback = provisioner.py --scope networks+volumes из манифеста (НЕ proxy-net) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Регрессионные тесты латентных классов C/F (DevPlan 136 W2 T2.3/T2.4) — deploy-modules.sh
##           provision-блок: (C) fallback провижинит ВСЕ сети/volumes из platform-env.yaml, не только
##           proxy-net; (F) провал provision — видимый FAIL [IMP:10] + exit 1, а не `|| true` маскировка.
## @scope    Статический source-анализ shell-фасада (deploy-modules.sh ≤74 LOC). Shell-фасады не
##           юнит-тестируются subprocess'ом (правило 7 DevPlan 136 §1) — файловый/статический анализ.
## @invariants
##   - `|| true` после provision-вызовов ЗАПРЕЩЁН (маскировка провала = класс F)
##   - Fallback обязан вызывать provisioner.py --scope networks И --scope volumes (класс C)
##   - docker network create proxy-net (старый fallback) — ЗАПРЕЩЁН (только одна сеть)
##   - FATAL-логи [IMP:10] + exit 1 на провал provision
## @rationale  W1 T1.3 (test_bootstrap_dry_run) покрыл provision в dry-run state-machine; W2 T2.3/T2.4
##             покрывают сам shell-фасад deploy-modules.sh (источник маскировки).
## @changes    2026-08-05 | Created (DevPlan 136 W2)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEPLOY_MODULES_SH = _REPO_ROOT / "core" / "internal" / "bootstrap" / "deploy-modules.sh"


# region FUNC_test_provision_no_mask_or_true
## @purpose — T2.4 (класс F): provision-вызовы БЕЗ `|| true` — провал видимый, НЕ маскировка.
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T2.4 — `|| true` маскировка provision снята
# · Scenario: deploy-modules.sh НЕ содержит 'provision-environment.sh ... || true' / 'provisioner.py ... || true';
#   содержит FATAL [IMP:10] + exit 1 на провал
# · Last fail: 2026-08-05 — строки 31-32 `bash provision-environment.sh --scope networks || true` глотали exit-код
# · Remove if: provision перестаёт быть обязательным пре-шагом (например, move в state_machine целиком)
@ldd_trajectory
def test_provision_no_mask_or_true(caplog: pytest.LogCaptureFixture) -> None:
    """T2.4: НЕТ `|| true` на provision-вызовах; FAIL [IMP:10] + exit 1 (видимость провала)."""
    caplog.set_level(logging.INFO)
    src = _DEPLOY_MODULES_SH.read_text()

    # Маскирующие паттерны отсутствуют
    assert 'provision-environment.sh" --scope networks || true' not in src, (
        "T2.4 negative: маскировка networks `|| true` должна быть снята"
    )
    assert 'provision-environment.sh" --scope volumes || true' not in src, (
        "T2.4 negative: маскировка volumes `|| true` должна быть снята"
    )
    assert 'provisioner.py" --scope networks --platform-env' in src, "fallback должен использовать provisioner.py"

    # Видимый FAIL + exit 1 (Fail-Fast, класс F)
    assert "FATAL: network provision failed" in src, "T2.4: FATAL network provision FAIL обязан логироваться"
    assert "FATAL: volume provision failed" in src, "T2.4: FATAL volume provision FAIL обязан логироваться"
    assert src.count("exit 1") >= 4, "T2.4: на каждый провал provision — exit 1 (networks+volumes × main+fallback)"

    # FAIL-логи идут на stderr (видимость оператору)
    assert 'FATAL: network provision failed (scope=networks)" >&2' in src, "FATAL обязан идти на stderr"

    logger.info("[IMP:9][test][t2.4] deploy-modules.sh: `|| true` снят, провал provision → FAIL [IMP:10] + exit 1")


# endregion FUNC_test_provision_no_mask_or_true


# region FUNC_test_fallback_provisions_all_networks_volumes
## @purpose — T2.3 (класс C): fallback провижинит ВСЕ сети/volumes из манифеста (provisioner.py),
##            а не только proxy-net (старый docker network create proxy-net).
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T2.3 — fallback provision всех сетей/volumes
# · Scenario: fallback (provision-environment.sh отсутствует) вызывает provisioner.py --scope networks
#   И --scope volumes с --platform-env; docker network create proxy-net (старый паттерн) отсутствует
# · Last fail: 2026-08-05 — else-ветка создавала ТОЛЬКО proxy-net и 0 volumes (частичный provision)
# · Remove if: else-ветка fallback удалена полностью
@ldd_trajectory
def test_fallback_provisions_all_networks_volumes(caplog: pytest.LogCaptureFixture) -> None:
    """T2.3: fallback = provisioner.py --scope networks + --scope volumes (все из platform-env.yaml)."""
    caplog.set_level(logging.INFO)
    src = _DEPLOY_MODULES_SH.read_text()

    # Fallback-ветка: provisioner.py с обоими scopes + --platform-env (манифест)
    assert 'provisioner.py" --scope networks --platform-env' in src, (
        "T2.3: fallback обязан провижинить ВСЕ сети через provisioner.py --scope networks"
    )
    assert 'provisioner.py" --scope volumes --platform-env' in src, (
        "T2.3: fallback обязан провижинить ВСЕ volumes через provisioner.py --scope volumes"
    )
    assert '--platform-env "${PATHS_INTERNAL_DIR}/../../platform-env.yaml"' in src, (
        "T2.3: fallback передаёт путь к platform-env.yaml (манифест)"
    )

    # R5 negative: старый fallback (только proxy-net) удалён
    assert "docker network create proxy-net" not in src, (
        "T2.3 negative: старый fallback 'docker network create proxy-net' (только 1 сеть) удалён"
    )
    assert "docker network inspect proxy-net" not in src, "T2.3 negative: inspect-only proxy-net fallback удалён"

    logger.info("[IMP:9][test][t2.3] deploy-modules.sh: fallback провижинит все сети/volumes из манифеста")


# endregion FUNC_test_fallback_provisions_all_networks_volumes
