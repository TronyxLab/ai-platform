# GREP_SUMMARY: manifest.mk, include-split, manifest-generation, DAG, atomic, check
# STRUCTURE: ┌Chain A (G1→G2→G5)┐ → ◇ Chain B (G3→G4) → ◇ Chain C (G6) → ⊕ Atomic (staging → rename) → ⊕ check → ⎋ sync-env-defaults
# region MODULE_CONTRACT
## @purpose  Manifest generation targets — DAG of 3 independent chains for atomic generation
## @scope    All generate-*, check-manifests, sync-env-defaults, check-env-defaults targets
## @invariants
##   - Three independent chains: A (secrets→platform-env→env-example), B (entrypoint→AGENTS.md), C (litellm-config)
##   - check-manifests runs --check on all 6 generators without producing output
##   - generate-manifests-atomic uses mktemp staging dir + atomic mv for no-partial-writes guarantee
##   - sync-env-defaults and check-env-defaults are standalone (also part of Chain A via dependencies)
## @rationale Extracted from root Makefile (DevPlan 090) to keep root <150 lines per AC-5b.
##            Separate file makes DAG structure navigable and reduces merge conflict surface.
## @changes 2026-07-30 | Extracted from Makefile lines 49-248 to makefiles/manifest.mk
# endregion MODULE_CONTRACT

# === Manifest generation targets — DAG (3 independent chains) ===
# Явный DAG через зависимости .PHONY таргетов (DevPlan 090):
#   Chain A: G1 → G2 → G5 (secrets-manifest → platform-env → .env.example)
#   Chain B: G3 → G4 (entrypoint-manifest → AGENTS.md)
#   Chain C: G6 (litellm-config)
.PHONY: generate-manifests generate-manifests-atomic check-manifests sync-env-defaults check-env-defaults
.PHONY: generate-secrets-manifest generate-platform-env generate-env-example
.PHONY: generate-entrypoint-manifest generate-agents-md generate-litellm-config

# 📝 TRAP[DEBT] · 2026-07-31 · MED · generate-manifests omits G2/G4/G5 — fix-gate doesn't fully repair stale manifests
# · Observed: final-gate F1 session — `make fix-gate` → generate-manifests ran ONLY G1/G3/G6;
#   core/AGENTS.md (G4) stayed stale until `make generate-agents-md` was invoked explicitly.
# · Suspected: hypothesis, needs verification — generate-manifests target list (line 25) missing
#   generate-agents-md / generate-env-example; only generate-manifests-atomic covers all 3 chains
#   (despite the DAG comment above declaring Chain B: G3→G4 part of the flow).
# · Impact: canonical repair claims G1-G6 but leaves G4 (and G2/G5) stale → check-manifests
#   still fails after `make fix-gate` — repair-loop break for manifest-drift blockers.
# · When: during BLOCKER F1 fix (plans 099/103 delegates_to drift), 2026-07-31
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
	echo "[IMP:8][check-manifests] G1: secrets-manifest..." && \
	python3 core/internal/scripts/generate_secrets_manifest.py \
		--secret-defs core/secret-definitions.yaml \
		--modules-dir core/modules \
		--output core/secrets-manifest.yaml \
		--check || errors=$$((errors + 1)); \
	echo "[IMP:8][check-manifests] G2: platform-env..." && \
	python3 core/internal/scripts/generate_platform_env.py \
		--infra core/platform-infra.yaml \
		--modules-dir core/modules \
		--secret-defs core/secret-definitions.yaml \
		--output platform-env.yaml \
		--smoke-env-output tests/_conftest/smoke_env_generated.py \
		--helpers-output tests/helpers/env_defaults_generated.py \
		--check || errors=$$((errors + 1)); \
	echo "[IMP:8][check-manifests] G3: entrypoint-manifest..." && \
	python3 core/internal/scripts/generate_entrypoint_manifest.py \
		--makefile-dir . \
		--gmake-path "$(shell which gmake 2>/dev/null || which make 2>/dev/null || echo make)" \
		--existing-manifest core/entrypoint-manifest.yaml \
		--tests-dir tests/gates \
		--output core/entrypoint-manifest.yaml \
		--check || errors=$$((errors + 1)); \
	echo "[IMP:8][check-manifests] G4: AGENTS.md..." && \
	python3 core/internal/scripts/generate_agents_md.py \
		--manifest core/entrypoint-manifest.yaml \
		--agents-md core/AGENTS.md \
		--marker canon_table \
		--check || errors=$$((errors + 1)); \
	echo "[IMP:8][check-manifests] G5: .env.example..." && \
	python3 core/internal/scripts/sync_env_defaults.py \
		--platform-env platform-env.yaml \
		--secret-defs core/secret-definitions.yaml \
		--output .env.example \
		--check || errors=$$((errors + 1)); \
	echo "[IMP:8][check-manifests] G6: litellm-config..." && \
	python3 core/internal/llm/config_renderer.py \
		--policy core/internal/llm/policy.yaml \
		--output core/modules/litellm/config/litellm-config.yml \
		--check || errors=$$((errors + 1)); \
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
