# GREP_SUMMARY: manifest.mk, include-split, manifest-generation, DAG, GENERATOR_ARGS, G1-G6
# STRUCTURE: ┌Chain A (G1→G2→G5)┐ → ◇ Chain B (G3→G4) → ◇ Chain C (G6) → ⊕ render-monitoring
# region MODULE_CONTRACT
## @purpose  Manifest generation targets — DAG of 3 independent chains
## @scope    All generate-* targets + render-monitoring. check-manifests/check-requirements/
##           check-profiles-parity/check-domain-parity удалены (План 175 W2.2) — суиты
##           check-suite.yaml вызывают инструменты напрямую.
## @invariants
##   - Three independent chains: A (secrets→platform-env→env-example), B (entrypoint→AGENTS.md), C (litellm-config)
##   - Freshness-проверка G1-G6 — core/internal/scripts/manifest_driver.py check (прямой вызов суита)
##   - .env.example freshness проверяется G5 внутри manifest_driver check
##   - generate-requirements standalone (G1-G6 контракт НЕ меняется, DevPlan 123 T11)
## @rationale Extracted from root Makefile (DevPlan 090) to keep root <150 lines per AC-5b.
##            Separate file makes DAG structure navigable and reduces merge conflict surface.
## @changes 2026-07-30 | Extracted from Makefile lines 49-248 to makefiles/manifest.mk
##           2026-08-03 | DevPlan 123 T11 (FL7) — +generate-requirements/check-requirements
##                      (requirements.txt GENERATED из pyproject.toml [project].dependencies)
##           2026-08-15 | DevPlan 171 W1.2 — check-env-defaults удалён (дубль G5 в check-manifests)
##           2026-08-15 | DevPlan 172 W2.2 — аргументы генераторов вынесены в make-переменные
##                      G1-G6_ARGS + GMAKE_PATH (единый SoT для generate/check, без ×2 дрейфа)
##           2026-08-16 | План 175 W2.2 — check-manifests/check-requirements/check-profiles-parity/
##                      check-domain-parity удалены (суиты вызывают инструменты напрямую)
# endregion MODULE_CONTRACT

# ═══ Shared generator arguments (DevPlan 172 W2.2 — единый набор для generate/check) ═══
# ⚠️ TRAP[DECISION] · 2026-08-15 · — · Аргументы генераторов — ОДИН раз, в make-переменных
# · Rejected: полные копии аргументов G1-G6 в generate-manifests И check-manifests (×2 дрейф)
# · Reason: правка одного генератора в двух местах — двойной SoT. Переменные = lazy (=):
# ·   раскрываются только в рецептах, которые их используют (make help не дёргает shell).
GMAKE_PATH = $(shell which gmake 2>/dev/null || which make 2>/dev/null || echo make)

G1_ARGS = --secret-defs core/secret-definitions.yaml --modules-dir core/modules --output core/secrets-manifest.yaml
G2_ARGS = --infra core/platform-infra.yaml --modules-dir core/modules --secret-defs core/secret-definitions.yaml \
	--output platform-env.yaml --smoke-env-output tests/_conftest/smoke_env_generated.py \
	--helpers-output tests/helpers/env_defaults_generated.py
G3_ARGS = --makefile-dir . --gmake-path $(GMAKE_PATH) --existing-manifest core/entrypoint-manifest.yaml \
	--tests-dir tests/gates --output core/entrypoint-manifest.yaml
G4_ARGS = --manifest core/entrypoint-manifest.yaml --agents-md core/AGENTS.md --marker canon_table
G4R_ARGS = --target root --manifest core/entrypoint-manifest.yaml --agents-md AGENTS.md
G5_ARGS = --platform-env platform-env.yaml --secret-defs core/secret-definitions.yaml --output .env.example
G6_ARGS = --policy core/internal/llm/policy.yaml --output core/modules/litellm/config/litellm-config.yml

# === Manifest generation targets — DAG (3 independent chains) ===
# Явный DAG через зависимости .PHONY таргетов (DevPlan 090):
#   Chain A: G1 → G2 → G5 (secrets-manifest → platform-env → .env.example)
#   Chain B: G3 → G4 (entrypoint-manifest → AGENTS.md)
#   Chain C: G6 (litellm-config)
# generate-manifests покрывает ВСЕ 6 генераторов (DevPlan 116 T5, U-44) — fix-gate
# (repair.mk → generate-manifests) чинит check-manifests полностью (G1-G6).
.PHONY: generate-manifests
.PHONY: generate-requirements
.PHONY: generate-secrets-manifest generate-platform-env generate-env-example
.PHONY: generate-entrypoint-manifest generate-agents-md generate-litellm-config render-monitoring

