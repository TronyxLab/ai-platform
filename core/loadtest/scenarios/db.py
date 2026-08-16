# GREP_SUMMARY: locust db scenario postgres pgwire wire-protocol read write optional scram md5 loadtest_metrics
# STRUCTURE: ▶ env LT_ENABLED (optional gate) → ◇ sys.path hack (rps_wait_time) → ◇ DbUser(User):
#           on_start (connect + CREATE TABLE IF NOT EXISTS + DELETE) → ○ task read_query
#           (SELECT count(*)) / write_query (INSERT payload loadtest-<n>) через pgwire →
#           PgError → raise (locust failure) → ⎋ on_stop close
# region MODULE_CONTRACT
## @purpose  Locust-сценарий db (DevPlan 148 TASK-2, OPTIONAL): реальная read/write нагрузка
##           на PostgreSQL через чистый stdlib PG wire protocol (pgwire.py) — НЕ HTTP-заглушка
##           146 W1 (GET по несуществующему мосту). PostgreSQL доступен ТОЛЬКО в docker-сети
##           shared-db-net (NO ports: directive) → прогон с LOAD_RUNNER=node +
##           LOAD_NETWORK=shared-db-net. Задачи read_query (SELECT count(*)) и write_query
##           (INSERT INTO loadtest_metrics (payload) VALUES ('loadtest-<n>')) с весом 1:1 —
##           per-task статистика (rps/p95/p99 записи vs чтения) в отчёте.
## @scope    Запускается ТОЛЬКО locust — локально или в locustio/locust:2.32.10 на ноде.
##           НЕ импортируется платформенным кодом. pgwire.py — чистый stdlib-модуль в том
##           же пакете (rsync-ом доставляется на ноду вместе со сценарием).
## @invariants
##   - optional-контракт: LT_ENABLED != "true" → sys.exit(2) ДО создания user-классов
##   - RPS — constant_throughput (wait_time = rps_wait_time(LT_TARGET_RPS, LT_USERS),
##     единый helper — 146-m1 TASK-2/3); users — размер пула
##   - on_start — идемпотентный старт: CREATE TABLE IF NOT EXISTS loadtest_metrics
##     (id bigserial primary key, ts timestamptz default now(), payload text) + DELETE
##     FROM loadtest_metrics (чистая таблица между прогонами)
##   - PgError (auth/сеть/SQL-ошибка сервера) → raise из задачи → locust фиксирует failure
##   - LT_PG_TABLE — строгий идентификатор [A-Za-z_][A-Za-z0-9_]* (fail-fast, SQL-injection
##     из env исключена — Simple Query без параметров)
## @rationale db-сценарий — «дыра» подсистемы (146 W1: 87 строк GET-заглушки без HTTP-пути).
##            Wire protocol на stdlib — тот же приём, что s3.py (SigV4 без boto3): ноль новой
##            инфраструктуры, unit-тестируемо, работает в locust-образе. Чистая таблица на
##            старте → read_query читает ТОЛЬКО write_query-данные текущего прогона
##            (детерминированная per-task статистика).
## @changes  2026-08-12 | DevPlan 148 TASK-2 — REWRITE (была GET-заглушка 146 W1)
# endregion MODULE_CONTRACT

import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from locust import User, task
from locust.env import Environment

# RPS-механизм (DevPlan 146-m1 TASK-3): общий helper rps_wait_time из пакета scenarios.
# locust грузит -f файл как top-level модуль (load_locustfile: module_name = basename),
# поэтому относительный импорт `from . import rps_wait_time` невозможен — добавляем
# корень пакета в sys.path и импортируем top-level (local, remote-контейнер и pytest).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scenarios import rps_wait_time  # pyright: ignore[reportImplicitRelativeImport]
from scenarios.pgwire import (  # pyright: ignore[reportImplicitRelativeImport]
    PGSocket,  # чистый stdlib PG wire protocol (148 TASK-1) — тип соединения для аннотации _conn
    connect,
)

