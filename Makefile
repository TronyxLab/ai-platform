# GREP_SUMMARY: makefile, include-split, bootstrap, deploy, scaffold, modules, ci, helpers
# STRUCTURE: ┌variables┐ → ◇ include makefiles/*.mk → ◇ .DEFAULT_GOAL → ⎋ 45 .PHONY targets across 6 includes
# region MODULE_CONTRACT
## @purpose  Root Makefile for ai-platform — unified facade, delegates to makefiles/*.mk via include
## @scope    Variables + COMPOSE_PROFILES + includes — all targets in makefiles/{bootstrap,deploy,scaffold,modules,ci,helpers}.mk
## @invariants
##   - Makefile is the single entry point — no direct shell script calls (AGENTS.md §1)
##   - Every .PHONY target must be in entrypoint-manifest.yaml allowed_verbs
##   - Pre-commit hooks run via lint.sh directly; validate.sh --lint via `make lint` (CI/manual)
##   - Makefile include-split preserves tab-sensitive parsing (W4-E4)
## @rationale  Centralized Makefile eliminates scattered scripts (RC-6). W4-E4 split: 747 → <150 LOC
##             with 6 thematical .mk files for navigability and reduced merge conflicts.
##             AGENTS.md invariant #1 mandates Makefile as single entry point.
## @changes 2026-07-22 | W4-E4: Makefile include-split — 747→~80 LOC, targets moved to makefiles/*.mk
# endregion MODULE_CONTRACT

SHELL := /bin/bash

# === Python virtualenv ===
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# === Platform root (resolved relative to this Makefile) ===
_platform_root := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

# === Docker Compose profiles — all 13 Docker modules ===
# Export COMPOSE_PROFILES globally — covers gate, test, and all docker compose invocations.
# Uses ?= so existing env takes precedence.
export COMPOSE_PROFILES ?= postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page

# === Docker Compose shared files (resolved at parse time, used by modules.mk) ===
COMPOSE_BASE_FILES := -f docker-compose.yml -f docker-compose.platform-dev.yml
ifeq ($(shell uname -s),Darwin)
    ifneq ($(wildcard docker-compose.macos.yml),)
        COMPOSE_BASE_FILES += -f docker-compose.macos.yml
    endif
endif

# === Includes — all targets defined in makefiles/ ===
include makefiles/bootstrap.mk
include makefiles/deploy.mk
include makefiles/scaffold.mk
include makefiles/modules.mk
include makefiles/ci.mk
include makefiles/helpers.mk
include makefiles/repair.mk

# === Manifest generation targets — DAG (3 independent chains) ===
# Явный DAG через зависимости .PHONY таргетов (DevPlan 090):
#   Chain A: G1 → G2 → G5 (secrets-manifest → platform-env → .env.example)
#   Chain B: G3 → G4 (entrypoint-manifest → AGENTS.md)
#   Chain C: G6 (litellm-config)
.PHONY: generate-manifests generate-manifests-atomic check-manifests sync-env-defaults check-env-defaults
.PHONY: generate-secrets-manifest generate-platform-env generate-env-example
.PHONY: generate-entrypoint-manifest generate-agents-md generate-litellm-config

generate-manifests: generate-secrets-manifest generate-entrypoint-manifest generate-litellm-config
	@echo "[IMP:9][generate-manifests] All manifests generated."

# ── Chain A ─────────────────────────────────────────────────
.PHONY: generate-secrets-manifest
generate-secrets-manifest:
	@echo "[IMP:7][generate-secrets-manifest] Generating secrets-manifest.yaml..."
	@python3 core/internal/scripts/generate_secrets_manifest.py \
		--secret-defs core/secret-definitions.yaml \
		--modules-dir core/modules \
		--output core/secrets-manifest.yaml

.PHONY: generate-platform-env
generate-platform-env: generate-secrets-manifest
	@echo "[IMP:7][generate-platform-env] Generating platform-env.yaml + generated Python files..."
	@python3 core/internal/scripts/generate_platform_env.py \
		--infra core/platform-infra.yaml \
		--modules-dir core/modules \
		--secret-defs core/secret-definitions.yaml \
		--output platform-env.yaml \
		--smoke-env-output tests/_conftest/smoke_env_generated.py \
		--helpers-output tests/helpers/env_defaults_generated.py

