# GREP_SUMMARY: project-practices.mk, project-check, project-sync-practices, project-set-practices, PLATFORM_DIR, K1, practices-CLI
# STRUCTURE: ┌3 глагола практик┐ → ◇ project-check (K1, --fix) → ◇ project-sync-practices (repair) → ◇ project-set-practices (level) → ⊕ .PHONY
# region MODULE_CONTRACT
## @purpose  Глаголы локального канала K1 практик (DevPlan 137 §2.1A/§4.7): тонкие фасады
##           над Python-CLI core.internal.practices.*. Вызываются из корневого Makefile
##           платформы с PROJECT=<dir> (делегат шаблона проекта → PLATFORM_DIR).
## @scope    Только локальная машина разработчика (K1). project-check/project-sync-practices/
##           project-set-practices регистрируются в entrypoint-manifest
##           (allowed_verbs + глоссарий через make generate-manifests && generate-agents-md).
## @invariants
##   - Никакой bash-логики — только python3 -m core.internal.practices.* (языковая политика)
##   - exit-коды CLI: project-check 0/1/4; sync 0/1/4; set 0/1/4 (shared/contracts.py)
##   - project-fix УДАЛЁН (План 175 W4.1) — alias project-check --fix
## @rationale Паритет sync-env-делегированию (K3/DD11): проект вызывает make project-*,
##            исполнение — платформенный Python (ноль копий логики в проекте).
## @changes  2026-08-05 · DevPlan 137 W1 — создан
##           2026-08-16 · План 175 W4.1 — project-fix удалён (alias project-check --fix)
# endregion MODULE_CONTRACT

.PHONY: project-check project-sync-practices project-set-practices

project-check: ; python3 -m core.internal.practices.check_project --project-dir $(PROJECT) $(if $(LEVEL),--level $(LEVEL),)
project-sync-practices: ; python3 -m core.internal.practices.sync_practices --project-dir $(PROJECT)
project-set-practices: ; python3 -m core.internal.practices.set_practices --project-dir $(PROJECT) --level $(LEVEL)
