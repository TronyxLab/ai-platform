#!/usr/bin/env python3
# GREP_SUMMARY: http-probe curl-http-code shared probe timeout connection parse runner-DI verify verify-sweep
# STRUCTURE: ▶ curl_http_code(url, timeout[, extra_args, timeout_label, runner]) → ⚡ curl -sS -o /dev/null -w %{http_code} --max-time t → ◇ TimeoutExpired | rc≠0 | unparseable → ⎋ (code|None, error|None)
# region MODULE_CONTRACT
## @purpose  Единый HTTP-probe примитив (DevPlan 172 W5.4): сборка curl-команды +
##           timeout/rc/парсинг-обработка. Дедуплицирует две реализации:
##           verify/domain_verifier._curl_http_code (план 170 W7-E1) и
##           verify_sweep/http_check.check_http (DevPlan 136 T5.1) — общий
##           кор-цикл curl-probe (~45 LOC дублировались с риском дрейфа).
## @scope    Потребители: core/internal/verify/domain_verifier.py (verify_domain/
##           verify_status_page), core/internal/verify_sweep/http_check.py (check_http).
##           shared-слой: НЕ импортирует bootstrap/deploy (слои только вниз).
## @invariants
##   1. curl-флаги фиксированы: -sS -o /dev/null -w '%{http_code}' --max-time {timeout};
##      extra_args добавляются ПЕРЕД url (--resolve/--user и т.п.)
##   2. Python-level timeout = timeout + 5 (паритет прежних вызовов subprocess.run)
##   3. Ошибки fail-verbose (R4): TimeoutExpired → '{label} timed out (>{t}s)';
##      rc≠0 → 'Connection failed (curl exit {rc})'; unparseable →
##      'Failed to parse HTTP code: {stdout}' — сообщения 1:1 с domain_verifier
##      (verify_sweep-сообщения совпадали по первым двум; третье унифицировано)
##   4. runner DI: параметр runner (None → subprocess.run) резолвится В ТЕЛЕ —
##      тесты patch("subprocess.run")/DI перехватывают
##   5. Код НЕ пишет verdict-классификацию (классификация — домен потребителя:
##      domain_verifier pass|warn, verify_sweep classify_http_code)
## @rationale SSH-раннер-прецедент (139 W3 T4): единый shared-примитив вместо
##            verbatim-копий; timeout-семантика едина, дрейф невозможен.
## @changes  2026-08-15 | DevPlan 172 W5.4 — Created
## @usecases
##   - verify_domain → curl_http_code(url, timeout) → (200, None)
##   - check_http → curl_http_code(url, timeout, extra_args=["--resolve", fqdn:443:host])
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable

logger = logging.getLogger(__name__)


# region FUNC_curl_http_code
def curl_http_code(
    url: str,
    timeout: int,
    *,
    timeout_label: str = "Connection",
    extra_args: list[str] | None = None,
    runner: Callable[[list[str], int], subprocess.CompletedProcess[str]] | None = None,
) -> tuple[int | None, str | None]:
    """curl → (http_code, error) — единый probe-примитив (172 W5.4).

    ## @purpose — Дедупликация curl-обвязки verify_domain/verify_status_page (170 W7-E1)
    ##            и verify_sweep.check_http (136 T5.1): одна сборка команды,
    ##            обработка timeout/rc/парсинга.
    ## @io — ⇥ url: str; timeout: int (--max-time, сек); timeout_label: str
    ##         (суффикс сообщения таймаута); extra_args: list[str] | None;
    ##         runner: DI (None → subprocess.run)
    ##       → ⎋ (int | None, str | None): code None ⇔ error не None
    ## @complexity — O(1) — один subprocess
    ## @invariants
    ##   - runner резолвится В ТЕЛЕ (дефолтный параметр НЕ захватывает объект —
    ##     тесты patch("subprocess.run") перехватывают)
    ##   - Сообщения: '{label} timed out (>{t}s)' / 'Connection failed (curl exit {rc})'
    ##     / 'Failed to parse HTTP code: {stdout}'
    ##   - timeout+5 — Python-level window (паритет прежних реализаций)
    ##   - curl flags фиксированы: -sS -o /dev/null -w '%{http_code}' --max-time {t}
    """
    cmd = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout)]
    if extra_args:
        cmd += extra_args
    cmd.append(url)
    logger.info("[IMP:7][curl_http_code][curl] Checking %s (timeout=%ds)", url, timeout)

    actual_runner = runner if runner is not None else subprocess.run
    try:
        result = actual_runner(cmd, timeout + 5)
    except subprocess.TimeoutExpired:
        logger.error("[IMP:10][curl_http_code][timeout] %s timed out for %s", timeout_label, url)
        return None, f"{timeout_label} timed out (>{timeout}s)"

    if result.returncode != 0:
        logger.info("[IMP:9][curl_http_code][fail] Connection failed for %s: curl exit %d", url, result.returncode)
        return None, f"Connection failed (curl exit {result.returncode})"

    try:
        http_code = int(result.stdout.strip())
    except ValueError:
        logger.error("[IMP:10][curl_http_code][parse] Failed to parse HTTP code from: %s", result.stdout)
        return None, f"Failed to parse HTTP code: {result.stdout.strip()}"
    return http_code, None


# endregion FUNC_curl_http_code
