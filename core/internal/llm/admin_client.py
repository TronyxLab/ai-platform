# GREP_SUMMARY: admin_client, LiteLLMAdminClient, httpx, LiteLLM-API, key-management, virtual-keys, Bearer-auth, pagination, transport-error
# ⚠️ TRAP[DECISION] · — · admin_client остаётся на httpx (177 W4 S13): LiteLLM Admin API
#   — внешний контракт (Bearer/auth, AsyncClient-методы, статус-семантика 404→None),
#   shared/http_client.py — urllib-клиент внутренних доменных потребителей;
#   консолидация потеряет AsyncClient-поверхность и детали статус-кодов.
#   · Rev: если появится 3-й httpx-потребитель ИЛИ http_client получит AsyncClient —
#   пересмотреть консолидацию.
# STRUCTURE: ▶ __init__(base_url, master_key) → ⚡ long-lived httpx.Client(base_url=..., headers=...) [lazy] →
#            ○ list_keys() → ◇ GET /key/info?page=N [◇ total_pages>page? → ○ next page] → ⊕ keys → ⎋ list →
#            ○ get_key_info(key) → ◇ GET /key/info?key={key} → ◇ 200→⎋ dict, 404→⎋ None, err→⚡ TransportError →
#            ○ generate_key(models, metadata, ...) → ◇ POST /key/generate → ⎋ dict │ ⚡ TransportError →
#            ○ update_key(key, ...) → ◇ PUT /key/update → ⎋ dict │ ⚡ TransportError →
#            ○ delete_key(key) → ◇ DELETE /key/delete → ⎋ bool →
#            ○ get_key_by_metadata(**filters) → ◇ list_keys() → ◇ filter → ⎋ dict | None │ ⚡ TransportError
# region MODULE_CONTRACT
## @purpose  Thin HTTP client for the LiteLLM Admin API. Manages virtual keys:
##           /key/info (paginated listing), /key/generate, /key/update, /key/delete. Used by
##           key_provisioner.py for idempotent key provisioning (DevPlan 049 Phase 4).
## @scope    All API calls to LiteLLM admin endpoints. Bearer auth with master key.
##           Provides both synchronous (httpx.Client) and asynchronous (httpx.AsyncClient) methods.
## @invariants
##   - base_url MUST NOT have trailing slash
##   - 404 on lookup = "ключ не существует" → None/[] — НЕ ошибка
##   - ЛЮБОЙ другой сбой (connect/timeout/non-404 HTTP) → LiteLLMTransportError:
##     transient-сбой НЕДОСТИЖИМ от «no key» — вызывающий обязан skip-WARN, НЕ generate
##     (REF-0104: иначе lookup-failure порождает дубликаты budget-bearing ключей)
##   - Sync httpx.Client — long-lived (lazy, один на жизнь клиента; connection reuse,
##     PERF-083), закрывается close()/context-manager
##   - list_keys() следует пагинации LiteLLM (page/total_pages) до исчерпания —
##     только страница 1 = невидимые ключи = дубликаты (PERF-082, подтверждено)
##   - generate_key always posts with metadata.project field for idempotency matching
## @rationale Isolates LiteLLM API surface from business logic. Enables easy mocking in tests
##            (transport DI в __init__). 404-vs-error семантика — контракт честного provisioner'а.
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 4)
##           2026-08-25 | REF-0104 — LiteLLMTransportError (404 ≠ transport-failure);
##                      пагинация list_keys (PERF-082); long-lived Client (PERF-083); transport-DI
# endregion MODULE_CONTRACT

import logging
from typing import TypedDict, cast

import httpx

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULT_TIMEOUT: float = 30.0

# ── Constants ─────────────────────────────────────────────────────────────────

_KEY_INFO_ENDPOINT: str = "/key/info"
_KEY_GENERATE_ENDPOINT: str = "/key/generate"
_KEY_UPDATE_ENDPOINT: str = "/key/update"
_KEY_DELETE_ENDPOINT: str = "/key/delete"
_KEY_PREVIEW_LEN: int = 16  # сколько символов ключа показывать в логах (обрезка)
HTTP_NOT_FOUND: int = 404  # статус "ключ не существует" — не ошибка (возврат None)
_PAGE_SIZE: int = 100  # запрошенный размер страницы листинга (LiteLLM page/size)
_MAX_PAGES: int = 1000  # anti-runaway guard пагинации (defensive ceiling)


