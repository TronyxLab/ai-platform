#!/usr/bin/env python3
# GREP_SUMMARY: test-http-client shared http-client timeout json proxy opener-injection urllib thin-client
# STRUCTURE: ┌fixtures (fake resp/recording opener)┐ → ◇ timeout default (canon timeouts) → ◇ get_json
#           (success|network|HTTP|JSON-error) → ◇ post_json (request build/setdefault) → ◇ proxy (explicit|env) → ⎋ 9 tests
# region MODULE_CONTRACT
## @purpose  Unit-тесты shared/http_client.py (DevPlan 177 W3.2 §TEST_SPEC) — тонкий urllib-клиент:
##           timeout-дефолт из timeouts.py, JSON-хелперы (get_json/post_json), proxy-передача.
## @scope    БЕЗ реальных сетевых вызовов — инъекция fake opener (параметр opener) +
##           monkeypatch urllib.request.ProxyHandler/getproxies (W4b DI-стиль, 0 monkeypatch urlopen).
## @invariants
##   - DEFAULT_HTTP_TIMEOUT == DOCKER_CMD_TIMEOUT (из timeouts.py, 0 литералов в http_client)
##   - get_json: URLError/HTTPError → HttpRequestError; битый JSON → HttpJsonError
##   - post_json: метод POST, Content-Type setdefault (потребительский не перетирается),
##     тело = json.dumps(payload).encode()
##   - proxy_url → ProxyHandler({http, https}); proxy_url=None → env-прокси urllib (getproxies)
## @rationale DevPlan 177 W3.2 §TEST_SPEC — тест-контракт нового shared-модуля (правило 3 shared/AGENTS.md).
## @changes  2026-08-16 | DevPlan 177 W3.2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import urllib.error
import urllib.request

import pytest

