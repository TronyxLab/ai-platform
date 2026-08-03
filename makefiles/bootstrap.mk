# GREP_SUMMARY: bootstrap.mk, bootstrap-node, node-update, converge, render-vhosts, deploy-context
# STRUCTURE: ┌variables┐ → ◇ bootstrap-node → ◇ node-update → ◇ converge → ◇ render-vhosts → ◇ deploy-context
# region MODULE_CONTRACT
## @purpose  Bootstrap and node lifecycle targets — bootstrap-node, node-update, converge, render-vhosts, deploy-context
## @scope    Included from root Makefile; delegates to core/entrypoints/
## @invariants
##   - bootstrap-node must be idempotent (AGENTS.md Invariant 6)
##   - converge: warnings (rc=1) не роняют make (exit 0), errors (rc=2) → exit 2 (make не может вернуть 1)
##   - deploy-context is idempotent (skips healthy projects)
## @rationale Makefile include-split W4-E4: bootstrap targets isolated from CI/scaffold.
##            DevPlan 047: added deploy-context target for standalone context project deploy.
# endregion MODULE_CONTRACT

.PHONY: bootstrap-node node-update converge render-vhosts deploy-context

## bootstrap-node: Idempotent node bootstrap
##   Usage: make bootstrap-node [NODE=<name>] [AGE_SECRET_KEY_FILE=<file>] [DRY_RUN=1] [AUTO_RECONCILE=1]
##   Variables:
##     NODE               (optional) Node name to bootstrap; auto-detected from
##                        /opt/node-configs/ if not specified (on VPS)
##     AGE_SECRET_KEY_FILE (optional) Path to AGE secret key file
##     DRY_RUN            (optional) Set to 1 for dry-run mode (no SCP/SSH)
##     AUTO_RECONCILE     (optional) Set to 1 for auto-recovery of stub projects after bootstrap (W4)
##   Delegates to core/entrypoints/bootstrap.sh → internal bootstrap orchestrator
bootstrap-node:
	@echo "[IMP:9][make][bootstrap-node] Bootstrapping node NODE=$(NODE)..."
	@PLATFORM_ROOT="$(_platform_root)" $(_platform_root)/core/entrypoints/bootstrap.sh \
		$(if $(NODE),--node '$(NODE)') \
		--resolve \
		$(if $(AGE_SECRET_KEY_FILE),--age-secret-key-file '$(AGE_SECRET_KEY_FILE)') \
		$(if $(filter 1,$(AUTO_RECONCILE)),--auto-reconcile) \
		$(if $(filter 1,$(DRY_RUN)),--dry-run)
	@echo "[IMP:9][make][bootstrap-node] Bootstrap complete"

## node-update: Update an already-provisioned node (CI regular update)
##   Usage: make node-update NODE=<name> [AGE_SECRET_KEY_FILE=<file>] [DRY_RUN=1] [RECONCILE=1]
##   RECONCILE=1: after update + converge, reconcile stub projects (W4)
##   Delegates to core/entrypoints/node-update.sh → internal/bootstrap/node-lifecycle.sh --mode update
##     5-step flow: verify_core → provision --scope networks --scope volumes → deploy docker modules
##     → deploy system modules → healthcheck
##   Variables:
##     NODE               Node name to update (required)
##     AGE_SECRET_KEY_FILE (optional) Path to AGE secret key file
##     DRY_RUN            (optional) Set to 1 for dry-run mode (print SSH command only)
##     RECONCILE          (optional) Set to 1 for stub project reconciliation after update (W4)
node-update:
	@echo "[IMP:9][make][node-update] Updating node NODE=$(NODE)..."
	@if [[ -z "$(NODE)" ]]; then \
		echo "[IMP:9][make][node-update] ERROR: NODE not set — usage: make node-update NODE=<name> [AGE_SECRET_KEY_FILE=<file>] [DRY_RUN=1] [RECONCILE=1]" >&2; \
		exit 1; \
	fi
	@PLATFORM_ROOT="$(_platform_root)" $(_platform_root)/core/entrypoints/node-update.sh \
		--node "$(NODE)" \
		$(if $(AGE_SECRET_KEY_FILE),--age-secret-key-file '$(AGE_SECRET_KEY_FILE)') \
		$(if $(filter 1,$(RECONCILE)),--reconcile) \
		$(if $(filter 1,$(DRY_RUN)),--dry-run)
	@echo "[IMP:9][make][node-update] Node update complete"

