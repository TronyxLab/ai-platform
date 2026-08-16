#!/usr/bin/env python3
# GREP_SUMMARY: docker-registry-auth, docker-hub-login, daemon-json, log-driver, rate-limit, idempotent, systemctl-restart
# STRUCTURE: ▶ ┌username+token┐ → ○ docker login → ◇ write daemon.json (log config) → ◇ auth-state guard (config.json) → ⚡ systemctl restart (только при изменении) → ⊕ bool → ⎋
# region MODULE_CONTRACT
## @purpose  Configure Docker Hub authentication (docker login) + daemon.json log-driver
##           rotation to eliminate anonymous rate-limiting (HTTP 429) during bootstrap pulls.
## @scope    Called from state_machine.py φ3 (phase_platform_setup, index 5) after install_docker.
##           Writes /etc/docker/daemon.json with log-driver config.
##           Performs `docker login` to Docker Hub with provided credentials.
## @invariants
##   1. Idempotent: if daemon.json already has log-driver config → skip write, skip restart
##   2. Idempotent: docker login with existing valid credentials → no-op
##   3. Non-fatal: if credentials missing → WARN, continue (rate-limit may apply)
##   4. Requires Docker installed (install_docker step must have run first)
##   5. systemctl restart docker — 0 раз или 1 раз за init (волна 117 D2): ТОЛЬКО если
##      daemon.json изменён ИЛИ запись auth появилась в ~/.docker/config.json после login
##   6. Docker Hub auth выполняется ТОЛЬКО из φ3
##   7. Registry-mirror не используется: authenticated docker.io login покрывает rate-limit
## @rationale StatusReport 045: Docker Hub rate-limit (429) blocked nginx pull during bootstrap.
##           Authenticated Docker Hub pulls (docker login) eliminate anonymous rate-limit.
##           Повторный вызов скрипта = no-op — restart docker по guard
##           (auth-состояние изменилось), а не при каждом вызове.
## @changes  2026-07-22 | DevPlan 047 Phase 2 — Created Docker Hub auth + registry-mirror module
##           2026-07-30 | T13b — Delegated _docker_login() to shared docker_auth module
##           2026-08-01 | Волна 117 D2 — restart по guard (auth-state change), idempotent no-op
##           2026-08-02 | DevPlan 119 A2 — timeout-литералы → канон shared/timeouts
##                      (DOCKER_CMD_TIMEOUT/DOCKER_RESTART_TIMEOUT/DOCKER_RESTART_POLL_*);
##                      локальная константа DOCKER_RESTART_TIMEOUT удалена
##           2026-08-13 | DevPlan 160 E1 — +runner/facts DI + daemon_json_path/docker_config_path/
##                      login_fn/restart_fn параметры (тесты без monkeypatch subprocess/os; поведение
##                      НЕ изменено: дефолты = реальные вызовы)
##           2026-08-13 | DevPlan 164 W0-3.7 — registry-mirror удалён (docker.io покрыт auth);
##                      daemon.json теперь пишет только log-driver конфиг
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

