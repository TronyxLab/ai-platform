"""
# GREP_SUMMARY: test-modules-mk-status-guard, F9, NODE-guard, status-make-target, fail-loud, remote-hint, makefile, modules-mk
# STRUCTURE: ▶ run `make -f makefiles/modules.mk status` with NODE env + fake docker → ◇ NODE=<prod> → exit 1 + hint | ◇ NODE=local → compose ps (fake docker) | ◇ no NODE → compose ps → ⎋ assertions + LDD
# region MODULE_CONTRACT
## @purpose  Unit tests for makefiles/modules.mk status NODE-guard (DevPlan 031 T6 / F9).
##           `make status NODE=<remote>` молча игнорировал NODE и показывал ПУСТУЮ таблицу
##           локального docker compose вместо состояния ноды (F9, ночной прогон 2026-09-03) —
##           честность: fail-loud с подсказкой (project-status/e2e-verify), зеркало
##           healthcheck-контракта (F-016).
## @scope    Makefile-рецепт `status` в makefiles/modules.mk — исполняется через subprocess make
##           (статический контракт реального make-таргета; прецедент healthcheck-guard тестов).
## @invariants
##   - subprocess — ТОЛЬКО для исполнения make-таргета (не бизнес-логика)
##   - NODE=<n> (≠local) → exit 1 + hint в stderr; docker compose НЕ вызывается (fake docker)
##   - NODE=local / пустой → рецепт выполняется (fake docker compose ps → exit 0)
##   - SHELL=/bin/bash — [[ ]] в рецепте (канон root Makefile SHELL := /bin/bash); standalone make
##     без root Makefile по умолчанию /bin/sh (dash на Linux не поддерживает [[)
## @changes 2026-09-03 · DevPlan 031 T6 (F9) — создан
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
_MAKEFILE: Path = _REPO_ROOT / "makefiles" / "modules.mk"

pytestmark = pytest.mark.static_audit


# region HELPERS


def _run_status(node: str | None, fake_docker_dir: Path) -> subprocess.CompletedProcess:
    """Run `make SHELL=/bin/bash -f makefiles/modules.mk status` with NODE env + fake docker."""
    env = dict(os.environ)
    env.pop("NODE", None)
    if node is not None:
        env["NODE"] = node
    # fake docker в начале PATH: compose ps → exit 0 (рецепт не зависит от реального docker)
    env["PATH"] = str(fake_docker_dir) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        ["make", "SHELL=/bin/bash", "-f", str(_MAKEFILE), "status"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(_REPO_ROOT),
        check=False,
    )


# endregion HELPERS


# region TEST_F9_STATUS_NODE_GUARD


# 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 031 T6 / F9 · NODE=<remote> → fail-loud + hint
# · Scenario: оператор `make status NODE=asi-team-vps` на dev-машине: NODE молча игнорировался →
#   пустая таблица локального docker compose ≠ состояние ноды (F9). Теперь exit 1 + hint
#   (project-status/e2e-verify), рецепт docker compose ps НЕ выполняется.
# · Last fail: 2026-09-03 — make status NODE=asi-team-vps вернул пустую локальную таблицу (F9)
# · Remove if: status получает настоящий remote-режим
def test_status_node_param_requires_remote_mode(tmp_path: Path) -> None:
    """F9: NODE≠local → fail-loud с подсказкой, docker compose НЕ вызывается."""
    fake_docker = tmp_path / "bin"
    fake_docker.mkdir()
    docker_log = tmp_path / "docker-calls"
    (fake_docker / "docker").write_text(
        f"#!/bin/sh\necho called >> {docker_log}\nexit 0\n",
        encoding="utf-8",
    )
    (fake_docker / "docker").chmod(0o755)

    result = _run_status(node="asi-team-vps", fake_docker_dir=fake_docker)
    # GNU make переводит recipe-fail (exit 1) в собственный exit 2 — fail-loud контракт = make НЕ
    # завершился успехом (healthcheck-прецедент: entrypoint exit 1 → make exit 2).
    assert result.returncode != 0, f"F9 FAIL: NODE=<prod> должен дать ненулевой exit, получен {result.returncode}"
    assert "NODE=asi-team-vps задан" in (result.stderr or result.stdout), f"нет упоминания NODE: {result.stderr}"
    assert "project-status" in (result.stderr or result.stdout), f"нет подсказки project-status: {result.stderr}"
    assert not docker_log.exists(), f"F9: docker compose ps не должен вызываться: {result.stdout}"
    logger.info("[IMP:9][test][F9] NODE=<remote> → fail-loud + hint, docker НЕ вызван PASS")


# 🧪 TRAP[TEST] · REGRESSION · DevPlan 031 T6 / F9 · NODE=local → рецепт исполняется
# · Scenario: явный NODE=local — локальная проверка стека сохраняется (dev-машина/сама нода)
# · Remove if: status NODE-семантика изменится
def test_status_node_local_runs_compose(tmp_path: Path) -> None:
    """F9: NODE=local → рецепт выполняется (fake docker compose ps вызывается)."""
    fake_docker = tmp_path / "bin"
    fake_docker.mkdir()
    docker_log = tmp_path / "docker-calls"
    (fake_docker / "docker").write_text(
        f"#!/bin/sh\necho called >> {docker_log}\nexit 0\n",
        encoding="utf-8",
    )
    (fake_docker / "docker").chmod(0o755)

    result = _run_status(node="local", fake_docker_dir=fake_docker)
    assert result.returncode == 0, f"NODE=local должен выполнить рецепт, stderr={result.stderr}"
    assert docker_log.exists(), f"docker compose ps должен быть вызван: {result.stdout}"
    logger.info("[IMP:9][test][F9] NODE=local → docker compose ps выполнен PASS")


# 🧪 TRAP[TEST] · REGRESSION · DevPlan 031 T6 / F9 · без NODE → рецепт исполняется
# · Scenario: make status без NODE (прежнее поведение, напр. на самой ноде) → локальный прогон
# · Remove if: status NODE-семантика изменится
def test_status_no_node_runs_compose(tmp_path: Path) -> None:
    """F9: без NODE → docker compose ps вызывается (обратная совместимость)."""
    fake_docker = tmp_path / "bin"
    fake_docker.mkdir()
    docker_log = tmp_path / "docker-calls"
    (fake_docker / "docker").write_text(
        f"#!/bin/sh\necho called >> {docker_log}\nexit 0\n",
        encoding="utf-8",
    )
    (fake_docker / "docker").chmod(0o755)

    result = _run_status(node=None, fake_docker_dir=fake_docker)
    assert result.returncode == 0, f"без NODE рецепт должен выполниться, stderr={result.stderr}"
    assert docker_log.exists(), f"docker compose ps должен быть вызван: {result.stdout}"
    logger.info("[IMP:9][test][F9] без NODE → docker compose ps выполнен PASS")


# endregion TEST_F9_STATUS_NODE_GUARD
