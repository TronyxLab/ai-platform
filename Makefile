# GREP_SUMMARY: makefile, test, gate, validate, pre-commit, lint, hermes-build-platform, hermes-build-context, deploy, bootstrap-node, context-promote, new-project, new-context, audit, secrets-unlock
# STRUCTURE: test [MARKER=] → dispatch by marker (static|smoke|component|integration|predeploy|contract|e2e|all) | gate [MODE=] → dispatch by mode (fast|full) | lint → validate.sh --lint ¶ hermes-build* → build.sh | deploy → deploy.sh | bootstrap-node → bootstrap.sh | context-promote → context-promote.sh | new-* → scaffold.sh | audit → audit.sh | secrets-unlock → secrets.sh
# region MODULE_CONTRACT
## @purpose  Root Makefile for ai-platform — unified facade for all operations per AGENTS.md invariant #1
## @scope    test/gate/validate lifecycle, platform deployment (deploy/context-promote/bootstrap-node),
##           build (hermes-build-platform/hermes-build-context), scaffold (new-project/new-context),
##           secrets-unlock, audit, healthcheck, local compose lifecycle (up/down/restart/status/backup/restore)
## @invariants
##   - Makefile is the single entry point — no direct shell script calls (AGENTS.md §1)
##   - Every .PHONY target must be in entrypoint-manifest.yaml allowed_verbs (or system exception)
##   - Pre-commit hooks run via lint.sh directly; validate.sh --lint via `make lint` (CI/manual)
##   - Docker network creation must precede docker compose up (TASK-07)
## @rationale  Centralized Makefile eliminates scattered scripts (RC-6). AGENTS.md invariant #1
##             mandates Makefile as single entry point. Split pre-commit/Makefile lint roles (M7).
## @changes 2026-07-21 | W1: deploy +NODE pre-flight check via vps-readiness.sh
##           2026-07-21 | W4: bootstrap-node +AUTO_RECONCILE, converge +RECONCILE, node-update +RECONCILE
##           2026-07-21 | W6: deploy +LAUNCH=1 (CI wait + verify + URL)
# endregion MODULE_CONTRACT

SHELL := /bin/bash

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

.PHONY: venv
venv: $(VENV)

# test infrastructure (volume dirs, Docker networks) is managed by tests/conftest.py
# test_infra fixture — autouse session-scoped, replaces former test-infra-up/down targets.

.PHONY: venv up down healthcheck restart status backup restore test gate validate pre-commit-install pre-commit-run help lint check-file-lines discover-modules dev-certs test-inventory-sync templates-check templates-render hermes-build-platform hermes-build-context hermes-push-l1 deploy deploy-project bootstrap-node context-promote new-project new-context project-sync-env remove-project adopt-project project-list project-status audit secrets-unlock provision node-update verify converge render-vhosts scripts-audit

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

## discover-modules: Auto-discover modules and regenerate docker-compose.yml include section
discover-modules:
	@echo "[IMP:7][make][discover-modules] Discovering modules..."
	@python3 core/internal/bootstrap/discover_modules.py
	@echo "[IMP:9][make][discover-modules] Module discovery complete"

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

## up: Start platform stack (docker compose up -d) — supports MODULES filter
up: discover-modules dev-certs
	@echo "[IMP:7][make][up] Starting platform stack..."
	# provision — канонический таргет (make provision SCOPE=...), up вызывает напрямую для двух scope
	@bash $(_platform_root)/core/internal/provision-environment.sh --scope networks --scope volumes \
		--platform-env $(_platform_root)/platform-env.yaml
	@_compose_files="-f docker-compose.yml -f docker-compose.platform-dev.yml"; \
	if [ "$$(uname)" = "Darwin" ] && [ -f docker-compose.macos.yml ]; then \
		_compose_files="$$_compose_files -f docker-compose.macos.yml"; \
		echo "[IMP:7][make][up] macOS detected — including docker-compose.macos.yml"; \
	fi; \
	if [ -n "$(MODULES)" ]; then \
		_profiles=""; \
		IFS=',' read -ra _mods <<< "$(MODULES)"; \
		for _m in "$${_mods[@]}"; do \
			_profiles="$$_profiles --profile $$_m"; \
		done; \
		echo "[IMP:7][make][up] Using profiles: $(MODULES)"; \
		docker compose $$_compose_files $$_profiles up -d; \
	else \
		docker compose $$_compose_files up -d; \
	fi
	@echo "[IMP:9][make][up] Platform stack started"

