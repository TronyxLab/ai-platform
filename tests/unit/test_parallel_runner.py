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
## ⚠️ TRAP[DECISION] · 2026-08-13 · — · E1 (DevPlan 160): parallel_runner НЕ конвертирован в DI
## · Rejected: runner/facts-инъекция (как в остальных 9 модулях E1) — production-код не имеет
## ·   прямых subprocess.run/os.geteuid/shutil.which/os.path.isfile в бизнес-функциях
## ·   (subprocess удалён в DevPlan 079 → shared retry_pull; docker compose — через shared).
## · Reason: 7 setattr (было 14, W3.5-4 консолидировал seams в _patch_deploy_group_env) —
## ·   fork-семантика (os.fork/os.waitpid/os.WIFEXITED/os.WEXITSTATUS) + lazy-импорты
## ·   docker_orchestrator. Инъекция «процесс-фабрики» = серьёзный рефакторинг
## ·   fork-параллелизма (ядро модуля, os._exit в child) без тестового выигрыша — архитектурная
## ·   причина по правилу E1 «неконвертируемые модули».
## · Rev: если появится threading-замена fork-параллелизма — переписать тесты на DI.
## @changes  2026-08-02 | DevPlan 118 D1 — создан
##           2026-08-13 | E1 (160) — анализ неконвертируемости задокументирован (TRAP[DECISION])
# endregion MODULE_CONTRACT

import logging
import os
from pathlib import Path
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
def _patch_deploy_group_env(monkeypatch) -> None:
    """Применить 4 os-process seams deploy_docker_group (fork-семантика, keep — TRAP[DI-KEEP]).

    ## @purpose — DRY двух rollback-тестов (W3.5-4, 164 S8): os.fork/waitpid/WIFEXITED/WEXITSTATUS —
    ##            процесс-контроль (os._exit в child) — неконвертируем без смены сущности модуля
    ##            (honest-floor §4, 167 D6). drain/resolve/compose_down — DI-параметры
    ##            deploy_docker_group (см. _group_di_kwargs).
    ## @io       ⇥ monkeypatch → ⎋ None
    ## @complexity O(1)
    """
    # ⚠️ TRAP[DI-KEEP] · 2026-08-14 · — · os.fork-семантика (fork-ядро модуля) · Rejected: DI-spawn-абстракция
    # · Reason: parallel_runner реально форкает процессы (os._exit в child) — DI = смена сущности (floor §4)
    # · Rev: при извлечении process-pool runner'а из parallel_runner
    monkeypatch.setattr(parallel_runner.os, "fork", lambda: 1)
    # ⚠️ TRAP[DI-KEEP] · 2026-08-14 · — · os.waitpid/WIFEXITED/WEXITSTATUS (HC-фаза) · Rejected: DI-spawn-абстракция
    # · Reason: процесс-контроль (waitpid/WIFEXITED/WEXITSTATUS — os-примитивы), не I/O-вызов (floor §4)
    # · Rev: при извлечении process-pool runner'а из parallel_runner
    monkeypatch.setattr(parallel_runner.os, "waitpid", lambda pid, *_, **__: (pid, 1))
    monkeypatch.setattr(parallel_runner.os, "WIFEXITED", lambda _: True)
    monkeypatch.setattr(parallel_runner.os, "WEXITSTATUS", lambda _: 1)


def _group_di_kwargs(compose_file: Path, *, down_calls: list | None = None) -> tuple[dict, list]:
    """DI-аргументы deploy_docker_group: drain/resolve/compose_down/deploy_module fakes (167 D3/D6, 170 W10-B).

    ## @purpose — Замена monkeypatch-патчей drain_all_count/_resolve_compose_file/
    ##            _shared_docker_compose_down/deploy_docker_module: тест передаёт fakes
    ##            параметрами (0 monkeypatch). deploy_module_fn — DI-seam 170 W10-B
    ##            (parallel_runner не импортирует docker_orchestrator; fail-fast при None).
    ## @io       ⇥ compose_file (Path), down_calls: list|None → ⎋ (kwargs: dict, down_calls: list)
    ## @complexity O(1)
    """
    if down_calls is None:
        down_calls = []

    def fake_down(compose_dir: str, **kwargs: object) -> bool:
        down_calls.append(compose_dir)
        return True

    return (
        {
            "drain_all_fn": lambda _, __: (0, 1, ["mod1"]),
            "resolve_compose_fn": lambda _: compose_file,
            "compose_down_fn": fake_down,
            "deploy_module_fn": lambda *_a, **_k: False,  # fork mocked → child не создаётся
        },
        down_calls,
    )


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

        _patch_deploy_group_env(monkeypatch)
        di_kwargs, _ = _group_di_kwargs(mod_dir / "docker-compose.yml")

        deployed, failed, names, rolled_back = parallel_runner.deploy_docker_group(
            ["mod1:"], str(modules_dir), **di_kwargs
        )
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
        # DI-параметры deploy_docker_group: fork-циклы не выполняем (os-fork keep — TRAP[DI-KEEP]).
        modules_dir = tmp_path / "modules"
        mod_dir = modules_dir / "mod1"
        mod_dir.mkdir(parents=True)
        compose_file = mod_dir / "docker-compose.yml"
        compose_file.write_text("services:\n  mod1:\n    image: x\n")

        _patch_deploy_group_env(monkeypatch)
        di_kwargs, down_calls = _group_di_kwargs(compose_file, down_calls=[])

        # deploy_docker_group: drain_all → fail → rollback (docker compose down для mod1)
        deployed, failed, names, rolled_back = parallel_runner.deploy_docker_group(
            ["mod1:"], str(modules_dir), **di_kwargs
        )
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
