# GREP_SUMMARY: hermes-agent Makefile start stop restart status logs help
# region MODULE_CONTRACT
## @purpose  Module lifecycle management for hermes-agent Docker module
## @scope    Called by platform users per role; sudo-whitelist enforces per-role restrictions
## @invariants
##   - All docker compose calls use docker-compose.base.yml
##   - env-file loaded from SECRETS_ENV_FILE (default: /run/platform/secrets.env)
##   - All targets log at IMP:7 minimum
## @rationale Targets match standard module lifecycle; minimal override — include standard template
# endregion MODULE_CONTRACT

MODULE_NAME := hermes-agent

include ../../templates/module.mk