## down: Stop platform stack and remove volumes
down:
	@echo "[IMP:7][make][down] Stopping platform stack..."
	@_compose_files="-f docker-compose.yml -f docker-compose.platform-dev.yml"; \
	if [ "$$(uname)" = "Darwin" ] && [ -f docker-compose.macos.yml ]; then \
		_compose_files="$$_compose_files -f docker-compose.macos.yml"; \
	fi; \
	docker compose $$_compose_files down -v
	@echo "[IMP:9][make][down] Platform stack stopped"

## healthcheck: Run all module healthcheck.sh scripts via entrypoint — exit 0 only if all pass
healthcheck:
	@echo "[IMP:7][make][healthcheck] Running all module healthchecks via entrypoint..."
	@bash $(_platform_root)/core/entrypoints/healthcheck.sh

## verify: Post-deploy HTTPS verification for all expose:true domains on a node
##   Usage: make verify NODE=<node>
##   Reads node.yaml → curl all domains with expose:true → exit 0 if all 200, exit 1 otherwise
##   Delegates to core/entrypoints/verify.sh
verify:
	@if [ -z "$(NODE)" ]; then echo "[IMP:9][make][verify] ERROR: NODE not set — usage: make verify NODE=<node>" >&2; exit 1; fi
	@echo "[IMP:7][make][verify] Running post-deploy verification for NODE=$(NODE)..."
	@PLATFORM_ROOT="$(_platform_root)" bash $(_platform_root)/core/entrypoints/verify.sh "$(NODE)"

## restart: Soft restart all Docker compose services (stop + start)
restart:
	@echo "[IMP:7][make][restart] Soft restarting all services..."
	@_compose_files="-f docker-compose.yml -f docker-compose.platform-dev.yml"; \
	if [ "$$(uname)" = "Darwin" ] && [ -f docker-compose.macos.yml ]; then \
		_compose_files="$$_compose_files -f docker-compose.macos.yml"; \
	fi; \
	docker compose $$_compose_files stop && docker compose $$_compose_files start
	@echo "[IMP:9][make][restart] All services soft restarted"

## status: Show running Docker compose services status
status:
	@echo "[IMP:7][make][status] Displaying running services..."
	@_compose_files="-f docker-compose.yml -f docker-compose.platform-dev.yml"; \
	if [ "$$(uname)" = "Darwin" ] && [ -f docker-compose.macos.yml ]; then \
		_compose_files="$$_compose_files -f docker-compose.macos.yml"; \
	fi; \
	docker compose $$_compose_files ps
	@echo "[IMP:9][make][status] Status displayed"

## backup: Trigger platform backup via backup-cron module
##   Delegates to core/modules/backup-cron/Makefile backup target
backup:
	@echo "[IMP:7][make][backup] Triggering backup via backup-cron module..."
	@make -C core/modules/backup-cron backup
	@echo "[IMP:9][make][backup] Backup complete"

## restore: Restore platform from backup via backup-cron module
##   Usage: make restore DUMP_FILE=<path>
##   Delegates to core/modules/backup-cron/Makefile restore target
restore:
	@echo "[IMP:7][make][restore] Restoring from backup via backup-cron module..."
	@if [[ -z "$(DUMP_FILE)" ]]; then \
		echo "[IMP:9][make][restore] ERROR: DUMP_FILE not set — usage: make restore DUMP_FILE=<path>" >&2; \
		exit 1; \
	fi
	@make -C core/modules/backup-cron restore DUMP_FILE="$(DUMP_FILE)"
	@echo "[IMP:9][make][restore] Restore complete"

