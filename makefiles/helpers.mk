# GREP_SUMMARY: helpers.mk, venv, templates-check, templates-render, dev-certs, dev-metrics, provision, test-inventory-sync, help, _get_all_profiles
# STRUCTURE: ┌venv setup┐ → ◇ templates-check → ◇ templates-render → ◇ dev-certs → ◇ dev-metrics → ◇ provision → ◇ test-inventory-sync → ◇ help → ◇ _get_all_profiles
# region MODULE_CONTRACT
## @purpose  Utility/helper targets — venv, templates, dev-certs, dev-metrics, provision, test-inventory-sync, help
## @scope    Included from root Makefile; no deployment or CI logic
## @invariants
##   - venv target creates virtualenv on first use (idempotent: second call = no-op)
##   - templates-check exits 1 on unresolved placeholders (dry-run before templates-render)
##   - dev-certs respects CERT_BACKEND env (auto/mkcert/openssl)
##   - dev-metrics (D-12, DevPlan 130 W1): dev-локали без cron metrics — генерирует
##     status-metrics.json (тот же экспортёр, что нодовый cron) + .htpasswd-platform
##     (secrets_manager htpasswd CLI); пути из .env (STATUS_METRICS_JSON/HTPASSWD_FILE);
##     повторный запуск безопасен (fresh metrics + salt-идемпотентный htpasswd)
## @rationale Makefile include-split W4-E4: helpers isolated from business logic targets
# endregion MODULE_CONTRACT

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

.PHONY: venv templates-check templates-render dev-certs dev-metrics provision provision-llm test-inventory-sync help _get_all_profiles

venv: $(VENV)

## templates-check: Dry-run render all templates from manifest — exit 0 if all resolvable, 1 with diagnostic at unresolved
templates-check:
	@echo "[IMP:7][make][templates-check] Checking template resolvability..."
	@python3 $(_platform_root)/core/internal/template_engine.py check --verbose
	@echo "[IMP:9][make][templates-check] All templates resolvable"

## templates-render: Render all templates per manifest
templates-render:
	@echo "[IMP:7][make][templates-render] Rendering templates from manifest..."
	@python3 $(_platform_root)/core/internal/template_engine.py render-all
	@echo "[IMP:9][make][templates-render] All templates rendered"

## dev-certs: Generate or validate dev SSL certificates (idempotent)
##   Delegates to core/modules/nginx/dev_cert_generator.py (DevPlan 099)
##   CERT_BACKEND env: auto (default), mkcert, openssl
## # ⚠️ TRAP[BUG] · 2026-07-16 · HIGH · PLATFORM_DOMAIN from .env · Root: env-chain break — `make` не читает .env, → контекстный домен молча терялся · Fix: recipe-level extraction (grep PLATFORM_DOMAIN= из .env) · Prevention: contract-проверка через DEV_CERTS_DIR+tmp
## # DevPlan 116 T3 (U-16): третий уровень fallback — platform-env.yaml env_defaults.PLATFORM_DOMAIN
## #   (SoT), приоритет: env → .env → platform-env.yaml. Хардкод ai-platform.local удалён.
dev-certs:
	@echo "[IMP:7][make][dev-certs] Ensuring dev SSL certificates..."
	@_env_pd="$$(grep -E '^PLATFORM_DOMAIN=' "$(_platform_root)/.env" 2>/dev/null | tail -n1 | cut -d= -f2-)"; \
	PLATFORM_DOMAIN="$${PLATFORM_DOMAIN:-$${_env_pd:-$$(python3 "$(_platform_root)/core/internal/scripts/yaml_query.py" --file "$(_platform_root)/platform-env.yaml" --get env_defaults.PLATFORM_DOMAIN)}}" \
	DEV_CERTS_DIR="$${DEV_CERTS_DIR:-$(_platform_root)/core/modules/nginx/dev-certs}" \
	DEV_CERTS_LIVE_ROOT="$${DEV_CERTS_LIVE_ROOT:-$(_platform_root)/core/modules/nginx/dev-certs}" \
	python3 $(_platform_root)/core/modules/nginx/dev_cert_generator.py
	@echo "[IMP:9][make][dev-certs] Dev certificates check complete"

