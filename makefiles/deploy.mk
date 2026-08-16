# GREP_SUMMARY: deploy.mk, deploy, deploy-project, context-promote, hermes-build-context, hermes-push-l2, verify-domains
# STRUCTURE: ┌variables┐ → ◇ deploy → ◇ deploy-project → ◇ context-promote → ◇ hermes build/push → ◇ verify-domains
# region MODULE_CONTRACT
## @purpose  Deployment targets — deploy, deploy-project, context-promote, hermes builds, verify-domains
## @scope    Included from root Makefile; delegates to core/entrypoints/
## @invariants
##   - deploy uses git push → CI (never direct SSH)
##   - deploy-project is emergency fallback (direct SSH tar)
##   - context-promote copies to context org
##   - verify-domains (бывш. verify) — HTTPS-верификация доменов; VPS-verb verify НЕ трогается (План 175 W4.3)
##   - hermes: единый образ hermes-agent-context (L1→L2 коллапс DevPlan 002) — только
##     hermes-build-context + hermes-push-l2; hermes-build-platform/hermes-push-l1 удалены
## @rationale Makefile include-split W4-E4: deployment targets isolated from bootstrap/CI
## @changes 2026-08-16 | План 175 W4.3 — verify переименован в verify-domains
## @changes 2026-08-16 | DevPlan 002 W3 T3.1 — hermes-build-platform/hermes-push-l1/GHCR_OWNER удалены
##            (L1 коллапс); hermes-build-context → прямой вызов python3 (без build.sh)
# endregion MODULE_CONTRACT

.PHONY: deploy deploy-project context-promote hermes-build-context hermes-push-l2 verify-domains

## deploy: Deploy project via git push → CI pipeline
##   Usage: make deploy PROJECT=<dir> [NODE=<node>] [LAUNCH=1]
##   NODE=<node>: run VPS pre-flight check before git push (W1)
##   LAUNCH=1: after git push, deploy directly via deliver (NODE→host, DevPlan 116 B1 T5)
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
	@# ── W6: LAUNCH=1 mode — deliver via orchestrator (NODE→host, DevPlan 116 B1 T5) ──
	@if [ "$(filter 1,$(LAUNCH))" = "1" ]; then \
		echo "[IMP:7][make][deploy] LAUNCH mode: deploying directly to NODE=$(NODE) via deliver..." >&2; \
		if [ -z "$(NODE)" ]; then \
			echo "[IMP:10][make][deploy] FATAL: LAUNCH=1 requires NODE=<node>" >&2; \
			exit 1; \
		fi; \
		source $(_platform_root)/core/lib/node-resolver.sh; \
		NODE_YAML_PATH="$$(resolve_node_yaml "$(NODE)" "$(_platform_root)" 2>/dev/null)" || { \
			echo "[IMP:10][make][deploy] FATAL: node.yaml not found for NODE=$(NODE) — cannot resolve host" >&2; \
			exit 1; \
		}; \
		DEPLOY_HOST="$$(extract_node_host "$$NODE_YAML_PATH")"; \
		if [ -z "$$DEPLOY_HOST" ]; then \
			echo "[IMP:10][make][deploy] FATAL: no host field in node.yaml for NODE=$(NODE)" >&2; \
			exit 1; \
		fi; \
		PROJECT_NAME="$$(basename "$(PROJECT)")"; \
		python3 -m core.internal.deploy.orchestrator_cli deliver \
			--project "$$PROJECT_NAME" \
			--project-dir "$(PROJECT)" \
			--host "$$DEPLOY_HOST"; \
	fi

