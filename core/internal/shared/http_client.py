#!/usr/bin/env python3
# GREP_SUMMARY: http-client, shared, urllib, json-get, json-post, proxy, timeout, thin-client, opener-injection
# STRUCTURE: ▶ request() ┌url, method, timeout, headers, data┐ → ◇ build_opener (proxy|env) → ○ Request → ○ opener.open → ⎋ HTTPResponse
#           → ◇ get_json → ⊕ json.loads → ⎋ object | HttpRequestError | HttpJsonError → ◇ post_json → ⊕ JSON body → ⎋ HTTPResponse
# region MODULE_CONTRACT
## @purpose  Тонкий shared HTTP-клиент (DevPlan 177 W3.2) — консолидация urllib-кода
##           5 потребителей: service_reload, langfuse_projects, prometheus_pull,
##           runner_cli, healthcheck_poller. НЕ надстройка над httpx — plain
##           http.client/urllib по семантике текущих потребителей (прямое замещение).
## @scope    Все Python-модули core/internal, выполняющие короткие HTTP-запросы
##           (reload, langfuse API, PromQL-pull, mock-probe, healthcheck). admin_client
##           (httpx, P3-решение) и telegram_notifier (канон Telegram) — НЕ мигрируются.
## @invariants
##   1. DEFAULT_HTTP_TIMEOUT = DOCKER_CMD_TIMEOUT (10s) из timeouts.py — единственный
##      источник дефолта (0 литералов timeout= здесь).
##   2. request()/post_json() — низкий уровень: возвращают http.client.HTTPResponse и
##      ПРОБРАСЫВАЮТ urllib.error.HTTPError/URLError/OSError/TimeoutError как есть —
##      потребители сохраняют свою не-фатальную семантику (409-идемпотентность,
##      mock-probe detail, non-fatal bool).
##   3. get_json() — JSON-граница (W11): сетевые ошибки → HttpRequestError, битый JSON
##      → HttpJsonError (потребитель оборачивает в доменные исключения, напр.
##      PrometheusError).
##   4. proxy_url=None → build_opener() — env-прокси urllib (HTTP_PROXY/HTTPS_PROXY/
##      NO_PROXY через getproxies); proxy_url задан → ProxyHandler({http, https}) —
##      семантика telegram_notifier (канон).
##   5. opener-параметр (инъекция в тестах — 0 реальных сетевых вызовов).
## @rationale DevPlan 177 W3.2: −6 дублей HTTP-кода (метрика «HTTP-клиенты 7 → 2»).
##            Транспорт остаётся stdlib (развёртываемость на голых нодах без pip);
##            потребители получают единую точку urlopen + proxy + JSON-хелперы.
##            Дефолт-таймаут = DOCKER_CMD_TIMEOUT: 3 из 5 потребителей уже канонизировали
##            10s на короткие HTTP-подвызовы через эту константу (W1-A1, план 170);
##            отдельного HTTP_TIMEOUT в timeouts.py нет (правка timeouts.py — вне скоупа).
## ⚠️ TRAP[DECISION] · 2026-08-16 · — · Дефолт HTTP-таймаута = DOCKER_CMD_TIMEOUT (10s)
## · Rejected: новый HTTP_TIMEOUT в timeouts.py (семантически чище)
## · Reason: правка timeouts.py вне скоупа задачи; потребители уже канонизировали 10s
## ·   на короткие HTTP-подвызовы через DOCKER_CMD_TIMEOUT (W1-A1 план 170)
## · Rev: появление потребителя, требующего HTTP-дефолт ≠ 10s → ввести HTTP_TIMEOUT в timeouts.py
## @changes 2026-08-16 | DevPlan 177 W3.2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import http.client
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import cast

from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT

logger = logging.getLogger(__name__)

# Канонический дефолт коротких HTTP-подвызовов (10s, DOCKER_CMD_TIMEOUT из timeouts.py —
# W1-A1 план 170: consumers service_reload/langfuse/runner_cli уже канонизировали 10s).
DEFAULT_HTTP_TIMEOUT = DOCKER_CMD_TIMEOUT


# region DATA_HttpRequestError
class HttpRequestError(Exception):
    """Транспортная ошибка HTTP-запроса (URLError/HTTPError/OSError/TimeoutError) — сеть недоступна."""


# endregion DATA_HttpRequestError


# region DATA_HttpJsonError
class HttpJsonError(Exception):
    """Ответ не является валидным JSON (причина-json.JSONDecodeError сохраняется в __cause__)."""


# endregion DATA_HttpJsonError


# region FUNC_build_opener
def build_opener(proxy_url: str | None = None) -> urllib.request.OpenerDirector:
    """OpenerDirector с proxy-семантикой: явный proxy | env-прокси urllib.

    ▶ ┌proxy_url?┐ → ◇ задан → ProxyHandler({http, https}) | → build_opener() (env getproxies) → ⎋ OpenerDirector

    ## @purpose  Единая точка proxy-конфигурации (семантика telegram_notifier): явный
    ##            proxy_url для проксируемых каналов, иначе — стандартные env-прокси
    ##            urllib (HTTP_PROXY/HTTPS_PROXY/NO_PROXY).
    ## @io — ⇥ proxy_url: str | None → ⎋ urllib.request.OpenerDirector
    ## @complexity — O(1)
    ## @invariants
    ##   - proxy_url=None → build_opener() — env-прокси через getproxies (инвариант 4)
    ##   - proxy_url задан → ProxyHandler {"http": url, "https": url}
    """
    if proxy_url:
        logger.info("[IMP:7][http_client][build_opener] explicit proxy: %s", proxy_url)
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    # env-прокси urllib (HTTP_PROXY/HTTPS_PROXY/NO_PROXY) — семантика потребителей по умолчанию
    return urllib.request.build_opener()


