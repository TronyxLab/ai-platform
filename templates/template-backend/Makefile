# GREP_SUMMARY: Makefile sync-env status help platform-delegation
# STRUCTURE: ┌platform delegate┐ → sync-env/status/help
# @purpose  Мини-фасад для команд платформы из папки проекта (K3, DD11)
# @invariants
#   - PLATFORM_DIR настраивается под локальное расположение платформы
#   - Все таргеты делегируют в Makefile платформы с PROJECT=$(CURDIR)
PLATFORM_DIR ?= $(HOME)/projects/ai-platform
# 🧐 TRAP[DECISION] · 2026-07-21 · — · PLATFORM_DIR not org-dependent — no T2 changes needed

sync-env: ; @$(MAKE) -C $(PLATFORM_DIR) project-sync-env PROJECT=$(CURDIR)
status:   ; @$(MAKE) -C $(PLATFORM_DIR) project-status PROJECT=$(CURDIR)
help:
	@echo "sync-env  — Regenerate .env.platform from platform config"
	@echo "status   — Check deployment status on node"