## test: Run tests with MARKER filter. Usage: make test [MARKER=static|smoke|component|integration|predeploy|contract|e2e|all]
##   MARKER=all (default) — full suite in canonical order: validate → lint → gates → contract → static → predeploy → smoke → component → integration
##   MARKER=static — schema validation + lint + static_audit + unit (no Docker)
##   MARKER=smoke — compose lifecycle + healthchecks (needs Docker)
##   MARKER=component — hermes-agent + observability health endpoints (needs Docker)
##   MARKER=integration — full hermes LLM stack (needs Docker)
##   MARKER=predeploy — container/config/network validation (needs Docker)
##   MARKER=contract — contract tests for entrypoint scripts (no Docker)
##   MARKER=static_audit — pure pytest static_audit only (no validate/lint)
##   MARKER=e2e — manual end-to-end tests against *.tronyx.ru (external, no Docker, dev-only)
test:
	@echo "[IMP:7][make][test] Running tests with MARKER=$(or $(MARKER),all)..."
	$(eval MARKER := $(or $(MARKER),all))
	@if [ "$(MARKER)" = "static" ]; then \
		echo "[IMP:7][make][test] Running schema validation..."; \
		bash $(_platform_root)/core/entrypoints/validate.sh && \
		echo "[IMP:7][make][test] Running lint..."; \
		bash $(_platform_root)/core/entrypoints/validate.sh --lint; \
		echo "[IMP:7][make][test] Running static_audit + unit tests..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ \
			-m "static_audit or (not e2e and not component and not smoke and not integration and not local_auth and not requires_docker)" \
			-v --tb=short --junitxml=tests/report-static.xml && \
		cp tests/report-static.xml tests/report.xml; \
	elif [ "$(MARKER)" = "static_audit" ]; then \
		echo "[IMP:7][make][test] Running static_audit only (no validate/lint)..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ \
			-m "static_audit or (not e2e and not component and not smoke and not integration and not local_auth and not requires_docker)" \
			-v --tb=short --junitxml=tests/report-static.xml && \
		cp tests/report-static.xml tests/report.xml; \
	elif [ "$(MARKER)" = "smoke" ]; then \
		echo "[IMP:7][make][test] Running smoke tests..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "smoke" -v --tb=short -rs \
			--junitxml=tests/report-smoke.xml && \
		cp tests/report-smoke.xml tests/report.xml; \
	elif [ "$(MARKER)" = "component" ]; then \
		echo "[IMP:7][make][test] Running component tests..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "component" -v --tb=short -rs \
			--junitxml=tests/report-component.xml && \
		cp tests/report-component.xml tests/report.xml; \
	elif [ "$(MARKER)" = "integration" ]; then \
		echo "[IMP:7][make][test] Running integration tests..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "integration" -v --tb=short -rs \
			--junitxml=tests/report-integration.xml && \
		cp tests/report-integration.xml tests/report.xml; \
	elif [ "$(MARKER)" = "predeploy" ]; then \
		echo "[IMP:7][make][test] Running predeploy tests..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "predeploy" -v --tb=short -rs \
			--junitxml=tests/report-predeploy.xml && \
		cp tests/report-predeploy.xml tests/report.xml; \
	elif [ "$(MARKER)" = "contract" ]; then \
		echo "[IMP:7][make][test] Running contract tests..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m contract -v \
			--junitxml=tests/report-contract.xml && \
		cp tests/report-contract.xml tests/report.xml; \
	elif [ "$(MARKER)" = "e2e" ]; then \
		echo "[IMP:7][make][test] Running E2E tests (manual, targets external *.tronyx.ru)..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "e2e" -v --tb=short -rs \
			--junitxml=tests/report-e2e.xml && \
		cp tests/report-e2e.xml tests/report.xml; \
	elif [ "$(MARKER)" = "all" ]; then \
		echo "[IMP:7][make][test] Running full test suite (canonical order: validate→lint→gates→contract→static→predeploy→smoke→component→integration)..."; \
		rm -f tests/report.xml tests/report*.xml && \
		echo "[IMP:7][make][test] Step 1/9: validate..."; \
		bash $(_platform_root)/core/entrypoints/validate.sh || { echo "[IMP:9][make][test] FAIL: validate"; exit 1; }; \
		echo "[IMP:7][make][test] Step 2/9: lint..."; \
		bash $(_platform_root)/core/entrypoints/validate.sh --lint; \
		echo "[IMP:7][make][test] Step 3/9: gates..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/gates/ -m "gate and not skip_enforcement" -v || { echo "[IMP:9][make][test] FAIL: gates"; exit 1; }; \
		echo "[IMP:7][make][test] Step 4/9: contract..."; \
		$(MAKE) test MARKER=contract || { echo "[IMP:9][make][test] FAIL: contract"; exit 1; }; \
		echo "[IMP:7][make][test] Step 5/9: static..."; \
		$(MAKE) test MARKER=static_audit || { echo "[IMP:9][make][test] FAIL: static"; exit 1; }; \
		echo "[IMP:7][make][test] Step 6/9: predeploy..."; \
		$(MAKE) test MARKER=predeploy || { echo "[IMP:9][make][test] FAIL: predeploy"; exit 1; }; \
		echo "[IMP:7][make][test] Step 7/9: smoke..."; \
		$(MAKE) test MARKER=smoke || { echo "[IMP:9][make][test] FAIL: smoke"; exit 1; }; \
		echo "[IMP:7][make][test] Step 8/9: component..."; \
		$(MAKE) test MARKER=component || { echo "[IMP:9][make][test] FAIL: component"; exit 1; }; \
		echo "[IMP:7][make][test] Step 9/9: integration..."; \
		$(MAKE) test MARKER=integration || { echo "[IMP:9][make][test] FAIL: integration"; exit 1; }; \
		echo "[IMP:7][make][test] Merging JUnit XML reports..."; \
		$(PYTHON) tests/merge_junit.py \
			tests/report-contract.xml \
			tests/report-static.xml \
			tests/report-predeploy.xml \
			tests/report-smoke.xml \
			tests/report-component.xml \
			tests/report-integration.xml \
			-o tests/report.xml; \
	else \
		echo "[IMP:9][make][test] ERROR: Unknown MARKER='$(MARKER)'. Valid values: static, static_audit, smoke, component, integration, predeploy, contract, e2e, all" >&2; \
		exit 1; \
	fi
	@echo "[IMP:9][make][test] Tests complete (MARKER=$(MARKER))"

