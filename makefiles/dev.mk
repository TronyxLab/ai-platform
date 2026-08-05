# GREP_SUMMARY: dev.mk, dev-hosts, etc-hosts, hosts-manager, dry-run, apply, macos, dev-infra
# STRUCTURE: ┌dev-hosts target┐ → ◇ APPLY=1? → ⊕ --apply (sudo, атомарно) · → ⊕ --dry-run (default, exit 1 на diff) → ⎋ python3 core/internal/dev_hosts.py
# region MODULE_CONTRACT
## @purpose  Dev-infrastructure targets for the local machine (macOS) — currently dev-hosts:
##           idempotent /etc/hosts management for the dev FQDN scheme (DevPlan 136 W4, T4.2).
## @scope    Included from root Makefile; local-dev targets only (no deployment/CI logic).
##           The business logic lives in core/internal/dev_hosts.py — this file is a thin facade.
## @invariants
##   - Default mode is --dry-run: exit 1 when the managed block differs from /etc/hosts
##     (blocking signal), exit 0 when in sync
##   - APPLY=1 switches to --apply: writes the managed block (sudo for /etc/hosts), idempotent
##   - Env chain mirrors the dev-certs canon (TRAP[BUG] 2026-07-16): env > .env > Python default;
##     `make` does not read .env — recipe-level extraction via grep
##   - Never runs with APPLY=1 implicitly — operator must opt in explicitly
## @rationale Extracted as makefiles/dev.mk per DevPlan 136 §8 file manifest (makefiles/dev.mk —
##            dev-hosts). Separate file keeps helpers.mk (shared dev-utils: venv/templates/
##            dev-certs/dev-metrics) free of the hosts-manager concern and matches the plan's
##            include-split convention (one thematic .mk per concern).
##            Alternative rejected: adding the target to helpers.mk (existing dev-* canon) —
##            helpers.mk would grow a second responsibility (hosts management vs generic
##            helpers) and the plan explicitly prescribes dev.mk. Both options feed
##            generate_entrypoint_manifest.py identically (it globs makefiles/*.mk).
## @changes 2026-08-05 | DevPlan 136 W4 (T4.2) — Created
# endregion MODULE_CONTRACT

.PHONY: dev-hosts

## dev-hosts: Управление /etc/hosts dev-блоком (default dry-run exit 1 на diff; APPLY=1 → apply)
##   Держит локальный /etc/hosts синхронным с dev-FQDN схемой (DevPlan 136 W4, T4.1-T4.2):
##   server_names vhost_renderer (dev-mode <project>.<suffix>) + base-домены из SAN dev-сертификатов.
##   Маркер-блок `# BEGIN ai-platform dev-hosts` / `# END ai-platform dev-hosts`; чужие строки
##   /etc/hosts НЕ трогаются (идемпотентность: повторный APPLY=1 = no-op).
##   Env-цепочка (canon dev-certs): env > .env > Python default (NODE_NAME/NODE_CONFIGS_DIR/
##   PLATFORM_DOMAIN/DEV_CERTS_DIR). Default --dry-run блокирует make при diff (exit 1).
dev-hosts:
	@echo "[IMP:7][make][dev-hosts] Checking /etc/hosts dev block (APPLY=$${APPLY:-0})..."
	@_env_file="$(_platform_root)/.env"; \
	_env_val() { grep -E "^$$1=" "$$_env_file" 2>/dev/null | tail -n1 | cut -d= -f2-; }; \
	export NODE_NAME="$${NODE_NAME:-$$(_env_val NODE_NAME)}"; \
	export NODE_CONFIGS_DIR="$${NODE_CONFIGS_DIR:-$$(_env_val NODE_CONFIGS_DIR)}"; \
	export PLATFORM_DOMAIN="$${PLATFORM_DOMAIN:-$$(_env_val PLATFORM_DOMAIN)}"; \
	export DEV_DOMAIN_SUFFIX="$${DEV_DOMAIN_SUFFIX:-$$(_env_val DEV_DOMAIN_SUFFIX)}"; \
	export DEV_CERTS_DIR="$${DEV_CERTS_DIR:-$(_platform_root)/core/modules/nginx/dev-certs}"; \
	ARGS="--dry-run"; \
	if [ "$${APPLY:-}" = "1" ]; then ARGS="--apply"; fi; \
	python3 $(_platform_root)/core/internal/dev_hosts.py $$ARGS
	@echo "[IMP:9][make][dev-hosts] Done (exit code propagated)"
