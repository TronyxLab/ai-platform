# GREP_SUMMARY: litellm Makefile start stop restart status logs compose include module-mk
# STRUCTURE: ┌litellm targets┐ → ◇ module.mk include → ⊕ compose lifecycle
# region MODULE_CONTRACT
## @purpose  Makefile for litellm module — LiteLLM compose lifecycle
## @scope    LiteLLM LLM Gateway proxy
## @invariants
##   - MODULE_NAME and CONTAINER defined before include
##   - CONTAINER points to litellm (only service)
##   - All targets delegated to core/templates/module.mk
# endregion MODULE_CONTRACT

MODULE_NAME := litellm
CONTAINER := litellm
include ../../templates/module.mk

# 🧐 TRAP[DECISION] · 2026-07-18 · — · Minimal module Makefile — litellm lifecycle through module.mk
# · Rejected: inline docker compose commands per target
# · Reason: module.mk provides zero-boilerplate lifecycle for all docker modules
# · Rev: when module needs custom behavior → override targets after include
