# GREP_SUMMARY: verify-sweep, check-tls, openssl-s-client, leaf-cert, san-wildcard, cert-expiry, warn-14d, chain-depth, le-issuer, r4-fail-not-skip
# STRUCTURE: ▶ check_tls ┌ep┐ → ◇ _fetch_s_client (openssl s_client -connect host:443 -servername fqdn -showcerts) → ◇ fail? → ⎋ TlsResult(fail)
#            → ◇ _analyze_leaf ┌output┐ → chain_depth + leaf PEM (tmp) → SAN wildcard ⊕ _cert_days_left ⊕ ssl_certs cross-check ⊕ issuer → ⎋ TlsResult
# region MODULE_CONTRACT
## @purpose  TLS-проверка endpoint sweep-верификации (DevPlan 136 T5.1): openssl s_client —
##           chain (число сертификатов), wildcard SAN-матчинг fqdn против subjectAltName,
##           expiry WARN<14d / FAIL при истечении, issuer (Let's Encrypt — инфо-WARN).
## @scope    check_tls (s_client-fetch + analyze-leaf) + pure-хелперы (san_matches_domain,
##           expiry_verdict, _extract_leaf_cert, _cert_san_matches, _cert_days_left) +
##           дефолтный subprocess-раннер + константы TLS-домена.
## @invariants
##   - `openssl s_client -connect {host}:{SSL_PORT} -servername {fqdn} -showcerts </dev/null`
##   - chain_depth = число CERTIFICATE-блоков в s_client выводе (0 → fail)
##   - SAN берётся из leaf (первого) PEM через `openssl x509 -noout -ext subjectAltName`
##   - expiry: _cert_days_left (openssl x509 -enddate) → expiry_verdict (<14d WARN, <0 FAIL);
##     cross-check ssl_certs.cert_check_expiry(PEM, 14*86400) — реюз shared/ssl_certs (T5.6)
##   - issuer: ssl_certs.cert_is_le_issuer — не FAIL-ветка, а WARN-инфо (self-signed dev)
##   - Любая openssl ошибка/timeout → verdict fail (fail-verbose, никогда молчаливый pass)
## @rationale Декомпозиция монолита verify_sweep.py (план 170 W7-E1, research-A §7):
##            check_tls 131 LOC/CC10 → _fetch_s_client + _analyze_leaf. Публичная сигнатура
##            сохранена 1:1 (7 DI-параметров, тесты T14-T17 инжектят runner/helper-функции);
##            группировка в TLSProbeContext отклонена: изменение сигнатуры потребовало бы
##            правок тел тестов (инвариант волны: тесты — только импорты, поведение 1:1).
## @changes  2026-08-15 | План 170 W7-E1 — выделено из verify_sweep.py (чистый move + split)
##           2026-08-14 | W3.5-4 (164 S8) — +4 helper DI (SAN/expiry/check_expiry/is_le)
## @usecases
##   - main() (__init__.py): check_tls(ep, timeout) → TlsResult на каждый endpoint
##   - tests/unit/test_verify_sweep.py: T14 (ok), T15 (expired), T16 (SAN mismatch), T17 (R4 handshake), T8/T9/T10
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from core.internal.verify_sweep.models import SSL_PORT, Endpoint, TlsResult

logger = logging.getLogger(__name__)

OPENSSL_TIMEOUT_DEFAULT: int = 10
"""## @invariant Таймаут openssl s_client / x509 subprocess (сек)."""

EXPIRY_WARN_DAYS: int = 14
"""## @invariant Порог WARN: <14 дней до истечения сертификата — WARN (не FAIL)."""

_CERT_BLOCK_RE = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL)

# Типы DI-хелперов (W3.5-4, 164 S8): переопределяемы тестами без monkeypatch внутренних функций.
SanMatchesFn = Callable[[str, str, int], bool | None]
DaysLeftFn = Callable[[str, int], int | None]


