# GREP_SUMMARY: health, loki, minio, ready-poll, healthcheck, docker-compose-ps, thread-safe-state, loki_ready, observability
# STRUCTURE: ┌LOKI_READY_STATE (lock)┐ → ◇ _wait_for_loki_ready(HTTP /ready poll) → ◇ _wait_for_minio_healthy(compose ps poll) → ⊕ loki_ready fixture → ⎋ bool

# region MODULE_CONTRACT
## @purpose  Health-readiness polling for smoke compose stacks: Loki HTTP /ready probe
##           (scratch image has no curl/wget — host-side poll), MinIO health poll
##           (one-shot init container breaks `compose up --wait`, D5), and the thread-safe
##           LOKI_READY_STATE registry (T12.7 T-10) with the loki_ready fixture.
##           Extracted from smoke.py (DevPlan 170 W8).
## @scope    Consumed by _conftest/compose.py (_start_single_module, platform_services) and
##           the loki_ready fixture (declared here; re-exported via smoke.py facade).
## @invariants
##   - Loki poll: every _POLL_INTERVAL_SECONDS (5s); True на первом HTTP 200; False по таймауту
##     (graceful degradation); IMP:8 на 503, IMP:9 на 200
##   - MinIO poll: compose ps --format json → minio running+healthy; one-shot createbuckets
##     контейнер НЕ считается (exited-контейнер ломает --wait контракт, D5)
##   - LOKI_READY_STATE: заполняется _record_loki_ready (thread-safe, lock), агрегируется
##     _loki_ready_aggregate(); False если модуль observability не стартовал (не наблюдался)
##   - loki_ready fixture: честный флаг (T12.7 T-10) — потребители (loki-зависимые тесты)
##     скипают при False (инфраструктурная недоступность — легитимный skip per tests/AGENTS.md)
## @rationale  Extracted from smoke.py to isolate polling/health domain from compose lifecycle (W8).
## @changes    CREATED: 2026-08-15 | DevPlan 170 W8: вынесен из tests/_conftest/smoke.py
##             (T12.7 T-10 логика сохранена 1:1)
# endregion MODULE_CONTRACT

import json
import logging
import os
import subprocess
import threading
import time as _time

import pytest

logger = logging.getLogger(__name__)

# ── Named constants for magic numbers used in health polls ─────────────
_POLL_INTERVAL_SECONDS = 5  # sleep interval in _wait_for_loki_ready
_REQUEST_TIMEOUT_SECONDS = 10  # requests.get timeout in _wait_for_loki_ready


# region LOKI_READY_STATE
# T12.7 (T-10): реестр готовности Loki (observability-модуль). Заполняется
# _start_single_module (ленивый HTTP-poll /ready), агрегируется _loki_ready_aggregate()
# в результат platform_services и потребляется фикстурой loki_ready (skip loki-зависимых).
_LOKI_READY_STATE: dict[str, object] = {"observed": False, "ready": False}
_LOKI_READY_LOCK = threading.Lock()


def _record_loki_ready(ready: bool) -> None:
    """Зафиксировать результат Loki /ready poll (thread-safe)."""
    with _LOKI_READY_LOCK:
        _LOKI_READY_STATE["observed"] = True
        _LOKI_READY_STATE["ready"] = ready


def _loki_ready_aggregate() -> bool:
    """Вернуть готовность Loki: False если не наблюдалась (модуль не стартовал)."""
    with _LOKI_READY_LOCK:
        return bool(_LOKI_READY_STATE["ready"])


# endregion LOKI_READY_STATE


@pytest.fixture(scope="session")
def loki_ready() -> bool:
    """True если Loki /ready poll прошёл (иначе False — loki-зависимые тесты skip).

    ## @purpose  T12.7 (T-10): честный флаг готовности Loki вместо silent-proceed.
    ##            Потребители (loki-зависимые тесты) запрашивают фикстуру и скипают при False
    ##            (инфраструктурная недоступность — легитимный skip per tests/AGENTS.md rule 4).
    ## @io       → ⎋ bool
    ## @complexity O(1)
    """
    return _loki_ready_aggregate()


