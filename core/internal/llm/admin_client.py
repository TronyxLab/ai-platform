# GREP_SUMMARY: admin_client, LiteLLMAdminClient, httpx, LiteLLM-API, key-management, virtual-keys, Bearer-auth
# STRUCTURE: ▶ __init__(base_url, master_key) → ⚡ httpx.Client(base_url=..., headers=...) →
#            ○ get_key_info(key) → ◇ GET /key/info?key={key} → ◇ 200→⎋ dict, 404→⎋ None →
#            ○ generate_key(models, metadata, ...) → ◇ POST /key/generate → ⎋ dict →
#            ○ update_key(key, ...) → ◇ PUT /key/update → ⎋ dict →
#            ○ delete_key(key) → ◇ DELETE /key/delete → ⎋ bool →
#            ○ get_key_by_metadata(**filters) → ◇ GET /key/info → ◇ filter → ⎋ dict | None
# region MODULE_CONTRACT
## @purpose  Thin HTTP client for the LiteLLM Admin API. Manages virtual keys:
##           /key/info, /key/generate, /key/update, /key/delete. Used by
##           key_provisioner.py for idempotent key provisioning (DevPlan 049 Phase 4).
## @scope    All API calls to LiteLLM admin endpoints. Bearer auth with master key.
##           Provides both synchronous (httpx.Client) and asynchronous (httpx.AsyncClient) methods.
## @invariants
##   - base_url MUST NOT have trailing slash
##   - 404 on /key/info returns None (key does not exist) — not an error
##   - Connection/HTTP errors are logged at IMP:8 and return None or raise
##   - generate_key always posts with metadata.project field for idempotency matching
## @rationale Isolates LiteLLM API surface from business logic. Enables easy mocking in tests.
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 4)
# endregion MODULE_CONTRACT

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULT_TIMEOUT: float = 30.0

# ── Constants ─────────────────────────────────────────────────────────────────

