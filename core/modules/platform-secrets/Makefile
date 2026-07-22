# GREP_SUMMARY: platform-secrets Makefile systemd module-system install enable status restart logs
# Module: platform-secrets (systemd install_type — system module)
# STRUCTURE: SERVICE_NAME → include ../../templates/module-system.mk → lifecycle via systemctl/journalctl
# region MODULE_CONTRACT
## @purpose  Systemd module Makefile for platform-secrets service lifecycle
## @scope    Provides install/status/restart/logs targets via systemd (no Docker compose)
## @invariants
##   - Uses module-system.mk template (not Docker module.mk)
##   - SERVICE_NAME := platform-secrets
##   - All targets use systemctl/journalctl — no docker compose commands
##   - Replaces old pattern: include module.mk + docker target overrides
## @rationale  platform-secrets is a systemd oneshot service, not a Docker container.
##             Per D3, module-system.mk provides the correct lifecycle contract for
##             install_type: system modules. The old include+override pattern (TRAP[DECISION]
##             Rev condition) is now resolved.
## @changes
##   2026-07-18 · Switched from module.mk → module-system.mk per D3 (system-module contract)
##              · Removed docker target overrides (now in module-system.mk template)
##              · module-system.mk created, old include+override pattern resolved
## @see ../../templates/module-system.mk
# endregion MODULE_CONTRACT

SERVICE_NAME := platform-secrets
include ../../templates/module-system.mk