.PHONY: generate-env-example
generate-env-example: generate-platform-env
	@echo "[IMP:7][generate-env-example] Generating .env.example..."
	@python3 core/internal/scripts/sync_env_defaults.py \
		--platform-env platform-env.yaml \
		--secret-defs core/secret-definitions.yaml \
		--output .env.example

# ── Chain B ─────────────────────────────────────────────────
.PHONY: generate-entrypoint-manifest
generate-entrypoint-manifest:
	@echo "[IMP:7][generate-entrypoint-manifest] Generating entrypoint-manifest.yaml..."
	@python3 core/internal/scripts/generate_entrypoint_manifest.py \
		--makefile-dir . \
		--gmake-path $(shell which gmake 2>/dev/null || which make 2>/dev/null || echo make) \
		--existing-manifest core/entrypoint-manifest.yaml \
		--tests-dir tests/gates \
		--output core/entrypoint-manifest.yaml

.PHONY: generate-agents-md
generate-agents-md: generate-entrypoint-manifest
	@echo "[IMP:7][generate-agents-md] Generating core/AGENTS.md canonical table..."
	@python3 core/internal/scripts/generate_agents_md.py \
		--manifest core/entrypoint-manifest.yaml \
		--agents-md core/AGENTS.md \
		--marker canon_table

# ── Chain C ─────────────────────────────────────────────────
.PHONY: generate-litellm-config
generate-litellm-config:
	@echo "[IMP:7][generate-litellm-config] Generating litellm-config.yml..."
	@python3 core/internal/llm/config_renderer.py \
		--policy core/internal/llm/policy.yaml \
		--output core/modules/litellm/config/litellm-config.yml