LT_ENDPOINT: str = os.environ.get("LT_ENDPOINT", "").strip()
LT_PG_USER: str = os.environ.get("LT_PG_USER", "postgres")
LT_PG_PASSWORD: str = os.environ.get("LT_PG_PASSWORD", "")
LT_PG_DB: str = os.environ.get("LT_PG_DB", "platform")
LT_PG_TABLE: str = os.environ.get("LT_PG_TABLE", "loadtest_metrics")
LT_TARGET_RPS: float = float(os.environ.get("LT_TARGET_RPS", "0"))
LT_USERS: int = int(os.environ.get("LT_USERS", "1"))

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# region FUNC__guard_enabled
def _guard_enabled() -> None:
    """Early exit if scenario disabled (optional-контракт db/s3).

    ▶ ┌env LT_ENABLED┐ → ◇ != "true" → sys.exit(2) → ⎋ None

    ## @purpose  Optional-сценарий вне runner не выполняется: locust падает с сообщением.
    ## @io — ⇥ None → ⎋ None | sys.exit(2)
    ## @complexity — O(1)
    """
    if os.environ.get("LT_ENABLED", "false").lower() != "true":
        sys.exit("scenario disabled (LT_ENABLED != true) — включите LOAD_SCENARIO_DB=1")


# endregion FUNC__guard_enabled


# region FUNC__parse_endpoint
def _parse_endpoint(endpoint: str) -> tuple[str, int]:
    """Разбор LT_ENDPOINT "host:port" → (host, port) — порт по умолчанию 5432.

    ▶ ┌endpoint┐ → ◇ пустой → sys.exit → ◇ "host:port" → (host, int(port)) → ⎋ (endpoint, 5432)

    ## @purpose  LT_ENDPOINT для db — "postgres:5432" (DNS-алиас shared-db-net, БЕЗ схемы —
    ##            это не HTTP!). Нечисловой порт → fail-fast (конфигурационная ошибка).
    ## @io — ⇥ endpoint: str → ⎋ (host: str, port: int)
    ## @complexity — O(1)
    """
    if not endpoint:
        sys.exit("LT_ENDPOINT не задан — ожидается host:port (например postgres:5432 в shared-db-net)")
    if ":" in endpoint:
        host, _, port = endpoint.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            sys.exit(f"LT_ENDPOINT={endpoint!r}: порт не число (ожидается host:port)")
    return endpoint, 5432


# endregion FUNC__parse_endpoint


_guard_enabled()

if not _SAFE_IDENTIFIER_RE.fullmatch(LT_PG_TABLE):
    sys.exit(
        f"LT_PG_TABLE={LT_PG_TABLE!r} — недопустимое имя таблицы "
        "(только [A-Za-z_][A-Za-z0-9_]*; Simple Query без параметров — идентификатор не экранируется)"
    )

_lt_host, _lt_port = _parse_endpoint(LT_ENDPOINT)


