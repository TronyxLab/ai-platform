# GREP_SUMMARY: locust llm scenario litellm chat completions mock POST json
# STRUCTURE: ▶ env LT_ENDPOINT/LT_PATH/LT_BODY/LT_MODEL → ◇ LlmUser(HttpUser) → ○ task POST /chat/completions → ⎋
# region MODULE_CONTRACT
## @purpose  Locust-сценарий llm (DevPlan 146 W1): POST /chat/completions на litellm
##           (mock-модель mock-echo из litellm-config.mock.yml, детерминизм AC6). Тело и
##           заголовки — из env (LT_*), заполняются config.py из core/loadtest/scenarios.yaml.
## @scope    Запускается ТОЛЬКО locust — локально или в locustio/locust:2.32.10 на ноде.
##           НЕ импортируется платформенным кодом.
## @invariants
##   - model в теле — из LT_MODEL (SoT: mock-echo); без mock-конфига на ноде runner
##     делает ранний FAIL с сообщением (AC6) ДО запуска генератора
##   - Точный RPS — constant_throughput (wait_time = rps_wait_time(LT_TARGET_RPS,
##     LT_USERS), единый helper — 146-m1 TASK-2/3); users — размер пула
##   - LT_ENABLED != "true" → немедленный выход (защита прямого запуска)
## @rationale LLM-сценарии гоняются только против mock-модели (echo) — детерминированный
##            ответ и стабильная латентность (~50ms) делают метрики воспроизводимыми (AC6).
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
LT_PATH: str = os.environ.get("LT_PATH", "/chat/completions")
LT_BODY: dict = json.loads(os.environ.get("LT_BODY", "{}"))
LT_HEADERS: dict = json.loads(os.environ.get("LT_HEADERS", "{}"))
LT_SSL_VERIFY: bool = os.environ.get("LT_SSL_VERIFY", "false").lower() == "true"
LT_TARGET_RPS: float = float(os.environ.get("LT_TARGET_RPS", "0"))
LT_USERS: int = int(os.environ.get("LT_USERS", "1"))


# region FUNC__guard_enabled
def _guard_enabled() -> None:
    """Early exit if scenario disabled (защита прямого запуска без runner).

    ▶ ┌env LT_ENABLED┐ → ◇ != "true" → sys.exit(2) → ⎋ None

    ## @purpose  Аналогична web._guard_enabled — единый контракт LT_ENABLED у всех сценариев.
    ## @io — ⇥ None → ⎋ None | sys.exit(2)
    ## @complexity — O(1)
    """
    if os.environ.get("LT_ENABLED", "true").lower() != "true":
        sys.exit("scenario disabled (LT_ENABLED != true) — включите LOAD_SCENARIO_<NAME>=1")


# endregion FUNC__guard_enabled


_guard_enabled()


# region CLASS_LlmUser
class LlmUser(HttpUser):
    """Пользователь llm-сценария: POST /chat/completions (non-stream) с телом из SoT.

    ▶ ┌host=LT_ENDPOINT┐ → ○ task POST LT_PATH (json=LT_BODY, headers=LT_HEADERS) → ○ sleep → ⎋

    ## @purpose  Non-stream нагрузка на litellm proxy (mock-echo). host = LT_ENDPOINT
    ##            (rendered: http://{host}:4000 — litellm без nginx-vhost, DevPlan 146 §3.2).
    ## @io — ⇥ env (модульный уровень) → ⎋ HTTP-запросы в цикле задач
    ## @invariants
    ##   - Тело содержит model=LT_MODEL (mock-echo) — детерминизм (AC6)
    ##   - verify=False при LT_SSL_VERIFY=false
    """

    host = LT_ENDPOINT
    wait_time = rps_wait_time(LT_TARGET_RPS, LT_USERS)

    @task
    def chat_completions(self) -> None:
        """POST /chat/completions с body из SoT (LT_BODY)."""
        self.client.post(
            LT_PATH,
            json=LT_BODY,
            headers=LT_HEADERS or None,
            verify=LT_SSL_VERIFY,
        )


# endregion CLASS_LlmUser