# region FUNC_san_matches_domain
def san_matches_domain(fqdn: str, san: str) -> bool:
    """Проверить, покрывает ли SAN (subjectAltName) домен — точное или wildcard-совпадение.

    ▶ ┌fqdn + san┐ → ◇ exact (case-insens) → ◇ '*.domain' wildcard (ровно один уровень)
      → ⊕ True | ⎋ False

    ## @purpose — Wildcard SAN-матчинг для check_tls (DevPlan 136 T5.1: «wildcard SAN-матчинг»).
    ##            Отличается от shared/ssl_certs.cert_subject_matches_domain (CN-only, подстрока):
    ##            SAN-домен сравнивается полностью, '*.example.com' покрывает ТОЛЬКО
    ##            <label>.example.com, НЕ глубже и НЕ сам apex.
    ## @io — ⇥ fqdn: str — проверяемый FQDN (lowercase на входе вызывающего)
    ##       ⇥ san: str — SAN из сертификата (может содержать 'DNS:' префикс — срезается)
    ##       → ⎋ bool — True если SAN покрывает fqdn
    ## @complexity — O(N) где N = len(fqdn)
    ## @invariants
    ##   - Точное совпадение (example.com == example.com) → True
    ##   - Wildcard '*.example.com' матчит ровно один уровень: api.example.com ✅,
    ##     example.com ❌, deep.api.example.com ❌ (одноуровневая семантика wildcard)
    ##   - 'DNS:' префикс SAN срезается (openssl x509 -ext subjectAltName формат)
    ##   - Регистро-независимо (lowercase с обеих сторон)
    ##   - '*' не в первом сегменте (foo.*.com) → False (невалидный wildcard)
    ##   - Пустой fqdn/san → False (fail-fast, никогда True)
    """
    fqdn_l = fqdn.strip().lower()
    san_l = san.strip().lower()
    if san_l.startswith("dns:"):
        san_l = san_l[4:].strip()

    if not fqdn_l or not san_l:
        logger.info("[IMP:8][san_matches_domain] Empty fqdn or san — False")
        return False

    if fqdn_l == san_l:
        logger.info("[IMP:9][san_matches_domain] Exact SAN match: %s", san_l)
        return True

    if san_l.startswith("*."):
        wildcard_suffix = san_l[1:]  # ".example.com"
        if fqdn_l.endswith(wildcard_suffix):
            prefix = fqdn_l[: -len(wildcard_suffix)]
            if prefix and "." not in prefix:
                logger.info("[IMP:9][san_matches_domain] Wildcard SAN %s matches %s", san_l, fqdn_l)
                return True

    logger.info("[IMP:8][san_matches_domain] SAN %s does NOT match %s", san_l, fqdn_l)
    return False


# endregion FUNC_san_matches_domain


# region FUNC_expiry_verdict
def expiry_verdict(days_left: int | None) -> str:
    """Expiry-вердикт по дням до истечения (WARN<14d / FAIL при истечении).

    ▶ ┌days_left┐ → ◇ None → 'fail' (непарсируемая дата — fail-verbose) → ◇ <0 → 'fail'
      → ◇ <14 → 'warn' → ⎋ 'ok'

    ## @purpose — Пороговая логика WARN/FAIL для check_tls expiry (I3): WARN при <14 дней,
    ##            FAIL при истечении (days_left < 0). None (непарсируемая дата) — FAIL
    ##            (fail-verbose, никогда молчаливый pass).
    ## @io — ⇥ days_left: int | None — дней до notAfter → ⎋ str ('ok'|'warn'|'fail')
    ## @complexity — O(1)
    ## @invariants
    ##   - days_left < 0 → 'fail' (сертификат истёк)
    ##   - 0 <= days_left < 14 → 'warn' (порог WARN, константа EXPIRY_WARN_DAYS)
    ##   - days_left >= 14 → 'ok'
    ##   - None → 'fail' (нельзя подтвердить — FAIL, не skip; R4-дух)
    """
    if days_left is None:
        logger.info("[IMP:9][expiry_verdict] No parseable expiry date → fail")
        return "fail"
    if days_left < 0:
        logger.info("[IMP:9][expiry_verdict] %d days left (<0) → fail (expired)", days_left)
        return "fail"
    if days_left < EXPIRY_WARN_DAYS:
        logger.info("[IMP:9][expiry_verdict] %d days left (<%d) → warn", days_left, EXPIRY_WARN_DAYS)
        return "warn"
    logger.info("[IMP:9][expiry_verdict] %d days left (>=%d) → ok", days_left, EXPIRY_WARN_DAYS)
    return "ok"


