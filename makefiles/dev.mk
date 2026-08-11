# GREP_SUMMARY: dev.mk, dev-hosts, etc-hosts, hosts-manager, dry-run, apply, macos, dev-infra, age-key-backup, DR
# STRUCTURE: ┌dev-hosts target┐ → ◇ APPLY=1? → ⊕ --apply (sudo, атомарно) · → ⊕ --dry-run (default, exit 1 на diff) → ⎋ python3 core/internal/dev_hosts.py ── ┌age-key-backup target┐ → ◇ AGE_RECIPIENT → ⎋ python3 -m core.internal.deploy.age_key_backup
# region MODULE_CONTRACT
## @purpose  Dev-infrastructure targets for the local machine (macOS) — dev-hosts:
##           idempotent /etc/hosts management for the dev FQDN scheme (DevPlan 136 W4, T4.2);
##           age-key-backup: off-node encrypted backup of the AGE master key (DevPlan 147 W2,
##           docs/age-master-key-dr.md §2 — DR-drill W3.1).
## @scope    Included from root Makefile; local-dev targets only (no deployment/CI logic).
##           The business logic lives in core/internal/dev_hosts.py and
##           core/internal/deploy/age_key_backup.py — this file is a thin facade.
## @invariants
##   - Default mode is --dry-run: exit 1 when the managed block differs from /etc/hosts
##     (blocking signal), exit 0 when in sync
##   - APPLY=1 switches to --apply: writes the managed block (sudo for /etc/hosts), idempotent
##   - Env chain mirrors the dev-certs canon (TRAP[BUG] 2026-07-16): env > .env > Python default;
##     `make` does not read .env — recipe-level extraction via grep
##   - Never runs with APPLY=1 implicitly — operator must opt in explicitly
##   - age-key-backup: реципиент — AGE_RECIPIENT env или --recipient флаг; ключ читается
##     локально по env-цепочке node_detect (AGE_SECRET_KEY → … → /etc/age/key.txt);
##     --dry-run по умолчанию БЕЗОПАСЕН (0 мутаций) — реальная выгрузка только без --dry-run
## @rationale Extracted as makefiles/dev.mk per DevPlan 136 §8 file manifest (makefiles/dev.mk —
##            dev-hosts). Separate file keeps helpers.mk (shared dev-utils: venv/templates/
##            dev-certs/dev-metrics) free of the hosts-manager concern and matches the plan's
##            include-split convention (one thematic .mk per concern).
##            Alternative rejected: adding the target to helpers.mk (existing dev-* canon) —
##            helpers.mk would grow a second responsibility (hosts management vs generic
##            helpers) and the plan explicitly prescribes dev.mk. Both options feed
##            generate_entrypoint_manifest.py identically (it globs makefiles/*.mk).
##            age-key-backup добавлен в dev.mk (DevPlan 147 W2): операторская DR-операция
##            локальной машины, канон dev-* (как dev-hosts/dev-certs).
## @changes 2026-08-05 | DevPlan 136 W4 (T4.2) — Created
## @changes 2026-08-11 | DevPlan 147 W2 — +age-key-backup (DR off-node backup, dr.md §2)
# endregion MODULE_CONTRACT

.PHONY: dev-hosts age-key-backup

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

## age-key-backup: Off-node encrypted backup of the AGE master key (dr.md §2, DevPlan 147 W2)
##   Usage: make age-key-backup [AGE_RECIPIENT=<pubkey>] [ARGS="--dry-run"]
##   Реципиент: AGE_RECIPIENT env или --recipient флаг (python3 -m core.internal.deploy.age_key_backup).
##   Ключ читается ЛОКАЛЬНО по env-цепочке node_detect; backup — ТОЛЬКО зашифрованный (sops).
##   Доп. флаги пробрасываются через ARGS (--dry-run/--no-upload/--output-enc/--s3-key).
##   Флаги CLI переопределяют ARGS (приоритет: AGE_RECIPIENT/DRY_RUN/NO_UPLOAD/OUTPUT_ENC/S3_KEY > ARGS).
age-key-backup:
	@echo "[IMP:7][make][age-key-backup] Off-node AGE master key backup (dr.md §2)..."
	@python3 -m core.internal.deploy.age_key_backup \
		$(if $(AGE_RECIPIENT),--recipient '$(AGE_RECIPIENT)',) \
		$(if $(filter 1,$(DRY_RUN)),--dry-run,) \
		$(if $(filter 1,$(NO_UPLOAD)),--no-upload,) \
		$(if $(OUTPUT_ENC),--output-enc '$(OUTPUT_ENC)',) \
		$(if $(S3_KEY),--s3-key '$(S3_KEY)',) \
		$(ARGS)
	@echo "[IMP:9][make][age-key-backup] Done (exit code propagated)"
