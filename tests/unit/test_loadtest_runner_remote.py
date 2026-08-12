# GREP_SUMMARY: loadtest runner-remote unit ship rsync trailing-slash build-rsync-push-cmd src-dir contract chmod container-write network docker-run
# STRUCTURE: ▶ fake run_subprocess (capture argv sequence) → ◇ ship('/x/loadtest') → ◇ argv[4]
#           '/x/loadtest/' (trailing slash) → ◇ ssh chmod -R a+rwX (BUG-6) → ◇ build_ssh_docker_run_cmd
#           --network (default host | shared-db-net) → ⎋
# region MODULE_CONTRACT
## @purpose  Unit-тесты remote-режима генератора (BUG-5/6, 146-m5/m6 + 148 TASK-11): ship() нормализует
##           src_dir с trailing slash — rsync копирует СОДЕРЖИМОЕ core/loadtest/ в
##           /tmp/loadtest-<ts>/ (не вложенную папку loadtest/), иначе контейнер
##           (workdir → /lt) не находит /lt/scenarios/<name>.py; после rsync — ssh
##           chmod -R a+rwX <remote_dir> (workdir root-owned → write-права для non-root
##           пользователя locust-образа, иначе PermissionError '/lt/results').
##           build_ssh_docker_run_cmd — docker-сеть (148 TASK-4/5): default '--network host'
##           (web/s3), '--network shared-db-net' для db (PostgreSQL только в docker-сети).
## @scope    Чистые функции core/internal/loadtest/runner_remote.py — subprocess
##           мокается (перехват argv-последовательности), без сети и rsync-бинарника.
## @invariants
##   - ship('/x/loadtest') → rsync-argv src = '/x/loadtest/' (trailing slash добавлен)
##   - ship('/x/loadtest/') → '/x/loadtest/' (rstrip + "/" — без двойного слэша)
##   - ship() выполняет 2 вызова: rsync-argv, затем ssh-argv с 'chmod -R a+rwX <remote_dir>'
##   - build_ssh_docker_run_cmd: default network → '--network host'; network=shared-db-net →
##     '--network shared-db-net' (без '--network host')
##   - LDD: caplog IMP:9 (Anti-Illusion Rule, .kilo/rules/testing.md)
## @rationale Trailing slash и root-owned workdir — хрупкие контракты rsync/docker (одна
##            буква или права ломают remote-режим); unit-тесты ловят регрессию ship()
##            (single-point fix BUG-5/6). --network — контракт доступа db-сценария к
##            shared-db-net (148): unit-тест фиксирует проброс сети в docker run.
## @changes  2026-08-11 | DevPlan 146-m5 BUG-5 — Created
## @changes  2026-08-11 | DevPlan 146-m6 BUG-6 — chmod-контракт + тест последовательности
## @changes  2026-08-12 | DevPlan 148 TASK-11 — network-тесты (--network host | shared-db-net)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from types import SimpleNamespace

from core.internal.loadtest import runner_remote

logger = logging.getLogger(__name__)

# Базовые kwargs build_ssh_docker_run_cmd (148 TASK-11) — immutable, module-level (RUF012)
_DOCKER_RUN_KWARGS: dict = {
    "image": "locustio/locust:2.32.10",
    "cpus": "2",
    "remote_workdir": "/tmp/loadtest-123",
    "env": {"LT_ENABLED": "true"},
    "locust_args": ["-f", "/lt/scenarios/web.py", "--headless"],
}


# region HELPER__capture_run_subprocess
def _capture_run_subprocess(monkeypatch, captured: dict) -> None:
    """Подмена run_subprocess: перехват argv-последовательности в captured (без rsync/ssh).

    ▶ ┌monkeypatch, captured┐ → ○ monkeypatch.setattr(run_subprocess, fake) → ⎋ None

    ## @purpose  Hermetic-прогон ship(): возвращаем rc=0 для каждого вызова; в captured:
    ##            "cmds" — список всех argv (порядок вызовов), "cmd" — последний argv.
    ## @io — ⇥ monkeypatch, captured: dict {"cmds": list[list[str]], "cmd": list[str]} → ⎋ None
    ## @complexity — O(1)
    """

    def _fake(cmd, timeout, check, non_fatal):
        captured.setdefault("cmds", []).append(cmd)
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(runner_remote, "run_subprocess", _fake)


# endregion HELPER__capture_run_subprocess