## validate: Run schema validation only (standalone). Same as `make test MARKER=static` step.
##   Invoked automatically as part of `make test MARKER=static` and `make gate MODE=fast`.
##   To run full validation suite: make test MARKER=static
validate:
	@echo "[IMP:9][make][validate] Running schema validation..."
	@bash $(_platform_root)/core/entrypoints/validate.sh
	@echo "[IMP:9][make][validate] Schema validation complete"

## lint: Run shellcheck + yamllint + pytest (best-effort, warn+skip on missing tools)
lint:
	@echo "[IMP:7][make][lint] Starting lint checks"
	@bash $(_platform_root)/core/entrypoints/validate.sh --lint

## pre-commit-install: Install pre-commit hooks (gitleaks + code-quality + check-doc-headers + pre-push + commit-msg)
pre-commit-install:
	@echo "[IMP:7][make][pre-commit-install] Installing pre-commit hooks..."
	@$(PIP) install pre-commit 2>/dev/null || pip3 install pre-commit 2>/dev/null || { \
		echo "[IMP:10][make][pre-commit-install] FATAL: pip install pre-commit failed"; \
		exit 1; \
	}
	@pre-commit install --hook-type pre-commit --hook-type pre-push --hook-type commit-msg 2>/dev/null || { \
		echo "[IMP:9][make][pre-commit-install] ERROR: pre-commit install failed — check if pre-commit is installed (pip install pre-commit)"; \
		exit 1; \
	}
	@echo "[IMP:9][make][pre-commit-install] Pre-commit hooks installed: gitleaks, ruff (verify only — run: ruff check --fix . && ruff format .), yamllint, check-github-workflows, check-compose-spec, check-yaml, trailing-whitespace, end-of-file-fixer, check-doc-headers, basedpyright, bandit, pre-push-gate, commit-msg"
	@echo "[IMP:8][make][pre-commit-install] Run 'pre-commit run --all-files' to validate all files"

## pre-commit-run: Run all pre-commit hooks against all files (CI use)
pre-commit-run:
	@echo "[IMP:7][make][pre-commit-run] Running all pre-commit hooks..."
	@pre-commit run --all-files 2>&1 || { \
		echo "[IMP:9][make][pre-commit-run] Some pre-commit hooks failed — review output above"; \
		exit 1; \
	}
	@echo "[IMP:9][make][pre-commit-run] All pre-commit hooks passed"

