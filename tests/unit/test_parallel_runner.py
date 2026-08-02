# GREP_SUMMARY: parallel-runner, unit, drain, rollback, fork, slot-waiter, deploy-group, D1, docker-orchestrator-decomposition
# STRUCTURE: ▶ mock os.waitpid/fork → ◇ _drain_completed_count [WNOHANG done|fail|ChildProcessError] → ◇ _drain_all_count [blocking done|error] → ◇ deploy_docker_group [rollback on fail] → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты для core/internal/bootstrap/deploy/parallel_runner.py (DevPlan 118 D1) —
##           fork-параллелизм, drain-примитивы, atomic rollback deploy_docker_group.
## @scope    Pure unit (mock os.waitpid/os.fork) — никакого реального fork-деплоя в тестах.
## @invariants
##   - _drain_completed_count: WNOHANG-семантика (готовые снимаются, незавершённые остаются)
##   - _drain_all_count: blocking-семантика (все снимаются, pids.clear())
##   - deploy_docker_group rollback: при fail → docker compose down для всех модулей группы
##   - LDD: IMP:9 лог в успешном сценарии
## @rationale DevPlan 118 D1 $TEST_SPEC: «существующие test_deploy_orchestrator + новые unit на
##            parallel_runner (drain, rollback)».
## @changes  2026-08-02 | DevPlan 118 D1 — создан
# endregion MODULE_CONTRACT

import logging
import os
from unittest import mock

from core.internal.bootstrap.deploy import parallel_runner

logger = logging.getLogger(__name__)


# region TEST_drain_completed_count
class TestDrainCompletedCount:
    # 🧪 TRAP[TEST] · 2026-08-02 · UNIT · _drain_completed_count — WNOHANG-снятие успешных детей
    def test_completed_success(self, caplog) -> None:
        """Завершившийся дочерний процесс (exit 0) снимается WNOHANG → deployed=1."""
        caplog.set_level(logging.DEBUG)
        with mock.patch.object(
            parallel_runner.os, "waitpid", return_value=(111, os.waitstatus_to_exitcode(0) and 0)
        ) as m:
            # returncode=0 → WIFEXITED True + WEXITSTATUS 0
            m.return_value = (111, 0)
            deployed, failed, names = parallel_runner.drain_completed_count([111], {111: "mod1"})
        logger.info("[IMP:9][test] drain_completed success: deployed=%d failed=%d names=%s", deployed, failed, names)
        assert deployed == 1
        assert failed == 0
        assert names == []
        assert "mod1" not in {111: "mod1"}  # pid_to_name.pop вызван (мутация)

    # 🧪 TRAP[TEST] · 2026-08-02 · UNIT · _drain_completed_count — WNOHANG-снятие failed детей
    def test_completed_failure(self, caplog) -> None:
        """Завершившийся дочерний процесс (exit 1) снимается → failed=1 + имя в failed_names."""
        caplog.set_level(logging.DEBUG)
        with mock.patch.object(parallel_runner.os, "waitpid") as m:
            m.return_value = (222, 1)  # WIFEXITED True, WEXITSTATUS 1
            deployed, failed, names = parallel_runner.drain_completed_count([222], {222: "mod2"})
        logger.info("[IMP:9][test] drain_completed failure: deployed=%d failed=%d names=%s", deployed, failed, names)
        assert deployed == 0
        assert failed == 1
        assert names == ["mod2"]

    # 🧪 TRAP[TEST] · 2026-08-02 · UNIT · _drain_completed_count — ChildProcessError → fail
    def test_child_process_error(self, caplog) -> None:
        """ChildProcessError (нет такого ребёнка) → fail=1, имя сохраняется."""
        caplog.set_level(logging.DEBUG)
        with mock.patch.object(parallel_runner.os, "waitpid", side_effect=ChildProcessError):
            deployed, failed, names = parallel_runner.drain_completed_count([333], {333: "mod3"})
        logger.info("[IMP:9][test] drain_completed ChildProcessError: failed=%d names=%s", failed, names)
        assert deployed == 0
        assert failed == 1
        assert names == ["mod3"]


# endregion TEST_drain_completed_count


# region TEST_drain_all_count
class TestDrainAllCount:
    # 🧪 TRAP[TEST] · 2026-08-02 · UNIT · _drain_all_count — blocking-снятие всех детей
    def test_drain_all(self, caplog) -> None:
        """Blocking drain: все pids снимаются, pids.clear()."""
        caplog.set_level(logging.DEBUG)
        with mock.patch.object(parallel_runner.os, "waitpid", return_value=(0, 0)):
            pids = [1, 2]
            pid_to_name = {1: "a", 2: "b"}
            deployed, failed, names = parallel_runner.drain_all_count(pids, pid_to_name)
        logger.info("[IMP:9][test] drain_all: deployed=%d failed=%d pids=%s", deployed, failed, pids)
        assert deployed == 2
        assert failed == 0
        assert pids == []  # cleared
        assert names == []

    # 🧪 TRAP[TEST] · 2026-08-02 · UNIT · _drain_all_count — ChildProcessError → fail
    def test_drain_all_error(self, caplog) -> None:
        """ChildProcessError → failed=1, имя в failed_names."""
        caplog.set_level(logging.DEBUG)
        with mock.patch.object(parallel_runner.os, "waitpid", side_effect=ChildProcessError):
            pids = [7]
            deployed, failed, names = parallel_runner.drain_all_count(pids, {7: "mod7"})
        logger.info("[IMP:9][test] drain_all error: failed=%d names=%s", failed, names)
        assert deployed == 0
        assert failed == 1
        assert names == ["mod7"]