## deploy-project: Direct project deploy bypassing CI (emergency fallback)
##   Usage: make deploy-project PROJECT=<dir> NODE=<node> [VERSION=<sha>] [DRY_RUN=1]
##   Validates PROJECT has ai-platform.yaml, resolves NODE→SSH host via extract_node_host
##   (core/lib/node-resolver.sh, 3-candidate path), deploys через deliver (ForcedCommandChannel
##   receive <project> <version>).
##   DevPlan 091 Wave A (AC-A2): delegates to DeployOrchestrator via orchestrator_cli.
deploy-project:
	@echo "[IMP:7][make][deploy-project] Direct deploy PROJECT=$(PROJECT) NODE=$(NODE)..."
	@if [[ -z "$(PROJECT)" ]]; then \
		echo "[IMP:9][make][deploy-project] ERROR: PROJECT not set" >&2; exit 1; \
	fi
	@if [[ -z "$(NODE)" ]]; then \
		echo "[IMP:9][make][deploy-project] ERROR: NODE not set" >&2; exit 1; \
	fi
	@PROJECT_BASE="$$(dirname "$(PROJECT)")"; \
	PROJECT_NAME="$$(basename "$(PROJECT)")"; \
	source $(_platform_root)/core/lib/node-resolver.sh; \
	NODE_YAML_PATH="$$(resolve_node_yaml "$(NODE)" "$(_platform_root)" 2>/dev/null)" || { \
		echo "[IMP:10][make][deploy-project] FATAL: node.yaml not found for NODE=$(NODE) — cannot resolve host" >&2; \
		exit 1; \
	}; \
	DEPLOY_HOST="$$(extract_node_host "$$NODE_YAML_PATH")"; \
	if [ -z "$$DEPLOY_HOST" ]; then \
		echo "[IMP:10][make][deploy-project] FATAL: no host field in node.yaml for NODE=$(NODE) (check node.host)" >&2; \
		exit 1; \
	fi; \
	echo "[IMP:8][make][deploy-project] Resolved NODE=$(NODE) → host=$$DEPLOY_HOST"; \
	python3 -m core.internal.deploy.orchestrator_cli deliver \
		--project "$$PROJECT_NAME" \
		--project-dir "$(PROJECT)" \
		--host "$$DEPLOY_HOST" \
		$(if $(KEY_FILE),--key-file '$(KEY_FILE)') \
		$(if $(VERSION),--version '$(VERSION)')
	@echo "[IMP:9][make][deploy-project] Direct deploy complete"

## context-promote: Promote platform to context org
##   Usage: make context-promote CONTEXT=<name>
##   Delegates to core/entrypoints/context-promote.sh → core/internal/deploy/context_promoter.py
##   (git push --mirror: SSH primary / HTTPS fallback с GIT_ASKPASS)
context-promote:
	@echo "[IMP:7][make][context-promote] Promoting platform to CONTEXT=$(CONTEXT)..."
	@if [[ -z "$(CONTEXT)" ]]; then \
		echo "[IMP:9][make][context-promote] ERROR: CONTEXT not set — usage: make context-promote CONTEXT=<name>" >&2; \
		exit 1; \
	fi
	@$(_platform_root)/core/entrypoints/context-promote.sh "$(CONTEXT)"
	@echo "[IMP:9][make][context-promote] Context promote complete"

## 🧐 TRAP[DECISION] · 2026-07-25 · — · L2 НИКОГДА не пушится в базовый репозиторий (tronyx161)
## · Context-специфичный L2 образ хранится ТОЛЬКО в org контекста, например ghcr.io/tronyxlab/.
## · Push L2 в tronyx161 запрещён — только контекстный org (context-overlay, per-node).
## · CI пушит L2 в tronyx161 с CONTEXT=ci — это отдельный CI-образ, не context-specific.
## · Rejected: fallback to GHCR_OWNER (tronyx161) — риск загрязнения базового репозитория L2-образами.
## · Reason: L2 — это контекстный overlay, его место в org контекста, не в source org.
## · Rev: если CI начнёт билдить context-specific L2, CONTEXT станет workflow_dispatch input.