# endregion FUNC_expiry_verdict


# region FUNC_check_tls
def check_tls(
    ep: Endpoint,
    *,
    timeout: int = OPENSSL_TIMEOUT_DEFAULT,
    s_client_runner: Callable[[list[str], int], subprocess.CompletedProcess[str]] | None = None,
    cert_san_matches_fn: Callable[[str, str, int], bool | None] | None = None,
    cert_days_left_fn: Callable[[str, int], int | None] | None = None,
    cert_check_expiry_fn: Callable[..., bool] | None = None,
    cert_is_le_issuer_fn: Callable[..., bool | None] | None = None,
) -> TlsResult:
    """TLS-проверка endpoint: openssl s_client → chain + SAN wildcard + expiry (I3).

    ▶ ┌ep┐ → ◇ _fetch_s_client (s_client -connect host:443 -servername fqdn -showcerts)
      → ◇ fail? → ⎋ TlsResult(fail) → ◇ _analyze_leaf (chain + leaf PEM → SAN/expiry/issuer) → ⎋ TlsResult

    ## @purpose — TLS-стек проверки (DevPlan 136 T5.1): chain (число сертификатов в выводе
    ##            s_client), wildcard SAN-матчинг fqdn против subjectAltName, expiry
    ##            WARN<14d / FAIL при истечении. Реюз shared/ssl_certs (T5.6) на извлечённом
    ##            leaf PEM: cert_check_expiry (порог 14 дней) + cert_is_le_issuer (инфо-WARN).
    ## @io — ⇥ ep: Endpoint; timeout: int; s_client_runner DI (None → subprocess.run);
    ##       cert_san_matches_fn/cert_days_left_fn/cert_check_expiry_fn/cert_is_le_issuer_fn DI
    ##       (None → модульные openssl-хелперы) → ⎋ TlsResult (chain_depth, san_ok, days_left,
    ##       verdict, issuer, error)
    ## @complexity — O(1) — 1-3 openssl subprocess (s_client + x509 SAN + x509 enddate/checkend)
    ## @invariants
    ##   - Сигнатура 7 DI-параметров сохранена 1:1 (тесты T14-T17; план 170 W7-E1)
    ##   - chain_depth = число CERTIFICATE-блоков в s_client выводе (0 → fail)
    ##   - SAN/expiry/issuer проверяются на leaf (первом) PEM — _analyze_leaf
    ##   - Любая openssl ошибка/timeout → verdict fail (fail-verbose, никогда молчаливый pass)
    ## @changes 2026-08-15 | План 170 W7-E1 — split на _fetch_s_client + _analyze_leaf
    """
    runner = s_client_runner or _default_subprocess_runner
    output, early_fail = _fetch_s_client(ep, timeout, runner)
    if early_fail is not None:
        return early_fail

    assert output is not None  # _fetch_s_client: output None только вместе с early_fail

    # W3.5-4 DI: helper-функции переопределяемы (None → модульные openssl-хелперы)
    san_fn = cert_san_matches_fn if cert_san_matches_fn is not None else _cert_san_matches
    days_fn = cert_days_left_fn if cert_days_left_fn is not None else _cert_days_left
    return _analyze_leaf(ep, output, timeout, san_fn, days_fn, cert_check_expiry_fn, cert_is_le_issuer_fn)


# endregion FUNC_check_tls


