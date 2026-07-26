#!/usr/bin/env python3
# GREP_SUMMARY: s3-ssl-cache, boto3, cert-upload, cert-download, cert-check, bulk-restore, letsencrypt
# STRUCTURE: ▶ upload_cert → download_cert → check_cert → bulk_restore → ⎋ CLI entry
# region MODULE_CONTRACT
## @purpose  Python port of s3-ssl-cache.sh — SSL certificate caching on S3.
##           Provides four operations: upload (save certs after issue), download
##           (restore certs before issue), check (validate cached cert), bulk-restore
##           (restore all domains from node.yaml). Direct os.environ access eliminates
##           the subshell credential propagation bug (DevPlan 052 root cause).
## @scope    Called from cert_orchestrator.py (direct import, no subprocess) and from
##           s3-ssl-cache.sh CLI facade (for backward compat with issue-cert.sh).
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
##   - account.tar.gz is the tar of acme.sh domain dir for domain persistence
## @rationale Eliminates root cause of DevPlan 052 bug (subshell credential propagation).
##            Eliminates two Tier-1 Strangler triggers (inline python3 heredoc in
##            _s3_download_file and _s3_bulk_restore). Direct import enables typed API
##            contract instead of subprocess string-based protocol.
## @changes   CREATED: 2026-07-25 · DevPlan 052 Phase 1 — Python port of s3-ssl-cache.sh
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tarfile
import tempfile

import boto3
import yaml
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
DEFAULT_CERT_DIR = "/etc/letsencrypt/live"
DEFAULT_ACME_HOME = "/opt/acme.sh"
DEFAULT_S3_PREFIX = "platform/ssl-certs"
DEFAULT_S3_ENDPOINT_URL = "https://s3.timeweb.cloud"
DEFAULT_S3_REGION = "us-east-1"
OPENSSL_TIMEOUT = 10  # seconds for each openssl subprocess call
CHECKEND_THRESHOLD = 2592000  # 30 days in seconds


# region INTERNAL HELPERS


