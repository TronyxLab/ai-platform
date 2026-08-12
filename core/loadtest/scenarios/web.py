# GREP_SUMMARY: locust web scenario nginx static status-page GET platform
# STRUCTURE: ▶ env LT_ENDPOINT/LT_PATHS/LT_SSL_VERIFY → ◇ WebUser(HttpUser) → ○ task (weighted GET paths) → ⎋
# region MODULE_CONTRACT
## @purpose  Locust-сценарий web (DevPlan 146 W1): GET-пути платформы (nginx front,
##           status-page). Все параметры — из env (LT_*), НИКАКИХ хардкодов (инвариант 2):
##           endpoint/пути/ssl_verify заполняет config.py из core/loadtest/scenarios.yaml.
## @scope    Запускается ТОЛЬКО locust (locust -f scenarios/web.py) — локально (dev-машина)
##           или в locustio/locust:2.32.10 на ноде (LOAD_RUNNER=node). НЕ импортируется
##           платформенным кодом (core/internal/loadtest/).
## @invariants
##   - Точный RPS — constant_throughput (wait_time = rps_wait_time(LT_TARGET_RPS,
##     LT_USERS), единый helper из __init__.py — 146-m1 TASK-2/3); users — размер пула
##   - Пути — из LT_PATHS (JSON-список строк); ssl_verify — LT_SSL_VERIFY ("true"/"false")
##   - LT_ENABLED != "true" → немедленный выход с сообщением (защита прямого запуска)
## @rationale Locust-сценарии — executable-спецификация SoT: читают env, не дублируют
##            значения scenarios.yaml (единая точка правок — YAML, DevPlan 146 §3.1).
## @changes  2026-08-11 | DevPlan 146 W1 — Created
# endregion MODULE_CONTRACT

import json
import os
import random
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
LT_PATHS: list[str] = json.loads(os.environ.get("LT_PATHS", '["/", "/status"]'))
LT_SSL_VERIFY: bool = os.environ.get("LT_SSL_VERIFY", "false").lower() == "true"
LT_TARGET_RPS: float = float(os.environ.get("LT_TARGET_RPS", "0"))
LT_USERS: int = int(os.environ.get("LT_USERS", "1"))


# region FUNC__guard_enabled
def _guard_enabled() -> None:
    """Early exit if scenario disabled (optional-сценарии: прямой запуск без runner).

    ▶ ┌env LT_ENABLED┐ → ◇ != "true" → sys.exit(2) → ⎋ None

    ## @purpose  Защита прямого запуска выключенного optional-сценария (db/s3): locust
    ##            падает с понятным сообщением, а не молча гоняет пустую нагрузку.
    ## @io — ⇥ None → ⎋ None | sys.exit(2)
    ## @complexity — O(1)
    """
    if os.environ.get("LT_ENABLED", "true").lower() != "true":
        sys.exit("scenario disabled (LT_ENABLED != true) — включите LOAD_SCENARIO_<NAME>=1")


# endregion FUNC__guard_enabled


_guard_enabled()


# region CLASS_WebUser
class WebUser(HttpUser):
    """Пользователь web-сценария: GET по путям платформы с равными весами.

    ▶ ┌host=LT_ENDPOINT┐ → ○ task GET path (вес 1) → ○ sleep(wait_time) → ⎋

    ## @purpose  GET-нагрузка на nginx front (главная + status-page). host задаётся из
    ##            LT_ENDPOINT (rendered endpoint сценария web из scenarios.yaml).
    ## @io — ⇥ env (модульный уровень) → ⎋ HTTP-запросы в цикле задач
    ## @invariants
    ##   - host = LT_ENDPOINT (rendered config.py: https://{domain}/ → домен или host ноды)
    ##   - wait_time = rps_wait_time(LT_TARGET_RPS, LT_USERS): constant_throughput при
    ##     заданном RPS (latency-адаптивно), иначе fallback between(0.05, 0.2)
    ##   - verify=False при LT_SSL_VERIFY=false (тестовые ноды: самоподписанные серты)
    """

    host = LT_ENDPOINT
    wait_time = rps_wait_time(LT_TARGET_RPS, LT_USERS)

    @task
    def get_paths(self) -> None:
        """GET-запросы по путям LT_PATHS (один случайный путь на итерацию)."""
        path = random.choice(LT_PATHS) if LT_PATHS else "/"
        self.client.get(path, verify=LT_SSL_VERIFY)


# endregion CLASS_WebUser
