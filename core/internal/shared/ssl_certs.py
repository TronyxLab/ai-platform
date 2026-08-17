#!/usr/bin/env python3
# GREP_SUMMARY: ssl-certs, openssl, x509, expiry, issuer, lets-encrypt, parseable, shared, checkend, san, wildcard
# STRUCTURE: ▶ cert_is_parseable ┌cert┐ → ◇ openssl x509 -noout → ⎋ bool → ▶ cert_check_expiry ┌cert,threshold┐ → ◇ openssl x509 -checkend → ⎋ bool
#            → ▶ cert_get_issuer ┌cert┐ → ◇ openssl x509 -issuer → ⎋ str|None → ▶ cert_is_le_issuer ┌cert┐ → ◇ issuer contains "Let's Encrypt" → ⎋ bool
#            → ▶ cert_get_san_list ┌cert┐ → ◇ openssl x509 -ext subjectAltName → ⊕ DNS-entries → ⎋ list[str]
#            → ▶ _cert_covers_domain ┌cert,domain┐ → ◇ SAN? (exact|wildcard одноуровневый) : CN-fallback → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Shared openssl x509 primitives for certificate validation — единый SoT openssl-проверок
##           (DevPlan 117 D21 + 118 C9). Заменяет дублирующиеся openssl subprocess-блоки в
##           s3_ssl_cache._validate_cert и cert_orchestrator._is_cert_valid.
##           DevPlan 118 C9: cert_is_valid() — ЕДИНАЯ комбинация «cert валиден» (parseable + LE +
##           domain match + expiry); s3_ssl_cache/cert_orchestrator/context_deployer делегируют в него.
## @scope    Импортируется s3_ssl_cache.py, cert_orchestrator.py и context_deployer.py (≥2 потребителя —
##            критерий shared/). Чистые функции без состояния: subprocess openssl + возврат bool/str/list.
## @invariants
##   - cert_check_expiry: openssl x509 -in <cert> -checkend <threshold> -noout → returncode==0 = не истёк
##   - cert_get_issuer: openssl x509 -in <cert> -issuer -noout → строка issuer (или None при ошибке)
##   - cert_get_subject: openssl x509 -in <cert> -subject -noout → строка subject (или None при ошибке)
##   - cert_is_le_issuer: case-sensitive "Let's Encrypt" в issuer
##   - cert_get_san_list: openssl x509 -in <cert> -noout -ext subjectAltName → список DNS-имён SAN
##     (префикс DNS: срезается регистронезависимо, trim, trailing dot убирается; IP:... игнорируются;
##     ошибки subprocess/timeout/rc!=0 → [] — никогда не raise)
##   - cert_is_valid (C9): parseable → LE → (domain if expected_domains) → (expiry if check_expiry);
##     expected_domains=None → без domain-check; check_expiry=False → без expiry-check
##   - Domain match (DevPlan 004 W1) — SAN primary / CN fallback (RFC 6125): SAN непуст → матч
##     ТОЛЬКО по SAN (exact или одноуровневый wildcard; *.example.com НЕ покрывает apex
##     example.com и глубже одного label); SAN пуст → CN-fallback (cert_subject_matches_domain).
##     SAN-ветка — case-insensitive; CN-ветка сохраняет прежнее поведение (подстрока, без lower)
##   - Non-fatal: никогда не raise — subprocess ошибки/timeout → False/None/[] (graceful degradation)
##   - DEFAULT_OPENSSL_TIMEOUT=10, DEFAULT_EXPIRY_THRESHOLD=2592000 (30 дней) — единственные
##     литералы в этом домене (DevPlan 117 D21)
## @rationale Две openssl-валидации (s3_ssl_cache.py:125-221, cert_orchestrator.py:271-320) дублировали
##            одни и те же примитивы (checkend/issuer) с разными хардкод-константами. Извлечение
##            в shared/ssl_certs.py устраняет дубль и централизует константы (U-14).
##            C9 (DevPlan 118): три реализации «валиден» (s3: parseable+LE+domain+expiry;
##            cert_orchestrator: expiry+LE; context_deployer: expiry+LE inline) → одна cert_is_valid().
##            DevPlan 004 W1: SAN-aware domain matching — современные LE-сертификаты SAN-only
##            (subject пуст), CN-only матчинг отвергал валидные серты из S3-кеша → false-miss →
##            пере-выпуск (риск LE rate-limit). Семантика wildcard — канон verify_sweep/tls_check.
## @changes  2026-08-01 | DevPlan 117 D21 — создан (дедупликация openssl-валидаций)
##           2026-08-02 | DevPlan 118 C9 — +cert_is_valid() единая комбинация; +cert_get_subject/
##                      cert_subject_matches_domain (domain-match из s3_ssl_cache)
##           2026-08-02 | DevPlan 119 D1 — +CLI-фасад main() (--is-le/--check-expiry, паттерн
##                      ssh_opts --shell): issue-cert.sh _is_le_cert/_acme_verify_cert удалены (AUDIT-1 F1/F2)
##           2026-08-16 | DevPlan 004 W1 — +SAN-aware domain matching (cert_get_san_list,
##                      _cert_covers_domain), RFC2253 CN patterns
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from dataclasses import dataclass
from typing import cast