# region FUNC_get_s3_client
## @purpose  Create boto3 S3 client from os.environ. Strips proxy vars first
##           (defence-in-depth against leaked HTTPS_PROXY from secrets.env).
## @io — ⇥ None (reads env) → ⎋ boto3 S3 client
## @complexity — O(1)
## @invariants
##   - Proxy vars (HTTPS_PROXY, HTTP_PROXY, NO_PROXY) stripped before client creation
##   - Falls back to DEFAULT_S3_ENDPOINT_URL constant if S3_ENDPOINT_URL not set
##   - Falls back to AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY if S3_* not set
##   - Uses botocore retries: max_attempts=3, mode='standard'
def _get_s3_client() -> boto3.client:
    """Create boto3 S3 client from environment variables.

    Strips proxy vars that may have leaked from secrets.env to prevent
    ProxyConnectionError on VPS (defence-in-depth).
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

    endpoint = os.environ.get("S3_ENDPOINT_URL") or DEFAULT_S3_ENDPOINT_URL
    akid = os.environ.get("S3_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID") or ""
    sak = os.environ.get("S3_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY") or ""
    region = os.environ.get("S3_REGION", DEFAULT_S3_REGION)

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=akid,
        aws_secret_access_key=sak,
        region_name=region,
        config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
    )


# endregion FUNC_get_s3_client


# region FUNC_validate_cert
## @purpose  Validate a downloaded PEM cert: openssl parseability, LE issuer,
##           domain subject match, and optional >30-day expiry check.
## @io — ⇥ cert_path: str, domain: str, check_expiry: bool → ⎋ bool
## @complexity — O(1) + 3-4 openssl subprocess calls
## @invariants
##   - Returns False on any validation failure (corrupt cert, wrong issuer, mismatch)
##   - LE issuer check: case-insensitive "Let's Encrypt" in issuer string
##   - Domain match: CN contains domain (supports wildcard *.domain)
##   - check_expiry: uses openssl x509 -checkend 2592000
##   - Non-fatal: on openssl failure, returns False (never raises)
def _validate_cert(cert_path: str, domain: str, check_expiry: bool = True) -> bool:
    """Validate PEM cert at cert_path: openssl parseable, LE issuer, domain match.

    ## @purpose  Shared validation used by both check_cert and download_cert.
    ## @invariants
    ##   - LE issuer: case-insensitive match on "Let's Encrypt"
    ##   - Domain match: CN contains domain (escaped for regex safety)
    ##   - check_expiry: openssl x509 -checkend <threshold>
    """
    try:
        # Step 1: Verify cert is parseable
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout"],
            capture_output=True,
            timeout=OPENSSL_TIMEOUT,
        )
        if result.returncode != 0:
            logger.info("[IMP:8][s3_ssl_cache] Cert not parseable: %s", cert_path)
            return False

        # Step 2: Verify Let's Encrypt issuer
        issuer_res = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-issuer", "-noout"],
            capture_output=True,
            text=True,
            timeout=OPENSSL_TIMEOUT,
        )
        if issuer_res.returncode != 0:
            return False
        cert_issuer = issuer_res.stdout.strip()
        if "Let's Encrypt" not in cert_issuer:
            logger.info(
                "[IMP:8][s3_ssl_cache] Cert issuer is not Let's Encrypt: %s",
                cert_issuer[:120],
            )
            return False

        # Step 3: Check domain subject match
        subject_res = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-subject", "-noout"],
            capture_output=True,
            text=True,
            timeout=OPENSSL_TIMEOUT,
        )
        if subject_res.returncode != 0:
            return False
        subject = subject_res.stdout.strip()
        # Match CN = domain or CN = *.domain (wildcard)
        if not (
            f"CN = {domain}" in subject
            or f"CN= {domain}" in subject
            or f"CN= {domain}" in subject
            or f"CN=*.{domain}" in subject
            or f"CN = *.{domain}" in subject
        ):
            logger.info(
                "[IMP:8][s3_ssl_cache] Cert subject does not match domain '%s': %s",
                domain,
                subject[:120],
            )
            return False

        # Step 4: Optionally check >30 days expiry
        if check_expiry:
            checkend = subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-in",
                    cert_path,
                    "-checkend",
                    str(CHECKEND_THRESHOLD),
                    "-noout",
                ],
                capture_output=True,
                timeout=OPENSSL_TIMEOUT,
            )
            if checkend.returncode != 0:
                logger.info(
                    "[IMP:8][s3_ssl_cache] Cert expires within 30 days or is expired: %s",
                    domain,
                )
                return False

        logger.info(
            "[IMP:9][s3_ssl_cache] Cert validated OK for %s (LE, domain match%s)",
            domain,
            ", expiry OK" if check_expiry else "",
        )
        return True

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("[IMP:7][s3_ssl_cache] Cert validation error for %s: %s", domain, e)
        return False


# endregion FUNC_validate_cert


# region FUNC_download_s3_file
## @purpose  Download a single file from S3 to local path. Returns True on success.
##           Used by both check_cert (temp download) and download_cert (restore).
## @io — ⇥ s3_key: str, local_dst: str → ⎋ bool
## @complexity — O(1) network call
## @invariants
##   - Returns False on ClientError (404/NoSuchKey = cache miss, logged at INFO)
##   - Returns False on any other exception (network error, logged at WARN)
##   - Never raises
def _download_s3_file(s3_key: str, local_dst: str) -> bool:
    """Download a single file from S3. Returns True on success.

    ## @purpose  Wrapper around boto3 client.download_file. Handles 404 as
    ##           cache miss (not an error).
    """
    try:
        client = _get_s3_client()
        bucket = os.environ.get("S3_BUCKET", "")
        if not bucket:
            logger.warning("[IMP:7][s3_ssl_cache] S3_BUCKET not set — cannot download")
            return False
        client.download_file(bucket, s3_key, local_dst)
        logger.info("[IMP:9][s3_ssl_cache] Downloaded: %s → %s", s3_key, local_dst)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        if code in ("NoSuchKey", "404"):
            logger.info("[IMP:8][s3_ssl_cache] S3 key not found (cache miss): %s", s3_key)
        else:
            logger.warning(
                "[IMP:7][s3_ssl_cache] S3 ClientError (code=%s) for key %s: %s",
                code,
                s3_key,
                e,
            )
        return False
    except Exception as e:
        logger.warning("[IMP:7][s3_ssl_cache] S3 download failed for key %s: %s", s3_key, e)
        return False


# endregion FUNC_download_s3_file


# region FUNC_upload_s3_file
## @purpose  Upload a single file to S3. Returns True on success.
## @io — ⇥ local_path: str, s3_key: str → ⎋ bool
## @complexity — O(1) network call
## @invariants
##   - Returns False silently on failure (non-fatal)
##   - Never raises
def _upload_s3_file(local_path: str, s3_key: str) -> bool:
    """Upload a single file to S3. Returns True on success.

    ## @purpose  Wrapper around boto3 client.upload_file. Non-fatal on failure.
    """
    try:
        client = _get_s3_client()
        bucket = os.environ.get("S3_BUCKET", "")
        if not bucket:
            logger.warning("[IMP:7][s3_ssl_cache] S3_BUCKET not set — cannot upload")
            return False
        client.upload_file(local_path, bucket, s3_key)
        logger.info("[IMP:9][s3_ssl_cache] Uploaded: %s → %s", local_path, s3_key)
        return True
    except Exception as e:
        logger.warning(
            "[IMP:7][s3_ssl_cache] S3 upload failed for %s → %s: %s",
            local_path,
            s3_key,
            e,
        )
        return False


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

    ## @purpose  Port of the inline python3 YAML parsing from s3-ssl-cache.sh _s3_bulk_restore().
    """
    if not node_yaml_path or not os.path.isfile(node_yaml_path):
        logger.warning("[IMP:7][s3_ssl_cache] node.yaml not found: %s", node_yaml_path)
        return []

    try:
        with open(node_yaml_path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("[IMP:7][s3_ssl_cache] Failed to parse node.yaml: %s", e)
        return []

    domains: list[str] = []

    # Platform domain
    domain = data.get("domain", "")
    if not domain:
        node_info = data.get("node", {})
        if isinstance(node_info, dict):
            domain = node_info.get("platform_domain", "") or node_info.get("domain", "")
    if domain:
        domains.append(domain)

    # Project domains
    projects = data.get("projects", [])
    if isinstance(projects, list):
        for p in projects:
            if isinstance(p, dict):
                pd = p.get("domain", "")
                if pd and pd not in domains:
                    domains.append(pd)

    return domains


# endregion FUNC_extract_domains_from_yaml


# endregion INTERNAL HELPERS


# region PUBLIC API


# region FUNC_upload_cert
## @purpose  Upload SSL cert files + acme.sh account data to S3.
##           Port of s3-ssl-cache.sh _s3_upload(). Uses boto3 directly.
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
    s3_prefix: str = DEFAULT_S3_PREFIX,
) -> bool:
    """Upload cert files to S3: fullchain.pem, privkey.pem, chain.pem (opt), account.tar.gz.

    ## @purpose — Port of s3-ssl-cache.sh _s3_upload(). Uses boto3 directly.
    ##            Reads S3_ACCESS_KEY, S3_SECRET_KEY, S3_ENDPOINT_URL from os.environ.
    ##            No subshell needed — works in the same Python process as caller.
    ## @invariants
    ##   - Non-fatal: returns False on failure, never raises
    ##   - Required files: fullchain.pem, privkey.pem (chain.pem optional)
    ##   - Account data: tar czf acme.sh domain dir → upload to S3
    ##   - Uses boto3 client with retries (max_attempts=3, mode='standard')
    ## @rationale Eliminates inline python3 heredoc in s3-ssl-cache.sh _s3_upload().
    ##           Direct os.environ access fixes credential propagation bug.
    """
    if not s3_bucket:
        s3_bucket = os.environ.get("S3_BUCKET", "")
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
        if not _upload_s3_file(path, s3_key):
            overall_success = False

    # Upload chain.pem if it exists (best-effort)
    chain_path = os.path.join(live_dir, "chain.pem")
    if os.path.isfile(chain_path):
        if not _upload_s3_file(chain_path, f"{s3_base}/chain.pem"):
            overall_success = False
    else:
        logger.info("[IMP:8][s3_ssl_cache] chain.pem not found for %s (expected for acme.sh) — skipping", domain)

    # Upload cert.pem if it exists (legacy format, best-effort)
    cert_pem_path = os.path.join(live_dir, "cert.pem")
    if os.path.isfile(cert_pem_path) and not _upload_s3_file(cert_pem_path, f"{s3_base}/cert.pem"):
        overall_success = False

    # ⚠️ TRAP[BUG] · 2026-07-23 · G3 · acme.sh account data path uses <domain>_ecc/
    # · Fallback: data/<domain>/ (legacy)
    # Upload acme.sh account data for domain persistence
    acct_dir = _find_acme_account_dir(domain, acme_home)
    if acct_dir:
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_tar:
                tar_path = tmp_tar.name
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(acct_dir, arcname=os.path.basename(acct_dir))
            if _upload_s3_file(tar_path, f"{s3_base}/account.tar.gz"):
                logger.info("[IMP:9][s3_ssl_cache] Account data uploaded for %s", domain)
            else:
                overall_success = False
            os.unlink(tar_path)
        except Exception as e:
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
##           openssl integrity. Returns True if restored successfully.
##           Port of s3-ssl-cache.sh _s3_download() + _s3_download_file().
## @io — ⇥ domain: str, cert_dir: str, acme_home: str, s3_bucket: str,
##       s3_prefix: str → ⎋ bool
## @complexity — O(T) where T = S3 round-trips + openssl validation
## @invariants
##   - Validates with openssl before placing files on disk
##   - LE issuer check rejects mkcert/self-signed certs
##   - Domain match prevents serving wrong domain's cert
##   - Non-fatal: returns False on failure, never raises
##   - Partial restore: privkey/chain download failure doesn't block full restore
def download_cert(
    domain: str,
    cert_dir: str = DEFAULT_CERT_DIR,
    acme_home: str = DEFAULT_ACME_HOME,
    s3_bucket: str = "",
    s3_prefix: str = DEFAULT_S3_PREFIX,
) -> bool:
    """Download and validate cert from S3. Validates issuer (LE only), domain match,
    openssl integrity. Returns True if restored successfully.

    ## @purpose — Port of s3-ssl-cache.sh _s3_download(). Downloads files to temp,
    ##            validates with openssl, then moves to destination.
    ## @invariants
    ##   - fullchain.pem validated: openssl parseable, LE issuer, domain match
    ##   - privkey.pem: downloaded but not validated (private key)
    ##   - chain.pem: optional, best-effort download
    ##   - account.tar.gz: extracted to acme_home/, non-fatal on failure
    """
    if not s3_bucket:
        s3_bucket = os.environ.get("S3_BUCKET", "")
    if not s3_bucket:
        logger.warning("[IMP:7][s3_ssl_cache] S3_BUCKET not set — cannot download cert for %s", domain)
        return False

    live_dir = os.path.join(cert_dir, domain)
    s3_base = f"{s3_prefix}/{domain}"

    logger.info("[IMP:8][s3_ssl_cache] Downloading cert for %s from S3", domain)

    # ── Download fullchain.pem (required) ──
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp_fullchain:
        tmp_fullchain_path = tmp_fullchain.name

    try:
        if not _download_s3_file(f"{s3_base}/fullchain.pem", tmp_fullchain_path):
            logger.info("[IMP:8][s3_ssl_cache] No fullchain.pem in S3 for %s — cache miss", domain)
            os.unlink(tmp_fullchain_path)
            return False

        # Validate with openssl
        if not _validate_cert(tmp_fullchain_path, domain, check_expiry=False):
            logger.warning(
                "[IMP:8][s3_ssl_cache] Downloaded fullchain.pem for %s failed validation",
                domain,
            )
            os.unlink(tmp_fullchain_path)
            return False

        # Create live dir and restore fullchain.pem
        os.makedirs(live_dir, exist_ok=True)
        dest_fullchain = os.path.join(live_dir, "fullchain.pem")
        os.replace(tmp_fullchain_path, dest_fullchain)
        os.chmod(dest_fullchain, 0o644)
        logger.info("[IMP:9][s3_ssl_cache] fullchain.pem restored for %s", domain)
    except Exception as e:
        logger.warning("[IMP:7][s3_ssl_cache] Failed to restore fullchain.pem for %s: %s", domain, e)
        if os.path.exists(tmp_fullchain_path):
            os.unlink(tmp_fullchain_path)
        return False

    # ── Download privkey.pem ──
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp_privkey:
        tmp_privkey_path = tmp_privkey.name
    try:
        if _download_s3_file(f"{s3_base}/privkey.pem", tmp_privkey_path):
            dest_privkey = os.path.join(live_dir, "privkey.pem")
            os.replace(tmp_privkey_path, dest_privkey)
            os.chmod(dest_privkey, 0o600)
            logger.info("[IMP:9][s3_ssl_cache] privkey.pem restored for %s", domain)
        else:
            logger.warning("[IMP:8][s3_ssl_cache] privkey.pem not in S3 for %s — proceeding without it", domain)
    except Exception as e:
        logger.warning("[IMP:7][s3_ssl_cache] Failed to restore privkey.pem for %s: %s", domain, e)
    finally:
        if os.path.exists(tmp_privkey_path):
            os.unlink(tmp_privkey_path)

    # ── Download chain.pem (optional) ──
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp_chain:
        tmp_chain_path = tmp_chain.name
    try:
        if _download_s3_file(f"{s3_base}/chain.pem", tmp_chain_path):
            dest_chain = os.path.join(live_dir, "chain.pem")
            os.replace(tmp_chain_path, dest_chain)
            os.chmod(dest_chain, 0o644)
            logger.info("[IMP:9][s3_ssl_cache] chain.pem restored for %s", domain)
        else:
            logger.info("[IMP:8][s3_ssl_cache] chain.pem not in S3 for %s — optional, skipping", domain)
    except Exception as e:
        logger.warning("[IMP:7][s3_ssl_cache] Failed to restore chain.pem for %s: %s", domain, e)
    finally:
        if os.path.exists(tmp_chain_path):
            os.unlink(tmp_chain_path)

    # ── Restore acme.sh account data ──
    # ⚠️ TRAP[BUG] · 2026-07-23 · G3 · Extract account.tar.gz to ACME_HOME/ not data/
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_account:
        tmp_account_path = tmp_account.name
    try:
        if _download_s3_file(f"{s3_base}/account.tar.gz", tmp_account_path):
            os.makedirs(acme_home, exist_ok=True)
            with tarfile.open(tmp_account_path, "r:gz") as tar:
                tar.extractall(path=acme_home)  # nosec B202 — extracted from trusted S3 bucket (platform-owned)
            logger.info("[IMP:9][s3_ssl_cache] acme.sh account data restored for %s", domain)
        else:
            logger.info("[IMP:8][s3_ssl_cache] No account data in S3 for %s — skipping", domain)
    except Exception as e:
        logger.warning(
            "[IMP:7][s3_ssl_cache] Failed to restore account data for %s: %s",
            domain,
            e,
        )
    finally:
        if os.path.exists(tmp_account_path):
            os.unlink(tmp_account_path)

    logger.info("[IMP:9][s3_ssl_cache] Cert download complete for %s", domain)
    return True