# endregion FUNC_build_opener


# region FUNC_request
def request(
    url: str,
    *,
    method: str = "GET",
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    headers: Mapping[str, str] | None = None,
    data: bytes | None = None,
    proxy_url: str | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> http.client.HTTPResponse:
    """Низкоуровневый HTTP-запрос: возвращает HTTPResponse (статус/тело — у вызывающего).

    ▶ ┌url, method, timeout, headers, data┐ → ○ opener (инъекция|build_opener) → ○ Request → ○ open → ⎋ HTTPResponse

    ## @purpose  Единая точка urllib-запроса (прямое замещение urlopen): статус-код
    ##            читает потребитель (resp.status/getcode), ошибки ПРОБРАСЫВАЮТСЯ как есть
    ##            (инвариант 2 — 409-идемпотентность и не-фатальные ветки живут в потребителях).
    ## @io — ⇥ url: str, method: str, timeout: int (default из timeouts.py),
    ##         headers: Mapping[str, str] | None, data: bytes | None,
    ##         proxy_url: str | None, opener: OpenerDirector | None (инъекция тестов)
    ##       → ⎋ http.client.HTTPResponse — НЕ закрыт, закрывает вызывающий (with resp:)
    ## @complexity — O(1) — один запрос
    ## @raises — urllib.error.HTTPError (4xx/5xx), urllib.error.URLError, OSError, TimeoutError
    ## @invariants
    ##   - Никаких преобразований ошибок — низкий уровень (инвариант 2)
    ##   - opener задан → используется как есть (0 сети в тестах); иначе build_opener(proxy_url)
    """
    opener_to_use = opener if opener is not None else build_opener(proxy_url)
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
    logger.info("[IMP:7][http_client][request] %s %s (timeout=%ds)", method, url, timeout)
    # opener.open → Any (typeshed urllib); HTTPResponse-граница (W11) — .status типизирован
    return cast(
        "http.client.HTTPResponse",
        opener_to_use.open(req, timeout=timeout),  # nosec B310 — центральная точка; endpoint'ы внутренние (caller-обоснованные)
    )


# endregion FUNC_request


# region FUNC_get_json
def get_json(
    url: str,
    *,
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    headers: Mapping[str, str] | None = None,
    proxy_url: str | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> object:
    """GET + JSON-parse (единый JSON-хелпер, JSON-граница W11).

    ▶ ┌url┐ → ○ request(GET) → ○ resp.read().decode() → ○ json.loads → ◇ сеть|статус → HttpRequestError → ◇ JSON → HttpJsonError → ⎋ object

    ## @purpose  Декодирование + парс JSON в одном месте; ошибки типизированы
    ##            (инвариант 3) — потребитель оборачивает в доменные исключения.
    ## @io — ⇥ url, timeout, headers, proxy_url, opener → ⎋ object (распарсенный JSON)
    ## @complexity — O(B) — B = размер ответа
    ## @raises — HttpRequestError (сеть/HTTP-статус 4xx-5xx через HTTPError ⊂ URLError),
    ##           HttpJsonError (битый JSON)
    ## @invariants
    ##   - Не проверяет HTTP-статус 200 (Prometheus API возвращает ошибки JSON-ом) —
    ##     семантика prometheus_pull._http_get_json сохраняется
    ##   - HTTPError ⊂ URLError → попадает в HttpRequestError
    """
    try:
        with request(url, timeout=timeout, headers=headers, proxy_url=proxy_url, opener=opener) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        msg = str(exc)
        raise HttpRequestError(msg) from exc
    try:
        # W11: json.loads → Any → object-граница JSON (потребитель сужает cast-ом);
        # cast(object, ...) закрывает pyright reportAny (strict) на границе.
        parsed = cast(object, json.loads(body))
    except json.JSONDecodeError as exc:
        msg = str(exc)
        raise HttpJsonError(msg) from exc
    logger.info("[IMP:9][http_client][get_json] JSON-ответ получен: %s", url)
    return parsed


# endregion FUNC_get_json


# region FUNC_post_json
def post_json(
    url: str,
    payload: object,
    *,
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    headers: Mapping[str, str] | None = None,
    proxy_url: str | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> http.client.HTTPResponse:
    """POST с JSON-телом (Content-Type: application/json) → HTTPResponse.

    ▶ ┌url, payload┐ → ○ json.dumps → encode → ○ request(POST) → ⎋ HTTPResponse

    ## @purpose  POST-JSON-хелпер: сериализация тела + content-type в одном месте.
    ##            Статус/детали HTTPError читает вызывающий (409-идемпотентность langfuse,
    ##            mock-probe detail runner_cli) — ошибки пробрасываются (инвариант 2).
    ## @io — ⇥ url, payload: object (json-сериализуемый), timeout, headers, proxy_url, opener
    ##       → ⎋ http.client.HTTPResponse (не закрыт)
    ## @complexity — O(1) — один запрос
    ## @raises — urllib.error.HTTPError/URLError/OSError/TimeoutError (как request)
    ## @invariants
    ##   - Content-Type: application/json — setdefault (потребительский header не перетирается)
    ##   - Только POST (json-тело) — GET с JSON-телом вне контракта
    """
    merged_headers = dict(headers or {})
    merged_headers.setdefault("Content-Type", "application/json")
    data = json.dumps(payload).encode("utf-8")
    logger.info("[IMP:7][http_client][post_json] POST %s (JSON body)", url)
    return request(
        url,
        method="POST",
        timeout=timeout,
        headers=merged_headers,
        data=data,
        proxy_url=proxy_url,
        opener=opener,
    )


# endregion FUNC_post_json
