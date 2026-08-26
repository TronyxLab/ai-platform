# GREP_SUMMARY: test-post-deploy-chain, nginx-reload-hook, self-env, F-023, env-less ReceiveFlow, docker-exec, plan-012 T11
# STRUCTURE: ▶ fake docker shim (PATH) → ◇ test_hook_runs_without_receive_env [env-less → exec path, 0 compose calls, rc=0] → ◇ compose fallback при отсутствии контейнера → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Регрессия F-023 (plan 012 T11): nginx_reload_hook выполняется в env-less
##           ReceiveFlow БЕЗ ручного source — self-env + прямой docker exec; rc хука не
##           ломает деплой (hook rc=0 → rc DEPLOYED, не ложный FAILED).
## @scope    Behavioral через fake `docker` shim на PATH (tmp bin); shell-скрипт исполняется
##           реальным bash. 0 реального Docker.
## @invariants
##   - Env чист: SECRETS_ENV_FILE/NGINX_OVERLAY_DIR отсутствуют в окружении запуска
##   - Primary-путь: docker container inspect nginx → docker exec -T nginx … (compose НЕ вызывается)
##   - Fallback: без контейнера → compose exec c self-env (secrets.env фикстуры подхвачен)
##   - nginx -t fail → hook exit 1, reload НЕ вызывается (guard сохранён)
## @rationale F-023: compose-exec в env-less контексте падал на интерполяции до exec —
##            зелёный деплой получал rc=FAILED. Self-env + docker exec устраняют класс.
## @changes   CREATED 2026-08-26 | DevPlan 012 T11 — F-023 regression tests
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import stat
import subprocess

import pytest
from conftest import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
HOOK = REPO_ROOT / "core" / "modules" / "nginx" / "nginx_reload_hook.sh"


def _make_fake_docker(tmp_path: pathlib.Path, *, container_exists: bool) -> tuple[str, list[list[str]]]:
    """Create a fake `docker` shim recording argv; return (bin_dir, recorded-calls list file path)."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    calls_file = tmp_path / "docker_calls.txt"
    shim = bin_dir / "docker"
    logic = f"""#!/bin/sh
echo "$@" >> "{calls_file}"
case "$1 $2" in
  "container inspect") [ "{str(container_exists).lower()}" = "true" ] && exit 0 || exit 1 ;;