# region FUNC__fetch_s_client
def _fetch_s_client(
    ep: Endpoint,
    timeout: int,
    runner: Callable[[list[str], int], subprocess.CompletedProcess[str]],
) -> tuple[str | None, TlsResult | None]:
    """s_client-fetch: openssl s_client → вывод или ранний fail-результат (план 170 W7-E1).

    ▶ ┌ep, timeout, runner┐ → ⚡ openssl s_client -connect host:443 -servername fqdn -showcerts
      → ◇ Timeout/FileNotFound/rc!=0 → ⎋ (None, TlsResult(fail)) → ⎋ (output, None)

    ## @purpose — Единственная точка openssl s_client subprocess (DI s_client_runner).
    ##            Все fail-ветки handshake-этапа агрегируются здесь: timeout, openssl
    ##            отсутствует, rc != 0 (R4: FAIL, не skip).
    ## @io — ⇥ ep; timeout; runner DI → ⎋ (str | None, TlsResult | None):
    ##       output — stdout s_client; ранний TlsResult — если handshake не удался
    ## @complexity — O(1) — один subprocess
    ## @invariants — output None ⇔ TlsResult не None (fail); fail-verbose с конкретной ошибкой
    """
    cmd = [
        "openssl",
        "s_client",
        "-connect",
        f"{ep.host}:{SSL_PORT}",
        "-servername",
        ep.fqdn,
        "-showcerts",
    ]
    logger.info("[IMP:7][_fetch_s_client] openssl s_client %s:%d SNI=%s", ep.host, SSL_PORT, ep.fqdn)

    try:
        result = runner(cmd, timeout)
    except subprocess.TimeoutExpired:
        logger.info("[IMP:10][_fetch_s_client] openssl s_client timeout for %s", ep.fqdn)
        return None, TlsResult(fqdn=ep.fqdn, verdict="fail", error=f"openssl s_client timed out (>{timeout}s)")
    except FileNotFoundError as exc:
        logger.info("[IMP:10][_fetch_s_client] openssl not found: %s", exc)
        return None, TlsResult(fqdn=ep.fqdn, verdict="fail", error=f"openssl not found: {exc}")

    if result.returncode != 0:
        logger.info(
            "[IMP:9][_fetch_s_client] TLS handshake failed for %s (openssl exit %d)", ep.fqdn, result.returncode
        )
        return None, TlsResult(
            fqdn=ep.fqdn,
            verdict="fail",
            error=f"TLS handshake failed (openssl exit {result.returncode})",
        )
    return result.stdout or "", None


# endregion FUNC__fetch_s_client