logger = logging.getLogger(__name__)


# region DATACLASS_CliArgs
@dataclass
class _CliArgs:
    """Типизированная граница argparse.Namespace CLI (W11, DevPlan 170)."""

    is_le: str
    check_expiry: list[str]


# endregion DATACLASS_CliArgs

# ── Constants (единый источник для openssl-проверок, DevPlan 117 D21) ──
DEFAULT_OPENSSL_TIMEOUT: int = 10
"""## @invariant Timeout (sec) для каждого openssl subprocess-вызова (канон s3_ssl_cache)."""

DEFAULT_EXPIRY_THRESHOLD: int = 2592000  # 30 days in seconds
"""## @invariant Порог expiry в секундах — cert считается валидным при >30 днях до истечения (канон -checkend 2592000)."""


# region FUNC_cert_is_parseable
## @purpose  Verify a PEM cert is parseable by openssl (syntax integrity check).
## @io       ⇥ cert_path: str, timeout: int → ⎋ bool (True = parseable)
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Uses `openssl x509 -in <cert> -noout` (no output = parseable)
##   - Returns False on subprocess error/timeout (never raises)
def cert_is_parseable(cert_path: str, timeout: int = DEFAULT_OPENSSL_TIMEOUT) -> bool:
    """Check that cert_path is a parseable PEM certificate."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout"], capture_output=True, timeout=timeout, check=False
        )
        if result.returncode != 0:
            logger.info("[IMP:8][ssl_certs] Cert not parseable: %s", cert_path)
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("[IMP:7][ssl_certs] openssl parse check failed for %s: %s", cert_path, e)
        return False
    else:
        return True


# endregion FUNC_cert_is_parseable


# region FUNC_cert_check_expiry
## @purpose  Check a cert has more than threshold_seconds until expiration.
## @io       ⇥ cert_path: str, threshold_seconds: int, timeout: int → ⎋ bool (True = not expired within threshold)
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Uses `openssl x509 -in <cert> -checkend <threshold> -noout` — returncode 0 = >threshold remaining
##   - Returns False on subprocess error/timeout (never raises)
def cert_check_expiry(cert_path: str, threshold_seconds: int, timeout: int = DEFAULT_OPENSSL_TIMEOUT) -> bool:
    """Check that cert has more than threshold_seconds until expiry (openssl -checkend)."""
    try:
        result = subprocess.run(
            [
                "openssl",
                "x509",
                "-in",
                cert_path,
                "-checkend",
                str(threshold_seconds),
                "-noout",
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            logger.info(
                "[IMP:8][ssl_certs] Cert expires within %ds or is unparseable: %s",
                threshold_seconds,
                cert_path,
            )
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("[IMP:7][ssl_certs] openssl expiry check failed for %s: %s", cert_path, e)
        return False
    else:
        return True


# endregion FUNC_cert_check_expiry


# region FUNC_cert_get_issuer
## @purpose  Extract the issuer DN from a certificate.
## @io       ⇥ cert_path: str, timeout: int → ⎋ str | None (issuer string, or None on error)
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Uses `openssl x509 -in <cert> -issuer -noout`
##   - Returns None on subprocess error/timeout (never raises)
def cert_get_issuer(cert_path: str, timeout: int = DEFAULT_OPENSSL_TIMEOUT) -> str | None:
    """Extract issuer DN from a cert, or None on failure."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-issuer", "-noout"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            return None
        issuer = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("[IMP:7][ssl_certs] openssl issuer check failed for %s: %s", cert_path, e)
        return None
    else:
        return issuer or None


# endregion FUNC_cert_get_issuer