# region DATA_LiteLLMTransportError
class LiteLLMTransportError(Exception):
    """Transient transport/HTTP failure of a LiteLLM Admin API call.

    ## @purpose  Отличить transient-сбой (connect/timeout/5xx) от семантического
    ##            «ключа нет» (404 → None). REF-0104: возврат None при transport-сбое
    ##            заставлял provisioner генерировать дубликаты budget-bearing ключей.
    ## @io — ⇥ message (+ __cause__ — исходный httpx-исключение) → ⎋ instance
    ## @complexity O(1)
    ## @invariants
    ##   - Никогда не бросается для HTTP 404 по lookup-эндпоинтам (404 → None/[])
    """


# endregion DATA_LiteLLMTransportError


# region DATA_KeyInfo
class KeyInfo(TypedDict, total=False):
    """Объект виртуального ключа LiteLLM Admin API /key/info (граница JSON).

    ## @purpose  Типизированная граница ответов /key/info, /key/generate, /key/update —
    ##            потребители (key_provisioner) читают поля через .get() без Any.
    ## @invariants
    ##   - key — токен sk-... (в single-response /key/info); в list-response контейнер
    ##     {'key': [...]} обрабатывается в get_key_by_metadata отдельно
    ##   - metadata — произвольные строковые теги (project, tier, env)
    """

    key: str
    models: list[str]
    max_budget: float
    rpm_limit: int
    metadata: dict[str, object]


# endregion DATA_KeyInfo


# region CLASS_LiteLLMAdminClient