# region HELPER_assert_ldd_imp9
def _assert_ldd_imp9(caplog) -> None:
    """Печать LDD-траектории IMP:7-10 + assert наличия IMP:9 (Anti-Illusion Rule).

    ## @purpose — Единая точка LDD-телеметрии тестов runner_remote.
    ## @io — ⇥ caplog → ⎋ None (assert found IMP:9)
    """
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
            if "[IMP:9]" in record.message:
                found = True
    print("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 log found"


# endregion HELPER_assert_ldd_imp9


# region TEST_ship_trailing_slash
# 🧪 TRAP[TEST] · Scenario: ship нормализует src_dir с trailing slash (BUG-5, 146-m5)
# · Regression: rsync без '/' → вложенная папка remote_dir/loadtest/ → контейнер не находит
# ·   /lt/scenarios/<name>.py (боевой s3-прогон: Could not find '/lt/scenarios/s3.py', rc=1)
# · Last fail: 2026-08-11 — LOAD_RUNNER=node s3 на tronyx-vps (ls /tmp/loadtest-*/ → loadtest)
# · Remove if: механизм доставки сценариев на ноду изменён (не rsync)
def test_ship_src_dir_trailing_slash(monkeypatch, caplog) -> None:
    """ship('/x/loadtest') без слэша → rsync-argv src = '/x/loadtest/' (содержимое, не папка)."""
    caplog.set_level(logging.INFO)
    captured: dict = {}
    _capture_run_subprocess(monkeypatch, captured)
    runner_remote.ship("203.0.113.10", "root", "/x/loadtest", "/tmp/loadtest-123", timeout=10)
    rsync_argv = captured["cmds"][0]  # первый вызов — rsync push (второй — ssh chmod, BUG-6)
    logger.info("[IMP:9][test][ship] src нормализован: %s (rsync argv[4])", rsync_argv[4])
    _assert_ldd_imp9(caplog)
    assert rsync_argv[0] == "rsync"
    assert rsync_argv[4] == "/x/loadtest/"  # trailing slash обязателен (BUG-5)
    assert rsync_argv[5] == "root@203.0.113.10:/tmp/loadtest-123"


# endregion TEST_ship_trailing_slash


# region TEST_ship_no_double_slash
# 🧪 TRAP[TEST] · Scenario: уже нормализованный src не получает двойной слэш
# · Regression: rstrip("/") + "/" задвоит '/' на входе с trailing slash (// ломает rsync)
# · Last fail: N/A (new) — 146-m5 BUG-5 fix
# · Remove if: нормализация src_dir в ship() изменена
def test_ship_src_dir_no_double_slash(monkeypatch, caplog) -> None:
    """ship('/x/loadtest/') (уже со слэшем) → argv src = '/x/loadtest/' (без '//')."""
    caplog.set_level(logging.INFO)
    captured: dict = {}
    _capture_run_subprocess(monkeypatch, captured)
    runner_remote.ship("203.0.113.10", "root", "/x/loadtest/", "/tmp/loadtest-123", timeout=10)
    rsync_argv = captured["cmds"][0]  # первый вызов — rsync push
    logger.info("[IMP:9][test][ship] src с trailing slash: %s (rsync argv[4])", rsync_argv[4])
    _assert_ldd_imp9(caplog)
    assert rsync_argv[4] == "/x/loadtest/"
    assert "//" not in rsync_argv[4]


# endregion TEST_ship_no_double_slash


# region TEST_ship_chmod_after_rsync
# 🧪 TRAP[TEST] · Scenario: ship после rsync выполняет ssh chmod -R a+rwX <remote_dir> (BUG-6, 146-m6)
# · Regression: root-owned workdir (mode 755) без chmod → non-root контейнер не создаёт
# ·   /lt/results (csv_prefix) → PermissionError: [Errno 13] Permission denied: '/lt/results'
# · Last fail: 2026-08-11 — LOAD_RUNNER=node s3 на tronyx-vps (после фикса BUG-5)
# · Remove if: работа контейнера переведена на root-пользователя или chown-механизм
def test_ship_chmod_after_rsync(monkeypatch, caplog) -> None:
    """ship(): 2 вызова — rsync-argv, затем ssh-argv с 'chmod -R a+rwX <remote_dir>'."""
    caplog.set_level(logging.INFO)
    captured: dict = {}
    _capture_run_subprocess(monkeypatch, captured)
    runner_remote.ship("203.0.113.10", "root", "/x/loadtest", "/tmp/loadtest-123", timeout=10)
    logger.info("[IMP:9][test][ship] последовательность: rsync → ssh chmod (%d вызовов)", len(captured["cmds"]))
    _assert_ldd_imp9(caplog)
    cmds = captured["cmds"]
    assert len(cmds) == 2  # rsync push + ssh chmod
    assert cmds[0][0] == "rsync"
    assert cmds[1][0] == "ssh"
    assert cmds[1][-2] == "root@203.0.113.10"
    assert cmds[1][-1] == "chmod -R a+rwX /tmp/loadtest-123"


# endregion TEST_ship_chmod_after_rsync


# region TEST_docker_run_network
# 🧪 TRAP[TEST] · Scenario: --network в docker run (148 TASK-4/5: default host; shared-db-net для db)
# · Regression: network не пробрасывается в build_ssh_docker_run_cmd → db-контейнер НЕ в
# ·   shared-db-net → postgres недоступен (NO ports: directive) → ConnectionError на ноде
# · Last fail: N/A (new) — 148 TASK-4 (docker run жёстко host)
# · Remove if: механизм сети генератора заменён (не --network docker run)
class TestDockerRunNetwork:
    def test_docker_run_network_default_host(self, caplog) -> None:
        """Без параметра network → '--network host' (web/s3 не меняются, backward-compat)."""
        caplog.set_level(logging.INFO)
        cmd = runner_remote.build_ssh_docker_run_cmd(**_DOCKER_RUN_KWARGS)
        logger.info("[IMP:9][test][network] docker run default: %s", cmd[:160])
        _assert_ldd_imp9(caplog)
        assert "--network host" in cmd

    def test_docker_run_network_shared_db_net(self, caplog) -> None:
        """network="shared-db-net" → '--network shared-db-net' (db-сценарий, 148 TASK-4)."""
        caplog.set_level(logging.INFO)
        cmd = runner_remote.build_ssh_docker_run_cmd(network="shared-db-net", **_DOCKER_RUN_KWARGS)
        logger.info("[IMP:9][test][network] docker run shared-db-net: %s", cmd[:160])
        _assert_ldd_imp9(caplog)
        assert "--network shared-db-net" in cmd
        assert "--network host" not in cmd


# endregion TEST_docker_run_network
