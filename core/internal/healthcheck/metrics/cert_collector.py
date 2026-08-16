#!/usr/bin/env python3
# GREP_SUMMARY: cert-collector ssl certificate cryptography.x509 wildcard san letsencrypt node.yaml
# STRUCTURE: ▶ get_certs(node_yaml) → domains[] → /etc/letsencrypt/live/<d>/fullchain.pem → cryptography.x509 parse
#            → SAN match (exact|wildcard) → certs[{cert_id, domains[], issuer, not_after_iso, ...}] → ⎋ list[dict]
# region MODULE_CONTRACT
## @purpose  SSL certificate collector via cryptography.x509 — NO subprocess openssl
## @scope    Host-side: reads node.yaml for domain list, reads Let's Encrypt PEM files from filesystem
## @invariants
##   - cryptography.x509 only, NO subprocess openssl (META Δ7)
##   - Wildcard support: exact match OR *.domain SAN match, not PLATFORM_DOMAIN guessing (Δ18)
##   - Dates in ISO 8601 (not locale-dependent openssl output)
##   - Graceful: missing cert file → warning in errors[], not crash
##   - Deduplication by cert_id (sha256 of fullchain path)
## @rationale Pure Python parsing avoids locale-dependent date parsing and subprocess overhead.
##            SAN-based matching handles wildcard certificates correctly (Δ16, Δ18).
##            cryptography library already on the system for certbot.
# endregion MODULE_CONTRACT

import hashlib
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import TypedDict, cast

from cryptography import x509
from cryptography.x509.oid import NameOID

# DevPlan 118 C7: /etc/letsencrypt/live — единый резолвер shared/deploy_paths.letsencrypt_live().
from core.internal.shared.deploy_paths import letsencrypt_live
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError
from core.internal.shared.node_yaml import NodeYaml

logger = logging.getLogger(__name__)

_LETSENCRYPT_LIVE = str(letsencrypt_live())


# region DATA_CertInfo
class CertInfo(TypedDict, total=False):
    """Разобранный сертификат (граница status-metrics.json).

    ## @purpose  Единица вывода cert_collector: cert_id (sha256 пути), issuer/subject,
    ##            not_after_iso, days_remaining, san[], source_path + domains[]
    ##            (добавляется в get_certs при дедупликации).
    """

    cert_id: str
    issuer: str
    subject: str
    not_after_iso: str
    days_remaining: int
    san: list[str]
    source_path: str
    domains: list[str]


# endregion DATA_CertInfo


# region FUNC__san_match
## @purpose  Check if a certificate SAN list covers a given domain (exact or wildcard match)
## @io       ⇥ cert_san: list[str] — SAN entries from cert (e.g. ['example.com', '*.example.com'])
##           ⇥ domain: str — domain to match (e.g. 'sub.example.com')
##           ⎋ bool — True if domain is covered
## @complexity  O(N) where N = len(cert_san)
def _san_match(cert_san: list[str], domain: str) -> bool:
    """Check if domain is covered by certificate SAN entries (exact or wildcard).

    # ▶ ┌cert_san[] + domain┐ → ◇ exact match? → True | ◇ wildcard *.suffix match? → True | ⎋ False

    Wildcard: *.example.com matches sub.example.com but NOT example.com itself.
    """
    domain = domain.lower().strip(".")
    for san_entry in cert_san:
        san = san_entry.lower().strip(".")
        # Exact match
        if san == domain:
            return True
        # Wildcard match: *.suffix → check domain ends with .suffix AND has exactly one subdomain level
        if san.startswith("*."):
            suffix = san[2:]  # Remove "*."
            if domain.endswith("." + suffix):
                # Count subdomain levels — must be exactly one level
                remaining = domain[: -len(suffix) - 1] if domain.endswith("." + suffix) else domain
                if remaining and "." not in remaining:
                    return True
    return False


# endregion FUNC__san_match