class LiteLLMAdminClient:
    """Synchronous + asynchronous HTTP client for the LiteLLM Admin API.

    ## @purpose  Manage LiteLLM virtual keys: info, generate, update, delete.
    ##           Bearer auth header is set once at construction.
    ## @io
    ##   - base_url: str — LiteLLM admin URL (e.g. http://litellm:4000)
    ##   - master_key: str — LITELLM_MASTER_KEY for Bearer auth
    ## @complexity O(1) per method call
    ## @invariants
    ##   - base_url is stripped of trailing slash
    ##   - Auth header is 'Authorization: Bearer {master_key}'
    ##   - Timeout is 30s for all requests
    """

    def __init__(
        self,
        base_url: str,
        master_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise client with base URL and master key.

        ## @purpose  Prepare shared Bearer auth header; sync httpx.Client создаётся
        ##           LAZY и живёт до close() (connection reuse — PERF-083).
        ## @io
        ##   - base_url: str — LiteLLM admin URL (e.g. http://litellm:4000)
        ##   - master_key: str — LITELLM_MASTER_KEY for Bearer auth
        ##   - transport: httpx.BaseTransport | None — DI-шов для тестов (MockTransport)
        ##   - timeout: float — per-request timeout seconds
        ## @complexity O(1)
        ## @invariants
        ##   - base_url is stripped of trailing slash
        ##   - Auth header is 'Authorization: Bearer {master_key}'
        """
        self._base_url = base_url.rstrip("/")
        self._master_key = master_key
        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json",
        }
        self._transport = transport
        self._timeout = timeout
        self._client: httpx.Client | None = None  # long-lived, lazy

        logger.log(
            logging.INFO,
            "[IMP:7][LiteLLMAdminClient][__init__] Client created: base_url=%s",
            self._base_url,
        )

    # ── Sync client (long-lived) ──────────────────────────────────────────────

    def _sync_client(self) -> httpx.Client:
        """Return the long-lived synchronous httpx.Client (lazy singleton).

        ## @purpose  Один Client на жизнь инстанса: connection-pool reuse вместо
        ##           connect+teardown на каждый запрос (PERF-083). Пересоздаётся
        ##           только после close().
        ## @complexity O(1)
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self._base_url,
                headers=self._headers,
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying httpx.Client (idempotent).

        ## @purpose  Явное освобождение connection-pool; после close() следующий
        ##           запрос лениво пересоздаст клиент.
        ## @complexity O(1)
        """
        if self._client is not None and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> "LiteLLMAdminClient":
        """Context-manager entry — клиент доступен, закрытие по выходу."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Context-manager exit — close() (не глотает исключения)."""
        self.close()

    # ── Async client (context manager) ────────────────────────────────────────

    def _async_client(self) -> httpx.AsyncClient:
        """Create an asynchronous httpx client.

        ## @purpose  Factory method for httpx.AsyncClient with shared headers and timeout.
        ## @complexity O(1)
        """
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=_DEFAULT_TIMEOUT,
        )

    # ── Sync methods ──────────────────────────────────────────────────────────

    def get_key_info(self, key: str) -> KeyInfo | None:
        """Synchronous: GET /key/info?key={key}.

        ## @purpose  Retrieve virtual key info by its token value.
        ##           Returns None ONLY for 404 (ключ не существует); любой
        ##           transport/HTTP-сбой → LiteLLMTransportError (REF-0104:
        ##           transient-сбой ≠ «no key»).
        ## @io
        ##   - key: str — the virtual key token (sk-...)
        ##   - ⎋ dict | None — key info dict; None только при 404
        ##   - ⚡ LiteLLMTransportError — connect/timeout/non-404 HTTP
        ## @complexity O(1) — single HTTP GET
        ## @invariants
        ##   - 404 → returns None (not an error)
        ##   - Transport failure → raises (НЕ маскируется None)
        """
        logger.log(
            logging.INFO,
            "[IMP:7][get_key_info] GET /key/info for key=%s...",
            key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
        )
        try:
            client = self._sync_client()
            response = client.get(_KEY_INFO_ENDPOINT, params={"key": key})
            response.raise_for_status()
            data: KeyInfo = cast("KeyInfo", response.json())  # W11: json → Any → KeyInfo
        except httpx.HTTPStatusError as e:
            if e.response.status_code == HTTP_NOT_FOUND:
                logger.log(
                    logging.INFO,
                    "[IMP:8][get_key_info] Key not found (404): %s...",
                    key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
                )
                return None
            logger.log(
                logging.WARNING,
                "[IMP:8][get_key_info] HTTP error: status=%s, key=%s...",
                e.response.status_code,
                key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
            )
            msg = f"GET /key/info failed: HTTP {e.response.status_code}"
            raise LiteLLMTransportError(msg) from e
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][get_key_info] Connection error: %s",
                e,
            )
            msg = f"GET /key/info failed: {type(e).__name__}: {e}"
            raise LiteLLMTransportError(msg) from e
        else:
            logger.log(
                logging.CRITICAL,
                "[IMP:9][get_key_info] Key info retrieved: models=%s, metadata=%s",
                data.get("models"),
                data.get("metadata"),
            )
            return data

    def generate_key(
        self,
        models: list[str],
        metadata: dict[str, str],
        max_budget: float = 0.0,
        budget_duration: str = "1d",
        rpm_limit: int = 10,
        **kwargs: object,
    ) -> KeyInfo:
        """Synchronous: POST /key/generate — create a new virtual key.

        ## @purpose  Generate a new LiteLLM virtual key with defined models,
        ##           budget, metadata tags, and rate limits.
        ## @io
        ##   - models: list[str] — alias names this key can access
        ##   - metadata: dict[str, str] — tags (project, tier, env)
        ##   - max_budget: float — daily budget in USD
        ##   - budget_duration: str — duration string (e.g. "1d")
        ##   - rpm_limit: int — requests per minute limit
        ##   - ⎋ dict — response with 'key' field containing the generated token
        ## @complexity O(1) — single HTTP POST
        ## @invariants
        ##   - metadata.project is REQUIRED for idempotency matching
        ##   - Response always contains a 'key' field on success
        """
        payload: dict[str, object] = {
            "models": models,
            "metadata": metadata,
            "max_budget": max_budget,
            "budget_duration": budget_duration,
            "rpm_limit": rpm_limit,
            **kwargs,
        }
        logger.log(
            logging.INFO,
            "[IMP:8][generate_key] POST /key/generate: models=%s, metadata=%s, budget=%s",
            models,
            metadata,
            max_budget,
        )
        try:
            client = self._sync_client()
            response = client.post(_KEY_GENERATE_ENDPOINT, json=payload)
            response.raise_for_status()
            data: KeyInfo = cast("KeyInfo", response.json())  # W11: json → Any → KeyInfo
        except httpx.HTTPError as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][generate_key] Error generating key: %s",
                e,
            )
            # REF-0104: uniform error-семантика — вызывающий ловит LiteLLMTransportError,
            # не знает деталей httpx (иначе часть сбоев ускользала из except-кортежей → mid-loop abort)
            msg = f"POST /key/generate failed: {type(e).__name__}: {e}"
            raise LiteLLMTransportError(msg) from e
        else:
            logger.log(
                logging.CRITICAL,
                "[IMP:9][generate_key] Key generated: project=%s, key=%s...",
                metadata.get("project", "unknown"),
                data.get("key", "???")[:16],
            )
            return data

    def update_key(
        self,
        key: str,
        models: list[str] | None = None,
        max_budget: float | None = None,
        rpm_limit: int | None = None,
        **kwargs: object,
    ) -> KeyInfo:
        """Synchronous: PUT /key/update — update an existing virtual key.

        ## @purpose  Update access models, budget, or rate limits for an existing key.
        ## @io
        ##   - key: str — the virtual key token to update
        ##   - models: list[str] | None — new model alias list
        ##   - max_budget: float | None — new daily budget
        ##   - rpm_limit: int | None — new rate limit
        ##   - ⎋ dict — response JSON from LiteLLM
        ## @complexity O(1) — single HTTP PUT
        """
        payload: dict[str, object] = {"key": key}
        if models is not None:
            payload["models"] = models
        if max_budget is not None:
            payload["max_budget"] = max_budget
        if rpm_limit is not None:
            payload["rpm_limit"] = rpm_limit
        payload.update(kwargs)

        logger.log(
            logging.INFO,
            "[IMP:7][update_key] PUT /key/update: key=%s..., models=%s",
            key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
            models,
        )
        try:
            client = self._sync_client()
            response = client.put(_KEY_UPDATE_ENDPOINT, json=payload)
            response.raise_for_status()
            data: KeyInfo = cast("KeyInfo", response.json())  # W11: json → Any → KeyInfo
        except httpx.HTTPError as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][update_key] Error updating key: %s",
                e,
            )
            msg = f"PUT /key/update failed: {type(e).__name__}: {e}"
            raise LiteLLMTransportError(msg) from e
        else:
            logger.log(
                logging.CRITICAL,
                "[IMP:9][update_key] Key updated: key=%s...",
                key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
            )
            return data

    def delete_key(self, key: str) -> bool:
        """Synchronous: DELETE /key/delete — delete a virtual key.

        ## @purpose  Remove a virtual key from LiteLLM. Returns True if deleted.
        ## @io
        ##   - key: str — the virtual key token to delete
        ##   - ⎋ bool — True if deleted (или уже удалён), False при transport-сбое
        ## @complexity O(1) — single HTTP DELETE
        """
        logger.log(
            logging.INFO,
            "[IMP:7][delete_key] DELETE /key/delete: key=%s...",
            key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
        )
        try:
            client = self._sync_client()
            response = client.delete(_KEY_DELETE_ENDPOINT, params={"key": key})
            if response.status_code == HTTP_NOT_FOUND:
                logger.log(
                    logging.INFO,
                    "[IMP:8][delete_key] Key not found (404), considered deleted: %s...",
                    key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
                )
                return True
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][delete_key] Error deleting key: %s",
                e,
            )
            return False
        else:
            logger.log(
                logging.CRITICAL,
                "[IMP:9][delete_key] Key deleted: %s...",
                key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
            )
            return True

    # region FUNC_list_keys
    def list_keys(self) -> list[KeyInfo]:
        """Synchronous: GET /key/info — fetch ALL virtual keys following pagination.

        ## @purpose  Полный список ключей с обходом страниц LiteLLM (page/total_pages).
        ##           REF-0104 / PERF-082 (подтверждена): чтение только страницы 1 делало
        ##           ключи за её пределами невидимыми → provisioner генерировал дубликаты.
        ##           Legacy-серверы без пагинации отвечают одним payload'ом без
        ##           total_pages — ровно один запрос.
        ## @io — ⎋ list[KeyInfo] (пустой при 404 = ключей нет)
        ##      ⚡ LiteLLMTransportError — transport/non-404 HTTP на любой странице
        ## @complexity O(P * K) где P = страницы, K = ключей на странице
        ## @invariants
        ##   - 404 → [] («ключей ещё нет» — не ошибка)
        ##   - Цикл до исчерпания total_pages; anti-runaway ceiling _MAX_PAGES
        ##   - Ответ без total_pages → single-page (совместимость с legacy LiteLLM)
        """
        all_keys: list[KeyInfo] = []
        page = 1
        requests_issued = 0
        while True:
            requests_issued += 1
            # defensive: runaway-пагинация не может зависнуть provision
            if requests_issued > _MAX_PAGES:
                msg = f"list_keys exceeded {_MAX_PAGES} pages — aborting"
                raise LiteLLMTransportError(msg)
            logger.log(
                logging.INFO,
                "[IMP:7][list_keys] GET /key/info page=%d",
                page,
            )
            try:
                client = self._sync_client()
                response = client.get(_KEY_INFO_ENDPOINT, params={"page": page, "size": _PAGE_SIZE})
                response.raise_for_status()
                data: dict[str, object] = cast("dict[str, object]", response.json())
            except httpx.HTTPStatusError as e:
                if e.response.status_code == HTTP_NOT_FOUND:
                    logger.log(logging.INFO, "[IMP:8][list_keys] No keys exist (404)")
                    return []
                msg = f"GET /key/info page={page} failed: HTTP {e.response.status_code}"
                raise LiteLLMTransportError(msg) from e
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                msg = f"GET /key/info page={page} failed: {type(e).__name__}: {e}"
                raise LiteLLMTransportError(msg) from e
            else:
                all_keys.extend(_parse_keys_payload(data))
                total_pages: object = data.get("total_pages")
                if isinstance(total_pages, int) and page < total_pages:
                    page += 1
                    continue
                break

        logger.log(
            logging.CRITICAL,
            "[IMP:9][list_keys] Fetched %d key(s) across %d request(s)",
            len(all_keys),
            requests_issued,
        )
        return all_keys

    # endregion FUNC_list_keys

    def get_key_by_metadata(self, **metadata_filters: str) -> KeyInfo | None:
        """Synchronous: list all keys (paginated) → filter by metadata fields.

        ## @purpose  Find a key by matching metadata fields (e.g. project=my-backend).
        ##           REF-0104 семантика: None ТОЛЬКО когда списка запросов нет (404)
        ##           или фильтр не дал match; transport/HTTP-сбой → LiteLLMTransportError.
        ## @io
        ##   - **metadata_filters: str — key-value pairs to match in metadata
        ##   - ⎋ dict | None — first matching key, or None
        ##   - ⚡ LiteLLMTransportError — transient failure (НЕ «no key»)
        ## @complexity O(P * K) fetch + O(N * F) filter
        ## @invariants
        ##   - Returns the FIRST key whose metadata contains ALL filter pairs
        ##   - 404 / no-match → None; сбой транспорта — НЕ маскируется
        """
        logger.log(
            logging.INFO,
            "[IMP:7][get_key_by_metadata] Finding key by metadata: %s",
            metadata_filters,
        )
        return find_key_by_metadata(self.list_keys(), **metadata_filters)

    # ── Async methods ─────────────────────────────────────────────────────────

    async def async_get_key_info(self, key: str) -> KeyInfo | None:
        """Async: GET /key/info?key={key}.

        ## @purpose  Async version of get_key_info for use in async contexts.
        ## @complexity O(1)
        """
        logger.log(
            logging.INFO,
            "[IMP:7][async_get_key_info] GET /key/info for key=%s...",
            key[:_KEY_PREVIEW_LEN] if len(key) > _KEY_PREVIEW_LEN else key,
        )
        try:
            async with self._async_client() as client:
                response = await client.get(_KEY_INFO_ENDPOINT, params={"key": key})
                if response.status_code == HTTP_NOT_FOUND:
                    return None
                response.raise_for_status()
                return cast("KeyInfo", response.json())  # W11: json → Any → KeyInfo
        except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][async_get_key_info] Error: %s",
                e,
            )
            return None

    async def async_generate_key(
        self,
        models: list[str],
        metadata: dict[str, str],
        max_budget: float = 0.0,
        budget_duration: str = "1d",
        rpm_limit: int = 10,
        **kwargs: object,
    ) -> KeyInfo:
        """Async: POST /key/generate.

        ## @purpose  Async version of generate_key.
        ## @complexity O(1)
        """
        payload: dict[str, object] = {
            "models": models,
            "metadata": metadata,
            "max_budget": max_budget,
            "budget_duration": budget_duration,
            "rpm_limit": rpm_limit,
            **kwargs,
        }
        try:
            async with self._async_client() as client:
                response = await client.post(_KEY_GENERATE_ENDPOINT, json=payload)
                response.raise_for_status()
                return cast("KeyInfo", response.json())  # W11: json → Any → KeyInfo
        except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][async_generate_key] Error: %s",
                e,
            )
            raise