## gate: Production Gate. Usage: make gate [MODE=fast|full|ci-docker] [PROJECT=<name>]
##   MODE=full (default) — validate → lint → gates → contract → static → predeploy → smoke → component
##   MODE=fast — validate → lint → gates → static → predeploy (no Docker)
##   MODE=ci-docker — contract → static → predeploy → smoke → component → skip-enforcement (Docker-dependent only, no pre-commit/validate/lint)
##   PROJECT=<name> — filter predeploy tests to a specific project (used in CI deploy workflow)
gate:
	@echo "[IMP:7][make][gate] Running gate with MODE=$(or $(MODE),full)..."
	$(eval MODE := $(or $(MODE),full))
	@if [ "$(MODE)" = "fast" ]; then \
		echo "[IMP:7][make][gate] MODE=fast — 6 steps: pre-commit, validate, lint, gates, static, predeploy..."; \
		rm -f tests/report.xml tests/report*.xml && \
		if [ -z "$(SKIP_PRECOMMIT)" ] || [ "$(SKIP_PRECOMMIT)" != "1" ]; then \
			echo "[IMP:7][make][gate] Step 1/6: pre-commit-run..."; \
			$(MAKE) pre-commit-run || { echo "[IMP:9][make][gate] FAIL: pre-commit-run"; exit 1; }; \
		else \
			echo "[IMP:7][make][gate] Step 1/6: pre-commit-run skipped (SKIP_PRECOMMIT=1)"; \
		fi; \
		echo "[IMP:7][make][gate] Step 2/6: validate..."; \
		bash $(_platform_root)/core/entrypoints/validate.sh || { echo "[IMP:9][make][gate] FAIL: validate"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 3/6: lint..."; \
		bash $(_platform_root)/core/entrypoints/validate.sh --lint || { echo "[IMP:9][make][gate] FAIL: lint"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 4/6: anti-drift CI gates..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/gates/ -m gate -v || { echo "[IMP:9][make][gate] FAIL: gates"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 5/6: static tests (no Docker)..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ \
			-m "static_audit or (not e2e and not component and not smoke and not integration and not local_auth and not requires_docker)" \
			-v --tb=short \
			--junitxml=tests/report-static.xml || { echo "[IMP:9][make][gate] FAIL: static"; exit 1; }; \
		echo "[IMP:7][make][gate] Step 6/6: predeploy tests (PROJECT=$(or $(PROJECT),all))..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/ -m "predeploy" -v --tb=short -rs \
			$(if $(PROJECT),-k "$(PROJECT)",) \
			--junitxml=tests/report-predeploy.xml || { echo "[IMP:9][make][gate] FAIL: predeploy"; exit 1; }; \
	elif [ "$(MODE)" = "full" ]; then \
		echo "[IMP:7][make][gate] MODE=full — running complete gate pipeline (canonical order)..."; \
		GATE_FAILED=0; \
		rm -f tests/report.xml tests/report*.xml; \
		if [ -z "$(SKIP_PRECOMMIT)" ] || [ "$(SKIP_PRECOMMIT)" != "1" ]; then \
			echo "[IMP:7][make][gate] Step 1/10: pre-commit-run..."; \
			$(MAKE) pre-commit-run || { echo "[IMP:9][make][gate] FAIL: pre-commit-run"; GATE_FAILED=1; }; \
		else \
			echo "[IMP:7][make][gate] Step 1/10: pre-commit-run skipped (SKIP_PRECOMMIT=1)"; \
		fi; \
		echo "[IMP:7][make][gate] Step 2/10: validate..."; \
		$(MAKE) validate || { echo "[IMP:9][make][gate] FAIL: validate"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 3/10: lint..."; \
		$(MAKE) lint || { echo "[IMP:9][make][gate] FAIL: lint"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 4/10: check-file-lines (non-blocking)..."; \
		$(MAKE) check-file-lines || true; \
		echo "[IMP:7][make][gate] Step 5/10: anti-drift gates (fail-fast)..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/gates/ \
			-m "gate and not skip_enforcement" -v || { echo "[IMP:9][make][gate] FAIL: anti-drift gates"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 6/10: contract tests..."; \
		$(MAKE) test MARKER=contract || { echo "[IMP:9][make][gate] FAIL: contract"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 7/10: static tests..."; \
		$(MAKE) test MARKER=static_audit || { echo "[IMP:9][make][gate] FAIL: static"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 8/10: predeploy tests..."; \
		$(MAKE) test MARKER=predeploy || { echo "[IMP:9][make][gate] FAIL: predeploy"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 9/10: smoke tests..."; \
		$(MAKE) test MARKER=smoke || { echo "[IMP:9][make][gate] FAIL: smoke"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 10/10: component tests..."; \
		$(MAKE) test MARKER=component || { echo "[IMP:9][make][gate] FAIL: component"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Merging JUnit XML reports..."; \
		$(PYTHON) tests/merge_junit.py \
			tests/report-contract.xml \
			tests/report-static.xml \
			tests/report-predeploy.xml \
			tests/report-smoke.xml \
			tests/report-component.xml \
			-o tests/report.xml || { echo "[IMP:9][make][gate] FAIL: JUnit merge"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Wave 2: skip enforcement gate..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest \
			tests/gates/test_gate_skip_enforcement.py -v || { echo "[IMP:9][make][gate] FAIL: skip enforcement"; GATE_FAILED=1; }; \
		if [ $$GATE_FAILED -ne 0 ]; then \
			echo "[IMP:9][make][gate] Gate: FAILURES DETECTED (MODE=full) — see individual FAIL messages above"; \
			exit 1; \
		fi; \
	elif [ "$(MODE)" = "ci-docker" ]; then \
		echo "[IMP:7][make][gate] MODE=ci-docker — running Docker-dependent gate pipeline (no pre-commit/validate/lint)..."; \
		GATE_FAILED=0; \
		rm -f tests/report.xml tests/report*.xml; \
		echo "[IMP:7][make][gate] Step 1/6: contract tests..."; \
		$(MAKE) test MARKER=contract || { echo "[IMP:9][make][gate] FAIL: contract"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 2/6: static tests..."; \
		$(MAKE) test MARKER=static_audit || { echo "[IMP:9][make][gate] FAIL: static"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 3/6: predeploy tests..."; \
		$(MAKE) test MARKER=predeploy || { echo "[IMP:9][make][gate] FAIL: predeploy"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 4/6: smoke tests..."; \
		$(MAKE) test MARKER=smoke || { echo "[IMP:9][make][gate] FAIL: smoke"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 5/6: component tests..."; \
		$(MAKE) test MARKER=component || { echo "[IMP:9][make][gate] FAIL: component"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Merging JUnit XML reports..."; \
		$(PYTHON) tests/merge_junit.py \
			tests/report-contract.xml \
			tests/report-static.xml \
			tests/report-predeploy.xml \
			tests/report-smoke.xml \
			tests/report-component.xml \
			-o tests/report.xml || { echo "[IMP:9][make][gate] FAIL: JUnit merge"; GATE_FAILED=1; }; \
		echo "[IMP:7][make][gate] Step 6/6: skip enforcement gate..."; \
		PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest \
			tests/gates/test_gate_skip_enforcement.py -v || { echo "[IMP:9][make][gate] FAIL: skip enforcement"; GATE_FAILED=1; }; \
		if [ $$GATE_FAILED -ne 0 ]; then \
			echo "[IMP:9][make][gate] Gate: FAILURES DETECTED (MODE=ci-docker) — see individual FAIL messages above"; \
			exit 1; \
		fi; \
	else \
		echo "[IMP:9][make][gate] ERROR: Unknown MODE='$(MODE)'. Valid values: fast, full, ci-docker" >&2; \
		exit 1; \
	fi
	@echo "[IMP:9][make][gate] Gate: ALL PASS (MODE=$(MODE))"

PLATFORM_SCRIPTS := core/entrypoints

# ── Resolve platform root relative to Makefile location ──
_platform_root := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

# ═══════════════════════════════════════════════════════════════════
# PLATFORM DEPLOYMENT OPERATIONS
# ═══════════════════════════════════════════════════════════════════

## deploy: Deploy project via git push → CI pipeline
##   Usage: make deploy PROJECT=<dir> [NODE=<node>] [LAUNCH=1]
##   NODE=<node>: run VPS pre-flight check before git push (W1)
##   LAUNCH=1: after git push, wait for CI + verify + print URL (W6)
##   Pushes main branch to origin, triggering CI workflow
deploy:
	@echo "[IMP:7][make][deploy] Deploying PROJECT=$(PROJECT)..."
	@if [[ -z "$(PROJECT)" ]]; then \
		echo "[IMP:9][make][deploy] ERROR: PROJECT not set — usage: make deploy PROJECT=<dir>" >&2; \
		exit 1; \
	fi
	@if [[ ! -d "$(PROJECT)/.git" ]]; then \
		echo "[IMP:9][make][deploy] ERROR: $(PROJECT) is not a git repository" >&2; \
		exit 1; \
	fi
	@if ! git -C "$(PROJECT)" remote get-url origin >/dev/null 2>&1; then \
		echo "[IMP:9][make][deploy] ERROR: No git remote 'origin' in $(PROJECT)" >&2; \
		exit 1; \
	fi
	@# ── W1: Pre-flight VPS readiness check ──
	@if [ -n "$(NODE)" ]; then \
		echo "[IMP:7][make][deploy] Pre-flight: checking VPS readiness for NODE=$(NODE)..." >&2; \
		source $(_platform_root)/core/lib/vps-readiness.sh && \
		check_vps_ready "$(NODE)" || { \
			echo "[IMP:10][make][deploy] FATAL: VPS not ready. Run: make bootstrap-node NODE=$(NODE) first" >&2; \
			exit 1; \
		}; \
		echo "[IMP:9][make][deploy] VPS ready — proceeding with git push" >&2; \
	fi
	@# ── Git push ──
	@cd "$(PROJECT)" && git push origin main
	@echo "[IMP:9][make][deploy] Git push complete — CI pipeline triggered"
	@# ── W6: LAUNCH=1 mode — deploy-project + verify ──
	@if [ "$(filter 1,$(LAUNCH))" = "1" ]; then \
		echo "[IMP:7][make][deploy] LAUNCH mode: waiting for CI and verifying..." >&2; \
		if [ -z "$(NODE)" ]; then \
			echo "[IMP:10][make][deploy] FATAL: LAUNCH=1 requires NODE=<node>" >&2; \
			exit 1; \
		fi; \
		bash $(_platform_root)/core/entrypoints/deploy-project.sh \
			--project "$(PROJECT)" \
			--node "$(NODE)" \
			--launch; \
	fi

## deploy-project: Direct project deploy bypassing CI (emergency fallback)
##   Usage: make deploy-project PROJECT=<dir> NODE=<node> [SKIP_VERIFY=1] [DRY_RUN=1]
##   Validates PROJECT has ai-platform.yaml, resolves NODE→SSH host, deploys with audit
deploy-project:
	@echo "[IMP:7][make][deploy-project] Direct deploy PROJECT=$(PROJECT) NODE=$(NODE)..."
	@if [[ -z "$(PROJECT)" ]]; then \
		echo "[IMP:9][make][deploy-project] ERROR: PROJECT not set" >&2; exit 1; \
	fi
	@if [[ -z "$(NODE)" ]]; then \
		echo "[IMP:9][make][deploy-project] ERROR: NODE not set" >&2; exit 1; \
	fi
	@$(_platform_root)/core/entrypoints/deploy-project.sh \
		--project "$(PROJECT)" \
		--node "$(NODE)" \
		$(if $(filter 1,$(SKIP_VERIFY)),--skip-verify) \
		$(if $(filter 1,$(DRY_RUN)),--dry-run)
	@echo "[IMP:9][make][deploy-project] Direct deploy complete"

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


## context-promote: Promote platform to context org
##   Usage: make context-promote CONTEXT=<name>
##   Delegates to core/entrypoints/context-promote.sh → copies to <context>/ai-platform
context-promote:
	@echo "[IMP:7][make][context-promote] Promoting platform to CONTEXT=$(CONTEXT)..."
	@if [[ -z "$(CONTEXT)" ]]; then \
		echo "[IMP:9][make][context-promote] ERROR: CONTEXT not set — usage: make context-promote CONTEXT=<name>" >&2; \
		exit 1; \
	fi
	@$(_platform_root)/core/entrypoints/context-promote.sh "$(CONTEXT)"
	@echo "[IMP:9][make][context-promote] Context promote complete"

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

## project-sync-env: Sync .env.platform from platform-env.yaml
##   Usage: make project-sync-env [NAME=<project_name>] [DOMAIN=<domain>]
##   Delegates to core/entrypoints/scaffold.sh sync-env
project-sync-env:
	@echo "[IMP:7][make][project-sync-env] Syncing .env.platform..."
	@$(_platform_root)/core/entrypoints/scaffold.sh sync-env \
		$(if $(NAME),--name '$(NAME)') \
		$(if $(DOMAIN),--domain '$(DOMAIN)')
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

## secrets-unlock: Decrypt SOPS/age secrets
##   Usage: make secrets-unlock [NODE=<name>]
##   Delegates to core/entrypoints/secrets.sh
secrets-unlock:
	@echo "[IMP:7][make][secrets-unlock] Decrypting secrets..."
	@$(_platform_root)/core/entrypoints/secrets.sh $(NODE)
	@echo "[IMP:9][make][secrets-unlock] Secrets decrypted"

## audit: Run platform system audit
##   Usage: make audit [NODE=<name>]
##   Delegates to core/entrypoints/audit.sh
audit:
	@echo "[IMP:7][make][audit] Running platform audit..."
	@$(_platform_root)/core/entrypoints/audit.sh $(NODE)
	@echo "[IMP:9][make][audit] Audit complete"

# ═══════════════════════════════════════════════════════════════════
# HERMES AGENT OPERATIONS
# ═══════════════════════════════════════════════════════════════════

## hermes-build-platform: Build L1 hermes image (linux/amd64; ARM via emulation)
##   Builds hermes-agent-base:latest for platform development
##   Stages: L1 (platform base) → local tag; use hermes-push-l1 for ghcr.io backup push
##   Delegates to core/entrypoints/build.sh build-platform
hermes-build-platform:
	@echo "[IMP:9][make][hermes-build-platform] Building L1 hermes images (linux/amd64)..."
	@$(_platform_root)/core/entrypoints/build.sh build-platform
	@echo "[IMP:9][make][hermes-build-platform] L1 build complete"
	@echo "  L1: hermes-agent-base:latest (local build — push via hermes-push-l1)"

## hermes-push-l1: Push L1 hermes-agent image to ghcr.io (disaster recovery backup)
hermes-push-l1:
	@echo "[IMP:7][make][hermes-push-l1] Pushing L1 hermes-agent-base to ghcr.io..."
	@docker tag hermes-agent-base:latest ghcr.io/tronyx161/hermes-agent-base:latest
	@docker push ghcr.io/tronyx161/hermes-agent-base:latest
	@echo "[IMP:9][make][hermes-push-l1] L1 pushed to ghcr.io"

## hermes-build-context: Build L1→L2 hermes images for CONTEXT
##   Usage: make hermes-build-context CONTEXT=<name>
##   Stages: L2 (context overlay) → push
##   Delegates to core/entrypoints/build.sh build-context
hermes-build-context:
	@echo "[IMP:9][make][hermes-build-context] Building L2 hermes images for CONTEXT=$(CONTEXT)..."
	@if [[ -z "$(CONTEXT)" ]]; then \
		echo "[IMP:9][make][hermes-build-context] ERROR: CONTEXT not set — usage: make hermes-build-context CONTEXT=<name>" >&2; \
		exit 1; \
	fi
	@$(_platform_root)/core/entrypoints/build.sh build-context "$(CONTEXT)"
	@echo "[IMP:9][make][hermes-build-context] L2 build complete"
	@echo "  L2: ghcr.io/$(CONTEXT)/hermes-agent-context:latest"


## check-file-lines: Check for files exceeding line limit (default 500) — non-blocking warning
##   Usage: make check-file-lines [MAX_LINES=500]
##   Delegates to core/entrypoints/check-file-lines.sh
check-file-lines:
	@echo "[IMP:7][make][check-file-lines] Checking file line limits..."
	@bash $(_platform_root)/core/entrypoints/check-file-lines.sh $(if $(MAX_LINES),--max-lines $(MAX_LINES))
	@echo "[IMP:9][make][check-file-lines] Check complete"

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

## scripts-audit: Проверить регистрацию всех shebang-скриптов в manifest или exceptions
.PHONY: scripts-audit
scripts-audit:
	@echo "[IMP:7][make][scripts-audit] Auditing shebang script registration..."
	@bash $(_platform_root)/core/internal/scripts-audit.sh

## render-vhosts: Regenerate Nginx vhost configs from node.yaml
##   Usage: make render-vhosts NODE=<name>
##   Delegates to core/internal/scaffold/add-vhost.sh --render-all --node
render-vhosts:
	@echo "[IMP:7][make][render-vhosts] Generating vhost configs from node.yaml..."
	@bash core/internal/scaffold/add-vhost.sh --render-all --node $(NODE) --node-configs-dir $(NODE_CONFIGS_DIR)
	@echo "[IMP:9][make][render-vhosts] Vhost generation complete"

# ## project-sync-secrets: Sync project repo-secrets from SOPS enc-file via gh CLI (DISABLED — requires T3.6)
# ##   Usage: make project-sync-secrets NAME=<project> [NODE=<node>]
# ##   Delegates to core/internal/scaffold/sync-repo-secrets.sh
# project-sync-secrets:
# 	@echo "[IMP:7][make][project-sync-secrets] Syncing repo secrets..."
# 	@bash core/internal/scaffold/sync-repo-secrets.sh --name $(NAME) $(if $(NODE),--node $(NODE),)
# 	@echo "[IMP:9][make][project-sync-secrets] Repo secrets sync complete"

## help: Show this help
help:
	@grep -E '^## [a-zA-Z][a-zA-Z0-9_-]*: .*$$' $(MAKEFILE_LIST) | \
		sed 's/^## /  /' | column -t -s ':'