# endregion FUNC_download_cert


# region FUNC_check_cert
## @purpose  Check if valid cert exists in S3 (>30 days expiry, correct domain, LE issuer).
##           Downloads fullchain.pem to temp, validates with openssl.
## @io — ⇥ domain: str, s3_bucket: str, s3_prefix: str → ⎋ bool
## @complexity — O(1) + S3 download + openssl validation
## @returns True if valid LE cert >30 days exists in S3 for domain
## @invariants
##   - Downloads fullchain.pem to temp file, deletes after validation
##   - Non-fatal: returns False on any failure (S3 unavailable, cert expired, etc.)
def check_cert(
    domain: str,
    s3_bucket: str = "",
    s3_prefix: str = DEFAULT_S3_PREFIX,
) -> bool:
    """Check if valid cert exists in S3 (>30 days expiry, correct domain, LE issuer).

    ## @purpose — Port of s3-ssl-cache.sh _s3_check(). Downloads fullchain.pem to temp,
    ##            validates with openssl (checkend 2592000s, issuer, domain match).
    ## @returns True if valid LE cert >30 days exists in S3
    """
    if not s3_bucket:
        s3_bucket = os.environ.get("S3_BUCKET", "")
    if not s3_bucket:
        logger.info("[IMP:8][s3_ssl_cache] S3_BUCKET not set — cannot check %s", domain)
        return False

    logger.info("[IMP:8][s3_ssl_cache] Checking S3 cache for %s", domain)

    # Download fullchain.pem to temp
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp_cert:
        tmp_cert_path = tmp_cert.name

    try:
        s3_key = f"{s3_prefix}/{domain}/fullchain.pem"
        if not _download_s3_file(s3_key, tmp_cert_path):
            logger.info("[IMP:8][s3_ssl_cache] No cert in S3 for %s — cache miss", domain)
            return False

        # Validate cert: LE issuer, domain match, >30 days expiry
        if _validate_cert(tmp_cert_path, domain, check_expiry=True):
            logger.info("[IMP:9][s3_ssl_cache] Valid cert in S3 for %s", domain)
            return True

        logger.info("[IMP:8][s3_ssl_cache] Cached cert for %s failed validation", domain)
        return False
    finally:
        if os.path.exists(tmp_cert_path):
            os.unlink(tmp_cert_path)


