# GREP_SUMMARY: loadtest runner unit build-locust-args no-max-rps lt-target-rps lt-users rps-wait-time helper constant-throughput fallback
# STRUCTURE: ▶ fake-config (SimpleNamespace) → ◇ _build_locust_args (no-rate-limit-flag / structure / parametrized)
#           → ◇ _locust_env (LT_TARGET_RPS/LT_USERS, per-step override) → ◇ rps_wait_time (constant_throughput | fallback)
#           → ⎋ 6 тестов, LDD IMP:7-10 траектория (Anti-Illusion Rule)
# region MODULE_CONTRACT
## @purpose  Unit-тесты runner_cli (DevPlan 146-m1 TASK-8, §$TEST_SPEC): регрессия BUG-1 —
##           _build_locust_args НЕ содержит rate-limit флага (--max-rps не существует
##           в locust 2.x); RPS-контроль передаётся env-ом LT_TARGET_RPS/LT_USERS через
##           _locust_env (в capacity — per-step override); helper rps_wait_time
##           (core/loadtest/scenarios/__init__.py) строит constant_throughput
##           (per-user = target/users) или between(0.05, 0.2) fallback.
## @scope    Чистые функции — без subprocess, без сети, без реального locust-прогона.
##           runner_cli НЕ импортирует locust (префлайт find_spec) — тесты build/env
##           работают в любом окружении; rps_wait_time-тесты требуют locust (load extra).
## @invariants
##   - Каждый тест изолирован: monkeypatch env LT_S3_* (нет дрейфа от окружения)
##   - LDD: caplog IMP:7-10 траектория печатается ДО ассертов; assert IMP:9
##     (Anti-Illusion Rule, .kilo/rules/testing.md)
##   - rps_wait_time-тесты: pytest.importorskip("locust") — skip в окружениях без
##     load extra (CI), выполнение в dev (.venv с [load])
## @rationale _build_locust_args — чистый builder: низкая стоимость unit-теста, высокая
##            ценность — ловит регрессию CLI-флагов (DevPlan 146-m1 §10). closure-проверки
##            constant_throughput/between валидируют РЕАЛЬНЫЙ механизм locust 2.32
##            (пин <2.33 закреплён в pyproject.toml — TASK-6).
## @changes  2026-08-11 | DevPlan 146-m1 TASK-8 — Created (BUG-1 RPS-фикс)
# 📝 TRAP[DEBT] · 2026-08-11 · MED · CI setup-python-venv не устанавливает load extra → rps_wait_time-тесты skipped в CI
# · Observed: .github/actions/setup-python-venv ставит только core/requirements.txt (locust — optional, вне [project].dependencies)
# · Suspected: CI gate (static_audit) выполняет test_loadtest_runner.py без locust → importorskip → 2 skipped
# · Impact: RPS-механизм (constant_throughput) не верифицируется в CI до устранения
# · When: during DevPlan 146-m1 TASK-8 implementation
# · Fix: добавить .[load] в setup-python-venv (вне скоупа 146-m1 File Manifest)
# ⚠️ TRAP[BUG] · 2026-08-11 · P1 · gevent.monkey.patch_all (locust import) ломает ssl в Python 3.14
# · Symptom: тест s3_ssl_cache падает RecursionError (ssl.SSLContext.options — 947 повторов одной строки)
# · Root: locust/__init__.py при импорте вызывает monkey.patch_all(); gevent-патч ssl несовместим
# ·   с Python 3.14 → последующие boto3/ssl вызовы в том же процессе (pytest без xdist) рекурсивны
# · Fix: LOCUST_SKIP_MONKEY_PATCH=1 (штатный флаг locust) в rps_wait_time-тестах ДО импорта locust —
# ·   gevent не патчится; helper constant_throughput — чистая функция, патч не нужен
# · Prevention: импорт locust в тестах — только с флагом; runtime locust (make load-test) —
# ·   отдельный процесс, patch_all работает штатно
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from core.internal.loadtest.runner_cli import _build_locust_args, _locust_env

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region HELPER__make_config
def _make_config(target_rps: int = 10, users: int = 20) -> SimpleNamespace:
    """Минимальный fake LoadtestConfig для _locust_env (без load_config/NODE-резолва).

    ▶ ┌target_rps, users┐ → ○ SimpleNamespace(scenario=spec, endpoint/host/domain) → ⎋ config

    ## @purpose  Hermetic-вход _locust_env: _locust_env читает только endpoint/host/domain
    ##            + scenario-поля — fake без файлов и сети (Zero Hardcode Rule).
    ## @io — ⇥ target_rps: int, users: int → ⎋ SimpleNamespace
    ## @complexity — O(1)
    """
    spec = SimpleNamespace(
        name="web",
        ssl_verify=False,
        method="GET",
        paths=("/", "/status"),
        path=None,
        model=None,
        stream=False,
        chunk_timeout=10.0,
        headers={},
        body_template=None,
        target_rps=target_rps,
        users=users,
    )
    return SimpleNamespace(
        endpoint="https://example.com/",
        node_host="1.2.3.4",
        platform_domain="example.com",
        scenario=spec,
    )