## hermes-push-l2: Push hermes-agent-context в ghcr.io контекста
##   Usage: make hermes-push-l2 CONTEXT=<org> [GHCR_PUSH_TOKEN=<token>]
##   CONTEXT обязателен — нормализуется: hyphens stripped (tronyx-lab → tronyxlab).
##   L2 НИКОГДА не пушится в базовый репозиторий — только в org контекста.
##   Требует GHCR_PUSH_TOKEN (classic PAT с write:packages).
##   Tag-policy U-60: пушатся :latest И :v<pyproject-version> (versioned-тег релизов).
hermes-push-l2:
	@L2_RAW="$(CONTEXT)"; \
	if [[ -z "$$L2_RAW" ]]; then \
		echo "[IMP:10][make][hermes-push-l2] ERROR: CONTEXT is required — usage: make hermes-push-l2 CONTEXT=<org>" >&2; \
		exit 1; \
	fi; \
	L2_ORG="$$(echo "$$L2_RAW" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]//g')"; \
	PLATFORM_VERSION="$$(sed -n 's/^version = "\(.*\)"$$/\1/p' pyproject.toml | head -1)"; \
	if [[ -z "$$PLATFORM_VERSION" ]]; then \
		echo "[IMP:10][make][hermes-push-l2] ERROR: version not found in pyproject.toml (tag-policy U-60)" >&2; \
		exit 1; \
	fi; \
	echo "[IMP:7][make][hermes-push-l2] Pushing hermes-agent-context to ghcr.io/$${L2_ORG} (tags: latest, v$${PLATFORM_VERSION})..."; \
	if [ -n "$(GHCR_PUSH_TOKEN)" ]; then \
		echo "$(GHCR_PUSH_TOKEN)" | docker login ghcr.io -u x-access-token --password-stdin 2>/dev/null || \
		echo "[IMP:7][make][hermes-push-l2] WARNING: GHCR_PUSH_TOKEN login failed" >&2; \
	elif [ -n "$(GHCR_PULL_TOKEN)" ]; then \
		echo "[IMP:7][make][hermes-push-l2] GHCR_PUSH_TOKEN not set — trying GHCR_PULL_TOKEN (read-only)" >&2; \
		echo "$(GHCR_PULL_TOKEN)" | docker login ghcr.io -u x-access-token --password-stdin 2>/dev/null || true; \
	fi; \
	docker tag hermes-agent-context:latest "ghcr.io/$${L2_ORG}/hermes-agent-context:latest" 2>/dev/null || true; \
	docker push "ghcr.io/$${L2_ORG}/hermes-agent-context:latest"; \
	docker tag hermes-agent-context:latest "ghcr.io/$${L2_ORG}/hermes-agent-context:v$${PLATFORM_VERSION}" 2>/dev/null || true; \
	docker push "ghcr.io/$${L2_ORG}/hermes-agent-context:v$${PLATFORM_VERSION}"; \
	echo "[IMP:9][make][hermes-push-l2] L2 push complete: ghcr.io/$${L2_ORG}/hermes-agent-context:{latest,v$${PLATFORM_VERSION}}"

## hermes-build-context: Build единый hermes-agent-context образ (L1→L2 коллапс DevPlan 002)
##   Usage: make hermes-build-context CONTEXT=<name>
##   Единый multi-stage Dockerfile (core/modules/hermes-agent/Dockerfile): base-стадия
##   (бывш. L1) + final-стадия (context overlay + CONTEXT guard + USER 10000).
##   Прямой вызов python3-модуля (build.sh удалён DevPlan 002 W2).
hermes-build-context:
	@echo "[IMP:9][make][hermes-build-context] Building hermes-agent-context for CONTEXT=$(CONTEXT)..."
	@if [[ -z "$(CONTEXT)" ]]; then \
		echo "[IMP:9][make][hermes-build-context] ERROR: CONTEXT not set — usage: make hermes-build-context CONTEXT=<name>" >&2; \
		exit 1; \
	fi
	@export DOCKER_BUILDKIT="$${DOCKER_BUILDKIT:-1}" && .venv/bin/python3 -m core.internal.build.hermes_images build-context
	@echo "[IMP:9][make][hermes-build-context] L2 build complete"
	@echo "  L2: hermes-agent-context:latest (push via hermes-push-l2 CONTEXT=$(CONTEXT))"

## verify-domains: Post-deploy HTTPS verification for expose:true domains on a node
##   Usage: make verify-domains NODE=<node> [PROJECT=<name>]
##   Reads node.yaml → curl domains with expose:true → exit 0 if all 200, exit 1 otherwise
##   PROJECT=<name> (DevPlan 125 T1, P-22) — сузить скоуп до одного проекта
##   (CI-verify деплоящегося проекта не падает от 502 соседа при параллельном деплое)
##   Делегирует core/entrypoints/verify.sh. Переименован из verify (План 175 W4.3) —
##   отличен от e2e-verify (sweep всех endpoints) и от VPS-verb verify (forced-command,
##   НЕ трогается — другой контур).
##   Delegates to core/entrypoints/verify.sh
verify-domains:
	@if [ -z "$(NODE)" ]; then echo "[IMP:9][make][verify-domains] ERROR: NODE not set — usage: make verify-domains NODE=<node> [PROJECT=<name>]" >&2; exit 1; fi
	@echo "[IMP:7][make][verify-domains] Running post-deploy verification for NODE=$(NODE) PROJECT=$(PROJECT)"
	@PLATFORM_ROOT="$(_platform_root)" bash $(_platform_root)/core/entrypoints/verify.sh "$(NODE)" "$(PROJECT)"
