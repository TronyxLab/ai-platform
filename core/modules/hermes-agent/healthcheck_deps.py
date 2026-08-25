#!/usr/bin/env python3
# GREP_SUMMARY: hermes-healthcheck deps required optional aggregation pg-isready redis-cli litellm tcp-probe healthy exit-code
# STRUCTURE: ▶ ┌pg/redis/litellm checkers┐ → ○ check_deps (required: PG+LiteLLM, optional: Redis) → ◇ healthy? → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  deps-режим healthcheck hermes-agent: агрегация required/optional зависимостей
#           (DevPlan 119 D6, AUDIT-1 F8). Перенос healthcheck.sh deps-ветки (48-112) в Python.
#           required: PostgreSQL + LiteLLM; optional: Redis (warn only).
## @scope    Вызывается healthcheck.sh deps-режим через `python3 healthcheck_deps.py` (exec, exit passthrough).
##           Чистая агрегация check_deps + I/O-примитивы (injectable для тестов).
## @invariants
##   - required = PG && LiteLLM: любой из них недоступен → unhealthy (exit 1)
##   - optional = Redis: недоступен → warn, НЕ влияет на вердикт (как shell deps-ветка)
##   - check_pg: pg_isready (если бинарник есть) → fallback TCP socket (эквивалент /dev/tcp)
##   - check_redis: redis-cli PING → "PONG" → fallback TCP socket (warn-only)
##   - check_litellm: HTTP GET 200 (urllib, не curl — встроенный клиент)
##   - Никогда не raise — сетевые ошибки/таймауты → False (graceful degradation)
##   - main() -> int канон (core/AGENTS.md); exit 0 = healthy, 1 = unhealthy
## @rationale D6 (DevPlan 119): healthcheck.sh deps-режим (48-112) — required/optional агрегация
##   без unit-тестов. Python + injectable checkers + R5 negative (required missing → unhealthy,
##   optional missing → healthy).
## @changes  2026-08-02 | DevPlan 119 D6 — Created (test-first: tests/unit/test_hermes_healthcheck.py)
## @changes  2026-08-14 | DevPlan 170 W1-A3 — порт litellm → локальная константа + TRAP (cross-layer)
## @see      core/modules/hermes-agent/healthcheck.sh (deps-режим → exec python3)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import http.client
import logging
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

logger = logging.getLogger(__name__)

# ⚠️ TRAP[DECISION] · 2026-08-14 · — · Порт-дубль SoT platform-infra.yaml (litellm 4000) — cross-layer
# · Rejected: импорт core/internal/shared/platform_ports из модуля
# · Reason: core/AGENTS.md Cross-layer — modules НЕ импортируют core/internal; контейнер hermes-agent
# ·   содержит только свой код. Значение — зеркало SoT (container-порт litellm); LITELLM_HEALTH_URL
# ·   в compose переопределяет URL (healthcheck.sh передаёт --litellm-url из env) — константа —
# ·   дефолт CLI. · Rev: при появлении модульного конфиг-механизма → убрать локальный дубль.
_LOCAL_PORT_LITELLM: int = 4000

TCP_PROBE_TIMEOUT: int = 3  # сек — socket fallback (эквивалент shell `timeout 3 bash -c "echo > /dev/tcp/..."`)
LITELLM_TIMEOUT: int = 10  # сек — HTTP GET (shell curl --connect-timeout 5 --max-time 10)
DEFAULT_LITELLM_URL: str = f"http://litellm:{_LOCAL_PORT_LITELLM}/health/liveliness"


@dataclass
class DepsResult:
    """Агрегированный вердикт deps-режима.

    ## @purpose  Чистый контракт check_deps → healthy() по required/optional семантике.
    ## @invariants  healthy = pg_ok AND litellm_ok (required); redis_ok — optional (warn only)
    """

    pg_ok: bool
    redis_ok: bool
    litellm_ok: bool

    def healthy(self) -> bool:
        """Вердикт: required (PG + LiteLLM) доступны; Redis не блокирует."""
        return self.pg_ok and self.litellm_ok