# endregion HELPER__make_config


# region HELPER_assert_ldd_imp9
def _assert_ldd_imp9(caplog) -> None:
    """Печать LDD-траектории IMP:7-10 + assert наличия IMP:9 (Anti-Illusion Rule).

    ## @purpose — Единая точка LDD-телеметрии тестов runner (контракт .kilo/rules/testing.md).
    ## @io — ⇥ caplog → ⎋ None (assert found IMP:9)
    """
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
            if "[IMP:9]" in record.message:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 business logic log found"


# endregion HELPER_assert_ldd_imp9


# ═══════════════════════════════════════════════════════════════════════════════
# _build_locust_args — регрессия BUG-1 (rate-limit флаг отсутствует в locust)
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_build_locust_args_no_max_rps
# GUARD-PRESERVE (168): BUG-1 regression guard — единственное покрытие инварианта «argv без --max-rps» (locust 2.x: rc=2)
# 🧪 TRAP[TEST] · Scenario: BUG-1 регрессия — argv без rate-limit флага
# · Regression: _build_locust_args снова добавит --max-rps (locust 2.x: unrecognized arguments → rc=2)
# · Last fail: 2026-08-11 — первый прогон tronyx-vps: locust: error: unrecognized arguments: --max-rps
# · Remove if: locust 2.x получит штатный CLI-флаг rate-limit и он станет каноном
def test_build_locust_args_no_max_rps(caplog) -> None:
    """argv НЕ содержит --max-rps (флаг не существует в locust 2.x, 146-m1 BUG-1)."""
    caplog.set_level(logging.INFO)
    args = _build_locust_args("scenarios/web.py", 20, 90, "/tmp/lt/run")
    logger.info("[IMP:9][test][build_locust_args] argv собран без rate-limit флага: %d флагов", len(args))
    _assert_ldd_imp9(caplog)
    assert "--max-rps" not in args


# endregion TEST_build_locust_args_no_max_rps


# region TEST_build_locust_args_structure
# GUARD-PRESERVE (168): headless-контракт guard — единственное покрытие набора обязательных locust-флагов (-f/--headless/-u/-r/--run-time/--csv)
# 🧪 TRAP[TEST] · Scenario: обязательные locust-флаги присутствуют (headless-контракт)
# · Regression: случайное удаление обязательного флага (-f/--headless/-u/-r/--run-time/--csv) → прогон без отчёта
# · Last fail: N/A (new)
# · Remove if: CLI-контракт runner_cli изменён (другой набор флагов)
def test_build_locust_args_structure(caplog) -> None:
    """Обязательные флаги: -f, --headless, -u, -r, --run-time, --csv, --csv-full-history."""
    caplog.set_level(logging.INFO)
    args = _build_locust_args("scenarios/web.py", 20, 90, "/tmp/lt/run")
    logger.info("[IMP:9][test][build_locust_args] структура argv валидна (headless-contract)")
    _assert_ldd_imp9(caplog)
    for flag in ("-f", "--headless", "-u", "-r", "--run-time", "--csv", "--csv-full-history"):
        assert flag in args, f"обязательный флаг {flag} отсутствует в argv"


# endregion TEST_build_locust_args_structure


# region TEST_build_locust_args_parametrized
# 🧪 TRAP[TEST] · Scenario: параметризация users/duration/csv_prefix → корректные значения в argv
# · Regression: перепутаны флаги или значения (users в -r, длительность без 's', csv-путь)
# · Last fail: N/A (new)
# · Remove if: формат locust-argv изменён
@pytest.mark.parametrize(
    "users,duration,csv_prefix",
    [
        (10, 90, "/tmp/run"),
        (40, 300, "/lt/results/x/run"),
        (1, 60, "out/stats"),
    ],
)
def test_build_locust_args_parametrized(users: int, duration: int, csv_prefix: str, caplog) -> None:
    """Значения флагов соответствуют параметрам: -u/-r = users, --run-time = Ns, --csv = prefix."""
    caplog.set_level(logging.INFO)
    args = _build_locust_args("scenarios/web.py", users, duration, csv_prefix)
    logger.info(
        "[IMP:9][test][build_locust_args] parametrized: users=%d duration=%d csv=%s", users, duration, csv_prefix
    )
    _assert_ldd_imp9(caplog)
    assert args[args.index("-u") + 1] == str(users)
    assert args[args.index("-r") + 1] == str(users)
    assert args[args.index("--run-time") + 1] == f"{duration}s"
    assert args[args.index("--csv") + 1] == csv_prefix
    assert args[-1] == "--csv-full-history"