# region FUNC_cert_is_le_issuer
## @purpose  Check a cert was issued by Let's Encrypt (case-sensitive "Let's Encrypt" in issuer DN).
##           Надстройка над cert_get_issuer — единая LE-проверка.
## @io       ⇥ cert_path: str, timeout: int → ⎋ bool (True = LE issuer)
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Case-sensitive "Let's Encrypt" match (LE всегда пишет "Let's Encrypt")
##   - Returns False on subprocess error/timeout (never raises)
def cert_is_le_issuer(cert_path: str, timeout: int = DEFAULT_OPENSSL_TIMEOUT) -> bool:
    """Check that cert issuer contains 'Let's Encrypt'."""
    issuer = cert_get_issuer(cert_path, timeout=timeout)
    if issuer is None:
        return False
    is_le = "Let's Encrypt" in issuer
    if not is_le:
        logger.info("[IMP:7][ssl_certs] Cert issuer is not Let's Encrypt: %s", issuer[:120])
    return is_le


# endregion FUNC_cert_is_le_issuer


# region FUNC_cert_get_subject
## @purpose  Extract the subject DN from a certificate (openssl x509 -subject — primitive для
##           cert_is_valid domain-match, DevPlan 118 C9).
## @io       ⇥ cert_path: str, timeout: int → ⎋ str | None (subject string, or None on error)
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Uses `openssl x509 -in <cert> -subject -noout`
##   - Returns None on subprocess error/timeout (never raises)
def cert_get_subject(cert_path: str, timeout: int = DEFAULT_OPENSSL_TIMEOUT) -> str | None:
    """Extract subject DN from a cert, or None on failure."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-subject", "-noout"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            return None
        subject = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("[IMP:7][ssl_certs] openssl subject check failed for %s: %s", cert_path, e)
        return None
    else:
        return subject or None


# endregion FUNC_cert_get_subject


# region FUNC_cert_subject_matches_domain
## @purpose  Проверить, что subject cert'а содержит домен (CN = domain или CN = *.domain, wildcard).
##           Порт domain-match из s3_ssl_cache._validate_cert (DevPlan 118 C9) — S3-кеш специфичная
##           проверка консолидирована в shared.
## @io       ⇥ subject: str, domain: str → ⎋ bool
## @complexity — O(1) — подстрока-матчинг
## @invariants
##   - Матчит: "CN = example.com", "CN= example.com", "CN=*.example.com", "CN = *.example.com"
##     (slashed-формат openssl -subject) + "CN=example.com", "CN=*.example.com" (RFC2253 без
##     пробелов, DevPlan 004 W1) + trailing-dot варианты ("CN = example.com.", "CN=example.com.")
##   - Регистро-нечувствительность НЕ гарантируется (прежнее поведение s3_ssl_cache)
def cert_subject_matches_domain(subject: str, domain: str) -> bool:
    """Check that a cert subject CN covers the given domain (exact or wildcard, C9 + RFC2253 W1)."""
    return any(
        pattern in subject
        for pattern in (
            f"CN = {domain}",
            f"CN= {domain}",
            f"CN=*.{domain}",
            f"CN = *.{domain}",
            f"CN={domain}",
            f"CN = {domain}.",
            f"CN={domain}.",
        )
    )


# endregion FUNC_cert_subject_matches_domain


# region FUNC_cert_get_san_list
## @purpose  Extract DNS SAN entries from a certificate (DevPlan 004 W1 — SAN primary matching).
##           openssl x509 -ext subjectAltName → список DNS-имён; IP-записи (IP:...) игнорируются.
## @io       ⇥ cert_path: str, timeout: int → ⎋ list[str] (DNS SAN entries; [] on error/no SAN)
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Uses `openssl x509 -in <cert> -noout -ext subjectAltName`
##   - Multiline-парсинг (референс verify_sweep/tls_check._cert_san_matches): строки,
##     начинающиеся с DNS: (case-insensitive), split по запятой
##   - Нормализация: срез префикса DNS: (case-insensitive), trim, убрать trailing dot
##     ("example.com." → "example.com"); IP:... записи — игнорируются отдельной веткой (не DNS)
##   - Non-fatal: subprocess error/timeout/rc!=0 → [] (никогда не raise)
def cert_get_san_list(cert_path: str, timeout: int = DEFAULT_OPENSSL_TIMEOUT) -> list[str]:
    """Extract DNS SAN entries from cert, or [] on failure/no SAN (W1)."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-ext", "subjectAltName"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("[IMP:7][ssl_certs] openssl SAN check failed for %s: %s", cert_path, e)
        return []
    else:
        sans: list[str] = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line.upper().startswith("DNS:"):
                continue  # IP:... и прочие не-DNS записи — игнорируются отдельной веткой
            for raw_token in line.split(","):
                token = raw_token.strip()
                if not token.upper().startswith("DNS:"):
                    continue
                entry = token[4:].strip().rstrip(".")
                if entry:
                    sans.append(entry)
        return sans


