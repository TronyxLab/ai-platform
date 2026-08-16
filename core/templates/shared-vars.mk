# GREP_SUMMARY: shared-vars.mk, MODULE_DIR, SHELL, template-shared, module-mk, module-system-mk
# STRUCTURE: ┌SHELL + MODULE_DIR┐ → ⊕ include из module.mk / module-system.mk
# region MODULE_CONTRACT
## @purpose  Общие переменные make-шаблонов модулей (DevPlan 172 W2.5): SHELL и MODULE_DIR.
## @scope    Включается ТОЛЬКО из core/templates/module.mk и core/templates/module-system.mk.
## @invariants
##   - MODULE_DIR резолвится от ПЕРВОГО makefile в MAKEFILE_LIST (makefile модуля,
##     который делает include) — формула перенесена сюда без изменения семантики
##     (в момент include в shared-vars.mk firstword(MAKEFILE_LIST) — всё ещё makefile модуля)
##   - НЕ включать напрямую из makefiles модулей (cross-layer: модули включают только
##     module.mk / module-system.mk / Makefile.common — cross_layer_linter)
## @rationale Две копии одной формулы MODULE_DIR (module.mk:49, module-system.mk:24)
##            дрейфовали; единый shared-vars.mk — одна точка определения.
## @changes  2026-08-15 | DevPlan 172 W2.5 — Created
# endregion MODULE_CONTRACT

SHELL := /bin/bash
MODULE_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
