# GREP_SUMMARY: langfuse Makefile start stop restart status logs backup compose include module-mk
# STRUCTURE: ┌langfuse targets┐ → ◇ module.mk include → ⊕ compose lifecycle
# region MODULE_CONTRACT
## @purpose  Makefile for langfuse module — Langfuse compose lifecycle
## @scope    Langfuse LLM Tracing
## @invariants
##   - MODULE_NAME and CONTAINER defined before include
##   - CONTAINER points to langfuse (only service)
##   - All targets delegated to core/templates/module.mk
# endregion MODULE_CONTRACT

MODULE_NAME := langfuse
CONTAINER := langfuse
include ../../templates/module.mk

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile — langfuse lifecycle through module.mk
# · Rejected: inline docker compose commands per target
# · Reason: module.mk provides zero-boilerplate lifecycle for all docker modules
# · Rev: when module needs custom behavior → override targets after include
