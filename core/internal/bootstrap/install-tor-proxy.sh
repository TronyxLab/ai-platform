#!/usr/bin/env bash
# GREP_SUMMARY: install-tor-proxy thin-facade python3 -m install_tor_proxy idempotent tor privoxy systemd apt iptables cron-healthcheck
# STRUCTURE: guard root → exec python3 -m core.internal.bootstrap.install_tor_proxy → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 127 W1): установка Tor + Privoxy (apt, torrc, Privoxy config,
##           systemd enable/start, firewall iptables, cron-healthcheck, circuit verify) — в
##           core/internal/bootstrap/install_tor_proxy.py (оркестрация; конфиг-генерация в
##           tor_setup 119 D2 / tor_transport 118 E1 / privoxy_config 119 D3).
## @scope    Called once during bootstrap phase φ1 (tor_enabled); safe to re-run (идемпотентно)
## @invariants
##   - <50 LOC thin facade — языковая политика: бизнес-логика в Python
##   - guard root + exec python3 -m (паттерн install-docker.sh)
##   - Аргументы --tor-bridges-file/--skip-tor-verify пробрасываются как есть (byte-compat)
## @rationale Strangler W1 (S2): 321 LOC → фасад; оркестрация тестируема в Python (DI subprocess)
## @changes  2026-08-04 | DevPlan 127 W1 — сокращён до фасада (было 321 LOC)
# endregion MODULE_CONTRACT

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "[IMP:10][tor-install][main] ERROR: must run as root" >&2
    exit 1
fi

exec python3 -m core.internal.bootstrap.install_tor_proxy "$@"
