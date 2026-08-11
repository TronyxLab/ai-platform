# GREP_SUMMARY: locust db scenario postgres pgbouncer optional SQL read
# STRUCTURE: ▶ env LT_ENABLED (optional gate) → ◇ DbUser(HttpUser) → ○ task GET paths → ⎋
# region MODULE_CONTRACT
## @purpose  Locust-сценарий db (DevPlan 146 W1, OPTIONAL): read-нагрузка на PostgreSQL
##           через HTTP-путь (pgbouncer admin / API-прокси). PostgreSQL не имеет нативного
##           HTTP — сценарий помечен optional в SoT и по умолчанию ВЫКЛЮЧЕН (LT_ENABLED=false):
##           включается только LOAD_SCENARIO_DB=1, когда оператор указал рабочий HTTP-endpoint
##           (LT_ENDPOINT из config.py). Ограничение задокументировано в docs/load-testing.md.
## @scope    Запускается ТОЛЬКО locust — локально или в locustio/locust:2.32 на ноде.
##           НЕ импортируется платформенным кодом.
## @invariants
##   - optional-контракт: LT_ENABLED != "true" → sys.exit(2) ДО создания user-классов
##   - GET-пути из LT_PATHS (JSON); RPS — locust --max-rps
## @rationale db-сценарий — заглушка-контракт для будущего HTTP-моста к pgbouncer
##            (DevPlan 146 §3.1: «если HTTP-пути нет — optional и пропускается»);
##            сам PostgreSQL в saturation-секции отчёта (pg_stat_database_numbackends).
## @changes  2026-08-11 | DevPlan 146 W1 — Created
# endregion MODULE_CONTRACT

import json
import os
import sys

from locust import HttpUser, between, task

LT_ENDPOINT: str = os.environ.get("LT_ENDPOINT", "").strip().rstrip("/")
LT_PATHS: list[str] = json.loads(os.environ.get("LT_PATHS", '["/"]'))
LT_SSL_VERIFY: bool = os.environ.get("LT_SSL_VERIFY", "false").lower() == "true"


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


_guard_enabled()


# region CLASS_DbUser
class DbUser(HttpUser):
    """Пользователь db-сценария: GET по HTTP-путям read-доступа к PostgreSQL.

    ▶ ┌host=LT_ENDPOINT┐ → ○ task GET path → ○ sleep → ⎋

    ## @purpose  Read-нагрузка на pg-слой через HTTP-endpoint (если оператор предоставил
    ##            путь в SoT/реализацию моста). По умолчанию сценарий выключен (optional).
    ## @io — ⇥ env (модульный уровень) → ⎋ HTTP-запросы в цикле задач
    ## @invariants
    ##   - host = LT_ENDPOINT (config.py: http://{host}:5432 — pgbouncer-порт по умолчанию)
    ##   - verify=False при LT_SSL_VERIFY=false
    """

    host = LT_ENDPOINT
    wait_time = between(0.05, 0.2)

    @task
    def read_query(self) -> None:
        """GET по read-пути (LT_PATHS)."""
        path = LT_PATHS[0] if LT_PATHS else "/"
        self.client.get(path, verify=LT_SSL_VERIFY)


# endregion CLASS_DbUser
