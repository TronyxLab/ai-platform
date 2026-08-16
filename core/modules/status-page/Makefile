# GREP_SUMMARY: status-page module Makefile docker compose target include module.mk
# STRUCTURE: MODULE_NAME + COMPOSE_FILE + CONTAINER → include module.mk
# region MODULE_CONTRACT
## @purpose  Makefile for status-page Docker module — defines module-specific variables, includes module.mk for lifecycle
## @scope    status-page module — live node status aggregation service
## @invariants
##   - MODULE_NAME = status-page
##   - COMPOSE_FILE = docker-compose.base.yml
##   - CONTAINER = status-page
##   - All lifecycle targets delegated to module.mk
## @rationale Minimal module Makefile per AGENTS.md contract — no custom targets needed for status-page
# endregion MODULE_CONTRACT

MODULE_NAME := status-page
SHELL := /bin/bash
MODULE_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
COMPOSE_FILE := $(MODULE_DIR)/docker-compose.base.yml
CONTAINER := status-page
COMPOSE_PROFILES ?= status-page

include ../../templates/module.mk