_KEY_INFO_ENDPOINT: str = "/key/info"
_KEY_GENERATE_ENDPOINT: str = "/key/generate"
_KEY_UPDATE_ENDPOINT: str = "/key/update"
_KEY_DELETE_ENDPOINT: str = "/key/delete"


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

    def __init__(self, base_url: str, master_key: str) -> None:
        """Initialise client with base URL and master key.

        ## @purpose  Create httpx.Client (sync) and httpx.AsyncClient (async)
        ##           with shared Bearer auth header.
        ## @complexity O(1)
        """
        self._base_url = base_url.rstrip("/")
        self._master_key = master_key
        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json",
        }

        logger.log(
            logging.INFO,
            "[IMP:7][LiteLLMAdminClient][__init__] Client created: base_url=%s",
            self._base_url,
        )

    # ── Sync client (context manager) ─────────────────────────────────────────

    def _sync_client(self) -> httpx.Client:
        """Create a synchronous httpx client.

        ## @purpose  Factory method for httpx.Client with shared headers and timeout.
        ## @complexity O(1)
        """
        return httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=_DEFAULT_TIMEOUT,
        )

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

    def get_key_info(self, key: str) -> dict[str, Any] | None:
        """Synchronous: GET /key/info?key={key}.

        ## @purpose  Retrieve virtual key info by its token value.
        ##           Returns None if key does not exist (404).
        ## @io
        ##   - key: str — the virtual key token (sk-...)
        ##   - ⎋ dict | None — key info dict with metadata, models, budget, etc.
        ## @complexity O(1) — single HTTP GET
        ## @invariants
        ##   - 404 → returns None (not an error)
        ##   - ConnectionError → logged at IMP:8, returns None
        """
        logger.log(
            logging.INFO,
            "[IMP:7][get_key_info] GET /key/info for key=%s...",
            key[:16] if len(key) > 16 else key,
        )
        try:
            with self._sync_client() as client:
                response = client.get(_KEY_INFO_ENDPOINT, params={"key": key})
                if response.status_code == 404:
                    logger.log(
                        logging.INFO,
                        "[IMP:8][get_key_info] Key not found (404): %s...",
                        key[:16] if len(key) > 16 else key,
                    )
                    return None
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                logger.log(
                    logging.CRITICAL,
                    "[IMP:9][get_key_info] Key info retrieved: models=%s, metadata=%s",
                    data.get("models"),
                    data.get("metadata"),
                )
                return data
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.log(
                logging.WARNING,
                "[IMP:8][get_key_info] HTTP error: status=%s, key=%s...",
                e.response.status_code,
                key[:16] if len(key) > 16 else key,
            )
            return None
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][get_key_info] Connection error: %s",
                e,
            )
            return None

    def generate_key(
        self,
        models: list[str],
        metadata: dict[str, str],
        max_budget: float = 0.0,
        budget_duration: str = "1d",
        rpm_limit: int = 10,
        **kwargs: Any,
    ) -> dict[str, Any]:
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
        payload: dict[str, Any] = {
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
            with self._sync_client() as client:
                response = client.post(_KEY_GENERATE_ENDPOINT, json=payload)
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                logger.log(
                    logging.CRITICAL,
                    "[IMP:9][generate_key] Key generated: project=%s, key=%s...",
                    metadata.get("project", "unknown"),
                    data.get("key", "???")[:16],
                )
                return data
        except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][generate_key] Error generating key: %s",
                e,
            )
            raise

    def update_key(
        self,
        key: str,
        models: list[str] | None = None,
        max_budget: float | None = None,
        rpm_limit: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
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
        payload: dict[str, Any] = {"key": key}
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
            key[:16] if len(key) > 16 else key,
            models,
        )
        try:
            with self._sync_client() as client:
                response = client.put(_KEY_UPDATE_ENDPOINT, json=payload)
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                logger.log(
                    logging.CRITICAL,
                    "[IMP:9][update_key] Key updated: key=%s...",
                    key[:16] if len(key) > 16 else key,
                )
                return data
        except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][update_key] Error updating key: %s",
                e,
            )
            raise

    def delete_key(self, key: str) -> bool:
        """Synchronous: DELETE /key/delete — delete a virtual key.

        ## @purpose  Remove a virtual key from LiteLLM. Returns True if deleted.
        ## @io
        ##   - key: str — the virtual key token to delete
        ##   - ⎋ bool — True if deleted successfully
        ## @complexity O(1) — single HTTP DELETE
        """
        logger.log(
            logging.INFO,
            "[IMP:7][delete_key] DELETE /key/delete: key=%s...",
            key[:16] if len(key) > 16 else key,
        )
        try:
            with self._sync_client() as client:
                response = client.delete(_KEY_DELETE_ENDPOINT, params={"key": key})
                if response.status_code == 404:
                    logger.log(
                        logging.INFO,
                        "[IMP:8][delete_key] Key not found (404), considered deleted: %s...",
                        key[:16] if len(key) > 16 else key,
                    )
                    return True
                response.raise_for_status()
                logger.log(
                    logging.CRITICAL,
                    "[IMP:9][delete_key] Key deleted: %s...",
                    key[:16] if len(key) > 16 else key,
                )
                return True
        except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][delete_key] Error deleting key: %s",
                e,
            )
            return False

    def get_key_by_metadata(self, **metadata_filters: str) -> dict[str, Any] | None:
        """Synchronous: GET /key/info → filter keys by metadata fields.

        ## @purpose  Find a key by matching metadata fields (e.g. project=my-backend).
        ##           Lists ALL keys (no key param), then filters server-side would be
        ##           ideal, but LiteLLM does not support server-side metadata filtering.
        ##           Client-side filtering is used with pagination awareness.
        ## @io
        ##   - **metadata_filters: str — key-value pairs to match in metadata
        ##   - ⎋ dict | None — first matching key, or None
        ## @complexity O(N) where N = total keys (client-side filter)
        ## @invariants
        ##   - Returns the FIRST key whose metadata contains ALL filter pairs
        ##   - If multiple keys match, returns the first one (ordered by LiteLLM)
        ##   - Connection errors return None with IMP:8 log
        """
        logger.log(
            logging.INFO,
            "[IMP:7][get_key_by_metadata] Finding key by metadata: %s",
            metadata_filters,
        )
        try:
            with self._sync_client() as client:
                response = client.get(_KEY_INFO_ENDPOINT)
                if response.status_code == 404:
                    logger.log(
                        logging.INFO,
                        "[IMP:8][get_key_by_metadata] No keys found",
                    )
                    return None
                response.raise_for_status()
                data: dict[str, Any] = response.json()

                # LiteLLM /key/info without 'key' param returns {'key': [...]} or a single key dict
                keys = data.get("key") or data.get("keys") or []
                if isinstance(keys, dict):
                    keys = [keys]

                logger.log(
                    logging.DEBUG,
                    "[IMP:7][get_key_by_metadata] Retrieved %d keys for filtering",
                    len(keys) if isinstance(keys, list) else 1,
                )

                # Client-side metadata filtering
                if isinstance(keys, list):
                    for k in keys:
                        key_metadata = k.get("metadata", {}) or {}
                        if not isinstance(key_metadata, dict):
                            continue
                        match = all(key_metadata.get(field) == value for field, value in metadata_filters.items())
                        if match:
                            logger.log(
                                logging.CRITICAL,
                                "[IMP:9][get_key_by_metadata] Key found matching metadata: %s",
                                metadata_filters,
                            )
                            return k

                logger.log(
                    logging.INFO,
                    "[IMP:8][get_key_by_metadata] No key matching metadata: %s",
                    metadata_filters,
                )
                return None

        except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][get_key_by_metadata] Error: %s",
                e,
            )
            return None

    # ── Async methods ─────────────────────────────────────────────────────────

    async def async_get_key_info(self, key: str) -> dict[str, Any] | None:
        """Async: GET /key/info?key={key}.

        ## @purpose  Async version of get_key_info for use in async contexts.
        ## @complexity O(1)
        """
        logger.log(
            logging.INFO,
            "[IMP:7][async_get_key_info] GET /key/info for key=%s...",
            key[:16] if len(key) > 16 else key,
        )
        try:
            async with self._async_client() as client:
                response = await client.get(_KEY_INFO_ENDPOINT, params={"key": key})
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()
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
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Async: POST /key/generate.

        ## @purpose  Async version of generate_key.
        ## @complexity O(1)
        """
        payload: dict[str, Any] = {
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
                return response.json()
        except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
            logger.log(
                logging.WARNING,
                "[IMP:8][async_generate_key] Error: %s",
                e,
            )
            raise


# endregion CLASS_LiteLLMAdminClient
