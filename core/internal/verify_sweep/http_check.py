# GREP_SUMMARY: verify-sweep, check-http, curl-resolve, by-design-codes, expected-codes, http-classification, r4-fail-not-skip, dns-pinned
# STRUCTURE: ▶ check_http ┌ep┐ → ⚡ curl -sS -o /dev/null -w '%{http_code}' --max-time t --resolve f:443:host https://f/ → ◇ rc/Timeout → fail → ◇ classify_http_code (by-design | expected allowlist) → ⎋ HttpResult
# region MODULE_CONTRACT
## @purpose  HTTP-проверка endpoint sweep-верификации (DevPlan 136 T5.1): curl c DNS-закреплением
##           за IP ноды (--resolve {fqdn}:443:{host}) + by-design классификация кодов
##           (200/301/302/401/403/404/444 pass; 502/504/5xx fail; per-endpoint expected allowlist).
## @scope    check_http + classify_http_code + дефолтный curl-раннер + константы HTTP-домена.
##           TLS-проверка — в tls_check.py (общая константа SSL_PORT — в models.py).
## @invariants
##   - `--resolve {ep.fqdn}:443:{ep.host}` — DNS pinned (I4: не зависит от публичного DNS)
##   - By-design классификация (I2): OK-коды {200,301,302,401,403,404,444}; FAIL {502,504};
##     любые другие non-200 → fail; expected allowlist строго переопределяет by-design
##   - Connection error / TimeoutExpired → verdict fail (R4: FAIL, не skip)
##   - Диагностика [IMP:7-10] в каждый вердикт (LDD-телеметрия)
## @rationale Декомпозиция монолита verify_sweep.py (план 170 W7-E1, research-A §7):
##            check_http 64 LOC выделен в отдельный модуль HTTP-домена. Сигнатура check_http
##            сохранена 1:1 (тесты T11-T13 инжектят curl_runner).
## @changes  2026-08-15 | План 170 W7-E1 — выделено из verify_sweep.py (чистый move)
## @usecases
##   - main() (__init__.py): check_http(ep, timeout) → HttpResult на каждый endpoint
##   - tests/unit/test_verify_sweep.py: T11 (200), T12 (R4 connection error), T13 (502), T6/T7 classify
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import cast

from core.internal.shared.http_probe import curl_http_code as _probe
from core.internal.verify_sweep.models import SSL_PORT, Endpoint, HttpResult

logger = logging.getLogger(__name__)

CURL_TIMEOUT_DEFAULT: int = 10
"""## @invariant HTTP-таймаут curl --max-time (сек)."""

# Коды HTTP по дизайну (I2). FAIL-коды (502/504/5xx) легального by-design статуса не имеют.
_BY_DESIGN_OK_CODES: frozenset[int] = frozenset({200, 301, 302, 401, 403, 404, 444})
_BY_DESIGN_FAIL_CODES: frozenset[int] = frozenset({502, 504})
"""## @invariant by-design классификация: OK-коды — 200/301/302/401/403/404/444; FAIL — 502/504; иное → FAIL."""


# region FUNC_classify_http_code
def classify_http_code(code: int, expected: list[int] | None = None) -> str:
    """By-design классификация HTTP-кода endpoint (expected_codes, I2).

    ▶ ┌code, expected┐ → ◇ expected задан? → code ∈ expected → 'pass' | 'fail'
      → ◇ 200/301/302/401/403/404/444 → 'pass' → ◇ 502/504 → 'fail' → ⎋ 'fail' (иное)

    ## @purpose — expected_codes классификация по дизайну: 404/444 deny и 401/403 auth НЕ
    ##            считаются FAIL на живом endpoint (иначе ложные FAIL, DevPlan 136 §9).
    ##            per-endpoint expected allowlist переопределяет by-design набор.
    ## @io — ⇥ code: int — HTTP-код ответа; expected: list[int] | None — per-endpoint allowlist
    ##       → ⎋ str — 'pass' | 'fail' (warn не используется HTTP-классификацией)
    ## @complexity — O(1)
    ## @invariants
    ##   - expected задан: code ∈ expected → 'pass', иначе 'fail' (allowlist строгий)
    ##   - expected=None: by-design OK = {200,301,302,401,403,404,444}; FAIL = {502,504} + всё иное
    ##   - Ни один FAIL-код (502/504) не имеет легального by-design статуса — always fail
    ##   - Вердикт 'fail' → exit 1 (единственная fail-ветка HTTP)
    """
    if expected is not None:
        verdict = "pass" if code in expected else "fail"
        logger.info("[IMP:9][classify_http_code] code=%d expected=%r → %s", code, expected, verdict)
        return verdict

    if code in _BY_DESIGN_OK_CODES:
        logger.info("[IMP:9][classify_http_code] code=%d by-design OK → pass", code)
        return "pass"
    if code in _BY_DESIGN_FAIL_CODES:
        logger.info("[IMP:9][classify_http_code] code=%d by-design FAIL → fail", code)
        return "fail"

    logger.info("[IMP:9][classify_http_code] code=%d unexpected → fail", code)
    return "fail"