# ── Atomic generation (staging → rename) ────────────────────
## @purpose  Атомарная генерация ВСЕХ манифестов: staging dir (mktemp) → trap EXIT → rename.
##           При падении любого генератора staging удаляется, оригиналы не тронуты.
## @invariants
##   - mktemp создаёт уникальный staging dir (PID collision-resistant)
##   - trap EXIT гарантирует очистку при любом failure или signal
##   - mv атомарнен на одной файловой системе (staging туда же, где проект)
.PHONY: generate-manifests-atomic
generate-manifests-atomic:
	@echo "[IMP:7][generate-manifests-atomic] Starting atomic manifest generation..."
	@staging="$$(mktemp -d /tmp/manifest-gen-XXXXXX)"; \
	trap "rm -rf $$staging" EXIT; \
	echo "[IMP:8][generate-manifests-atomic] Staging dir: $$staging"; \
	\
	# Chain A: secrets → platform-env → env-example ; \
	python3 core/internal/scripts/generate_secrets_manifest.py \
		--secret-defs core/secret-definitions.yaml \
		--modules-dir core/modules \
		--output "$$staging/secrets-manifest.yaml" && \
	python3 core/internal/scripts/generate_platform_env.py \
		--infra core/platform-infra.yaml \
		--modules-dir core/modules \
		--secret-defs core/secret-definitions.yaml \
		--output "$$staging/platform-env.yaml" \
		--smoke-env-output "$$staging/smoke_env_generated.py" \
		--helpers-output "$$staging/env_defaults_generated.py" && \
	python3 core/internal/scripts/sync_env_defaults.py \
		--platform-env platform-env.yaml \
		--secret-defs core/secret-definitions.yaml \
		--output "$$staging/.env.example" && \
	\
	# Chain B: entrypoint → agents-md ; \
	python3 core/internal/scripts/generate_entrypoint_manifest.py \
		--makefile-dir . \
		--gmake-path "$(shell which gmake 2>/dev/null || which make 2>/dev/null || echo make)" \
		--existing-manifest core/entrypoint-manifest.yaml \
		--tests-dir tests/gates \
		--output "$$staging/entrypoint-manifest.yaml" && \
	cp core/AGENTS.md "$$staging/AGENTS.md" && \
	python3 core/internal/scripts/generate_agents_md.py \
		--manifest core/entrypoint-manifest.yaml \
		--agents-md "$$staging/AGENTS.md" \
		--marker canon_table && \
	\
	# Chain C: litellm-config ; \
	python3 core/internal/llm/config_renderer.py \
		--policy core/internal/llm/policy.yaml \
		--output "$$staging/litellm-config.yml" && \
	\
	# Атомарный rename — все или ничего (единый mv, не цикл) ; \
	echo "[IMP:9][generate-manifests-atomic] Atomic rename: $$staging → $(CURDIR)"; \
	mv "$$staging"/* "$(CURDIR)/" && \
	echo "[IMP:9][generate-manifests-atomic] All manifests generated atomically."

## @purpose  Проверка актуальности всех сгенерированных манифестов через --check каждого генератора.
##           Быстрее git diff (не требует полной генерации) и точнее (byte-level сравнение).
## @invariants
##   - Использует --check каждого из 6 генераторов (G1-G6)
##   - Exit 0 = все fresh, exit 1 = хотя бы один stale
##   - Reproducible: make fix-gate исправляет divergence
.PHONY: check-manifests
check-manifests:
	@echo "[IMP:7][check-manifests] Checking all generated manifests are up to date..."
	@errors=0; \
	# G1: secrets-manifest ; \
	python3 core/internal/scripts/generate_secrets_manifest.py \
		--secret-defs core/secret-definitions.yaml \
		--modules-dir core/modules \
		--output core/secrets-manifest.yaml \
		--check || errors=$$((errors + 1)); \
	# G2: platform-env (all 3 outputs) ; \
	python3 core/internal/scripts/generate_platform_env.py \
		--infra core/platform-infra.yaml \
		--modules-dir core/modules \
		--secret-defs core/secret-definitions.yaml \
		--output platform-env.yaml \
		--smoke-env-output tests/_conftest/smoke_env_generated.py \
		--helpers-output tests/helpers/env_defaults_generated.py \
		--check || errors=$$((errors + 1)); \
	# G3: entrypoint-manifest ; \
	python3 core/internal/scripts/generate_entrypoint_manifest.py \
		--makefile-dir . \
		--gmake-path "$(shell which gmake 2>/dev/null || which make 2>/dev/null || echo make)" \
		--existing-manifest core/entrypoint-manifest.yaml \
		--tests-dir tests/gates \
		--output core/entrypoint-manifest.yaml \
		--check || errors=$$((errors + 1)); \
	# G4: AGENTS.md ; \
	python3 core/internal/scripts/generate_agents_md.py \
		--manifest core/entrypoint-manifest.yaml \
		--agents-md core/AGENTS.md \
		--marker canon_table \
		--check || errors=$$((errors + 1)); \
	# G5: .env.example ; \
	python3 core/internal/scripts/sync_env_defaults.py \
		--platform-env platform-env.yaml \
		--secret-defs core/secret-definitions.yaml \
		--output .env.example \
		--check || errors=$$((errors + 1)); \
	# G6: litellm-config ; \
	python3 core/internal/llm/config_renderer.py \
		--policy core/internal/llm/policy.yaml \
		--output core/modules/litellm/config/litellm-config.yml \
		--check || errors=$$((errors + 1)); \
	# Result ; \
	if [ $$errors -gt 0 ]; then \
		echo "[GATE:FAIL][id:check-manifests][class:L1]" >&2; \
		echo ">>> REPAIR_RECIPE_START >>>" >&2; \
		echo "make fix-gate && git add -u && make gate MODE=fast" >&2; \
		echo "<<< REPAIR_RECIPE_END <<<" >&2; \
		exit 1; \
	fi; \
	echo "[IMP:9][check-manifests] All generated manifests are up to date."

sync-env-defaults:
	@echo "[IMP:7][sync-env-defaults] Generating .env.example from SoT..."
	@python3 core/internal/scripts/sync_env_defaults.py \
		--platform-env platform-env.yaml \
		--secret-defs core/secret-definitions.yaml \
		--output .env.example
	@echo "[IMP:9][sync-env-defaults] .env.example regenerated from SoT."

check-env-defaults:
	@echo "[IMP:7][check-env-defaults] Checking .env.example is up to date..."
	@python3 core/internal/scripts/sync_env_defaults.py \
		--platform-env platform-env.yaml \
		--secret-defs core/secret-definitions.yaml \
		--output .env.example \
		--check || \
		(echo "[GATE:FAIL][id:check-env-defaults][class:L1]" && \
		 echo ">>> REPAIR_RECIPE_START >>>" && \
		 echo "make sync-env-defaults && git add .env.example && make check-env-defaults" && \
		 echo "<<< REPAIR_RECIPE_END <<<" && exit 1)
	@echo "[IMP:9][check-env-defaults] .env.example is up to date."

# === Default target ===
.DEFAULT_GOAL := help