# endregion TEST_build_locust_args_parametrized


# region TEST_locust_env_has_target_rps
# 🧪 TRAP[TEST] · Scenario: _locust_env содержит LT_TARGET_RPS и LT_USERS (RPS-механизм 146-m1)
# · Regression: удаление LT_TARGET_RPS/LT_USERS из env → сценарии вернутся к between-fallback (нет RPS-контроля)
# · Last fail: N/A (new) — 146-m1 BUG-1 fix
# · Remove if: RPS-механизм заменён на иной канал (не env)
def test_locust_env_has_target_rps(monkeypatch, caplog) -> None:
    """LT_TARGET_RPS (target прогона) и LT_USERS (размер пула) присутствуют в env; per-step override в capacity."""
    caplog.set_level(logging.INFO)
    for key in ("LT_S3_ACCESS_KEY", "LT_S3_SECRET_KEY", "LT_S3_BUCKET", "LT_S3_OBJECT"):
        monkeypatch.delenv(key, raising=False)
    env = _locust_env(_make_config(target_rps=10, users=20))
    logger.info("[IMP:9][test][locust_env] LT_TARGET_RPS=%s LT_USERS=%s", env.get("LT_TARGET_RPS"), env.get("LT_USERS"))
    _assert_ldd_imp9(caplog)
    assert env["LT_TARGET_RPS"] == "10"
    assert env["LT_USERS"] == "20"
    # capacity: каждый шаг передаёт свой RPS/пул через параметры (per-step override)
    env_step = _locust_env(_make_config(), rps=32, users=64)
    assert env_step["LT_TARGET_RPS"] == "32"
    assert env_step["LT_USERS"] == "64"


# endregion TEST_locust_env_has_target_rps


# region TEST_locust_env_passthrough_pg


# 🧪 TRAP[TEST] · Scenario: _locust_env пробрасывает LT_PG_*/LT_LANGFUSE_* (DevPlan 148 W3 BUG-1/BUG-5)
# · Regression: db-сценарий на LOAD_RUNNER=node не получал LT_PG_PASSWORD/LT_PG_USER
#   (passthrough был только для LT_S3_*) → PgError auth в контейнере (fail 100%);
#   langfuse-ключи LT_LANGFUSE_* не доходили до контейнера (Basic auth недоступен)
# · Last fail: 2026-08-12 (W3 runtime, db smoke BLOCKED, langfuse 403)
# · Remove if: db/langfuse-сценарии переведены на иной канал передачи секретов (не env)
def test_locust_env_passthrough_pg(caplog) -> None:
    """LT_PG_*/LT_LANGFUSE_* из os.environ попадают в env locust (remote-режим, docker -e)."""
    caplog.set_level(logging.INFO)
    # DI (W-H DevPlan 163): LT_*-override через environ= параметр (0 setenv)
    env = _locust_env(
        _make_config(target_rps=10, users=20),
        environ={
            "LT_PG_USER": "postgres",
            "LT_PG_PASSWORD": "secret-pw",
            "LT_PG_DB": "platform",
            "LT_LANGFUSE_PUBLIC_KEY": "pk-lf_public",
            "LT_LANGFUSE_SECRET_KEY": "sk-lf_secret",
            "LT_CHUNK_TIMEOUT": "25",  # BUG-7: env override поверх spec.chunk_timeout
        },
    )
    logger.info(
        "[IMP:9][test][locust_env] LT_PG_USER=%s LT_PG_DB=%s pg_passthrough=%s lf_passthrough=%s chunk_timeout=%s",
        env.get("LT_PG_USER"),
        env.get("LT_PG_DB"),
        "LT_PG_PASSWORD" in env,
        "LT_LANGFUSE_SECRET_KEY" in env,
        env.get("LT_CHUNK_TIMEOUT"),
    )
    _assert_ldd_imp9(caplog)
    assert env["LT_PG_USER"] == "postgres"
    assert env["LT_PG_PASSWORD"] == "secret-pw"
    assert env["LT_PG_DB"] == "platform"
    assert env["LT_LANGFUSE_PUBLIC_KEY"] == "pk-lf_public"
    assert env["LT_LANGFUSE_SECRET_KEY"] == "sk-lf_secret"
    assert env["LT_CHUNK_TIMEOUT"] == "25"