# region FUNC__analyze_leaf
def _analyze_leaf(
    ep: Endpoint,
    s_client_output: str,
    timeout: int,
    san_fn: SanMatchesFn,
    days_fn: DaysLeftFn,
    cert_check_expiry_fn: Callable[..., bool] | None,
    cert_is_le_issuer_fn: Callable[..., bool | None] | None,
) -> TlsResult:
    """analyze-leaf: chain_depth + leaf PEM → SAN/expiry/issuer → вердикт (план 170 W7-E1).

    ▶ ┌output┐ → ○ _CERT_BLOCK_RE.findall → chain_depth → ○ _extract_leaf_cert → ⊕ tmp PEM
      → ◇ SAN (san_fn) → ◇ days_left (days_fn) → expiry_verdict → ◇ ssl_certs cross-check + issuer
      → ⊕ verdict composition → ⎋ TlsResult

    ## @purpose — Разбор leaf-сертификата из вывода s_client: SAN wildcard-матчинг, expiry
    ##            WARN/FAIL, кросс-проверка shared/ssl_certs (T5.6), issuer-инфо. Temp PEM
    ##            файл с гарантированной очисткой (finally).
    ## @io — ⇥ ep; s_client_output; timeout; san_fn/days_fn (DI, None → модульные);
    ##         cert_check_expiry_fn/cert_is_le_issuer_fn (DI, None → ssl_certs-канон)
    ##       → ⎋ TlsResult (chain_depth, san_ok, days_left, verdict, issuer, error)
    ## @complexity — O(L) парсинг + 1-2 openssl x509 subprocess
    ## @invariants
    ##   - chain_depth 0 → fail («No certificate chain»); leaf извлечение None → fail
    ##   - verdict = expiry_fail | (SAN False → fail) | expiry_verdict (warn/ok)
    ##   - issuer: "Let's Encrypt" | "unknown" (is_le False) | None (непарсируемо)
    ##   - Temp PEM удаляется в finally (research-B §1: tempfile с finally — ок)
    """
    chain_depth = len(_CERT_BLOCK_RE.findall(s_client_output))
    if chain_depth == 0:
        logger.info("[IMP:10][_analyze_leaf] No certificate chain for %s", ep.fqdn)
        return TlsResult(fqdn=ep.fqdn, chain_depth=0, verdict="fail", error="No certificate chain in s_client output")

    leaf_pem = _extract_leaf_cert(s_client_output)
    if leaf_pem is None:
        logger.info("[IMP:10][_analyze_leaf] Leaf cert extraction failed for %s", ep.fqdn)
        return TlsResult(fqdn=ep.fqdn, chain_depth=chain_depth, verdict="fail", error="Leaf cert extraction failed")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".pem", delete=False) as tmp:
        tmp.write(leaf_pem)
        tmp_path = tmp.name
    try:
        san_ok = san_fn(tmp_path, ep.fqdn, timeout)

        # ── Expiry: days_left + shared/ssl_certs реюз (T5.6) ──
        days_left = days_fn(tmp_path, timeout)
        expiry_v = expiry_verdict(days_left)

        from core.internal.shared.ssl_certs import cert_check_expiry as _cert_check_expiry_impl

        expiry_fn = cert_check_expiry_fn if cert_check_expiry_fn is not None else _cert_check_expiry_impl
        cross_ok = expiry_fn(tmp_path, EXPIRY_WARN_DAYS * 86400, timeout=timeout)

        # ── Issuer (информационный WARN, не FAIL-ветка) ─────────────
        from core.internal.shared.ssl_certs import cert_is_le_issuer as _cert_is_le_issuer_impl

        le_fn = cert_is_le_issuer_fn if cert_is_le_issuer_fn is not None else _cert_is_le_issuer_impl
        is_le = le_fn(tmp_path, timeout=timeout)

        verdict = expiry_v if expiry_v == "fail" else ("fail" if san_ok is False else expiry_v)
        if san_ok is False:
            logger.info("[IMP:9][_analyze_leaf] %s — SAN mismatch → fail", ep.fqdn)
        logger.info(
            "[IMP:9][_analyze_leaf] %s — chain=%d san_ok=%s days_left=%s cross_check=%s le=%s verdict=%s",
            ep.fqdn,
            chain_depth,
            san_ok,
            days_left,
            cross_ok,
            is_le,
            verdict,
        )
        return TlsResult(
            fqdn=ep.fqdn,
            chain_depth=chain_depth,
            san_ok=san_ok,
            days_left=days_left,
            verdict=verdict,
            issuer="Let's Encrypt" if is_le else ("unknown" if is_le is False else None),
        )
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            logger.warning("[IMP:7][_analyze_leaf] Temp PEM cleanup failed: %s", tmp_path)


# endregion FUNC__analyze_leaf


# region FUNC__extract_leaf_cert
def _extract_leaf_cert(s_client_output: str) -> str | None:
    """Извлечь первый (leaf) PEM-сертификат из вывода openssl s_client -showcerts.

    ▶ ┌s_client_output┐ → ○ re BEGIN/END CERTIFICATE → ⊕ first block → ⎋ str | None

    ## @purpose — Первый CERTIFICATE-блок в s_client выводе = leaf (серверный) сертификат;
    ##            остальные — цепочка (intermediates/root). SAN/expiry проверяются на leaf.
    ## @io — ⇥ s_client_output: str → ⎋ str | None (leaf PEM с маркерами, None если блоков нет)
    ## @complexity — O(L) где L = длина вывода
    ## @invariants
    ##   - Возвращает ПЕРВЫЙ блок (порядок s_client: leaf → chain)
    ##   - Сохраняет BEGIN/END маркеры (необходимо для openssl x509 -in)
    ##   - Нет блоков → None (fail-verbose)
    """
    match = _CERT_BLOCK_RE.search(s_client_output)
    if not match:
        return None
    logger.info("[IMP:8][_extract_leaf_cert] Extracted leaf PEM (%d chars)", len(match.group(0)))
    return match.group(0)


# endregion FUNC__extract_leaf_cert


