#!/usr/bin/env bash
# GREP_SUMMARY: install-docker thin-facade python3 -m docker_installer idempotent docker compose-plugin apt no-ports
# STRUCTURE: guard root → exec python3 -m core.internal.bootstrap.docker_installer → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 118 E2): установка Docker Engine + Compose plugin (пакеты,
##           daemon.json live-restore, systemd override, verify 2375/2376) — в
##           core/internal/bootstrap/docker_installer.py.
## @scope    Called once during bootstrap phase φ1; safe to re-run on already-provisioned nodes
## @invariants
##   - <10 LOC thin facade — языковая политика: бизнес-логика в Python
##   - Docker daemon ports (2375/2376) NEVER opened (verify в Python)
## @rationale Strangler E2: apt/systemd-оркестрация + verify → Python (тестируемо)
## @changes  2026-08-02 | DevPlan 118 E2 — сокращён до фасада (было 218 LOC)
# endregion MODULE_CONTRACT

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "[IMP:10][install-docker][main] ERROR: must run as root" >&2
    exit 1
fi

exec python3 -m core.internal.bootstrap.docker_installer
