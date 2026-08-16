# GREP_SUMMARY: helpers.mk, venv, templates-check, templates-render, dev-certs, dev-metrics, provision, help, _get_all_profiles, env_reader
# STRUCTURE: ┌venv setup┐ → ◇ templates-check → ◇ templates-render → ◇ dev-certs → ◇ dev-metrics → ◇ provision → ◇ help → ◇ _get_all_profiles
# region MODULE_CONTRACT
## @purpose  Utility/helper targets — venv, templates, dev-certs, dev-metrics, provision, help
## @scope    Included from root Makefile; no deployment or CI logic
## @invariants
##   - venv target creates virtualenv on first use (idempotent: second call = no-op)
##   - templates-check exits 1 on unresolved placeholders (dry-run before templates-render)
##   - dev-certs respects CERT_BACKEND env (auto/mkcert/openssl)
##   - dev-metrics (D-12, DevPlan 130 W1): dev-локали без cron metrics — генерирует
##     status-metrics.json (тот же экспортёр, что нодовый cron) + .htpasswd-platform
##     (secrets_manager htpasswd CLI); пути из .env (STATUS_METRICS_JSON/HTPASSWD_FILE);
##     повторный запуск безопасен (fresh metrics + salt-идемпотентный htpasswd)
##   - Чтение .env — через core.internal.shared.env_reader (DevPlan 172 W2.3) —
##     0 inline grep/cut в рецептах (языковая политика: shell — тонкий фасад)
## @rationale Makefile include-split W4-E4: helpers isolated from business logic targets
# endregion MODULE_CONTRACT

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

.PHONY: venv templates-check templates-render dev-certs dev-metrics provision provision-llm help help-all _get_all_profiles

venv: $(VENV)

## templates-check: Dry-run render all templates from manifest — exit 0 if all resolvable, 1 with diagnostic at unresolved
templates-check:
	@echo "[IMP:7][make][templates-check] Checking template resolvability..."
	@$(PYTHON) -m core.internal.template_engine check --verbose
	@echo "[IMP:9][make][templates-check] All templates resolvable"

## templates-render: Render all templates per manifest
templates-render:
	@echo "[IMP:7][make][templates-render] Rendering templates from manifest..."
	@$(PYTHON) -m core.internal.template_engine render-all
	@echo "[IMP:9][make][templates-render] All templates rendered"

## dev-certs: Generate or validate dev SSL certificates (idempotent)
##   Delegates to core/modules/nginx/dev_cert_generator.py (DevPlan 099)
##   CERT_BACKEND env: auto (default), mkcert, openssl
## # ⚠️ TRAP[BUG] · 2026-07-16 · HIGH · PLATFORM_DOMAIN from .env · Root: env-chain break — `make` не читает .env, → контекстный домен молча терялся · Fix: recipe-level extraction (grep PLATFORM_DOMAIN= из .env) · Prevention: contract-проверка через DEV_CERTS_DIR+tmp
## # Третий уровень fallback — platform-env.yaml env_defaults.PLATFORM_DOMAIN
## #   (SoT), приоритет: env → .env → platform-env.yaml.
dev-certs:
	@echo "[IMP:7][make][dev-certs] Ensuring dev SSL certificates..."
	@_env_pd="$$($(PYTHON) -m core.internal.shared.env_reader get PLATFORM_DOMAIN --file "$(_platform_root)/.env")"; \
	PLATFORM_DOMAIN="$${PLATFORM_DOMAIN:-$${_env_pd:-$$($(PYTHON) -m core.internal.scripts.yaml_query --file "$(_platform_root)/platform-env.yaml" --get env_defaults.PLATFORM_DOMAIN)}}" \
	DEV_CERTS_DIR="$${DEV_CERTS_DIR:-$(_platform_root)/core/modules/nginx/dev-certs}" \
	DEV_CERTS_LIVE_ROOT="$${DEV_CERTS_LIVE_ROOT:-$(_platform_root)/core/modules/nginx/dev-certs}" \
	$(PYTHON) -m core.modules.nginx.dev_cert_generator
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
	export STATUS_METRICS_JSON="$${STATUS_METRICS_JSON:-$$($(PYTHON) -m core.internal.shared.env_reader get STATUS_METRICS_JSON --file "$$_env_file")}"; \
	export HTPASSWD_FILE="$${HTPASSWD_FILE:-$$($(PYTHON) -m core.internal.shared.env_reader get HTPASSWD_FILE --file "$$_env_file")}"; \
	export NODE_NAME="$${NODE_NAME:-$$($(PYTHON) -m core.internal.shared.env_reader get NODE_NAME --file "$$_env_file")}"; \
	export NODE_CONFIGS_DIR="$${NODE_CONFIGS_DIR:-$$($(PYTHON) -m core.internal.shared.env_reader get NODE_CONFIGS_DIR --file "$$_env_file")}"; \
	export PLATFORM_MASTER_EMAIL="$${PLATFORM_MASTER_EMAIL:-$$($(PYTHON) -m core.internal.shared.env_reader get PLATFORM_MASTER_EMAIL --file "$$_env_file")}"; \
	export PLATFORM_MASTER_PASSWORD="$${PLATFORM_MASTER_PASSWORD:-$$($(PYTHON) -m core.internal.shared.env_reader get PLATFORM_MASTER_PASSWORD --file "$$_env_file")}"; \
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
	@export LITELLM_MASTER_KEY="$${LITELLM_MASTER_KEY:-$$($(PYTHON) -m core.internal.shared.env_reader get LITELLM_MASTER_KEY --file "$(_platform_root)/.env")}"; \
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

## help: Показать помощь для человека (7 сценариев, только public-глаголы)
##   Двухролевой вывод (План 175 W1): make help — сценарии; make help-all — полный реестр.
##   SoT — core/entrypoint-manifest.yaml (visibility + scenarios), генератор generate_help.py.
help:
	@python3 $(_platform_root)/core/internal/scripts/generate_help.py --mode scenarios --manifest $(_platform_root)/core/entrypoint-manifest.yaml

## help-all: Показать полный реестр глаголов (включая internal-пометки)
##   Системное исключение .PHONY (категория служебных таргетов make, как help/venv).
help-all:
	@python3 $(_platform_root)/core/internal/scripts/generate_help.py --mode registry --manifest $(_platform_root)/core/entrypoint-manifest.yaml

# COMPOSE_PROFILES — SoT: core/platform-infra.yaml env_defaults (DevPlan 116 T2, U-02).
# Runtime-чтение через yaml_query.py (dotted-ключи) — хардкод-копии устранены;
# parity-гейт test_gate_profiles_parity проверяет отсутствие копий вне allowlist.
_get_all_profiles:
	@echo "$$($(PYTHON) -m core.internal.scripts.yaml_query --file core/platform-infra.yaml --get env_defaults.COMPOSE_PROFILES)"