# region FUNC__cert_san_matches
def _cert_san_matches(cert_path: str, fqdn: str, timeout: int) -> bool | None:
    """Проверить SAN сертификата на покрытие fqdn (openssl x509 -ext subjectAltName).

    ▶ ┌cert_path, fqdn┐ → ⚡ openssl x509 -in C -noout -ext subjectAltName → ◇ rc!=0 → None
      → ○ parse 'DNS:' entries → ⊕ any san_matches_domain → ⎋ bool | None

    ## @purpose — SAN-матчинг leaf-сертификата (I3). openssl x509 -ext subjectAltName выводит
    ##            'DNS:example.com, DNS:*.example.com' — парсится и матчится per-SAN.
    ## @io — ⇥ cert_path: str; fqdn: str; timeout: int → ⎋ bool | None (None = непарсируемо)
    ## @complexity — O(1) subprocess + O(S) где S = число SAN
    ## @invariants
    ##   - subprocess ошибка/timeout → None (fail-verbose → вызывающий решает fail)
    ##   - Ни один SAN не матчит → False
    ##   - DNS:-префикс срезается в san_matches_domain
    """
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-ext", "subjectAltName"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("[IMP:7][_cert_san_matches] openssl SAN check failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.warning("[IMP:7][_cert_san_matches] openssl SAN exit %d", result.returncode)
        return None

    sans: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("DNS:"):
            continue
        sans.extend(token.strip() for token in line.split(",") if token.strip().startswith("DNS:"))
    if not sans:
        logger.info("[IMP:8][_cert_san_matches] No DNS SANs in cert for %s", fqdn)
        return False

    matched = any(san_matches_domain(fqdn, san) for san in sans)
    logger.info("[IMP:9][_cert_san_matches] fqdn=%s SANs=%r → %s", fqdn, sans, matched)
    return matched


# endregion FUNC__cert_san_matches


# region FUNC__cert_days_left
def _cert_days_left(cert_path: str, timeout: int) -> int | None:
    """Дней до истечения сертификата (openssl x509 -enddate → datetime).

    ▶ ┌cert_path┐ → ⚡ openssl x509 -in C -enddate -noout → parse 'notAfter=...' → ⊕ (notAfter - now).days → ⎋ int | None

    ## @purpose — Дней до notAfter для различения WARN (<14д) / FAIL (expired). Дата парсится
    ##            из `openssl x509 -enddate` ('notAfter=Aug  5 12:00:00 2026 GMT'). Отличие от
    ##            shared/ssl_certs.cert_check_expiry (-checkend bool): нужен сам день — для
    ##            пороговой классификации (T5.6 @rationale).
    ## @io — ⇥ cert_path: str; timeout: int → ⎋ int | None (None = непарсируемо)
    ## @complexity — O(1) subprocess
    ## @invariants
    ##   - openssl ошибка/непарсируемая дата → None (fail-verbose)
    ##   - Округление: (notAfter - now).days — floor; notAfter через минуту → 0 (warn)
    ##   - Формат 'notAfter=' + strftime('%b %e %H:%M:%S %Y %Z') (openssl -enddate)
    """
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-enddate", "-noout"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("[IMP:7][_cert_days_left] openssl enddate failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.warning("[IMP:7][_cert_days_left] openssl enddate exit %d", result.returncode)
        return None

    marker = "notAfter="
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith(marker):
            continue
        try:
            not_after = datetime.strptime(line[len(marker) :].strip(), "%b %e %H:%M:%S %Y %Z").replace(
                tzinfo=timezone.utc
            )
        except ValueError as exc:
            logger.warning("[IMP:7][_cert_days_left] Unparseable notAfter %r: %s", line, exc)
            return None
        days = (not_after - datetime.now(timezone.utc)).days
        logger.info("[IMP:9][_cert_days_left] notAfter=%s days_left=%d", line[len(marker) :].strip(), days)
        return days
    logger.warning("[IMP:7][_cert_days_left] no notAfter line in enddate output")
    return None


# endregion FUNC__cert_days_left


# region FUNC__default_subprocess_runner
def _default_subprocess_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Дефолтный openssl-раннер (subprocess.run) — DI-точка для unit-тестов.

    ▶ ┌cmd, timeout┐ → ⚡ subprocess.run(capture_output=True, text=True) → ⎋ CompletedProcess
    ## @purpose — Единая точка subprocess для openssl s_client (тесты инжектят s_client_runner).
    ## @io — ⇥ cmd: list[str]; timeout: int → ⎋ subprocess.CompletedProcess
    ## @complexity — O(1)
    """
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


# endregion FUNC__default_subprocess_runner
