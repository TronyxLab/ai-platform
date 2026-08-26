#!/usr/bin/env python3
# GREP_SUMMARY: s3-client, boto3, s3, client-factory, endpoint, access-key, retries, shared, lazy-import
# STRUCTURE: ▶ get_s3_client ┌endpoint/access_key/secret_key/max_attempts/region┐ → ◇ env-fallback → ◇ lazy import boto3 → ⊕ boto3.client (BotoConfig retries) → ⎋ client
# region MODULE_CONTRACT
## @purpose  Shared boto3 S3 client factory — единый SoT создания S3-клиента платформенного домена
##           (DevPlan 117 D26). Заменяет дублирующиеся boto3.client-фабрики в s3_ssl_cache._get_s3_client
##           и preflight.probe_s3_connectivity (инлайн).
## @scope    Импортируется s3_ssl_cache.py и preflight.py (≥2 потребителя — критерий shared/).
##           НЕ покрывает backup-cron домен (upload.py/retention.py — отдельный домен с таймаутами,
##           вне скоупа D26). sha256 — stdlib hashlib, НЕ выносится (DevPlan 117 D26).
## @invariants
##   - Параметры None → env-fallback: S3_ENDPOINT_URL/S3_ACCESS_KEY/AWS_ACCESS_KEY_ID/
##     S3_SECRET_KEY/AWS_SECRET_ACCESS_KEY/S3_REGION → platform_config.default_s3_region()
##   - max_attempts: retries (s3_ssl_cache=3, preflight probe=1) — BotoConfig retries mode='standard'
##   - Proxy-stripping (HTTPS_PROXY/HTTP_PROXY/NO_PROXY) — ответственность вызывающего (s3_ssl_cache
##     делает это перед вызовом; preflight прокси не требуется)
##   - Чистая фабрика: не выполняет I/O, не кэширует клиент, никогда не raise (boto3.client может
##     raise только при неверных аргументах — caller обрабатывает)
##   - DevPlan 015 F-08: import boto3/botocore — ЛЕНИВЫЙ (внутри get_s3_client), top-level импорт
##     отсутствует — модуль грузится даже без boto3; аннотация -> boto3.client остаётся строкой
##     (from __future__ import annotations) и не вычисляется на импорте
## @rationale 4 boto3-фабрики (s3_ssl_cache, upload, retention, preflight) имели разные конфиги.
##            Унификация ТОЛЬКО s3_ssl_cache + preflight (один домен bootstrap): s3_ssl_cache
##            (max_attempts=3, proxy-stripping) и preflight (max_attempts=1, быстрый probe).
##            upload/retention (backup-cron) — отдельный домен, НЕ трогаются (DevPlan 117 D26).
##            F-08 (015): top-level `import boto3` ронял `from core.internal.bootstrap import
##            s3_ssl_cache` при отсутствии зависимости → весь S3-кеш молча выключен. Lazy-import
##            устраняет класс «модуль не загрузился → кеш выключен» (не только симптом).
## @changes  2026-08-01 | DevPlan 117 D26 — создан (дедупликация boto3-фабрик)
## @changes  2026-08-27 | DevPlan 015 F-08 — import boto3/botocore → внутрь get_s3_client (lazy);
##                      TYPE_CHECKING для аннотаций (модуль грузится без boto3)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import boto3

from core.internal.config import platform_config

logger = logging.getLogger(__name__)

DEFAULT_S3_ENDPOINT_URL: str = "https://s3.timeweb.cloud"
"""## @invariant Default S3 endpoint (канон s3_ssl_cache)."""


# region FUNC_get_s3_client
## @purpose  Create a boto3 S3 client with env-fallback resolution and retry config.
##           Ленивый импорт boto3/botocore (DevPlan 015 F-08): модуль грузится БЕЗ boto3,
##           клиент создаётся только при реальном S3-вызове; отсутствие boto3 → ImportError
##           (диагноз вызывающему — s3_ssl_cache деградирует non-fatal, preflight WARN).
## @io       ⇥ endpoint: str | None, access_key: str | None, secret_key: str | None,
##           max_attempts: int, region: str | None → ⎋ boto3 S3 client
## @complexity — O(1)
## @invariants
##   - endpoint None → S3_ENDPOINT_URL env → DEFAULT_S3_ENDPOINT_URL
##   - access_key None → S3_ACCESS_KEY → AWS_ACCESS_KEY_ID → ""
##   - secret_key None → S3_SECRET_KEY → AWS_SECRET_ACCESS_KEY → ""
##   - region None → S3_REGION env → platform_config.default_s3_region()
##   - BotoConfig(retries={"max_attempts": max_attempts, "mode": "standard"})
##   - boto3/botocore импортируются ТОЛЬКО внутри функции (lazy, F-08)
def get_s3_client(
    endpoint: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    max_attempts: int = 3,
    region: str | None = None,
) -> boto3.client:
    """Create a boto3 S3 client from explicit params or environment fallbacks.

    ## @purpose — Единая фабрика S3-клиента для платформенного домена (bootstrap).
    ##            Обрабатывает env-цепочку: explicit param → S3_* env → AWS_* env → defaults.
    ## @io — ⇥ endpoint/access_key/secret_key/max_attempts/region → ⎋ boto3 S3 client
    ## @complexity — O(1)
    """
    # F-08 (DevPlan 015): lazy-импорт — boto3/botocore тянутся только при реальном вызове.
    # Top-level импорт ронял s3_ssl_cache-модуль при отсутствии boto3 (S3-кеш молча выключен).
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config as BotoConfig  # type: ignore[import-untyped]

    ep = endpoint or os.environ.get("S3_ENDPOINT_URL") or DEFAULT_S3_ENDPOINT_URL

    akid = access_key or os.environ.get("S3_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID") or ""
    sak = secret_key or os.environ.get("S3_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY") or ""
    resolved_region = region or os.environ.get("S3_REGION") or platform_config.default_s3_region()

    logger.info(
        "[IMP:8][s3_client] Creating S3 client (endpoint=%s, max_attempts=%d, region=%s)",
        ep,
        max_attempts,
        resolved_region,
    )
    return boto3.client(
        "s3",
        endpoint_url=ep,
        aws_access_key_id=akid,
        aws_secret_access_key=sak,
        region_name=resolved_region,
        config=BotoConfig(retries={"max_attempts": max_attempts, "mode": "standard"}),
    )


# endregion FUNC_get_s3_client
