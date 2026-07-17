# GREP_SUMMARY: litellm Makefile start stop restart status logs backup compose include module-mk
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
