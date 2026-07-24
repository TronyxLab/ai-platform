# GREP_SUMMARY: helpers.mk, venv, templates-check, templates-render, dev-certs, provision, test-inventory-sync, help, _get_all_profiles
# STRUCTURE: ┌venv setup┐ → ◇ templates-check → ◇ templates-render → ◇ dev-certs → ◇ provision → ◇ test-inventory-sync → ◇ help → ◇ _get_all_profiles
# region MODULE_CONTRACT
## @purpose  Utility/helper targets — venv, templates, dev-certs, provision, test-inventory-sync, help
## @scope    Included from root Makefile; no deployment or CI logic
## @invariants
##   - venv target creates virtualenv on first use (idempotent: second call = no-op)
##   - templates-check exits 1 on unresolved placeholders (dry-run before templates-render)
##   - dev-certs respects CERT_BACKEND env (auto/mkcert/openssl)
## @rationale Makefile include-split W4-E4: helpers isolated from business logic targets
# endregion MODULE_CONTRACT

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

.PHONY: venv templates-check templates-render dev-certs provision provision-llm test-inventory-sync help _get_all_profiles

venv: $(VENV)

## templates-check: Dry-run render all templates from manifest — exit 0 if all resolvable, 1 with diagnostic at unresolved
templates-check:
	@echo "[IMP:7][make][templates-check] Checking template resolvability..."
	@core/internal/template-engine.sh check --verbose
	@echo "[IMP:9][make][templates-check] All templates resolvable"

## templates-render: Render all templates per manifest
templates-render:
	@echo "[IMP:7][make][templates-render] Rendering templates from manifest..."
	@core/internal/template-engine.sh render-all
	@echo "[IMP:9][make][templates-render] All templates rendered"

## dev-certs: Generate or validate dev SSL certificates (idempotent)
##   Delegates to core/modules/nginx/generate-dev-certs.sh
##   CERT_BACKEND env: auto (default), mkcert, openssl
## # ⚠️ TRAP[BUG] · 2026-07-16 · HIGH · PLATFORM_DOMAIN from .env · Root: env-chain break — `make` не читает .env, → контекстный домен молча терялся · Fix: recipe-level extraction (grep PLATFORM_DOMAIN= из .env) · Prevention: contract-проверка через DEV_CERTS_DIR+tmp
dev-certs:
	@echo "[IMP:7][make][dev-certs] Ensuring dev SSL certificates..."
	@_env_pd="$$(grep -E '^PLATFORM_DOMAIN=' "$(_platform_root)/.env" 2>/dev/null | tail -n1 | cut -d= -f2-)"; \
	PLATFORM_DOMAIN="$${PLATFORM_DOMAIN:-$${_env_pd:-ai-platform.local}}" \
	bash $(_platform_root)/core/modules/nginx/generate-dev-certs.sh
	@echo "[IMP:9][make][dev-certs] Dev certificates check complete"

## provision-llm: Provision LiteLLM virtual keys for all LLM consumers
##   Delegates to core/entrypoints/provision-llm.sh → key_provisioner.py
##   Exports LITELLM_MASTER_KEY from .env if not already set
##   Uses 127.0.0.1:4000 when called from host (Docker DNS name litellm not resolvable outside Docker)
provision-llm:
	@echo "[IMP:7][make][provision-llm] Provisioning LiteLLM virtual keys..."
	@export LITELLM_MASTER_KEY="$${LITELLM_MASTER_KEY:-$$(grep -E '^LITELLM_MASTER_KEY=' "$(_platform_root)/.env" 2>/dev/null | tail -n1 | cut -d= -f2-)}"; \
	$(_platform_root)/core/entrypoints/provision-llm.sh --base-url http://127.0.0.1:4000
	@echo "[IMP:9][make][provision-llm] Virtual key provisioning complete"

## provision: Provision environment (networks, volumes, CI env) from platform-env.yaml
##   Usage: make provision [SCOPE=all|networks,volumes]
##   SCOPE=all (default): networks + volumes + CI env vars
##   SCOPE=networks,volumes — comma-separated → повторяемые --scope флаги
##   Delegates to core/internal/provision-environment.sh (idempotent)
## ## @invariants
##   - SCOPE=networks,volumes → разворачивается в --scope networks --scope volumes
##   - Скрипт поддерживает массив scopes=() (multi-scope), НЕ скаляр
provision:
	@echo "[IMP:7][make][provision] Provisioning environment (SCOPE=$(or $(SCOPE),all))..."
	@_scopes="$(or $(SCOPE),all)"; \
	_scope_args=""; \
	for _s in $${_scopes//,/ }; do \
		_scope_args="$${_scope_args} --scope $$_s"; \
	done; \
	bash $(_platform_root)/core/internal/provision-environment.sh \
		$${_scope_args} \
		--platform-env $(_platform_root)/platform-env.yaml
	@echo "[IMP:9][make][provision] Environment provisioned"

## test-inventory-sync: Regenerate tests/test_inventory.yaml from pytest --collect-only
test-inventory-sync:
	@echo "[IMP:7][make][test-inventory-sync] Regenerating test inventory..."
	@$(PYTHON) tests/tools/sync_inventory.py
	@echo "[IMP:9][make][test-inventory-sync] Inventory regenerated"

## help: Show this help
help:
	@grep -E '^## [a-zA-Z][a-zA-Z0-9_-]*: .*$$' $(MAKEFILE_LIST) | \
		sed 's/^## /  /' | column -t -s ':'

# COMPOSE_PROFILES source of truth: all 13 Docker modules with profiles.
# Used by CI and production scripts for ${VAR:?error} compatibility (DevPlan 033 Option A).
_get_all_profiles:
	@echo "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page"