# endregion TEST_locust_env_passthrough_pg


# ═══════════════════════════════════════════════════════════════════════════════
# rps_wait_time — RPS-механизм (locust 2.32, пин <2.33; требуется load extra)
# ═══════════════════════════════════════════════════════════════════════════════

_LOCUST_REASON = (
    'locust (load extra) не установлен — тест RPS-механизма требует locust; установите: pip install -e ".[load]"'
)


# region TEST_rps_wait_time_constant_throughput
# 🧪 TRAP[TEST] · Scenario: RPS>0 → constant_throughput (per-user = target/users)
# · Regression: helper вернёт между-fallback или наивную 1/rps (без учёта latency) при заданном RPS
# · Last fail: N/A (new) — 146-m1 BUG-1 fix
# · Remove if: RPS-механизм сценариев заменён (не constant_throughput)
def test_rps_wait_time_constant_throughput(monkeypatch, caplog) -> None:
    """rps_wait_time(10, 20) → constant_throughput(0.5): wait = 1/(target/users) = 2.0s (latency-адаптивно)."""
    # gevent.monkey.patch_all (locust при импорте) ломает ssl в Python 3.14 — последующие
    # boto3-тесты падают RecursionError (ssl.SSLContext.options бесконечная рекурсия).
    # LOCUST_SKIP_MONKEY_PATCH=1 — штатный флаг locust: gevent не патчится (см. TRAP[BUG]).
    # Флаг ДО importorskip — иначе importorskip импортирует locust первым (patch_all без флага).
    monkeypatch.setenv("LOCUST_SKIP_MONKEY_PATCH", "1")
    pytest.importorskip("locust", reason=_LOCUST_REASON)
    from core.loadtest.scenarios import rps_wait_time  # locust-dependent — импорт внутри функции

    caplog.set_level(logging.INFO)
    wait = rps_wait_time(10, 20)  # per-user rps = 10/20 = 0.5 → constant_pacing(1/0.5 = 2.0)
    logger.info("[IMP:9][test][rps_wait_time] constant_throughput: per-user=%s wait=%s", 0.5, 2.0)
    _assert_ldd_imp9(caplog)
    assert callable(wait)
    # constant_throughput(0.5) = constant_pacing(2.0): единственная свободная переменная — wait_time
    assert wait.__closure__[0].cell_contents == 2.0
    # инвариант деления: другой таргет/пул → per-user меняется (20/10 = 2.0 → wait 0.5)
    wait_hot = rps_wait_time(20, 10)
    assert wait_hot.__closure__[0].cell_contents == 0.5


# endregion TEST_rps_wait_time_constant_throughput


# region TEST_rps_wait_time_fallback
# 🧪 TRAP[TEST] · Scenario: RPS=0 или users=0 → between(0.05, 0.2) fallback (без RPS-контроля)
# · Regression: деление на 0 при users=0 или выход за границы между при отсутствии LT_TARGET_RPS
# · Last fail: N/A (new) — 146-m1 BUG-1 fix
# · Remove if: fallback-семантика сценариев изменена
def test_rps_wait_time_fallback(monkeypatch, caplog) -> None:
    """rps_wait_time(0, 10) и rps_wait_time(10, 0) → between(0.05, 0.2) (без RPS-контроля)."""
    monkeypatch.setenv("LOCUST_SKIP_MONKEY_PATCH", "1")  # gevent/ssl-конфликт — см. TRAP[BUG]
    pytest.importorskip("locust", reason=_LOCUST_REASON)
    from core.loadtest.scenarios import rps_wait_time  # locust-dependent — импорт внутри функции

    caplog.set_level(logging.INFO)
    wait = rps_wait_time(0, 10)  # target_rps=0 → fallback
    wait_no_users = rps_wait_time(10, 0)  # users=0 → fallback (защита деления на 0)
    logger.info("[IMP:9][test][rps_wait_time] fallback between(0.05, 0.2) при rps<=0 или users<=0")
    _assert_ldd_imp9(caplog)
    for w in (wait, wait_no_users):
        assert callable(w)
        cells = sorted(c.cell_contents for c in w.__closure__)
        assert cells == [0.05, 0.2]  # between(min=0.05, max=0.2)


# endregion TEST_rps_wait_time_fallback