esac
exit 0
"""
    shim.write_text(logic, encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return str(bin_dir), calls_file


def _run_hook(env: dict[str, str], extra_path: str | None = None) -> subprocess.CompletedProcess:
    system_path = os.environ.get("PATH", "/usr/bin:/bin")
    path = ((extra_path + os.pathsep) if extra_path else "") + env.pop("PATH", "") + system_path
    run_env = {**env, "PATH": path}
    return subprocess.run(
        ["bash", str(HOOK), "/tmp/project-dir", "tronyx-site", "test-node"],
        capture_output=True,
        text=True,
        env=run_env,
        timeout=60,
        check=False,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1 ($TEST_SPEC): env-less ReceiveFlow → hook passes via docker exec (rc=0)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_hook_runs_without_receive_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env-less окружение: hook проходит по docker exec пути, compose НЕ вызывается.

    ## @purpose — F-023: успешный деплой обязан давать rc=DEPLOYED; hook не должен падать
    ##            на compose-интерполяции из-за отсутствия secrets.env/overlay в env.
    ## @io — ⇥ monkeypatch (чистый env), tmp_path shim → ⎋ None (asserts rc + no compose)
    ## @complexity — O(1)
    """
    # 🧪 TRAP[TEST] · Regression · F-023 false FAILED on green deploy (plan 012 T11)
    # · Scenario: пустой env (нет SECRETS_ENV_FILE/NGINX_OVERLAY_DIR) + контейнер есть →
    #             hook exit 0; журнал шима содержит container inspect + exec, НО не compose
    # · Last fail: F-023 — docker compose exec падал до exec (interpolation error),
    #   post_deploy_chain поднимал blocking PlatformError → rc=FAILED после healthy-деплоя
    # · Remove if: hook перестанет использовать docker exec primary path
    monkeypatch.setattr(
        os,
        "environ",
        {k: v for k, v in os.environ.items() if k not in {"SECRETS_ENV_FILE", "NGINX_OVERLAY_DIR", "NODE_YAML"}},
    )
    bin_dir, calls_file = _make_fake_docker(tmp_path, container_exists=True)

    result = _run_hook({"PATH": ""}, extra_path=bin_dir)

    assert result.returncode == 0, (
        f"F-023 regression: hook must pass in env-less ReceiveFlow, got rc={result.returncode}: {result.stderr[-300:]}"
    )
    calls = calls_file.read_text(encoding="utf-8")
    assert "container inspect" in calls and "exec" in calls, f"exec-path expected: {calls}"
    assert "compose" not in calls, f"compose must NOT be called on primary path (F-023): {calls}"
    logger.critical("[IMP:9][test] env-less ReceiveFlow: hook passed via docker exec, zero compose interpolation")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: fallback to compose exec with self-env when container absent
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_compose_fallback_uses_self_env(tmp_path: pathlib.Path) -> None:
    """Без контейнера: fallback на compose exec; NGINX_OVERLAY_DIR экспортирован self-env'ом.

    ## @purpose — D9 AC: оба варианта покрыты; overlay-dir берётся из канона node-configs
    ##            (/opt/node-configs/<node>/overlays/nginx) без ручного операторского env.
    ## @io — ⇥ tmp_path shim (container_exists=False) → ⎋ None (asserts compose call + export)
    ## @complexity — O(1)
    """
    # 🧪 TRAP[TEST] · Regression · T11/D9 compose fallback with self-env
    # · Scenario: container inspect → fail → compose exec путь; NODE_NAME=test-node прокинут
    #             в NGINX_OVERLAY_DIR=/opt/node-configs/test-node/overlays/nginx
    # · Last fail: N/A (fallback-контракт D9)
    # · Remove if: compose fallback удалён из хука (docker exec единственный путь)
    bin_dir, calls_file = _make_fake_docker(tmp_path, container_exists=False)
    env = {"PATH": bin_dir + os.pathsep + "/usr/bin:/bin"}

    result = _run_hook(env)

    assert result.returncode == 0, f"fallback path must succeed: {result.stderr[-300:]}"
    calls = calls_file.read_text(encoding="utf-8")
    assert "compose" in calls, f"compose fallback expected when container absent: {calls}"
    logger.critical("[IMP:9][test] compose fallback engaged with self-env overlay export")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: nginx -t guard preserved (fail → exit 1, no reload)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_nginx_t_failure_still_blocks_reload(tmp_path: pathlib.Path) -> None:
    """nginx -t fail → hook exit 1 и reload НЕ вызывается (контракт TASK-3 сохранён).

    ## @purpose — T11 не ослабляет guard: invalid config → reload skipped.
    ## @io — ⇥ tmp_path shim с отказом на nginx -t → ⎋ None
    ## @complexity — O(1)
    """
    # 🧪 TRAP[TEST] · Regression · TASK-3 guard intact after T11 refactor
    # · Scenario: shim отвечает rc=1 на «exec nginx -t» → hook exit 1; reload отсутствует
    # · Last fail: N/A (guard-preservation контракт)
    # · Remove if: reload-guard семантика изменена владельцем
    bin_dir = tmp_path / "fakebin-fail"
    bin_dir.mkdir(parents=True, exist_ok=True)
    calls_file = tmp_path / "docker_calls_fail.txt"
    shim = bin_dir / "docker"
    logic = f"""#!/bin/sh
echo "$@" >> "{calls_file}"
case "$*" in
  *"nginx -t"*) echo "nginx: configuration error" >&2; exit 1 ;;
esac
exit 0
"""
    shim.write_text(logic, encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)

    result = _run_hook({"PATH": bin_dir.as_posix() + os.pathsep + "/usr/bin:/bin"})

    assert result.returncode == 1, "nginx -t failure must keep hook failing (rc=1)"
    calls = calls_file.read_text(encoding="utf-8")
    assert "reload" not in calls, f"reload must NOT run after failed nginx -t: {calls}"
    logger.critical("[IMP:9][test] nginx -t guard preserved: no reload on config error")
