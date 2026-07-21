# GREP_SUMMARY: bootstrap.mk, bootstrap-node, node-update, converge, render-vhosts
# STRUCTURE: ┌variables┐ → ◇ bootstrap-node → ◇ node-update → ◇ converge → ◇ render-vhosts
# region MODULE_CONTRACT
## @purpose  Bootstrap and node lifecycle targets — bootstrap-node, node-update, converge, render-vhosts
## @scope    Included from root Makefile; delegates to core/entrypoints/
## @invariants
##   - bootstrap-node must be idempotent (AGENTS.md Invariant 6)
##   - converge preserves exit-code semantics (0=clean, 1=warnings, 2=errors)
## @rationale Makefile include-split W4-E4: bootstrap targets isolated from CI/scaffold
# endregion MODULE_CONTRACT

.PHONY: bootstrap-node node-update converge render-vhosts

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
converge:
	@echo "[IMP:7][make][converge] Running node reconciliation..."
	@bash core/entrypoints/converge.sh --node $(NODE) \
		$(if $(DRY_RUN),--dry-run,) \
		$(if $(filter 1,$(RECONCILE)),--reconcile)
	@echo "[IMP:9][make][converge] Node reconciliation complete"

## render-vhosts: Regenerate Nginx vhost configs from node.yaml
##   Usage: make render-vhosts NODE=<name>
##   Delegates to core/internal/scaffold/add-vhost.sh --render-all --node
render-vhosts:
	@echo "[IMP:7][make][render-vhosts] Generating vhost configs from node.yaml..."
	@bash core/internal/scaffold/add-vhost.sh --render-all --node $(NODE) --node-configs-dir $(NODE_CONFIGS_DIR)
	@echo "[IMP:9][make][render-vhosts] Vhost generation complete"
