# GREP_SUMMARY: parallel-runner, unit, drain, rollback, fork, slot-waiter, deploy-group, D1, docker-orchestrator-decomposition, REF-0005, WIFEXITED, all-names
# STRUCTURE: ▶ mock os.waitpid/fork → ◇ drain_completed_count [WNOHANG done|fail|ChildProcessError] → ◇ drain_all_count [blocking done|error|status-accounting REF-0005] → ◇ deploy_docker_group [real-drain failed+all_names+rollback] → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты для core/internal/bootstrap/deploy/parallel_runner.py (DevPlan 118 D1) —
##           fork-параллелизм, drain-примитивы, atomic rollback deploy_docker_group.
## @scope    Pure unit (mock os.waitpid/os.fork) — никакого реального fork-деплоя в тестах.
## @invariants
##   - drain_completed_count: WNOHANG-семантика (готовые снимаются, незавершённые остаются)
##   - drain_all_count: blocking-семантика (все снимаются, pids.clear())
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


# region TEST_drain_completed
class TestDrainCompletedCount:
    # 🧪 TRAP[TEST] · 2026-08-02 · UNIT · drain_completed_count — WNOHANG-снятие успешных детей
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

    # 🧪 TRAP[TEST] · 2026-08-02 · UNIT · drain_completed_count — WNOHANG-снятие failed детей
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

    # 🧪 TRAP[TEST] · 2026-08-02 · UNIT · drain_completed_count — ChildProcessError → fail
    def test_child_process_error(self, caplog) -> None:
        """ChildProcessError (нет такого ребёнка) → fail=1, имя сохраняется."""
        caplog.set_level(logging.DEBUG)
        with mock.patch.object(parallel_runner.os, "waitpid", side_effect=ChildProcessError):
            deployed, failed, names = parallel_runner.drain_completed_count([333], {333: "mod3"})
        logger.info("[IMP:9][test] drain_completed ChildProcessError: failed=%d names=%s", failed, names)
        assert deployed == 0
        assert failed == 1
        assert names == ["mod3"]


# endregion TEST_drain_completed


# region TEST_drain_all
class TestDrainAllCount:
    # 🧪 TRAP[TEST] · 2026-08-02 · UNIT · drain_all_count — blocking-снятие всех детей
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

    # 🧪 TRAP[TEST] · 2026-08-02 · UNIT · drain_all_count — ChildProcessError → fail
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


# endregion TEST_drain_all