generate-manifests: generate-secrets-manifest generate-platform-env generate-env-example \
                    generate-entrypoint-manifest generate-agents-md generate-litellm-config
	@echo "[IMP:9][generate-manifests] All manifests generated (G1-G6)."

# ── Chain A ─────────────────────────────────────────────────
.PHONY: generate-secrets-manifest
generate-secrets-manifest:
	@echo "[IMP:7][generate-secrets-manifest] Generating secrets-manifest.yaml..."
	@python3 core/internal/scripts/generate_secrets_manifest.py $(G1_ARGS)

.PHONY: generate-platform-env
generate-platform-env: generate-secrets-manifest
	@echo "[IMP:7][generate-platform-env] Generating platform-env.yaml + generated Python files..."
	@python3 core/internal/scripts/generate_platform_env.py $(G2_ARGS)

.PHONY: generate-env-example
generate-env-example: generate-platform-env
	@echo "[IMP:7][generate-env-example] Generating .env.example..."
	@python3 core/internal/scripts/sync_env_defaults.py $(G5_ARGS)

# ── Chain B ─────────────────────────────────────────────────
.PHONY: generate-entrypoint-manifest
generate-entrypoint-manifest:
	@echo "[IMP:7][generate-entrypoint-manifest] Generating entrypoint-manifest.yaml..."
	@python3 core/internal/scripts/generate_entrypoint_manifest.py $(G3_ARGS)

.PHONY: generate-agents-md
generate-agents-md: generate-entrypoint-manifest
	@echo "[IMP:7][generate-agents-md] Generating core/AGENTS.md canonical table..."
	@python3 core/internal/scripts/generate_agents_md.py $(G4_ARGS)
	@echo "[IMP:7][generate-agents-md][root] Generating root AGENTS.md glossary (G4 --target root, DevPlan 116 B11 T3)..."
	@python3 core/internal/scripts/generate_agents_md.py $(G4R_ARGS)

# ── Chain C ─────────────────────────────────────────────────
.PHONY: generate-litellm-config
generate-litellm-config:
	@echo "[IMP:7][generate-litellm-config] Generating litellm-config.yml..."
	@python3 core/internal/llm/config_renderer.py $(G6_ARGS)

## render-monitoring: Рендер конфигурации мониторинга после деплоя проекта (DevPlan 116 B7 T7, U-65)
##   Сигнатура: make render-monitoring PROJECT_DIR=<dir> PROJECT=<name> [NODE=<node>]
##   Отсутствие PROJECT_DIR/PROJECT → argparse fail (exit 1, fail-fast)
render-monitoring:
	@echo "[IMP:7][render-monitoring] Rendering monitoring config for PROJECT=$(PROJECT)"
	@python3 core/internal/monitoring/config_renderer.py \
		--project-dir "$(PROJECT_DIR)" --project "$(PROJECT)" \
		$(if $(NODE),--node "$(NODE)",)

## @purpose  Проверка актуальности сгенерированных манифестов ПЕРЕНЕСЕНА в
##           core/internal/scripts/manifest_driver.py check (План 175 W2.1) — суит
##           'check-manifests' check-suite.yaml вызывает его напрямую. make-таргет
##           check-manifests удалён (W2.2). Reproducible: make fix-gate исправляет divergence.

## generate-requirements: core/requirements.txt GENERATED из pyproject.toml [project].dependencies
##   (единый SoT runtime-зависимостей, DevPlan 123 T11 FL7). Отдельный таргет — НЕ входит
##   в generate-manifests цепочку (G1-G6 контракт не меняется).
generate-requirements:
	@echo "[IMP:7][generate-requirements] Generating core/requirements.txt from pyproject.toml [project].dependencies..."
	@python3 core/internal/scripts/sync_requirements.py \
		--pyproject pyproject.toml \
		--output core/requirements.txt
	@echo "[IMP:9][generate-requirements] core/requirements.txt regenerated from pyproject.toml SoT."

## check-requirements: УДАЛЁН (План 175 W2.2) — суит 'check-requirements' check-suite.yaml
##   → python3 core/internal/scripts/sync_requirements.py --pyproject pyproject.toml
##   --output core/requirements.txt --check (прямой вызов).

## check-profiles-parity: УДАЛЁН (План 175 W2.2) — pytest-гейт test_gate_profiles_parity.py
##   живёт в суите 'gates' check-suite.yaml; repair → make generate-manifests.

## check-domain-parity: УДАЛЁН (План 175 W2.2) — pytest-гейт test_gate_domain_parity.py
##   живёт в суите 'gates' check-suite.yaml; repair → make generate-manifests.
