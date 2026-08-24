# GREP_SUMMARY: fakes, fake-command-runner, make-proc, completed-process, subprocess-doubles, DI-test-doubles
# STRUCTURE: ▶ make_proc(rc, stdout, stderr) → ◇ subprocess.CompletedProcess → ⎋ proc
#            ▶ FakeCommandRunner(results FIFO / default) → ◇ run() records calls+kwargs → ◇ pop/default → ⎋ proc
# region MODULE_CONTRACT
## @purpose  Общие subprocess-двойники для тестов (T2.16c): CompletedProcess-фабрика
##           make_proc + базовый scripted FakeCommandRunner (DI-канон W4b).
##           Устраняет 6 копий `def _proc(...)` и 5 копий scripted FakeCommandRunner
##           (test_cert_orchestrator / test_core_deliverer / test_docker_ops /
##           test_python_deps / test_remote_executor).
## @scope    Все unit-тесты, использующие fake-раннеры subprocess (runner= DI-канал).
## @invariants
##   - make_proc: args=[] (модули под тестом не читают args), stdout/stderr могут быть str|bytes
##   - FakeCommandRunner.run() НЕ raise (канон subprocess_io check=False — graceful)
##   - results исчерпаны → default (стабильное поведение для многошаговых сценариев)
##   - run() принимает ВСЕ kwargs канона CommandRunner-протокола + superset: timeout/check/
##     non_fatal/fatal_rc/env/input — input покрывает stdin-транспорт секретов (REF-0007:
##     core_deliverer._run_cmd_stdin / remote_executor._ssh_exec), записывается в kwargs
##   - last_cmd / last_kwargs — observability для эффект-ассертов (timeout-контракты)
## @rationale DRY: 11 файлов дублировали идентичные классы. Специализированные фейки с
##            per-module exception-translation (PlatformFatalError/RuntimeError/CommandFailedError,
##            psql-роутеры, rc_fn-симуляторы) ОСТАВЛЕНЫ локально — они несут доменную семантику
##            (см. §ОТКЛОНЕНИЯ в отчёте консолидации).
## @changes
##   LAST_CHANGE: 2026-08-22 | Created (T2.16c consolidation)
# endregion MODULE_CONTRACT

import subprocess

# region FUNC_make_proc


def make_proc(rc: int = 0, stdout: str | bytes = "", stderr: str | bytes = "") -> subprocess.CompletedProcess:
    """Build a CompletedProcess with given rc/stdout/stderr (fake-раннер результат).

    ## @purpose — Единая фабрика CompletedProcess для тестов: заменяет 6 локальных копий
    ##            `def _proc(rc=0, stdout="", stderr="")` (T2.16c).
    ## @io — ⇥ rc: int, stdout: str|bytes, stderr: str|bytes → ⎋ subprocess.CompletedProcess
    ## @complexity — O(1)
    ## @invariants
    ##   - args=[] — контракт фейков: модули под тестом не читают args (лишь rc/stdout/stderr)
    ##   - stdout/stderr принимают str И bytes (test_docker_ops канон)
    """
    return subprocess.CompletedProcess([], returncode=rc, stdout=stdout, stderr=stderr)


# endregion FUNC_make_proc


# region CLASS_FakeCommandRunner


class FakeCommandRunner:
    """Scripted CommandRunner (DI-канон W4b): результат из последовательности или дефолт.

    ## @purpose — Замена monkeypatch subprocess.run в тестах: каждый вызов записывается
    ##            (calls/kwargs), возвращается scripted CompletedProcess (FIFO из results,
    ##            затем default). Базовый фейк для 5 тест-файлов (T2.16c).
    ## @io — ⇥ results: list[CompletedProcess] (потребительский FIFO), default: CompletedProcess
    ##      → ⎋ CompletedProcess (каждый run())
    ## @complexity — O(1) — pop из списка / дефолт
    ## @invariants
    ##   - results исчерпаны → default (стабильное поведение для многошаговых сценариев)
    ##   - run() НЕ raise (канон subprocess_io check=False — graceful)
    ##   - run() принимает timeout/check/non_fatal/fatal_rc/env — superset CommandRunner-протокола
    ##     (env-канал cert_orchestrator 154 W1: TRAP[DI-SEAM] — протокол без env, фейки принимают)
    ##   - kwargs записываются {"timeout","check","non_fatal","fatal_rc","env", **extra} —
    ##     superset-kwargs (в т.ч. input= для stdin-транспорта REF-0007) попадают в запись;
    ##     эффект-ассерты читают last_kwargs["timeout"] / last_kwargs["input"]
    """

    def __init__(self, results: list | None = None, default: subprocess.CompletedProcess | None = None) -> None:
        self._results: list = list(results) if results else []
        self.default: subprocess.CompletedProcess = (
            default if default is not None else subprocess.CompletedProcess([], 0, "", "")
        )
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    @property
    def last_cmd(self) -> list[str] | None:
        return self.calls[-1] if self.calls else None

    @property
    def last_kwargs(self) -> dict:
        return self.kwargs[-1] if self.kwargs else {}

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=(), env=None, **extra):
        self.calls.append(list(cmd))
        self.kwargs.append({
            "timeout": timeout,
            "check": check,
            "non_fatal": non_fatal,
            "fatal_rc": fatal_rc,
            "env": env,
            **extra,
        })
        if self._results:
            return self._results.pop(0)
        return self.default


# endregion CLASS_FakeCommandRunner