from core.internal.shared import http_client
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region HELPER_FakeHttp
class _FakeResp:
    """HTTPResponse-заменитель (status + read, context manager) — без сети.

    ## @purpose — фикстур ответа: http_client.request возвращает объект как есть
    ##            (потребитель читает .status/.getcode(); get_json — .read()).
    ## @io — ⇥ status: int, body: bytes → ⎋ _FakeResp
    """

    def __init__(self, status: int = 200, body: bytes = b""):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _RecordingOpener:
    """OpenerDirector-заменитель: записывает (Request, timeout), возвращает/бросает фикстур.

    ## @purpose — инъекция HTTP-слоя (инвариант 5 http_client): 0 сети в тестах.
    ## @io — ⇥ result: _FakeResp | Exception → ⎋ (записи в self.calls для ассертов)
    ## @complexity — O(1)
    """

    def __init__(self, result: _FakeResp | Exception):
        self.result = result
        self.calls: list[tuple[urllib.request.Request, int | None]] = []

    def open(self, req: urllib.request.Request, timeout: int | None = None) -> object:
        self.calls.append((req, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _req_header(req: urllib.request.Request, name: str) -> str | None:
    """Case-insensitive чтение заголовка Request (py3.10 dict vs py3.14 capitalize-keys).

    ## @purpose — py3.14 хранит ключи capitalized ("Content-type"), get_header — exact-match;
    ##            stable-assert между версиями Python (target py310+).
    ## @io — ⇥ req, name → ⎋ значение заголовка | None
    """
    return next((v for k, v in req.headers.items() if k.lower() == name.lower()), None)


# endregion HELPER_FakeHttp


# region TEST_timeout_default
# 🧪 TRAP[TEST] · Scenario: timeout-дефолт из timeouts.py (канон, DevPlan 177 W3.2)
# · Regression: DEFAULT_HTTP_TIMEOUT == DOCKER_CMD_TIMEOUT (10s) — 0 хардкод-литералов в http_client
# · Last fail: N/A (new)
# · Remove if: дефолт HTTP-таймаута переехал в отдельную константу timeouts.py
class TestTimeoutDefault:
    def test_default_timeout_from_timeouts(self) -> None:
        """DEFAULT_HTTP_TIMEOUT берётся из timeouts.DOCKER_CMD_TIMEOUT (не хардкод)."""
        assert http_client.DEFAULT_HTTP_TIMEOUT == DOCKER_CMD_TIMEOUT
        assert DOCKER_CMD_TIMEOUT == 10
        logger.critical(
            "[IMP:9][test][http_client] default timeout = %ds (canon timeouts.DOCKER_CMD_TIMEOUT)",
            http_client.DEFAULT_HTTP_TIMEOUT,
        )

    def test_request_uses_default_timeout(self) -> None:
        """request() без timeout → канон-дефолт пробрасывается в opener.open."""
        opener = _RecordingOpener(_FakeResp(200))
        with http_client.request("http://internal:9090/-/reload", method="POST", opener=opener) as resp:
            assert resp.status == 200

        req, timeout = opener.calls[0]
        assert timeout == http_client.DEFAULT_HTTP_TIMEOUT
        assert req.get_method() == "POST"


# endregion TEST_timeout_default


# region TEST_get_json
# 🧪 TRAP[TEST] · Scenario: get_json — успешный JSON-parse + LDD IMP:9
# · Regression: метод GET, timeout пробрасывается, IMP:9-лог присутствует (Anti-Illusion)
# · Last fail: N/A (new)
# · Remove if: get_json контракт изменён
class TestGetJson:
    def test_get_json_success(self, caplog) -> None:
        """GET + JSON-parse → распарсенный dict; LDD IMP:9 подтверждает траекторию."""
        caplog.set_level(logging.INFO)
        opener = _RecordingOpener(_FakeResp(200, b'{"status": "success", "data": [1, 2]}'))
        result = http_client.get_json("http://prom:9090/api/v1/label/__name__/values", timeout=7, opener=opener)

        assert result == {"status": "success", "data": [1, 2]}
        req, timeout = opener.calls[0]
        assert req.get_method() == "GET"
        assert timeout == 7

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in list(caplog.records):
            if "[IMP:" in record.message:
                logger.info("%s", record.message)
                if "[IMP:9]" in record.message:
                    found = True
        print("--- END LDD TRAJECTORY ---")
        assert found, "IMP:9 log missing (successful get_json)"

    def test_get_json_network_error(self) -> None:
        """URLError (сеть недоступна) → HttpRequestError (потребитель оборачивает)."""
        opener = _RecordingOpener(urllib.error.URLError("connection refused"))
        with pytest.raises(http_client.HttpRequestError):
            http_client.get_json("http://x/", opener=opener)

    def test_get_json_http_error(self) -> None:
        """HTTPError (4xx/5xx; ⊂ URLError) → HttpRequestError."""
        opener = _RecordingOpener(urllib.error.HTTPError("url", 500, "Internal", {}, None))
        with pytest.raises(http_client.HttpRequestError):
            http_client.get_json("http://x/", opener=opener)

    def test_get_json_bad_json(self) -> None:
        """Не-JSON ответ → HttpJsonError (JSON-граница W11)."""
        opener = _RecordingOpener(_FakeResp(200, b"<html>not json</html>"))
        with pytest.raises(http_client.HttpJsonError):
            http_client.get_json("http://x/", opener=opener)


# endregion TEST_get_json


# region TEST_post_json
# 🧪 TRAP[TEST] · Scenario: post_json — сборка POST-запроса (метод/Content-Type/тело/timeout)
# · Regression: тело = json.dumps(payload), Content-Type setdefault — потребительский не перетирается
# · Last fail: N/A (new)
# · Remove if: post_json контракт изменён
class TestPostJson:
    def test_post_json_builds_request(self) -> None:
        """POST + JSON-тело + Content-Type application/json; caller читает статус."""
        opener = _RecordingOpener(_FakeResp(201, b"{}"))
        with http_client.post_json(
            "http://langfuse:4000/api/public/projects",
            {"name": "myapp", "retention": 30},
            timeout=DOCKER_CMD_TIMEOUT,
            headers={"Authorization": "Bearer sk-test"},
            opener=opener,
        ) as resp:
            assert resp.status == 201

        req, timeout = opener.calls[0]
        assert req.get_method() == "POST"
        assert timeout == DOCKER_CMD_TIMEOUT
        assert _req_header(req, "Content-Type") == "application/json"
        assert _req_header(req, "Authorization") == "Bearer sk-test"
        assert req.data == b'{"name": "myapp", "retention": 30}'

    def test_post_json_respects_consumer_content_type(self) -> None:
        """Потребительский Content-Type не перетирается (setdefault-семантика)."""
        opener = _RecordingOpener(_FakeResp(200))
        http_client.post_json(
            "http://x/",
            {},
            headers={"Content-Type": "application/vnd.custom+json"},
            opener=opener,
        )
        req, _ = opener.calls[0]
        assert _req_header(req, "Content-Type") == "application/vnd.custom+json"


# endregion TEST_post_json


# region TEST_proxy
# 🧪 TRAP[TEST] · Scenario: proxy-передача — явный proxy_url → ProxyHandler({http, https})
# · Regression: семантика telegram_notifier (канон); 0 сетевых вызовов
# · Last fail: N/A (new)
# · Remove if: proxy-контракт http_client изменён
class TestProxy:
    def test_build_opener_explicit_proxy(self, monkeypatch) -> None:
        """proxy_url задан → ProxyHandler {"http": url, "https": url}."""
        captured: dict[str, object] = {}

        class _FakeProxyHandler(urllib.request.BaseHandler):
            def __init__(self, proxies: object = None):
                captured["proxies"] = proxies

        monkeypatch.setattr("urllib.request.ProxyHandler", _FakeProxyHandler)
        http_client.build_opener("http://proxy:8080")
        assert captured["proxies"] == {"http": "http://proxy:8080", "https": "http://proxy:8080"}

    def test_build_opener_default_env_proxy(self, monkeypatch) -> None:
        """proxy_url=None → env-прокси urllib (getproxies) — не пустой ProxyHandler."""
        monkeypatch.setattr(
            "urllib.request.getproxies",
            lambda: {"http": "http://env-proxy:3128", "https": "http://env-proxy:3128"},
        )
        opener = http_client.build_opener(None)
        proxy_handler = next(h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler))
        assert proxy_handler.proxies == {"http": "http://env-proxy:3128", "https": "http://env-proxy:3128"}
        logger.critical(
            "[IMP:9][test][http_client] env-proxy passed through: %s",
            sorted(proxy_handler.proxies),
        )


# endregion TEST_proxy