# region FUNC__load_cert
## @purpose  Load and parse a PEM certificate file via cryptography.x509
## @io       ⇥ path: str — absolute path to fullchain.pem
##           ⎋ dict | None — parsed cert data or None on failure
## @complexity  O(1) — single file read + single x509 parse
def _load_cert(path: str) -> CertInfo | None:
    """Load a PEM certificate from path and return parsed dict.

    # ▶ ┌path┐ → open PEM → cryptography.x509.load_pem → extract issuer, subject, not_after, SAN → ⎋ dict

    Returns dict with: cert_id, issuer, subject, not_after_iso, days_remaining, san, source_path.
    Returns None on parse error.
    """
    logger_ = logging.getLogger(__name__)
    try:
        with pathlib.Path(path).open("rb") as f:
            pem_data = f.read()
    except (OSError, PermissionError) as exc:
        logger_.warning("[IMP:8][cert_collector][_load_cert] Cannot read cert file %s: %s", path, exc)
        return None

    try:
        cert = x509.load_pem_x509_certificate(pem_data)
    except (ValueError, TypeError, OSError) as exc:
        logger_.warning("[IMP:8][cert_collector][_load_cert] Failed to parse cert %s: %s", path, exc)
        return None

    # cert_id: sha256 of fullchain path (stable identifier — same cert always same ID)
    cert_id = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]

    # Issuer
    try:
        issuer = cert.issuer.rfc4514_string() if cert.issuer else ""
    except (ValueError, AttributeError, TypeError):
        # Some certs have non-standard issuer fields
        issuer_attrs = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
        issuer = cast("str", issuer_attrs[0].value) if issuer_attrs else "unknown"

    # Subject
    try:
        subject = cert.subject.rfc4514_string() if cert.subject else ""
    except (ValueError, AttributeError, TypeError):
        subject_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        subject = cast("str", subject_attrs[0].value) if subject_attrs else "unknown"

    # Not After (ISO 8601)
    not_after = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after
    if isinstance(not_after, datetime):
        # Normalise offset-naive → offset-aware UTC
        # cryptography < 41.0.0 returns naive datetime (always UTC, just without tzinfo)
        if not_after.tzinfo is None:
            not_after = not_after.replace(tzinfo=timezone.utc)
        not_after_iso = not_after.strftime("%Y-%m-%dT%H:%M:%SZ")
        days_remaining = (not_after - datetime.now(timezone.utc)).days
    else:
        not_after_iso = str(not_after)
        days_remaining = 0

    # SAN entries
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        san_entries = list(san_ext.value.get_values_for_type(x509.DNSName)) if san_ext.value else []
    except x509.ExtensionNotFound:
        san_entries = []

    result: CertInfo = {
        "cert_id": cert_id,
        "issuer": issuer,
        "subject": subject,
        "not_after_iso": not_after_iso,
        "days_remaining": days_remaining,
        "san": san_entries,
        "source_path": path,
    }

    logger_.info(
        "[IMP:9][cert_collector][_load_cert] Loaded cert: %s (issuer=%s, expires=%s, %d SANs)",
        pathlib.Path(path).name,
        issuer,
        not_after_iso,
        len(san_entries),
    )
    return result


# endregion FUNC__load_cert


# region FUNC_get_certs
## @purpose  Collect SSL certificates for all domains from node.yaml
## @io       ⇥ node_yaml_path: str — path to node.yaml
##           ⎋ list[dict] — parsed certificates with SAN match info
## @complexity  O(D * L) where D = node domains, L = Let's Encrypt live dirs
def get_certs(node_yaml_path: str) -> list[CertInfo]:
    """Collect SSL certificates for domains from node.yaml using cryptography.x509.

    # ▶ ┌node.yaml┐ → domains[] → try /etc/letsencrypt/live/<d>/fullchain.pem
    #    → if not found: search all live/*/fullchain.pem, SAN match
    #    → deduplicate by cert_id → ⊕ certs[] → ⎋ list[dict]

    Steps:
    1. Load node.yaml → extract expose:true domains
    2. For each domain: try /etc/letsencrypt/live/<domain>/fullchain.pem
    3. If not found: search all live/*/fullchain.pem for SAN matching the domain
    4. Deduplicate: same cert_id → merge domains[]
    5. Graceful failure: missing files → warning log, not crash

    Returns list of cert dicts with keys:
    cert_id, domains[], issuer, subject, not_after_iso, days_remaining, san[], source_path.
    """
    logger_ = logging.getLogger(__name__)
    logger_.info("[IMP:8][cert_collector][get_certs] Starting certificate collection")

    # Step 1: Load node.yaml
    try:
        node = NodeYaml(node_yaml_path)
        projects = node.get_projects()
    except (ConfigNotFoundError, ConfigParseError, OSError) as exc:
        logger_.warning("[IMP:8][cert_collector][get_certs] Failed to load node.yaml %s: %s", node_yaml_path, exc)
        return []

    # Extract domains from projects
    domains: list[str] = []
    for p in projects:
        if isinstance(p, dict):
            domain = str(p.get("domain", ""))
            expose = p.get("expose", False)
            if domain and expose is True:
                domains.append(domain)

    if not domains:
        logger_.info("[IMP:8][cert_collector][get_certs] No exposed domains in node.yaml")
        return []

    logger_.info("[IMP:8][cert_collector][get_certs] Found %d domain(s) to check", len(domains))

    # Step 2: Try direct path for each domain
    cert_map: dict[str, CertInfo] = {}  # cert_id → cert
    for domain in domains:
        cert_path = os.path.join(_LETSENCRYPT_LIVE, domain, "fullchain.pem")
        if os.path.isfile(cert_path):
            cert_data = _load_cert(cert_path)
            if cert_data:
                _merge_domain(cert_map, cert_data, domain)
                continue

        # Step 3: Domain not found at direct path — search all live certs
        logger_.info("[IMP:8][cert_collector][get_certs] No direct cert for %s — searching live/", domain)
        domain_cert = _search_wildcard_cert(domain)
        if domain_cert:
            _merge_domain(cert_map, domain_cert, domain)
        else:
            logger_.warning("[IMP:8][cert_collector][get_certs] No cert found for domain %s", domain)

    logger_.info(
        "[IMP:9][cert_collector][get_certs] Collected %d unique certificate(s) for %d domain(s)",
        len(cert_map),
        len(domains),
    )
    return list(cert_map.values())