# endregion FUNC_cert_get_san_list


# region FUNC__san_entry_covers
## @purpose  Проверить, покрывает ли ОДНА SAN-запись домен: точное совпадение ИЛИ wildcard
##           *.parent (ровно одна метка слева). Эквивалент семантики
##           verify_sweep/tls_check.san_matches_domain (канон одноуровневого wildcard),
##           внедрён локально — shared ниже по слоям, импорт из verify_sweep запрещён.
## @io       ⇥ san: str (нормализованная DNS-запись без "DNS:"), domain: str → ⎋ bool
## @complexity — O(N) где N = len(domain)
## @invariants
##   - Case-insensitive (lowercase с обеих сторон)
##   - Wildcard '*.example.com' матчит ровно один уровень: app.example.com ✅,
##     a.b.example.com ❌, example.com ❌ (apex — отдельная запись)
##   - '*' не в первом сегменте (foo.*.com) → False; пустые san/domain → False
def _san_entry_covers(san: str, domain: str) -> bool:
    """Check that a single SAN entry covers domain (exact or one-level wildcard, W1)."""
    san_l = san.strip().lower()
    domain_l = domain.strip().lower()
    if not san_l or not domain_l:
        return False
    if san_l == domain_l:
        return True
    if san_l.startswith("*."):
        wildcard_suffix = san_l[1:]  # ".example.com"
        if domain_l.endswith(wildcard_suffix):
            prefix = domain_l[: -len(wildcard_suffix)]
            if prefix and "." not in prefix:
                return True
    return False


# endregion FUNC__san_entry_covers


# region FUNC__cert_covers_domain
## @purpose  SAN-aware domain matching (DevPlan 004 W1): SAN primary / CN fallback (RFC 6125).
##           SAN непуст → матч ТОЛЬКО по SAN (без CN-fallback); SAN пуст → CN через
##           cert_get_subject + cert_subject_matches_domain (прежняя семантика).
## @io       ⇥ cert_path: str, domain: str, timeout: int → ⎋ bool
## @complexity — O(1) + 1-2 openssl subprocess
## @invariants
##   - SAN непуст И ни одна запись не совпала → False БЕЗ fallback на CN
##   - SAN-ветка — case-insensitive; CN-ветка — прежнее поведение (подстрока)
##   - Non-fatal: subprocess ошибки → [] SAN → CN-fallback ветка (никогда не raise)
def _cert_covers_domain(cert_path: str, domain: str, timeout: int = DEFAULT_OPENSSL_TIMEOUT) -> bool:
    """Check cert covers domain: SAN primary (exact|wildcard), CN fallback only if SAN empty (W1)."""
    san_list = cert_get_san_list(cert_path, timeout=timeout)
    if san_list:
        # 🧐 TRAP[DECISION] · 2026-08-16 · — · SAN present → CN non-authoritative (RFC 6125 / CA-B Forum) · Rejected: CN-fallback всегда — держит баг-класс «CN совпал случайно» · Reason: SAN presence deprecates CN matching · Rev: появление legacy-сертификатов с рассинхроном CN/SAN
        return any(_san_entry_covers(san, domain) for san in san_list)
    subject = cert_get_subject(cert_path, timeout=timeout)
    if subject is None:
        return False
    return cert_subject_matches_domain(subject, domain)


# endregion FUNC__cert_covers_domain


