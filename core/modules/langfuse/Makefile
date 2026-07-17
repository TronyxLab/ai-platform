# GREP_SUMMARY: langfuse Makefile start stop restart status logs backup compose include module-mk
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