def _wait_for_loki_ready(
    url: str,
    timeout: int,
    logger: logging.Logger,
) -> bool:
    """Poll Loki /ready endpoint until HTTP 200 or timeout.

    ## @purpose — Bridge the gap between Docker liveness healthcheck
    ##            (loki -version checks only process alive) and actual
    ##            query frontend readiness. Loki scratch image has no
    ##            curl/wget — cannot HTTP-probe from inside container.
    ##            This poll runs from the host, outside the container.
    ## @io — ⇥ url, timeout → ⌋ bool (True=200 received)
    ## @complexity — O(T) where T=poll iterations
    ## @invariants
    ##   - Polls every _POLL_INTERVAL_SECONDS (5s)
    ##   - Returns True on first HTTP 200
    ##   - Returns False on timeout — graceful degradation
    ##   - IMP:8 logs on 503, IMP:9 on 200
    ## @rationale — TRAP[BUG] in loki docker-compose.base.yml documents
    ##              that scratch image has no HTTP client for healthcheck.
    ##              This function is the external readiness probe that
    ##              complements the internal liveness healthcheck.
    """
    import requests as _requests

    deadline = _time.monotonic() + timeout
    first_poll = True

    while _time.monotonic() < deadline:
        if not first_poll:
            _time.sleep(_POLL_INTERVAL_SECONDS)
        first_poll = False

        try:
            r = _requests.get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
            if r.status_code == 200:
                logger.info(
                    "[IMP:9][health][_wait_for_loki_ready] Loki /ready OK: HTTP 200 — %s",
                    r.text.strip(),
                )
                return True
            logger.info(
                "[IMP:8][health][_wait_for_loki_ready] Loki /ready returned %d: %s — waiting...",
                r.status_code,
                r.text.strip()[:100],
            )
        except _requests.RequestException as exc:
            logger.info(
                "[IMP:8][health][_wait_for_loki_ready] Loki /ready unreachable: %s — waiting...",
                exc,
            )

    logger.warning(
        "[IMP:9][health][_wait_for_loki_ready] Loki /ready timeout after %ds",
        timeout,
    )
    return False


def _wait_for_minio_healthy(
    compose_base_args: list[str],
    timeout: int,
    logger: logging.Logger,
) -> bool:
    """Poll docker compose ps --format json until minio container is healthy.

    ## @purpose — Wait for minio (not minio-createbuckets one-shot init container)
    ##            to become healthy. minio-createbuckets exits 0 after creating
    ##            buckets, which makes `docker compose up --wait` return 1 even
    ##            though minio itself is healthy. This function polls only the
    ##            minio container's Health status.
    ## @io — ⇥ compose_base_args, timeout, logger → ⎋ bool (healthy within timeout)
    ## @complexity — O(T) where T = timeout / poll_interval
    ## @rationale — D5: one-shot init container exits 0, breaking --wait contract.
    ##              Separate health poll avoids coupling to createbuckets lifecycle.
    """
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if _poll_minio_health_once(compose_base_args):
            return True
        _time.sleep(2)

    logger.warning("[IMP:9][health][_wait_for_minio_healthy] MinIO not healthy within %ds", timeout)
    return False


def _poll_minio_health_once(compose_base_args: list[str]) -> bool:
    """Один poll-шаг: docker compose ps → minio healthy? (PLW0717-хелпер).

    ## @purpose — Извлечение тела try-цикла: один запрос compose ps --format json
    ##             и проверка Health==healthy контейнера minio. Возвращает False при
    ##             недоступности/неготовности (вызывающий делает sleep+retry).
    ## @io — ⇥ compose_base_args → ⎋ bool (minio running+healthy в этом снимке)
    ## @complexity — O(N) где N = строки JSONL вывода
    """
    # DevPlan 006 W6: SMOKE_ENV обязана попасть и в compose ps — root compose включает
    # nginx с ${NGINX_OVERLAY_DIR:?} (B23): без smoke-env интерполяция ВСЕГДА падает
    # → poll вечно возвращает False → minio ложно «not healthy within 120s» (корень
    # флака ci-docker smoke). Плюс stderr-tail в warning (прежний лог глотал причину).
    from _conftest.env import get_smoke_env

    try:
        ps_result = subprocess.run(
            [*compose_base_args, "ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env={**os.environ, **get_smoke_env()},
        )
        if ps_result.returncode != 0:
            logger.warning(
                "[IMP:8][health][_wait_for_minio_healthy] docker compose ps failed rc=%d: %s",
                ps_result.returncode,
                (ps_result.stderr or "").strip()[-200:],
            )
            return False
        return _minio_healthy_in_output(ps_result.stdout)
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:8][health][_wait_for_minio_healthy] docker compose ps timed out")
    return False


def _minio_healthy_in_output(stdout: str) -> bool:
    """Проверить JSONL-вывод compose ps на minio running+healthy (PLW0717-хелпер).

    ## @io — ⇥ stdout: str (JSONL, одна строка на контейнер) → ⎋ bool
    ## @complexity — O(N) где N = строки JSONL
    """
    for line in stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            container = json.loads(line)
        except json.JSONDecodeError:
            continue

        service = container.get("Service", "")
        state = container.get("State", "")
        health = container.get("Health", "")

        if service == "minio" and state == "running" and health == "healthy":
            logger.info(
                "[IMP:9][health][_wait_for_minio_healthy] MinIO is healthy (service=%s state=%s health=%s)",
                service,
                state,
                health,
            )
            return True
    return False