# region FUNC_cert_is_valid
## @purpose  Единая комбинация «сертификат валиден» (DevPlan 118 C9): parseable + LE issuer +
##           (domain match — если expected_domains задан) + (expiry — если check_expiry).
##           Дедупликация трёх реализаций: s3_ssl_cache._validate_cert (parseable+LE+domain+expiry),
##           cert_orchestrator._is_cert_valid (expiry+LE), context_deployer (expiry+LE inline).
##           Domain match — SAN primary / CN fallback (DevPlan 004 W1, _cert_covers_domain).
## @io       ⇥ cert_path: str; threshold: int (сек до истечения, default 30 дней);
##              expected_domains: str | list[str] | None; check_expiry: bool; timeout: int
##           ⎋ bool (True = валиден)
## @complexity — O(1) + 2-4 openssl subprocess
## @invariants
##   - Порядок: parseable → LE issuer → (domain match: SAN primary, CN fallback) → (expiry)
##     — fail-fast на первом False
##   - expected_domains=None → domain-check пропускается (cert_orchestrator/context_deployer семантика)
##   - check_expiry=False → expiry-check пропускается (s3_ssl_cache download-семантика)
##   - Non-fatal: никогда не raise — subprocess ошибки → False (graceful degradation)
def cert_is_valid(
    cert_path: str,
    threshold: int = DEFAULT_EXPIRY_THRESHOLD,
    expected_domains: str | list[str] | None = None,
    check_expiry: bool = True,
    timeout: int = DEFAULT_OPENSSL_TIMEOUT,
) -> bool:
    """Check cert is valid: parseable + LE issuer + optional domain match (SAN primary, CN fallback) + optional expiry (C9+W1)."""
    # 1. Parseable (openssl x509 -noout)
    if not cert_is_parseable(cert_path, timeout=timeout):
        return False

    # 2. LE issuer (reject mkcert/self-signed — P0 TRAP[BUG] 2026-07-22)
    if not cert_is_le_issuer(cert_path, timeout=timeout):
        logger.info("[IMP:8][ssl_certs] cert_is_valid: not Let's Encrypt issuer — invalid: %s", cert_path)
        return False

    # 3. Domain match (опционально — expected_domains; SAN primary / CN fallback, W1)
    domains = [expected_domains] if isinstance(expected_domains, str) else (expected_domains or [])
    if domains and not any(_cert_covers_domain(cert_path, d, timeout=timeout) for d in domains):
        san_list = cert_get_san_list(cert_path, timeout=timeout)
        logger.info(
            "[IMP:8][ssl_certs] cert_is_valid: SAN/CN does not match domains %s: SAN=%r: %s",
            domains,
            san_list,
            cert_path,
        )
        return False

    # 4. Expiry (>threshold до истечения, опционально — check_expiry)
    if check_expiry and not cert_check_expiry(cert_path, threshold, timeout=timeout):
        logger.info("[IMP:8][ssl_certs] cert_is_valid: expires within %ds or unparseable: %s", threshold, cert_path)
        return False

    logger.info("[IMP:9][ssl_certs] cert_is_valid: OK (%s)", cert_path)
    return True


# endregion FUNC_cert_is_valid


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI facade для shell-фасадов (DevPlan 119 D1 — паттерн ssh_opts --shell).

    ▶ ┌argv┐ → ◇ --is-le CERT? → cert_is_le_issuer → ⎋ exit 0/1 │ ◇ --check-expiry CERT DAYS?
      → cert_check_expiry(cert, DAYS*86400) → ⎋ exit 0/1

    ## @purpose — Интерфейс для issue-cert.sh: `python3 -m core.internal.shared.ssl_certs --is-le <cert>`
    ##            / `--check-expiry <cert> <days>` (паттерн ssh_opts --shell, D1).
    ## @io       ⇥ argv: list[str] | None → ⎋ int (0=LE/valid, 1=not, 2=usage error)
    ## @complexity O(1) + 1 openssl subprocess
    ## @invariants
    ##   - --is-le CERT: exit 0 iff cert_is_le_issuer(CERT) (non-fatal: missing/unreadable → exit 1)
    ##   - --check-expiry CERT DAYS: exit 0 iff cert_check_expiry(CERT, DAYS*86400)
    ##   - Никогда не raise — subprocess ошибки → exit 1 (graceful degradation, канон ssl_certs)
    ##   - Без аргументов — usage error exit 2 (fail-fast)
    """
    parser = argparse.ArgumentParser(description="ssl_certs — единый SoT openssl x509-проверок (D1)")
    parser.add_argument("--is-le", metavar="CERT", help="Exit 0 if cert issued by Let's Encrypt (exit 1 otherwise)")
    parser.add_argument(
        "--check-expiry",
        nargs=2,
        metavar=("CERT", "DAYS"),
        help="Exit 0 if cert remains valid > DAYS days (exit 1 otherwise)",
    )
    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))

    if args.is_le:
        if cert_is_le_issuer(args.is_le):
            logger.info("[IMP:9][ssl_certs][cli] --is-le: LE issuer OK: %s", args.is_le)
            return 0
        logger.info("[IMP:8][ssl_certs][cli] --is-le: not Let's Encrypt: %s", args.is_le)
        return 1

    if args.check_expiry:
        cert_path, days = args.check_expiry
        if cert_check_expiry(cert_path, int(days) * 86400):
            logger.info("[IMP:9][ssl_certs][cli] --check-expiry: OK (>%s days): %s", days, cert_path)
            return 0
        logger.info("[IMP:8][ssl_certs][cli] --check-expiry: expires within %s days: %s", days, cert_path)
        return 1

    parser.error("No action specified — use --is-le CERT or --check-expiry CERT DAYS")
    return 2  # unreachable (parser.error exits)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