# endregion FUNC_check_cert


# region FUNC_bulk_restore
## @purpose  Parse node.yaml → extract all domains → check + download each.
##           Returns {domain: status} dict. Replaces inline python3 YAML parsing
##           from s3-ssl-cache.sh _s3_bulk_restore().
## @io — ⇥ node_yaml_path: str, s3_bucket: str, s3_prefix: str → ⎋ dict[str, str]
## @complexity — O(D * (check + download)) where D = number of domains
## @invariants
##   - Non-fatal: failure of one domain does not block others
##   - Returns dict with status per domain: "restored" | "miss" | "error"
##   - Empty dict on missing YAML or no domains
def bulk_restore(
    node_yaml_path: str,
    s3_bucket: str = "",
    s3_prefix: str = DEFAULT_S3_PREFIX,
) -> dict[str, str]:
    """Parse node.yaml → extract all domains → check + download each.

    ## @purpose — Port of s3-ssl-cache.sh _s3_bulk_restore(). Replaces inline
    ##            python3 YAML parsing + JSON output with typed Python API.
    ## @returns {domain: status} dict where status ∈ {"restored", "miss", "error"}
    """
    if not s3_bucket:
        s3_bucket = os.environ.get("S3_BUCKET", "")
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
        try:
            if check_cert(domain, s3_bucket, s3_prefix):
                if download_cert(domain, DEFAULT_CERT_DIR, DEFAULT_ACME_HOME, s3_bucket, s3_prefix):
                    status = "restored"
                    logger.info("[IMP:9][s3_ssl_cache] Bulk restored: %s", domain)
                else:
                    status = "error"
                    logger.warning("[IMP:7][s3_ssl_cache] Bulk download failed: %s", domain)
            else:
                logger.info("[IMP:8][s3_ssl_cache] Bulk cache miss: %s", domain)
        except Exception as e:
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
##           Tries <domain>_ecc/ first (acme.sh default), falls back to data/<domain>/ (legacy).
## @io — ⇥ domain: str, acme_home: str → ⎋ str | None
## @complexity — O(1) — filesystem stat calls
## @invariants
##   - Returns None if neither path exists
def _find_acme_account_dir(domain: str, acme_home: str) -> str | None:
    """Find acme.sh account directory for domain.

    ⚠️ TRAP[BUG] · 2026-07-23 · G3 · acme.sh account data path uses <domain>_ecc/
    · Observed: account data never uploaded because data/<domain>/ doesn't exist
    · Root: acme.sh stores account data in <domain>_ecc/ directory structure
    · Fix: try <domain>_ecc first (acme.sh default), fall back to data/<domain> (legacy)
    """
    ecc_path = os.path.join(acme_home, f"{domain}_ecc")
    if os.path.isdir(ecc_path):
        return ecc_path

    legacy_path = os.path.join(acme_home, "data", domain)
    if os.path.isdir(legacy_path):
        return legacy_path

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

    if len(sys.argv) < 2:
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
    elif command in ("upload", "download", "check"):
        if len(sys.argv) < 3:
            print(f"Usage: s3_ssl_cache.py {command} <domain>")
            sys.exit(1)
        domain = sys.argv[2]
        s3_bucket = os.environ.get("S3_BUCKET", "")
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
