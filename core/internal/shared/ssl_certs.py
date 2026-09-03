#!/usr/bin/env python3
# GREP_SUMMARY: ssl-certs, openssl, x509, expiry, issuer, lets-encrypt, parseable, shared, checkend, san, wildcard, on-disk-coverage, wildcard-parent, wildcard-covers, le-live, run-openssl, pubkey-match, pair-match, fqdn-validation
# STRUCTURE: ▶ _run_openssl ┌args,cert,timeout,op┐ → ◇ openssl x509 -in cert … → ⎋ CompletedProcess|None (5 блоков дедуплицированы)
#            → ▶ cert_is_parseable ┌cert┐ → ◇ openssl x509 -noout → ⎋ bool → ▶ cert_check_expiry ┌cert,threshold┐ → ◇ openssl x509 -checkend → ⎋ bool
#            → ▶ cert_get_issuer ┌cert┐ → ◇ openssl x509 -issuer → ⎋ str|None → ▶ cert_is_le_issuer ┌cert┐ → ◇ issuer contains "Let's Encrypt" → ⎋ bool
#            → ▶ cert_get_san_list ┌cert┐ → ◇ openssl x509 -ext subjectAltName → ⊕ DNS-entries → ⎋ list[str]
#            → ▶ _cert_covers_domain ┌cert,domain┐ → ◇ SAN? (exact|wildcard одноуровневый) : CN-fallback → ⎋ bool
#            → ▶ cert_covers_domain ┌le_live,domain┐ → ◇ direct live/{domain} | wildcard live/{parent} (CN=*.parent) → ⎋ bool [F14]
#            → ▶ cert_key_pair_matches ┌cert,key┐ → ◇ openssl pubkey(cert) == pkey pubout(key) (whitespace-normalized) → ⎋ bool [REF-0008]
#            → ▶ validate_cert_domain_fqdn ┌fqdn┐ → ◇ labels(≥2,RFC,TLD) → ⊕ ConfigValidationError | ⎋ None [REF-0008]
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
##   - cert_is_valid (C9): parseable → LE → (pair-match if key_path) → (domain if expected_domains)
##     → (expiry if check_expiry); expected_domains=None → без domain-check; check_expiry=False
##     → без expiry-check; key_path=None → без pair-check (REF-0008: пара cert+key обязательна
##     в restore-путях — несогласованная пара «valid on disk» = nginx outage при здоровой системе)
##   - cert_key_pair_matches (REF-0008): openssl x509 -pubkey vs openssl pkey -pubout,
##     whitespace-normalized сравнение; subprocess ошибки → False (никогда не raise)
##   - validate_cert_domain_fqdn (REF-0008): fqdn ≥2 labels, каждый — RFC DNS label
##     ([a-z0-9] start/end, ≤63), TLD [a-z]{2,}; violation → ConfigValidationError (fail-fast;
##     закрывает path-traversal/RCE через needs.domain в cert pipeline, SEC-0026)
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
##           2026-08-22 | T2.4 дедупликация — +_run_openssl(): 5 повторяющихся subprocess openssl
##                      блоков (parseable/expiry/issuer/subject/san) → единый приватный хелпер;
##                      публичные сигнатуры и возвращаемые значения сохранены 1:1
##           2026-08-24 | REF-0008 (meta-refactoring В2) — +cert_key_pair_matches (pubkey-match
##                      cert↔key), +key_path в cert_is_valid (pair-check), +validate_cert_domain_fqdn
##                      (fail-fast FQDN-валидатор для cert-pipeline; дублирует правила
##                      vhost_renderer.validate_vhost_identifiers — см. TRAP[DECISION] у функции)
##           2026-09-02 | DevPlan 030 TASK-1 — +cert_covers_domain (on-disk coverage direct|wildcard-parent, F14)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from core.internal.shared.exceptions import ConfigValidationError

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


