#!/usr/bin/env python3
# GREP_SUMMARY: s3-ssl-cache, boto3, cert-upload, cert-download, cert-check, bulk-restore, letsencrypt
# STRUCTURE: ▶ upload_cert → download_cert → check_cert → bulk_restore → ⎋ CLI entry
# region MODULE_CONTRACT
## @purpose  Python port of the shell s3-ssl-cache — SSL certificate caching on S3.
##           Provides four operations: upload (save certs after issue), download
##           (restore certs before issue), check (validate cached cert), bulk-restore
##           (restore all domains from node.yaml). Direct os.environ access eliminates
##           the subshell credential propagation bug (DevPlan 052 root cause).
## @scope    Called from cert_orchestrator.py (direct import, no subprocess) and from
##           the shell s3-ssl-cache CLI facade (for backward compat with issue_cert.py).
## @location core/internal/bootstrap/s3_ssl_cache.py
## @input    env: S3_ACCESS_KEY, S3_SECRET_KEY, S3_ENDPOINT_URL, S3_BUCKET, S3_REGION
## @output   Each function returns bool (success/failure) — non-fatal, never raises.
## @invariants
##   - Non-fatal: all exceptions caught, logged as warnings, return False
##   - Uses boto3 client with retries (max_attempts=3, mode='standard')
##   - Direct os.environ access — no subshell, no credential propagation bug
##   - uploaded files: fullchain.pem, privkey.pem, chain.pem (opt), account.tar.gz, cert.pem (opt)
##   - S3 key pattern: s3://<bucket>/<prefix>/<domain>/{fullchain,privkey,chain,account,cert}.pem|tar.gz
##   - check cert uses openssl x509 -checkend 2592000 (>30 days), issuer validation, domain match
##   - download validates openssl parseability, LE issuer, domain match before restoring
##   - REF-0008: privkey ОБЯЗАТЕЛЕН в download (partial restore без ключа = TLS outage при DR)
##     + pubkey-match пары cert↔key ДО commit на диск (несогласованная пара не пишется никогда)
##   - account.tar.gz is the tar of acme.sh domain dir for domain persistence
##   - DevPlan 015 F-08: НЕТ top-level импортов boto3/botocore — модуль грузится БЕЗ boto3;
##     S3-операции деградируют точным WARN «boto3 missing» + return False (non-fatal),
##     а не «module not loaded» (модуль выключен целиком)
## @rationale Eliminates root cause of DevPlan 052 bug (subshell credential propagation).
##            Eliminates two Tier-1 Strangler triggers (inline python3 heredoc in
##            _s3_download_file and _s3_bulk_restore). Direct import enables typed API
##            contract instead of subprocess string-based protocol.
## @changes   CREATED: 2026-07-25 · DevPlan 052 Phase 1 — Python port of the shell s3-ssl-cache
## @changes   2026-08-27 | DevPlan 015 F-08 — lazy boto3/botocore: top-level импорты убраны,
##            _boto3_available() + _get_s3_client→None + локальные exception-классы (S3-кеш
##            грузится и деградирует точным диагнозом при отсутствии boto3)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import cast

# ⚠️ TRAP[BUG] · 2026-08-05 · HI · cron-контекст acme.sh: python3 s3_ssl_cache.py upload $domain → ModuleNotFoundError
# · Symptom: issue_cert.py --reloadcmd/--renew-hook (install-cert reloadcmd, cron_installer --renew-hook) вызывают
# ·   `python3 <SCRIPT_DIR>/s3_ssl_cache.py upload <domain>` из cron-окружения acme.sh — без PYTHONPATH
# ·   (daily renewal, env -i-like) → `from core.internal...` падал → S3-бэкап сертификатов молча терялся.
# · Root: sys.path-инъекция корня репо отсутствовала; core.* импорты (config/platform_config,
# ·   shared/atomic_writer, shared/deploy_paths, shared/s3_client, shared/ssl_certs) требовали PYTHONPATH.
# · Fix: self-bootstrap корня репо (канон config_renderer.py:44-45) ДО core.* импортов.
# ·   Файл: core/internal/bootstrap/s3_ssl_cache.py → корень = 4 уровня parent.
# · Prevention: любой модуль, вызываемый из cron/hook-контекста (чистый env), обязан иметь self-bootstrap.
# · DevPlan 136 W2 T2.2: тест env -i python3 s3_ssl_cache.py → осмысленный exit (0/1 usage), НЕ ModuleNotFoundError.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.internal.config import platform_config