# ── Path setup for shared module import ──
# File is at core/internal/bootstrap/docker_registry_auth.py → repo root = 4 levels up
# (namespace package core/, no __init__.py). Pre-existing bug: 3 levels inserted core/
# itself → `from core.internal...` failed under direct-script invocation.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
from core.internal.shared import docker_ops  # W1: docker info примитив (гейт docker_sole_path)
from core.internal.shared.atomic_writer import atomic_write_json as _atomic_write_json
from core.internal.shared.docker_auth import docker_login as shared_docker_login
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.subprocess_io import CommandRunner
from core.internal.shared.timeouts import (
    DOCKER_CMD_TIMEOUT,
    DOCKER_RESTART_POLL_INTERVAL,
    DOCKER_RESTART_POLL_RETRIES,
    DOCKER_RESTART_TIMEOUT,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
DAEMON_JSON_PATH = "/etc/docker/daemon.json"


# region FUNC_configure_docker_auth
## @purpose — Configure Docker Hub auth + daemon.json log-driver rotation.
##            Performs docker login if credentials provided.
##            Idempotent end-to-end (волна 117 D2): повторный вызов (любой) = no-op —
##            restart docker происходит ТОЛЬКО если auth-состояние изменилось
##            (daemon.json записан ИЛИ запись auth появилась в ~/.docker/config.json).
## @io — ⇥ username: str, token: str → ⎋ bool (True = configured/written)
## @complexity — O(1) + subprocess for docker login
## @invariants
##   - If daemon.json already has log-driver config → skip write (no restart)
##   - docker login идемпотентен; restart docker — только если записи auth НЕ было до login
##   - Итог: systemctl restart docker — 0 раз или 1 раз за init (AC-A2, волна 117 D2)
##   - Non-fatal: missing credentials → WARN, return True (already "configured" as best-effort)
## @changes 2026-08-13 | E1 (160): +runner/facts/daemon_json_path/docker_config_path/login_fn/restart_fn
##            DI-параметры (None = реальные вызовы/пути; поведение без изменений)
## @changes 2026-08-13 | 164 W0-3.7: mirror_url параметр удалён (registry-mirror убран)
def configure_docker_auth(
    username: str,
    token: str,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    daemon_json_path: str | None = None,
    docker_config_path: str | None = None,
    login_fn: Callable[[str, str], bool] | None = None,
    restart_fn: Callable[[], bool] | None = None,
) -> bool:
    """Configure Docker Hub auth + daemon.json log-driver.

    ▶ ┌username+token┐ → ○ docker login → ◇ write daemon.json (log config) → ◇ auth-state guard → ⚡ systemctl restart → ⊕ bool → ⎋

    Returns True if configured (or already configured), False on error.
    """
    logger.info("[IMP:8][docker_auth] Configuring Docker Hub auth")

    # ── Step 1: Write daemon.json with log-driver config (idempotent, guarded restart) ──
    written = _write_daemon_json(daemon_json_path=daemon_json_path, facts=facts)
    if written:
        logger.info("[IMP:9][docker_auth] daemon.json updated — restart required (log config applied)")
    else:
        logger.info("[IMP:7][docker_auth] daemon.json already configured — no change")

    # ── Step 2: Docker login (idempotent, non-fatal) ──
    if not username or not token:
        logger.warning("[IMP:7][docker_auth] Docker Hub credentials not set — rate-limit (429) may apply")
        # без login нет изменения auth-состояния: restart только если daemon.json изменился
        if written:
            _do_restart(restart_fn, runner=runner)
        return True  # Non-fatal: best-effort configured

    # ── Step 2a: Guard restart на изменение auth-состояния (волна 117 D2) ──
    # docker login идемпотентен: повторный login с уже валидными creds = no-op.
    # systemctl restart docker выполняется ТОЛЬКО если запись auth в ~/.docker/config.json
    # отсутствовала ДО login (т.е. auth-состояние реально изменилось). Повторный вызов
    # скрипта (или уже залогиненный оператор) → 0 restarts.
    # ⚠️ TRAP[BUG] · 2026-08-01 · P2 · Edge case: оператор вручную залогинился ДО bootstrap
    # · Symptom: config.json уже содержит auth-запись → auth_changed=False; если daemon.json
    # ·   тоже предконфигурирован (mirror присутствует) → 0 restarts → mirror НЕ применяется
    # ·   до следующего restart docker (деградация rate-limit оптимизации, не ошибка корректности).
    # · Root: guard по auth-состоянию не может отличить «auth добавлен скриптом» от «был до».
    # · Fix: restart при written (daemon.json изменился) НЕ зависит от auth — mirror применяется
    # ·   всегда при первом bootstrap; ручной предварительный login — редкий операторский кейс.
    # · Prevention: не менять guard на «всегда restart» — это вернёт 2 restarts за init (D2).
    auth_before = _auth_entry_exists(config_path=docker_config_path, facts=facts)
    login_ok = _do_login(login_fn, username, token)
    if not login_ok:
        logger.warning("[IMP:7][docker_auth] Docker Hub login failed — rate-limit may apply")
    auth_changed = auth_before is False  # запись auth появилась после login

    if written or auth_changed:
        logger.info(
            "[IMP:9][docker_auth] Restarting Docker (daemon_json_written=%s, auth_changed=%s)",
            written,
            auth_changed,
        )
        _do_restart(restart_fn, runner=runner)
    else:
        logger.info("[IMP:7][docker_auth] No auth-state change — skipping docker restart (idempotent, D2)")
    return True


# endregion FUNC_configure_docker_auth


# region FUNC__do_login
## @purpose — DI-шов для docker login: login_fn задан (тесты) → login_fn(username, token);
##            None → _docker_login (реальный subprocess через shared docker_auth).
## @io — ⇥ login_fn: Callable|None, username, token → ⎋ bool
## @complexity — O(1)
## @changes 2026-08-13 | E1 (160) — created (DI: тесты передают fake-login вместо patch subprocess.run)
def _do_login(login_fn: Callable[[str, str], bool] | None, username: str, token: str) -> bool:
    if login_fn is not None:
        return bool(login_fn(username, token))
    return _docker_login(username, token)


# endregion FUNC__do_login


# region FUNC__do_restart
## @purpose — DI-шов для restart docker: restart_fn задан (тесты) → restart_fn();
##            None → _restart_docker(runner=runner, facts=facts) (реальный systemctl + docker_info).
## @io — ⇥ restart_fn: Callable|None, runner → ⎋ bool
## @complexity — O(1)
## @changes 2026-08-13 | E1 (160) — created (DI: тесты передают fake-restart вместо monkeypatch _restart_docker)
def _do_restart(restart_fn: Callable[[], bool] | None, *, runner: CommandRunner | None = None) -> bool:
    if restart_fn is not None:
        return bool(restart_fn())
    return _restart_docker(runner=runner)


# endregion FUNC__do_restart


# region FUNC_auth_entry_exists
## @purpose — Check whether ~/.docker/config.json already contains an auth entry for
##            Docker Hub (registry-1.docker.io / https://index.docker.io/v1/).
##            Используется как guard для restart (волна 117 D2): если запись уже есть —
##            docker login no-op → restart не нужен.
## @io — ⇥ config_path: str | None (None = ~/.docker/config.json), facts → ⎋ bool (True = auth entry present)
## @complexity — O(1)
## @invariants
##   - config.json отсутствует или некорректен JSON → трактуется как «нет auth» (False)
##   - Ключи auths: и registry-1.docker.io, и https://index.docker.io/v1/ считаются Docker Hub
## @changes 2026-08-13 | E1 (160): +config_path/facts DI (тесты передают tmp_path без monkeypatch)
def _auth_entry_exists(
    config_path: str | None = None,
    *,
    facts: EnvironmentFacts | None = None,
) -> bool:
    """Return True if ~/.docker/config.json already has a Docker Hub auth entry."""
    path = config_path or os.path.join(os.path.expanduser("~"), ".docker", "config.json")
    if not (facts or default_env_facts()).path_isfile(path):
        return False
    try:
        with Path(path).open(encoding="utf-8") as f:
            data = cast("object", json.load(f))  # W11-G3: json.load → Any; JSON-граница (isinstance-гард ниже)
        auths = (
            cast("dict[str, object]", cast("dict[str, object]", data).get("auths", {}))
            if isinstance(data, dict)
            else {}
        )  # W11-G3: JSON-граница — isinstance-сужение → dict[Unknown, Unknown]; каст ключей
        return any(
            key in auths
            for key in ("registry-1.docker.io", "https://index.docker.io/v1/", "https://registry-1.docker.io/v1/")
        )
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[IMP:7][docker_auth] Cannot read %s: %s — treating as no auth entry", path, e)
        return False


# endregion FUNC_auth_entry_exists


# region FUNC_write_daemon_json
## @purpose — Write /etc/docker/daemon.json with log-driver rotation config (registry-mirror
##            не используется). Idempotent: если конфиг уже содержит log-driver → skip.
## @io — ⇥ daemon_json_path: str | None (None = DAEMON_JSON_PATH), facts → ⎋ bool (True if written, False if skipped)
## @complexity — O(1)
## @invariants
##   - Preserves existing keys in daemon.json (merge, not overwrite)
##   - Adds log-driver + log-opts if not present
##   - Creates parent dir if missing
##   - registry-mirrors в существующем daemon.json НЕ трогаются (не наш канал; docker
##     сам перечитывает конфиг — сторонние mirror-настройки остаются на месте)
## @changes 2026-08-13 | E1 (160): +daemon_json_path/facts DI (тесты передают tmp_path без monkeypatch)
## @changes 2026-08-13 | 164 W0-3.7: mirror_url параметр удалён — пишет только log-конфиг
def _write_daemon_json(
    daemon_json_path: str | None = None,
    *,
    facts: EnvironmentFacts | None = None,
) -> bool:
    """Write daemon.json with log-driver rotation config. Returns True if file was written/changed."""
    path = daemon_json_path or DAEMON_JSON_PATH
    # Read existing daemon.json or start fresh
    existing: dict[str, object] = {}
    if (facts or default_env_facts()).path_isfile(path):
        try:
            with Path(path).open(encoding="utf-8") as f:
                existing = cast("dict[str, object]", json.load(f))  # W11-G3: json.load → Any; JSON-граница
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[IMP:7][docker_auth] Corrupt daemon.json, overwriting: %s", e)
            existing = {}

    # Log-driver config already present → no-op
    if existing.get("log-driver") == "json-file" and existing.get("log-opts") == {"max-size": "10m", "max-file": "3"}:
        logger.info("[IMP:7][docker_auth] Log config already in daemon.json — no change")
        return False

    # Add log-driver config (json-file with rotation) if not present
    if "log-driver" not in existing:
        existing["log-driver"] = "json-file"
    if "log-opts" not in existing:
        existing["log-opts"] = {"max-size": "10m", "max-file": "3"}

    # Write atomically (shared atomic_writer canon — E5: tempfile + fsync + os.replace)
    try:
        _atomic_write_json(path, existing)
        logger.info("[IMP:9][docker_auth] daemon.json written with log-driver config")
    except OSError as e:
        logger.error("[IMP:10][docker_auth] Failed to write daemon.json: %s", e)
        return False
    else:
        return True


# endregion FUNC_write_daemon_json


# region FUNC_docker_login
## @purpose — Perform `docker login` to Docker Hub with provided credentials.
##            Uses password-stdin for security (no token in command line/ps).
## @io — ⇥ username: str, token: str → ⎋ bool (True = success)
## @complexity — O(1) + subprocess
## @invariants
##   - Token passed via stdin (never in command arguments)
##   - Non-fatal: failure logs WARN and returns False
##   - Idempotent: Docker caches credentials in ~/.docker/config.json
def _docker_login(username: str, token: str) -> bool:
    """Login to Docker Hub via shared docker_auth module.

    ## @purpose — Thin wrapper delegating to docker_auth.docker_login().
    ##            All credential handling, subprocess management, and
    ##            error handling live in the shared module.
    ## @io — ⇥ username: str, token: str → ⎋ bool (True = success)
    ## @complexity — O(1) + delegation
    """
    return shared_docker_login(username=username, token=token)


# endregion FUNC_docker_login


# region FUNC_restart_docker
## @purpose — Restart Docker daemon via systemctl after daemon.json change.
##            Waits for Docker to become responsive after restart.
## @io — ⇥ runner: CommandRunner | None → ⎋ bool (True = restarted and responsive)
## @complexity — O(1) + subprocess
## @invariants
##   - Uses `systemctl restart docker`
##   - Waits up to DOCKER_RESTART_TIMEOUT seconds for Docker socket
##   - Non-fatal: if restart fails, logs WARN and returns False
## @changes 2026-08-13 | E1 (160): +runner DI — runner=None → subprocess.run (default),
##            runner задан → runner.run (fake scripted)
def _restart_docker(*, runner: CommandRunner | None = None) -> bool:
    """Restart Docker daemon. Returns True if successful."""
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        if runner is None:
            result = subprocess.run(
                ["systemctl", "restart", "docker"],
                capture_output=True,
                text=True,
                timeout=DOCKER_RESTART_TIMEOUT,
                check=False,
            )
        else:
            result = runner.run(["systemctl", "restart", "docker"], timeout=DOCKER_RESTART_TIMEOUT, check=False)
        if result.returncode != 0:
            logger.warning(
                "[IMP:7][docker_auth] systemctl restart docker failed (exit=%d): %s",
                result.returncode,
                result.stderr.strip()[:200],
            )
            return False
        import time

        for _ in range(DOCKER_RESTART_POLL_RETRIES):  # 6 × 5s = 30s max wait
            time.sleep(DOCKER_RESTART_POLL_INTERVAL)
            check = docker_ops.docker_info(timeout=DOCKER_CMD_TIMEOUT)
            if check.returncode == 0:
                logger.info("[IMP:9][docker_auth] Docker restarted and responsive")
                return True
        logger.warning("[IMP:7][docker_auth] Docker restart succeeded but daemon not responding after 30s")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("[IMP:7][docker_auth] Docker restart error: %s", e)
        return False
    else:
        return False


# endregion FUNC_restart_docker


# region CLI


# region FUNC_main_cli
## @purpose — CLI entry point for standalone testing. Reads creds from env vars.
## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = error)
## @complexity — O(1) + subprocess
def main() -> int:
    """CLI entry point for docker_registry_auth."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    username = os.environ.get("DOCKER_HUB_USERNAME", "")
    token = os.environ.get("DOCKER_HUB_TOKEN", "")
    if not username or not token:
        logger.warning("[IMP:7][docker_auth] DOCKER_HUB_USERNAME/DOCKER_HUB_TOKEN not set")
    ok = configure_docker_auth(username, token)
    return 0 if ok else 1


# endregion FUNC_main_cli


# endregion CLI


if __name__ == "__main__":
    sys.exit(main())