# region FUNC__run_openssl
## @purpose  Единый openssl x509 subprocess-хелпер (T2.4 дедупликация): 5 повторяющихся блоков
##           subprocess.run(["openssl", "x509", "-in", CERT, ...]) в cert_is_parseable/
##           cert_check_expiry/cert_get_issuer/cert_get_subject/cert_get_san_list сведены
##           к одному приватному хелперу. Subprocess-ошибки (timeout/FileNotFoundError/OSError)
##           логируются здесь ОДИН раз (IMP:7) и возвращают None — публичные функции трактуют
##           None как graceful degradation (False/None/[]), НИКОГДА не raise (канон ssl_certs).
## @io       ⇥ args: list[str] (флаги после openssl x509 -in CERT), cert_path: str,
##              timeout: int, op: str (метка операции для лога: parse|expiry|issuer|subject|SAN)
##           ⎋ subprocess.CompletedProcess[str] | None (None = subprocess error/timeout)
## @complexity O(1) + 1 openssl subprocess
## @invariants
##   - Всегда text=True (stdout нужен issuer/subject/san; parseable/expiry — stdout пуст)
##   - check=False: returncode проверяет вызывающий (не raise на rc!=0)
##   - Subprocess error → IMP:7 warning openssl OP check failed + None (никогда не raise)
def _run_openssl(args: list[str], cert_path: str, timeout: int, *, op: str) -> subprocess.CompletedProcess[str] | None:
    """Run openssl x509 -in CERT ARGS with единым error-handling; None on subprocess error."""
    try:
        return subprocess.run(
            ["openssl", "x509", "-in", cert_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning("[IMP:7][ssl_certs] openssl %s check failed for %s: %s", op, cert_path, e)
        return None


# endregion FUNC__run_openssl


# region FUNC_cert_is_parseable
## @purpose  Verify a PEM cert is parseable by openssl (syntax integrity check).
## @io       ⇥ cert_path: str, timeout: int → ⎋ bool (True = parseable)
## @complexity — O(1) + openssl subprocess
## @invariants
##   - Uses `openssl x509 -in <cert> -noout` (no output = parseable)
##   - Returns False on subprocess error/timeout (never raises)
def cert_is_parseable(cert_path: str, timeout: int = DEFAULT_OPENSSL_TIMEOUT) -> bool:
    """Check that cert_path is a parseable PEM certificate."""
    result = _run_openssl(["-noout"], cert_path, timeout, op="parse")
    if result is None:
        return False  # subprocess error уже залогирован в _run_openssl (IMP:7)
    if result.returncode != 0:
        logger.info("[IMP:8][ssl_certs] Cert not parseable: %s", cert_path)
        return False
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
    result = _run_openssl(["-checkend", str(threshold_seconds), "-noout"], cert_path, timeout, op="expiry")
    if result is None:
        return False  # subprocess error уже залогирован в _run_openssl (IMP:7)
    if result.returncode != 0:
        logger.info(
            "[IMP:8][ssl_certs] Cert expires within %ds or is unparseable: %s",
            threshold_seconds,
            cert_path,
        )
        return False
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
    result = _run_openssl(["-issuer", "-noout"], cert_path, timeout, op="issuer")
    if result is None or result.returncode != 0:
        return None
    issuer = result.stdout.strip()
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
    result = _run_openssl(["-subject", "-noout"], cert_path, timeout, op="subject")
    if result is None or result.returncode != 0:
        return None
    subject = result.stdout.strip()
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


# region FUNC_cert_covers_domain
## @purpose  Проверить on-disk покрытие домена сертификатом Let's Encrypt (DevPlan 030 TASK-1,
##           F14): direct-каталог live/{domain}/fullchain.pem ИЛИ wildcard-родитель
##           live/{parent}/fullchain.pem, где серт покрывает домен по SAN (primary) или CN
##           (fallback) через единый канон _cert_covers_domain. Раньше assertion (a) проверял
##           только direct-каталог (isfile) → ложный FAIL «no cert on disk» для wildcard
##           *.asiteam.ru → roadmap.asiteam.ru (серт реально на диске).
##           ⚠️ F14-факт: wildcard-серт acme.sh имеет CN=apex (asiteam.ru) + SAN=*.asiteam.ru —
##           матчинг ОБЯЗАН быть SAN-aware (_cert_covers_domain); CN-only не видит wildcard в SAN.
## @io       ⇥ le_live: Path (резолв deploy_paths.letsencrypt_live()), domain: str → ⎋ bool
## @complexity O(A) — A = число родительских суффиксов (≤2 openssl SAN+CN проверок)
## @invariants
##   - direct: live/{domain}/fullchain.pem существует И _cert_covers_domain(cert, domain)
##   - wildcard: для i in range(1, len(labels)-1): parent = labels[i:]; live/{parent}/
##     fullchain.pem существует И _cert_covers_domain(cert, "*.{parent}") (SAN primary / CN fallback;
##     ТОЛЬКО настоящий wildcard — direct-серт родителя НЕ проходит, B12 TRAP[BUG])
##   - Отсутствие ЛЮБОГО покрытия (direct И wildcard) → False (fail-closed, R3)
##   - Non-fatal: never raise — isfile/SAN/CN-ошибки → False (graceful degradation)
def cert_covers_domain(le_live: Path, domain: str) -> bool:
    """Check domain is covered by an on-disk LE cert (direct or wildcard parent, F14)."""
    # 1. Direct: сертификат самого домена live/{domain}/fullchain.pem (SAN primary / CN fallback)
    direct = le_live / domain / "fullchain.pem"
    if direct.is_file() and _cert_covers_domain(str(direct), domain):
        return True

    # 2. Wildcard: *.parent покрывает поддомен (только для subdomains — parent != domain)
    return cert_wildcard_covers_domain(le_live, domain)


# endregion FUNC_cert_covers_domain


# region FUNC_cert_wildcard_covers_domain
## @purpose  Проверить on-disk WILDCARD-PARENT покрытие домена (DevPlan 031 T4 / F5):
##           walk родительских суффиксов домена (subdomain → apex), live/{parent}/fullchain.pem
##           существует И покрывает "*.{parent}" (SAN primary / CN fallback через _cert_covers_domain).
##           НЕ включает direct-покрытие (live/{domain}/) — в отличие от cert_covers_domain.
##           Выделен из cert_covers_domain (DRY): F5-скрипт оркестрации сертификатов обязан
##           отличать «покрыт валидным wildcard-родителем» (выпуск direct-серта НЕ нужен —
##           vhost серверит wildcard) от «на диске лежит битый direct self-signed» (выпуск НУЖЕН).
##           Прямое использование cert_covers_domain для skip было бы неверно: direct-ветка
##           матчит по subject и вернула бы True для invalid self-signed CN=domain, замаскировав
##           сломанный direct-серт (регрессия выпуска).
## @io       ⇥ le_live: Path (letsencrypt_live()), domain: str → ⎋ bool
## @complexity O(A) — A = число родительских суффиксов (≤2 openssl SAN+CN проверок)
## @invariants
##   - Только subdomains: parent != domain (range(1, len(labels)-1) — apex не имеет родителя)
##   - ТОЛЬКО настоящий wildcard: direct-серт родителя НЕ проходит (B12 TRAP[BUG] —
##     _cert_covers_domain(cert, "*.{parent}") требует wildcard-SAN/CN)
##   - Отсутствие покрытия → False (fail-closed); never raise (isfile/SAN/CN-ошибки → False)
## @rationale Q: Почему отдельная функция, а не флаг в cert_covers_domain?
##            A: у двух потребителей РАЗНАЯ семантика: domains.py (F14, on-disk convergence) —
##            «любое покрытие достаточно»; cert_orchestrator (F5, skip выпуска) — «только
##            wildcard-родитель отменяет direct-выпуск». Общий флаг размыл бы оба контракта.
def cert_wildcard_covers_domain(le_live: Path, domain: str) -> bool:
    """Check an on-disk LE WILDCARD PARENT covers the domain (no direct check, F5)."""
    labels = domain.split(".")
    for i in range(1, len(labels) - 1):
        parent = ".".join(labels[i:])
        wildcard_path = le_live / parent / "fullchain.pem"
        if wildcard_path.is_file() and _cert_covers_domain(str(wildcard_path), f"*.{parent}"):
            return True
    return False


# endregion FUNC_cert_wildcard_covers_domain


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
    result = _run_openssl(["-noout", "-ext", "subjectAltName"], cert_path, timeout, op="SAN")
    if result is None or result.returncode != 0:
        return []
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


# region FUNC_san_list_covers
def san_list_covers(cert_san: list[str], domain: str) -> bool:
    """Покрывает ли SAN-список сертификата домен (exact или one-level wildcard).

    ## @purpose  Публичный list-level контракт поверх _san_entry_covers (AI-0066,
    ##            DevPlan 17 T5.2): единая точка SAN-матчинга — cert_collector
    ##            переиспользует канон вместо третьей локальной копии.
    ## @io       ⇥ cert_san: list[str] (например ['example.com', '*.example.com']),
    ##              domain: str → ⎋ bool
    ## @complexity O(N) где N = len(cert_san)
    ## @invariants
    ##   - '*.example.com' матчит sub.example.com, НЕ example.com и НЕ a.b.example.com
    ##   - Trailing-dot FQDN ('example.com.') нормализуется на ОБОИХ сторонах
    ##     (прежнее поведение cert_collector._san_match — AI-0066 миграция без
    ##     изменения семантики; _san_entry_covers ожидает уже нормализованные записи)
    """
    domain_n = domain.lower().strip(".")
    return any(_san_entry_covers(san.lower().strip("."), domain_n) for san in cert_san)


# endregion FUNC_san_list_covers


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


# region FUNC_cert_key_pair_matches
## @purpose  Проверить криптографическое соответствие пары cert+key (REF-0008 подпункт 1/2):
##           публичный ключ сертификата (openssl x509 -pubkey) == публичный ключ приватного
##           ключа (openssl pkey -pubout). Несогласованная пара на диске = nginx падает
##           (SSL_CTX_use_PrivateKey_file mismatch) при «здоровой» системе — класс FAIL-0300.
## @io       ⇥ cert_path: str, key_path: str, timeout: int → ⎋ bool (True = пара согласована)
## @complexity O(1) + 2 openssl subprocess
## @invariants
##   - Сравнение PEM-pubkey строк после удаления ВСЕГО whitespace (переносы строк openssl
##     детерминированы, но нормализация делает матчинг устойчивым к 64-col wrapping разницам)
##   - Non-fatal: subprocess error/timeout/rc!=0 → False + IMP:7 WARN (никогда не raise;
##     канон ssl_certs graceful degradation)
def cert_key_pair_matches(cert_path: str, key_path: str, timeout: int = DEFAULT_OPENSSL_TIMEOUT) -> bool:
    """Check that the private key matches the certificate (pubkey comparison, REF-0008)."""

    def _pubkey_output(cmd: list[str], op: str) -> str | None:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning("[IMP:7][ssl_certs] openssl %s check failed (%s): %s", op, cert_path, e)
            return None
        if result.returncode != 0:
            logger.info("[IMP:8][ssl_certs] openssl %s rc=%d for %s", op, result.returncode, cert_path)
            return None
        return result.stdout

    cert_pub = _pubkey_output(["openssl", "x509", "-in", cert_path, "-noout", "-pubkey"], "x509 -pubkey")
    if cert_pub is None:
        return False
    key_pub = _pubkey_output(["openssl", "pkey", "-in", key_path, "-pubout"], "pkey -pubout")
    if key_pub is None:
        return False

    matches = "".join(cert_pub.split()) == "".join(key_pub.split())
    if matches:
        logger.info("[IMP:9][ssl_certs] cert/key pair match OK: %s", cert_path)
    else:
        logger.warning(
            "[IMP:8][ssl_certs] cert/key MISMATCH (pubkey differs): cert=%s key=%s",
            cert_path,
            key_path,
        )
    return matches


# endregion FUNC_cert_key_pair_matches


# region FUNC_validate_cert_domain_fqdn
## @purpose  Fail-fast FQDN-валидатор для cert-pipeline (REF-0008 подпункт 6, SEC-0026):
##           needs.domain из node.yaml попадает в пути live/`domain`/, S3-keys и shell-строки
##           reloadcmd — `../` в домене = path traversal/RCE под root. Правила идентичны
##           vhost_renderer.validate_vhost_identifiers (fqdn-часть): ≥2 labels, RFC DNS label,
##           TLD [a-z]{2,}.
## @io       ⇥ fqdn: str → ⊕ ConfigValidationError | ⎋ None (PASS)
## @complexity O(N) — regex per label
## @invariants
##   - Violation → ConfigValidationError (exit 4) — вызывающие (orchestrate_certs entry,
##     add_project, register_project) обязаны НЕ продолжать обработку (fail-fast)
# 🧐 TRAP[DECISION] · 2026-08-24 · — · Дублирование fqdn-правил vhost_renderer в shared/ssl_certs
# · Rejected: импорт validate_vhost_identifiers из scaffold в shared/cert-модули
# · Reason: import-linter acyclic-internal-domains — scaffold импортирует shared → ребро
# ·   shared→scaffold создало бы цикл; перенос канонической реализации в shared = rename-риск
# ·   для потребителей vhost_renderer вне скоупа launch-window (freeze P3: только точечные диффы)
# · Rev: пост-launch миграция validate_vhost_identifiers в shared (или выделение fqdn-части)
# ·   с делегацией vhost_renderer — удалить дубль и этот TRAP
_FQDN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TLD_RE = re.compile(r"^[a-z]{2,}$")
_MIN_FQDN_LABELS = 2


def validate_cert_domain_fqdn(fqdn: str) -> None:
    """Validate FQDN for the cert pipeline (fail-fast on traversal/injection, REF-0008)."""
    labels = fqdn.split(".")
    if len(labels) < _MIN_FQDN_LABELS:
        msg = f"Invalid cert domain FQDN (single label, no TLD): {fqdn!r}"
        raise ConfigValidationError(msg)
    for label in labels:
        if not _FQDN_LABEL_RE.match(label):
            msg = f"Invalid cert domain FQDN label {label!r} in {fqdn!r}"
            raise ConfigValidationError(msg)
    if not _TLD_RE.match(labels[-1]):
        msg = f"Invalid cert domain FQDN TLD {labels[-1]!r} in {fqdn!r}"
        raise ConfigValidationError(msg)


# endregion FUNC_validate_cert_domain_fqdn


# region FUNC_cert_is_valid
## @purpose  Единая комбинация «сертификат валиден» (DevPlan 118 C9): parseable + LE issuer +
##           (pair-match — если key_path задан) + (domain match — если expected_domains задан) +
##           (expiry — если check_expiry). Дедупликация трёх реализаций: s3_ssl_cache._validate_cert
##           (parseable+LE+domain+expiry), cert_orchestrator._is_cert_valid (expiry+LE),
##           context_deployer (expiry+LE inline). Domain match — SAN primary / CN fallback
##           (DevPlan 004 W1, _cert_covers_domain). REF-0008: key_path включает pair-match
##           (cert_key_pair_matches) — restore-пути обязаны проверять согласованность пары.
## @io       ⇥ cert_path: str; threshold: int (сек до истечения, default 30 дней);
##              expected_domains: str | list[str] | None; check_expiry: bool;
##              key_path: str | None (приватный ключ для pair-match); timeout: int
##           ⎋ bool (True = валиден)
## @complexity — O(1) + 2-6 openssl subprocess
## @invariants
##   - Порядок: parseable → LE issuer → (pair-match if key_path) → (domain match: SAN primary,
##     CN fallback) → (expiry) — fail-fast на первом False
##   - expected_domains=None → domain-check пропускается (cert_orchestrator/context_deployer семантика)
##   - check_expiry=False → expiry-check пропускается (s3_ssl_cache download-семантика)
##   - key_path=None → pair-check пропускается (additive-only: прежние сигнатуры/поведение 1:1)
##   - Non-fatal: никогда не raise — subprocess ошибки → False (graceful degradation)
def cert_is_valid(
    cert_path: str,
    threshold: int = DEFAULT_EXPIRY_THRESHOLD,
    expected_domains: str | list[str] | None = None,
    check_expiry: bool = True,
    timeout: int = DEFAULT_OPENSSL_TIMEOUT,
    *,
    key_path: str | None = None,
) -> bool:
    """Check cert is valid: parseable + LE issuer + optional pair-match + domain match + expiry (C9+W1+REF-0008)."""
    # 1. Parseable (openssl x509 -noout)
    if not cert_is_parseable(cert_path, timeout=timeout):
        return False

    # 2. LE issuer (reject mkcert/self-signed — P0 TRAP[BUG] 2026-07-22)
    if not cert_is_le_issuer(cert_path, timeout=timeout):
        logger.info("[IMP:8][ssl_certs] cert_is_valid: not Let's Encrypt issuer — invalid: %s", cert_path)
        return False

    # 3. Pair-match (REF-0008: приватный ключ соответствует сертификату — опционально, key_path)
    if key_path is not None and not cert_key_pair_matches(cert_path, key_path, timeout=timeout):
        logger.warning(
            "[IMP:7][ssl_certs] cert_is_valid: privkey does not match certificate — invalid pair: %s",
            cert_path,
        )
        return False

    # 4. Domain match (опционально — expected_domains; SAN primary / CN fallback, W1)
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

    # 5. Expiry (>threshold до истечения, опционально — check_expiry)
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