# W11: DI-типы checkers (инжектируются тестами; default — модульные функции)
# W11: checker-типы — вызываемость с фактическими аргументами (timeout — дефолт модульных функций;
# DI-фейки тестов принимают (host, port) — 2 аргумента, контракт ниже)
PgChecker = Callable[[str, str | int], bool]
RedisChecker = Callable[[str, str | int], bool]
LitellmChecker = Callable[[str], bool]


# region DATA_CliArgs
@dataclass
class CliArgs:
    """Типизированные аргументы CLI (W11: argparse.Namespace → dataclass-namespace)."""

    pg_host: str = "pgbouncer"
    pg_port: str = "6432"
    redis_host: str = "redis"
    redis_port: str = "6379"
    litellm_url: str = DEFAULT_LITELLM_URL


# endregion DATA_CliArgs


# region FUNC_check_pg
HTTP_OK: int = 200  # успешный HTTP-статус LiteLLM


def check_pg(host: str, port: str | int, timeout: int = TCP_PROBE_TIMEOUT) -> bool:
    """PostgreSQL доступность: pg_isready → fallback TCP socket (эквивалент /dev/tcp).

    ▶ ┌host,port┐ → ◇ pg_isready? rc0 → True │ TCP socket → True │ ⎋ False

    ## @io — ⇥ host, port, timeout → ⎋ bool
    ## @complexity O(1) + 1 subprocess или 1 socket
    """
    if shutil.which("pg_isready"):
        try:
            result = subprocess.run(
                ["pg_isready", "-h", host, "-p", str(port)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if result.returncode == 0:
                logger.info("[IMP:8][deps] PostgreSQL: ok (pg_isready)")
                return True
            logger.info("[IMP:9][deps] PostgreSQL: FAIL (pg_isready)")
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("[IMP:7][deps] pg_isready error — falling back to TCP probe")
        else:
            return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            logger.info("[IMP:8][deps] PostgreSQL: ok (TCP socket)")
            return True
    except OSError:
        logger.info("[IMP:9][deps] PostgreSQL: FAIL (TCP socket)")
        return False


# endregion FUNC_check_pg


# region FUNC_check_redis
def check_redis(host: str, port: str | int, timeout: int = TCP_PROBE_TIMEOUT) -> bool:
    """Redis доступность: redis-cli PING → "PONG" → fallback TCP (warn-only, optional).

    ▶ ┌host,port┐ → ◇ redis-cli? PONG → True │ TCP socket → True │ ⎋ False

    ## @io — ⇥ host, port, timeout → ⎋ bool
    ## @complexity O(1) + 1 subprocess или 1 socket
    """
    if shutil.which("redis-cli"):
        # ⚠️ TRAP[BUG] · 2026-08-25 · LOW · DR-L5 · redis-probe без auth → NOAUTH false-negative
        # · Symptom: requirepass обязателен (T2.0a) — PING без пароля отвечает NOAUTH-error,
        #   PONG-ветка перманентно ложная; зависимость «жива» только по TCP-fallback
        # · Fix: REDISCLI_AUTH из env REDIS_PASSWORD (официальный env redis-cli —
        #   пароль не светится в argv/ps); без пароля в env — прежнее поведение
        # · Prevention: credentialed-контракт PLATFORM_REDIS_URL (DR-H3 fix, тот же аудит)
        probe_env = dict(os.environ)
        if os.environ.get("REDIS_PASSWORD"):
            probe_env["REDISCLI_AUTH"] = os.environ["REDIS_PASSWORD"]
        try:
            result = subprocess.run(
                ["redis-cli", "-h", host, "-p", str(port), "PING"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=probe_env,
            )
            if "PONG" in result.stdout:
                logger.info("[IMP:8][deps] Redis: ok (redis-cli PONG)")
                return True
            logger.info("[IMP:8][deps] Redis: warn (no PONG — optional)")
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("[IMP:7][deps] redis-cli error — falling back to TCP probe")
        else:
            return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            logger.info("[IMP:8][deps] Redis: ok (TCP socket reachable)")
            return True
    except OSError:
        logger.info("[IMP:8][deps] Redis: warn (TCP unreachable — optional)")
        return False


# endregion FUNC_check_redis


# region FUNC_check_litellm
def check_litellm(url: str, timeout: int = LITELLM_TIMEOUT) -> bool:
    """LiteLLM доступность: HTTP GET → 200.

    ▶ ┌url┐ → ○ urllib GET → ◇ 200? True │ ⎋ False

    ## @io — ⇥ url, timeout → ⎋ bool
    ## @complexity O(1) + 1 HTTP запрос
    """
    try:
        # nosec B310 — внутренний LiteLLM healthcheck endpoint (http://litellm:4000, локальная сеть).
        # Паттерн repo-wide (healthcheck_poller/langfuse_projects/service_reload).
        resp = cast("http.client.HTTPResponse", urllib.request.urlopen(url, timeout=timeout))  # nosec B310 — W11: urlopen → Any → HTTPResponse
        with resp:
            ok = resp.status == HTTP_OK
            logger.info("[IMP:%d][deps] LiteLLM: %s (HTTP %s)", 8 if ok else 9, "ok" if ok else "FAIL", resp.status)
            return ok
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        logger.info("[IMP:9][deps] LiteLLM: FAIL (HTTP error or timeout): %s", exc)
        return False


# endregion FUNC_check_litellm


# region FUNC_check_deps
def check_deps(
    pg_host: str,
    pg_port: str | int,
    redis_host: str,
    redis_port: str | int,
    litellm_url: str,
    *,
    check_pg_fn: PgChecker | None = None,
    check_redis_fn: RedisChecker | None = None,
    check_litellm_fn: LitellmChecker | None = None,
) -> DepsResult:
    """Агрегация required/optional зависимостей (DevPlan 119 D6).

    ▶ ┌hosts/ports/url┐ → ○ PG (required) → ○ Redis (optional) → ○ LiteLLM (required) → ⊕ DepsResult

    ## @purpose — deps-ветка healthcheck.sh (48-112): required PG+LiteLLM, optional Redis.
    ## @io — ⇥ pg_host, pg_port, redis_host, redis_port, litellm_url; check_*_fn (injectable, default модульные)
    ##           ⎋ DepsResult
    ## @complexity O(1) — 3 проверки (субпроцессы/сокеты/HTTP)
    ## @invariants — check_*_fn инжектируются для тестов (DI > mocks, tests/AGENTS.md)
    """
    pg_ok = (check_pg_fn or check_pg)(pg_host, pg_port)
    redis_ok = (check_redis_fn or check_redis)(redis_host, redis_port)
    litellm_ok = (check_litellm_fn or check_litellm)(litellm_url)

    result = DepsResult(pg_ok=pg_ok, redis_ok=redis_ok, litellm_ok=litellm_ok)
    if result.healthy():
        logger.info("[IMP:9][deps] All required dependencies ok (PG+LiteLLM)")
    else:
        logger.info("[IMP:9][deps] FAIL: required dependencies not available (PG=%s LiteLLM=%s)", pg_ok, litellm_ok)
    return result


# endregion FUNC_check_deps


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI: `python3 healthcheck_deps.py --pg-host ... --pg-port ... [--redis-*] [--litellm-url ...]`.

    ▶ ┌argv┐ → ○ check_deps → ◇ healthy? exit 0 │ exit 1

    ## @purpose — интерфейс для healthcheck.sh deps-режима (exec python3, exit passthrough).
    ## @io — ⇥ argv → ⎋ int (0 = healthy, 1 = unhealthy)
    ## @invariants — exit 1 при недоступности required (PG/LiteLLM); Redis warn-only
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Hermes-agent deps healthcheck (DevPlan 119 D6)")
    parser.add_argument("--pg-host", default="pgbouncer")
    parser.add_argument("--pg-port", default="6432")
    parser.add_argument("--redis-host", default="redis")
    parser.add_argument("--redis-port", default="6379")
    parser.add_argument("--litellm-url", default=DEFAULT_LITELLM_URL)
    args = parser.parse_args(argv, namespace=CliArgs())  # W11: dataclass-namespace

    result = check_deps(
        args.pg_host,
        args.pg_port,
        args.redis_host,
        args.redis_port,
        args.litellm_url,
    )
    return 0 if result.healthy() else 1


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