## dev-metrics: Generate dev status-metrics.json + .htpasswd-platform (idempotent, D-12)
##   Dev-локали (macOS) не имеют нодового cron (/etc/cron.d/platform-metrics) — файлы,
##   которые на ноде обновляются раз в минуту, здесь генерируются вручную.
##   Вызывает ТОТ ЖЕ экспортёр, что cron (platform_export_metrics.py, node-фасад
##   platform-export-metrics.sh) + htpasswd через secrets_manager CLI (DevPlan 102).
##   Пути/креды — из .env (STATUS_METRICS_JSON/HTPASSWD_FILE/PLATFORM_MASTER_*), как dev-certs.
##   Идемпотентность: status-metrics.json перегенерируется (свежесть — цель);
##   .htpasswd-platform НЕ перезаписывается при неизменных кредах (salt-идемпотентность,
##   TRAP[BUG] 2026-07-31 в htpasswd.py).
## # ⚠️ `make` не читает .env — recipe-level extraction (паттерн TRAP[BUG] dev-certs 2026-07-16):
## #   env > .env > fail-fast (STATUS_METRICS_JSON/HTPASSWD_FILE обязательны).
dev-metrics:
	@echo "[IMP:7][make][dev-metrics] Generating dev metrics + htpasswd..."
	@_env_file="$(_platform_root)/.env"; \
	_env_val() { grep -E "^$$1=" "$$_env_file" 2>/dev/null | tail -n1 | cut -d= -f2-; }; \
	export STATUS_METRICS_JSON="$${STATUS_METRICS_JSON:-$$(_env_val STATUS_METRICS_JSON)}"; \
	export HTPASSWD_FILE="$${HTPASSWD_FILE:-$$(_env_val HTPASSWD_FILE)}"; \
	export NODE_NAME="$${NODE_NAME:-$$(_env_val NODE_NAME)}"; \
	export NODE_CONFIGS_DIR="$${NODE_CONFIGS_DIR:-$$(_env_val NODE_CONFIGS_DIR)}"; \
	export PLATFORM_MASTER_EMAIL="$${PLATFORM_MASTER_EMAIL:-$$(_env_val PLATFORM_MASTER_EMAIL)}"; \
	export PLATFORM_MASTER_PASSWORD="$${PLATFORM_MASTER_PASSWORD:-$$(_env_val PLATFORM_MASTER_PASSWORD)}"; \
	: "$${STATUS_METRICS_JSON:?STATUS_METRICS_JSON not set — укажи в .env (см. .env.example, RC-сессия 121)}"; \
	: "$${HTPASSWD_FILE:?HTPASSWD_FILE not set — укажи в .env (см. .env.example, RC-сессия 121)}"; \
	mkdir -p "$$(dirname "$$STATUS_METRICS_JSON")" "$$(dirname "$$HTPASSWD_FILE")"; \
	echo "[IMP:8][make][dev-metrics] Exporting metrics → $$STATUS_METRICS_JSON (node=$${NODE_NAME:-unknown})"; \
	python3 -m core.internal.healthcheck.platform_export_metrics; \
	echo "[IMP:9][make][dev-metrics] status-metrics.json regenerated"; \
	if [[ -n "$${PLATFORM_MASTER_EMAIL:-}" && -n "$${PLATFORM_MASTER_PASSWORD:-}" ]]; then \
		python3 $(_platform_root)/core/internal/bootstrap/lifecycle/secrets_manager.py htpasswd \
			--email "$$PLATFORM_MASTER_EMAIL" --password "$$PLATFORM_MASTER_PASSWORD" \
			--htpasswd-file "$$HTPASSWD_FILE"; \
		echo "[IMP:9][make][dev-metrics] .htpasswd-platform ensured (idempotent)"; \
	else \
		echo "[IMP:8][make][dev-metrics] PLATFORM_MASTER_EMAIL/PASSWORD not set — htpasswd skipped"; \
	fi
	@echo "[IMP:9][make][dev-metrics] Dev metrics + htpasswd complete"

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
##   ⚠️ ЕДИНСТВЕННАЯ точка регенерации (single-source, DevPlan 116 B11 T6, U-79):
##   - CI (push-gate/platform-test) НЕ вызывает test-inventory-sync
##   - make fix-gate вызывает generate-manifests (НЕ inventory)
##   - гейт tests/gates/test_gate_test_inventory.py делает СВОЙ --collect-only
##     (намеренный anti-tamper дубль T18 — НЕ рефакторить в shared)
##   - тест test_no_second_inventory_regeneration (гейт) ловит добавление второго вызова
test-inventory-sync:
	@echo "[IMP:7][make][test-inventory-sync] Regenerating test inventory..."
	@$(PYTHON) tests/tools/sync_inventory.py
	@echo "[IMP:9][make][test-inventory-sync] Inventory regenerated"

## help: Show this help
help:
	@grep -E '^## [a-zA-Z][a-zA-Z0-9_-]*: .*$$' $(MAKEFILE_LIST) | \
		sed 's/^## /  /' | column -t -s ':'

# COMPOSE_PROFILES — SoT: core/platform-infra.yaml env_defaults (DevPlan 116 T2, U-02).
# Runtime-чтение через yaml_query.py (dotted-ключи) — хардкод-копии устранены;
# parity-гейт test_gate_profiles_parity проверяет отсутствие копий вне allowlist.
_get_all_profiles:
	@echo "$$(python3 core/internal/scripts/yaml_query.py --file core/platform-infra.yaml --get env_defaults.COMPOSE_PROFILES)"
