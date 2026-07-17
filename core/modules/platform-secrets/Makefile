# GREP_SUMMARY: platform-secrets Makefile module-contract systemd service install enable
# Module: platform-secrets (systemd install_type — not a docker compose module)
# STRUCTURE: MODULE_NAME → include ../../templates/module.mk — systemd module, targets overridden for systemctl
# region MODULE_CONTRACT
## @purpose  Minimal Makefile for platform-secrets module (systemd-type)
## @scope    Provides module lifecycle targets per core/modules/AGENTS.md contract
## @invariants
##   - include ../../templates/module.mk first for canonical targets
##   - MODULE_NAME set to platform-secrets
##   - Docker compose targets overridden after include with systemctl wrappers
## @rationale  Every module needs a Makefile per module contract — even systemd modules.
##             Created per TASK-6C Phase 6.
# endregion MODULE_CONTRACT

MODULE_NAME := platform-secrets

# Must include module.mk first for contract compliance, then override
# Docker-specific targets with systemctl wrappers for this systemd module.
# GNU Make uses the LAST recipe for each target, so our overrides win.
include ../../templates/module.mk

# ── Systemd module overrides (Docker targets → systemctl wrappers) ──
# Platform-secrets is a systemd oneshot service, not a Docker container.
# These overrides ensure `make start/stop/status/logs` work gracefully
# and don't attempt to run docker compose on a non-Docker module.

.PHONY: start stop restart status logs

start:
	@echo "[IMP:7][platform-secrets-mk][start] Starting platform-secrets service (systemd)"
	@systemctl start platform-secrets 2>/dev/null || \
		echo "[IMP:9][platform-secrets-mk][start] Service not installed — run install.sh first"

stop:
	@echo "[IMP:7][platform-secrets-mk][stop] Stopping platform-secrets service (systemd)"
	@systemctl stop platform-secrets 2>/dev/null || true

restart: stop start

status:
	@echo "[IMP:7][platform-secrets-mk][status] Platform-secrets systemd status:"
	@systemctl status platform-secrets --no-pager 2>/dev/null || echo "  not installed"

logs:
	@echo "[IMP:7][platform-secrets-mk][logs] Platform-secrets journal:"
	@journalctl -u platform-secrets --no-pager -n 50 2>/dev/null || echo "  no logs"
