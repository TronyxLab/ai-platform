# GREP_SUMMARY: module-system-mk Makefile fragment systemd service install enable status restart logs
# STRUCTURE: SERVICE_NAME + SYSTEMD_DIR → targets: install ──→ cp unit → daemon-reload → enable → restart ──→ IMP:9; status ──→ systemctl status --no-pager; restart ──→ systemctl restart; logs ──→ journalctl -u --tail
# region MODULE_CONTRACT
## @purpose  Reusable Makefile fragment for systemd module lifecycle management (install_type: system)
## @scope    Include in system-type module Makefiles after defining SERVICE_NAME
## @usage    Include this fragment after declaring required variables:
##              SERVICE_NAME := my-systemd-service
##              include ../../templates/module-system.mk
## @invariants
##   - SERVICE_NAME must be defined before include (defaults to MODULE_DIR basename)
##   - All targets use systemctl/journalctl — NO docker compose
##   - Targets: install, status, restart, logs — NO build/up/backup/down (docker semantics)
##   - All targets log at IMP:7 minimum, critical paths at IMP:9
##   - install target copies *.service files to SYSTEMD_DIR, daemon-reloads, enables, restarts
## @rationale  System-type modules (install_type: system) like platform-secrets need systemd-native
##   lifecycle operations instead of docker compose. Created per D3 — alternative contract to
##   module.mk for services managed by systemd (oneshot, forking, simple types).
## @changes
##   2026-07-18 · Created per D3 system-module contract (DevPlan 011)
# endregion MODULE_CONTRACT

# ── Overridable variables (modules set these before include) ──
# SHELL + MODULE_DIR — общий канон (DevPlan 172 W2.5: shared-vars.mk, единая формула)
include ../../templates/shared-vars.mk
SERVICE_NAME ?= $(notdir $(realpath $(MODULE_DIR)))
SYSTEMD_DIR  ?= /etc/systemd/system

.PHONY: install status restart logs help

## install: Install/update systemd unit
install: ## Install/update $(SERVICE_NAME) systemd unit
	@echo "[IMP:7][$(SERVICE_NAME)-sysmk][install] Installing $(SERVICE_NAME) systemd unit"
	cp $(MODULE_DIR)/*.service $(SYSTEMD_DIR)/
	systemctl daemon-reload
	systemctl enable $(SERVICE_NAME)
	systemctl restart $(SERVICE_NAME)
	@echo "[IMP:9][$(SERVICE_NAME)-sysmk][install] $(SERVICE_NAME) installed and started"

## status: Show service status
status: ## Show $(SERVICE_NAME) systemd service status
	@echo "[IMP:7][$(SERVICE_NAME)-sysmk][status] Service status:"
	@systemctl status $(SERVICE_NAME) --no-pager 2>/dev/null || echo "  not installed"

## restart: Restart service via systemd
restart: ## Restart $(SERVICE_NAME) via systemd
	@echo "[IMP:7][$(SERVICE_NAME)-sysmk][restart] Restarting $(SERVICE_NAME)"
	systemctl restart $(SERVICE_NAME)
	@echo "[IMP:9][$(SERVICE_NAME)-sysmk][restart] $(SERVICE_NAME) restarted"

## logs: Show service logs
logs: ## Show $(SERVICE_NAME) systemd journal logs
	@echo "[IMP:7][$(SERVICE_NAME)-sysmk][logs] Service logs (last 50 lines):"
	@journalctl -u $(SERVICE_NAME) --no-pager -n 50 2>/dev/null || echo "  no logs"

## help: Show available targets
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
