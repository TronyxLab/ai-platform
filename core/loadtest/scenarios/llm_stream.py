# GREP_SUMMARY: locust llm-stream scenario SSE streaming chat completions custom client chunk-timeout
# STRUCTURE: ▶ env LT_STREAM/LT_CHUNK_TIMEOUT → ◇ LlmStreamUser(HttpUser) → ○ task stream POST → ○ gevent.Timeout chunk-read → ⎋
# region MODULE_CONTRACT
## @purpose  Locust-сценарий llm_stream (DevPlan 146 W1): кастомный SSE-клиент —
##           stream=true через self.client.stream (geventhttpclient), чтение чанков с
##           явным chunk-timeout (gevent.Timeout, 10s из SoT LT_CHUNK_TIMEOUT).
##           Покрывает риск R4 «Locust SSE-клиент не покрывает chunk-timeout».
## @scope    Запускается ТОЛЬКО locust — локально или в locustio/locust:2.32.10 на ноде.
##           НЕ импортируется платформенным кодом.
## @invariants
##   - Чтение каждого чанка ограничено gevent.Timeout(LT_CHUNK_TIMEOUT) — зависший
##     стрим фиксируется как failure("chunk timeout"), а не висит до общего таймаута
##   - Тело содержит stream=true и model=LT_MODEL (mock-echo)
##   - Точный RPS — constant_throughput (wait_time = rps_wait_time(LT_TARGET_RPS,
##     LT_USERS), единый helper — 146-m1 TASK-2/3); users — размер пула
## @rationale SSE-стримы (litellm stream=true) — другой профиль нагрузки (долгие ответы,
##            чанки, удержание соединения) — отдельный сценарий с собственным RPS (5/s).
## @changes  2026-08-11 | DevPlan 146 W1 — Created
# endregion MODULE_CONTRACT

import json
import os
import sys
from pathlib import Path

from locust import HttpUser, task
from locust.clients import ResponseContextManager

# RPS-механизм (DevPlan 146-m1 TASK-3): общий helper rps_wait_time из пакета scenarios.
# locust грузит -f файл как top-level модуль (load_locustfile: module_name = basename),
# поэтому относительный импорт `from . import rps_wait_time` невозможен — добавляем
# корень пакета в sys.path и импортируем top-level (local, remote-контейнер и pytest).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from typing import cast  # pyright: ignore[reportImplicitRelativeImport]

from scenarios import rps_wait_time  # pyright: ignore[reportImplicitRelativeImport]

LT_ENDPOINT: str = os.environ.get("LT_ENDPOINT", "").strip().rstrip("/")
HTTP_OK: int = 200  # успешный статус ответа LLM-провайдера
LT_PATH: str = os.environ.get("LT_PATH", "/chat/completions")
# W11: json.loads → Any → cast к границе JSON (body — произвольная структура, headers — str:str)
LT_BODY: dict[str, object] = cast("dict[str, object]", json.loads(os.environ.get("LT_BODY", "{}")))
LT_HEADERS: dict[str, str] = cast("dict[str, str]", json.loads(os.environ.get("LT_HEADERS", "{}")))
LT_CHUNK_TIMEOUT: float = float(os.environ.get("LT_CHUNK_TIMEOUT", "10"))
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


# region CLASS_LlmStreamUser
class LlmStreamUser(HttpUser):
    """Пользователь llm_stream: SSE-стрим /chat/completions с chunk-timeout.

    ▶ ┌host=LT_ENDPOINT┐ → ○ with client.stream(POST, stream=true) → ○ gevent.Timeout
      ∋ chunk: iter_lines → ◇ Timeout → failure("chunk timeout") | успех → success() → ⎋

    ## @purpose  Streaming-нагрузка на litellm (mock-echo, stream=true). Кастомный клиент:
    ##            locust .stream() + gevent.Timeout вокруг чтения чанков — зависший стрим
    ##            детектируется за LT_CHUNK_TIMEOUT (риск R4 DevPlan 146 §7).
    ## @io — ⇥ env (модульный уровень) → ⎋ HTTP-SSE-запросы в цикле задач
    ## @invariants
    ##   - catch_response=True — явный success()/failure() по результату чтения
    ##   - Время чтения всего стрима ограничено chunk-timeout × чанки (per-chunk)
    ##   - verify=False при LT_SSL_VERIFY=false
    """

    host = LT_ENDPOINT
    wait_time = rps_wait_time(LT_TARGET_RPS, LT_USERS)

    @task
    def stream_chat_completions(self) -> None:
        """POST stream=true и чтение SSE-чанков с per-chunk таймаутом."""
        from gevent import (  # pyright: ignore[reportMissingModuleSource] — locust runtime (gevent — зависимость locust, в CI-венве load-extra не ставится)
            Timeout,
        )

        # BUG-8/8v2 (148 W3 r3-r5): для catch_response=True locust ТРЕБУЕТ with-блок
        # (иначе LocustError «request has not yet been made»); при этом __exit__ без явного
        # close() drain'ит оставшееся тело стрима → 40-60s блокировка. Решение (проверено
        # изолированно на VPS, r5): response.close() ВНУТРИ with ДО __exit__ — закрывает
        # response.raw._fp → при выходе из with drain возвращает b'' (мгновенно).
        with self.client.post(
            LT_PATH,
            json=LT_BODY,
            headers=LT_HEADERS or None,
            catch_response=True,
            stream=True,
            verify=LT_SSL_VERIFY,
        ) as resp_ctx:
            # locust.post() при catch_response=True возвращает ResponseContextManager
            # (union-сигнатура post → ResponseContextManager | Response | LocustResponse;
            # success()/failure() объявлены ТОЛЬКО на ResponseContextManager) → cast к классу
            response = cast(ResponseContextManager, resp_ctx)
            try:
                with Timeout(LT_CHUNK_TIMEOUT):
                    for _chunk in response.iter_lines():
                        pass
            except Timeout:
                response.failure(f"chunk timeout ({LT_CHUNK_TIMEOUT}s)")  # pyright: ignore[reportUnknownMemberType] — W11 external locust failure(exc) untyped
            else:
                if response.status_code == HTTP_OK:
                    response.success()
                else:
                    response.failure(f"HTTP {response.status_code}")  # pyright: ignore[reportUnknownMemberType] — W11 external locust failure(exc) untyped
            response.close()  # внутри with, до __exit__ — без drain-блокировки


# endregion CLASS_LlmStreamUser