# endregion FUNC_get_certs


# region FUNC__merge_domain
## @purpose  Дедупликация по cert_id: добавление домена к существующему серту или регистрация нового.
## @io       ⇥ cert_map: dict[str, CertInfo], cert: CertInfo, domain: str → ⎋ None
## @complexity  O(1) — dict-операции
## @changes  2026-08-15 | DevPlan 170 W11 — извлечение merge-логики (TypedDict-типизация)
def _merge_domain(cert_map: dict[str, CertInfo], cert: CertInfo, domain: str) -> None:
    """Merge domain into cert_map by cert_id (dedup; W11: typed access вместо голых dict)."""
    cid = cast("str", cert.get("cert_id"))  # W11: CertInfo total=False — cert_id всегда задан _load_cert
    existing = cert_map.get(cid)
    if existing is not None:
        domains_list = existing.get("domains") or []
        if domain not in domains_list:
            existing["domains"] = [*domains_list, domain]
    else:
        cert["domains"] = [domain]
        cert_map[cid] = cert


# endregion FUNC__merge_domain


# region FUNC__search_wildcard_cert
## @purpose  Search all Let's Encrypt live certs for a SAN matching the given domain
## @io       ⇥ domain: str — domain to match
##           ⎋ dict | None — cert dict if found, None otherwise
## @complexity  O(N * S) where N = live dirs, S = avg SAN count per cert
def _search_wildcard_cert(domain: str) -> CertInfo | None:
    """Search all /etc/letsencrypt/live/*/fullchain.pem for a SAN matching the domain.

    Iterates all live directories, loads each cert, checks SAN match.
    Returns the first matching cert (most specific match wins via cert with fewest SANs).
    """
    logger_ = logging.getLogger(__name__)

    if not os.path.isdir(_LETSENCRYPT_LIVE):
        logger_.warning(
            "[IMP:8][cert_collector][_search_wildcard] Let's Encrypt live dir not found: %s", _LETSENCRYPT_LIVE
        )
        return None

    candidates: list[CertInfo] = []
    # ruff: ignore[PLW0717] — внутри try есть break/continue/await/yield — извлечение ломает управляющий поток
    try:
        for entry in sorted(os.listdir(_LETSENCRYPT_LIVE)):
            live_dir = os.path.join(_LETSENCRYPT_LIVE, entry)
            if not os.path.isdir(live_dir):
                continue
            cert_path = os.path.join(live_dir, "fullchain.pem")
            if not os.path.isfile(cert_path):
                continue

            cert_data = _load_cert(cert_path)
            if cert_data and _san_match(cert_data.get("san", []), domain):
                candidates.append(cert_data)
    except OSError as exc:
        logger_.warning("[IMP:8][cert_collector][_search_wildcard] Cannot list live dir: %s", exc)
        return None

    if not candidates:
        return None

    # Prefer most specific cert (fewest SANs = best match)
    candidates.sort(key=lambda c: len(c.get("san", [])))
    best = candidates[0]
    logger_.info(
        "[IMP:9][cert_collector][_search_wildcard] Wildcard match for %s → cert %s (%d SANs)",
        domain,
        best.get("source_path", ""),
        len(best.get("san", [])),
    )
    return best


# endregion FUNC__search_wildcard_cert


if __name__ == "__main__":
    # Quick manual test
    logging.basicConfig(level=logging.INFO)
    test_path = os.environ.get("NODE_YAML_PATH", "/opt/node-configs/test-node/node.yaml")
    certs = get_certs(test_path)
    print(f"Certs found: {len(certs)}")
    for c in certs:
        print(
            f"  {c.get('cert_id')}: domains={c.get('domains')} issuer={c.get('issuer')} "
            f"expires={c.get('not_after_iso')} days={c.get('days_remaining')}"
        )
