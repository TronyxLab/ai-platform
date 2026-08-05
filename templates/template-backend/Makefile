# GREP_SUMMARY: Makefile sync-env status project-check project-fix project-sync-practices project-set-practices help platform-delegation
# STRUCTURE: ┌platform delegate┐ → sync-env/status/project-* (practices K1) → help
# @purpose  Мини-фасад для команд платформы из папки проекта (K3, DD11)
# @invariants
#   - PLATFORM_DIR настраивается под локальное расположение платформы
#   - Все таргеты делегируют в Makefile платформы с PROJECT=$(CURDIR)
#   - project-* таргеты — практики проекта (DevPlan 137, K1: project-check/fix/sync/set)
PLATFORM_DIR ?= $(HOME)/projects/ai-platform
# 🧐 TRAP[DECISION] · 2026-07-21 · — · PLATFORM_DIR not org-dependent — no T2 changes needed

sync-env: ; @$(MAKE) -C $(PLATFORM_DIR) project-sync-env PROJECT=$(CURDIR)
status:   ; @$(MAKE) -C $(PLATFORM_DIR) project-status PROJECT=$(CURDIR)
project-check: ; @$(MAKE) -C $(PLATFORM_DIR) project-check PROJECT=$(CURDIR)
project-fix:   ; @$(MAKE) -C $(PLATFORM_DIR) project-fix PROJECT=$(CURDIR)
project-sync-practices: ; @$(MAKE) -C $(PLATFORM_DIR) project-sync-practices PROJECT=$(CURDIR)
project-set-practices: ; @$(MAKE) -C $(PLATFORM_DIR) project-set-practices PROJECT=$(CURDIR) LEVEL=$(LEVEL)
help:
	@echo "sync-env  — Regenerate .env.platform from platform config"
	@echo "status   — Check deployment status on node"
	@echo "project-check — Run practices checks (K1)"
	@echo "project-fix — Auto-fix practices checks"
	@echo "project-sync-practices — Regenerate GENERATED practices files"
	@echo "project-set-practices — Set practices level (LEVEL=baseline|full|auto)"