# endregion CLASS_LiteLLMAdminClient


# region FUNC_parse_keys_payload
def _parse_keys_payload(data: dict[str, object]) -> list[KeyInfo]:
    """Normalize a /key/info listing payload into a list of KeyInfo.

    ## @purpose  LiteLLM возвращает {'keys': [...]} (paginated) или {'key': [...]} /
    ##            одиночный key-dict (legacy) — единая нормализация для list_keys.
    ## @io — ⇥ data: parsed JSON object → ⎋ list[KeyInfo]
    ## @complexity O(K)
    ## @invariants
    ##   - Не-дикты внутри списка пропускаются (защита границы JSON)
    """
    raw_keys: object = data.get("keys") or data.get("key") or []
    if isinstance(raw_keys, dict):
        return [cast("KeyInfo", cast(object, raw_keys))]
    if isinstance(raw_keys, list):
        key_list = cast("list[object]", raw_keys)
        return [cast("KeyInfo", cast(object, k)) for k in key_list if isinstance(k, dict)]
    return []


# endregion FUNC_parse_keys_payload


# region FUNC_find_key_by_metadata
def find_key_by_metadata(keys: list[KeyInfo], **metadata_filters: str) -> KeyInfo | None:
    """Filter a key list by metadata fields (client-side, no HTTP).

    ## @purpose  Чистая фильтрация уже скачанного списка (PERF-081: fetch ONCE +
    ##           filter вместо N полных скачиваний). Используется provisioner'ом
    ##           поверх одного list_keys() вызова.
    ## @io — ⇥ keys, **metadata_filters → ⎋ KeyInfo | None (первый match или None)
    ## @complexity O(N * F)
    ## @invariants
    ##   - Match = ВСЕ фильтр-пары присутствуют в metadata ключа
    """
    result: KeyInfo | None = None
    for k in keys:
        key_metadata = k.get("metadata") or {}
        if not isinstance(key_metadata, dict):
            continue
        match = all(key_metadata.get(field) == value for field, value in metadata_filters.items())
        if match:
            result = k
            break

    if result is not None:
        logger.log(
            logging.CRITICAL,
            "[IMP:9][find_key_by_metadata] Key found matching metadata: %s",
            metadata_filters,
        )
    else:
        logger.log(
            logging.INFO,
            "[IMP:8][find_key_by_metadata] No key matching metadata: %s",
            metadata_filters,
        )
    return result


# endregion FUNC_find_key_by_metadata
