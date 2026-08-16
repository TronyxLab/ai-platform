# GREP_SUMMARY: scaffold.mk, new-project, new-context, adopt-project, remove-project, project-sync-env, project-list, project-status
# STRUCTURE: ┌variables┐ → ◇ new-project → ◇ new-context → ◇ project-sync-env → ◇ remove-project → ◇ adopt-project → ◇ project-list → ◇ project-status
# region MODULE_CONTRACT
## @purpose  Scaffold targets — project/context creation, removal, adoption, listing
## @scope    Included from root Makefile; delegates to core/entrypoints/scaffold.sh
## @invariants
##   - new-project is the ONLY way to create a project (AGENTS.md glossary rule)
##   - remove-project must be safe (compose down without -v, no data loss)
## @rationale Makefile include-split W4-E4: scaffold targets isolated from CI/build
# endregion MODULE_CONTRACT

.PHONY: new-project new-context project-sync-env remove-project adopt-project project-list project-status

## new-project: Create a new project from template
##   Usage: make new-project NAME=<name> TEMPLATE=<template>
##   Delegates to core/entrypoints/scaffold.sh new-project
new-project:
	@echo "[IMP:7][make][new-project] Creating project NAME=$(NAME) from TEMPLATE=$(TEMPLATE)..."
	@if [[ -z "$(NAME)" ]]; then \
		echo "[IMP:9][make][new-project] ERROR: NAME not set — usage: make new-project NAME=<name> [TEMPLATE=<template>]" >&2; \
		exit 1; \
	fi
	@$(_platform_root)/core/entrypoints/scaffold.sh new-project "$(NAME)" "$(TEMPLATE)"
	@echo "[IMP:9][make][new-project] Project created"

## new-context: Create a new deployment context
##   Usage: make new-context NODE=<name>
##   Delegates to core/entrypoints/scaffold.sh new-context
new-context:
	@echo "[IMP:7][make][new-context] Creating context NODE=$(NODE)..."
	@if [[ -z "$(NODE)" ]]; then \
		echo "[IMP:9][make][new-context] ERROR: NODE not set — usage: make new-context NODE=<name>" >&2; \
		exit 1; \
	fi
	@$(_platform_root)/core/entrypoints/scaffold.sh new-context "$(NODE)"
	@echo "[IMP:9][make][new-context] Context created"

## project-sync-env: Sync .env.platform + AI-PLATFORM.md (DevPlan 133)
##   Usage: make project-sync-env [NAME=<project_name>] [DOMAIN=<domain>] [PROJECT_DIR=<project_dir>]
##   Delegates to core/entrypoints/scaffold.sh sync-env
##   PROJECT_DIR: генерирует .env.platform в директории проекта и пересобирает AI-PLATFORM.md
##   PROJECT: алиас PROJECT_DIR (GENERATED-проектные Makefile передают PROJECT=$(CURDIR) —
##   v1.0.1 TRAP[BUG] Фазы 3: без алиаса sync-env падал «--yaml is required»)
project-sync-env:
	@echo "[IMP:7][make][project-sync-env] Syncing .env.platform..."
	@$(_platform_root)/core/entrypoints/scaffold.sh sync-env \
		$(if $(NAME),--name '$(NAME)') \
		$(if $(DOMAIN),--domain '$(DOMAIN)') \
		$(if $(PROJECT_DIR),--project-dir '$(PROJECT_DIR)') \
		$(if $(PROJECT),--project-dir '$(PROJECT)')
	@echo "[IMP:9][make][project-sync-env] Sync complete"

## remove-project: Remove project from lifecycle (safe — no data loss)
##   Usage: make remove-project NAME=<name> [NODE=<node>]
##   Delegates to core/entrypoints/scaffold.sh remove-project
remove-project:
	@echo "[IMP:7][make][remove-project] Removing project NAME=$(NAME)..."
	@if [[ -z "$(NAME)" ]]; then \
		echo "[IMP:9][make][remove-project] ERROR: NAME not set — usage: make remove-project NAME=<name>" >&2; \
		exit 1; \
	fi
	@$(_platform_root)/core/entrypoints/scaffold.sh remove-project --name "$(NAME)" $(if $(NODE),--node '$(NODE)')
	@echo "[IMP:9][make][remove-project] Remove complete"

## adopt-project: Adopt existing project into platform lifecycle
##   Usage: make adopt-project DIR=<project_dir> [NAME=<name>] [DOMAIN=<domain>]
##   Delegates to core/entrypoints/scaffold.sh adopt-project
adopt-project:
	@echo "[IMP:7][make][adopt-project] Adopting project DIR=$(DIR)..."
	@if [[ -z "$(DIR)" ]]; then \
		echo "[IMP:9][make][adopt-project] ERROR: DIR not set — usage: make adopt-project DIR=<project_dir>" >&2; \
		exit 1; \
	fi
	@$(_platform_root)/core/entrypoints/scaffold.sh adopt-project --dir "$(DIR)" \
		$(if $(NAME),--name '$(NAME)') \
		$(if $(ORG),--org '$(ORG)') \
		$(if $(NODE),--node '$(NODE)') \
		$(if $(DOMAIN),--domain '$(DOMAIN)') \
		$(if $(FORCE),--force)
	@echo "[IMP:9][make][adopt-project] Adopt complete"

## project-list: List registered projects from local node.yaml
##   Usage: make project-list [NODE=<node>]
##   Delegates to core/entrypoints/scaffold.sh list
project-list:
	@echo "[IMP:7][make][project-list] Listing projects..."
	@$(_platform_root)/core/entrypoints/scaffold.sh list $(if $(NODE),--node '$(NODE)')
	@echo "[IMP:9][make][project-list] List complete"

## project-status: Query live status of project(s) on target node
##   Usage: make project-status NAME=<name> [NODE=<node>]
##   Delegates to core/entrypoints/scaffold.sh status
project-status:
	@echo "[IMP:7][make][project-status] Querying project status..."
	@if [[ -z "$(NAME)" ]]; then \
		echo "[IMP:9][make][project-status] ERROR: NAME not set — usage: make project-status NAME=<name> [NODE=<node>]" >&2; \
		exit 1; \
	fi
	@$(_platform_root)/core/entrypoints/scaffold.sh status --name "$(NAME)" $(if $(NODE),--node '$(NODE)')
	@echo "[IMP:9][make][project-status] Status query complete"