# region CLASS_DbUser
class DbUser(User):
    """Пользователь db-сценария: read/write задачи по PG wire protocol (не HTTP).

    ▶ ┌host=LT_ENDPOINT┐ → ○ on_start: connect + CREATE TABLE IF NOT EXISTS + DELETE →
      ○ task read_query (SELECT count(*)) / task write_query (INSERT loadtest-<n>) вес 1:1
      → ○ on_stop: close → ⎋

    ## @purpose  Read/write нагрузка на PostgreSQL (DevPlan 148 TASK-2, SC_DB_RW): каждая
    ##            задача — query() через общее соединение пользователя; PgError пробрасывается
    ##            (locust failure). Вес 1:1 → locust stats.csv содержит read_query/write_query
    ##            строки — per-task rps/p95/p99 записи vs чтения в отчёте.
    ## @io — ⇥ env (модульный уровень) → ⎋ PG-запросы в цикле задач
    ## @invariants
    ##   - НЕ HttpUser: собственный транспорт (pgwire.PGSocket), клиент не используется
    ##   - read_query: SELECT count(*) FROM loadtest_metrics (скорость чтения)
    ##   - write_query: INSERT INTO loadtest_metrics (payload) VALUES ('loadtest-<n>')
    ##     (скорость записи; <n> — per-user счётчик, payload экранируется — только алфавит)
    ##   - on_start идемпотентен (CREATE TABLE IF NOT EXISTS + DELETE) — чистая таблица
    ##   - PgError → НЕ ловится → locust фиксирует failure задачи (error_rate)
    """

    wait_time = rps_wait_time(LT_TARGET_RPS, LT_USERS)

    # W11: аннотации инстанс-атрибутов (class не @final — reportUnannotatedClassAttribute/
    # reportOptionalMemberAccess без явного типа); _conn — PGSocket | None (on_stop guard)
    _conn: PGSocket | None = None
    _write_seq: int = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)

    def on_start(self) -> None:
        """Подключение + инициализация таблицы (CREATE IF NOT EXISTS + DELETE) — идемпотентно.

        ▶ ┌env┐ → ○ connect(_lt_host, _lt_port, user/password/db) → ○ CREATE TABLE IF NOT EXISTS
          → ○ DELETE FROM loadtest_metrics → ⎋ None | PgError (locust spawn failure)

        ## @purpose  Чистая таблица между прогонами (инвариант MODULE_CONTRACT): read_query
        ##            читает ТОЛЬКО write_query-данные текущего прогона — per-task статистика
        ##            детерминирована. Ошибка подключения → PgError на старте пользователя.
        ## @io — ⇥ None → ⎋ None | PgError
        ## @complexity — O(1) — 2 простых запроса
        """
        self._conn = connect(_lt_host, _lt_port, LT_PG_USER, LT_PG_PASSWORD, LT_PG_DB)
        # nosec B608: LT_PG_TABLE валидирован _SAFE_IDENTIFIER_RE (^[A-Za-z_][A-Za-z0-9_]*$) на
        # module-уровне — не пользовательский ввод; Simple Query не имеет параметров (см. инвариант)
        self._conn.query(
            f"CREATE TABLE IF NOT EXISTS {LT_PG_TABLE} "
            "(id bigserial primary key, ts timestamptz default now(), payload text)"  # nosec B608
        )
        self._conn.query(f"DELETE FROM {LT_PG_TABLE}")  # nosec B608

    def on_stop(self) -> None:
        """Закрытие соединения (Terminate + close, идемпотентно)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @task
    def read_query(self) -> None:
        """SELECT count(*) FROM loadtest_metrics — «скорость чтения» (per-task строка stats)."""
        self._fire_query("read_query", lambda: self._conn.query(f"SELECT count(*) FROM {LT_PG_TABLE}"))  # nosec B608 — LT_PG_TABLE валидирован regex

    @task
    def write_query(self) -> None:
        """INSERT INTO loadtest_metrics (payload) VALUES ('loadtest-<n>') — «скорость записи»."""
        self._write_seq += 1
        # nosec B608: LT_PG_TABLE валидирован _SAFE_IDENTIFIER_RE; payload — алфавитно-цифровой
        # 'loadtest-<n>' (никаких кавычек/метасимволов) — инъекция невозможна
        self._fire_query(
            "write_query",
            lambda: self._conn.query(
                f"INSERT INTO {LT_PG_TABLE} (payload) VALUES ('loadtest-{self._write_seq}')"  # nosec B608
            ),
        )

    def _fire_query(self, name: str, fn: Callable[[], object]) -> None:
        """Выполнение SQL + fire события locust (кастомный transport — НЕ HttpUser).

        ▶ ┌name, fn┐ → ○ t0 → ○ fn() → ○ t1 → ○ events.request.fire (PG, name, ms) →
          ◇ exc → fire(exception) + raise → ⎋ None

        ## @purpose  DbUser(User) не имеет встроенного клиента (не HttpUser) — без явного
        ##            events.request.fire() locust НЕ засчитывает запросы в stats (BUG-4,
        ##            148 W3 r2: SQL выполнялся, stats=0 → verdict FAIL). fire() с
        ##            request_type="PG" и name=задачи → per-task строки read_query/write_query
        ##            в stats.csv (per-task отчёт). Исключение → fire(exception=exc) —
        ##            locust фиксирует failure, затем raise (незамаскированная ошибка).
        ## @io — ⇥ name: str (имя задачи = строка stats), fn: Callable[[], object]
        ##       → ⎋ None | raise исключения из fn
        ## @complexity — O(1) — один запрос + fire
        ## @changes  2026-08-15 | DevPlan 170 W11 — fn: Callable (было untyped), cast environment
        """
        start = time.perf_counter()
        exception: BaseException | None = None
        try:
            fn()
        # ruff: ignore[BLE001] — locust task wrapper (BUG-4, 148 W3 r2): перехват для
        # fire(exception=) в finally, затем re-raise — locust stats требует exception, тихого
        # маскирования нет (raise exception после fire)
        except Exception as exc:  # noqa: EXC — locust fire(exception=) требует перехвата (BUG-4)
            exception = exc
        finally:
            # W11: locust User.environment — untyped параметр __init__ → Unknown-цепочка;
            # cast к типизированному locust.env.Environment (events.request.fire — typed)
            env = cast(Environment, self.environment)
            env.events.request.fire(  # pyright: ignore[reportUnknownMemberType] — W11 external locust EventHook.fire(**kwargs) untyped
                request_type="PG",
                name=name,
                response_time=(time.perf_counter() - start) * 1000,
                response_length=0,
                response=None,
                exception=exception,
                context=self.context(),  # pyright: ignore[reportUnknownMemberType] — W11 external locust User.context() untyped
            )
        if exception is not None:
            raise exception


# endregion CLASS_DbUser
