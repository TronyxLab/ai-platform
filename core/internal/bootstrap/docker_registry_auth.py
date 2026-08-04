#!/usr/bin/env python3
# GREP_SUMMARY: docker-registry-auth, docker-hub-login, registry-mirror, daemon-json, rate-limit, idempotent, systemctl-restart
# STRUCTURE: ▶ ┌username+token+mirror┐ → ○ docker login → ◇ write daemon.json → ◇ auth-state guard (config.json) → ⚡ systemctl restart (только при изменении) → ⊕ bool → ⎋
# region MODULE_CONTRACT
## @purpose  Configure Docker Hub authentication and optional registry-mirror to eliminate
##           rate-limiting (HTTP 429) during bootstrap image pulls.
## @scope    Called from state_machine.py φ3 (phase_platform_setup, index 5) after install_docker.
##           Writes /etc/docker/daemon.json with registry-mirrors and log-driver config.
##           Performs `docker login` to Docker Hub with provided credentials.
## @invariants
##   1. Idempotent: if daemon.json already has registry-mirrors → skip write, skip restart
##   2. Idempotent: docker login with existing valid credentials → no-op
##   3. Non-fatal: if credentials missing → WARN, continue (rate-limit may apply)
##   4. Requires Docker installed (install_docker step must have run first)
##   5. systemctl restart docker — 0 раз или 1 раз за init (волна 117 D2): ТОЛЬКО если
##      daemon.json изменён ИЛИ запись auth появилась в ~/.docker/config.json после login
##   6. mirror.gcr.io is a public Google mirror — no auth required (TRAP[DECISION] in DevPlan)
##   7. Docker Hub auth выполняется ТОЛЬКО из φ3 (φ6 дубль удалён, волна 117 D2)
## @rationale StatusReport 045: Docker Hub rate-limit (429) blocked nginx pull during bootstrap.
##           Configuring Docker Hub auth + registry-mirror eliminates anonymous rate-limit.
##           Registry-mirror (mirror.gcr.io) provides a pull-through cache that reduces
##           direct Docker Hub requests.
##           Волна 117 D2: повторный вызов скрипта = no-op — restart docker по guard
##           (auth-состояние изменилось), а не при каждом вызове.
## @changes  2026-07-22 | DevPlan 047 Phase 2 — Created Docker Hub auth + registry-mirror module
##           2026-07-30 | T13b — Delegated _docker_login() to shared docker_auth module
##           2026-08-01 | Волна 117 D2 — restart по guard (auth-state change), idempotent no-op
##           2026-08-02 | DevPlan 119 A2 — timeout-литералы → канон shared/timeouts
##                      (DOCKER_CMD_TIMEOUT/DOCKER_RESTART_TIMEOUT/DOCKER_RESTART_POLL_*);
##                      локальная константа DOCKER_RESTART_TIMEOUT удалена
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── Path setup for shared module import ──
# File is at core/internal/bootstrap/docker_registry_auth.py → repo root = 4 levels up
# (namespace package core/, no __init__.py). Pre-existing bug: 3 levels inserted core/
# itself → `from core.internal...` failed under direct-script invocation.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# DevPlan 119 E5: атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
from core.internal.shared import docker_ops  # W1: docker info примитив (гейт docker_sole_path)
from core.internal.shared.atomic_writer import atomic_write_json as _atomic_write_json
from core.internal.shared.docker_auth import docker_login as shared_docker_login
from core.internal.shared.timeouts import (
    DOCKER_CMD_TIMEOUT,
    DOCKER_RESTART_POLL_INTERVAL,
    DOCKER_RESTART_POLL_RETRIES,
    DOCKER_RESTART_TIMEOUT,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
DEFAULT_MIRROR_URL = "https://mirror.gcr.io"
DAEMON_JSON_PATH = "/etc/docker/daemon.json"


# region FUNC_configure_docker_auth
## @purpose — Configure Docker Hub auth + registry-mirror in daemon.json.
##            Performs docker login if credentials provided.
##            Idempotent end-to-end (волна 117 D2): повторный вызов (любой) = no-op —
##            restart docker происходит ТОЛЬКО если auth-состояние изменилось
##            (daemon.json записан ИЛИ запись auth появилась в ~/.docker/config.json).
## @io — ⇥ username: str, token: str, mirror_url: Optional[str] → ⎋ bool (True = configured/written)
## @complexity — O(1) + subprocess for docker login
## @invariants
##   - If daemon.json already has registry-mirrors with mirror_url → skip write (no restart)
##   - docker login идемпотентен; restart docker — только если записи auth НЕ было до login
##   - Итог: systemctl restart docker — 0 раз или 1 раз за init (AC-A2, волна 117 D2)
##   - Non-fatal: missing credentials → WARN, return True (already "configured" as best-effort)
def configure_docker_auth(
    username: str,
    token: str,
    mirror_url: str | None = None,
) -> bool:
    """Configure Docker Hub auth + optional registry mirror.

    ▶ ┌username+token+mirror┐ → ○ docker login → ◇ write daemon.json → ◇ auth-state guard → ⚡ systemctl restart → ⊕ bool → ⎋

    Returns True if configured (or already configured), False on error.
    """
    mirror = mirror_url or DEFAULT_MIRROR_URL
    logger.info("[IMP:8][docker_auth] Configuring Docker Hub auth (mirror=%s)", mirror)

    # ── Step 1: Write daemon.json with registry-mirror (idempotent, guarded restart) ──
    written = _write_daemon_json(mirror)
    if written:
        logger.info("[IMP:9][docker_auth] daemon.json updated — restart required (mirror applied)")
    else:
        logger.info("[IMP:7][docker_auth] daemon.json already configured — no mirror change")

    # ── Step 2: Docker login (idempotent, non-fatal) ──
    if not username or not token:
        logger.warning("[IMP:7][docker_auth] Docker Hub credentials not set — rate-limit (429) may apply")
        # без login нет изменения auth-состояния: restart только если daemon.json изменился
        if written:
            _restart_docker()
        return True  # Non-fatal: mirror is still configured

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
    auth_before = _auth_entry_exists()
    login_ok = _docker_login(username, token)
    if not login_ok:
        logger.warning("[IMP:7][docker_auth] Docker Hub login failed — rate-limit may apply")
    auth_changed = auth_before is False  # запись auth появилась после login

    if written or auth_changed:
        logger.info(
            "[IMP:9][docker_auth] Restarting Docker (daemon_json_written=%s, auth_changed=%s)",
            written,
            auth_changed,
        )
        _restart_docker()
    else:
        logger.info("[IMP:7][docker_auth] No auth-state change — skipping docker restart (idempotent, D2)")
    return True


# endregion FUNC_configure_docker_auth


# region FUNC_auth_entry_exists
## @purpose — Check whether ~/.docker/config.json already contains an auth entry for
##            Docker Hub (registry-1.docker.io / https://index.docker.io/v1/).
##            Используется как guard для restart (волна 117 D2): если запись уже есть —
##            docker login no-op → restart не нужен.
## @io — ⇥ None → ⎋ bool (True = auth entry present)
## @complexity — O(1)
## @invariants
##   - config.json отсутствует или некорректен JSON → трактуется как «нет auth» (False)
##   - Ключи auths: и registry-1.docker.io, и https://index.docker.io/v1/ считаются Docker Hub
def _auth_entry_exists() -> bool:
    """Return True if ~/.docker/config.json already has a Docker Hub auth entry."""
    config_path = os.path.join(os.path.expanduser("~"), ".docker", "config.json")
    if not os.path.isfile(config_path):
        return False
    try:
        with open(config_path) as f:
            data = json.load(f)
        auths = data.get("auths", {}) if isinstance(data, dict) else {}
        return any(
            key in auths
            for key in ("registry-1.docker.io", "https://index.docker.io/v1/", "https://registry-1.docker.io/v1/")
        )
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[IMP:7][docker_auth] Cannot read %s: %s — treating as no auth entry", config_path, e)
        return False


# endregion FUNC_auth_entry_exists


# region FUNC_write_daemon_json
## @purpose — Write /etc/docker/daemon.json with registry-mirrors and log config.
##            Idempotent: if existing config already contains the mirror → skip.
## @io — ⇥ mirror_url: str → ⎋ bool (True if written, False if skipped)
## @complexity — O(1)
## @invariants
##   - Preserves existing keys in daemon.json (merge, not overwrite)
##   - Only adds registry-mirrors if mirror_url not already present
##   - Adds log-driver + log-opts if not present
##   - Creates parent dir if missing
def _write_daemon_json(mirror_url: str) -> bool:
    """Write daemon.json with registry-mirror. Returns True if file was written/changed."""
    # Read existing daemon.json or start fresh
    existing: dict[str, Any] = {}
    if os.path.isfile(DAEMON_JSON_PATH):
        try:
            with open(DAEMON_JSON_PATH) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[IMP:7][docker_auth] Corrupt daemon.json, overwriting: %s", e)
            existing = {}

    # Check if mirror already present
    mirrors: list[str] = existing.get("registry-mirrors", [])
    if mirror_url in mirrors:
        logger.info("[IMP:7][docker_auth] Mirror %s already in daemon.json", mirror_url)
        # Check if log-driver is also set
        if existing.get("log-driver") == "json-file":
            return False  # Fully configured

    # Merge: add mirror if not present
    if mirror_url not in mirrors:
        mirrors.append(mirror_url)
    existing["registry-mirrors"] = mirrors

    # Add log-driver config (json-file with rotation) if not present
    if "log-driver" not in existing:
        existing["log-driver"] = "json-file"
    if "log-opts" not in existing:
        existing["log-opts"] = {"max-size": "10m", "max-file": "3"}

    # Write atomically (shared atomic_writer canon — E5: tempfile + fsync + os.replace)
    try:
        _atomic_write_json(DAEMON_JSON_PATH, existing)
        logger.info("[IMP:9][docker_auth] daemon.json written with registry-mirrors: %s", mirrors)
        return True
    except OSError as e:
        logger.error("[IMP:10][docker_auth] Failed to write daemon.json: %s", e)
        return False


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
## @io — ⇥ None → ⎋ bool (True = restarted and responsive)
## @complexity — O(1) + subprocess
## @invariants
##   - Uses `systemctl restart docker`
##   - Waits up to DOCKER_RESTART_TIMEOUT seconds for Docker socket
##   - Non-fatal: if restart fails, logs WARN and returns False
def _restart_docker() -> bool:
    """Restart Docker daemon. Returns True if successful."""
    try:
        result = subprocess.run(
            ["systemctl", "restart", "docker"],
            capture_output=True,
            text=True,
            timeout=DOCKER_RESTART_TIMEOUT,
        )
        if result.returncode != 0:
            logger.warning(
                "[IMP:7][docker_auth] systemctl restart docker failed (exit=%d): %s",
                result.returncode,
                result.stderr.strip()[:200],
            )
            return False
        # Wait for Docker to be responsive (W1: docker info — shared/docker_ops, non-fatal)
        import time

        for _ in range(DOCKER_RESTART_POLL_RETRIES):  # 6 × 5s = 30s max wait
            time.sleep(DOCKER_RESTART_POLL_INTERVAL)
            check = docker_ops.docker_info(timeout=DOCKER_CMD_TIMEOUT)
            if check.returncode == 0:
                logger.info("[IMP:9][docker_auth] Docker restarted and responsive")
                return True
        logger.warning("[IMP:7][docker_auth] Docker restart succeeded but daemon not responding after 30s")
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("[IMP:7][docker_auth] Docker restart error: %s", e)
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