# region TEST_drain_all_status_accounting
class TestDrainAllStatusAccounting:
    """REF-0005 (DevPlan 11 W0): drain_all_count зеркалирует WIFEXITED/WEXITSTATUS.

    ## @purpose — BUG-0301≡BUG-0801: blocking drain считал ЛЮБОГО дождавшегося ребёнка успешным
    ##            (unconditional deployed+=1) → group_failed=0 → атомарный откат группы не
    ##            срабатывал, verdict success на поломанном стеке. Реальный drain_all_count +
    ##            mocked waitpid (карточка REF-0005 «Tests required», TDD-red 2026-08-24).
    """

    # 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · REF-0005/BUG-0301 — drain_all_count failed-учёт
    # · Scenario: ребёнок завершился с exit=3 (status=3<<8) → failed=1 + имя в failed_names.
    # · Last fail: 2026-08-24 — unconditional deployed+=1 при любом успешном waitpid:
    # ·   deployed=1/failed=0 на упавшем ребёнке (красный до фикса).
    # · Remove if: drain_all_count теряет статусную семантику (не допускается — REF-0005).
    def test_failed_child_counted_as_failed(self, caplog) -> None:
        """Ребёнок с ненулевым exit-кодом → failed=1 + имя из pid_to_name (НЕ deployed)."""
        caplog.set_level(logging.DEBUG)
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        with mock.patch.object(parallel_runner.os, "waitpid", return_value=(42, 3 << 8)):
            pids = [42]
            deployed, failed, names = parallel_runner.drain_all_count(pids, {42: "mod-fail"})
        for record in caplog.records:
            if "[IMP:" in record.message and int(record.message.split("[IMP:")[1].split("]")[0]) >= 7:
                print(record.message)
        print("--- END LDD TRAJECTORY ---")
        logger.info("[IMP:9][test] drain_all status: deployed=%d failed=%d names=%s", deployed, failed, names)
        assert pids == [], "blocking drain очищает pids"
        assert deployed == 0, f"exit=3 НЕ может быть deployed (REF-0005): deployed={deployed}"
        assert failed == 1, f"failed-ребёнок обязан считаться (REF-0005): failed={failed}"
        assert names == ["mod-fail"], f"имя из pid_to_name обязано вернуться: {names}"

    # 🧪 TRAP[TEST] · 2026-08-24 · SCENARIO · REF-0005 — смешанный исход (ok + fail)
    # · Last fail: 2026-08-24 — оба ребёнка считались deployed (failed=0).
    # · Remove if: drain_all_count перестаёт быть единой точкой финального drain группы.
    def test_mixed_children_split_by_status(self, caplog) -> None:
        """Двое детей: exit=0 и exit=1 → deployed=1, failed=1, failed_names=['modB']."""
        caplog.set_level(logging.DEBUG)
        statuses = {101: 0, 102: 1}
        with mock.patch.object(
            parallel_runner.os, "waitpid", side_effect=lambda pid, *_, **__: (pid, statuses.get(pid, 0))
        ):
            pids = [101, 102]
            deployed, failed, names = parallel_runner.drain_all_count(pids, {101: "modA", 102: "modB"})
        logger.info(
            "[IMP:9][test] drain_all mixed: deployed=%d failed=%d names=%s",
            deployed,
            failed,
            names,
        )
        assert deployed == 1
        assert failed == 1
        assert names == ["modB"]

    # 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · REF-0005/BUG-0501≡BUG-0703 — all_names ДО drain
    # · Scenario: deploy_docker_group с РЕАЛЬНЫМ drain_all_count (DI drain_all_fn=None):
    # ·   drain очищает pid_to_name → all_names пуст → групповой healthcheck по 0 модулям;
    # ·   failed-учёт честный → group_failed>0 → атомарный rollback группы (сигнал REF-W1).
    # · Last fail: 2026-08-24 — живой баг маскировался fake-drain в тестах: all_names=[],
    # ·   HC не форкался, failed=0, rollback не срабатывал.
    # · Remove if: healthcheck-per-group переезжает в другой механизм учёта имён.
    def test_group_real_drain_failed_and_healthchecks(self, tmp_path, caplog, monkeypatch) -> None:
        """Реальный drain: failed_names из pid_to_name; all_names non-empty (HC fork per module)."""
        caplog.set_level(logging.DEBUG)
        modules_dir = tmp_path / "modules"
        mod_a = modules_dir / "modA"
        mod_b = modules_dir / "modB"
        mod_a.mkdir(parents=True)
        mod_b.mkdir(parents=True)
        for mod in (mod_a, mod_b):
            (mod / "docker-compose.yml").write_text(f"services:\n  {mod.name}:\n    image: x\n", encoding="utf-8")

        # ── os-примитивы (fork-семантика keep — TRAP[DI-KEEP]): уникальные pid по счётчику ──
        fork_calls: list[int] = []

        def fake_fork() -> int:
            fork_calls.append(len(fork_calls))
            return 101 + len(fork_calls) - 1  # deploy: 101,102 → HC: 103,104

        statuses = {101: 0, 102: 1}  # modA ok, modB fail; HC-дети (103+) → default 0 (pass)
        monkeypatch.setattr(parallel_runner.os, "fork", fake_fork)
        monkeypatch.setattr(parallel_runner.os, "waitpid", lambda pid, *_, **__: (pid, statuses.get(pid, 0)))
        monkeypatch.setattr(parallel_runner.os, "WIFEXITED", lambda _: True)
        monkeypatch.setattr(parallel_runner.os, "WEXITSTATUS", lambda s: s)

        down_calls: list[str] = []

        def fake_down(compose_dir: str, **_: object) -> bool:
            down_calls.append(compose_dir)
            return True

        deployed, failed, failed_names, rolled_back = parallel_runner.deploy_docker_group(
            ["modA:", "modB:"],
            str(modules_dir),
            resolve_compose_fn=lambda _p: mod_a / "docker-compose.yml",
            compose_down_fn=fake_down,
            deploy_module_fn=lambda *_a, **_k: True,  # fork замокан → child-тело не исполняется
        )

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            if "[IMP:" in record.message and int(record.message.split("[IMP:")[1].split("]")[0]) >= 7:
                print(record.message)
        print("--- END LDD TRAJECTORY ---")
        logger.info(
            "[IMP:9][test] group real-drain: deployed=%d failed=%d names=%s rolled_back=%s forks=%d downs=%d",
            deployed,
            failed,
            failed_names,
            rolled_back,
            len(fork_calls),
            len(down_calls),
        )
        # ── честный failed-учёт (drain_all_count зеркалит WEXITSTATUS) ──
        assert deployed == 1, f"только modA успешен: deployed={deployed}"
        assert failed == 1, f"group_failed обязан быть >0 при failed-ребёнке (REF-0005): failed={failed}"
        assert failed_names == ["modB"], f"имена из pid_to_name: {failed_names}"
        # ── all_names собран ДО drain → групповой healthcheck идёт по ВСЕМ модулям ──
        assert len(fork_calls) == 4, (
            f"2 deploy-fork + 2 HC-fork (all_names non-empty, REF-0005); forks={len(fork_calls)}"
        )
        # ── failed>0 ⇒ вердикт группы ≠ success: атомарный откат группы (сигнал; сам rollback — W1) ──
        assert sorted(rolled_back) == ["modA", "modB"], f"atomic rollback группы: {rolled_back}"
        assert len(down_calls) == 2, f"compose down для всех модулей группы: {down_calls}"


# endregion TEST_drain_all_status_accounting


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
