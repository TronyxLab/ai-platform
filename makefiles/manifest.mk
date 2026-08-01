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
# generate-manifests покрывает ВСЕ 6 генераторов (DevPlan 116 T5, U-44) — fix-gate
# (repair.mk → generate-manifests) чинит check-manifests полностью (G1-G6).
.PHONY: generate-manifests generate-manifests-atomic check-manifests sync-env-defaults check-env-defaults
.PHONY: generate-secrets-manifest generate-platform-env generate-env-example
.PHONY: generate-entrypoint-manifest generate-agents-md generate-litellm-config render-monitoring
.PHONY: check-profiles-parity check-domain-parity

generate-manifests: generate-secrets-manifest generate-platform-env generate-env-example \
                    generate-entrypoint-manifest generate-agents-md generate-litellm-config
	@echo "[IMP:9][generate-manifests] All manifests generated (G1-G6)."

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
	@echo "[IMP:7][generate-agents-md][root] Generating root AGENTS.md glossary (G4 --target root, DevPlan 116 B11 T3)..."
	@python3 core/internal/scripts/generate_agents_md.py \
		--target root \
		--manifest core/entrypoint-manifest.yaml \
		--agents-md AGENTS.md

# ── Chain C ─────────────────────────────────────────────────
.PHONY: generate-litellm-config
generate-litellm-config:
	@echo "[IMP:7][generate-litellm-config] Generating litellm-config.yml..."
	@python3 core/internal/llm/config_renderer.py \
		--policy core/internal/llm/policy.yaml \
		--output core/modules/litellm/config/litellm-config.yml

## render-monitoring: Рендер конфигурации мониторинга после деплоя проекта (DevPlan 116 B7 T7, U-65)
##   Сигнатура: make render-monitoring PROJECT_DIR=<dir> PROJECT=<name> [NODE=<node>]
##   Отсутствие PROJECT_DIR/PROJECT → argparse fail (exit 1, fail-fast)
render-monitoring:
	@echo "[IMP:7][render-monitoring] Rendering monitoring config for PROJECT=$(PROJECT)"
	@python3 core/internal/monitoring_config_renderer.py \
		--project-dir "$(PROJECT_DIR)" --project "$(PROJECT)" \
		$(if $(NODE),--node "$(NODE)",)

# ── Atomic generation (staging → rename) ────────────────────
# 📝 TRAP[DEBT] · 2026-08-01 · MED · generate-manifests-atomic — латентная поломка mv-семантики
# · Observed: таргет нигде не вызывается (dead); `mv "$$staging"/* "$(CURDIR)/"` кладёт
#   ВСЕ манифесты в репозиторий (secrets-manifest.yaml должен быть core/, AGENTS.md —
#   root) — затирает root AGENTS.md содержимым core/AGENTS.md; G4 --target root (B11 T3)
#   в атомарную цепочку НЕ добавлен именно из-за этого
# · Suspected: таргет был написан "на будущее" (DevPlan 090) и никогда не запускался
# · Impact: запуск -atomic тихо повредит root AGENTS.md + разбросает манифесты
# · When: during B11 T3 implementation — deferred, out of scope
## @purpose  Атомарная генерация ВСЕХ манифестов: staging dir (mktemp) → trap EXIT → rename.
##           При падении любого генератора staging удаляется, оригиналы не тронуты.
## @invariants
##   - mktemp создаёт уникальный staging dir (PID collision-resistant)
##   - trap EXIT гарантирует очистку при любом failure или signal
##   - mv атомарнен на одной файловой системе (staging туда же, где проект)
##   - ⚠️ НЕ ИСПОЛЬЗОВАТЬ до фикса TRAP[DEBT] 2026-08-01 (mv-семантика ломает root AGENTS.md)
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
	echo "[IMP:8][check-manifests] G4-root: root AGENTS.md glossary..." && \
	python3 core/internal/scripts/generate_agents_md.py \
		--target root \
		--manifest core/entrypoint-manifest.yaml \
		--agents-md AGENTS.md \
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

## check-profiles-parity: COMPOSE_PROFILES — единый SoT (platform-infra.yaml), 0 хардкод-копий (DevPlan 116 T9)
check-profiles-parity:
	@echo "[IMP:7][check-profiles-parity] Running profiles parity gate..."
	@python3 -m pytest tests/gates/test_gate_profiles_parity.py -q || \
		(echo "[GATE:FAIL][id:check-profiles-parity][class:L1]" && \
		 echo ">>> REPAIR_RECIPE_START >>>" && \
		 echo "make generate-manifests && git add -u && make check-profiles-parity" && \
		 echo "<<< REPAIR_RECIPE_END <<<" && exit 1)
	@echo "[IMP:9][check-profiles-parity] Profiles parity gate passed."

## check-domain-parity: PLATFORM_DOMAIN — единое определение, 0 legacy-доменов (DevPlan 116 T9)
check-domain-parity:
	@echo "[IMP:7][check-domain-parity] Running domain parity gate..."
	@python3 -m pytest tests/gates/test_gate_domain_parity.py -q || \
		(echo "[GATE:FAIL][id:check-domain-parity][class:L1]" && \
		 echo ">>> REPAIR_RECIPE_START >>>" && \
		 echo "make generate-manifests && git add -u && make check-domain-parity" && \
		 echo "<<< REPAIR_RECIPE_END <<<" && exit 1)
	@echo "[IMP:9][check-domain-parity] Domain parity gate passed."