## converge: Idempotent reconcile — конвергирует ноду с desired state из node.yaml
##   Usage: make converge NODE=<name> [DRY_RUN=1] [RECONCILE=1]
##   RECONCILE=1: after converge, reconcile stub projects (deploy if GHCR image exists) (W4)
##   Delegates to core/entrypoints/converge.sh
##   ⚠️ TRAP[BUG] · 2026-07-31 · P1 · PLATFORM_ROOT не экспортировался → REMOTE converge падал
##   · Symptom: `make converge NODE=<host>` → "bash: /opt/platform/core/internal/bootstrap/converge.sh:
##   ·   No such file or directory" — E2E DevPlan 095 T8 на test-VPS.
##   · Root: без PLATFORM_ROOT remote-cmd.sh build_converge_ssh_cmd резолвил remote_root=/opt/platform
##   ·   (core на VPS лежит по mirror-пути PLATFORM_ROOT — см. remote-cmd.sh TRAP[BUG] PLATFORM_ROOT).
##   ·   node-update (строкой ниже) PLATFORM_ROOT экспортирует — converge НЕТ (несоответствие).
##   · Fix: PLATFORM_ROOT="$(_platform_root)" — та же обвязка, что у bootstrap-node/node-update.
##   · Prevention: любой remote-таргет (bootstrap-node/node-update/converge) экспортирует PLATFORM_ROOT.
converge:
	@echo "[IMP:7][make][converge] Running node reconciliation..."
	@PLATFORM_ROOT="$(_platform_root)" bash $(_platform_root)/core/entrypoints/converge.sh --node $(NODE) \
		$(if $(DRY_RUN),--dry-run,) \
		$(if $(filter 1,$(RECONCILE)),--reconcile) \
		|| _conv_rc=$$?; \
	if [ "$${_conv_rc:-0}" -eq 1 ]; then \
		echo "[IMP:8][make][converge] Warnings (rc=1) — non-fatal drift, exit 0" >&2; \
		exit 0; \
	fi; \
	exit "$${_conv_rc:-0}"
	@echo "[IMP:9][make][converge] Node reconciliation complete"

## render-vhosts: Regenerate Nginx vhost configs from node.yaml
##   Usage: make render-vhosts NODE=<name>
##   NODE_CONFIGS_DIR default: $(_platform_root)/node-configs (DevPlan 116 B1 T8, U-55)
##   Delegates to core/internal/scaffold/add-vhost.sh --render-all --node
render-vhosts:
	@echo "[IMP:7][make][render-vhosts] Generating vhost configs from node.yaml..."
	@bash $(_platform_root)/core/internal/scaffold/add-vhost.sh --render-all --node $(NODE) --node-configs-dir $(NODE_CONFIGS_DIR)
	@echo "[IMP:9][make][render-vhosts] Vhost generation complete"

# DevPlan 116 B1 T8 (U-55): NODE_CONFIGS_DIR с дефолтом — make render-vhosts работает
# без явного NODE_CONFIGS_DIR (0 set -u фейлов на незаданной переменной).
# Канон: PLATFORM_ROOT/node-configs (3-candidate path в node-resolver.sh, первый кандидат).
NODE_CONFIGS_DIR ?= $(_platform_root)/node-configs

## deploy-context: Deploy all projects of a context on a bootstrapped node (DevPlan 047)
##   Usage: make deploy-context NODE=<name> [CONTEXT=<context>]
##   Standalone invocation of the deploy_context phase from bootstrap pipeline.
##   Deploys all projects from node.yaml where context matches, using ghcr.io pull
##   primary with build-on-node fallback. Idempotent: skips healthy projects.
##   Delegates to core/entrypoints/deploy-context.sh → context_deployer.py
deploy-context:
	@echo "[IMP:9][make][deploy-context] Deploying context projects NODE=$(NODE) CONTEXT=$(CONTEXT)..."
	@if [[ -z "$(NODE)" ]]; then \
		echo "[IMP:10][make][deploy-context] ERROR: NODE not set — usage: make deploy-context NODE=<name> [CONTEXT=<ctx>]" >&2; \
		exit 1; \
	fi
	@PLATFORM_ROOT="$(_platform_root)" $(_platform_root)/core/entrypoints/deploy-context.sh \
		--node "$(NODE)" \
		$(if $(CONTEXT),--context "$(CONTEXT)")
	@echo "[IMP:9][make][deploy-context] Context deploy complete"