# DevPlan 118 C7: /etc/letsencrypt/live — единый резолвер shared/deploy_paths.letsencrypt_live().
# DevPlan 119 E5: атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
# Финальный коммит восстановленных pem-файлов (download→validate→atomic commit).
from core.internal.shared.atomic_writer import atomic_write as _atomic_write
from core.internal.shared.deploy_paths import letsencrypt_live
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    PlatformFatalError,
)
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.s3_client import (
    get_s3_client as _shared_get_s3_client,  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: s3_client.get_s3_client аннотирован `-> boto3.client` (функция-as-тип) → Unknown
)
from core.internal.shared.ssl_certs import (
    DEFAULT_EXPIRY_THRESHOLD,
    DEFAULT_OPENSSL_TIMEOUT,
    cert_is_valid,  # C9: единая комбинация «cert валиден» (DevPlan 118 C9)
    cert_key_pair_matches,  # REF-0008: pubkey-match пары cert↔key до commit на диск
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
_ARGV_CMD_MIN: int = 2  # argv: <command> <arg>
_ARGV_CMD_AND_ARG: int = 3  # argv: <command> <subcommand> <arg>
DEFAULT_CERT_DIR = str(letsencrypt_live())
DEFAULT_ACME_HOME = "/opt/acme.sh"
DEFAULT_SSL_CACHE_PREFIX = "platform/ssl-certs"
# DEFAULT_S3_ENDPOINT_URL → shared/s3_client (DevPlan 117 D26)
# DEFAULT_S3_REGION removed — use platform_config.default_s3_region() instead
# OPENSSL_TIMEOUT / CHECKEND_THRESHOLD → shared/ssl_certs (DevPlan 117 D21):
#   DEFAULT_OPENSSL_TIMEOUT / DEFAULT_EXPIRY_THRESHOLD (единый источник openssl-примитивов)


# region INTERNAL HELPERS


# region FUNC__boto3_available
## @purpose  Lazy-проверка доступности boto3/botocore (DevPlan 015 F-08). Модуль грузится
##           БЕЗ boto3 (top-level импортов нет); S3-операции деградируют точным WARN +
##           return False (non-fatal контракт) вместо «module not loaded» (кеш выключен целиком).
## @io       ⇥ None → ⎋ bool (True = boto3/botocore доступны)
## @complexity — O(1) — import-проба
## @invariants
##   - ImportError → WARN «boto3 missing — install via python_deps ensure» + return False
##   - Логируется ОДИН раз за вызов (не спамит в цикле по доменам)
def _boto3_available() -> bool:
    """Return True if boto3/botocore are importable (lazy — module loads without them, F-08)."""
    try:
        import boto3  # ruff: ignore[F401] — lazy-проба (F-08)
        import botocore  # ruff: ignore[F401] — lazy-проба (F-08)
    except ImportError:
        logger.warning(
            "[IMP:7][s3_ssl_cache] boto3 missing — S3 cache degraded "
            "(boto3 не установлен в интерпретаторе; install via python_deps ensure)"
        )
        return False
    return True


# endregion FUNC__boto3_available


# region FUNC_get_s3_client
## @purpose  Create boto3 S3 client from os.environ. Strips proxy vars first
##           (defence-in-depth against leaked HTTPS_PROXY from secrets.env).
##           Делегирует создание клиента в shared/s3_client.get_s3_client (DevPlan 117 D26).
##           Возвращает None при отсутствии boto3 (F-08: lazy-деградация, non-fatal).
## @io — ⇥ None (reads env) → ⎋ boto3 S3 client | None (boto3 missing)
## @complexity — O(1)
## @invariants
##   - Proxy vars (HTTPS_PROXY, HTTP_PROXY, NO_PROXY) stripped before client creation
##   - Fallbacks (endpoint/keys/region) — в shared/s3_client (env-цепочка, DevPlan 117 D26)
##   - Uses botocore retries: max_attempts=3, mode='standard'
##   - None при отсутствии boto3 — вызывающий обязан вернуть False (не raise)
def _get_s3_client() -> object | None:
    """Create boto3 S3 client from environment variables (delegates to shared/s3_client).

    Strips proxy vars that may have leaked from secrets.env to prevent
    ProxyConnectionError on VPS (defence-in-depth).

    ## @changes 2026-08-15 | W11-G3 — аннотация boto3.client → object (boto3 stub-less;
    ##            клиент используется только через attribute-access с ignore-комментариями)
    ## @changes 2026-08-27 | DevPlan 015 F-08 — None при отсутствии boto3 (lazy-деградация)
    """
    # Defence-in-depth: strip proxy vars that leaked from secrets.env
    for proxy_var in (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "https_proxy",
        "http_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        os.environ.pop(proxy_var, None)

    if not _boto3_available():
        return None  # WARN уже в _boto3_available; контракт non-fatal — caller вернёт False

    return cast(
        "object", _shared_get_s3_client(max_attempts=3)
    )  # W11-G1 cross-file: shared get_s3_client → Unknown (boto3.client-as-тип)


# endregion FUNC_get_s3_client


# region FUNC_validate_cert
## @purpose  Validate a downloaded PEM cert — ДЕЛЕГИРУЕТ в shared/ssl_certs.cert_is_valid
##           (DevPlan 118 C9, единая комбинация parseable+LE+pair-match+domain match+expiry).
##           Тонкий совместимый wrapper (S3-кеш семантика: expected_domains + check_expiry +
##           key_path); РЕАЛИЗАЦИЯ живёт в shared — 0 дублей логики (AC-C9).
## @io — ⇥ cert_path: str, domain: str, check_expiry: bool, key_path: str | None → ⎋ bool
## @complexity — O(1) + openssl subprocess (в shared)
## @invariants
##   - Returns False on any validation failure (corrupt cert, wrong issuer, mismatch)
##   - key_path задан → pair-match обязателен (REF-0008: несогласованная пара = invalid)
##   - Non-fatal: on openssl failure, returns False (never raises)
def _validate_cert(cert_path: str, domain: str, check_expiry: bool = True, *, key_path: str | None = None) -> bool:
    """Validate PEM cert at cert_path — delegating to shared cert_is_valid (DevPlan 118 C9 + REF-0008)."""
    valid = cert_is_valid(
        cert_path,
        threshold=DEFAULT_EXPIRY_THRESHOLD,
        expected_domains=domain,
        check_expiry=check_expiry,
        timeout=DEFAULT_OPENSSL_TIMEOUT,
        key_path=key_path,
    )
    if valid:
        logger.info(
            "[IMP:9][s3_ssl_cache] Cert validated OK for %s (LE, domain match%s%s)",
            domain,
            ", expiry OK" if check_expiry else "",
            ", pair match OK" if key_path else "",
        )
    return valid


# endregion FUNC_validate_cert


# region FUNC_download_s3_file
## @purpose  Download a single file from S3 to local path. Returns True on success.
##           Used by both check_cert (temp download) and download_cert (restore).
## @io — ⇥ s3_key: str, local_dst: str, s3_client: object | None (W4b DI),
##          bucket: str | None = None (DI, W-H DevPlan 163 — bucket override; None = env S3_BUCKET)
##          → ⎋ bool
## @complexity — O(1) network call
## @invariants
##   - Returns False on ClientError (404/NoSuchKey = cache miss, logged at INFO)
##   - Returns False on any other exception (network error, logged at WARN)
##   - Never raises
##   - s3_client параметром (W4b): ленивый default = _get_s3_client() (ровно текущее)
##   - bucket параметром (W-H): тесты передают "test-bucket" вместо monkeypatch.setenv(S3_BUCKET)
def _download_s3_file(
    s3_key: str,
    local_dst: str,
    *,
    s3_client: object | None = None,
    bucket: str | None = None,
) -> bool:
    """Download a single file from S3. Returns True on success.

    ## @purpose  Wrapper around boto3 client.download_file. Handles 404 as
    ##           cache miss (not an error).
    """
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        if s3_client is None:
            s3_client = _get_s3_client()
            if s3_client is None:
                # F-08: boto3 missing — WARN уже в _boto3_available(); non-fatal контракт
                return False
        client = cast("object", s3_client)
        resolved_bucket = (
            bucket if bucket is not None else os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
        )
        if not resolved_bucket:
            logger.warning("[IMP:7][s3_ssl_cache] S3_BUCKET not set — cannot download")
            return False
        client.download_file(resolved_bucket, s3_key, local_dst)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — boto3-клиент (stub-less, object); DI-переданный fake поддерживает тот же API
        logger.info("[IMP:9][s3_ssl_cache] Downloaded: %s → %s", s3_key, local_dst)
    # ruff: ignore[blind-except] — non-fatal контракт: любые boto3/OSError → WARN + False (F-08)
    except Exception as e:  # noqa: EXC — best-effort (non-fatal контракт s3_ssl_cache: never raise, return False; F-08: boto3-классы резолвятся ЛОКАЛЬНО)
        try:
            from botocore.exceptions import (
                ClientError as BotoClientError,  # type: ignore[import-untyped]  # lazy (F-08)
            )
        except ImportError:
            BotoClientError = ()  # boto3 отсутствует — классификация не нужна (guard не пустил бы сюда)
        if isinstance(e, BotoClientError):
            code = e.response.get("Error", {}).get("Code", "Unknown")
            if code in {"NoSuchKey", "404"}:
                logger.info("[IMP:8][s3_ssl_cache] S3 key not found (cache miss): %s", s3_key)
            else:
                logger.warning(
                    "[IMP:7][s3_ssl_cache] S3 ClientError (code=%s) for key %s: %s",
                    code,
                    s3_key,
                    e,
                )
        else:
            logger.warning("[IMP:7][s3_ssl_cache] S3 download failed for key %s: %s", s3_key, e)
        return False
    else:
        return True


# endregion FUNC_download_s3_file


# region FUNC_upload_s3_file
## @purpose  Upload a single file to S3. Returns True on success.
## @io — ⇥ local_path: str, s3_key: str, s3_client: object | None (W4b DI),
##          bucket: str | None = None (DI, W-H DevPlan 163 — bucket override; None = env S3_BUCKET)
##          → ⎋ bool
## @complexity — O(1) network call
## @invariants
##   - Returns False silently on failure (non-fatal)
##   - Never raises
def _upload_s3_file(
    local_path: str,
    s3_key: str,
    *,
    s3_client: object | None = None,
    bucket: str | None = None,
) -> bool:
    """Upload a single file to S3. Returns True on success.

    ## @purpose  Wrapper around boto3 client.upload_file. Non-fatal on failure.
    """
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        if s3_client is None:
            s3_client = _get_s3_client()
            if s3_client is None:
                # F-08: boto3 missing — WARN уже в _boto3_available(); non-fatal контракт
                return False
        client = cast("object", s3_client)
        resolved_bucket = (
            bucket if bucket is not None else os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
        )
        if not resolved_bucket:
            logger.warning("[IMP:7][s3_ssl_cache] S3_BUCKET not set — cannot upload")
            return False
        client.upload_file(local_path, resolved_bucket, s3_key)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] — boto3-клиент (stub-less, object); DI-переданный fake поддерживает тот же API
        logger.info("[IMP:9][s3_ssl_cache] Uploaded: %s → %s", local_path, s3_key)
    # ruff: ignore[blind-except] — non-fatal контракт: любые boto3/OSError → WARN + False (F-08)
    except Exception as e:  # noqa: EXC — best-effort (non-fatal контракт s3_ssl_cache: never raise, return False; F-08: top-level boto3-импорты убраны)
        # Прежний (ClientError, S3UploadFailedError, FileNotFoundError, OSError) → единый WARN;
        # except Exception покрывает тот же набор + любые boto3-сбои (контракт инварианта non-fatal).
        logger.warning(
            "[IMP:7][s3_ssl_cache] S3 upload failed for %s → %s: %s",
            local_path,
            s3_key,
            e,
        )
        return False
    else:
        return True


# endregion FUNC_upload_s3_file


# region FUNC_extract_domains_from_yaml
## @purpose  Parse node.yaml and extract all domains (platform + project domains).
## @io — ⇥ node_yaml_path: str → ⎋ list[str]
## @complexity — O(N) where N = number of projects
## @invariants
##   - Platform domain from data['domain'] or data['node']['platform_domain'] or data['node']['domain']
##   - Project domains from data['projects'][*]['domain']
##   - Deduplicates: same domain in platform and projects = one entry
##   - Returns empty list on missing/invalid YAML (never raises)
def _extract_domains_from_yaml(node_yaml_path: str) -> list[str]:
    """Extract all domains from a node.yaml file.

    ## @purpose  Port of the inline python3 YAML parsing from the shell s3-ssl-cache _s3_bulk_restore().
    """
    if not node_yaml_path or not os.path.isfile(node_yaml_path):
        logger.warning("[IMP:7][s3_ssl_cache] node.yaml not found: %s", node_yaml_path)
        return []

    try:
        node = NodeYaml(node_yaml_path)
    except (ConfigNotFoundError, ConfigParseError, OSError) as e:
        logger.warning("[IMP:7][s3_ssl_cache] Failed to parse node.yaml: %s", e)
        return []

    domains: list[str] = []

    # Platform domain: try top-level domain, then node.platform_domain, then node.domain
    domain = node.get("domain", default="") or ""  # W11-G1 cross-file: node_yaml.get (G1 overload) → str
    if not domain:
        domain = node.get("node.platform_domain", default="") or ""
    if not domain:
        domain = node.get("node.domain", default="") or ""
    if domain:
        domains.append(domain)

    # Project domains
    projects = cast(
        "object", node.get("projects", default=[])
    )  # W11-G1 cross-file: node_yaml.get (G1) — default=[] → list[Unknown]
    if isinstance(projects, list):
        for p in projects:  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: p Unknown из node_yaml.get (G1)
            if isinstance(p, dict):
                pd = cast("str", cast("dict[str, object]", p).get("domain", "") or "")
                if pd and pd not in domains:
                    domains.append(pd)

    return domains


# endregion FUNC_extract_domains_from_yaml


# endregion INTERNAL HELPERS


# region PUBLIC API


# region FUNC_upload_cert
## @purpose  Upload SSL cert files + acme.sh account data to S3.
##           Port of the shell s3-ssl-cache _s3_upload(). Uses boto3 directly.
## @io — ⇥ domain: str, cert_dir: str, acme_home: str, s3_bucket: str,
##       s3_prefix: str → ⎋ bool
## @complexity — O(N) where N = files to upload (~4-5)
## @invariants
##   - Required files: fullchain.pem, privkey.pem — others are best-effort
##   - Non-fatal: returns False on failure, never raises
##   - Uploads: fullchain.pem, privkey.pem, chain.pem (opt), cert.pem (opt), account.tar.gz
##   - Account data: tar czf acme.sh domain dir (ecc) → upload to S3
def upload_cert(
    domain: str,
    cert_dir: str = DEFAULT_CERT_DIR,
    acme_home: str = DEFAULT_ACME_HOME,
    s3_bucket: str = "",
    s3_prefix: str = DEFAULT_SSL_CACHE_PREFIX,
) -> bool:
    """Upload cert files to S3: fullchain.pem, privkey.pem, chain.pem (opt), account.tar.gz.

    ## @purpose — Port of the shell s3-ssl-cache _s3_upload(). Uses boto3 directly.
    ##            Reads S3_ACCESS_KEY, S3_SECRET_KEY, S3_ENDPOINT_URL from os.environ.
    ##            No subshell needed — works in the same Python process as caller.
    ## @invariants
    ##   - Non-fatal: returns False on failure, never raises
    ##   - Required files: fullchain.pem, privkey.pem (chain.pem optional)
    ##   - Account data: tar czf acme.sh domain dir → upload to S3
    ##   - Uses boto3 client with retries (max_attempts=3, mode='standard')
    ##   - bucket propaged to helpers (2026-08-27 fix)
    ## @rationale Eliminates inline python3 heredoc in the shell s3-ssl-cache _s3_upload().
    ##           Direct os.environ access fixes credential propagation bug.
    """
    if not s3_bucket:
        s3_bucket = os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
    if not s3_bucket:
        logger.warning("[IMP:7][s3_ssl_cache] S3_BUCKET not set — cannot upload cert for %s", domain)
        return False

    live_dir = os.path.join(cert_dir, domain)
    s3_base = f"{s3_prefix}/{domain}"
    overall_success = True

    # ⚠️ TRAP[BUG] · 2026-07-23 · G2 · chain.pem not required — acme.sh --install-cert
    # outputs only fullchain.pem + privkey.pem
    required_files = [
        ("fullchain.pem", os.path.join(live_dir, "fullchain.pem")),
        ("privkey.pem", os.path.join(live_dir, "privkey.pem")),
    ]

    # Validate required files exist
    missing = 0
    for _name, path in required_files:
        if not os.path.isfile(path):
            logger.warning("[IMP:8][s3_ssl_cache] Missing cert file for %s: %s", domain, path)
            missing += 1
    if missing > 0:
        logger.warning(
            "[IMP:7][s3_ssl_cache] %d cert file(s) missing for %s — cannot upload",
            missing,
            domain,
        )
        return False

    # Upload required files
    for name, path in required_files:
        s3_key = f"{s3_base}/{name}"
        if not _upload_s3_file(path, s3_key, bucket=s3_bucket):
            overall_success = False

    # Upload chain.pem if it exists (best-effort)
    chain_path = os.path.join(live_dir, "chain.pem")
    if os.path.isfile(chain_path):
        if not _upload_s3_file(chain_path, f"{s3_base}/chain.pem", bucket=s3_bucket):
            overall_success = False
    else:
        logger.info("[IMP:8][s3_ssl_cache] chain.pem not found for %s (expected for acme.sh) — skipping", domain)

    # Upload cert.pem if it exists (format, best-effort)
    cert_pem_path = os.path.join(live_dir, "cert.pem")
    if os.path.isfile(cert_pem_path) and not _upload_s3_file(cert_pem_path, f"{s3_base}/cert.pem", bucket=s3_bucket):
        overall_success = False

    # ⚠️ TRAP[BUG] · 2026-07-23 · G3 · acme.sh account data path uses <domain>_ecc/
    # · Fallback: data/<domain>/ (fallback)
    # Upload acme.sh account data for domain persistence
    acct_dir = _find_acme_account_dir(domain, acme_home)
    if acct_dir:
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_tar:
                tar_path = tmp_tar.name
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(acct_dir, arcname=Path(acct_dir).name)
            if _upload_s3_file(tar_path, f"{s3_base}/account.tar.gz", bucket=s3_bucket):
                logger.info("[IMP:9][s3_ssl_cache] Account data uploaded for %s", domain)
            else:
                overall_success = False
            os.unlink(tar_path)
        except (tarfile.TarError, OSError, FileNotFoundError) as e:
            logger.warning(
                "[IMP:7][s3_ssl_cache] Failed to pack/upload account data for %s: %s",
                domain,
                e,
            )
            overall_success = False
    else:
        logger.info(
            "[IMP:8][s3_ssl_cache] No acme.sh account data for %s — skipping account upload",
            domain,
        )

    if overall_success:
        logger.info("[IMP:9][s3_ssl_cache] Cert upload complete for %s", domain)
    return overall_success


# endregion FUNC_upload_cert


# region FUNC_download_cert
## @purpose  Download and validate cert from S3. Validates issuer (LE only), domain match,
##           openssl integrity AND cert↔key pair match (REF-0008). Returns True if restored.
##           Port of the shell s3-ssl-cache _s3_download() + _s3_download_file().
## @io — ⇥ domain: str, cert_dir: str, acme_home: str, s3_bucket: str,
##       s3_prefix: str → ⎋ bool
## @complexity — O(T) where T = S3 round-trips + openssl validation
## @invariants
##   - Validates with openssl before placing files on disk
##   - LE issuer check rejects mkcert/self-signed certs
##   - Domain match prevents serving wrong domain's cert
##   - REF-0008 (1): privkey.pem ОБЯЗАТЕЛЕН — отсутствие в S3 = restore failed (partial restore
##     без ключа давал «valid on disk» fullchain и TLS outage при DR-рестарте nginx)
##   - REF-0008 (2): pubkey-match пары cert↔key ДО атомарного commit — несогласованная пара
##     никогда не попадает на диск (crash между записями больше не создаёт broken state)
##   - Non-fatal: returns False on failure, never raises
##   - chain/account — optional, best-effort (не блокируют restore пары)
# region FUNC__plw_body_download_cert_2
## @purpose  Тело try-блока (PLW0717 extraction из download_cert) — семантика except не меняется.
## @io       ⇥ acme_home, domain, s3_base, tmp_account_path, bucket: str|None (None=env) → ⎋ результат try-тела
## @io       · bucket propaged to helpers (2026-08-27 fix)
## @complexity O(1) — извлечение управляющего потока
def _plw_body_download_cert_2(
    acme_home: str,
    domain: str,
    s3_base: str,
    tmp_account_path: str,
    *,
    bucket: str | None = None,
) -> None:
    if _download_s3_file(f"{s3_base}/account.tar.gz", tmp_account_path, bucket=bucket):
        os.makedirs(acme_home, exist_ok=True)
        with tarfile.open(tmp_account_path, "r:gz") as tar:
            # filter="data" (PEP 706) — consistent with orchestrator.py and payload_deliverer.py
            # nosec B202 — extracted from trusted S3 bucket (platform-owned)
            tar.extractall(path=acme_home, filter="data")  # nosec B202 — extracted from trusted S3 bucket (platform-owned)
        logger.info("[IMP:9][s3_ssl_cache] acme.sh account data restored for %s", domain)
    else:
        logger.info("[IMP:8][s3_ssl_cache] No account data in S3 for %s — skipping", domain)


# endregion FUNC__plw_body_download_cert_2


# region FUNC__plw_body_download_cert
## @purpose  Тело try-блока (PLW0717 extraction из download_cert) — семантика except не меняется.
## @io       ⇥ domain, live_dir, s3_base, tmp_chain_path, bucket: str|None (None=env) → ⎋ результат try-тела
## @io       · bucket propaged to helpers (2026-08-27 fix)
## @complexity O(1) — извлечение управляющего потока
def _plw_body_download_cert(
    domain: str,
    live_dir: str,
    s3_base: str,
    tmp_chain_path: str,
    *,
    bucket: str | None = None,
) -> None:
    if _download_s3_file(f"{s3_base}/chain.pem", tmp_chain_path, bucket=bucket):
        dest_chain = os.path.join(live_dir, "chain.pem")
        with Path(tmp_chain_path).open("rb") as tf:
            _atomic_write(dest_chain, tf.read(), mode=0o644)
        logger.info("[IMP:9][s3_ssl_cache] chain.pem restored for %s", domain)
    else:
        logger.info("[IMP:8][s3_ssl_cache] chain.pem not in S3 for %s — optional, skipping", domain)


# endregion FUNC__plw_body_download_cert


# region FUNC__restore_pair_body
## @purpose  Тело try-блока download_cert (C901-экстракция, REF-0008): скачать+валидировать
##           fullchain → privkey (обязателен) → pubkey-match → атомарный commit пары.
## @io       ⇥ domain, live_dir, s3_base, tmp_fullchain_path, tmp_privkey_path,
##            bucket: str|None (None=env) → ⎋ bool (True = пара закоммичена)
## @io       · bucket propaged to helpers (2026-08-27 fix)
## @complexity O(1) + 2 S3 download + 3 openssl subprocess
def _restore_pair_body(
    domain: str,
    live_dir: str,
    s3_base: str,
    tmp_fullchain_path: str,
    tmp_privkey_path: str,
    *,
    bucket: str | None = None,
) -> bool:
    """Скачать и провалидировать пару cert+key; закоммитить только согласованную (REF-0008)."""
    if not _download_s3_file(f"{s3_base}/fullchain.pem", tmp_fullchain_path, bucket=bucket):
        logger.info("[IMP:8][s3_ssl_cache] No fullchain.pem in S3 for %s — cache miss", domain)
        return False

    # Validate with openssl (parseable + LE issuer + domain match; expiry off — restore-first)
    if not _validate_cert(tmp_fullchain_path, domain, check_expiry=False):
        logger.warning(
            "[IMP:8][s3_ssl_cache] Downloaded fullchain.pem for %s failed validation",
            domain,
        )
        return False

    # REF-0008 (1): privkey обязателен — без него пара невосстановима (DR = TLS outage)
    if not _download_s3_file(f"{s3_base}/privkey.pem", tmp_privkey_path, bucket=bucket):
        logger.warning(
            "[IMP:7][s3_ssl_cache] privkey.pem missing in S3 for %s — refusing partial restore "
            "(cert without key cannot serve TLS)",
            domain,
        )
        return False

    # REF-0008 (2): pubkey-match ДО commit — несогласованная пара не пишется на диск
    if not cert_key_pair_matches(tmp_fullchain_path, tmp_privkey_path):
        logger.warning(
            "[IMP:7][s3_ssl_cache] privkey does not match certificate for %s — refusing to restore mismatched pair",
            domain,
        )
        return False

    # All validations passed — atomic commit of the consistent pair
    os.makedirs(live_dir, exist_ok=True)
    dest_fullchain = os.path.join(live_dir, "fullchain.pem")
    dest_privkey = os.path.join(live_dir, "privkey.pem")
    with Path(tmp_fullchain_path).open("rb") as tf:
        _atomic_write(dest_fullchain, tf.read(), mode=0o644)
    with Path(tmp_privkey_path).open("rb") as tf:
        _atomic_write(dest_privkey, tf.read(), mode=0o600)
    logger.info(
        "[IMP:9][s3_ssl_cache] Cert pair restored for %s (fullchain+privkey, pair matched)",
        domain,
    )
    return True


# endregion FUNC__restore_pair_body


def download_cert(
    domain: str,
    cert_dir: str = DEFAULT_CERT_DIR,
    acme_home: str = DEFAULT_ACME_HOME,
    s3_bucket: str = "",
    s3_prefix: str = DEFAULT_SSL_CACHE_PREFIX,
) -> bool:
    """Download and validate cert from S3. Validates issuer (LE only), domain match,
    openssl integrity, and cert/key pair match. Returns True if restored successfully.

    ## @purpose — Port of the shell s3-ssl-cache _s3_download(). Downloads files to temp,
    ##            validates with openssl (+pair-match, REF-0008), then commits atomically.
    ## @invariants
    ##   - fullchain.pem validated: openssl parseable, LE issuer, domain match
    ##   - privkey.pem REQUIRED (REF-0008): missing in S3 → False (no partial restore)
    ##   - pair pubkey-match required (REF-0008): mismatched pair never committed to disk
    ##   - chain.pem: optional, best-effort download
    ##   - account.tar.gz: extracted to acme_home/, non-fatal on failure
    ##   - bucket propaged to helpers (2026-08-27 fix)
    """
    if not s3_bucket:
        s3_bucket = os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
    if not s3_bucket:
        logger.warning("[IMP:7][s3_ssl_cache] S3_BUCKET not set — cannot download cert for %s", domain)
        return False

    live_dir = os.path.join(cert_dir, domain)
    s3_base = f"{s3_prefix}/{domain}"

    logger.info("[IMP:8][s3_ssl_cache] Downloading cert for %s from S3", domain)

    # ── Download + validate fullchain & privkey (REQUIRED pair, REF-0008) ──
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp_fullchain:
        tmp_fullchain_path = tmp_fullchain.name
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp_privkey:
        tmp_privkey_path = tmp_privkey.name

    restored = False
    try:
        restored = _restore_pair_body(domain, live_dir, s3_base, tmp_fullchain_path, tmp_privkey_path, bucket=s3_bucket)
    except (OSError, FileNotFoundError, PermissionError) as e:
        logger.warning("[IMP:7][s3_ssl_cache] Failed to restore cert pair for %s: %s", domain, e)
    finally:
        for tmp_path in (tmp_fullchain_path, tmp_privkey_path):
            if Path(tmp_path).exists():
                Path(tmp_path).unlink()
    if not restored:
        return False

    # ── Download chain.pem (optional) ──
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp_chain:
        tmp_chain_path = tmp_chain.name
    try:
        _plw_body_download_cert(domain, live_dir, s3_base, tmp_chain_path, bucket=s3_bucket)
    except (OSError, FileNotFoundError, PermissionError) as e:
        logger.warning("[IMP:7][s3_ssl_cache] Failed to restore chain.pem for %s: %s", domain, e)
    finally:
        if Path(tmp_chain_path).exists():
            os.unlink(tmp_chain_path)

    # ── Restore acme.sh account data ──
    # ⚠️ TRAP[BUG] · 2026-07-23 · G3 · Extract account.tar.gz to ACME_HOME/ not data/
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_account:
        tmp_account_path = tmp_account.name
    try:
        _plw_body_download_cert_2(acme_home, domain, s3_base, tmp_account_path, bucket=s3_bucket)
    except (tarfile.TarError, OSError, FileNotFoundError) as e:
        logger.warning(
            "[IMP:7][s3_ssl_cache] Failed to restore account data for %s: %s",
            domain,
            e,
        )
    finally:
        if Path(tmp_account_path).exists():
            os.unlink(tmp_account_path)

    logger.info("[IMP:9][s3_ssl_cache] Cert download complete for %s", domain)
    return True


# endregion FUNC_download_cert

# region FUNC_check_cert
## @purpose  Check if valid cert PAIR exists in S3 (>30 days expiry, correct domain, LE issuer,
##           privkey present + pubkey-match — REF-0008). False при отсутствии/несогласованности
##           privkey: download_cert всё равно отказал бы в restore (mandatory pair),
##           pointless issue-attempt исключается.
## @io — ⇥ domain: str, s3_bucket: str, s3_prefix: str → ⎋ bool
## @complexity — O(1) + 2 S3 downloads + openssl validation
## @invariants
##   - Downloads fullchain.pem + privkey.pem to temp files, deletes after validation
##   - REF-0008: privkey missing/mismatching → False (cache считается невалидным)
##   - Non-fatal: returns False on any failure (S3 unavailable, cert expired, etc.)
##   - bucket propaged to helpers (2026-08-27 fix)


# region FUNC__check_pair_body
## @purpose  Тело try-блока check_cert (C901-экстракция, REF-0008): валидация fullchain +
##           обязательный privkey + pubkey-match кэша.
## @io       ⇥ domain, s3_prefix, tmp_cert_path, tmp_key_path, bucket: str|None (None=env) → ⎋ bool (True = валидная пара в S3)
## @io       · bucket propaged to helpers (2026-08-27 fix)
## @complexity O(1) + 2 S3 download + 4 openssl subprocess
def _check_pair_body(
    domain: str,
    s3_prefix: str,
    tmp_cert_path: str,
    tmp_key_path: str,
    *,
    bucket: str | None = None,
) -> bool:
    """Валидировать закэшированную пару: cert-валидность + privkey presence + pair-match."""
    if not _download_s3_file(f"{s3_prefix}/{domain}/fullchain.pem", tmp_cert_path, bucket=bucket):
        logger.info("[IMP:8][s3_ssl_cache] No cert in S3 for %s — cache miss", domain)
        return False

    # Validate cert: LE issuer, domain match, >30 days expiry
    if not _validate_cert(tmp_cert_path, domain, check_expiry=True):
        logger.info("[IMP:8][s3_ssl_cache] Cached cert for %s failed validation", domain)
        return False

    # REF-0008: privkey обязателен и должен соответствовать сертификату
    if not _download_s3_file(f"{s3_prefix}/{domain}/privkey.pem", tmp_key_path, bucket=bucket):
        logger.warning(
            "[IMP:7][s3_ssl_cache] Cached privkey.pem missing for %s — cache invalid "
            "(restore would fail on mandatory pair)",
            domain,
        )
        return False
    if not cert_key_pair_matches(tmp_cert_path, tmp_key_path):
        logger.warning(
            "[IMP:7][s3_ssl_cache] Cached pair mismatched for %s — cache invalid (REF-0008)",
            domain,
        )
        return False

    logger.info("[IMP:9][s3_ssl_cache] Valid cert pair in S3 for %s", domain)
    return True


# endregion FUNC__check_pair_body


def check_cert(
    domain: str,
    s3_bucket: str = "",
    s3_prefix: str = DEFAULT_SSL_CACHE_PREFIX,
) -> bool:
    """Check if valid cert pair exists in S3 (>30 days, correct domain, LE issuer, key match).

    ## @purpose — Port of the shell s3-ssl-cache _s3_check(). Downloads fullchain+privkey to
    ##            temp, validates with openssl (checkend 2592000s, issuer, domain match,
    ##            pubkey-match — REF-0008).
    ## @returns True if valid LE cert pair >30 days exists in S3
    """
    if not s3_bucket:
        s3_bucket = os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
    if not s3_bucket:
        logger.info("[IMP:8][s3_ssl_cache] S3_BUCKET not set — cannot check %s", domain)
        return False

    logger.info("[IMP:8][s3_ssl_cache] Checking S3 cache for %s", domain)

    # Download fullchain.pem + privkey.pem to temp
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp_cert:
        tmp_cert_path = tmp_cert.name
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp_key:
        tmp_key_path = tmp_key.name

    try:
        return _check_pair_body(domain, s3_prefix, tmp_cert_path, tmp_key_path, bucket=s3_bucket)
    finally:
        for tmp_path in (tmp_cert_path, tmp_key_path):
            if Path(tmp_path).exists():
                os.unlink(tmp_path)


# endregion FUNC_check_cert


# region FUNC_bulk_restore
## @purpose  Parse node.yaml → extract all domains → check + download each.
##           Returns {domain: status} dict. Replaces inline python3 YAML parsing
##           from the shell s3-ssl-cache _s3_bulk_restore().
## @io — ⇥ node_yaml_path: str, s3_bucket: str, s3_prefix: str → ⎋ dict[str, str]
## @complexity — O(D * (check + download)) where D = number of domains
## @invariants
##   - Non-fatal: failure of one domain does not block others
##   - Returns dict with status per domain: "restored" | "miss" | "error"
##   - Empty dict on missing YAML or no domains
def bulk_restore(
    node_yaml_path: str,
    s3_bucket: str = "",
    s3_prefix: str = DEFAULT_SSL_CACHE_PREFIX,
) -> dict[str, str]:
    """Parse node.yaml → extract all domains → check + download each.

    ## @purpose — Port of the shell s3-ssl-cache _s3_bulk_restore(). Replaces inline
    ##            python3 YAML parsing + JSON output with typed Python API.
    ## @returns {domain: status} dict where status ∈ {"restored", "miss", "error"}
    """
    if not s3_bucket:
        s3_bucket = os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
    if not s3_bucket:
        s3_bucket = ""

    domains = _extract_domains_from_yaml(node_yaml_path)
    if not domains:
        logger.info("[IMP:8][s3_ssl_cache] No domains found in %s", node_yaml_path)
        return {}

    result: dict[str, str] = {}
    logger.info(
        "[IMP:8][s3_ssl_cache] Bulk restore for %d domains from %s",
        len(domains),
        node_yaml_path,
    )

    for domain in domains:
        if not domain:
            continue
        status = "miss"
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            if check_cert(domain, s3_bucket, s3_prefix):
                if download_cert(domain, DEFAULT_CERT_DIR, DEFAULT_ACME_HOME, s3_bucket, s3_prefix):
                    status = "restored"
                    logger.info("[IMP:9][s3_ssl_cache] Bulk restored: %s", domain)
                else:
                    status = "error"
                    logger.warning("[IMP:7][s3_ssl_cache] Bulk download failed: %s", domain)
                logger.info("[IMP:8][s3_ssl_cache] Bulk cache miss: %s", domain)
        except (ConfigNotFoundError, ConfigParseError, PlatformFatalError, OSError) as e:
            status = "error"
            logger.warning("[IMP:7][s3_ssl_cache] Bulk restore error for %s: %s", domain, e)
        result[domain] = status

    restored_count = sum(1 for v in result.values() if v == "restored")
    logger.info(
        "[IMP:9][s3_ssl_cache] Bulk restore complete: %d/%d restored",
        restored_count,
        len(domains),
    )
    return result


# endregion FUNC_bulk_restore


# endregion PUBLIC API


# region HELPERS


# region FUNC_find_acme_account_dir
## @purpose  Find acme.sh account directory for a domain.
##           Tries <domain\>_ecc/ first (acme.sh default), falls back to data/<domain\>/ (fallback).
## @io — ⇥ domain: str, acme_home: str → ⎋ str | None
## @complexity — O(1) — filesystem stat calls
## @invariants
##   - Returns None if neither path exists
def _find_acme_account_dir(domain: str, acme_home: str) -> str | None:
    """Find acme.sh account directory for domain.

    ⚠️ TRAP[BUG] · 2026-07-23 · G3 · acme.sh account data path uses <domain\\>_ecc/
    · Observed: account data never uploaded because data/<domain\\>/ doesn't exist
    · Root: acme.sh stores account data in <domain\\>_ecc/ directory structure
    · Fix: try <domain\\>_ecc first (acme.sh default), fall back to data/<domain\\> (fallback)
    """
    ecc_path = os.path.join(acme_home, f"{domain}_ecc")
    if os.path.isdir(ecc_path):
        return ecc_path

    fallback_path = os.path.join(acme_home, "data", domain)
    if os.path.isdir(fallback_path):
        return fallback_path

    return None


# endregion FUNC_find_acme_account_dir


# endregion HELPERS


# region CLI

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [IMP:%(levelno)s][s3_ssl_cache] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
    )

    if len(sys.argv) < _ARGV_CMD_MIN:
        print("Usage: s3_ssl_cache.py <upload|download|check|bulk-restore> <domain|--node-yaml PATH>")
        sys.exit(1)

    command = sys.argv[1]

    if command == "bulk-restore":
        node_yaml: str = ""
        for i, arg in enumerate(sys.argv[2:], start=2):
            if arg == "--node-yaml" and i + 1 < len(sys.argv):
                node_yaml = sys.argv[i + 1]
        result = bulk_restore(node_yaml)
        print(json.dumps(result))
        sys.exit(0)
    elif command in {"upload", "download", "check"}:
        if len(sys.argv) < _ARGV_CMD_AND_ARG:
            print(f"Usage: s3_ssl_cache.py {command} <domain>")
            sys.exit(1)
        domain = sys.argv[2]
        s3_bucket = os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
        ok = False
        if command == "upload":
            ok = upload_cert(domain, s3_bucket=s3_bucket)
        elif command == "download":
            ok = download_cert(domain, s3_bucket=s3_bucket)
        elif command == "check":
            ok = check_cert(domain, s3_bucket=s3_bucket)
        sys.exit(0 if ok else 1)
    else:
        print(f"Unknown command: {command}")
        print("Usage: s3_ssl_cache.py <upload|download|check|bulk-restore> <domain|--node-yaml PATH>")
        sys.exit(1)

# endregion CLI