# endregion TEST_drain_all_count


# region TEST_deploy_docker_group_rollback
class TestDeployDockerGroupRollback:
    # 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · atomic rollback при fail (W5-E1, DevPlan 118 D1)
    # · Last fail: N/A (новый тест на вынесенный parallel_runner)
    # · Remove if: atomic rollback удаляется
    def test_rollback_on_failure(self, tmp_path, caplog, monkeypatch) -> None:
        """Один модуль fail → docker compose down для всех модулей группы (rollback)."""
        caplog.set_level(logging.DEBUG)
        modules_dir = tmp_path / "modules"
        mod_dir = modules_dir / "mod1"
        mod_dir.mkdir(parents=True)
        (mod_dir / "docker-compose.yml").write_text("services:\n  mod1:\n    image: x\n")

        # fork → parent; drain_all → fail (имитация failed deploy)
        monkeypatch.setattr(parallel_runner.os, "fork", lambda: 1)
        monkeypatch.setattr(parallel_runner, "drain_all_count", lambda pids, pid_to_name: (0, 1, ["mod1"]))
        monkeypatch.setattr(parallel_runner.os, "waitpid", lambda pid, *a, **k: (pid, 1))
        monkeypatch.setattr(parallel_runner.os, "WIFEXITED", lambda s: True)
        monkeypatch.setattr(parallel_runner.os, "WEXITSTATUS", lambda s: 1)
        monkeypatch.setattr(
            "core.internal.bootstrap.deploy.docker_orchestrator._resolve_compose_file",
            lambda d: mod_dir / "docker-compose.yml",
        )
        monkeypatch.setattr(
            "core.internal.bootstrap.deploy.parallel_runner._shared_docker_compose_down",
            lambda *a, **k: True,
        )

        deployed, failed, names, rolled_back = parallel_runner.deploy_docker_group(["mod1:"], str(modules_dir))
        logger.info(
            "[IMP:9][test] deploy_group rollback: deployed=%d failed=%d rolled_back=%s",
            deployed,
            failed,
            rolled_back,
        )
        assert failed == 1
        assert names == ["mod1"]
        assert rolled_back == ["mod1"], f"rollback не выполнен: {rolled_back}"

    # 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · rollback — docker compose down per module
    def test_rollback_invokes_compose_down(self, tmp_path, caplog, monkeypatch) -> None:
        """Прямая проверка rollback-ветки: group_failed>0 → docker_compose_down для модулей."""
        caplog.set_level(logging.DEBUG)
        # Патчим внутренние зависимости deploy_docker_group: fork-циклы не выполняем.
        modules_dir = tmp_path / "modules"
        mod_dir = modules_dir / "mod1"
        mod_dir.mkdir(parents=True)
        compose_file = mod_dir / "docker-compose.yml"
        compose_file.write_text("services:\n  mod1:\n    image: x\n")

        # deploy-фаза: fork → parent (pid накапливается), drain_all_count → fail (имитация failed deploy)
        monkeypatch.setattr(parallel_runner.os, "fork", lambda: 1)
        monkeypatch.setattr(parallel_runner, "drain_all_count", lambda pids, pid_to_name: (0, 1, ["mod1"]))
        # HC-фаза: fork → parent, waitpid → exit 1 (fail, не блокирует)
        monkeypatch.setattr(parallel_runner.os, "waitpid", lambda pid, *a, **k: (pid, 1))
        monkeypatch.setattr(parallel_runner.os, "WIFEXITED", lambda s: True)
        monkeypatch.setattr(parallel_runner.os, "WEXITSTATUS", lambda s: 1)
        monkeypatch.setattr(
            "core.internal.bootstrap.deploy.docker_orchestrator._resolve_compose_file",
            lambda d: compose_file,
        )
        down_calls = []

        def fake_down(compose_dir, **kwargs):
            down_calls.append(compose_dir)
            return True

        monkeypatch.setattr(
            "core.internal.bootstrap.deploy.parallel_runner._shared_docker_compose_down",
            fake_down,
        )

        # deploy_docker_group: drain_all → fail → rollback (docker compose down для mod1)
        deployed, failed, names, rolled_back = parallel_runner.deploy_docker_group(["mod1:"], str(modules_dir))
        logger.info(
            "[IMP:9][test] deploy_group rollback: deployed=%d failed=%d rolled_back=%s",
            deployed,
            failed,
            rolled_back,
        )
        assert failed == 1
        assert names == ["mod1"]
        assert rolled_back == ["mod1"], f"rollback не выполнен: {rolled_back}"
        assert len(down_calls) == 1, f"docker compose down не вызван: {down_calls}"


# endregion TEST_deploy_docker_group_rollback
