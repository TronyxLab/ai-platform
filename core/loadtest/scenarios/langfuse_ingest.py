# GREP_SUMMARY: locust langfuse scenario traces ingest public api POST langfuse
# STRUCTURE: ▶ env LT_ENDPOINT/LT_PATH/LT_HEADERS/LT_BODY → ◇ LangfuseIngestUser(HttpUser) → ○ task POST traces → ⎋
# region MODULE_CONTRACT
## @purpose  Locust-сценарий langfuse_ingest (DevPlan 146 W1): POST /api/public/traces —
##           нагрузка на langfuse + postgres + clickhouse (трассировочный инжест).
##           Authorization: Bearer {LANGFUSE_PUBLIC_KEY} — из env LOAD_LANGFUSE_PUBLIC_KEY
##           (секреты ноды), подставляется config.py при рендере headers.
## @scope    Запускается ТОЛЬКО locust — локально или в locustio/locust:2.32 на ноде.
##           НЕ импортируется платформенным кодом.
## @invariants
##   - headers целиком из LT_HEADERS (rendered config.py: публичный ключ langfuse)
##   - Точный RPS — constant_throughput (wait_time = rps_wait_time(LT_TARGET_RPS,
##     LT_USERS), единый helper — 146-m1 TASK-2/3); users — размер пула
##   - LT_ENABLED != "true" → немедленный выход (защита прямого запуска)
## @rationale Инжест трасс — самый нагруженный путь langfuse (запись в postgres+clickhouse);
##            отдельный сценарий с низким RPS (5/s) — не валит backend, но виден в saturation.
## @changes  2026-08-11 | DevPlan 146 W1 — Created
# endregion MODULE_CONTRACT

import json
import os
import sys
from pathlib import Path

from locust import HttpUser, task

# RPS-механизм (DevPlan 146-m1 TASK-3): общий helper rps_wait_time из пакета scenarios.
# locust грузит -f файл как top-level модуль (load_locustfile: module_name = basename),
# поэтому относительный импорт `from . import rps_wait_time` невозможен — добавляем
# корень пакета в sys.path и импортируем top-level (local, remote-контейнер и pytest).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scenarios import rps_wait_time

LT_ENDPOINT: str = os.environ.get("LT_ENDPOINT", "").strip().rstrip("/")
LT_PATH: str = os.environ.get("LT_PATH", "/api/public/traces")
LT_BODY: dict = json.loads(os.environ.get("LT_BODY", "{}"))
LT_HEADERS: dict = json.loads(os.environ.get("LT_HEADERS", "{}"))
LT_SSL_VERIFY: bool = os.environ.get("LT_SSL_VERIFY", "false").lower() == "true"
LT_TARGET_RPS: float = float(os.environ.get("LT_TARGET_RPS", "0"))
LT_USERS: int = int(os.environ.get("LT_USERS", "1"))


# region FUNC__guard_enabled
def _guard_enabled() -> None:
    """Early exit if scenario disabled (защита прямого запуска без runner).

    ▶ ┌env LT_ENABLED┐ → ◇ != "true" → sys.exit(2) → ⎋ None

    ## @purpose  Единый контракт LT_ENABLED у всех сценариев (см. web.py).
    ## @io — ⇥ None → ⎋ None | sys.exit(2)
    ## @complexity — O(1)
    """
    if os.environ.get("LT_ENABLED", "true").lower() != "true":
        sys.exit("scenario disabled (LT_ENABLED != true) — включите LOAD_SCENARIO_<NAME>=1")


# endregion FUNC__guard_enabled


_guard_enabled()


# region CLASS_LangfuseIngestUser
class LangfuseIngestUser(HttpUser):
    """Пользователь langfuse_ingest: POST /api/public/traces (инжест трасс).

    ▶ ┌host=LT_ENDPOINT┐ → ○ task POST LT_PATH (json=LT_BODY, headers=LT_HEADERS) → ○ sleep → ⎋

    ## @purpose  Инжест трассировок через public API langfuse (langfuse.{domain} — SoT
    ##            конвенция 146-m1 BUG-2; per-node override: LOAD_ENDPOINT_LANGFUSE_INGEST).
    ##            host = LT_ENDPOINT (rendered); ключ — в LT_HEADERS.
    ## @io — ⇥ env (модульный уровень) → ⎋ HTTP-запросы в цикле задач
    ## @invariants
    ##   - Authorization из LT_HEADERS (config.py подставил LOAD_LANGFUSE_PUBLIC_KEY)
    ##   - verify=False при LT_SSL_VERIFY=false
    """

    host = LT_ENDPOINT
    wait_time = rps_wait_time(LT_TARGET_RPS, LT_USERS)

    @task
    def ingest_trace(self) -> None:
        """POST /api/public/traces с телом-фикстурой из SoT (LT_BODY)."""
        self.client.post(
            LT_PATH,
            json=LT_BODY,
            headers=LT_HEADERS or None,
            verify=LT_SSL_VERIFY,
        )


# endregion CLASS_LangfuseIngestUser
