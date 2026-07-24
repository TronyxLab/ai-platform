# GREP_SUMMARY: deploy.mk, deploy, deploy-project, context-promote, hermes-build-platform, hermes-build-context, hermes-push-l1, verify
# STRUCTURE: ┌variables┐ → ◇ deploy → ◇ deploy-project → ◇ context-promote → ◇ hermes-build-* → ◇ verify
# region MODULE_CONTRACT
## @purpose  Deployment targets — deploy, deploy-project, context-promote, hermes builds, verify
## @scope    Included from root Makefile; delegates to core/entrypoints/
## @invariants
##   - deploy uses git push → CI (never direct SSH)
##   - deploy-project is emergency fallback (direct SSH tar)
##   - context-promote copies to context org
## @rationale Makefile include-split W4-E4: deployment targets isolated from bootstrap/CI
# endregion MODULE_CONTRACT

.PHONY: deploy deploy-project context-promote hermes-build-platform hermes-build-context hermes-push-l1 verify

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
GHCR_OWNER ?= $(shell echo "${GITHUB_REPOSITORY_OWNER:-tronyx161}" | tr '[:upper:]' '[:lower:]')
hermes-push-l1:
	@echo "[IMP:7][make][hermes-push-l1] Pushing L1 hermes-agent-base to ghcr.io/${GHCR_OWNER} (non-fatal)..."
	@docker tag hermes-agent-base:latest "ghcr.io/${GHCR_OWNER}/hermes-agent-base:latest" 2>/dev/null || true
	-docker push "ghcr.io/${GHCR_OWNER}/hermes-agent-base:latest" 2>/dev/null || echo "[IMP:7][make][hermes-push-l1] Push skipped — permission denied or registry unavailable (DR backup)"
	@echo "[IMP:9][make][hermes-push-l1] L1 push complete (or skipped)"

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

## verify: Post-deploy HTTPS verification for all expose:true domains on a node
##   Usage: make verify NODE=<node>
##   Reads node.yaml → curl all domains with expose:true → exit 0 if all 200, exit 1 otherwise
##   Delegates to core/entrypoints/verify.sh
verify:
	@if [ -z "$(NODE)" ]; then echo "[IMP:9][make][verify] ERROR: NODE not set — usage: make verify NODE=<node>" >&2; exit 1; fi
	@echo "[IMP:7][make][verify] Running post-deploy verification for NODE=$(NODE)..."
	@PLATFORM_ROOT="$(_platform_root)" bash $(_platform_root)/core/entrypoints/verify.sh "$(NODE)"