# endregion FUNC_classify_http_code


# region FUNC_check_http
def check_http(
    ep: Endpoint,
    *,
    timeout: int = CURL_TIMEOUT_DEFAULT,
    curl_runner: Callable[[list[str], int], subprocess.CompletedProcess[str]] | None = None,
) -> HttpResult:
    """HTTP-проверка endpoint: curl --resolve {fqdn}:443:{host} → by-design классификация.

    ▶ ┌ep┐ → ⚡ curl -sS -o /dev/null -w '%{http_code}' --max-time t --resolve f:443:host https://f/
      → ◇ rc/TimeoutExpired → fail | ◇ code → classify_http_code → ⎋ HttpResult

    ## @purpose — Проверка доступности endpoint по HTTPS с DNS-закреплением за IP ноды (I4).
    ##            Вердикт — by-design классификация (I2): 404/444 deny, 401/403 auth,
    ##            301/302 redirect — pass; 502/504/5xx — fail; connection error — fail (R4).
    ## @io — ⇥ ep: Endpoint; timeout: int; curl_runner DI (None → subprocess.run)
    ##       → ⎋ HttpResult (code, verdict, error)
    ## @complexity — O(1) — один subprocess curl
    ## @invariants
    ##   - `--resolve {ep.fqdn}:{SSL_PORT}:{ep.host}` — DNS pinned (не зависит от публичного DNS)
    ##   - Выход из stdout: %{http_code}; connection error → verdict fail (не skip)
    ##   - subprocess.TimeoutExpired → fail c ошибкой (fail-verbose)
    ##   - per-endpoint ep.expected allowlist → classify_http_code(code, ep.expected)
    """
    url = f"https://{ep.fqdn}/"
    logger.info("[IMP:7][check_http] curl %s (resolve → %s:%d)", url, ep.host, SSL_PORT)

    # 172 W5.4: общий probe-примитив shared/http_probe (дедуп domain_verifier._curl_http_code)
    code, probe_error = _probe(
        url,
        timeout,
        timeout_label="Connection",
        extra_args=["--resolve", f"{ep.fqdn}:{SSL_PORT}:{ep.host}"],
        runner=curl_runner,
    )
    if probe_error is not None:
        logger.info("[IMP:9][check_http] Probe failed for %s: %s", ep.fqdn, probe_error)
        return HttpResult(fqdn=ep.fqdn, code=None, verdict="fail", error=probe_error)

    verdict = classify_http_code(cast(int, code), ep.expected)
    logger.info("[IMP:9][check_http] %s — HTTP %s verdict=%s", ep.fqdn, code, verdict)
    return HttpResult(fqdn=ep.fqdn, code=cast(int, code), verdict=verdict)


# endregion FUNC_check_http


# region FUNC__default_curl_runner
def _default_curl_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Дефолтный curl-раннер (subprocess.run) — DI-точка для unit-тестов.

    ▶ ┌cmd, timeout┐ → ⚡ subprocess.run(capture_output=True, text=True) → ⎋ CompletedProcess
    ## @purpose — Единственная точка subprocess для check_http (тесты инжектят curl_runner).
    ## @io — ⇥ cmd: list[str]; timeout: int → ⎋ subprocess.CompletedProcess
    ## @complexity — O(1)
    """
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


# endregion FUNC__default_curl_runner
